"""Alembic migration 0009 tests — the session engine's persistence layer.

FLE-9 Task 5. These are the enforcement half of the contract documented at the top of
0009_practice_sessions.py and pinned in FLE-21's "Pilot Session Telemetry" rev 2.

The choice of what to test here is deliberate. A test that only asserts "column X
exists" catches a typo; the assertions below catch the things that would let the
PILOT produce data nobody can read, which is the failure mode that cannot be fixed
after the fact:

- The open-session partial unique. Without it, resolve-or-generate is a read-then-write
  race and a double-tap gives a user two plans for one day. The test also pins the
  half that is easy to get wrong in the other direction: a COMPLETED session must NOT
  block a second session the same day.
- `not_reached` as the item default. FLE-21 §2's one expensive-to-retrofit decision.
- The state/terminal_reason and state/ended_at pairings. An 'abandoned' row with no
  reason loses the day_rolled-vs-timeout split, and timeout is the signal that a
  tz_offset_minutes is corrupt rather than that a user walked away.
- `ck_session_items_rating_requires_rated`. FLE-4 §5.4 is explicit that a rating on
  the consolidation item converts the win back into an assessment.
- The drill_progress ladder's invariants: one row per (user, drill), rungs on the
  5-BPM grid, and mastered_at present exactly when state = 'mastered' (without it a
  mastered drill never enters maintenance and is excluded from selection forever).

Runs against the real Postgres test database (DATABASE_URL env var or default).
Requires `alembic upgrade head` to have been applied before running.
"""
import ast
import os
import pathlib
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

# Read the migration's enum literals via AST rather than importing it. conftest.py puts
# server/ on sys.path, where the local `server/alembic/` package shadows the installed
# alembic distribution, so exec'ing a migration under pytest raises ImportError. Every
# constant we want is a plain literal. Same technique as test_alembic_0007.py.
_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0009_practice_sessions.py"
)
_WANTED = (
    "SESSION_MODE",
    "SESSION_STATE",
    "SESSION_TERMINAL_REASON",
    "SESSION_ITEM_BLOCK",
    "SESSION_ITEM_KIND",
    "SESSION_ITEM_STATE",
    "ADVANCE_MODE",
    "DRILL_PROGRESS_STATE",
    "DRILL_ATTEMPT_OUTCOME",
)
_tree = ast.parse(_MIGRATION_PATH.read_text())
MIGRATION_ENUMS = {
    node.targets[0].id: tuple(ast.literal_eval(node.value))
    for node in _tree.body
    if isinstance(node, ast.Assign)
    and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id in _WANTED
}
assert set(MIGRATION_ENUMS) == set(_WANTED), (
    f"Migration 0009 no longer defines {set(_WANTED) - set(MIGRATION_ENUMS)} "
    "as module-level literals"
)

# The Python enums the ORM binds. Imported so a value added on one side and not the
# other fails here rather than at the first INSERT in production.
from app.models.db import (  # noqa: E402
    AdvanceMode,
    DrillAttemptOutcome,
    DrillProgressState,
    SessionItemBlock,
    SessionItemKind,
    SessionItemState,
    SessionMode,
    SessionState,
    SessionTerminalReason,
)

# PG type name -> (migration literal, ORM enum)
ENUM_TRIPLES = [
    ("session_mode", "SESSION_MODE", SessionMode),
    ("session_state", "SESSION_STATE", SessionState),
    ("session_terminal_reason", "SESSION_TERMINAL_REASON", SessionTerminalReason),
    ("session_item_block", "SESSION_ITEM_BLOCK", SessionItemBlock),
    ("session_item_kind", "SESSION_ITEM_KIND", SessionItemKind),
    ("session_item_state", "SESSION_ITEM_STATE", SessionItemState),
    ("advance_mode", "ADVANCE_MODE", AdvanceMode),
    ("drill_progress_state", "DRILL_PROGRESS_STATE", DrillProgressState),
    ("drill_attempt_outcome", "DRILL_ATTEMPT_OUTCOME", DrillAttemptOutcome),
]


