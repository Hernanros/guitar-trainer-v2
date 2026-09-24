"""drills: the four FLE-4 §13 taxonomy columns — family, tier, tier_raw_score, status

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-24

FLE-13. Migration 0006 shipped the drill bank without §13's taxonomy columns, so
`drills` records WHAT a drill is made of but not WHERE IT SITS in the §8/§9 grid.
The expensive consequence is §10.3: the pilot gate is "every one of the 45
pilot-band cells at cell_stock >= 2", a cell is a (family, tier) pair, and with no
family column there are no cells. The gate is currently unmeasurable — not failing,
unmeasurable. That is what this migration fixes.

WHAT IS BACKFILLED HERE, AND WHAT IS NOT
----------------------------------------
`status` is backfilled IN THIS MIGRATION, in SQL, because it is a pure restatement
of a column already present: canonical_drill_id IS NULL is today's 'active' and
non-NULL is 'duplicate'. That is exactly what app/ai/drill_dedupe.py writes and what
app/sessions/select.py already reads as its proxy, so the backfill cannot disagree
with live behaviour.

`family`, `tier` and `tier_raw_score` are NOT backfilled here. Both need Python:
tier comes from app.sessions.taxonomy.compute_tier(), which parses tab_snippet, and
family comes from app.sessions.family_classify, which keyword-matches prose. Neither
is expressible in SQL, and importing app code into a migration would couple the
deploy-time schema path to the application package — the precise coupling FLE-68
just finished untangling. They are backfilled by `scripts/backfill_drill_taxonomy.py`,
which is idempotent, dry-run by default, and re-runnable when the rubric is retuned.

So the columns land NULLABLE, and NULL means "not yet classified" rather than
"has no family". app/sessions/coverage.py counts NULL-family drills as stocking NO
cell and reports them separately, which is the §10.1-safe reading: an unclassified
drill undercounts a cell, it never fabricates one.

`status` is the exception — NOT NULL with a server_default of 'active', because
every existing row has a defined answer and new rows must not be able to omit it.

INDEXES
-------
ix_drills_coverage is the §10.3 gate's query: count active canonical drills grouped
by (family, tier). It is partial on status='active' because retired and duplicate
rows must never count toward cell stock — §10.1 grades what is SELECTABLE, and
select.py's SELECTABLE filter is the same predicate.

downgrade() drops the columns then the types, in that order.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


# §8 — the 18 families. Mirrors app.sessions.taxonomy.TechniqueFamily;
# test_alembic_0011.py pins the two copies to each other so they cannot drift.
TECHNIQUE_FAMILY = (
    "R1", "R2", "R3",
    "L1", "L2", "L3", "L4",
    "C1", "C2", "C3", "C4",
    "F1", "F2", "F3",
    "T1", "T2",
    "M1", "M2",
)
# §9 — five tiers on the [0,1] mastery scale. Mirrors taxonomy.Tier.
DRILL_TIER = ("D1", "D2", "D3", "D4", "D5")
# §13 — the bank's lifecycle states.
#   active          selectable; the only state that counts toward §10 coverage
#   duplicate       collapsed onto a canonical by dedup (canonical_drill_id set)
#   retired         deliberately withdrawn; kept for history, never served
#   pending_review  written but not yet trusted — the escape hatch for a drill a
#                   future quality gate flags without wanting to delete it
DRILL_STATUS = ("active", "duplicate", "retired", "pending_review")

_NEW_ENUMS = {
    "technique_family": TECHNIQUE_FAMILY,
    "drill_tier": DRILL_TIER,
    "drill_status": DRILL_STATUS,
}


def _enum(name: str) -> postgresql.ENUM:
    """Reference a type the raw SQL below created — never re-create it.

    Same pattern as 0009's _enum and the ORM's create_type=False: letting add_column
    emit CREATE TYPE would leave the type behind on a failed partial upgrade.
    """
    return postgresql.ENUM(*_NEW_ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    # -----------------------------------------------------------------
    # Step 1: the three enum types, idempotently — a re-run after a partial
    # failure must not trip over a type that already exists.
    # -----------------------------------------------------------------
    for name, values in _NEW_ENUMS.items():
        rendered = ", ".join(f"'{v}'" for v in values)
        op.execute(
            f"DO $$ BEGIN "
            f"CREATE TYPE {name} AS ENUM ({rendered}); "
            f"EXCEPTION WHEN duplicate_object THEN NULL; "
            f"END $$;"
        )

    # -----------------------------------------------------------------
    # Step 2: the four columns.
    # -----------------------------------------------------------------
    op.add_column(
        "drills",
        sa.Column(
            "family",
            _enum("technique_family"),
            nullable=True,
            comment="§8 technique family — which CELL of the bank this drill stocks. "
            "Distinct from skill_node_id, which says whose weakness it serves. "
            "NULL = not yet classified (app.sessions.family_classify declined); such "
            "a drill stocks no cell and is reported separately by coverage.py. NULL "
            "never means 'has no family'.",
        ),
    )
    op.add_column(
        "drills",
        sa.Column(
            "tier",
            _enum("drill_tier"),
            nullable=True,
            comment="§9 difficulty tier, D1-D5. Computed by "
            "app.sessions.taxonomy.compute_tier() from tab_snippet + the bpm ladder, "
            "then STORED so retuning the §9.1 rubric is one UPDATE rather than a "
            "re-read of every tab. NULL until the backfill script runs.",
        ),
    )
    op.add_column(
        "drills",
        sa.Column(
            "tier_raw_score",
            sa.Integer(),
            nullable=True,
            comment="§9.1 raw 0-20 feature sum behind `tier`. Stored so the rubric's "
            "CUTOFFS can be retuned without re-deriving the features — that is the "
            "whole reason §9.1 is a score and not just a label.",
        ),
    )
    op.add_column(
        "drills",
        sa.Column(
            "status",
            _enum("drill_status"),
            nullable=False,
            server_default=sa.text("'active'"),
            comment="§13 lifecycle. Only 'active' counts toward §10 coverage and is "
            "selectable. Backfilled from canonical_drill_id by this migration.",
        ),
    )

    # -----------------------------------------------------------------
    # Step 3: backfill `status` from the column that already encodes it.
    #
    # The server_default already made every row 'active'; this is the one
    # statement that separates out the collapsed duplicates.
    # -----------------------------------------------------------------
    op.execute(
        "UPDATE drills SET status = 'duplicate' WHERE canonical_drill_id IS NOT NULL"
    )

    # -----------------------------------------------------------------
    # Step 4: constraints and the coverage index.
    # -----------------------------------------------------------------
    # §9.1 sums five 0-4 buckets, so the raw score is bounded. A value outside
    # [0, 20] means the rubric changed shape and this CHECK should be revisited
    # deliberately rather than discovered through a nonsense coverage report.
    op.create_check_constraint(
        "ck_drills_tier_raw_score_range",
        "drills",
        "tier_raw_score IS NULL OR (tier_raw_score >= 0 AND tier_raw_score <= 20)",
    )
    # tier and tier_raw_score are two halves of one computation: a tier with no score
    # behind it cannot be re-tuned, and a score with no tier is not a classification.
    # Either both are present or neither is.
    op.create_check_constraint(
        "ck_drills_tier_pairs_raw_score",
        "drills",
        "(tier IS NULL) = (tier_raw_score IS NULL)",
    )
    # A duplicate is exactly a row with a canonical, in both directions. This is the
    # constraint that keeps `status` from drifting away from the column it was
    # derived from once code starts writing status directly.
    op.create_check_constraint(
        "ck_drills_status_matches_canonical",
        "drills",
        "(status = 'duplicate') = (canonical_drill_id IS NOT NULL)",
    )

    # The §10.3 gate's read: cell_stock per (family, tier) over selectable rows.
    op.execute(
        "CREATE INDEX ix_drills_coverage ON drills (family, tier) "
        "WHERE status = 'active' AND family IS NOT NULL AND tier IS NOT NULL"
    )
    # §10.4's gap-directed generation asks the inverse question — "which of MY rows
    # are still unclassified?" — and that is a per-user scan.
    op.execute(
        "CREATE INDEX ix_drills_unclassified ON drills (user_id) "
        "WHERE family IS NULL OR tier IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_drills_unclassified", table_name="drills")
    op.drop_index("ix_drills_coverage", table_name="drills")
    op.drop_constraint("ck_drills_status_matches_canonical", "drills", type_="check")
    op.drop_constraint("ck_drills_tier_pairs_raw_score", "drills", type_="check")
    op.drop_constraint("ck_drills_tier_raw_score_range", "drills", type_="check")

    op.drop_column("drills", "status")
    op.drop_column("drills", "tier_raw_score")
    op.drop_column("drills", "tier")
    op.drop_column("drills", "family")

    # Types drop last — every column referencing them is gone by now.
    for name in _NEW_ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")
