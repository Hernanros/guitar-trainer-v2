"""The §5.2 fan-out and the FLE-4 §7 ladder controller (FLE-64).

lifecycle.py records what the user did; this is where what they did changes what
they are asked to do next. The selection rule for a test here is narrower than
FLE-21's, and it is this: **would its absence let the ladder move for a reason the
player would experience as arbitrary?**

That rules in three families of assertion and rules out almost everything else.

- **The CLEAR / HOLD boundary (§7.1).** A top rating after 5 of 20 reps is not a
  clear. Get it wrong and the ladder inflates on partial attempts, the player is
  stranded three rungs above what they can hold, and the symptom arrives weeks later
  as "the tempos feel arbitrary" from someone who has already stopped practising.
  The boundary is parametrised at exactly 0.80 x planned, one rep either side.

- **Once per attempt.** The player's outbox delivers twice. A second flush of the
  same rating must not move the rung again — a double push is indistinguishable at
  read time from a player improving faster than they are, and it silently poisons
  every rung in the bank.

- **The vetoes (§7.4, §7.3).** `allow_push = false` and `re_entry` both withhold the
  push while BANKING the clear. If the bank is dropped instead of withheld, a player
  coming back from a layoff has to earn the same rung twice and the app has quietly
  punished them for returning.

Everything here runs against real Postgres. The upsert, the `FOR UPDATE` that makes
the once-guard a guard, the partial-unique the daily verdict conflicts against, and
`ck_drill_progress_rung_multiple_of_5` are all database behaviours — mocked out, they
all pass while shipping nothing. Fixture construction is inherited from
test_session_snapshot.py; the suite-wide event-loop defect it works around is FLE-62.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.sessions import ladder, lifecycle
from app.sessions.ladder import (
    CLEAR,
    HOLD,
    LOW_RATING,
    MID_RATING,
    MISS,
    SKIP,
    TOP_RATING,
    LadderState,
    classify,
    initial_rung,
    transition,
)
from app.sessions.plan import CLEARS_TO_PUSH, MISSES_TO_DROP, RUNG_STEP_BPM
from app.sessions.store import resolve_or_generate

from tests.test_session_snapshot import (  # row builders, already proven by FLE-9
    DAY,
    TEST_DB_URL,
    _link_song_skill,
    _make_drill,
    _make_root_chain,
    _make_song,
    _make_user,
)


@pytest.fixture
async def db():
    """Same construction as test_session_snapshot.py's — see the reasoning there."""
    engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)
    conn = await engine.connect()
    yield conn
    conn.sync_connection.connection.dbapi_connection.driver_connection.terminate()
    engine.sync_engine.dispose(close=False)


# ---------------------------------------------------------------------------
# §7.1 — the classifier. Pure, so the boundaries are cheap to pin exhaustively.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rating,completed,planned,expected",
    [
        # The boundary itself: 0.80 x 20 = 16. Sixteen clears, fifteen does not.
        (TOP_RATING, 16, 20, CLEAR),
        (TOP_RATING, 15, 20, HOLD),
        (TOP_RATING, 20, 20, CLEAR),
        (TOP_RATING, 25, 20, CLEAR),  # overshoot is still a clear
        # §7.1's quoted example — the whole reason completed_reps exists.
        (TOP_RATING, 5, 20, HOLD),
        # A bad night is a miss regardless of how many reps were ground out.
        (LOW_RATING, 20, 20, MISS),
        (LOW_RATING, 0, 20, MISS),
        # getting_closer holds, always.
        (MID_RATING, 20, 20, HOLD),
        (MID_RATING, 0, 20, HOLD),
    ],
)
def test_classify_pins_the_clear_boundary(rating, completed, planned, expected):
    assert classify(rating, completed, planned) == expected


@pytest.mark.parametrize("completed,planned", [(None, 20), (16, None), (16, 0)])
def test_missing_rep_data_can_never_promote_a_rung(completed, planned):
    """FLE-21 §2.1 — reps are never inferred, and unanswerable is not a clear.

    A top rating with no rep count is the shape an older client sends. Classifying
    it CLEAR would let a client bug push every rung in the bank; classifying it HOLD
    costs one session of progress and is recoverable.
    """
    assert classify(TOP_RATING, completed, planned) == HOLD