# ---------------------------------------------------------------------------
# Test database setup — mirrors test_alembic_0007.py
# ---------------------------------------------------------------------------

def _make_test_db_url() -> str:
    raw = os.environ.get(
        "DATABASE_URL", "postgresql://gt:devpass@localhost:5433/guitar_trainer"
    )
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if raw.startswith(prefix):
            if prefix == "postgresql+asyncpg://":
                return raw
            return raw.replace(prefix, "postgresql+asyncpg://", 1)
    return raw


TEST_DB_URL = _make_test_db_url()

@pytest.fixture
async def db():
    """A private engine and CONNECTION per test, with a SYNCHRONOUS teardown.

    All three of those choices are load-bearing, and none is what the other
    DB-touching modules in this suite do — so it is worth saying why.

    0. An AsyncConnection, not an AsyncSession. These tests speak raw SQL, so the
       session layer buys nothing — and it costs: an AsyncSession that is not closed
       schedules an `AsyncSession.close()` coroutine during GC, which then gets
       awaited on whatever loop happens to be current. That is the same cross-loop
       failure as (2) arriving by the back door, and it is not preventable from a
       synchronous teardown. No session object, no finalizer to mis-schedule.

    1. Per test, on NullPool. The session-scoped engine that
       test_alembic_0003/0004/0006/0007 share pools connections ACROSS tests, and an
       asyncpg connection is bound to the event loop that opened it. Under
       pytest-asyncio 0.23.8 (with conftest.py's deprecated session-scoped
       `event_loop` override) different modules end up on different loops, so the
       pooled connection gets reused from the wrong one and the run dies with
       "got Future attached to a different loop". That is not hypothetical:
       test_alembic_0007 passes alone and fails 11 of its own tests in a full-suite
       run for exactly this reason, today, before this file existed.

    2. No `await` after the `yield`. A fixture finalizer is driven by
       `event_loop.run_until_complete(...)` against conftest.py's SESSION-scoped
       loop, not the per-function loop the test body ran on — so an
       `await session.rollback()` here would reach a connection owned by a loop that
       is closing, which is the same failure by a different route. Instead the
       teardown reaches the raw asyncpg connection and calls its synchronous
       `terminate()`: the transport closes immediately, the server rolls back the
       open transaction, and no loop is touched.

    Nothing below commits, so terminate-without-commit IS this module's isolation:
    every user, skill node, drill and session these tests create is discarded when
    the test that made it ends.

    The suite-wide loop management is a real defect and worth its own fix. This
    module works around it rather than adding to it — a test file that is green
    alone and red in CI teaches people to ignore CI.
    """
    engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)
    conn = await engine.connect()

    yield conn

    # Synchronous only. `driver_connection` is the asyncpg Connection and its
    # `terminate()` drops the transport without awaiting; the server rolls back the
    # open uncommitted transaction as the connection dies.
    conn.sync_connection.connection.dbapi_connection.driver_connection.terminate()
    # close=False: the transport is already gone, so there is nothing to close and
    # asking would queue async work. This just drops SQLAlchemy's references, which
    # is what stops a GC-time finalizer running on some other test's loop.
    engine.sync_engine.dispose(close=False)


# ---------------------------------------------------------------------------
# Row builders. Each returns the new id; all INSERTs are rolled back by the fixture.
# ---------------------------------------------------------------------------

async def _make_user(db) -> uuid.UUID:
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:id, '{}'::jsonb)"), {"id": uid}
    )
    return uid


async def _make_skill_node(db, user_id: uuid.UUID) -> uuid.UUID:
    nid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
            "VALUES (:id, :uid, :name, 'leaf', 0.0)"
        ),
        {"id": nid, "uid": user_id, "name": f"node-{nid.hex[:8]}"},
    )
    return nid


