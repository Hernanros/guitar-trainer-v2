"""FLE-108 — songs.tuning actually gets written, and NULL means off catalog.

Migration 0014 and test_alembic_0014.py already cover the column's shape, its
CHECK constraint, and the one writer 0014 shipped with
(_ensure_catalog_song_as_user_song). What neither covered is that the column was
NULL on **48 of 48 prod rows** while 39 of them matched a catalog row — the
catalog-pick path is not how songs are born, and nothing else wrote it.

These tests pin the three new writers and the resolution rule:

1. `catalog_tuning_map` / `catalog_tuning` — the exact-key lookup, including the
   deliberate non-match on a band-vs-member artist credit (FLE-108 asked for a
   fuzzy join; prod has zero instances needing one, and a loose join can match
   the wrong song).
2. `resolve_known_tuning` — returns the column when set, falls back to the
   catalog and assigns the tag back onto the row, and returns None (no
   exception) off catalog, because a hard failure would block free-text entry.
3. Migration 0017's backfill predicate — matched NULL rows get a tuning,
   off-catalog rows stay NULL.

The regression that motivates 2 is specific: `GET /songs/{id}/breakdown` passed
the raw `song.tuning` column, so after FLE-128 made a catalog miss default to
`standard`, a NULL column on an in-catalog song turned a correct Sonnet
inference into a confident wrong default. Kashmir (catalog: dadgad) was one tap
away from being regenerated in standard tuning.

Runs against the real Postgres test database. Requires `alembic upgrade head`.
"""
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.song_tuning import (
    catalog_tuning,
    catalog_tuning_map,
    resolve_known_tuning,
)


def _make_test_db_url() -> str:
    raw = os.environ.get(
        "DATABASE_URL",
        "postgresql://gt:devpass@localhost:5433/guitar_trainer",
    )
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return (
                raw
                if prefix == "postgresql+asyncpg://"
                else raw.replace(prefix, "postgresql+asyncpg://", 1)
            )
    return raw


engine = create_async_engine(_make_test_db_url(), echo=False, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Distinctive titles so the probes can never collide with catalog or seed data.
CATALOG_TITLE = "Kashmir (FLE-108 Probe)"
CATALOG_ARTIST = "Led Zeppelin"
CATALOG_TUNING = "dadgad"
OFF_CATALOG_TITLE = "Cause We've Ended as Lovers (FLE-108 Probe)"
OFF_CATALOG_ARTIST = "Jeff Beck"


@pytest.fixture(scope="session")
async def db():
    async with SessionFactory() as session:
        yield session


@pytest.fixture(autouse=True)
async def _release_transaction(db):
    yield
    await db.rollback()


@pytest.fixture
async def seeded(db: AsyncSession):
    """One catalog row and a user, inside the test's transaction (no commit)."""
    catalog_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:id, '{}'::jsonb)"),
        {"id": str(user_id)},
    )
    await db.execute(
        text(
            "INSERT INTO song_catalog "
            "(id, title, artist, genre, primary_skill_root, difficulty, tuning) "
            "VALUES (:id, :title, :artist, 'Rock', 'rhythm', 0.7, :tuning)"
        ),
        {
            "id": str(catalog_id),
            "title": CATALOG_TITLE,
            "artist": CATALOG_ARTIST,
            "tuning": CATALOG_TUNING,
        },
    )
    await db.flush()
    return {"catalog_id": catalog_id, "user_id": user_id}


async def _insert_song(
    db: AsyncSession, user_id: uuid.UUID, title: str, artist: str, tuning: str | None
) -> int:
    result = await db.execute(
        text(
            "INSERT INTO songs (title, artist, breakdown, category, user_id, tuning) "
            "VALUES (:title, :artist, "
            "'{\"placeholder\": \"test\"}'::jsonb, 'aspirational', "
            "CAST(:user_id AS uuid), :tuning) RETURNING id"
        ),
        {
            "title": title,
            "artist": artist,
            "user_id": str(user_id),
            "tuning": tuning,
        },
    )
    song_id = int(result.scalar_one())
    await db.flush()
    return song_id


# ---------------------------------------------------------------------------
# 1. The lookup
# ---------------------------------------------------------------------------

async def test_catalog_tuning_exact_match(db: AsyncSession, seeded) -> None:
    assert await catalog_tuning(db, CATALOG_TITLE, CATALOG_ARTIST) == CATALOG_TUNING


async def test_catalog_tuning_is_case_and_whitespace_insensitive(
    db: AsyncSession, seeded
) -> None:
    """Same normalization as songs_user_title_artist_uidx, plus trim.

    A wizard-entered artist with a trailing space must not read as off catalog.
    """
    assert (
        await catalog_tuning(db, CATALOG_TITLE.upper(), "  led zeppelin ")
        == CATALOG_TUNING
    )


async def test_catalog_tuning_misses_a_different_artist_credit(
    db: AsyncSession, seeded
) -> None:
    """The deliberate miss. A band-vs-member credit does NOT match.

    FLE-108 proposed an alias column or a looser join for exactly this. It stays
    an exact join on purpose: joining on title alone across all of prod returns
    zero rows whose artists differ, so there is nothing to catch, and a loose
    join would happily match a different song of the same name.
    """
    assert await catalog_tuning(db, CATALOG_TITLE, "Jimmy Page") is None


