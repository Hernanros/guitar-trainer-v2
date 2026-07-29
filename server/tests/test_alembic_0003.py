"""Alembic migration 0003 tests.

Verifies migration 0003 (song_catalog + user_sessions + breakdown_generated_at):
- Schema assertions: enum values, column existence, index existence
- Seed data: 10 hand-curated song_catalog rows present
- Partial-unique index behavior: coexistence of reroll marker + rating for same (user, song, day)
- Dual-default on SongCatalog.difficulty (Python-side + server_default)
- songs.breakdown_generated_at nullable timestamptz

Runs against the real Postgres test database (DATABASE_URL env var or default).
Requires `alembic upgrade head` to have been applied before running.

Uses session-scoped event loop from conftest.py + asyncpg for async DB access.
"""
import os
import uuid
from datetime import date
from decimal import Decimal

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
# 1. Enum value verification
# ---------------------------------------------------------------------------

async def test_primary_skill_root_enum_values(db: AsyncSession) -> None:
    """primary_skill_root enum must have exactly 6 values matching PrimarySkillRoot."""
    result = await db.execute(
        text("SELECT ARRAY(SELECT unnest(enum_range(NULL::primary_skill_root))) AS vals")
    )
    row = result.one()
    vals = row.vals
    expected = {"rhythm", "lead", "chord_voicings", "fingerstyle", "music_theory", "timing"}
    assert set(vals) == expected, f"primary_skill_root enum values mismatch: {vals}"
    assert len(vals) == 6, f"Expected 6 values, got {len(vals)}"


async def test_rating_level_enum_values(db: AsyncSession) -> None:
    """rating_level enum must have exactly 3 values matching RatingLevel."""
    result = await db.execute(
        text("SELECT ARRAY(SELECT unnest(enum_range(NULL::rating_level))) AS vals")
    )
    row = result.one()
    vals = row.vals
    expected = {"not_my_tempo", "getting_closer", "thats_what_im_looking_for"}
    assert set(vals) == expected, f"rating_level enum values mismatch: {vals}"
    assert len(vals) == 3, f"Expected 3 values, got {len(vals)}"


# ---------------------------------------------------------------------------
# 2. Seed catalog verification
# ---------------------------------------------------------------------------

async def test_song_catalog_row_count(db: AsyncSession) -> None:
    """song_catalog must have at least 10 rows after migration 0003 seed."""
    result = await db.execute(text("SELECT COUNT(*) FROM song_catalog"))
    count = result.scalar_one()
    assert count >= 10, f"Expected >= 10 song_catalog rows, got {count}"


async def test_song_catalog_seed_titles(db: AsyncSession) -> None:
    """All 10 hand-curated songs must be present in song_catalog."""
    expected_titles = {
        "Blackbird",
        "Comfortably Numb",
        "Eruption",
        "Hotel California",
        "Little Wing",
        "Purple Haze",
        "Sweet Home Chicago",
        "Thunderstruck",
        "Wish You Were Here",
        "Wonderwall",
    }
    result = await db.execute(text("SELECT title FROM song_catalog"))
    actual_titles = {row.title for row in result.all()}
    missing = expected_titles - actual_titles
    assert not missing, f"Missing seed songs from song_catalog: {missing}"


# ---------------------------------------------------------------------------
# 3. Column existence checks
# ---------------------------------------------------------------------------

async def test_breakdown_generated_at_column(db: AsyncSession) -> None:
    """songs.breakdown_generated_at must exist as nullable timestamptz."""
    result = await db.execute(
        text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'songs' AND column_name = 'breakdown_generated_at'
        """)
    )
    row = result.one_or_none()
    assert row is not None, "songs.breakdown_generated_at column not found"
    assert row.is_nullable == "YES", "songs.breakdown_generated_at should be nullable"
    # data_type for TIMESTAMPTZ is 'timestamp with time zone' in information_schema
    assert "timestamp" in row.data_type.lower(), f"Unexpected data_type: {row.data_type}"


async def test_user_sessions_bank_source_column(db: AsyncSession) -> None:
    """user_sessions.bank_source must exist as nullable character varying (Revision B)."""
    result = await db.execute(
        text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'user_sessions' AND column_name = 'bank_source'
        """)
    )
    row = result.one_or_none()
    assert row is not None, "user_sessions.bank_source column not found (Revision B)"
    assert row.is_nullable == "YES", "user_sessions.bank_source should be nullable"
    assert "character varying" in row.data_type.lower(), f"Unexpected data_type: {row.data_type}"


# ---------------------------------------------------------------------------
# 4. Partial-unique index verification (Revision C)
# ---------------------------------------------------------------------------

async def test_partial_unique_indexes_exist(db: AsyncSession) -> None:
    """Both partial-unique indexes must exist on user_sessions (Revision C)."""
    result = await db.execute(
        text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'user_sessions'
            AND indexname IN ('uq_user_sessions_daily_reroll', 'uq_user_sessions_daily_rating')
        """)
    )
    found = {row.indexname for row in result.all()}
    assert "uq_user_sessions_daily_reroll" in found, "uq_user_sessions_daily_reroll index not found"
    assert "uq_user_sessions_daily_rating" in found, "uq_user_sessions_daily_rating index not found"


async def test_songs_user_title_artist_uidx_exists(db: AsyncSession) -> None:
    """songs_user_title_artist_uidx must exist on songs (Revision F)."""
    result = await db.execute(
        text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'songs' AND indexname = 'songs_user_title_artist_uidx'
        """)
    )
    row = result.one_or_none()
    assert row is not None, "songs_user_title_artist_uidx index not found (Revision F)"