async def _make_drill(db, user_id: uuid.UUID, skill_node_id: uuid.UUID) -> uuid.UUID:
    did = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO drills (id, user_id, name, name_normalized, skill_node_id, "
            "song_specific, what, tab_snippet, start_bpm, target_bpm, repetitions, "
            "success_criterion) "
            "VALUES (:id, :uid, :name, :norm, :node, false, 'what', '{}'::jsonb, "
            "60, 100, 12, 'clean')"
        ),
        {
            "id": did,
            "uid": user_id,
            "name": f"drill-{did.hex[:8]}",
            "norm": f"drill{did.hex[:8]}",
            "node": skill_node_id,
        },
    )
    return did


async def _make_session(
    db,
    user_id: uuid.UUID,
    *,
    day: date,
    state: str = "planned",
    terminal_reason: str | None = None,
    ended_at: datetime | None = None,
    started_at: datetime | None = None,
    item_count: int = 4,
) -> uuid.UUID:
    sid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO practice_sessions "
            "(id, user_id, local_calendar_day, tz_offset_minutes, target_minutes, mode, "
            " allow_push, generator_version, seed, mode_rule, state, terminal_reason, "
            " ended_at, started_at, item_count) "
            "VALUES (:id, :uid, :day, 0, 30, 'BALANCED', true, 'session-gen/1.0.0', "
            " 'seed', 'default', CAST(:state AS session_state), "
            " CAST(:reason AS session_terminal_reason), :ended, :started, :items)"
        ),
        {
            "id": sid,
            "uid": user_id,
            "day": day,
            "state": state,
            "reason": terminal_reason,
            "ended": ended_at,
            "started": started_at,
            "items": item_count,
        },
    )
    return sid


async def _make_item(
    db,
    session_id: uuid.UUID,
    *,
    index: int = 0,
    block: str = "technique",
    kind: str = "drill",
    drill_id: uuid.UUID | None = None,
    song_id: int | None = None,
    rated: bool = True,
    rating: str | None = None,
) -> uuid.UUID:
    iid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO practice_session_items "
            "(id, session_id, item_index, block, kind, drill_id, song_id, planned_seconds, "
            " rated, skippable, click_enabled, rating) "
            "VALUES (:id, :sid, :idx, CAST(:block AS session_item_block), "
            " CAST(:kind AS session_item_kind), :drill, :song, 240, :rated, true, true, "
            " CAST(:rating AS rating_level))"
        ),
        {
            "id": iid,
            "sid": session_id,
            "idx": index,
            "block": block,
            "kind": kind,
            "drill": drill_id,
            "song": song_id,
            "rated": rated,
            "rating": rating,
        },
    )
    return iid


TODAY = date(2026, 9, 23)


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", ["practice_sessions", "practice_session_items", "drill_progress"])
async def test_table_exists(db, table):
    found = await db.scalar(
        text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}
    )
    assert found == table, f"migration 0009 did not create {table}"


@pytest.mark.parametrize("pg_name,literal_name,py_enum", ENUM_TRIPLES)
async def test_enum_values_agree_across_pg_migration_and_orm(db, pg_name, literal_name, py_enum):
    """The three places an enum value can be declared must not drift.

    A value added to the ORM but not to the type is an InvalidTextRepresentation at
    INSERT time — in production, on the first user who hits the new branch.
    """
    rows = await db.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = :n "
            "ORDER BY e.enumsortorder"
        ),
        {"n": pg_name},
    )
    in_pg = tuple(r[0] for r in rows)
    assert in_pg, f"enum type {pg_name} was not created"
    assert in_pg == MIGRATION_ENUMS[literal_name]
    assert in_pg == tuple(e.value for e in py_enum)


async def test_superseded_is_not_a_terminal_reason(db):
    """FLE-21 R7 deleted it — supersession can no longer occur, so it must not exist.

    Left in place it would be an enum value nothing writes, which a future reader
    would reasonably interpret as a state the system can reach.
    """
    assert "superseded" not in MIGRATION_ENUMS["SESSION_TERMINAL_REASON"]


@pytest.mark.parametrize(
    "column",
    [
        "session_item_id", "rung_bpm", "planned_bpm", "planned_reps",
        "outcome", "re_entry", "duration_s", "target_skill_node_id",
    ],
)
async def test_drill_attempts_gained_the_full_record(db, column):
    """FLE-4 §13. 0006 shipped the stub; without these a ladder transition is unauditable."""
    found = await db.scalar(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'drill_attempts' AND column_name = :c"
        ),
        {"c": column},
    )
    assert found == column