def test_an_unrecognised_rating_holds_rather_than_moving():
    """A verdict the ladder does not understand must not move it in either direction."""
    assert classify(None, 20, 20) == HOLD
    assert classify("some_future_rating", 20, 20) == HOLD


# ---------------------------------------------------------------------------
# §7.2 / §7.4 / §7.5 — the transition. Also pure.
# ---------------------------------------------------------------------------


def _active(rung: int = 60, clears: int = 0, misses: int = 0, state: str = "active"):
    return LadderState(
        rung_bpm=rung,
        consecutive_clears=clears,
        consecutive_misses=misses,
        state=state,
    )


def _move(before, outcome, *, allow_push=True, re_entry=False, start=60, target=100):
    return transition(
        before,
        outcome,
        allow_push=allow_push,
        re_entry=re_entry,
        start_bpm=start,
        target_bpm=target,
    )


def test_one_clear_banks_but_does_not_push():
    """"Nail it TWICE and you move up." One clear can be luck."""
    moved = _move(_active(), CLEAR)
    assert moved.after.consecutive_clears == 1
    assert moved.after.rung_bpm == 60
    assert not moved.pushed


def test_the_second_consecutive_clear_pushes_one_rung_and_resets_the_count():
    moved = _move(_active(clears=CLEARS_TO_PUSH - 1), CLEAR)
    assert moved.pushed
    assert moved.after.rung_bpm == 60 + RUNG_STEP_BPM
    assert moved.after.consecutive_clears == 0, (
        "leaving the count standing would push again on the very next clear, "
        "halving the two-sessions-per-rung pace §7.2 is explicit about"
    )


def test_a_hold_between_two_clears_resets_the_count():
    """"Two IN A ROW" is the rule. A hold is not evidence for the rung."""
    after_first = _move(_active(), CLEAR).after
    after_hold = _move(after_first, HOLD).after
    assert after_hold.consecutive_clears == 0
    assert not _move(after_hold, CLEAR).pushed


def test_the_second_consecutive_miss_drops_one_rung():
    moved = _move(_active(rung=80, misses=MISSES_TO_DROP - 1), MISS)
    assert moved.dropped
    assert moved.after.rung_bpm == 75
    assert moved.after.consecutive_misses == 0


def test_a_drop_never_goes_below_the_drills_own_floor():
    moved = _move(_active(rung=60, misses=1), MISS, start=60)
    assert moved.after.rung_bpm == 60, "start_bpm is the floor, not a suggestion"


def test_a_clear_at_the_top_of_the_ladder_masters_the_drill():
    moved = _move(_active(rung=100, clears=1), CLEAR, target=100)
    assert moved.newly_mastered
    assert moved.after.state == "mastered"
    assert moved.after.mastered_at_set, "§7.5's 14-day timer is armed by mastered_at"
    assert moved.after.rung_bpm == 100, "there is no rung above target_bpm"


def test_allow_push_false_withholds_the_push_and_banks_the_clear():
    """§7.4 — the single most important tempo rule in the document.

    The clear must SURVIVE. Dropping it would mean a consolidating session silently
    costs the player a night of progress, which is tempo pressure applied to someone
    who is already disengaging — exactly what §7.4 exists to prevent.
    """
    moved = _move(_active(clears=CLEARS_TO_PUSH - 1), CLEAR, allow_push=False)
    assert not moved.pushed
    assert moved.after.rung_bpm == 60
    assert moved.after.consecutive_clears == CLEARS_TO_PUSH


def test_allow_push_false_does_not_withhold_a_drop():
    """§7.4 vetoes the PUSH. A player who has now missed twice still drops back —
    holding them at a tempo they cannot play is not consolidation."""
    moved = _move(_active(rung=80, misses=MISSES_TO_DROP - 1), MISS, allow_push=False)
    assert moved.dropped and moved.after.rung_bpm == 75


