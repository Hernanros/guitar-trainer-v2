"""Retune global seed drill tier_raw_score after FLE-75 ladder_stretch rescale.

FLE-75 rescaled _LADDER_STRETCH_BUCKETS in app/sessions/taxonomy.py against the
FLE-72 span cap (MAX_LADDER_SPAN_BPM = 15). With the new boundaries the feature
uses its full 0-4 range again, and every drill's raw score rises by +2 to +4.

The three global seed drills (user_id IS NULL) inserted by migration 0012 were
already correct in the migration source, but 0012 had already been applied on prod
with the pre-FLE-75 values. This migration applies the matching one-time UPDATE so
stored tier_raw_score agrees with what compute_tier() now returns.

The 12 per-user drills are handled separately by backfill_drill_taxonomy.py
--recompute-tier --apply, which re-runs compute_tier() end-to-end against tab_snippet
and family, and is run as part of deploying this change.

New scores (span = target_bpm - start_bpm):
  chromatic spider one string      span=30  ladder_stretch was bucket 2 → now 4  raw 3→5  tier D2 (unchanged)
  openchord cycle clean ring       span=25  ladder_stretch was bucket 2 → now 4  raw 10→12 tier D2 (unchanged, C1-clamped)
  one string four subdivisions     span=30  ladder_stretch was bucket 2 → now 4  raw 5→7  tier D2 (unchanged)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-04
"""

from alembic import op
from sqlalchemy import text

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text(
            """
            UPDATE drills
            SET tier_raw_score = CASE name_normalized
                WHEN 'chromatic spider one string'        THEN 5
                WHEN 'openchord cycle clean ring'         THEN 12
                WHEN 'one string four subdivisions'       THEN 7
            END
            WHERE user_id IS NULL
              AND name_normalized IN (
                  'chromatic spider one string',
                  'openchord cycle clean ring',
                  'one string four subdivisions'
              )
            """
        )
    )


def downgrade() -> None:
    op.execute(
        text(
            """
            UPDATE drills
            SET tier_raw_score = CASE name_normalized
                WHEN 'chromatic spider one string'        THEN 3
                WHEN 'openchord cycle clean ring'         THEN 10
                WHEN 'one string four subdivisions'       THEN 5
            END
            WHERE user_id IS NULL
              AND name_normalized IN (
                  'chromatic spider one string',
                  'openchord cycle clean ring',
                  'one string four subdivisions'
              )
            """
        )
    )