# ---------------------------------------------------------------------------
# The open-session partial unique — both halves
# ---------------------------------------------------------------------------

async def test_two_open_sessions_same_day_are_rejected(db):
    """This index IS the double-tap defence for resolve-or-generate (FLE-21 §1)."""
    uid = await _make_user(db)
    await _make_session(db, uid, day=TODAY, state="planned")
    with pytest.raises(IntegrityError):
        await _make_session(db, uid, day=TODAY, state="in_progress")
    await db.rollback()


async def test_completed_session_does_not_block_a_second_session_same_day(db):
    """The half FLE-4 §13's `WHERE state != 'abandoned'` got wrong.

    A user who finishes a session and wants to practise again the same evening is
    having a good day, not producing a telemetry anomaly. If the index covered
    completed rows, the second POST would 500 on a unique violation.
    """
    uid = await _make_user(db)
    now = datetime.now(timezone.utc)
    await _make_session(
        db, uid, day=TODAY, state="completed", terminal_reason="user_completed",
        ended_at=now, started_at=now - timedelta(minutes=30),
    )
    # Must not raise.
    await _make_session(db, uid, day=TODAY, state="planned")
    count = await db.scalar(
        text("SELECT count(*) FROM practice_sessions WHERE user_id = :u"), {"u": uid}
    )
    assert count == 2


async def test_abandoned_session_does_not_block_the_next_day(db):
    """The morning-after path: yesterday's session is day-rolled, today's generates."""
    uid = await _make_user(db)
    yesterday = TODAY - timedelta(days=1)
    await _make_session(
        db, uid, day=yesterday, state="abandoned", terminal_reason="day_rolled",
        ended_at=datetime.now(timezone.utc),
    )
    await _make_session(db, uid, day=TODAY, state="planned")
    count = await db.scalar(
        text("SELECT count(*) FROM practice_sessions WHERE user_id = :u"), {"u": uid}
    )
    assert count == 2


# ---------------------------------------------------------------------------
# Session lifecycle invariants
# ---------------------------------------------------------------------------

async def test_terminal_state_requires_a_reason(db):
    """An 'abandoned' row with no reason loses the day_rolled-vs-timeout split.

    That split is not cosmetic: 'timeout' should fire approximately never, and if it
    fires during the pilot it is a bug report about a corrupt tz_offset_minutes, not
    a metric about a participant.
    """
    uid = await _make_user(db)
    with pytest.raises(IntegrityError):
        await _make_session(
            db, uid, day=TODAY, state="abandoned", terminal_reason=None,
            ended_at=datetime.now(timezone.utc),
        )
    await db.rollback()


async def test_non_terminal_state_must_not_carry_a_reason(db):
    uid = await _make_user(db)
    with pytest.raises(IntegrityError):
        await _make_session(
            db, uid, day=TODAY, state="in_progress", terminal_reason="user_completed",
            started_at=datetime.now(timezone.utc),
        )
    await db.rollback()


async def test_terminal_state_requires_ended_at(db):
    uid = await _make_user(db)
    with pytest.raises(IntegrityError):
        await _make_session(
            db, uid, day=TODAY, state="completed", terminal_reason="user_completed",
            ended_at=None, started_at=datetime.now(timezone.utc),
        )
    await db.rollback()


async def test_in_progress_requires_started_at(db):
    """`planned` means never opened; anything live has been opened (FLE-21 R5)."""
    uid = await _make_user(db)
    with pytest.raises(IntegrityError):
        await _make_session(db, uid, day=TODAY, state="in_progress", started_at=None)
    await db.rollback()


async def test_generated_but_never_started_session_can_still_be_abandoned(db):
    """An ignored plan is a real row, and it is NOT the same number as a bail.

    Only started_at IS NOT NULL rows enter FLE-21 §4's completion-rate denominator,
    so this row must be storable without faking a start time.
    """
    uid = await _make_user(db)
    await _make_session(
        db, uid, day=TODAY - timedelta(days=1), state="abandoned",
        terminal_reason="day_rolled", ended_at=datetime.now(timezone.utc), started_at=None,
    )
    started = await db.scalar(
        text("SELECT started_at FROM practice_sessions WHERE user_id = :u"), {"u": uid}
    )
    assert started is None