def test_a_re_entry_clear_earns_the_record_back_but_cannot_promote():
    """§7.3 — layoff costs a rung of confidence, never a rung of record."""
    moved = _move(_active(clears=CLEARS_TO_PUSH - 1), CLEAR, re_entry=True)
    assert not moved.pushed
    assert moved.after.consecutive_clears == CLEARS_TO_PUSH


def test_a_re_entry_miss_still_counts_toward_a_drop():
    """§7.3 says so explicitly — the re-entry guard is on promotion only."""
    moved = _move(_active(rung=80, misses=MISSES_TO_DROP - 1), MISS, re_entry=True)
    assert moved.dropped


def test_two_clears_in_maintenance_re_master_the_drill():
    moved = _move(_active(rung=90, clears=1, state="maintenance"), CLEAR, target=100)
    assert moved.after.state == "mastered" and moved.after.mastered_at_set


def test_two_misses_in_maintenance_return_the_drill_to_active_a_rung_down():
    moved = _move(_active(rung=90, misses=1, state="maintenance"), MISS)
    assert moved.after.state == "active"
    assert moved.after.rung_bpm == 85


def test_a_skip_moves_nothing_at_all():
    """§7.2 — a skip is not data."""
    before = _active(rung=70, clears=1, misses=1)
    assert _move(before, SKIP).after == before


def test_initial_rung_snaps_an_odd_start_bpm_onto_the_grid():
    """ck_drill_progress_rung_multiple_of_5 is a hard constraint; 63 would reject."""
    assert initial_rung(63) == 65
    assert initial_rung(60) == 60
    assert initial_rung(5) == 20, "clamped to the CHECK's floor rather than rejected"


# ---------------------------------------------------------------------------
# The fan-out, against real rows.
# ---------------------------------------------------------------------------


async def _stocked_user(db, *, session_length: int = 30) -> uuid.UUID:
    uid = await _make_user(db, f'{{"session_length_min": {session_length}}}')
    rhythm = await _make_root_chain(db, uid, "Rhythm", 0.2, 0.35)
    lead = await _make_root_chain(db, uid, "Lead", 0.6, 0.55)
    for leaf in rhythm + lead:
        for _ in range(2):
            await _make_drill(db, uid, leaf)
    return uid


async def _user_with_a_song(db) -> tuple[uuid.UUID, int]:
    """A stocked user whose session will also contain a repertoire block."""
    uid = await _make_user(db, '{"session_length_min": 30}')
    rhythm = await _make_root_chain(db, uid, "Rhythm", 0.2, 0.35)
    lead = await _make_root_chain(db, uid, "Lead", 0.6, 0.55)
    for leaf in rhythm + lead:
        for _ in range(2):
            await _make_drill(db, uid, leaf)
    song_id = await _make_song(db, uid)
    for leaf in rhythm:
        await _link_song_skill(db, song_id, leaf)
    return uid, song_id


async def _session(db, uid, *, day: date = DAY, song_id: int | None = None):
    stored = await resolve_or_generate(
        db, uid, tz_offset_minutes=0, local_calendar_day=day, song_id=song_id
    )
    items = await lifecycle.load_items(db, stored.session_id)
    assert items, "generator produced no items — scenario is not exercising anything"
    return stored.session_id, items


def _first_rated_drill(items):
    for item in items:
        if item.rated and item.drill_id is not None:
            return item
    raise AssertionError("scenario produced no rated drill item")


def _first_rated_song(items):
    for item in items:
        if item.rated and item.song_id is not None:
            return item
    raise AssertionError("scenario produced no rated song item")


async def _set_allow_push(db, sid, value: bool) -> None:
    await db.execute(
        text("UPDATE practice_sessions SET allow_push = :v WHERE id = :s"),
        {"v": value, "s": sid},
    )


async def _progress(db, uid, drill_id):
    return (
        await db.execute(
            text(
                "SELECT rung_bpm, consecutive_clears, consecutive_misses, attempts, "
                "last_practiced_on, state::text AS state, mastered_at "
                "FROM drill_progress WHERE user_id = :u AND drill_id = :d"
            ),
            {"u": uid, "d": drill_id},
        )
    ).first()


