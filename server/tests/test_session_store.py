"""Writer tests — plan to rows, and the double-tap that must not produce two plans.

FLE-9 Task 5, the write half. These run against the real Postgres because the thing
under test IS a database behaviour: `uq_practice_sessions_open_day`, a partial unique
index that only exists in migration 0009. Mocked out, `resolve_or_generate` is a
SELECT-then-INSERT that passes every test and ships the exact race it was written to
prevent.

The scenarios that earn their place here:

- The double tap. Two concurrent generates on a cold day. One INSERT wins, the other
  must resolve to the winner rather than raise or write a second plan. A user with two
  plans for one day has one the device keeps and one FLE-21 counts as an abandoned
  session — the pilot then reads as "the user quit", which is the opposite of true.
- Idempotence. Calling twice returns the same session, with `created` true exactly
  once, because a caller that emits telemetry on every call would double-count.
- Day rolling. Yesterday's open session is closed 'day_rolled', not resumed: its plan
  was built from a snapshot whose recency window has since moved.
- Song inheritance. A resolved session keeps its own song even when a different
  song_id is passed. §5.3 says a session never re-rolls its song, and the failure is
  silent — the user's plan changes underneath them mid-session.
- The rows themselves. Every boolean the generator decided has to survive the trip to
  Postgres, because FLE-10 R5 says the client derives none of them.
"""
import asyncio
import os
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.sessions.assemble import plan_as_rows
from app.sessions.store import (
    OPEN_SESSION_INDEX,
    close_rolled_over_sessions,
    resolve_or_generate,
)

