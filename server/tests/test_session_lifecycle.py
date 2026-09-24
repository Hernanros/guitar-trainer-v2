"""Telemetry lifecycle tests — FLE-21, the half of the session record that says what
the user DID.

These run against the real Postgres, because most of what is under test IS a database
behaviour: the monotonic lattice is `WHERE state = 'not_reached'`, the clamp is
`LEAST(..., 4 * planned_seconds)`, the sweep's day-roll is a per-row date comparison
against that row's own tz_offset_minutes. Mocked out, all three pass while shipping
nothing.

The selection rule for what earns a test here: **would its absence let the pilot
produce data nobody can read?** FLE-21 exists because a readout that silently degrades
to self-report is not discovered until it is being written, months after the sessions
it describes. So the assertions below are weighted towards the failures that are
invisible at the time they happen:

- **Out-of-order and duplicate delivery.** The player's outbox is fire-and-forget; it
  WILL flush `complete(2)` ahead of `enter(2)` and it WILL deliver twice. A handler
  that assumed order would strand items `in_progress`, and a stranded item is counted
  as neither reached nor skipped — every completion number then drifts, upward,
  quietly. Tested in both directions.
- **`skipped` vs `not_reached`.** FLE-13 asks "where did they bail" AND "which block do
  they skip". They are the same column unless a skip is an explicit event against a row
  that already exists. This is the one decision FLE-21 §2 calls unretrofittable.
- **The sweep.** Its absence does not lose data; it leaves sessions `in_progress`
  forever, which REMOVES them from the completion-rate denominator. A metric that
  improves because rows went missing reads as success, so it gets three tests.
- **§4's five readout queries, run end-to-end.** The last test builds a two-day history
  and answers every question in FLE-21's done-when table with SQL. If that test passes,
  the issue is done; if it fails, nothing else here matters.

Fixture construction (private engine, per-test connection, synchronous teardown) is
inherited from test_session_snapshot.py — the reasoning is documented there and the
suite-wide event-loop defect it works around is filed as FLE-62.
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.sessions import lifecycle
from app.sessions.lifecycle import (
    ACTIVE_SECONDS_CLAMP_FACTOR,
    ItemNotFound,
    RatingNotPermitted,
    SessionNotFound,
)
from app.sessions.store import resolve_or_generate

from tests.test_session_snapshot import (  # row builders, already proven by FLE-9
    DAY,
    TEST_DB_URL,
    _make_drill,
    _make_root_chain,
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
# Scenario builders
# ---------------------------------------------------------------------------


async def _stocked_user(db, *, session_length: int = 30) -> uuid.UUID:
    """A user with enough material that build_plan produces a full session."""
    uid = await _make_user(db, f'{{"session_length_min": {session_length}}}')
    rhythm = await _make_root_chain(db, uid, "Rhythm", 0.2, 0.35)
    lead = await _make_root_chain(db, uid, "Lead", 0.6, 0.55)
    for leaf in rhythm + lead:
        for _ in range(2):
            await _make_drill(db, uid, leaf)
    return uid


async def _session(db, uid, *, day: date = DAY):
    """Generate a real session through the FLE-9 writer. Returns (id, item rows)."""
    stored = await resolve_or_generate(
        db, uid, tz_offset_minutes=0, local_calendar_day=day
    )
    items = await lifecycle.load_items(db, stored.session_id)
    assert items, "generator produced no items — scenario is not exercising anything"
    return stored.session_id, items


async def _row(db, sid):
    return (
        await db.execute(
            text(
                "SELECT state::text AS state, terminal_reason::text AS terminal_reason, "
                "started_at, ended_at, completion_ratio, elapsed_active_seconds, "
                "last_item_index_reached, item_count, local_calendar_day "
                "FROM practice_sessions WHERE id = :s"
            ),
            {"s": sid},
        )
    ).first()


async def _item(db, sid, idx):
    return (
        await db.execute(
            text(
                "SELECT state::text AS state, started_at, ended_at, active_seconds, "
                "rating::text AS rating, completed_reps, planned_seconds, "
                "advance_mode::text AS advance_mode, rated, skippable, block::text AS block "
                "FROM practice_session_items WHERE session_id = :s AND item_index = :i"
            ),
            {"s": sid, "i": idx},
        )
    ).first()


async def _first_rated_index(db, sid) -> int | None:
    return await db.scalar(
        text(
            "SELECT item_index FROM practice_session_items "
            "WHERE session_id = :s AND rated ORDER BY item_index LIMIT 1"
        ),
        {"s": sid},
    )


async def _first_unrated_index(db, sid) -> int | None:
    return await db.scalar(
        text(
            "SELECT item_index FROM practice_session_items "
            "WHERE session_id = :s AND NOT rated ORDER BY item_index LIMIT 1"
        ),
        {"s": sid},
    )


# ---------------------------------------------------------------------------
# /start — write-once (FLE-21 R5)
# ---------------------------------------------------------------------------


async def test_start_opens_the_session(db):
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)

    before = await _row(db, sid)
    assert before.state == "planned"
    assert before.started_at is None, (
        "generation must NOT set started_at — FLE-21 §1 keeps generated_at and "
        "started_at separate so an ignored plan is not counted as a bail"
    )

    await lifecycle.start_session(db, uid, sid)

    after = await _row(db, sid)
    assert after.state == "in_progress"
    assert after.started_at is not None


async def test_start_is_write_once(db):
    """R5. started_at is the day-7 return metric; a resume must not re-date it."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)

    first = (await lifecycle.start_session(db, uid, sid)).started_at
    second = (await lifecycle.start_session(db, uid, sid)).started_at

    assert first == second, "a second /start moved started_at — day-7 return is now wrong"