async def _attempts(db, uid, drill_id):
    return (
        await db.execute(
            text(
                "SELECT rung_bpm, planned_bpm, planned_reps, reps_completed, "
                "tempo_reached_bpm, rating::text AS rating, outcome::text AS outcome, "
                "re_entry, duration_s, session_item_id, target_skill_node_id, "
                "local_calendar_day "
                "FROM drill_attempts WHERE user_id = :u AND drill_id = :d "
                "ORDER BY attempted_at"
            ),
            {"u": uid, "d": drill_id},
        )
    ).all()


async def _clear_again(db, uid, item, *, allow_push=True, rating=TOP_RATING):
    """One more attempt on the same ladder, bypassing the session surface.

    The fan-out is once per ITEM by design, so a second attempt on the same drill
    needs either a second session — whose contents §11.2 decides, not this test — or
    the controller directly. This is the controller directly.
    """
    return await ladder.record_attempt(
        db,
        user_id=uid,
        drill_id=item.drill_id,
        session_item_id=item.id,
        local_calendar_day=DAY,
        planned_bpm=item.planned_bpm,
        planned_reps=item.planned_reps,
        completed_reps=item.planned_reps,
        rating=rating,
        re_entry=False,
        allow_push=allow_push,
        duration_s=180,
        target_skill_node_id=item.target_skill_node_id,
    )


async def _rate(db, uid, sid, item, rating, *, reps=None, seconds=200):
    return await lifecycle.complete_item(
        db,
        uid,
        sid,
        item.item_index,
        active_seconds=seconds,
        rating=rating,
        completed_reps=item.planned_reps if reps is None else reps,
    )


async def test_a_rated_drill_writes_the_full_attempt_record(db):
    """§13 — everything needed to AUDIT the transition, not just the verdict."""
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)

    outcome, _ = await _rate(db, uid, sid, item, TOP_RATING)

    rows = await _attempts(db, uid, item.drill_id)
    assert len(rows) == 1
    attempt = rows[0]
    assert attempt.outcome == CLEAR
    assert attempt.rating == TOP_RATING
    assert attempt.planned_bpm == item.planned_bpm
    assert attempt.planned_reps == item.planned_reps
    assert attempt.reps_completed == item.planned_reps
    assert attempt.session_item_id == item.id
    assert attempt.target_skill_node_id == item.target_skill_node_id
    assert attempt.duration_s == 200
    assert attempt.local_calendar_day == DAY
    assert outcome.fanout.outcome == CLEAR


async def test_the_attempt_records_the_rung_it_was_taken_at_not_the_pushed_one(db):
    """An attempt is evidence about the tempo it happened at.

    Storing the post-push rung would date every clear one rung high, and the drift
    is invisible: the numbers stay plausible and every one of them is wrong.
    """
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)

    await _rate(db, uid, sid, item, TOP_RATING)
    first_rung = (await _progress(db, uid, item.drill_id)).rung_bpm
    # The second clear goes through the controller directly rather than through a
    # second generated session: whether selection re-serves this drill tomorrow is
    # §11.2's business, and this test is about the attempt record, not about that.
    await _clear_again(db, uid, item)

    rows = await _attempts(db, uid, item.drill_id)
    assert [r.rung_bpm for r in rows] == [first_rung, first_rung], (
        "both attempts happened AT the starting rung; the push is what the second "
        "one caused, not the tempo it was taken at"
    )
    assert (await _progress(db, uid, item.drill_id)).rung_bpm == first_rung + RUNG_STEP_BPM


async def test_tempo_reached_is_written_only_when_the_tempo_was_actually_held(db):
    """A MISS at 80 is evidence the player did NOT hold 80."""
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)

    await _rate(db, uid, sid, item, LOW_RATING)

    attempt = (await _attempts(db, uid, item.drill_id))[0]
    assert attempt.outcome == MISS
    assert attempt.tempo_reached_bpm is None


async def test_the_first_rated_attempt_creates_the_ladder_at_the_drills_floor(db):
    """Nothing writes drill_progress before the first rating — the generator LEFT
    JOINs it and reads absence as "active at start_bpm"."""
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)
    assert await _progress(db, uid, item.drill_id) is None

    await _rate(db, uid, sid, item, MID_RATING)

    progress = await _progress(db, uid, item.drill_id)
    assert progress is not None
    assert progress.rung_bpm % RUNG_STEP_BPM == 0
    assert progress.attempts == 1
    assert progress.last_practiced_on == DAY
    assert progress.state == "active"


