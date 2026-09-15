"""Alembic migration 0007 tests — song_catalog tuning column + 10 -> 60 expansion.

FLE-6 Task 8. These tests are the enforcement half of the tagging contract
documented at the top of 0007_song_catalog_tuning_and_expansion.py. The catalog
feeds the session generator, so an untagged or mistagged row is a silently bad
session rather than a loud failure — these assertions make it loud.

Covered:
- Schema: tuning column exists, NOT NULL, CHECK-constrained, server_default.
- Dedupe: song_catalog_title_artist_uidx exists and actually rejects a dupe.
- Volume: >= 60 rows.
- Tagging completeness: every row has a legal tuning, a legal primary_skill_root,
  a non-empty genre, and a difficulty in [0, 1].
- Selector coverage: NO 0.15-wide difficulty window in the player_level range has
  zero candidates. This is the assertion that actually protects the daily loop —
  selectors/today_song.py matches ABS(difficulty - player_level) <= 0.15, so an
  empty window means a real user gets NO song on a bank draw.
- Spread: genre / tuning / skill-root variety floors, so a future bulk edit that
  collapses the catalog back to "9 rock songs in standard" fails here.

Runs against the real Postgres test database (DATABASE_URL env var or default).
Requires `alembic upgrade head` to have been applied before running.
"""
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Read the migration's own constants so the test cannot drift from the data.
#
# Extracted via AST rather than imported: the migration does `from alembic import
# op` at module level, and conftest.py puts `server/` on sys.path[0], where the
# local `server/alembic/` package shadows the installed alembic distribution. So
# exec_module() on a migration raises ImportError under pytest. Every constant we
# want is a plain literal, so ast.literal_eval reads them without executing anything.
import ast
import pathlib

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0007_song_catalog_tuning_and_expansion.py"
)
_WANTED = ("ALLOWED_TUNINGS", "_NEW_SONGS", "_SEED_0003_TUNINGS")
_tree = ast.parse(_MIGRATION_PATH.read_text())
_consts = {
    node.targets[0].id: ast.literal_eval(node.value)
    for node in _tree.body
    if isinstance(node, ast.Assign)
    and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id in _WANTED
}
assert set(_consts) == set(_WANTED), (
    f"Migration 0007 no longer defines {set(_WANTED) - set(_consts)} as module-level literals"
)

ALLOWED_TUNINGS = set(_consts["ALLOWED_TUNINGS"])
NEW_SONGS = _consts["_NEW_SONGS"]
SEED_0003_TUNINGS = _consts["_SEED_0003_TUNINGS"]

VALID_SKILL_ROOTS = {
    "rhythm", "lead", "chord_voicings", "fingerstyle", "music_theory", "timing",
}

# selectors/today_song.py: ABS(sc.difficulty - pl.player_level) <= 0.15
SELECTOR_BAND = Decimal("0.15")


