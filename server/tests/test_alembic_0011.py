"""Alembic migration 0011 tests — the FLE-4 §13 drill taxonomy columns.

FLE-13. The enforcement half of the contract at the top of
0011_drills_taxonomy_columns.py, plus the §10 coverage grading those columns exist
to make possible.

What is worth testing here, and why these and not "column X exists":

- THE THREE-WAY ENUM PIN. `technique_family`, `drill_tier` and `drill_status` are
  each written out three times — in the migration, in app/models/db.py, and (for
  family and tier) in app/sessions/taxonomy.py. That duplication is deliberate and
  documented in all three places, but it is only safe if drift is impossible. These
  tests are what makes it impossible.
- ck_drills_status_matches_canonical, in BOTH directions. It is the constraint that
  stops `status` drifting away from `canonical_drill_id` once code starts writing
  status directly, and a one-directional test would miss exactly that.
- ck_drills_tier_pairs_raw_score. A tier with no raw score behind it cannot be
  retuned, which defeats the entire point of §9.1 storing a score rather than a
  label.
- THE COVERAGE GATE ITSELF. §10.3 is a hard gate on the pilot, and before this
  migration it was not failing — it was UNMEASURABLE. The tests at the bottom pin
  that it now computes, that an empty bank fails it rather than vacuously passing,
  and that unclassified drills are reported as unclassified rather than silently
  counted as coverage.

Runs against the real Postgres test database. Requires `alembic upgrade head`.
"""
import ast
import os
import pathlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

# Read the migration's literals via AST rather than importing it: conftest.py puts
# server/ on sys.path, where the local `server/alembic/` package shadows the installed
# alembic distribution. Same technique as test_alembic_0007/0009.
_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0011_drills_taxonomy_columns.py"
)
_WANTED = ("TECHNIQUE_FAMILY", "DRILL_TIER", "DRILL_STATUS")
_tree = ast.parse(_MIGRATION_PATH.read_text())
MIGRATION_ENUMS = {
    node.targets[0].id: tuple(ast.literal_eval(node.value))
    for node in _tree.body
    if isinstance(node, ast.Assign)
    and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id in _WANTED
}
assert set(MIGRATION_ENUMS) == set(_WANTED), (
    f"Migration 0011 no longer defines {set(_WANTED) - set(MIGRATION_ENUMS)} "
    "as module-level literals"
)

from app.models.db import DrillStatus, DrillTier, TechniqueFamilyEnum  # noqa: E402
from app.sessions.coverage import (  # noqa: E402
    MIN_CELL_STOCK,
    grade,
    load_coverage,
    pilot_band_cells,
    viable_cells,
)
from app.sessions.taxonomy import (  # noqa: E402
    PILOT_BAND_CELLS,
    VIABLE_CELLS,
    TechniqueFamily,
    Tier,
)

# PG type name -> (migration literal, ORM enum)
ENUM_TRIPLES = [
    ("technique_family", "TECHNIQUE_FAMILY", TechniqueFamilyEnum),
    ("drill_tier", "DRILL_TIER", DrillTier),
    ("drill_status", "DRILL_STATUS", DrillStatus),
]


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
    """Per-test connection on NullPool with a synchronous teardown.

    Identical to test_alembic_0009's fixture and load-bearing for the same reasons —
    see that module's docstring. Nothing commits, so terminate-without-commit is this
    module's isolation.
    """
    engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)
    conn = await engine.connect()

    yield conn

    conn.sync_connection.connection.dbapi_connection.driver_connection.terminate()
    engine.sync_engine.dispose(close=False)


# ---------------------------------------------------------------------------
# Row builders. All INSERTs are rolled back by the fixture.
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