async def test_start_rejects_another_users_session(db):
    """Access control, not convenience: a crafted UUID must not write onto another
    participant's record, because the readout cannot tell afterwards."""
    owner = await _stocked_user(db)
    intruder = await _make_user(db)
    sid, _ = await _session(db, owner)

    with pytest.raises(SessionNotFound):
        await lifecycle.start_session(db, intruder, sid)


# ---------------------------------------------------------------------------
# Item lattice (FLE-21 §5.3, R9)
# ---------------------------------------------------------------------------


async def test_enter_marks_in_progress_and_bumps_high_water(db):
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)

    outcome, session = await lifecycle.enter_item(db, uid, sid, 0)

    assert outcome.applied is True
    assert outcome.state == "in_progress"
    assert session.last_item_index_reached == 0
    assert (await _item(db, sid, 0)).started_at is not None


async def test_enter_after_complete_is_a_noop(db):
    """The outbox can flush `complete(2)` ahead of `enter(2)`. The late enter must not
    resurrect a finished item — a resurrected item never reaches a terminal state
    again, and stranded items quietly leave the completion numerator."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)
    idx = await _first_unrated_index(db, sid)

    await lifecycle.complete_item(db, uid, sid, idx, active_seconds=30)
    outcome, _ = await lifecycle.enter_item(db, uid, sid, idx)

    assert outcome.applied is False
    assert outcome.state == "completed"
    assert (await _item(db, sid, idx)).state == "completed"


async def test_last_item_index_reached_never_decreases(db):
    """It is a high-water mark, not a cursor — FLE-13's "where did they bail" is
    meaningless if a late event for an earlier item rewinds it."""
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    assert len(items) >= 3

    await lifecycle.enter_item(db, uid, sid, 2)
    _, session = await lifecycle.enter_item(db, uid, sid, 0)

    assert session.last_item_index_reached == 2


async def test_terminal_transition_on_not_reached_item_is_legal(db):
    """§5.3. A dropped `enter` must never cost the `complete`. started_at is
    back-filled with the server receipt time — an approximation, and a better one than
    having no record that an item the user demonstrably finished was ever reached."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)
    idx = await _first_unrated_index(db, sid)

    assert (await _item(db, sid, idx)).state == "not_reached"
    outcome, _ = await lifecycle.complete_item(db, uid, sid, idx, active_seconds=42)

    row = await _item(db, sid, idx)
    assert outcome.applied is True
    assert row.state == "completed"
    assert row.started_at is not None
    assert row.ended_at is not None


async def test_complete_after_skip_is_last_write_wins(db):
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)
    idx = await _first_unrated_index(db, sid)

    await lifecycle.skip_item(db, uid, sid, idx)
    assert (await _item(db, sid, idx)).state == "skipped"

    await lifecycle.complete_item(db, uid, sid, idx, active_seconds=10)
    assert (await _item(db, sid, idx)).state == "completed"