async def test_catalog_tuning_map_keys_on_normalized_pair(
    db: AsyncSession, seeded
) -> None:
    mapping = await catalog_tuning_map(db)
    assert mapping[(CATALOG_TITLE.lower(), CATALOG_ARTIST.lower())] == CATALOG_TUNING
    # The real catalog is loaded too — the map is the whole table, not a filter.
    assert len(mapping) > 1


# ---------------------------------------------------------------------------
# 2. Resolution at generation time
# ---------------------------------------------------------------------------

class _FakeSong:
    """Stands in for the Song ORM row: resolve_known_tuning only needs these."""

    def __init__(self, title, artist, tuning=None, song_id=1):
        self.title = title
        self.artist = artist
        self.tuning = tuning
        self.id = song_id


async def test_resolve_prefers_the_already_set_column(db: AsyncSession, seeded) -> None:
    """A populated column is authoritative and costs no catalog lookup."""
    song = _FakeSong(CATALOG_TITLE, CATALOG_ARTIST, tuning="eb_standard")
    assert await resolve_known_tuning(db, song) == "eb_standard"
    assert song.tuning == "eb_standard"


async def test_resolve_falls_back_to_catalog_and_backfills_the_row(
    db: AsyncSession, seeded
) -> None:
    """The regression this issue is about.

    The column is NULL on an in-catalog song — the state of 39 of 48 prod rows.
    Resolution must return the catalog's tag, not None: returning None would send
    the generator down FLE-128's default-to-standard path and produce a standard
    tuning for a DADGAD song.
    """
    song = _FakeSong(CATALOG_TITLE, CATALOG_ARTIST, tuning=None)
    assert await resolve_known_tuning(db, song) == CATALOG_TUNING
    assert song.tuning == CATALOG_TUNING, (
        "the resolved tag must be assigned back onto the row so the caller's "
        "commit persists it — otherwise every breakdown re-pays the lookup and "
        "the column stays NULL forever"
    )


async def test_resolve_returns_none_off_catalog_without_raising(
    db: AsyncSession, seeded
) -> None:
    """Off catalog is a logged miss, never an exception.

    FLE-108 asked for off-catalog generation to fail loudly. FLE-125 §5.1 settled
    it the other way round: hard-failing blocks free-text entry, which is the
    common path for real users. The loud half is the WARNING here plus
    Breakdown.tuning_source='default'.
    """
    song = _FakeSong(OFF_CATALOG_TITLE, OFF_CATALOG_ARTIST, tuning=None)
    assert await resolve_known_tuning(db, song) is None
    assert song.tuning is None


async def test_resolve_logs_the_off_catalog_miss(
    db: AsyncSession, seeded, caplog
) -> None:
    import logging

    song = _FakeSong(OFF_CATALOG_TITLE, OFF_CATALOG_ARTIST, tuning=None)
    with caplog.at_level(logging.WARNING, logger="app.services.song_tuning"):
        await resolve_known_tuning(db, song)
    assert any(
        "Off-catalog breakdown" in r.message or "Off-catalog breakdown" in r.getMessage()
        for r in caplog.records
    ), "a catalog miss must be visible in the logs — that is FLE-108's 'loudly'"


# ---------------------------------------------------------------------------
# 3. Migration 0017's backfill predicate
# ---------------------------------------------------------------------------

_BACKFILL = """
UPDATE songs s
   SET tuning = c.tuning
  FROM song_catalog c
 WHERE s.tuning IS NULL
   AND lower(s.title) = lower(c.title)
   AND lower(trim(s.artist)) = lower(trim(c.artist))
"""


async def test_backfill_fills_matched_rows_and_leaves_off_catalog_null(
    db: AsyncSession, seeded
) -> None:
    user_id = seeded["user_id"]
    matched = await _insert_song(db, user_id, CATALOG_TITLE, CATALOG_ARTIST, None)
    unmatched = await _insert_song(
        db, user_id, OFF_CATALOG_TITLE, OFF_CATALOG_ARTIST, None
    )

    await db.execute(text(_BACKFILL))
    await db.flush()

    rows = dict(
        (int(r["id"]), r["tuning"])
        for r in (
            await db.execute(
                text("SELECT id, tuning FROM songs WHERE id = ANY(:ids)"),
                {"ids": [matched, unmatched]},
            )
        ).mappings()
    )
    assert rows[matched] == CATALOG_TUNING
    assert rows[unmatched] is None, (
        "an off-catalog row must stay NULL — defaulting it to 'standard' here "
        "would erase the only signal that separates catalog ground truth from a "
        "chosen default (FLE-125 option 1)"
    )


async def test_backfill_does_not_overwrite_an_existing_value(
    db: AsyncSession, seeded
) -> None:
    """Idempotence, and the reason for `WHERE tuning IS NULL`.

    A row whose tuning was corrected by hand must survive a re-run.
    """
    user_id = seeded["user_id"]
    song_id = await _insert_song(
        db, user_id, CATALOG_TITLE, CATALOG_ARTIST, "eb_standard"
    )

    await db.execute(text(_BACKFILL))
    await db.flush()

    value = (
        await db.execute(
            text("SELECT tuning FROM songs WHERE id = :id"), {"id": song_id}
        )
    ).scalar_one()
    assert value == "eb_standard"
