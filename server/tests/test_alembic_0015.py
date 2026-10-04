"""Alembic migration 0015 tests — song_catalog.rights_status column + 64-row backfill.

FLE-51. Sibling of test_alembic_0007.py and deliberately built on the same
pattern: read the migration's own constants via AST, then assert the database
agrees with them.

Why these assertions and not others
-----------------------------------
rights_status answers "is a user's own clip of this song postable?" (FLE-16
§3-§4: YouTube Content ID does not monetise covers cut over a backing track, and
Instagram mutes retroactively). Nothing in the request path reads the column
yet — FLE-16's roster/check-in work is its first consumer. That makes it exactly
the kind of column that rots quietly: a row added later picks up the 'unknown'
DEFAULT and nobody notices until someone ships a share button that lies.

So the load-bearing test here is test_no_row_left_on_the_default. The others
protect the shape it depends on.

Covered:
- Schema: column exists, TEXT, NOT NULL, server_default 'unknown'.
- CHECK: rejects a value outside ALLOWED_RIGHTS_STATUSES, DB-side.
- Backfill completeness: zero rows sitting on the default, and the migration's
  map covers the live catalog exactly in both directions.
- The two named public_domain rows are tagged, and no others are.
- The §4 trap: Sweet Home Chicago must NOT be public_domain.
- ORM dual default, so an ORM insert never produces None on read.

Runs against the real Postgres test database (DATABASE_URL env var or default).
Requires `alembic upgrade head` to have been applied before running.
"""
import ast
import os
import pathlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Read the migration's own constants so the test cannot drift from the data.
# AST rather than import, for the reason spelled out in test_alembic_0007.py:
# conftest.py puts `server/` on sys.path[0], where the local `server/alembic/`
# package shadows the installed alembic distribution, so exec_module() on a
# migration raises ImportError under pytest.
_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0015_song_catalog_rights_status.py"
)
_WANTED = ("ALLOWED_RIGHTS_STATUSES", "_RIGHTS_STATUS")
_tree = ast.parse(_MIGRATION_PATH.read_text())
_consts = {
    node.targets[0].id: ast.literal_eval(node.value)
    for node in _tree.body
    if isinstance(node, ast.Assign)
    and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id in _WANTED
}
assert set(_consts) == set(_WANTED), (
    f"Migration 0015 no longer defines {set(_WANTED) - set(_consts)} as module-level literals"
)

ALLOWED_RIGHTS_STATUSES = set(_consts["ALLOWED_RIGHTS_STATUSES"])
RIGHTS_STATUS = _consts["_RIGHTS_STATUS"]