async def test_unknown_item_index_raises(db):
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)

    with pytest.raises(ItemNotFound):
        await lifecycle.enter_item(db, uid, sid, len(items) + 50)


# ---------------------------------------------------------------------------
# Ratings (FLE-21 §5.1, R1)
# ---------------------------------------------------------------------------


async def test_null_rating_still_lands_completed(db):
    """R1/F1. Warm-up, song_play, consolidation and any slot dropped by
    MAX_RATING_TAPS are unrated BY DESIGN — in a 15-minute session that is most of
    them. Requiring a rating would force the player to fabricate data."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)
    idx = await _first_unrated_index(db, sid)

    await lifecycle.complete_item(db, uid, sid, idx, active_seconds=60, rating=None)

    row = await _item(db, sid, idx)
    assert row.state == "completed"
    assert row.rating is None


async def test_rating_on_an_unrated_item_is_rejected(db):
    """§5.1. Checked against the SERVER's `rated` flag — FLE-10 R5 says the client
    derives no flags, and this is where that stops being a slogan. FLE-4 §5.4: a
    rating on the consolidation item converts the win back into an assessment."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)
    idx = await _first_unrated_index(db, sid)

    with pytest.raises(RatingNotPermitted):
        await lifecycle.complete_item(
            db, uid, sid, idx, active_seconds=60, rating="thats_what_im_looking_for"
        )

    assert (await _item(db, sid, idx)).state == "not_reached", "rejected write leaked"


async def test_rating_is_stored_on_a_rated_item(db):
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)
    idx = await _first_rated_index(db, sid)
    assert idx is not None, "generator produced no rated items — scenario is wrong"

    await lifecycle.complete_item(
        db,
        uid,
        sid,
        idx,
        active_seconds=120,
        rating="getting_closer",
        completed_reps=14,
        advance_mode="user_tap",
    )

    row = await _item(db, sid, idx)
    assert row.rating == "getting_closer"
    assert row.completed_reps == 14
    assert row.advance_mode == "user_tap"


async def test_duplicate_unrated_complete_does_not_erase_a_rating(db):
    """Losing a rating to a retry is data loss the USER can see. The outbox may flush
    an unrated duplicate behind the rated write; COALESCE keeps the tap."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)
    idx = await _first_rated_index(db, sid)

    await lifecycle.complete_item(
        db, uid, sid, idx, active_seconds=100, rating="not_my_tempo", completed_reps=8
    )
    await lifecycle.complete_item(db, uid, sid, idx, active_seconds=100, rating=None)

    row = await _item(db, sid, idx)
    assert row.rating == "not_my_tempo"
    assert row.completed_reps == 8


# ---------------------------------------------------------------------------
# active_seconds (FLE-21 §6)
# ---------------------------------------------------------------------------


async def test_active_seconds_is_clamped_to_4x_planned(db):
    """§6. active_seconds is NOT paused on background or screen-lock — the product is
    a propped-up phone and two hands on a guitar — so the cost of the rule is a
    forgotten session inflating the number. The clamp catches a phone left on a music
    stand overnight; 4x is generous on purpose, since FLE-4 §6 permits deliberate
    overrun and only OFFERS auto-advance at 1.5x."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)
    idx = await _first_unrated_index(db, sid)
    planned = (await _item(db, sid, idx)).planned_seconds

    outcome, session = await lifecycle.complete_item(
        db, uid, sid, idx, active_seconds=planned * 500
    )

    cap = ACTIVE_SECONDS_CLAMP_FACTOR * planned
    assert outcome.clamped is True
    assert outcome.active_seconds == cap
    assert (await _item(db, sid, idx)).active_seconds == cap
    # The readout identifies clamped items in SQL as active_seconds = 4 * planned.
    assert cap == 4 * planned


async def test_a_normal_duration_is_not_clamped(db):
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)
    idx = await _first_unrated_index(db, sid)
    planned = (await _item(db, sid, idx)).planned_seconds

    outcome, _ = await lifecycle.complete_item(
        db, uid, sid, idx, active_seconds=planned + 5
    )

    assert outcome.clamped is False
    assert outcome.active_seconds == planned + 5