async def _make_drill(
    db,
    user_id: uuid.UUID,
    skill_node_id: uuid.UUID,
    *,
    family: str | None = None,
    tier: str | None = None,
    tier_raw_score: int | None = None,
    status: str | None = None,
    canonical_drill_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert one drill. `status` defaults to the value the CHECK constraint allows."""
    did = uuid.uuid4()
    if status is None:
        status = "duplicate" if canonical_drill_id is not None else "active"
    if tier is not None and tier_raw_score is None:
        tier_raw_score = 8
    await db.execute(
        text(
            "INSERT INTO drills (id, user_id, name, name_normalized, skill_node_id, "
            "canonical_drill_id, song_specific, what, tab_snippet, start_bpm, "
            "target_bpm, repetitions, success_criterion, family, tier, "
            "tier_raw_score, status) "
            "VALUES (:id, :uid, :name, :norm, :node, :canon, false, 'what', "
            "'{}'::jsonb, 60, 100, 12, 'clean', CAST(:family AS technique_family), "
            "CAST(:tier AS drill_tier), :raw, CAST(:status AS drill_status))"
        ),
        {
            "id": did,
            "uid": user_id,
            "name": f"drill-{did.hex[:8]}",
            "norm": f"drill{did.hex[:8]}",
            "node": skill_node_id,
            "canon": canonical_drill_id,
            "family": family,
            "tier": tier,
            "raw": tier_raw_score,
            "status": status,
        },
    )
    return did


# ---------------------------------------------------------------------------
# The three-way enum pin
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "pg_type,literal_name,orm_enum", ENUM_TRIPLES, ids=[t[0] for t in ENUM_TRIPLES]
)
async def test_pg_enum_matches_migration_literal_and_orm(db, pg_type, literal_name, orm_enum):
    """The type in the DB, the tuple in the migration, and the ORM enum are one set.

    Order is not asserted — Postgres enum ordering is a property of the type, and
    nothing in §8/§9/§13 depends on it — but membership is exact in both directions.
    """
    rows = await db.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = :name"
        ),
        {"name": pg_type},
    )
    in_db = {r.enumlabel for r in rows}
    assert in_db, f"type {pg_type} does not exist — is the DB migrated to 0011?"
    assert in_db == set(MIGRATION_ENUMS[literal_name])
    assert in_db == {e.value for e in orm_enum}


def test_family_and_tier_match_the_pure_taxonomy_module():
    """The fourth copy: app/sessions/taxonomy.py, which the session core imports.

    taxonomy.py deliberately imports no SQLAlchemy, so it cannot share an enum with
    db.py. This is the assertion that keeps the split honest.
    """
    assert set(MIGRATION_ENUMS["TECHNIQUE_FAMILY"]) == {f.value for f in TechniqueFamily}
    assert set(MIGRATION_ENUMS["DRILL_TIER"]) == {t.value for t in Tier}


# ---------------------------------------------------------------------------
# Column shape
# ---------------------------------------------------------------------------

async def test_taxonomy_columns_exist_with_the_right_nullability(db):
    """family/tier/tier_raw_score nullable, status NOT NULL.

    The nullability IS the design: NULL means "not yet classified" and the backfill
    fills it later, but a row with no status would be a row that cannot be filtered
    on, and every read path filters on status.
    """
    rows = await db.execute(
        text(
            "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'drills' AND column_name IN "
            "('family', 'tier', 'tier_raw_score', 'status')"
        )
    )
    cols = {r.column_name: r for r in rows}
    assert set(cols) == {"family", "tier", "tier_raw_score", "status"}
    assert cols["family"].is_nullable == "YES"
    assert cols["tier"].is_nullable == "YES"
    assert cols["tier_raw_score"].is_nullable == "YES"
    assert cols["status"].is_nullable == "NO"
    assert "active" in (cols["status"].column_default or "")


async def test_a_new_drill_defaults_to_active(db):
    """New rows are selectable without the writer having to know about §13."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    did = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO drills (id, user_id, name, name_normalized, skill_node_id, "
            "song_specific, what, tab_snippet, start_bpm, target_bpm, repetitions, "
            "success_criterion) VALUES (:id, :uid, 'n', 'n', :node, false, 'w', "
            "'{}'::jsonb, 60, 100, 12, 'c')"
        ),
        {"id": did, "uid": uid, "node": node},
    )
    row = (
        await db.execute(
            text("SELECT status::text AS status FROM drills WHERE id = :id"), {"id": did}
        )
    ).one()
    assert row.status == "active"


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

async def test_a_duplicate_row_must_say_it_is_a_duplicate(db):
    """canonical_drill_id set + status 'active' is rejected.

    This is the direction that matters most: without it, a deduped drill stays in the
    selectable pool and competes with the canonical it was folded into.
    """
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    canonical = await _make_drill(db, uid, node)
    with pytest.raises(IntegrityError, match="ck_drills_status_matches_canonical"):
        await _make_drill(db, uid, node, canonical_drill_id=canonical, status="active")


async def test_status_duplicate_requires_a_canonical(db):
    """And the reverse: 'duplicate' with nothing to be a duplicate OF is rejected."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    with pytest.raises(IntegrityError, match="ck_drills_status_matches_canonical"):
        await _make_drill(db, uid, node, status="duplicate")


async def test_retired_and_pending_review_need_no_canonical(db):
    """The other two states are orthogonal to dedup and must stay insertable."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    for status in ("retired", "pending_review"):
        assert await _make_drill(db, uid, node, status=status)


async def test_tier_and_raw_score_are_both_or_neither(db):
    """§9.1's retune path needs the score; a bare tier cannot be recomputed.

    Written as an UPDATE rather than through _make_drill, because that helper pairs
    the two for callers on purpose — going through it would test the helper's
    convenience, not the constraint. An UPDATE is also the realistic way this
    constraint gets hit: the backfill writes tier and tier_raw_score together, and
    a future retune that forgot the score would arrive exactly like this.
    """
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    did = await _make_drill(db, uid, node, tier="D3", tier_raw_score=8)

    with pytest.raises(IntegrityError, match="ck_drills_tier_pairs_raw_score"):
        await db.execute(
            text("UPDATE drills SET tier_raw_score = NULL WHERE id = :id"), {"id": did}
        )