async def test_duplicate_rating_raises_integrity_error(db: AsyncSession) -> None:
    """Second rating for same (user_id, song_id, local_calendar_day) raises IntegrityError (Revision C)."""
    # Fetch a real user_id and song_id from the DB (must exist from prior migrations)
    user_row = await db.execute(text("SELECT id FROM users LIMIT 1"))
    user_id = user_row.scalar_one()
    song_row = await db.execute(text("SELECT id FROM songs LIMIT 1"))
    song_id = song_row.scalar_one()

    test_day = date(2099, 1, 1)  # Far future — won't collide with real usage; date object for asyncpg
    session_id_1 = str(uuid.uuid4())
    session_id_2 = str(uuid.uuid4())

    try:
        # Insert first rating
        await db.execute(
            text("""
                INSERT INTO user_sessions
                    (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes,
                     is_reroll_marker)
                VALUES
                    (:id, :uid, :sid, 'getting_closer', :day, 0, false)
            """),
            {"id": session_id_1, "uid": str(user_id), "sid": song_id, "day": test_day},
        )
        await db.flush()

        # Second rating for same (user_id, song_id, day) with is_reroll_marker=false should fail
        with pytest.raises(IntegrityError):
            await db.execute(
                text("""
                    INSERT INTO user_sessions
                        (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes,
                         is_reroll_marker)
                    VALUES
                        (:id, :uid, :sid, 'not_my_tempo', :day, 0, false)
                """),
                {"id": session_id_2, "uid": str(user_id), "sid": song_id, "day": test_day},
            )
            await db.flush()
    finally:
        # Rollback to clean state regardless of result
        await db.rollback()


async def test_reroll_marker_coexists_with_rating(db: AsyncSession) -> None:
    """Reroll marker (is_reroll_marker=true) + rating (is_reroll_marker=false) can coexist
    for same (user_id, song_id, local_calendar_day) — Revision C partial-unique semantics."""
    user_row = await db.execute(text("SELECT id FROM users LIMIT 1"))
    user_id = user_row.scalar_one()
    song_row = await db.execute(text("SELECT id FROM songs LIMIT 1"))
    song_id = song_row.scalar_one()

    test_day = date(2099, 2, 1)  # Unique far-future day; date object for asyncpg
    reroll_id = str(uuid.uuid4())
    rating_id = str(uuid.uuid4())

    try:
        # Insert reroll marker (is_reroll_marker=true, rating=NULL)
        await db.execute(
            text("""
                INSERT INTO user_sessions
                    (id, user_id, song_id, local_calendar_day, tz_offset_minutes,
                     is_reroll_marker, bank_source)
                VALUES
                    (:id, :uid, :sid, :day, 0, true, 'seed_catalog')
            """),
            {"id": reroll_id, "uid": str(user_id), "sid": song_id, "day": test_day},
        )

        # Insert rating for same (user, song, day) with is_reroll_marker=false — should succeed
        await db.execute(
            text("""
                INSERT INTO user_sessions
                    (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes,
                     is_reroll_marker)
                VALUES
                    (:id, :uid, :sid, 'thats_what_im_looking_for', :day, 0, false)
            """),
            {"id": rating_id, "uid": str(user_id), "sid": song_id, "day": test_day},
        )
        await db.flush()

        # Both rows should exist
        result = await db.execute(
            text("""
                SELECT COUNT(*) FROM user_sessions
                WHERE local_calendar_day = :day AND user_id = :uid
            """),
            {"day": test_day, "uid": str(user_id)},
        )
        count = result.scalar_one()
        assert count == 2, f"Expected 2 rows (reroll + rating), got {count}"
    finally:
        await db.rollback()


# ---------------------------------------------------------------------------
# 5. ORM dual-default verification (SongCatalog.difficulty)
# ---------------------------------------------------------------------------

async def test_song_catalog_orm_dual_default(db: AsyncSession) -> None:
    """SongCatalog.difficulty dual-default: explicit value preserved; after DB INSERT and refresh,
    omitted difficulty resolves to 0.0 from server_default (Phase 2 hotfix 5076789 pattern).

    The SQLAlchemy 'default' applies during INSERT statement generation (not __init__).
    The 'server_default' ensures the DB populates 0.0 on direct-SQL inserts.
    Both ensure that after flush+refresh, difficulty is never None in the API response.
    """
    from app.models.db import SongCatalog, PrimarySkillRoot

    # Explicit difficulty
    song_explicit = SongCatalog(
        title="Test Dual Default Explicit",
        artist="Test Artist",
        genre="Test",
        primary_skill_root=PrimarySkillRoot.RHYTHM,
        difficulty=Decimal("0.5"),
    )
    assert song_explicit.difficulty == Decimal("0.5"), "Explicit difficulty should be preserved"

    # Insert a song with explicit difficulty and verify it round-trips through DB
    try:
        db.add(song_explicit)
        await db.flush()
        await db.refresh(song_explicit)
        assert song_explicit.difficulty == Decimal("0.5"), (
            f"Explicit difficulty not preserved after DB round-trip: {song_explicit.difficulty!r}"
        )

        # Verify server_default via raw SQL — insert without difficulty, check DB gives 0.0
        test_id = str(uuid.uuid4())
        await db.execute(
            text("""
                INSERT INTO song_catalog (id, title, artist, genre, primary_skill_root)
                VALUES (:id, 'Test Default Song', 'Test', 'Test', 'lead')
            """),
            {"id": test_id},
        )
        await db.flush()
        result = await db.execute(
            text("SELECT difficulty FROM song_catalog WHERE id = :id"),
            {"id": test_id},
        )
        difficulty_from_db = result.scalar_one()
        assert difficulty_from_db == Decimal("0.0") or difficulty_from_db == 0, (
            f"server_default 0.0 not applied: {difficulty_from_db!r}"
        )
    finally:
        await db.rollback()