# ---------------------------------------------------------------------------
# Test database setup — mirrors test_alembic_0003.py
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

    Cleanup is done by the autouse _release_transaction fixture below, NOT here —
    see its docstring for why the rollback cannot live in this teardown.
    """
    async with SessionFactory() as session:
        yield session


@pytest.fixture(autouse=True)
async def _release_transaction(db):
    """Roll back after every test so no connection is checked out at session teardown.

    Most tests here are plain SELECTs, which still open an implicit transaction.
    Leaving one open means SQLAlchemy closes the connection when the session-scoped
    `db` fixture finalizes — and pytest-asyncio runs session-scoped finalizers on a
    different event loop than the one asyncpg bound the connection to, so that close
    raises 'Future attached to a different loop' and pytest reports a spurious
    'ERROR at teardown' on whichever test ran last.

    Rolling back here instead runs the cleanup inside the test's own loop, where the
    connection is valid, and leaves the session with nothing to close. This is why the
    rollback cannot simply be appended to the `db` fixture body — that code runs on the
    finalizer's loop and hits the same error it is trying to avoid.

    (test_alembic_0003.py dodges this only by accident: its final test happens to roll
    back inside a finally block.)
    """
    yield
    await db.rollback()


# ---------------------------------------------------------------------------
# 1. Schema
# ---------------------------------------------------------------------------

async def test_tuning_column_shape(db: AsyncSession) -> None:
    """song_catalog.tuning exists, is NOT NULL, and defaults to 'standard'."""
    result = await db.execute(
        text(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'song_catalog' AND column_name = 'tuning'"
        )
    )
    row = result.mappings().one_or_none()
    assert row is not None, "song_catalog.tuning column missing — migration 0007 not applied?"
    assert row["data_type"] == "text", f"Expected text, got {row['data_type']}"
    assert row["is_nullable"] == "NO", "tuning must be NOT NULL"
    assert "standard" in (row["column_default"] or ""), (
        f"tuning server_default should be 'standard', got {row['column_default']!r}"
    )


async def test_tuning_check_constraint_rejects_unknown(db: AsyncSession) -> None:
    """The CHECK constraint must reject a tuning outside ALLOWED_TUNINGS."""
    try:
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "INSERT INTO song_catalog (id, title, artist, genre, primary_skill_root, difficulty, tuning) "
                    "VALUES (:id, 'Bogus Tuning Probe', 'Test', 'Test', 'lead', 0.5, 'nashville_high_strung')"
                ),
                {"id": str(uuid.uuid4())},
            )
            await db.flush()
    finally:
        await db.rollback()


async def test_title_artist_dedupe_index_rejects_duplicate(db: AsyncSession) -> None:
    """song_catalog_title_artist_uidx must reject a case-insensitive duplicate.

    A duplicate row is not cosmetic: catalog_pick does ORDER BY random() LIMIT 1
    over the matching difficulty band, so a duplicated song is drawn twice as
    often as its neighbours.
    """
    try:
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "INSERT INTO song_catalog (id, title, artist, genre, primary_skill_root, difficulty, tuning) "
                    "VALUES (:id, 'kAsHmIr', 'led zeppelin', 'Hard Rock', 'timing', 0.61, 'dadgad')"
                ),
                {"id": str(uuid.uuid4())},
            )
            await db.flush()
    finally:
        await db.rollback()


# ---------------------------------------------------------------------------
# 2. Volume + tagging completeness
# ---------------------------------------------------------------------------

async def test_catalog_has_at_least_60_songs(db: AsyncSession) -> None:
    """The expansion target: ~60 songs live."""
    count = (await db.execute(text("SELECT COUNT(*) FROM song_catalog"))).scalar_one()
    assert count >= 60, f"Expected >= 60 song_catalog rows after migration 0007, got {count}"


async def test_all_new_songs_present(db: AsyncSession) -> None:
    """Every one of the 54 new curated entries landed."""
    rows = await db.execute(text("SELECT lower(title), lower(artist) FROM song_catalog"))
    present = {(t, a) for t, a in rows.all()}
    missing = [
        f"{title} — {artist}"
        for title, artist, _g, _s, _d, _tn in NEW_SONGS
        if (title.lower(), artist.lower()) not in present
    ]
    assert not missing, f"Missing {len(missing)} migration-0007 songs: {missing}"


async def test_every_row_is_fully_tagged(db: AsyncSession) -> None:
    """The generator reads these fields — no row may be half-tagged.

    Checked together rather than as four tests so a failure reports the whole
    offending row, which is what a curator needs to fix it.
    """
    rows = await db.execute(
        text(
            "SELECT title, artist, genre, primary_skill_root::text AS skill, difficulty, tuning "
            "FROM song_catalog ORDER BY difficulty"
        )
    )
    problems = []
    for r in rows.mappings().all():
        label = f"{r['title']} — {r['artist']}"
        if r["tuning"] not in ALLOWED_TUNINGS:
            problems.append(f"{label}: illegal tuning {r['tuning']!r}")
        if r["skill"] not in VALID_SKILL_ROOTS:
            problems.append(f"{label}: illegal primary_skill_root {r['skill']!r}")
        if not (r["genre"] or "").strip():
            problems.append(f"{label}: empty genre")
        if not (Decimal("0") <= r["difficulty"] <= Decimal("1")):
            problems.append(f"{label}: difficulty {r['difficulty']} outside [0, 1]")
    assert not problems, "Mistagged catalog rows:\n  " + "\n  ".join(problems)


async def test_seed_0003_tunings_backfilled(db: AsyncSession) -> None:
    """The 4 non-standard rows among the original 10 are no longer mislabelled 'standard'."""
    rows = await db.execute(text("SELECT title, tuning FROM song_catalog"))
    actual = {t: tn for t, tn in rows.all()}
    wrong = {
        title: (actual.get(title), expected)
        for title, expected in SEED_0003_TUNINGS.items()
        if actual.get(title) != expected
    }
    assert not wrong, f"Migration-0003 rows with wrong tuning (got, expected): {wrong}"


# ---------------------------------------------------------------------------
# 3. Selector coverage — the assertion that protects the daily loop
# ---------------------------------------------------------------------------

async def test_no_empty_selector_window(db: AsyncSession) -> None:
    """Every plausible player_level must have at least one catalog candidate.

    selectors/today_song.py picks catalog songs with
    ABS(difficulty - player_level) <= 0.15. If a player_level lands in a window
    with no songs, catalog_pick returns nothing and the user gets no song on a
    bank draw. Swept at 0.05 resolution across the reachable player_level range.

    player_level = AVG(mastery) over leaf skill_nodes, so it is bounded by the
    mastery scale [0, 1]; new users fall back to 0.5.
    """
    difficulties = [
        d for (d,) in (await db.execute(text("SELECT difficulty FROM song_catalog"))).all()
    ]
    assert difficulties, "song_catalog is empty"

    empty_windows = []
    level = Decimal("0.00")
    while level <= Decimal("1.00"):
        candidates = [d for d in difficulties if abs(d - level) <= SELECTOR_BAND]
        if not candidates:
            empty_windows.append(str(level))
        level += Decimal("0.05")

    assert not empty_windows, (
        "player_level values with ZERO catalog candidates (user would get no song "
        f"on a bank draw): {empty_windows}"
    )


async def test_selector_window_density_at_anchors(db: AsyncSession) -> None:
    """Beginner / intermediate / advanced anchors each need real choice, not one song.

    A single candidate in a window means every bank draw at that level returns
    the same song — the churn failure this task exists to prevent. 3 is the
    floor for "the reroll can actually produce something different".
    """
    difficulties = [
        d for (d,) in (await db.execute(text("SELECT difficulty FROM song_catalog"))).all()
    ]
    thin = {}
    for anchor in (Decimal("0.20"), Decimal("0.50"), Decimal("0.80")):
        n = len([d for d in difficulties if abs(d - anchor) <= SELECTOR_BAND])
        if n < 3:
            thin[str(anchor)] = n
    assert not thin, f"Difficulty anchors with fewer than 3 candidates: {thin}"


# ---------------------------------------------------------------------------
# 4. Spread — guards against the catalog collapsing back to one player's taste
# ---------------------------------------------------------------------------

async def test_genre_spread(db: AsyncSession) -> None:
    """Author bias is a named risk in the FLE-1 plan. Enforce real genre variety.

    Also caps any single genre's share: 9 of the original 10 rows were Rock or
    Blues, which is the exact shape this guards against.
    """
    rows = await db.execute(
        text("SELECT genre, COUNT(*) AS n FROM song_catalog GROUP BY genre ORDER BY n DESC")
    )
    counts = {r["genre"]: r["n"] for r in rows.mappings().all()}
    total = sum(counts.values())
    assert len(counts) >= 20, f"Expected >= 20 distinct genres, got {len(counts)}: {counts}"
    top_genre, top_n = max(counts.items(), key=lambda kv: kv[1])
    assert top_n <= total * 0.25, (
        f"Genre {top_genre!r} is {top_n}/{total} of the catalog — over the 25% concentration cap"
    )


async def test_tuning_spread(db: AsyncSession) -> None:
    """Non-standard tunings must be materially represented, not a token one or two.

    Alt tunings are where the AI teacher earns trust with intermediate/advanced
    players (see .planning/investigations/260908-sonnet-tuning-quality.md), so the
    catalog has to actually exercise that path.
    """
    rows = await db.execute(
        text("SELECT tuning, COUNT(*) AS n FROM song_catalog GROUP BY tuning")
    )
    counts = {r["tuning"]: r["n"] for r in rows.mappings().all()}
    total = sum(counts.values())
    non_standard = total - counts.get("standard", 0)
    assert len(counts) >= 7, f"Expected >= 7 distinct tunings in use, got {len(counts)}: {counts}"
    assert non_standard >= total * 0.20, (
        f"Only {non_standard}/{total} catalog songs are in a non-standard tuning — under the 20% floor"
    )


async def test_every_skill_root_has_songs(db: AsyncSession) -> None:
    """All 6 skill roots need catalog material, or a user focused on one gets nothing."""
    rows = await db.execute(
        text("SELECT primary_skill_root::text AS skill, COUNT(*) AS n FROM song_catalog GROUP BY 1")
    )
    counts = {r["skill"]: r["n"] for r in rows.mappings().all()}
    missing = VALID_SKILL_ROOTS - set(counts)
    assert not missing, f"Skill roots with no catalog songs: {missing} (have: {counts})"
    thin = {k: v for k, v in counts.items() if v < 3}
    assert not thin, f"Skill roots with fewer than 3 songs: {thin}"
