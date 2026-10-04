"""Alembic migration 0014 tests — songs.tuning, carried from song_catalog (FLE-33).

FLE-33. The enforcement half of the contract at the top of
0014_songs_tuning.py: song_catalog.tuning (migration 0007) was write-only until
this migration gave `songs` a column to carry it into, and until
_ensure_catalog_song_as_user_song (selectors/today_song.py) actually wrote it.

Covered:
- Three-way parity: this migration's ALLOWED_TUNINGS, migration 0007's
  ALLOWED_TUNINGS, and app.ai.breakdown.TUNING_NOTE_MAP's keys must be the exact
  same set. Drift between any two silently breaks either the CHECK constraint
  (schema rejects a tag the prompt table doesn't know) or the breakdown prompt
  (prompt table lacks a tag the schema allows) — this test makes drift loud.
- Schema: songs.tuning exists, is nullable, and its CHECK accepts NULL + every
  allowed value and rejects a bogus one.
- Behavior: _ensure_catalog_song_as_user_song carries tuning through on a fresh
  insert, and backfills tuning on a pre-existing songs row that predates this
  column (tuning IS NULL).

Runs against the real Postgres test database. Requires `alembic upgrade head`.
"""
import ast
import os
import pathlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Read the migration's own ALLOWED_TUNINGS via AST rather than importing it — the
# migration does `from alembic import op` at module level, and conftest.py puts
# server/ on sys.path where the local `server/alembic/` package shadows the
# installed alembic distribution. Same technique as test_alembic_0007/0011.
_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0014_songs_tuning.py"
)
_tree = ast.parse(_MIGRATION_PATH.read_text())
_consts = {
    node.targets[0].id: ast.literal_eval(node.value)
    for node in _tree.body
    if isinstance(node, ast.Assign)
    and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id == "ALLOWED_TUNINGS"
}
assert "ALLOWED_TUNINGS" in _consts, "Migration 0014 no longer defines ALLOWED_TUNINGS as a module-level literal"
MIGRATION_0014_TUNINGS = set(_consts["ALLOWED_TUNINGS"])

_MIGRATION_0007_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0007_song_catalog_tuning_and_expansion.py"
)
_tree_0007 = ast.parse(_MIGRATION_0007_PATH.read_text())
_consts_0007 = {
    node.targets[0].id: ast.literal_eval(node.value)
    for node in _tree_0007.body
    if isinstance(node, ast.Assign)
    and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id == "ALLOWED_TUNINGS"
}
MIGRATION_0007_TUNINGS = set(_consts_0007["ALLOWED_TUNINGS"])

# Imported, not AST-read: breakdown.py is plain-importable (no alembic shadow issue).
from app.ai.breakdown import TUNING_NOTE_MAP  # noqa: E402

BREAKDOWN_MAP_TUNINGS = set(TUNING_NOTE_MAP.keys())


def test_tuning_sets_agree_three_way():
    assert MIGRATION_0014_TUNINGS == MIGRATION_0007_TUNINGS, (
        "0014's ALLOWED_TUNINGS has drifted from 0007's — a value either one "
        "allows and the other doesn't makes the CHECK constraints disagree."
    )
    assert MIGRATION_0014_TUNINGS == BREAKDOWN_MAP_TUNINGS, (
        "0014's ALLOWED_TUNINGS has drifted from app.ai.breakdown.TUNING_NOTE_MAP's "
        "keys — a tag the schema allows but the prompt table doesn't know produces "
        "a known_tuning the user message silently skips (treated as unrecognized)."
    )


# ---------------------------------------------------------------------------
# Test database setup — mirrors test_alembic_0007.py
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
    """Session-scoped async DB session — shares the conftest.py event loop.

    Cleanup is the autouse _release_transaction fixture below, not here — see
    test_alembic_0007.py's identical fixture for why.
    """
    async with SessionFactory() as session:
        yield session


@pytest.fixture(autouse=True)
async def _release_transaction(db):
    yield
    await db.rollback()


# ---------------------------------------------------------------------------
# 1. Schema
# ---------------------------------------------------------------------------

async def test_tuning_column_shape(db: AsyncSession) -> None:
    """songs.tuning exists, is nullable, and has no server default."""
    result = await db.execute(
        text(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'songs' AND column_name = 'tuning'"
        )
    )
    row = result.mappings().one_or_none()
    assert row is not None, "songs.tuning column missing — migration 0014 not applied?"
    assert row["data_type"] == "text", f"Expected text, got {row['data_type']}"
    assert row["is_nullable"] == "YES", (
        "tuning must stay nullable — NULL is the 'no catalog ground truth' signal"
    )
    assert row["column_default"] is None, (
        "tuning must have no DEFAULT — unlike song_catalog.tuning, NULL vs "
        "non-NULL on `songs` is a meaningful distinction, not a gap to paper over"
    )


async def test_tuning_check_constraint_allows_null(db: AsyncSession) -> None:
    """NULL must be insertable — it is the primary state for every pre-0014 row
    and every user-onboarded song with no song_catalog match."""
    song_id = None
    try:
        result = await db.execute(
            text(
                "INSERT INTO songs (title, artist, breakdown, category, tuning) "
                "VALUES ('Tuning Null Probe', 'Test', "
                "'{\"tab\":{\"measures\":[],\"tuning\":[]},\"chords\":[],\"technique_notes\":[]}'::jsonb, "
                "'aspirational', NULL) RETURNING id"
            )
        )
        song_id = result.scalar_one()
        await db.flush()
    finally:
        await db.rollback()


