"""Alembic migration 0020 tests — the FLE-101 §2 drill → section link.

Three columns and one CHECK. The columns themselves are not worth a test; what is
worth testing is the contract the rest of the feature rests on:

- ck_drills_section_link_complete, IN BOTH DIRECTIONS AND FOR EACH COLUMN.
  A half-written link is the one state the §3 predicate cannot answer honestly:
  `prepares_section` alone declares a section is required for graduation while the
  beat range that would let the player SEE the passage is missing, so the reward
  unlocks onto nothing renderable. A test that only checked "all three present is
  OK" would miss exactly the partial writes the constraint exists to stop, so each
  of the three partial shapes is asserted separately.

- THE COLUMNS ARE NULLABLE WITH NO DEFAULT. Every drill banked before this
  migration has no link, and design §5 reads a NULL link as the implicit,
  already-cleared whole-song section — i.e. pre-FLE-101 behaviour preserved
  exactly. A DEFAULT would put an invented passage name on every existing row, and
  §3 gates a reward on these values. `test_an_insert_without_the_link_is_the_normal_case`
  is the regression test for someone later "tidying up" by adding one.

- THE ORM AND THE MIGRATION AGREE ON THE WIDTH. prepares_section is String(80) in
  both; the ORM silently truncating or the DB rejecting at a different boundary is
  the kind of drift that only shows up on a long disambiguated label.

Runs against the real Postgres test database. Requires `alembic upgrade head`.
"""
import os
import pathlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.models.db import Drill

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0020_drills_section_link.py"
)
_SRC = _MIGRATION_PATH.read_text()


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
    """Per-test connection on NullPool, terminated without commit. Same as 0011's."""
    engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)
    conn = await engine.connect()

    yield conn

    conn.sync_connection.connection.dbapi_connection.driver_connection.terminate()
    engine.sync_engine.dispose(close=False)


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


async def _insert_drill(db, user_id, node_id, *, label, start, end) -> uuid.UUID:
    did = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO drills (id, user_id, name, name_normalized, skill_node_id, "
            "    song_specific, what, tab_snippet, start_bpm, target_bpm, "
            "    repetitions, success_criterion, prepares_section, "
            "    section_start_beat, section_end_beat) "
            "VALUES (:id, :uid, :name, :norm, :node, false, 'what', '{}'::jsonb, "
            "    60, 100, 12, 'clean', :label, :start, :end)"
        ),
        {
            "id": did, "uid": user_id, "name": f"drill-{did.hex[:8]}",
            "norm": f"drill{did.hex[:8]}", "node": node_id,
            "label": label, "start": start, "end": end,
        },
    )
    return did


# ---------------------------------------------------------------------------
# ck_drills_section_link_complete
# ---------------------------------------------------------------------------

async def test_a_complete_link_is_accepted(db):
    uid = await _make_user(db)
    nid = await _make_skill_node(db, uid)
    did = await _insert_drill(db, uid, nid, label="Solo", start=8, end=15)
    row = (
        await db.execute(
            text(
                "SELECT prepares_section, section_start_beat, section_end_beat "
                "FROM drills WHERE id = :id"
            ),
            {"id": did},
        )
    ).one()
    assert tuple(row) == ("Solo", 8, 15)


async def test_no_link_at_all_is_accepted(db):
    """§5 — the shape every drill banked before FLE-101 has."""
    uid = await _make_user(db)
    nid = await _make_skill_node(db, uid)
    did = await _insert_drill(db, uid, nid, label=None, start=None, end=None)
    assert did is not None


@pytest.mark.parametrize(
    "label,start,end,missing",
    [
        ("Solo", None, None, "both beats"),
        ("Solo", 8, None, "section_end_beat"),
        ("Solo", None, 15, "section_start_beat"),
        (None, 8, 15, "prepares_section"),
        (None, 8, None, "prepares_section and section_end_beat"),
    ],
)
async def test_a_partial_link_is_rejected(db, label, start, end, missing):
    """Each partial shape separately — a one-directional test would miss most."""
    uid = await _make_user(db)
    nid = await _make_skill_node(db, uid)
    with pytest.raises((IntegrityError, DBAPIError)) as exc:
        await _insert_drill(db, uid, nid, label=label, start=start, end=end)
    assert "ck_drills_section_link_complete" in str(exc.value), (
        f"a link missing {missing} must be refused by the named constraint"
    )


# ---------------------------------------------------------------------------
# Nullability, defaults, and width
# ---------------------------------------------------------------------------

async def test_the_three_columns_are_nullable_with_no_default(db):
    """A DEFAULT would invent a passage name on every pre-FLE-101 row (see §5)."""
    rows = (
        await db.execute(
            text(
                "SELECT column_name, is_nullable, column_default, "
                "       character_maximum_length "
                "  FROM information_schema.columns "
                " WHERE table_name = 'drills' "
                "   AND column_name IN ('prepares_section', 'section_start_beat', "
                "                       'section_end_beat')"
            )
        )
    ).all()
    assert len(rows) == 3, "migration 0020 did not add all three columns"
    for name, nullable, default, width in rows:
        assert nullable == "YES", f"{name} must be nullable"
        assert default is None, f"{name} must have no DEFAULT"
        if name == "prepares_section":
            assert width == 80


def test_the_orm_and_the_migration_agree_on_the_label_width():
    assert Drill.__table__.c.prepares_section.type.length == 80
    assert "sa.String(length=80)" in _SRC


async def test_the_partial_index_exists_and_excludes_unlinked_rows(db):
    """The §3 predicate's query. Partial because NULL rows are never looked at."""
    indexdef = (
        await db.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                " WHERE tablename = 'drills' AND indexname = 'ix_drills_section_link'"
            )
        )
    ).scalar_one()
    assert "origin_song_id" in indexdef and "prepares_section" in indexdef
    assert "WHERE (prepares_section IS NOT NULL)" in indexdef


def test_downgrade_removes_everything_it_added():
    for dropped in (
        'op.drop_index("ix_drills_section_link"',
        'op.drop_constraint("ck_drills_section_link_complete"',
        'op.drop_column("drills", "section_end_beat")',
        'op.drop_column("drills", "section_start_beat")',
        'op.drop_column("drills", "prepares_section")',
    ):
        assert dropped in _SRC, f"downgrade() never does: {dropped}"