async def test_duplicate_complete_does_not_double_count_elapsed(db):
    """elapsed_active_seconds is RECOMPUTED as a SUM, never incremented. §4 compares it
    against `ended_at - started_at` to spot a participant practising in fragments; an
    accumulator that grew on every retry would make everyone look fragmented."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)
    idx = await _first_unrated_index(db, sid)

    _, first = await lifecycle.complete_item(db, uid, sid, idx, active_seconds=90)
    _, second = await lifecycle.complete_item(db, uid, sid, idx, active_seconds=90)

    assert first.elapsed_active_seconds == 90
    assert second.elapsed_active_seconds == 90


# ---------------------------------------------------------------------------
# skipped vs not_reached — FLE-21 §2's unretrofittable decision
# ---------------------------------------------------------------------------


async def test_skipped_and_not_reached_are_distinguishable(db):
    """The whole reason every item row is INSERTed at generation time. FLE-13 asks both
    "where did they bail" and "which block do they skip"; if rows appeared only when
    touched, both a skipped block and a never-reached block would simply be absent and
    the second question could not be answered at all."""
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    assert len(items) >= 3

    await lifecycle.enter_item(db, uid, sid, 0)
    await lifecycle.complete_item(db, uid, sid, 0, active_seconds=60)
    await lifecycle.skip_item(db, uid, sid, 1)
    # index 2+ deliberately untouched

    states = dict(
        (r.item_index, r.state)
        for r in (
            await db.execute(
                text(
                    "SELECT item_index, state::text AS state FROM practice_session_items "
                    "WHERE session_id = :s ORDER BY item_index"
                ),
                {"s": sid},
            )
        ).all()
    )
    assert states[0] == "completed"
    assert states[1] == "skipped"
    assert states[2] == "not_reached"


async def test_skip_records_terminal_only_progress(db):
    """§5.3 — active_seconds and completed_reps are accepted on /skip and /complete,
    and nowhere else. Mid-item depth lives in the client's MMKV checkpoint."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)

    await lifecycle.skip_item(db, uid, sid, 1, active_seconds=7)

    row = await _item(db, sid, 1)
    assert row.state == "skipped"
    assert row.active_seconds == 7
    assert row.ended_at is not None


# ---------------------------------------------------------------------------
# Session completion
# ---------------------------------------------------------------------------


async def test_complete_session_sets_terminal_state_and_ratio(db):
    uid = await _stocked_user(db)
    sid, items = await _session(db, uid)
    await lifecycle.start_session(db, uid, sid)
    await lifecycle.complete_item(db, uid, sid, 0, active_seconds=60)
    await lifecycle.skip_item(db, uid, sid, 1)

    session, tally = await lifecycle.complete_session(db, uid, sid)

    assert session.state == "completed"
    assert session.terminal_reason == "user_completed"
    assert session.ended_at is not None
    assert tally.done_items == 1
    assert tally.skipped_items == 1
    assert tally.planned_items == len(items)
    # Completed items only — a session the user skipped four items out of is not
    # 100% complete in any sense the readout wants.
    assert float(session.completion_ratio) == pytest.approx(1 / len(items), abs=0.001)


async def test_complete_session_is_idempotent(db):
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid)
    await lifecycle.start_session(db, uid, sid)

    first, _ = await lifecycle.complete_session(db, uid, sid)
    second, _ = await lifecycle.complete_session(db, uid, sid)

    assert first.ended_at == second.ended_at, "a repeat /complete re-dated ended_at"


async def test_user_completion_outranks_the_clocks_abandonment(db):
    """§3.1 — the midnight-crossing case. A user practising at 23:55 and still playing
    at 00:05 gets day-rolled underneath them by the sweep. When they then tap "done",
    that is the truth and the sweep's guess is not. R7 deleted `superseded`, so the
    clock is the only thing that can abandon a session — hence every abandonment is
    overridable here."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid, day=DAY - timedelta(days=1))
    await lifecycle.start_session(db, uid, sid)

    await lifecycle.sweep_open_sessions(db)
    swept = await _row(db, sid)
    assert swept.state == "abandoned" and swept.terminal_reason == "day_rolled"

    session, _ = await lifecycle.complete_session(db, uid, sid)

    assert session.state == "completed"
    assert session.terminal_reason == "user_completed"


async def test_item_writes_against_a_terminal_session_are_recorded(db):
    """§3.1 — the player keeps running after the sweep fires. Its writes are accepted
    and recorded; they do not resurrect the session and they do not error."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid, day=DAY - timedelta(days=1))
    await lifecycle.start_session(db, uid, sid)
    await lifecycle.sweep_open_sessions(db)

    outcome, session = await lifecycle.complete_item(db, uid, sid, 0, active_seconds=30)

    assert outcome.applied is True
    assert (await _item(db, sid, 0)).state == "completed"
    assert session.state == "abandoned", "an item write resurrected a terminal session"