async def test_clearing_both_tier_columns_together_is_allowed(db):
    """The other side of the pairing: un-tiering a row is legitimate.

    A §9.1 retune that widens the rubric may want to blank tiers and re-derive them,
    and the constraint must not stand in the way of that.
    """
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    did = await _make_drill(db, uid, node, tier="D3", tier_raw_score=8)

    await db.execute(
        text("UPDATE drills SET tier = NULL, tier_raw_score = NULL WHERE id = :id"),
        {"id": did},
    )
    row = (
        await db.execute(
            text("SELECT tier::text AS tier FROM drills WHERE id = :id"), {"id": did}
        )
    ).one()
    assert row.tier is None


async def test_raw_score_outside_the_rubric_range_is_rejected(db):
    """§9.1 sums five 0-4 buckets. 21 means the rubric changed shape."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    with pytest.raises(IntegrityError, match="ck_drills_tier_raw_score_range"):
        await _make_drill(db, uid, node, tier="D5", tier_raw_score=21)


# ---------------------------------------------------------------------------
# §10 coverage — what the columns were added FOR
# ---------------------------------------------------------------------------

def test_the_grid_matches_the_spec_totals():
    """60 viable cells, 45 in the pilot band. The denominators of every §10 report."""
    assert len(viable_cells()) == VIABLE_CELLS == 60
    assert len(pilot_band_cells()) == PILOT_BAND_CELLS == 45


async def test_cell_stock_counts_active_classified_drills(db):
    """The §10 primitive that did not exist before this migration."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    for _ in range(3):
        await _make_drill(db, uid, node, family="L1", tier="D3")

    report = await load_coverage(db, user_id=uid)
    assert report.stock_of(TechniqueFamily.L1, Tier.D3) == 3
    assert report.stock_of(TechniqueFamily.L1, Tier.D2) == 0


async def test_duplicates_and_retired_drills_do_not_stock_a_cell(db):
    """§10 grades what is SELECTABLE — the same predicate select.py filters on.

    Counting a collapsed duplicate would count one drill twice, and counting a
    retired one would claim coverage the session engine will never serve.
    """
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    canonical = await _make_drill(db, uid, node, family="C2", tier="D3")
    await _make_drill(db, uid, node, family="C2", tier="D3", canonical_drill_id=canonical)
    await _make_drill(db, uid, node, family="C2", tier="D3", status="retired")
    await _make_drill(db, uid, node, family="C2", tier="D3", status="pending_review")

    report = await load_coverage(db, user_id=uid)
    assert report.stock_of(TechniqueFamily.C2, Tier.D3) == 1


async def test_an_unclassified_drill_stocks_nothing_and_is_reported_as_such(db):
    """The §10.1 safety property, in one test.

    A NULL-family drill must not quietly vanish from the report — "we are 45 cells
    short" and "we are 45 cells short because 300 drills are unclassified" have
    opposite remedies, and a bare cell count cannot distinguish them.
    """
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    await _make_drill(db, uid, node, family=None, tier=None)
    await _make_drill(db, uid, node, family="R2", tier=None)

    report = await load_coverage(db, user_id=uid)
    assert report.classified_active == 0
    assert report.total_active == 2
    assert report.unclassified_family == 1
    assert report.unclassified_tier == 2


async def test_an_empty_bank_fails_the_pilot_gate_rather_than_passing_vacuously(db):
    """§10.3 is a claim about STOCK. No stock is not enough stock."""
    uid = await _make_user(db)
    report = await load_coverage(db, user_id=uid)
    assert report.pilot_gate_passes is False
    assert len(report.thin_cells(pilot_band_cells())) == PILOT_BAND_CELLS


async def test_one_drill_in_a_cell_is_still_thin(db):
    """MIN_CELL_STOCK is 2: one drill in a cell means the selector has no choice."""
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    await _make_drill(db, uid, node, family="L1", tier="D3")

    report = await load_coverage(db, user_id=uid)
    assert MIN_CELL_STOCK == 2
    assert (TechniqueFamily.L1, Tier.D3) in report.thin_cells(pilot_band_cells())
    assert (TechniqueFamily.L1, Tier.D3) not in report.empty_cells(pilot_band_cells())


async def test_grade_names_classification_as_the_blocker_when_it_is_one(db):
    """A failing gate with unclassified rows is a backfill problem, not a content one.

    Those call for opposite actions — run the classifier vs. generate more drills —
    so the report has to distinguish them.
    """
    uid = await _make_user(db)
    node = await _make_skill_node(db, uid)
    await _make_drill(db, uid, node, family=None, tier=None)

    report = grade(await load_coverage(db, user_id=uid))
    assert report["pilot_gate_passes"] is False
    assert report["blocked_by_unclassified"] is True
    assert report["unclassified_family"] == 1


async def test_thin_cells_are_returned_in_a_stable_order(db):
    """§10.4's generation queue reads this. A worklist that reshuffles is not a queue."""
    uid = await _make_user(db)
    report = await load_coverage(db, user_id=uid)
    first = report.thin_cells(pilot_band_cells())
    second = report.thin_cells(pilot_band_cells())
    assert first == second
    assert list(first) == sorted(first, key=lambda c: (list(TechniqueFamily).index(c[0]), c[1].ordinal))
