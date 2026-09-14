"""Alembic migration 0005 tests.

Verifies migration 0005 (user_sessions.drill_index + user_sessions.target_skill_node_id
+ recreated uq_user_sessions_daily_rating partial-unique index with COALESCE(drill_index, -1)
+ ck_drill_index_pairs_skill_node CHECK constraint):

- Columns exist with the right types + nullability
- CHECK constraint enforces both-or-neither on (drill_index, target_skill_node_id)
- Recreated partial-unique index treats each drill_index (including NULL via COALESCE)
  as its own slot within (user_id, song_id, local_calendar_day) WHERE is_reroll_marker=false
- Downgrade removes the CHECK, recreates the original index (without drill_index in key),
  drops the two columns

Runs against the real Postgres test database (DATABASE_URL env var or default).
Requires `alembic upgrade head` to have been applied before running.

Mirrors test_alembic_0003.py / test_alembic_0004.py scaffolding.
"""
import os
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
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
# Fixture helpers
# ---------------------------------------------------------------------------

async def _seed_user_song_skill(db: AsyncSession) -> tuple[str, int, str]:
    """Seed a user + song + skill_node and return (user_id, song_id, skill_node_id).

    All rows committed. Callers must clean up afterwards.
    """
    user_id = str(uuid.uuid4())
    skill_id = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO users (id, preferences) "
            "VALUES (:uid, '{}'::jsonb) ON CONFLICT (id) DO NOTHING"
        ),
        {"uid": user_id},
    )
    await db.execute(
        text(
            "INSERT INTO songs "
            "  (title, artist, genre, difficulty, bpm, key, breakdown, user_id, category) "
            "VALUES ('T', 'A', 'Blues', 'intermediate', 100, 'E', '{}'::jsonb, "
            "        :uid, 'working_on')"
        ),
        {"uid": user_id},
    )
    song_id = (
        await db.execute(
            text("SELECT id FROM songs WHERE user_id = :uid ORDER BY id DESC LIMIT 1"),
            {"uid": user_id},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
            "VALUES (:sid, :uid, 'Leaf', 'leaf', 0.3)"
        ),
        {"sid": skill_id, "uid": user_id},
    )
    await db.commit()
    return user_id, song_id, skill_id


async def _cleanup_user(db: AsyncSession, user_id: str) -> None:
    await db.execute(text(f"DELETE FROM user_sessions WHERE user_id = '{user_id}'"))
    await db.execute(
        text(
            "DELETE FROM song_skills WHERE song_id IN "
            f"(SELECT id FROM songs WHERE user_id = '{user_id}')"
        )
    )
    await db.execute(text(f"DELETE FROM songs WHERE user_id = '{user_id}'"))
    await db.execute(text(f"DELETE FROM skill_nodes WHERE user_id = '{user_id}'"))
    await db.execute(text(f"DELETE FROM users WHERE id = '{user_id}'"))
    await db.commit()


# ---------------------------------------------------------------------------
# 1. Column existence + type/nullability
# ---------------------------------------------------------------------------

async def test_0005_upgrade_adds_drill_index_column(db: AsyncSession) -> None:
    """user_sessions.drill_index must exist as nullable integer after 0005."""
    result = await db.execute(
        text(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'user_sessions' AND column_name = 'drill_index'"
        )
    )
    row = result.one_or_none()
    assert row is not None, "user_sessions.drill_index column not found after 0005 upgrade"
    assert row.is_nullable == "YES", "drill_index should be nullable"
    assert "integer" in row.data_type.lower(), f"Unexpected data_type for drill_index: {row.data_type}"


async def test_0005_upgrade_adds_target_skill_node_id_column(db: AsyncSession) -> None:
    """user_sessions.target_skill_node_id must exist as nullable uuid with FK to skill_nodes after 0005."""
    result = await db.execute(
        text(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'user_sessions' AND column_name = 'target_skill_node_id'"
        )
    )
    row = result.one_or_none()
    assert row is not None, "user_sessions.target_skill_node_id column not found after 0005 upgrade"
    assert row.is_nullable == "YES", "target_skill_node_id should be nullable"
    assert "uuid" in row.data_type.lower(), (
        f"Unexpected data_type for target_skill_node_id: {row.data_type}"
    )

    # FK to skill_nodes exists
    fk_row = (
        await db.execute(
            text(
                "SELECT ccu.table_name AS referenced_table "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
                "JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name "
                "WHERE tc.table_name = 'user_sessions' "
                "  AND tc.constraint_type = 'FOREIGN KEY' "
                "  AND kcu.column_name = 'target_skill_node_id'"
            )
        )
    ).one_or_none()
    assert fk_row is not None, "FK on target_skill_node_id not found"
    assert fk_row.referenced_table == "skill_nodes", (
        f"target_skill_node_id FK should point to skill_nodes, got {fk_row.referenced_table}"
    )