async def test_target_minutes_is_constrained_to_the_supported_lengths(db):
    """FLE-4 §0. A fifth value is a migration plus a mobile picker plus re-onboarding."""
    uid = await _make_user(db)
    with pytest.raises(IntegrityError):
        await db.execute(
            text(
                "INSERT INTO practice_sessions "
                "(id, user_id, local_calendar_day, tz_offset_minutes, target_minutes, mode, "
                " allow_push, generator_version, seed, mode_rule, item_count) "
                "VALUES (:id, :uid, :day, 0, 20, 'BALANCED', true, 'v', 's', 'default', 4)"
            ),
            {"id": uuid.uuid4(), "uid": uid, "day": TODAY},
        )
    await db.rollback()


# ---------------------------------------------------------------------------
# Item invariants
# ---------------------------------------------------------------------------

async def test_items_default_to_not_reached(db):
    """FLE-21 §2's one expensive-to-retrofit decision, at the schema level.

    Rows are INSERTed at generation time and only then touched. If the default were
    anything else, a never-opened session would misreport as partially played.
    """
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    drill = await _make_drill(db, uid, node)
    sid = await _make_session(db, uid, day=TODAY)
    await _make_item(db, sid, index=0, drill_id=drill)
    state = await db.scalar(
        text("SELECT state FROM practice_session_items WHERE session_id = :s"), {"s": sid}
    )
    assert state == "not_reached"
    assert MIGRATION_ENUMS["SESSION_ITEM_STATE"][0] == "not_reached"


async def test_item_index_is_unique_within_a_session(db):
    """Two items at index 3 makes `…/items/3/complete` ambiguous and the plan unorderable."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    drill = await _make_drill(db, uid, node)
    sid = await _make_session(db, uid, day=TODAY)
    await _make_item(db, sid, index=0, drill_id=drill)
    with pytest.raises(IntegrityError):
        await _make_item(db, sid, index=0, drill_id=drill)
    await db.rollback()


async def test_drill_item_may_not_carry_a_song_id(db):
    """An item's referent is implied by its kind; a mismatched pair renders as an
    empty card on the device with nothing the player can do about it at runtime."""
    uid = await _make_user(db)
    sid = await _make_session(db, uid, day=TODAY)
    song_id = await db.scalar(text("SELECT id FROM songs LIMIT 1"))
    if song_id is None:
        pytest.skip("no songs in the test database to attach")
    with pytest.raises(IntegrityError):
        await _make_item(db, sid, index=0, kind="drill", song_id=song_id)
    await db.rollback()


async def test_song_item_may_not_carry_a_drill_id(db):
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    drill = await _make_drill(db, uid, node)
    sid = await _make_session(db, uid, day=TODAY)
    with pytest.raises(IntegrityError):
        await _make_item(db, sid, index=0, kind="song_play", drill_id=drill)
    await db.rollback()


async def test_rating_on_an_unrated_item_is_rejected(db):
    """FLE-4 §5.4 — a rating on the consolidation item converts the win back into an
    assessment. The API returns 422; this is the backstop behind it."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    drill = await _make_drill(db, uid, node)
    sid = await _make_session(db, uid, day=TODAY)
    with pytest.raises(IntegrityError):
        await _make_item(
            db, sid, index=0, drill_id=drill, rated=False, rating="thats_what_im_looking_for"
        )
    await db.rollback()


async def test_null_rating_on_a_rated_item_is_legal(db):
    """FLE-21 R1 — NULL is a first-class value.

    Four item classes are unrated by design and auto-advance lands `completed` with a
    NULL rating on a rated item. Requiring a rating would force the player to
    fabricate data or strand items in_progress.
    """
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    drill = await _make_drill(db, uid, node)
    sid = await _make_session(db, uid, day=TODAY)
    await _make_item(db, sid, index=0, drill_id=drill, rated=True, rating=None)
    rating = await db.scalar(
        text("SELECT rating FROM practice_session_items WHERE session_id = :s"), {"s": sid}
    )
    assert rating is None


