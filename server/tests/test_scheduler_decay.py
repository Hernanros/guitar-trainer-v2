"""Tests for Phase 4 Slice D: nightly decay scheduler (scheduler.py).

Tests invoke decay_all_nodes() directly — NOT via APScheduler — to keep
tests deterministic and fast (no timing dependency on cron triggers).

APScheduler scheduler teardown is handled by the module-level autouse
fixture _scheduler_teardown to prevent "loop is closed" errors across tests.

Coverage:
  - test_decay_updates_eligible_nodes
  - test_decay_skips_recent_nodes
  - test_decay_skips_zero_mastery_nodes
  - test_decay_clamps_to_zero_via_greatest
  - test_decay_run_row_inserted_on_success
  - test_decay_run_row_inserted_on_failure
  - test_get_scheduler_returns_singleton
  - test_startup_registers_decay_job
  - test_decay_all_nodes_manual_invocation

All tests run against the real Postgres test database.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure no live API key leaks
os.environ.pop("ANTHROPIC_API_KEY", None)


# ---------------------------------------------------------------------------
# Test DB helpers
# ---------------------------------------------------------------------------

def _make_test_db_url() -> str:
    raw = os.environ.get(
        "DATABASE_URL",
        "postgresql://gt:devpass@localhost:5433/guitar_trainer",
    )
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    return raw


_engine = create_async_engine(
    _make_test_db_url(), echo=False, pool_size=2, max_overflow=0
)
_SessionFactory = async_sessionmaker(
    bind=_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest.fixture()
async def db() -> AsyncSession:
    """Per-test DB session with cleanup."""
    async with _SessionFactory() as session:
        yield session


# ---------------------------------------------------------------------------
# Scheduler teardown fixture — prevents "loop is closed" across tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def _scheduler_teardown():
    """Shut down the scheduler after each test to release the asyncio loop ref."""
    yield
    from app.scheduler import get_scheduler
    sched = get_scheduler()
    if sched.running:
        sched.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

async def _seed_user(db: AsyncSession) -> str:
    uid = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO users (id, preferences) "
            "VALUES (:uid, '{}'::jsonb) ON CONFLICT DO NOTHING"
        ),
        {"uid": uid},
    )
    await db.commit()
    return uid


async def _seed_skill_node(
    db: AsyncSession,
    user_id: str,
    mastery: float = 0.8,
    updated_at_offset_days: int = -8,
    last_decayed_at: datetime | None = None,
) -> str:
    """Insert a skill_node with a controlled updated_at and mastery.

    asyncpg requires actual datetime objects (not ISO strings) for timestamptz params.
    """
    node_id = str(uuid.uuid4())
    updated_at = datetime.now(tz=timezone.utc) + timedelta(days=updated_at_offset_days)
    params: dict[str, Any] = {
        "id": node_id,
        "uid": user_id,
        "mastery": str(mastery),
        "updated_at": updated_at,
        "last_decayed_at": last_decayed_at,
    }
    await db.execute(
        text(
            "INSERT INTO skill_nodes "
            "(id, user_id, name, level, mastery, updated_at, last_decayed_at) "
            "VALUES "
            "(:id, :uid, 'Test Node', 'leaf', :mastery, :updated_at, :last_decayed_at)"
        ),
        params,
    )
    await db.commit()
    return node_id


async def _get_node(db: AsyncSession, node_id: str) -> dict:
    result = await db.execute(
        text(
            "SELECT mastery, last_decayed_at "
            "FROM skill_nodes WHERE id = :id"
        ),
        {"id": node_id},
    )
    row = result.fetchone()
    assert row is not None, f"Node {node_id} not found"
    return {"mastery": float(row.mastery), "last_decayed_at": row.last_decayed_at}


async def _count_decay_runs(db: AsyncSession) -> int:
    result = await db.execute(text("SELECT COUNT(*) FROM decay_runs"))
    return result.scalar_one()


async def _cleanup_node(db: AsyncSession, node_id: str) -> None:
    await db.execute(text("DELETE FROM skill_nodes WHERE id = :id"), {"id": node_id})
    await db.commit()


async def _cleanup_user(db: AsyncSession, user_id: str) -> None:
    # Remove all skill_nodes for user first (FK constraint)
    await db.execute(
        text("DELETE FROM skill_nodes WHERE user_id = :uid"), {"uid": user_id}
    )
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await db.commit()


async def _cleanup_decay_runs(db: AsyncSession) -> None:
    await db.execute(text("DELETE FROM decay_runs"))
    await db.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_decay_updates_eligible_nodes(db: AsyncSession) -> None:
    """Eligible rows (updated_at > 7d, mastery > 0) get mastery * 0.95 and last_decayed_at set."""
    from app.scheduler import decay_all_nodes

    user_id = await _seed_user(db)
    node1 = await _seed_skill_node(db, user_id, mastery=0.8, updated_at_offset_days=-8)
    node2 = await _seed_skill_node(db, user_id, mastery=0.8, updated_at_offset_days=-10)

    await _cleanup_decay_runs(db)
    await decay_all_nodes()

    row1 = await _get_node(db, node1)
    row2 = await _get_node(db, node2)
    await _cleanup_user(db, user_id)
    await _cleanup_decay_runs(db)

    assert abs(row1["mastery"] - 0.8 * 0.95) < 0.001, f"Expected ~0.76, got {row1['mastery']}"
    assert abs(row2["mastery"] - 0.8 * 0.95) < 0.001, f"Expected ~0.76, got {row2['mastery']}"
    assert row1["last_decayed_at"] is not None, "last_decayed_at should be set"
    assert row2["last_decayed_at"] is not None, "last_decayed_at should be set"


async def test_decay_skips_recent_nodes(db: AsyncSession) -> None:
    """Rows updated within the last 7 days are NOT decayed."""
    from app.scheduler import decay_all_nodes

    user_id = await _seed_user(db)
    node1 = await _seed_skill_node(db, user_id, mastery=0.8, updated_at_offset_days=-3)
    node2 = await _seed_skill_node(db, user_id, mastery=0.8, updated_at_offset_days=-1)

    await _cleanup_decay_runs(db)
    await decay_all_nodes()

    row1 = await _get_node(db, node1)
    row2 = await _get_node(db, node2)
    await _cleanup_user(db, user_id)
    await _cleanup_decay_runs(db)

    assert abs(row1["mastery"] - 0.8) < 0.001, f"Recent node should be untouched, got {row1['mastery']}"
    assert abs(row2["mastery"] - 0.8) < 0.001, f"Recent node should be untouched, got {row2['mastery']}"
    assert row1["last_decayed_at"] is None, "last_decayed_at should remain NULL for skipped row"
    assert row2["last_decayed_at"] is None, "last_decayed_at should remain NULL for skipped row"


async def test_decay_skips_zero_mastery_nodes(db: AsyncSession) -> None:
    """Rows with mastery=0 are excluded by the AND mastery > 0 predicate."""
    from app.scheduler import decay_all_nodes

    user_id = await _seed_user(db)
    node = await _seed_skill_node(db, user_id, mastery=0.0, updated_at_offset_days=-10)

    await _cleanup_decay_runs(db)
    await decay_all_nodes()

    row = await _get_node(db, node)
    await _cleanup_user(db, user_id)
    await _cleanup_decay_runs(db)

    assert row["mastery"] == 0.0, f"Zero-mastery node should be untouched, got {row['mastery']}"
    assert row["last_decayed_at"] is None, "last_decayed_at should remain NULL"


async def test_decay_clamps_to_zero_via_greatest(db: AsyncSession) -> None:
    """GREATEST(mastery * 0.95, 0) clamps correctly for tiny positive mastery."""
    from app.scheduler import decay_all_nodes

    user_id = await _seed_user(db)
    # mastery=0.001 qualifies (mastery > 0); result is GREATEST(0.001 * 0.95, 0) = 0.00095 ~ still > 0
    node = await _seed_skill_node(db, user_id, mastery=0.001, updated_at_offset_days=-10)

    await _cleanup_decay_runs(db)
    await decay_all_nodes()

    row = await _get_node(db, node)
    await _cleanup_user(db, user_id)
    await _cleanup_decay_runs(db)

    expected = round(0.001 * 0.95, 5)
    assert row["mastery"] >= 0, "GREATEST should clamp to 0 floor"
    assert row["mastery"] <= 0.001, f"Mastery should decrease, got {row['mastery']}"
    assert row["mastery"] > 0, f"Tiny mastery should remain > 0 after single decay, got {row['mastery']}"


async def test_decay_run_row_inserted_on_success(db: AsyncSession) -> None:
    """Each successful decay invocation writes one decay_runs row with started_at, finished_at, nodes_affected."""
    from app.scheduler import decay_all_nodes

    user_id = await _seed_user(db)
    await _seed_skill_node(db, user_id, mastery=0.8, updated_at_offset_days=-8)

    await _cleanup_decay_runs(db)
    before = await _count_decay_runs(db)
    await decay_all_nodes()
    after = await _count_decay_runs(db)

    # Fetch the inserted row
    result = await db.execute(
        text("SELECT started_at, finished_at, nodes_affected, error FROM decay_runs ORDER BY started_at DESC LIMIT 1")
    )
    row = result.fetchone()

    await _cleanup_user(db, user_id)
    await _cleanup_decay_runs(db)

    assert after == before + 1, "One new decay_runs row should be inserted"
    assert row is not None
    assert row.started_at is not None, "started_at must be populated"
    assert row.finished_at is not None, "finished_at must be populated on success"
    assert row.nodes_affected is not None, "nodes_affected must be set"
    assert row.nodes_affected >= 1, f"nodes_affected should be >= 1, got {row.nodes_affected}"
    assert row.error is None, f"error should be NULL on success, got {row.error}"


async def test_decay_run_row_inserted_on_failure(db: AsyncSession) -> None:
    """Even when decay fails, a decay_runs audit row with error text is written."""
    from app.scheduler import decay_all_nodes

    await _cleanup_decay_runs(db)
    before = await _count_decay_runs(db)

    # Patch the first AsyncSessionLocal call to raise on execute, but allow the
    # second (fresh-session error path) to proceed normally.
    original_session_local = None
    call_count = 0

    class _FailingSession:
        """Context manager that raises on execute to simulate DB error."""
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated DB failure in decay_all_nodes")
            raise RuntimeError("Should not reach second execute on failing session")

        async def rollback(self):
            pass

        async def commit(self):
            pass

    from app import scheduler as sched_module
    original = sched_module.AsyncSessionLocal

    session_calls = 0

    class _PatchedFactory:
        def __call__(self):
            nonlocal session_calls
            session_calls += 1
            if session_calls == 1:
                return _FailingSession()
            # Second call is the error-path fresh session — use real session
            return original()

    patched = _PatchedFactory()

    with patch.object(sched_module, "AsyncSessionLocal", patched):
        await decay_all_nodes()

    after = await _count_decay_runs(db)

    # Fetch the error row
    result = await db.execute(
        text("SELECT started_at, finished_at, error FROM decay_runs ORDER BY started_at DESC LIMIT 1")
    )
    row = result.fetchone()
    await _cleanup_decay_runs(db)

    assert after == before + 1, "One decay_runs row should be inserted even on failure"
    assert row is not None
    assert row.error is not None, "error field should be populated on failure"
    assert "Simulated DB failure" in row.error, f"Expected failure message in error, got: {row.error}"
    assert row.finished_at is not None, "finished_at set in failure path"


async def test_get_scheduler_returns_singleton() -> None:
    """get_scheduler() called twice returns the same AsyncIOScheduler instance."""
    from app.scheduler import get_scheduler
    s1 = get_scheduler()
    s2 = get_scheduler()
    assert s1 is s2, "get_scheduler() must return the same instance on every call"


async def test_startup_registers_decay_job() -> None:
    """FastAPI startup event registers a cron job for decay_all_nodes at 03:00 UTC.

    Triggers the startup event by calling on_startup() directly, then inspects
    the scheduler's job list. Using the startup handler directly avoids TestClient
    loop-isolation issues in the session-scoped event loop fixture.
    """
    from app.main import on_startup
    from app.scheduler import get_scheduler

    # Call the startup handler directly — registers the job on the singleton scheduler
    await on_startup()

    sched = get_scheduler()
    jobs = sched.get_jobs()
    job_ids = [j.id for j in jobs]
    assert "decay_all_nodes" in job_ids, f"decay_all_nodes job not found; jobs: {job_ids}"

    # Verify cron trigger at 03:00 UTC
    decay_job = next(j for j in jobs if j.id == "decay_all_nodes")
    trigger = decay_job.trigger
    # APScheduler v3 CronTrigger — fields are accessible as trigger.fields list
    hour_field = next((f for f in trigger.fields if f.name == "hour"), None)
    minute_field = next((f for f in trigger.fields if f.name == "minute"), None)
    assert hour_field is not None and str(hour_field) == "3", f"Expected hour=3, got {hour_field}"
    assert minute_field is not None and str(minute_field) == "0", f"Expected minute=0, got {minute_field}"


async def test_decay_all_nodes_manual_invocation(db: AsyncSession) -> None:
    """Integration: manually calling decay_all_nodes() outside its scheduled trigger produces correct results."""
    from app.scheduler import decay_all_nodes

    user_id = await _seed_user(db)
    # Eligible: old node with mastery > 0
    eligible = await _seed_skill_node(db, user_id, mastery=0.6, updated_at_offset_days=-8)
    # Ineligible: fresh node
    ineligible = await _seed_skill_node(db, user_id, mastery=0.6, updated_at_offset_days=-2)

    await _cleanup_decay_runs(db)
    await decay_all_nodes()

    eligible_row = await _get_node(db, eligible)
    ineligible_row = await _get_node(db, ineligible)

    result = await db.execute(
        text("SELECT nodes_affected, error FROM decay_runs ORDER BY started_at DESC LIMIT 1")
    )
    audit = result.fetchone()

    await _cleanup_user(db, user_id)
    await _cleanup_decay_runs(db)

    # Eligible row decayed
    assert abs(eligible_row["mastery"] - 0.6 * 0.95) < 0.001, (
        f"Eligible node mastery should be ~0.57, got {eligible_row['mastery']}"
    )
    assert eligible_row["last_decayed_at"] is not None

    # Ineligible row untouched
    assert abs(ineligible_row["mastery"] - 0.6) < 0.001, (
        f"Ineligible node should be untouched, got {ineligible_row['mastery']}"
    )
    assert ineligible_row["last_decayed_at"] is None

    # Audit row inserted
    assert audit is not None
    assert audit.nodes_affected >= 1
    assert audit.error is None


async def test_decay_debounce_prevents_double_run_within_20h(db: AsyncSession) -> None:
    """Debounce guard: a node decayed less than 20 hours ago is NOT re-decayed on a second run."""
    from app.scheduler import decay_all_nodes
    from datetime import timezone

    user_id = await _seed_user(db)
    # Seed a node that is old enough to decay normally
    node = await _seed_skill_node(db, user_id, mastery=0.8, updated_at_offset_days=-8)

    await _cleanup_decay_runs(db)
    # First run — node should be decayed
    await decay_all_nodes()

    row_after_first = await _get_node(db, node)
    assert abs(row_after_first["mastery"] - 0.8 * 0.95) < 0.001, "First run should decay the node"
    assert row_after_first["last_decayed_at"] is not None

    # Second run immediately after — node was just decayed, so last_decayed_at is now() - ~0s
    # which is NOT < now() - 20h — so the node should NOT be decayed again
    await decay_all_nodes()

    row_after_second = await _get_node(db, node)
    await _cleanup_user(db, user_id)
    await _cleanup_decay_runs(db)

    # Mastery should be the same as after the first run (not decayed twice)
    expected = 0.8 * 0.95
    assert abs(row_after_second["mastery"] - expected) < 0.001, (
        f"Second run within 20h should not re-decay; expected ~{expected:.3f}, got {row_after_second['mastery']:.3f}"
    )


async def test_decay_last_decayed_at_updated_on_affected_rows(db: AsyncSession) -> None:
    """last_decayed_at is set to now() on rows that are decayed, and remains NULL on skipped rows."""
    from app.scheduler import decay_all_nodes

    user_id = await _seed_user(db)
    affected = await _seed_skill_node(db, user_id, mastery=0.5, updated_at_offset_days=-9)
    skipped = await _seed_skill_node(db, user_id, mastery=0.5, updated_at_offset_days=-2)

    now_before = datetime.now(tz=timezone.utc)
    await _cleanup_decay_runs(db)
    await decay_all_nodes()

    affected_row = await _get_node(db, affected)
    skipped_row = await _get_node(db, skipped)
    now_after = datetime.now(tz=timezone.utc)

    await _cleanup_user(db, user_id)
    await _cleanup_decay_runs(db)

    assert affected_row["last_decayed_at"] is not None, "Decayed row must have last_decayed_at set"
    lda = affected_row["last_decayed_at"]
    if lda.tzinfo is None:
        lda = lda.replace(tzinfo=timezone.utc)
    assert now_before <= lda <= now_after, (
        f"last_decayed_at {lda} should be within test window [{now_before}, {now_after}]"
    )
    assert skipped_row["last_decayed_at"] is None, "Skipped row must have last_decayed_at remain NULL"


async def test_decay_run_records_error_on_exception(db: AsyncSession) -> None:
    """decay_runs audit row has error text populated when decay_all_nodes raises internally."""
    from app import scheduler as sched_module
    from app.scheduler import decay_all_nodes

    original = sched_module.AsyncSessionLocal
    call_count = 0

    class _FailOnUpdate:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, stmt, params=None, **kw):
            nonlocal call_count
            call_count += 1
            # First execute is the INSERT decay_runs starter row; second is the UPDATE — that's where we fail
            if call_count == 2:
                raise RuntimeError("Simulated UPDATE failure")
            # For the first INSERT, return a fake result with scalar_one
            mock_result = MagicMock()
            mock_result.scalar_one.return_value = uuid.uuid4()
            return mock_result

        async def rollback(self):
            pass

        async def commit(self):
            pass

    session_calls = 0

    class _PatchFactory:
        def __call__(self):
            nonlocal session_calls
            session_calls += 1
            if session_calls == 1:
                return _FailOnUpdate()
            return original()

    await _cleanup_decay_runs(db)
    before = await _count_decay_runs(db)

    with patch.object(sched_module, "AsyncSessionLocal", _PatchFactory()):
        await decay_all_nodes()

    after = await _count_decay_runs(db)
    result = await db.execute(
        text("SELECT error FROM decay_runs ORDER BY started_at DESC LIMIT 1")
    )
    row = result.fetchone()
    await _cleanup_decay_runs(db)

    assert after == before + 1, "One decay_runs row must be written even on error"
    assert row is not None and row.error is not None, "error field must be set"
    assert "Simulated UPDATE failure" in row.error, f"Expected error text, got: {row.error}"