# ---------------------------------------------------------------------------
# The sweep (FLE-21 §3)
# ---------------------------------------------------------------------------


async def test_sweep_closes_a_rolled_over_session(db):
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid, day=DAY - timedelta(days=1))
    await lifecycle.start_session(db, uid, sid)

    result = await lifecycle.sweep_open_sessions(db)

    row = await _row(db, sid)
    assert result.day_rolled >= 1
    assert row.state == "abandoned"
    assert row.terminal_reason == "day_rolled"
    assert row.ended_at is not None
    assert row.completion_ratio is not None


async def test_sweep_leaves_todays_open_session_alone(db):
    """The sweep must not close a session the user is in the middle of. Its day-roll
    predicate is evaluated against the row's OWN tz_offset_minutes, because the pilot
    roster is not in one timezone."""
    uid = await _stocked_user(db)
    today = await db.scalar(
        text("SELECT DATE((now() AT TIME ZONE 'UTC') + (0 * INTERVAL '1 minute'))")
    )
    sid, _ = await _session(db, uid, day=today)
    await lifecycle.start_session(db, uid, sid)

    await lifecycle.sweep_open_sessions(db)

    assert (await _row(db, sid)).state == "in_progress"


async def test_sweep_keeps_an_ignored_plan_out_of_the_denominator(db):
    """§1. A session generated and never opened is an IGNORED PLAN, not a bail. The
    sweep closes the row — otherwise it stays open forever — but started_at remains
    NULL, and §4's completion-rate query filters on `started_at IS NOT NULL`."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid, day=DAY - timedelta(days=1))
    # deliberately never started

    await lifecycle.sweep_open_sessions(db)

    row = await _row(db, sid)
    assert row.state == "abandoned"
    assert row.started_at is None


async def test_sweep_is_idempotent(db):
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid, day=DAY - timedelta(days=1))
    await lifecycle.start_session(db, uid, sid)

    await lifecycle.sweep_open_sessions(db)
    ended_first = (await _row(db, sid)).ended_at
    await lifecycle.sweep_open_sessions(db)

    assert (await _row(db, sid)).ended_at == ended_first


async def test_timeout_backstop_fires_on_a_corrupt_tz_offset(db):
    """§3's second condition. A row whose local_calendar_day is in the FUTURE can never
    day-roll, so without this it would stay open forever. It exists purely as
    protection against a corrupt tz_offset_minutes and should fire approximately never
    — when it does, that is a bug report, not a metric, which is why the sweep logs it
    at WARNING."""
    uid = await _stocked_user(db)
    sid, _ = await _session(db, uid, day=DAY)
    await lifecycle.start_session(db, uid, sid)
    # Corrupt the row the way a bad client clock would: a day that cannot roll, and
    # activity that stopped well beyond the 36-hour window.
    await db.execute(
        text(
            "UPDATE practice_sessions "
            "SET local_calendar_day = (now() AT TIME ZONE 'UTC')::date + 5, "
            "    last_activity_at = now() - interval '40 hours' "
            "WHERE id = :s"
        ),
        {"s": sid},
    )

    result = await lifecycle.sweep_open_sessions(db)

    row = await _row(db, sid)
    assert result.timed_out >= 1
    assert row.state == "abandoned"
    assert row.terminal_reason == "timeout"


# ---------------------------------------------------------------------------
# The coverage check — FLE-21's done-when table, answered in SQL
# ---------------------------------------------------------------------------


async def test_every_readout_question_is_answerable(db):
    """The test this whole issue is for.

    Builds a two-day history for one user — day 1 completed, day 2 started, one block
    skipped, the tail never reached — and then answers all five questions from FLE-21
    §4 with server-side SQL only. No analytics SDK, no self-report, no client input
    beyond the numbers the player POSTs.

    If this passes, the pilot readout can be written. If it fails, the readout
    degrades to what participants SAY they did — which is the failure FLE-21 was filed
    to prevent, and which is not detectable until months after the data was lost.
    """
    uid = await _stocked_user(db)
    day1 = DAY - timedelta(days=7)
    day2 = DAY - timedelta(days=1)

    # --- Day 1: opened and finished.
    sid1, items1 = await _session(db, uid, day=day1)
    await lifecycle.start_session(db, uid, sid1)
    for i in range(len(items1)):
        await lifecycle.enter_item(db, uid, sid1, i)
        await lifecycle.complete_item(db, uid, sid1, i, active_seconds=60)
    await lifecycle.complete_session(db, uid, sid1)

    # --- Day 2: opened, skipped item 1, bailed after item 2.
    sid2, items2 = await _session(db, uid, day=day2)
    await lifecycle.start_session(db, uid, sid2)
    await lifecycle.enter_item(db, uid, sid2, 0)
    await lifecycle.complete_item(db, uid, sid2, 0, active_seconds=120)
    await lifecycle.skip_item(db, uid, sid2, 1)
    await lifecycle.enter_item(db, uid, sid2, 2)
    # ...and walked away. The sweep closes it.
    await lifecycle.sweep_open_sessions(db)

    # Q1 — did they return on day 7? Distinct local days with a started session.
    active_days = await db.scalar(
        text(
            "SELECT count(DISTINCT local_calendar_day) FROM practice_sessions "
            "WHERE user_id = :u AND started_at IS NOT NULL"
        ),
        {"u": uid},
    )
    span = await db.scalar(
        text(
            "SELECT max(local_calendar_day) - min(local_calendar_day) "
            "FROM practice_sessions WHERE user_id = :u AND started_at IS NOT NULL"
        ),
        {"u": uid},
    )
    assert active_days == 2
    assert span == 6, "the day-7 return window is derivable from started_at alone"

    # Q2 — what share of sessions were completed?
    completed, terminal = (
        await db.execute(
            text(
                "SELECT count(*) FILTER (WHERE state = 'completed') AS completed, "
                "       count(*) FILTER (WHERE state IN ('completed','abandoned')) AS terminal "
                "  FROM practice_sessions "
                " WHERE user_id = :u AND started_at IS NOT NULL"
            ),
            {"u": uid},
        )
    ).first()
    assert (completed, terminal) == (1, 2), "completion rate is 1/2, server-side"

    # Q3 — where did they bail?
    bail = await _row(db, sid2)
    assert bail.state == "abandoned"
    assert bail.last_item_index_reached == 2, "high-water mark names the bail point"

    # Q4 — which block do they skip? Separable from not_reached only because §2
    # writes every item row at generation time, and the denominator excludes
    # unskippable items per §4.1.
    skipped = (
        await db.execute(
            text(
                "SELECT block::text AS block, count(*) AS n FROM practice_session_items "
                "WHERE session_id = :s AND state = 'skipped' AND skippable GROUP BY block"
            ),
            {"s": sid2},
        )
    ).all()
    not_reached = await db.scalar(
        text(
            "SELECT count(*) FROM practice_session_items "
            "WHERE session_id = :s AND state = 'not_reached'"
        ),
        {"s": sid2},
    )
    assert sum(r.n for r in skipped) == 1, "the skip is attributable to a block"
    assert not_reached == len(items2) - 3, "untouched items are on the record, as such"

    # Q5 — did the generated duration match reality? All three numbers present.
    duration = (
        await db.execute(
            text(
                "SELECT target_minutes, elapsed_active_seconds, "
                "       EXTRACT(EPOCH FROM (ended_at - started_at)) AS wall_seconds "
                "  FROM practice_sessions WHERE id = :s"
            ),
            {"s": sid2},
        )
    ).first()
    assert duration.target_minutes > 0
    assert duration.elapsed_active_seconds == 120
    assert duration.wall_seconds is not None, (
        "server-observed wall clock must corroborate the client-reported active time"
    )

    # §7 — no PII. Neither table has a free-text column; every non-numeric value is an
    # enum or a foreign key, so nothing participant-authored can reach the readout.
    text_columns = await db.scalar(
        text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name IN ('practice_sessions','practice_session_items') "
            "  AND data_type IN ('text','character varying') "
            "  AND column_name NOT IN ('generator_version','seed','mode_rule')"
        )
    )
    assert text_columns == 0, "a free-text column can leak participant text into the readout"