async def test_a_duplicate_flush_of_the_same_rating_does_not_move_the_ladder_twice(db):
    """The outbox WILL deliver twice. §7.2 is once per attempt.

    This is the assertion the whole `FOR UPDATE` read exists for. A second push here
    is indistinguishable at read time from a player improving faster than they are.
    """
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)

    first, _ = await _rate(db, uid, sid, item, TOP_RATING)
    before = await _progress(db, uid, item.drill_id)
    second, _ = await _rate(db, uid, sid, item, TOP_RATING)
    after = await _progress(db, uid, item.drill_id)

    assert first.fanout.outcome == CLEAR
    assert second.fanout.is_empty, "the retry must fan out to nothing"
    assert len(await _attempts(db, uid, item.drill_id)) == 1
    assert (before.rung_bpm, before.consecutive_clears, before.attempts) == (
        after.rung_bpm,
        after.consecutive_clears,
        after.attempts,
    )


async def test_an_unrated_complete_fans_out_to_nothing(db):
    """Warm-up, song_play and consolidation are unrated BY DESIGN (R1) — and in a
    15-minute session that is most of the items."""
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    unrated = next(i for i in items if not i.rated and i.drill_id is not None)

    outcome, _ = await lifecycle.complete_item(
        db, uid, sid, unrated.item_index, active_seconds=100
    )

    assert outcome.fanout.is_empty
    assert await _progress(db, uid, unrated.drill_id) is None
    assert await _attempts(db, uid, unrated.drill_id) == []


async def test_a_skip_writes_no_attempt_and_moves_no_rung(db):
    """§7.2 — a skip is not data. Not a SKIP attempt row either: `drill_attempts` is
    the history of things the player DID, and §5.4's consolidation fallback reads a
    clear RATE off it whose denominator a skip would silently inflate."""
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)

    outcome, _ = await lifecycle.skip_item(db, uid, sid, item.item_index)

    assert outcome.fanout.is_empty
    assert await _attempts(db, uid, item.drill_id) == []
    assert await _progress(db, uid, item.drill_id) is None


async def test_allow_push_false_banks_the_clear_in_the_database(db):
    """§7.4 end to end: two clears, no push, and the count still standing so the
    push lands in the next session."""
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)
    await _set_allow_push(db, sid, False)

    await _rate(db, uid, sid, item, TOP_RATING)
    start_rung = (await _progress(db, uid, item.drill_id)).rung_bpm

    record = await _clear_again(db, uid, item, allow_push=False)

    progress = await _progress(db, uid, item.drill_id)
    assert record.push_withheld
    assert progress.rung_bpm == start_rung
    assert progress.consecutive_clears == CLEARS_TO_PUSH

    # And the banked clear lands the moment a session allows it.
    pushed = await _clear_again(db, uid, item, allow_push=True)
    assert pushed.pushed
    assert (await _progress(db, uid, item.drill_id)).rung_bpm == start_rung + RUNG_STEP_BPM


async def test_a_top_rating_on_a_partial_attempt_holds_the_rung_in_the_database(db):
    """The §7.1 boundary, proven through the fan-out rather than the pure function —
    because the reps that reach the classifier come off the ROW the terminal write
    COALESCEd, not off the request body."""
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    item = _first_rated_drill(items)
    partial = max(1, int(item.planned_reps * 0.5))

    await _rate(db, uid, sid, item, TOP_RATING, reps=partial)

    assert (await _attempts(db, uid, item.drill_id))[0].outcome == HOLD
    assert (await _progress(db, uid, item.drill_id)).consecutive_clears == 0