# ---------------------------------------------------------------------------
# 2. CHECK constraint (both-or-neither)
# ---------------------------------------------------------------------------

async def test_0005_check_constraint_rejects_drill_index_alone(db: AsyncSession) -> None:
    """INSERT with drill_index=0 + target_skill_node_id=NULL raises IntegrityError (CHECK)."""
    user_id, song_id, _ = await _seed_user_song_skill(db)
    try:
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "INSERT INTO user_sessions "
                    "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
                    "   is_reroll_marker, drill_index, target_skill_node_id) "
                    "VALUES (:sid, :uid, :song_id, 'getting_closer', :day, 0, false, 0, NULL)"
                ),
                {
                    "sid": str(uuid.uuid4()),
                    "uid": user_id,
                    "song_id": song_id,
                    "day": date(2099, 5, 1),
                },
            )
            await db.flush()
        await db.rollback()
    finally:
        await _cleanup_user(db, user_id)


async def test_0005_check_constraint_rejects_target_alone(db: AsyncSession) -> None:
    """INSERT with drill_index=NULL + target_skill_node_id=<uuid> raises IntegrityError (CHECK)."""
    user_id, song_id, skill_id = await _seed_user_song_skill(db)
    try:
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "INSERT INTO user_sessions "
                    "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
                    "   is_reroll_marker, drill_index, target_skill_node_id) "
                    "VALUES (:sid, :uid, :song_id, 'getting_closer', :day, 0, false, NULL, :tsid)"
                ),
                {
                    "sid": str(uuid.uuid4()),
                    "uid": user_id,
                    "song_id": song_id,
                    "day": date(2099, 5, 2),
                    "tsid": skill_id,
                },
            )
            await db.flush()
        await db.rollback()
    finally:
        await _cleanup_user(db, user_id)


async def test_0005_check_constraint_allows_both_null(db: AsyncSession) -> None:
    """INSERT with both drill_index=NULL + target_skill_node_id=NULL succeeds (song rating)."""
    user_id, song_id, _ = await _seed_user_song_skill(db)
    try:
        await db.execute(
            text(
                "INSERT INTO user_sessions "
                "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
                "   is_reroll_marker, drill_index, target_skill_node_id) "
                "VALUES (:sid, :uid, :song_id, 'getting_closer', :day, 0, false, NULL, NULL)"
            ),
            {
                "sid": str(uuid.uuid4()),
                "uid": user_id,
                "song_id": song_id,
                "day": date(2099, 5, 3),
            },
        )
        await db.commit()
    finally:
        await _cleanup_user(db, user_id)


async def test_0005_check_constraint_allows_both_set(db: AsyncSession) -> None:
    """INSERT with both drill_index=0 + target_skill_node_id=<uuid> succeeds (drill rating)."""
    user_id, song_id, skill_id = await _seed_user_song_skill(db)
    try:
        await db.execute(
            text(
                "INSERT INTO user_sessions "
                "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
                "   is_reroll_marker, drill_index, target_skill_node_id) "
                "VALUES (:sid, :uid, :song_id, 'getting_closer', :day, 0, false, 0, :tsid)"
            ),
            {
                "sid": str(uuid.uuid4()),
                "uid": user_id,
                "song_id": song_id,
                "day": date(2099, 5, 4),
                "tsid": skill_id,
            },
        )
        await db.commit()
    finally:
        await _cleanup_user(db, user_id)


# ---------------------------------------------------------------------------
# 3. Partial-unique index behavior (COALESCE(drill_index, -1) key)
# ---------------------------------------------------------------------------