async def test_tuning_check_constraint_rejects_unknown(db: AsyncSession) -> None:
    """The CHECK constraint must reject a tuning outside ALLOWED_TUNINGS."""
    try:
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "INSERT INTO songs (title, artist, breakdown, category, tuning) "
                    "VALUES ('Bogus Tuning Probe', 'Test', "
                    "'{\"tab\":{\"measures\":[],\"tuning\":[]},\"chords\":[],\"technique_notes\":[]}'::jsonb, "
                    "'aspirational', 'nashville_high_strung')"
                )
            )
            await db.flush()
    finally:
        await db.rollback()


@pytest.mark.parametrize("tuning", sorted(MIGRATION_0014_TUNINGS))
async def test_tuning_check_constraint_accepts_every_allowed_value(
    db: AsyncSession, tuning: str
) -> None:
    try:
        await db.execute(
            text(
                "INSERT INTO songs (title, artist, breakdown, category, tuning) "
                "VALUES ('Tuning Accept Probe', 'Test', "
                "'{\"tab\":{\"measures\":[],\"tuning\":[]},\"chords\":[],\"technique_notes\":[]}'::jsonb, "
                "'aspirational', :tuning)"
            ),
            {"tuning": tuning},
        )
        await db.flush()
    finally:
        await db.rollback()


# ---------------------------------------------------------------------------
# 2. Behavior — _ensure_catalog_song_as_user_song carries/backfills tuning
# ---------------------------------------------------------------------------
# Separate function-scoped session: _ensure_catalog_song_as_user_song commits
# internally (selectors/today_song.py), so the session-scoped rollback fixture
# above cannot isolate these tests — explicit teardown DELETEs instead, same
# pattern as test_today_song_selector.py's seed_user fixture.

def _make_session() -> AsyncSession:
    return SessionFactory()


@pytest.fixture
async def behavior_db():
    async with _make_session() as session:
        yield session


async def test_ensure_catalog_song_carries_tuning_on_insert(behavior_db: AsyncSession) -> None:
    from app.selectors.today_song import _ensure_catalog_song_as_user_song

    db = behavior_db
    user_id = uuid.uuid4()
    catalog_id = uuid.uuid4()

    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:id, '{}'::jsonb)"),
        {"id": str(user_id)},
    )
    await db.execute(
        text(
            "INSERT INTO song_catalog (id, title, artist, genre, primary_skill_root, difficulty, tuning) "
            "VALUES (:id, 'Little Wing (0014 Probe)', 'Jimi Hendrix', 'Rock', 'lead', 0.6, 'eb_standard')"
        ),
        {"id": str(catalog_id)},
    )
    await db.commit()

    try:
        songs_id = await _ensure_catalog_song_as_user_song(db, user_id, catalog_id)

        row = (
            await db.execute(text("SELECT tuning FROM songs WHERE id = :id"), {"id": songs_id})
        ).mappings().one()
        assert row["tuning"] == "eb_standard", (
            "song_catalog.tuning must be carried into the new songs row, not "
            "dropped on the floor"
        )
    finally:
        await db.execute(text("DELETE FROM songs WHERE user_id = :uid"), {"uid": str(user_id)})
        await db.execute(text("DELETE FROM song_catalog WHERE id = :id"), {"id": str(catalog_id)})
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(user_id)})
        await db.commit()


async def test_ensure_catalog_song_backfills_tuning_on_existing_row(behavior_db: AsyncSession) -> None:
    """A songs row inserted before this column carried data (tuning IS NULL) gets
    backfilled the next time the same user is ensured into the same catalog song —
    not left permanently stuck on the pre-0014 state."""
    from app.selectors.today_song import _ensure_catalog_song_as_user_song

    db = behavior_db
    user_id = uuid.uuid4()
    catalog_id = uuid.uuid4()

    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:id, '{}'::jsonb)"),
        {"id": str(user_id)},
    )
    await db.execute(
        text(
            "INSERT INTO song_catalog (id, title, artist, genre, primary_skill_root, difficulty, tuning) "
            "VALUES (:id, 'Drop D Probe', 'Test Artist', 'Rock', 'rhythm', 0.4, 'drop_d')"
        ),
        {"id": str(catalog_id)},
    )
    # Pre-existing songs row, as if upserted before migration 0014 ever ran:
    # same title/artist (case-insensitive match key), tuning left NULL.
    await db.execute(
        text(
            "INSERT INTO songs (title, artist, genre, difficulty, breakdown, user_id, category, tuning) "
            "VALUES ('Drop D Probe', 'Test Artist', 'Rock', 'Intermediate', "
            "'{\"tab\":{\"measures\":[],\"tuning\":[]},\"chords\":[],\"technique_notes\":[]}'::jsonb, "
            ":uid, 'aspirational', NULL)"
        ),
        {"uid": str(user_id)},
    )
    await db.commit()

    try:
        songs_id = await _ensure_catalog_song_as_user_song(db, user_id, catalog_id)

        row = (
            await db.execute(text("SELECT tuning FROM songs WHERE id = :id"), {"id": songs_id})
        ).mappings().one()
        assert row["tuning"] == "drop_d", (
            "a pre-existing NULL-tuning songs row must be backfilled from "
            "song_catalog on the next ensure, not left stuck NULL forever"
        )
    finally:
        await db.execute(text("DELETE FROM songs WHERE user_id = :uid"), {"uid": str(user_id)})
        await db.execute(text("DELETE FROM song_catalog WHERE id = :id"), {"id": str(catalog_id)})
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(user_id)})
        await db.commit()
