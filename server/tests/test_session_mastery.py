"""FLE-65 — §5.2 step 5: the rating that actually moves `skill_nodes.mastery`.

FLE-64 shipped steps 2-4 of the fan-out. A rated item wrote `drill_attempts`, moved the
§7.2 ladder, and recorded the repertoire daily verdict — and moved no mastery at all.
That is invisible from every angle the pilot looks at it from: the ladder moves, so
tomorrow's TEMPO adapts and the session feels responsive; only `deficit`, ~100 of the
~180 points in FLE-4 §11.2's selection score, silently reads a column nothing writes.
So tomorrow's session is planned well and CHOSEN as if the user had never practised.

These run against real Postgres, because the three things most likely to be wrong are
all database behaviours:

- **The clamp.** `LEAST(1.0, GREATEST(0.0, mastery + shift))` is built from typed
  literals because asyncpg rejects `::numeric` inside a parameterised query. A mocked
  session cannot tell a working clamp from one that raises on first contact with the
  driver, and `skill_nodes.mastery` is NUMERIC(4,3) — 1.100 does not fit.
- **The blast radius.** "Moves exactly one row" is a claim about every OTHER row in the
  table, so the assertions below diff the user's whole node set before and after rather
  than reading the one node they expect to move. A WHERE clause that lost its node
  filter would pass a single-node assertion and fail here.
- **The double-count gate.** It is `ON CONFLICT DO NOTHING`'s RETURNING against a
  partial expression index. There is no Python branch to unit-test; the index IS the
  rule, and it only exists in the database.

Row builders come from test_session_snapshot.py and the session/rating helpers from
test_session_ladder.py — both already proven by FLE-9 and FLE-64, and re-used rather
than re-written so a change to the generator's shape breaks one place.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.sessions import lifecycle, mastery
from app.sessions.ladder import LOW_RATING, MID_RATING, TOP_RATING

from tests.test_session_ladder import (  # session + rating helpers, proven by FLE-64
    _first_rated_drill,
    _first_rated_song,
    _rate,
    _session,
    _stocked_user,
    _user_with_a_song,
    db,  # noqa: F401 — the per-test connection fixture
)
from tests.test_session_snapshot import (  # row builders, already proven by FLE-9
    DAY,
    _make_root_chain,
    _make_user,
)

# D-08's table, restated as literals. Importing the dict and asserting it equals itself
# would prove nothing; these are the numbers FLE-4 §1 actually agreed, so a retune has
# to change them HERE too and cannot slip through green.
D08 = {
    LOW_RATING: Decimal("-0.05"),
    MID_RATING: Decimal("0.05"),
    TOP_RATING: Decimal("0.15"),
}


# ---------------------------------------------------------------------------
# Helpers — every assertion below is about the whole node set, not one node.
# ---------------------------------------------------------------------------


async def _masteries(db, uid) -> dict[uuid.UUID, Decimal]:
    """Every node this user owns, by id. The denominator for "exactly one row"."""
    rows = (
        await db.execute(
            text("SELECT id, mastery FROM skill_nodes WHERE user_id = :u"),
            {"u": uid},
        )
    ).all()
    return {r.id: r.mastery for r in rows}


def _moved(before: dict, after: dict) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    """The nodes whose mastery changed. Keyed so the caller can name the one it wants."""
    return {
        nid: (before[nid], after[nid])
        for nid in before
        if before[nid] != after.get(nid)
    }


async def _set_mastery(db, node_id, value: str) -> None:
    await db.execute(
        text("UPDATE skill_nodes SET mastery = :m WHERE id = :n"),
        {"m": Decimal(value), "n": node_id},
    )


async def _retarget(db, item_id, node_id) -> None:
    """Point a session item at a different skill node.

    This is how the crafted-id case is reached. The player never sends a node id — it
    comes off the server-written item row — so the only way a rating can arrive
    carrying another user's node is for that row to hold one. Writing it directly is
    the honest reproduction of that, and it is also what a compromised generator or a
    hand-edited row would leave behind.
    """
    await db.execute(
        text(
            "UPDATE practice_session_items SET target_skill_node_id = :n WHERE id = :i"
        ),
        {"n": node_id, "i": item_id},
    )


async def _whole_song_verdict(db, uid, song_id, rating=TOP_RATING) -> uuid.UUID:
    """The row `POST /api/v1/sessions` leaves behind for a whole-song rating.

    Same slot the player's step 4 writes: `drill_index` NULL, both markers false. The
    Today card's shift is applied in the same transaction as this row, so the row's
    existence is exactly the fact the player's gate has to notice.
    """
    vid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO user_sessions (id, user_id, song_id, rating, "
            "local_calendar_day, tz_offset_minutes, is_reroll_marker, "
            "is_daily_pick_marker) VALUES (:id, :u, :s, CAST(:r AS rating_level), "
            ":d, 0, false, false)"
        ),
        {"id": vid, "u": uid, "s": song_id, "r": rating, "d": DAY},
    )
    return vid


async def _attempt_count(db, uid) -> int:
    return await db.scalar(
        text("SELECT count(*) FROM drill_attempts WHERE user_id = :u"), {"u": uid}
    )


# ---------------------------------------------------------------------------
# shift_for — pure, and the one branch that cannot be reached through the DB.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rating,expected", list(D08.items()))
def test_each_rating_earns_exactly_d08s_shift(rating, expected):
    assert mastery.shift_for(rating) == expected


@pytest.mark.parametrize("rating", [None, "brilliant", "", "NOT_MY_TEMPO"])
def test_a_rating_d08_does_not_know_moves_nothing_rather_than_raising(rating):
    """A `rating_level` value added without an agreed shift must hold, not 500.

    Unreachable through the database — the enum would reject the cast long before the
    shift is looked up — which is exactly why it is tested here. By the time step 5
    runs, the rating is committed to the item row and the ladder has already moved in
    the same transaction; a KeyError would roll both back over a missing dict entry.
    Mirrors `ladder.classify`: a verdict we do not understand must never move anything.
    """
    assert mastery.shift_for(rating) is None


# ---------------------------------------------------------------------------
# The drill item — one node, the one the attempt was recorded against.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rating", list(D08))
async def test_a_rated_drill_moves_exactly_one_node_by_d08s_shift(db, rating):
    """The issue's first done-when, for all three tiers.

    `_moved` is a whole-table diff: the assertion is not just that the target node moved
    by the right amount but that it is the ONLY node that moved. Migration 0009's column
    comment — "the node this attempt's rating moves mastery on" — is a statement about
    one node, and the leaves in this fixture share a parent, so a shift that walked the
    tree or dropped its id filter would land here.
    """
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)
    # Off both clamps, so the arithmetic is visible rather than saturated.
    await _set_mastery(db, item.target_skill_node_id, "0.400")

    before = await _masteries(db, uid)
    outcome, _ = await _rate(db, uid, sid, item, rating)

    after = await _masteries(db, uid)
    moved = _moved(before, after)
    assert list(moved) == [item.target_skill_node_id]
    was, now = moved[item.target_skill_node_id]
    assert now - was == D08[rating]
    assert outcome.fanout.mastery_shifted is True
    assert outcome.fanout.mastery_node_id == item.target_skill_node_id


async def test_the_shift_is_clamped_at_the_ceiling_sql_side(db):
    """0.95 + 0.15 is 1.10, and `mastery` is NUMERIC(4,3) — it does not fit.

    Clamped in the UPDATE and not in Python: the read and the write are then one
    statement, so a concurrent shift cannot be computed against a stale value.
    """
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)
    await _set_mastery(db, item.target_skill_node_id, "0.950")

    await _rate(db, uid, sid, item, TOP_RATING)

    after = await _masteries(db, uid)
    assert after[item.target_skill_node_id] == Decimal("1.000")


async def test_the_shift_is_clamped_at_the_floor_sql_side(db):
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)
    await _set_mastery(db, item.target_skill_node_id, "0.020")

    await _rate(db, uid, sid, item, LOW_RATING)

    after = await _masteries(db, uid)
    assert after[item.target_skill_node_id] == Decimal("0.000")


async def test_a_duplicate_flush_does_not_shift_mastery_twice(db):
    """The player's outbox is fire-and-forget and WILL deliver the same rating twice.

    Step 5 inherits FLE-64's once-per-item gate rather than adding its own: `_fan_out`
    is reached only when this write is the one that set the item's rating. Worth its own
    test anyway, because mastery is the one fan-out target where a double application is
    silent — the ladder's rung would visibly jump two, a duplicate attempt row would be
    countable, but 0.400 -> 0.700 looks exactly like a legitimate 0.400 -> 0.550.
    """
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)
    await _set_mastery(db, item.target_skill_node_id, "0.400")

    before = await _masteries(db, uid)
    await _rate(db, uid, sid, item, TOP_RATING)
    once = await _masteries(db, uid)
    second, _ = await _rate(db, uid, sid, item, TOP_RATING)
    twice = await _masteries(db, uid)

    assert once[item.target_skill_node_id] - before[item.target_skill_node_id] == D08[
        TOP_RATING
    ]
    assert twice == once
    assert second.fanout.mastery_shifted is False


async def test_an_unrated_complete_moves_no_mastery(db):
    """Most items in a 15-minute session are unrated by design (FLE-4 §5.1/§5.3/§5.4).

    `rating=None` is a first-class value, not a missing one, and it must not be read as
    an implicit verdict in either direction.
    """
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)

    before = await _masteries(db, uid)
    outcome, _ = await _rate(db, uid, sid, item, None)

    assert await _masteries(db, uid) == before
    assert outcome.fanout.mastery_shifted is False


async def test_a_skip_moves_no_mastery(db):
    """"A skip is not data" (§7.2) — and least of all is it evidence about a skill."""
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)

    before = await _masteries(db, uid)
    await lifecycle.skip_item(db, uid, sid, item.item_index, active_seconds=4)

    assert await _masteries(db, uid) == before


# ---------------------------------------------------------------------------
# T-04.1-05 — a crafted target node cannot reach another participant.
# ---------------------------------------------------------------------------


async def test_a_crafted_target_node_cannot_move_another_users_mastery(db):
    """The issue's third done-when, and the reason `user_id` is in the WHERE clause.

    Checked in the UPDATE rather than by a SELECT beforehand: a check-then-act would
    leave a window in which the node changes owner between the two statements, and a
    single statement has no such window. The victim's node is asserted UNCHANGED — not
    merely that the call reported failure — because "we returned False" and "we did not
    write" are different claims and only the second one matters here.
    """
    victim = await _make_user(db, "{}")
    victim_leaf = (await _make_root_chain(db, victim, "Rhythm", 0.30))[0]

    attacker = await _stocked_user(db)
    sid, items = await _session(db, attacker)
    item = _first_rated_drill(items)
    await _retarget(db, item.id, victim_leaf)
    # Re-read so the item object carries the crafted id the handler will act on.
    items = await lifecycle.load_items(db, sid)
    item = next(i for i in items if i.item_index == item.item_index)
    assert item.target_skill_node_id == victim_leaf

    victim_before = await _masteries(db, victim)
    attacker_before = await _masteries(db, attacker)
    outcome, _ = await _rate(db, attacker, sid, item, TOP_RATING)

    assert await _masteries(db, victim) == victim_before
    assert await _masteries(db, attacker) == attacker_before
    assert outcome.fanout.mastery_shifted is False

    # And the rest of the fan-out still committed. The guard is a filter on one UPDATE,
    # not a rejection of the write: the user really did practise, and losing the attempt
    # record and the ladder move to punish a corrupt row would cost the pilot real data
    # the player will not send again.
    assert outcome.state == "completed"
    assert outcome.fanout.attempt_id is not None
    assert await _attempt_count(db, attacker) == 1


async def test_an_item_with_no_target_node_is_survivable(db):
    """A rated item the generator never gave a node. Logged, not raised.

    Same reasoning as the crafted-id case: the rating and the ladder move are already in
    this transaction, so the honest outcome is a rating that moves no mastery, reported
    as such, rather than a 500 that loses the attempt.
    """
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)
    await _retarget(db, item.id, None)
    items = await lifecycle.load_items(db, sid)
    item = next(i for i in items if i.item_index == item.item_index)

    before = await _masteries(db, uid)
    outcome, _ = await _rate(db, uid, sid, item, TOP_RATING)

    assert await _masteries(db, uid) == before
    assert outcome.fanout.mastery_shifted is False
    assert outcome.fanout.attempt_id is not None


# ---------------------------------------------------------------------------
# The repertoire item — the double-count, closed by construction.
# ---------------------------------------------------------------------------


async def test_the_repertoire_item_shifts_its_section_node_and_nothing_else(db):
    """One node, not D-07's every leaf — and the ruling this test exists to pin.

    The row step 4 writes IS D-07's whole-song row, so shifting every `song_skills` leaf
    would be the consistent-looking choice. It is the wrong one: `section_skill_node_id`
    is the LOWEST-mastery leaf of that set (snapshot.py `_SONG_SKILLS_SQL` orders by
    mastery ASC and takes the first), and an equal shift across all of them preserves
    that ordering exactly. The user would be handed the same section every day for the
    length of the pilot no matter how well they played it. Shifting the one section that
    was actually worked is what lets tomorrow hand back the next-weakest one.

    The song in this fixture has three linked leaves, so "the others did not move" is a
    real assertion rather than a vacuous one.
    """
    uid, song_id = await _user_with_a_song(db)
    sid, items = await _session(db, uid, song_id=song_id)
    item = _first_rated_song(items)
    linked = {
        r.skill_node_id
        for r in (
            await db.execute(
                text("SELECT skill_node_id FROM song_skills WHERE song_id = :s"),
                {"s": song_id},
            )
        ).all()
    }
    assert item.target_skill_node_id in linked
    assert len(linked) > 1, "fixture must link more than one leaf or this proves nothing"

    before = await _masteries(db, uid)
    outcome, _ = await _rate(db, uid, sid, item, TOP_RATING)

    moved = _moved(before, await _masteries(db, uid))
    assert list(moved) == [item.target_skill_node_id]
    was, now = moved[item.target_skill_node_id]
    assert now - was == D08[TOP_RATING]
    assert outcome.fanout.daily_verdict_written is True
    assert outcome.fanout.mastery_shifted is True


async def test_a_today_card_rating_already_taken_withholds_the_players_shift(db):
    """The double-count, closed by construction — the issue's second done-when.

    The gate is not "check whether the Today card rated today". It is step 4's
    `ON CONFLICT DO NOTHING` RETURNING nothing: `uq_user_sessions_daily_rating` is the
    single arbiter of who owns the day's whole-song slot and it decides under a row
    lock. On both surfaces "owns the verdict row" and "applied the shift" are set in one
    transaction, so at most one shift per (user, song, day) is an invariant rather than
    a tendency.

    The 409 the player then receives is a state-sync signal (FLE-21 §5.2), NOT a
    rejection — the item's own rating is committed either way, which is precisely why
    this could not be left to "the second write probably will not happen".
    """
    uid, song_id = await _user_with_a_song(db)
    sid, items = await _session(db, uid, song_id=song_id)
    item = _first_rated_song(items)
    await _whole_song_verdict(db, uid, song_id)  # the Today card got there first

    before = await _masteries(db, uid)
    outcome, _ = await _rate(db, uid, sid, item, TOP_RATING)

    assert await _masteries(db, uid) == before
    assert outcome.fanout.daily_verdict_conflict is True
    assert outcome.fanout.daily_verdict_written is False
    assert outcome.fanout.mastery_shifted is False
    # The rating itself still landed. That is the whole reason the gate has to be
    # by construction: the losing write is answered 409 and committed, not rolled back.
    assert outcome.state == "completed"
    rated = await db.scalar(
        text("SELECT rating::text FROM practice_session_items WHERE id = :i"),
        {"i": item.id},
    )
    assert rated == TOP_RATING


async def test_the_players_rating_takes_the_slot_the_today_card_would_have_used(db):
    """The other direction — and why the Today card's own 409 needs no new code.

    Having rated in the player, the day's whole-song slot is taken. `POST
    /api/v1/sessions` counts exactly this row shape in its app-level guard and answers
    409 BEFORE reaching its mastery UPDATE, and the partial unique index catches the
    concurrent case by rolling that transaction back whole. Asserted here as the index
    rejecting a second insert, which is the fact both of those depend on.
    """
    uid, song_id = await _user_with_a_song(db)
    sid, items = await _session(db, uid, song_id=song_id)
    item = _first_rated_song(items)

    outcome, _ = await _rate(db, uid, sid, item, TOP_RATING)
    assert outcome.fanout.daily_verdict_written is True
    assert outcome.fanout.mastery_shifted is True

    # The shape POST /api/v1/sessions' own guard counts: whole-song slot, not a marker.
    contenders = await db.scalar(
        text(
            "SELECT count(*) FROM user_sessions WHERE user_id = :u AND song_id = :s "
            "AND local_calendar_day = :d AND drill_index IS NULL "
            "AND is_reroll_marker = false AND is_daily_pick_marker = false"
        ),
        {"u": uid, "s": song_id, "d": DAY},
    )
    assert contenders == 1

    # And the index, not the application, is what makes that count unbeatable.
    with pytest.raises(Exception) as caught:
        await _whole_song_verdict(db, uid, song_id, rating=LOW_RATING)
    assert "uq_user_sessions_daily_rating" in str(caught.value)
    await db.rollback()


async def test_the_daily_pick_marker_does_not_withhold_the_shift(db):
    """FLE-54's marker shares the slot's shape but is provenance, not a rating.

    `record_daily_verdict`'s conflict target repeats the index predicate
    (`is_daily_pick_marker = false`), so today's persisted pick does not look like a
    verdict. If it did, the player's very first repertoire rating of the day would be
    withheld on every user who got a Song of the Day — which is all of them.
    """
    uid, song_id = await _user_with_a_song(db)
    sid, items = await _session(db, uid, song_id=song_id)
    item = _first_rated_song(items)
    await db.execute(
        text(
            "INSERT INTO user_sessions (id, user_id, song_id, rating, "
            "local_calendar_day, tz_offset_minutes, is_reroll_marker, "
            "is_daily_pick_marker) VALUES (:id, :u, :s, CAST(:r AS rating_level), "
            ":d, 0, false, true)"
        ),
        {"id": uuid.uuid4(), "u": uid, "s": song_id, "r": TOP_RATING, "d": DAY},
    )

    before = await _masteries(db, uid)
    outcome, _ = await _rate(db, uid, sid, item, MID_RATING)

    moved = _moved(before, await _masteries(db, uid))
    assert list(moved) == [item.target_skill_node_id]
    assert moved[item.target_skill_node_id][1] - moved[item.target_skill_node_id][
        0
    ] == D08[MID_RATING]
    assert outcome.fanout.mastery_shifted is True


async def test_a_drill_shift_is_not_gated_by_the_songs_verdict(db):
    """Different slots. A whole-song verdict already taken must not silence a drill.

    The gate is on the repertoire branch alone, because only that branch contends for
    the `user_sessions` whole-song row. A drill attempt's node is written by nothing
    else, so gating it too would mean that rating the song on the Today card quietly
    disabled mastery for every drill in that evening's session.
    """
    uid, song_id = await _user_with_a_song(db)
    sid, items = await _session(db, uid, song_id=song_id)
    drill = _first_rated_drill(items)
    await _whole_song_verdict(db, uid, song_id)
    await _set_mastery(db, drill.target_skill_node_id, "0.400")

    before = await _masteries(db, uid)
    outcome, _ = await _rate(db, uid, sid, drill, TOP_RATING)

    moved = _moved(before, await _masteries(db, uid))
    assert list(moved) == [drill.target_skill_node_id]
    assert outcome.fanout.mastery_shifted is True


# ---------------------------------------------------------------------------
# The reason any of this exists: §11.2's `deficit` can now see yesterday.
# ---------------------------------------------------------------------------


async def test_the_selectors_deficit_term_sees_the_rating(db):
    """End-to-end on the claim the issue is actually about.

    Not a test of `select.py` — of the column it reads. `deficit` is 1 - mastery on
    `target_skill_node_id`, ~100 of ~180 points, and before FLE-65 a player rating left
    that value bit-identical. A user who nailed a drill scored exactly as needy
    tomorrow as one who had never touched it, which is the failure mode nothing else in
    the suite could observe: every other fan-out target moved correctly.
    """
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)
    await _set_mastery(db, item.target_skill_node_id, "0.400")

    def deficit(m):
        return Decimal("1.0") - m

    before = deficit((await _masteries(db, uid))[item.target_skill_node_id])
    await _rate(db, uid, sid, item, TOP_RATING)
    after = deficit((await _masteries(db, uid))[item.target_skill_node_id])

    assert before - after == D08[TOP_RATING], "the selector still cannot see yesterday"