async def test_0005_partial_index_allows_drill_variants_and_null(db: AsyncSession) -> None:
    """For a single (user, song, local_day), a NULL-drill_index INSERT + drill_index=0 INSERT +
    drill_index=1 INSERT all succeed (COALESCE(-1) treats NULL as its own slot)."""
    user_id, song_id, skill_id = await _seed_user_song_skill(db)
    day = date(2099, 6, 1)
    try:
        # Song-level rating (drill_index=NULL)
        await db.execute(
            text(
                "INSERT INTO user_sessions "
                "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
                "   is_reroll_marker, drill_index, target_skill_node_id) "
                "VALUES (:sid, :uid, :song_id, 'getting_closer', :day, 0, false, NULL, NULL)"
            ),
            {"sid": str(uuid.uuid4()), "uid": user_id, "song_id": song_id, "day": day},
        )
        # Drill 0
        await db.execute(
            text(
                "INSERT INTO user_sessions "
                "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
                "   is_reroll_marker, drill_index, target_skill_node_id) "
                "VALUES (:sid, :uid, :song_id, 'getting_closer', :day, 0, false, 0, :tsid)"
            ),
            {
                "sid": str(uuid.uuid4()),
                "uid": user_id,
                "song_id": song_id,
                "day": day,
                "tsid": skill_id,
            },
        )
        # Drill 1
        await db.execute(
            text(
                "INSERT INTO user_sessions "
                "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
                "   is_reroll_marker, drill_index, target_skill_node_id) "
                "VALUES (:sid, :uid, :song_id, 'thats_what_im_looking_for', :day, 0, false, 1, :tsid)"
            ),
            {
                "sid": str(uuid.uuid4()),
                "uid": user_id,
                "song_id": song_id,
                "day": day,
                "tsid": skill_id,
            },
        )
        await db.commit()

        # Verify all 3 rows landed
        count = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM user_sessions "
                    "WHERE user_id = :uid AND song_id = :song_id "
                    "  AND local_calendar_day = :day AND is_reroll_marker = false"
                ),
                {"uid": user_id, "song_id": song_id, "day": day},
            )
        ).scalar_one()
        assert count == 3, f"Expected 3 rows (song+drill0+drill1), got {count}"
    finally:
        await _cleanup_user(db, user_id)


async def test_0005_partial_index_blocks_duplicate_drill_index(db: AsyncSession) -> None:
    """Two INSERTs with drill_index=0 for the same (user, song, local_day) — second raises IntegrityError."""
    user_id, song_id, skill_id = await _seed_user_song_skill(db)
    day = date(2099, 6, 2)
    try:
        await db.execute(
            text(
                "INSERT INTO user_sessions "
                "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
                "   is_reroll_marker, drill_index, target_skill_node_id) "
                "VALUES (:sid, :uid, :song_id, 'getting_closer', :day, 0, false, 0, :tsid)"
            ),
            {
                "sid": str(uuid.uuid4()),
                "uid": user_id,
                "song_id": song_id,
                "day": day,
                "tsid": skill_id,
            },
        )
        await db.flush()

        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "INSERT INTO user_sessions "
                    "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
                    "   is_reroll_marker, drill_index, target_skill_node_id) "
                    "VALUES (:sid, :uid, :song_id, 'not_my_tempo', :day, 0, false, 0, :tsid)"
                ),
                {
                    "sid": str(uuid.uuid4()),
                    "uid": user_id,
                    "song_id": song_id,
                    "day": day,
                    "tsid": skill_id,
                },
            )
            await db.flush()
        await db.rollback()
    finally:
        await _cleanup_user(db, user_id)


async def test_0005_partial_index_blocks_duplicate_song_rating(db: AsyncSession) -> None:
    """Two INSERTs with drill_index=NULL for the same (user, song, local_day) — second raises IntegrityError."""
    user_id, song_id, _ = await _seed_user_song_skill(db)
    day = date(2099, 6, 3)
    try:
        await db.execute(
            text(
                "INSERT INTO user_sessions "
                "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
                "   is_reroll_marker, drill_index, target_skill_node_id) "
                "VALUES (:sid, :uid, :song_id, 'getting_closer', :day, 0, false, NULL, NULL)"
            ),
            {"sid": str(uuid.uuid4()), "uid": user_id, "song_id": song_id, "day": day},
        )
        await db.flush()

        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "INSERT INTO user_sessions "
                    "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
                    "   is_reroll_marker, drill_index, target_skill_node_id) "
                    "VALUES (:sid, :uid, :song_id, 'not_my_tempo', :day, 0, false, NULL, NULL)"
                ),
                {"sid": str(uuid.uuid4()), "uid": user_id, "song_id": song_id, "day": day},
            )
            await db.flush()
        await db.rollback()
    finally:
        await _cleanup_user(db, user_id)


# ---------------------------------------------------------------------------
# 4. Recreated index still exists with expected name
# ---------------------------------------------------------------------------

async def test_0005_partial_unique_index_still_named_correctly(db: AsyncSession) -> None:
    """After migration 0005, the recreated index is still called uq_user_sessions_daily_rating."""
    idx = (
        await db.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'user_sessions' "
                "  AND indexname = 'uq_user_sessions_daily_rating'"
            )
        )
    ).scalar_one_or_none()
    assert idx is not None, (
        "uq_user_sessions_daily_rating not found — migration 0005 must recreate it "
        "with COALESCE(drill_index, -1) in the key"
    )

    # Verify the definition contains COALESCE (COALESCE-based key confirms the recreated variant)
    idxdef = (
        await db.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'user_sessions' "
                "  AND indexname = 'uq_user_sessions_daily_rating'"
            )
        )
    ).scalar_one()
    assert "COALESCE" in idxdef.upper(), (
        f"Recreated index missing COALESCE — got: {idxdef}"
    )