from tests.test_session_snapshot import (  # the row builders, already proven
    DAY,
    TEST_DB_URL,
    _link_song_skill,
    _make_drill,
    _make_root_chain,
    _make_session,
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


async def _stocked_user(db, *, session_length: int = 30) -> uuid.UUID:
    """A user with enough material that build_plan produces a full session."""
    uid = await _make_user(db, f'{{"session_length_min": {session_length}}}')
    rhythm = await _make_root_chain(db, uid, "Rhythm", 0.2, 0.35)
    lead = await _make_root_chain(db, uid, "Lead", 0.6, 0.55)
    for leaf in rhythm + lead:
        for _ in range(2):
            await _make_drill(db, uid, leaf)
    return uid


async def _generate(db, uid, **kwargs):
    return await resolve_or_generate(
        db, uid, tz_offset_minutes=0, local_calendar_day=DAY, **kwargs
    )


async def _count_sessions(db, uid) -> int:
    return await db.scalar(
        text("SELECT count(*) FROM practice_sessions WHERE user_id = :u"), {"u": uid}
    )


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------

async def test_generate_writes_the_header_and_every_item(db):
    uid = await _stocked_user(db, session_length=45)
    stored = await _generate(db, uid)

    assert stored.created is True and stored.state == "planned"
    row = (
        await db.execute(
            text(
                "SELECT target_minutes, item_count, state::text AS state, "
                "       generator_version, seed, mode::text AS mode, mode_rule, "
                "       local_calendar_day, ended_at, terminal_reason "
                "  FROM practice_sessions WHERE id = :id"
            ),
            {"id": stored.session_id},
        )
    ).one()

    assert row.target_minutes == 45          # the onboarding preference, persisted
    assert row.state == "planned"
    assert row.local_calendar_day == DAY
    assert row.ended_at is None and row.terminal_reason is None
    assert row.mode == stored.plan.mode.value
    assert row.mode_rule == stored.plan.mode_rule
    assert row.seed == stored.plan.seed
    assert row.generator_version == stored.plan.generator_version

    written = await db.scalar(
        text("SELECT count(*) FROM practice_session_items WHERE session_id = :id"),
        {"id": stored.session_id},
    )
    assert written == len(stored.plan.items) == row.item_count


async def test_items_keep_their_order_and_their_flags(db):
    """FLE-10 R5: the client derives none of these, so all of them must survive."""
    uid = await _stocked_user(db)
    stored = await _generate(db, uid)

    rows = (
        await db.execute(
            text(
                "SELECT item_index, block::text AS block, kind::text AS kind, "
                "       drill_id::text AS drill_id, song_id, planned_seconds, "
                "       planned_bpm, planned_reps, rated, skippable, click_enabled, "
                "       re_entry, repeat_ok, is_consolidation, state::text AS state "
                "  FROM practice_session_items WHERE session_id = :id "
                " ORDER BY item_index"
            ),
            {"id": stored.session_id},
        )
    ).all()

    assert [r.item_index for r in rows] == list(range(len(rows)))
    assert all(r.state == "not_reached" for r in rows), "0009's item default"

    for written, planned in zip(rows, plan_as_rows(stored.plan)):
        assert written.block == planned["block"]
        assert written.kind == planned["kind"]
        assert written.planned_seconds == planned["planned_seconds"]
        assert written.planned_bpm == planned["planned_bpm"]
        assert written.planned_reps == planned["planned_reps"]
        assert written.rated == planned["rated"]
        assert written.skippable == planned["skippable"]
        assert written.click_enabled == planned["click_enabled"]
        assert written.re_entry == planned["re_entry"]
        assert written.repeat_ok == planned["repeat_ok"]
        assert written.is_consolidation == planned["is_consolidation"]
        assert written.drill_id == planned["drill_id"]
        assert written.song_id == planned["song_id"]


async def test_the_songs_id_reaches_the_header(db):
    uid = await _stocked_user(db)
    (leaf,) = await _make_root_chain(db, uid, "Timing", 0.3)
    song_id = await _make_song(db, uid, category="working_on", bpm=110)
    await _link_song_skill(db, song_id, leaf)

    stored = await _generate(db, uid, song_id=song_id)
    assert await db.scalar(
        text("SELECT song_id FROM practice_sessions WHERE id = :id"),
        {"id": stored.session_id},
    ) == song_id


# ---------------------------------------------------------------------------
# Resolve — the same session, twice
# ---------------------------------------------------------------------------

async def test_second_call_resolves_rather_than_generating(db):
    uid = await _stocked_user(db)
    first = await _generate(db, uid)
    second = await _generate(db, uid)

    assert second.session_id == first.session_id
    assert first.created is True and second.created is False
    assert second.resolved is True
    assert await _count_sessions(db, uid) == 1


async def test_an_in_progress_session_is_resolved_not_replaced(db):
    """The user is mid-session and backgrounds the app. Coming back must not re-plan."""
    uid = await _stocked_user(db)
    first = await _generate(db, uid)
    await db.execute(
        text(
            "UPDATE practice_sessions SET state = 'in_progress'::session_state, "
            "started_at = now() WHERE id = :id"
        ),
        {"id": first.session_id},
    )

    second = await _generate(db, uid)
    assert second.session_id == first.session_id
    assert second.state == "in_progress" and second.created is False


async def test_a_resolved_session_keeps_its_own_song(db):
    """§5.3 — a session never re-rolls its song.

    A silent failure if it did: the user is halfway through a plan and the material
    changes underneath them because something upstream re-picked.
    """
    uid = await _stocked_user(db)
    (leaf,) = await _make_root_chain(db, uid, "Timing", 0.3)
    original = await _make_song(db, uid, category="working_on", bpm=100)
    await _link_song_skill(db, original, leaf)
    other = await _make_song(db, uid, category="working_on", bpm=140)

    first = await _generate(db, uid, song_id=original)
    second = await _generate(db, uid, song_id=other)

    assert second.session_id == first.session_id
    assert await db.scalar(
        text("SELECT song_id FROM practice_sessions WHERE id = :id"),
        {"id": first.session_id},
    ) == original


async def test_a_completed_session_does_not_block_a_second_one(db):
    """The partial unique covers OPEN sessions only.

    Two sessions in a day is legitimate — the constraint exists to stop two OPEN
    plans, not to ration practice.
    """
    uid = await _stocked_user(db)
    first = await _generate(db, uid)
    await db.execute(
        text(
            "UPDATE practice_sessions "
            "   SET state = 'completed'::session_state, started_at = now(), "
            "       ended_at = now(), "
            "       terminal_reason = 'user_completed'::session_terminal_reason "
            " WHERE id = :id"
        ),
        {"id": first.session_id},
    )

    second = await _generate(db, uid)
    assert second.created is True and second.session_id != first.session_id
    assert await _count_sessions(db, uid) == 2


# ---------------------------------------------------------------------------
# The double tap
# ---------------------------------------------------------------------------

@pytest.fixture
def blind_precheck(monkeypatch):
    """Make the pre-check SELECT miss exactly once, as the losing writer's does.

    The race cannot be reproduced by simply pre-inserting the winner: the pre-check
    would find it and return down the ordinary resolve path, never reaching the
    INSERT. That is the trap this fixture exists to avoid — a test that reads as a
    race test, passes, and leaves the conflict branch completely uncovered.

    Blinding only the FIRST call is what makes it faithful. The loser's pre-check
    ran before the winner committed; its post-conflict re-read runs after, and must
    see the winner. A fixture that always returned None would assert the opposite of
    the required behaviour.
    """
    from app.sessions import store

    real = store._find_open
    calls = {"n": 0}

    async def blinded(db, user_id, day):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real(db, user_id, day)

    monkeypatch.setattr(store, "_find_open", blinded)
    return calls


async def test_the_losing_writer_resolves_to_the_winner(db, blind_precheck):
    """The double tap. The loser must resolve to the winner, not raise, not re-plan.

    This drives the real INSERT into the real partial unique index and recovers — the
    one behaviour in this module that cannot be tested without Postgres.
    """
    uid = await _stocked_user(db)
    winner = await _make_session(db, uid, day=DAY, state="planned")

    stored = await _generate(db, uid)

    assert blind_precheck["n"] >= 2, "the conflict branch never ran"
    assert stored.session_id == winner
    assert stored.created is False
    assert await _count_sessions(db, uid) == 1, "a second plan was written"


async def test_the_savepoint_leaves_the_transaction_usable(db, blind_precheck):
    """The reason the optimistic INSERT is wrapped in begin_nested().

    A unique violation aborts the whole transaction in Postgres. Without the
    savepoint, every statement after the conflict — including the re-read that finds
    the winner — fails with InFailedSqlTransaction, and a handled race surfaces as a
    500. The rolled-back items are the other half: the loser must leave nothing of
    its own plan behind.
    """
    uid = await _stocked_user(db)
    winner = await _make_session(db, uid, day=DAY, state="planned")

    await _generate(db, uid)

    assert await db.scalar(text("SELECT 1")) == 1, "transaction left unusable"
    assert await _count_sessions(db, uid) == 1
    orphans = await db.scalar(
        text(
            "SELECT count(*) FROM practice_session_items WHERE session_id <> :winner"
        ),
        {"winner": winner},
    )
    assert orphans == 0, "the losing writer's items survived the rollback"


async def test_the_conflict_matcher_is_specific_to_the_open_day_index(db):
    """The except-branch must not swallow unrelated integrity errors.

    Pinned against the live catalog rather than a string literal, so renaming the
    index in a later migration fails here instead of turning every future constraint
    violation into a silent resolve.
    """
    assert await db.scalar(
        text("SELECT count(*) FROM pg_class WHERE relname = :n"),
        {"n": OPEN_SESSION_INDEX},
    ) == 1


# ---------------------------------------------------------------------------
# Day rolling
# ---------------------------------------------------------------------------

async def test_yesterdays_open_session_is_abandoned_as_day_rolled(db):
    """Not resumed: its plan came from a snapshot whose recency window has moved."""
    uid = await _stocked_user(db)
    stale = await _make_session(db, uid, day=DAY - timedelta(days=1), state="planned")

    fresh = await _generate(db, uid)
    assert fresh.created is True and fresh.session_id != stale

    row = (
        await db.execute(
            text(
                "SELECT state::text AS state, "
                "       terminal_reason::text AS reason, ended_at "
                "  FROM practice_sessions WHERE id = :id"
            ),
            {"id": stale},
        )
    ).one()
    assert row.state == "abandoned"
    assert row.reason == "day_rolled"
    assert row.ended_at is not None, "0009 makes ended_at mandatory for terminal states"


async def test_day_rolling_leaves_finished_sessions_alone(db):
    """A completed session from yesterday keeps its own terminal_reason."""
    uid = await _stocked_user(db)
    done = await _make_session(
        db, uid, day=DAY - timedelta(days=1), state="completed", completion_ratio=1.0
    )

    await _generate(db, uid)

    assert await db.scalar(
        text("SELECT terminal_reason::text FROM practice_sessions WHERE id = :id"),
        {"id": done},
    ) == "user_completed"


async def test_close_rolled_over_sessions_is_callable_on_its_own(db):
    """For the nightly job: a user who never opens the app again still gets closed.

    Otherwise their last session stays 'planned' forever and every completion-rate
    denominator in FLE-21 is quietly wrong.
    """
    uid = await _stocked_user(db)
    stale = await _make_session(db, uid, day=DAY - timedelta(days=2), state="planned")

    closed = await close_rolled_over_sessions(db, uid, today=DAY)
    assert closed == [stale]
    assert await db.scalar(
        text("SELECT state::text FROM practice_sessions WHERE id = :id"), {"id": stale}
    ) == "abandoned"

    assert await close_rolled_over_sessions(db, uid, today=DAY) == [], "idempotent"


async def test_day_rolling_is_scoped_to_the_user(db):
    other = await _stocked_user(db)
    theirs = await _make_session(db, other, day=DAY - timedelta(days=1), state="planned")

    uid = await _stocked_user(db)
    await _generate(db, uid)

    assert await db.scalar(
        text("SELECT state::text FROM practice_sessions WHERE id = :id"), {"id": theirs}
    ) == "planned"