# The two CATALOG §4 names the migration is allowed to call public_domain.
# Hard-coded rather than derived from the map: deriving it would make the test
# agree with the migration by construction, which is the one thing it must not
# do. These two names trace to 260915-01-CATALOG.md §4, not to the migration.
EXPECTED_PUBLIC_DOMAIN = {"Canon in D", "Recuerdos de la Alhambra"}


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

    Cleanup is done by the autouse _release_transaction fixture below, NOT here —
    see test_alembic_0007.py's copy of this fixture for the full explanation.
    """
    async with SessionFactory() as session:
        yield session


@pytest.fixture(autouse=True)
async def _release_transaction(db):
    """Roll back after every test so no connection is checked out at session teardown.

    Same reasoning as test_alembic_0007.py: a session-scoped finalizer runs on a
    different event loop than the one asyncpg bound the connection to, so closing
    there raises 'Future attached to a different loop'. Rolling back inside the
    test's own loop leaves the session with nothing to close.
    """
    yield
    await db.rollback()


# ---------------------------------------------------------------------------
# 1. Schema
# ---------------------------------------------------------------------------

async def test_rights_status_column_shape(db: AsyncSession) -> None:
    """song_catalog.rights_status exists, is TEXT NOT NULL, defaults to 'unknown'."""
    result = await db.execute(
        text(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'song_catalog' AND column_name = 'rights_status'"
        )
    )
    row = result.mappings().one_or_none()
    assert row is not None, (
        "song_catalog.rights_status column missing — migration 0015 not applied?"
    )
    assert row["data_type"] == "text", f"Expected text, got {row['data_type']}"
    assert row["is_nullable"] == "NO", "rights_status must be NOT NULL"
    assert "unknown" in (row["column_default"] or ""), (
        f"rights_status server_default should be 'unknown', got {row['column_default']!r}"
    )


async def test_rights_status_check_constraint_rejects_unknown_value(db: AsyncSession) -> None:
    """The CHECK must reject a value outside ALLOWED_RIGHTS_STATUSES, DB-side.

    DB-side is the point: the roster work in FLE-16 may write this column from a
    script rather than through the ORM, so an application-layer validator alone
    would not hold.
    """
    try:
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "INSERT INTO song_catalog "
                    "(id, title, artist, genre, primary_skill_root, difficulty, rights_status) "
                    "VALUES (:id, 'Bogus Rights Probe', 'Test', 'Test', 'lead', 0.5, 'probably_fine')"
                ),
                {"id": str(uuid.uuid4())},
            )
            await db.flush()
    finally:
        await db.rollback()


async def test_check_constraint_is_named(db: AsyncSession) -> None:
    """The CHECK carries its explicit name, which downgrade() drops by name."""
    result = await db.execute(
        text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = 'song_catalog_rights_status_check' "
            "AND conrelid = 'song_catalog'::regclass"
        )
    )
    assert result.scalar() == 1, (
        "song_catalog_rights_status_check missing — downgrade() drops it by name, "
        "so an auto-generated name would make the downgrade a silent no-op"
    )


# ---------------------------------------------------------------------------
# 2. Backfill completeness — the assertions that actually protect the column
# ---------------------------------------------------------------------------

async def test_no_row_left_on_the_default(db: AsyncSession) -> None:
    """Zero rows sitting on the 'unknown' DEFAULT without being named in the map.

    This is the load-bearing test. The column's whole value is that someone
    looked at every row; a row carrying the DEFAULT is a row nobody looked at.

    Note the phrasing: this is NOT "zero rows are 'unknown'". The migration tags
    Black Mountain Side 'unknown' deliberately (CATALOG §4 records the Bert
    Jansch / "Down by Blackwaterside" provenance dispute). A reasoned 'unknown'
    is a curation decision; an inherited one is an oversight. Only the second is
    a failure, so the assertion is over rows MISSING FROM THE MAP, not over the
    value.
    """
    result = await db.execute(text("SELECT title FROM song_catalog ORDER BY title"))
    live_titles = [r[0] for r in result]

    untagged = [t for t in live_titles if t not in RIGHTS_STATUS]
    assert not untagged, (
        f"{len(untagged)} catalog row(s) are not named in migration 0015's "
        f"_RIGHTS_STATUS map and are therefore sitting on the 'unknown' DEFAULT: "
        f"{untagged}. Add them to the map — do not widen this assertion."
    )


async def test_map_has_no_rows_the_catalog_does_not(db: AsyncSession) -> None:
    """The map must not name songs that are not in the catalog.

    The other direction of the same contract. A stale map entry means a retitled
    or removed song silently stopped being tagged while the map still looks
    complete.
    """
    result = await db.execute(text("SELECT title FROM song_catalog"))
    live_titles = {r[0] for r in result}

    stale = sorted(set(RIGHTS_STATUS) - live_titles)
    assert not stale, (
        f"migration 0015's _RIGHTS_STATUS names {len(stale)} song(s) absent from "
        f"song_catalog: {stale}"
    )


async def test_every_live_value_matches_the_map(db: AsyncSession) -> None:
    """Every row's stored rights_status equals what the migration says it should be.

    Catches a hand-edit in prod that drifted from the curator record, and catches
    a backfill UPDATE whose WHERE clause quietly matched nothing.
    """
    result = await db.execute(
        text("SELECT title, rights_status FROM song_catalog ORDER BY title")
    )
    mismatches = [
        (title, actual, RIGHTS_STATUS[title])
        for title, actual in result
        if title in RIGHTS_STATUS and actual != RIGHTS_STATUS[title]
    ]
    assert not mismatches, (
        "song_catalog.rights_status disagrees with migration 0015's map "
        f"(title, in_db, expected): {mismatches}"
    )


async def test_every_live_value_is_legal(db: AsyncSession) -> None:
    """Belt-and-braces over the CHECK: no row holds a value outside the four."""
    result = await db.execute(
        text("SELECT DISTINCT rights_status FROM song_catalog ORDER BY rights_status")
    )
    live = {r[0] for r in result}
    assert live <= ALLOWED_RIGHTS_STATUSES, (
        f"Illegal rights_status value(s) in song_catalog: {live - ALLOWED_RIGHTS_STATUSES}"
    )


# ---------------------------------------------------------------------------
# 3. The specific judgments CATALOG §4 pins down
# ---------------------------------------------------------------------------

async def test_exactly_the_two_named_songs_are_public_domain(db: AsyncSession) -> None:
    """public_domain is exactly {Canon in D, Recuerdos de la Alhambra} — no more.

    CATALOG §4: "every composition in the catalog is under copyright except"
    these two. This test is the guard against a future curator inferring PD from
    age or feel and quietly widening the set; public_domain is the one value
    that could license a user to post, so it is the one that must be earned.
    """
    result = await db.execute(
        text("SELECT title FROM song_catalog WHERE rights_status = 'public_domain'")
    )
    actual = {r[0] for r in result}
    assert actual == EXPECTED_PUBLIC_DOMAIN, (
        f"public_domain set drifted from CATALOG §4. Expected "
        f"{sorted(EXPECTED_PUBLIC_DOMAIN)}, got {sorted(actual)}. "
        f"Adding a song here needs a dated authority in §4, not an age estimate."
    )


async def test_sweet_home_chicago_is_not_public_domain(db: AsyncSession) -> None:
    """CATALOG §4's named trap, asserted directly.

    Robert Johnson's 1937 publication does not enter the US public domain until
    2033. It is the most-assumed-PD song in the catalog and the specific reason
    this column is worth having, so it gets its own test rather than relying on
    the set-equality above to catch it.
    """
    result = await db.execute(
        text("SELECT rights_status FROM song_catalog WHERE title = 'Sweet Home Chicago'")
    )
    status = result.scalar_one()
    assert status == "copyrighted", (
        f"Sweet Home Chicago is tagged {status!r} — it is under copyright until 2033 "
        f"(CATALOG §4)."
    )


# ---------------------------------------------------------------------------
# 4. ORM parity
# ---------------------------------------------------------------------------

async def test_rights_status_orm_dual_default(db: AsyncSession) -> None:
    """An ORM insert that omits rights_status reads back 'unknown', not None.

    Mirrors test_alembic_0003.py::test_song_catalog_orm_dual_default. The
    Python-side `default=` is what makes this pass before flush/refresh; without
    it, serializing a freshly-created SongCatalog emits null for a NOT NULL
    column.
    """
    from app.models.db import SongCatalog

    try:
        row = SongCatalog(
            title="ORM Rights Default Probe",
            artist="Test",
            genre="Test",
            primary_skill_root="lead",
            difficulty=0.5,
        )
        db.add(row)
        await db.flush()
        assert row.rights_status == "unknown", (
            f"ORM default missing — got {row.rights_status!r}. Add `default='unknown'` "
            f"alongside the server_default on SongCatalog.rights_status."
        )
    finally:
        await db.rollback()