async def test_items_cascade_when_the_session_is_deleted(db):
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    drill = await _make_drill(db, uid, node)
    sid = await _make_session(db, uid, day=TODAY)
    await _make_item(db, sid, index=0, drill_id=drill)
    await db.execute(text("DELETE FROM practice_sessions WHERE id = :s"), {"s": sid})
    left = await db.scalar(
        text("SELECT count(*) FROM practice_session_items WHERE session_id = :s"), {"s": sid}
    )
    assert left == 0


async def test_deleting_a_drill_does_not_delete_the_item_that_used_it(db):
    """SET NULL, not CASCADE. Removing a drill from the bank must not silently delete
    the telemetry proving the user practised it."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    drill = await _make_drill(db, uid, node)
    sid = await _make_session(db, uid, day=TODAY)
    iid = await _make_item(db, sid, index=0, drill_id=drill)
    await db.execute(text("DELETE FROM drills WHERE id = :d"), {"d": drill})
    row = (
        await db.execute(
            text("SELECT drill_id, state FROM practice_session_items WHERE id = :i"), {"i": iid}
        )
    ).one_or_none()
    assert row is not None, "the item row was deleted with its drill"
    assert row[0] is None


# ---------------------------------------------------------------------------
# drill_progress — the ladder's invariants
# ---------------------------------------------------------------------------

async def _make_progress(db, user_id, drill_id, *, rung=60, state="active", mastered_at=None):
    pid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO drill_progress (id, user_id, drill_id, rung_bpm, state, mastered_at) "
            "VALUES (:id, :u, :d, :r, CAST(:s AS drill_progress_state), :m)"
        ),
        {"id": pid, "u": user_id, "d": drill_id, "r": rung, "s": state, "m": mastered_at},
    )
    return pid


async def test_one_ladder_per_user_and_drill(db):
    """The upsert target for the rating write path. A lost race here forks a ladder,
    and then two rows disagree about the tempo the player actually held."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    drill = await _make_drill(db, uid, node)
    await _make_progress(db, uid, drill)
    with pytest.raises(IntegrityError):
        await _make_progress(db, uid, drill)
    await db.rollback()


async def test_rung_must_sit_on_the_five_bpm_grid(db):
    """FLE-4 §1 — every planned tempo is a multiple of 5, matching the skill graph's
    tempo bins. An off-grid rung makes the next round_to_5() push lossy."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    drill = await _make_drill(db, uid, node)
    with pytest.raises(IntegrityError):
        await _make_progress(db, uid, drill, rung=63)
    await db.rollback()


async def test_mastered_requires_mastered_at(db):
    """§7.5 — mastered_at arms the 14-day maintenance timer. Without it a mastered
    drill never enters maintenance and is excluded from selection forever."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    drill = await _make_drill(db, uid, node)
    with pytest.raises(IntegrityError):
        await _make_progress(db, uid, drill, state="mastered", mastered_at=None)
    await db.rollback()


async def test_active_must_not_carry_mastered_at(db):
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    drill = await _make_drill(db, uid, node)
    with pytest.raises(IntegrityError):
        await _make_progress(
            db, uid, drill, state="active", mastered_at=datetime.now(timezone.utc)
        )
    await db.rollback()


async def test_ladder_counters_start_at_zero(db):
    """§7 init. A ladder that starts mid-count would push or drop a rung early."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    drill = await _make_drill(db, uid, node)
    await _make_progress(db, uid, drill)
    row = (
        await db.execute(
            text(
                "SELECT consecutive_clears, consecutive_misses, attempts, state, "
                "last_practiced_on FROM drill_progress WHERE user_id = :u"
            ),
            {"u": uid},
        )
    ).one()
    assert row[0] == 0 and row[1] == 0 and row[2] == 0
    assert row[3] == "active"
    assert row[4] is None