async def test_the_rated_repertoire_item_writes_the_daily_verdict(db):
    """§5.2 step 4. One rating call, not two — the player never POSTs /sessions."""
    uid, song_id = await _user_with_a_song(db)
    sid, items = await _session(db, uid, song_id=song_id)
    item = _first_rated_song(items)

    outcome, _ = await _rate(db, uid, sid, item, MID_RATING)

    rows = (
        await db.execute(
            text(
                "SELECT rating::text AS rating, drill_index, target_skill_node_id, "
                "is_reroll_marker, is_daily_pick_marker, tz_offset_minutes "
                "FROM user_sessions "
                "WHERE user_id = :u AND song_id = :s AND local_calendar_day = :d"
            ),
            {"u": uid, "s": song_id, "d": DAY},
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].rating == MID_RATING
    assert rows[0].drill_index is None, "this is the WHOLE-SONG verdict slot"
    assert rows[0].target_skill_node_id is None, (
        "ck_drill_index_pairs_skill_node is both-or-neither — a section node is not "
        "a drill slot"
    )
    assert not rows[0].is_reroll_marker and not rows[0].is_daily_pick_marker
    assert outcome.fanout.daily_verdict_written
    assert not outcome.fanout.daily_verdict_conflict


async def test_a_verdict_that_already_exists_conflicts_without_losing_the_rating(db):
    """The user rated the song on the Today card first.

    409 is a state-sync signal (UI-SPEC §10), and the item's own rating must survive
    it — a rolled-back write here is telemetry the player will never send again.
    """
    uid, song_id = await _user_with_a_song(db)
    sid, items = await _session(db, uid, song_id=song_id)
    item = _first_rated_song(items)
    await db.execute(
        text(
            "INSERT INTO user_sessions (id, user_id, song_id, rating, "
            "local_calendar_day, tz_offset_minutes) VALUES "
            "(:id, :u, :s, CAST(:r AS rating_level), :d, 0)"
        ),
        {"id": uuid.uuid4(), "u": uid, "s": song_id, "r": TOP_RATING, "d": DAY},
    )

    outcome, _ = await _rate(db, uid, sid, item, MID_RATING)

    assert outcome.fanout.daily_verdict_conflict
    assert not outcome.fanout.daily_verdict_written
    kept = (
        await db.execute(
            text(
                "SELECT rating::text AS rating, state::text AS state "
                "FROM practice_session_items WHERE id = :i"
            ),
            {"i": item.id},
        )
    ).first()
    assert kept.rating == MID_RATING and kept.state == "completed"
    # And the Today-card verdict is untouched — last write does NOT win here.
    existing = (
        await db.execute(
            text(
                "SELECT rating::text AS rating FROM user_sessions "
                "WHERE user_id = :u AND song_id = :s AND local_calendar_day = :d"
            ),
            {"u": uid, "s": song_id, "d": DAY},
        )
    ).all()
    assert [r.rating for r in existing] == [TOP_RATING]


async def test_a_daily_pick_marker_does_not_block_the_verdict(db):
    """FLE-54's marker shares the slot's shape and is excluded from the index
    predicate. If that ever regresses, every session with a persisted daily pick
    would 409 on its repertoire rating."""
    uid, song_id = await _user_with_a_song(db)
    sid, items = await _session(db, uid, song_id=song_id)
    item = _first_rated_song(items)
    await db.execute(
        text(
            "INSERT INTO user_sessions (id, user_id, song_id, local_calendar_day, "
            "tz_offset_minutes, is_daily_pick_marker) "
            "VALUES (:id, :u, :s, :d, 0, true)"
        ),
        {"id": uuid.uuid4(), "u": uid, "s": song_id, "d": DAY},
    )

    outcome, _ = await _rate(db, uid, sid, item, MID_RATING)

    assert outcome.fanout.daily_verdict_written
    assert not outcome.fanout.daily_verdict_conflict


async def test_a_rated_song_item_writes_no_drill_rows(db):
    """The two halves of the fan-out are exclusive — a song is not a drill."""
    uid, song_id = await _user_with_a_song(db)
    sid, items = await _session(db, uid, song_id=song_id)
    item = _first_rated_song(items)

    await _rate(db, uid, sid, item, MID_RATING)

    count = await db.scalar(
        text("SELECT count(*) FROM drill_attempts WHERE user_id = :u"), {"u": uid}
    )
    assert count == 0
