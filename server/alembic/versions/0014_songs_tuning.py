"""songs: add nullable tuning column, carried from song_catalog on upsert

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-04

FLE-33. Migration 0007 tagged all 64 song_catalog rows with their canonical
recorded tuning, but nothing downstream of song_catalog ever read the column —
_ensure_catalog_song_as_user_song (selectors/today_song.py) upserts a catalog
pick into `songs` and drops the tuning tag on the floor. `songs` has no tuning
column to carry it into, so Sonnet has never been told a song's ground-truth
tuning and has always inferred it from the title (the path that produced the
Lenny incident — .planning/investigations/260908-sonnet-tuning-quality.md).

This migration only adds the column. The column is NULLABLE with no DEFAULT,
and that nullability is load-bearing, not an oversight:

  - NULL means "no catalog ground truth for this song" — true for every
    user-onboarded song (no song_catalog row to copy from) and for every
    `songs` row that existed before this migration. The breakdown prompt's
    fallback for NULL is unchanged: Sonnet still infers tuning from the title.
  - A non-NULL value means "copied from song_catalog.tuning at upsert time" —
    ground truth the breakdown prompt can state instead of ask, and the value
    the emitted tab.tuning is checked against.

So NULL vs non-NULL is exactly the "two paths should be distinguishable" seam
the issue asks for; no separate boolean or provenance column is needed.

The CHECK constraint mirrors song_catalog_tuning_check (migration 0007) so a
copied value can never drift from the set the breakdown SYSTEM_PROMPT and
TUNING_NOTE_MAP (app/ai/breakdown.py) know about. It allows NULL explicitly —
a plain `tuning IN (...)` CHECK would reject every NULL-write row under NOT
NULL semantics for CHECK, which Postgres evaluates as NULL (neither true nor
false) and therefore passes; stated explicitly here for the reader, not
because Postgres needs the OR arm.

downgrade() drops the column (and its CHECK constraint with it).
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


# Must stay a superset-equal mirror of ALLOWED_TUNINGS in
# 0007_song_catalog_tuning_and_expansion.py and TUNING_NOTE_MAP in
# app/ai/breakdown.py. All three are checked against each other by
# tests/test_alembic_0014.py.
ALLOWED_TUNINGS = (
    "standard",
    "eb_standard",
    "drop_d",
    "drop_c",
    "open_d",
    "open_e",
    "open_g",
    "dadgad",
)


def upgrade() -> None:
    allowed = ", ".join(f"'{t}'" for t in ALLOWED_TUNINGS)
    op.execute(
        f"""
        ALTER TABLE songs
          ADD COLUMN tuning TEXT NULL
            CONSTRAINT songs_tuning_check CHECK (tuning IS NULL OR tuning IN ({allowed}))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE songs DROP COLUMN IF EXISTS tuning")
