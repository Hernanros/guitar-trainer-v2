"""Alembic migration 0004 tests.

Verifies migration 0004 (governor_calls + skill_node_proposals + skill_node_rejections
+ decay_runs + skill_nodes.canonical_node_id + skill_nodes.last_decayed_at):
- Schema assertions: all four new tables exist
- Column existence checks on skill_nodes (canonical_node_id, last_decayed_at)
- Index existence check on governor_calls (ix_governor_calls_user_feature_created)
- Index existence check on skill_nodes (ix_skill_nodes_canonical)

Runs against the real Postgres test database (DATABASE_URL env var or default).
Requires `alembic upgrade head` to have been applied before running.

Mirrors test_alembic_0003.py scaffolding.
"""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# ---------------------------------------------------------------------------
# Test database setup — mirrors conftest.py's URL handling
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


TEST_DB_URL = _make_test_db_url()
engine = create_async_engine(TEST_DB_URL, echo=False, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
async def db():
    """Session-scoped async DB session — shares the conftest.py event loop."""
    async with SessionFactory() as session:
        yield session


# ---------------------------------------------------------------------------
# 1. New table existence checks
# ---------------------------------------------------------------------------

async def test_governor_calls_table_exists(db: AsyncSession) -> None:
    """governor_calls table must exist after migration 0004."""
    result = await db.execute(
        text("SELECT to_regclass('public.governor_calls') AS rel")
    )
    row = result.one()
    assert row.rel is not None, "governor_calls table not found after 0004 upgrade"


async def test_skill_node_proposals_table_exists(db: AsyncSession) -> None:
    """skill_node_proposals table must exist after migration 0004."""
    result = await db.execute(
        text("SELECT to_regclass('public.skill_node_proposals') AS rel")
    )
    row = result.one()
    assert row.rel is not None, "skill_node_proposals table not found after 0004 upgrade"


async def test_skill_node_rejections_table_exists(db: AsyncSession) -> None:
    """skill_node_rejections table must exist after migration 0004."""
    result = await db.execute(
        text("SELECT to_regclass('public.skill_node_rejections') AS rel")
    )
    row = result.one()
    assert row.rel is not None, "skill_node_rejections table not found after 0004 upgrade"


async def test_decay_runs_table_exists(db: AsyncSession) -> None:
    """decay_runs table must exist after migration 0004."""
    result = await db.execute(
        text("SELECT to_regclass('public.decay_runs') AS rel")
    )
    row = result.one()
    assert row.rel is not None, "decay_runs table not found after 0004 upgrade"


# ---------------------------------------------------------------------------
# 2. Column existence checks on skill_nodes
# ---------------------------------------------------------------------------

async def test_skill_nodes_canonical_node_id_column(db: AsyncSession) -> None:
    """skill_nodes.canonical_node_id must exist as nullable UUID after migration 0004."""
    result = await db.execute(
        text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'skill_nodes' AND column_name = 'canonical_node_id'
        """)
    )
    row = result.one_or_none()
    assert row is not None, "skill_nodes.canonical_node_id column not found after 0004 upgrade"
    assert row.is_nullable == "YES", "skill_nodes.canonical_node_id should be nullable"
    assert "uuid" in row.data_type.lower(), f"Unexpected data_type for canonical_node_id: {row.data_type}"


async def test_skill_nodes_last_decayed_at_column(db: AsyncSession) -> None:
    """skill_nodes.last_decayed_at must exist as nullable timestamptz after migration 0004."""
    result = await db.execute(
        text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'skill_nodes' AND column_name = 'last_decayed_at'
        """)
    )
    row = result.one_or_none()
    assert row is not None, "skill_nodes.last_decayed_at column not found after 0004 upgrade"
    assert row.is_nullable == "YES", "skill_nodes.last_decayed_at should be nullable"
    assert "timestamp" in row.data_type.lower(), f"Unexpected data_type for last_decayed_at: {row.data_type}"


# ---------------------------------------------------------------------------
# 3. Index existence checks
# ---------------------------------------------------------------------------

async def test_ix_governor_calls_user_feature_created_exists(db: AsyncSession) -> None:
    """ix_governor_calls_user_feature_created index must exist on governor_calls (D-Claude-schema)."""
    result = await db.execute(
        text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'governor_calls'
              AND indexname = 'ix_governor_calls_user_feature_created'
        """)
    )
    row = result.one_or_none()
    assert row is not None, "ix_governor_calls_user_feature_created index not found after 0004 upgrade"


async def test_ix_skill_nodes_canonical_exists(db: AsyncSession) -> None:
    """ix_skill_nodes_canonical index must exist on skill_nodes (D-12)."""
    result = await db.execute(
        text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'skill_nodes'
              AND indexname = 'ix_skill_nodes_canonical'
        """)
    )
    row = result.one_or_none()
    assert row is not None, "ix_skill_nodes_canonical index not found after 0004 upgrade"


# ---------------------------------------------------------------------------
# 4. governor_calls column schema verification
# ---------------------------------------------------------------------------

async def test_governor_calls_columns(db: AsyncSession) -> None:
    """governor_calls must have all expected columns per D-Claude-schema."""
    expected_cols = {
        "id", "user_id", "feature", "model",
        "prompt_tokens_estimated", "prompt_tokens_actual", "output_tokens_actual",
        "dollars_estimated", "dollars_actual", "error_code", "created_at",
    }
    result = await db.execute(
        text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'governor_calls'
        """)
    )
    actual_cols = {row.column_name for row in result.all()}
    missing = expected_cols - actual_cols
    assert not missing, f"governor_calls missing columns: {missing}"
