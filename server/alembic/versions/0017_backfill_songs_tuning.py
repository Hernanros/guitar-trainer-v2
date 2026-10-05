"""songs.tuning: backfill from song_catalog for every row that joins

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-05

FLE-108. Migration 0014 added `songs.tuning` and documented it as "copied from
song_catalog.tuning at upsert time". Measured read-only against prod on
2026-10-05, with 0014 deployed:

    SELECT count(*), count(tuning) FROM songs;   -->   48, 0

Zero rows. The only writer 0014 shipped with was
`_ensure_catalog_song_as_user_song` (selectors/today_song.py), which covers the
catalog-pick path; the onboarding wizard (api/v1/users.py step 4) creates nearly
every `songs` row and never consulted the catalog. That code half is fixed in
this same change — this migration is for the rows that already exist.

WHAT IT TOUCHES
---------------
`tuning IS NULL` rows whose (lower(title), lower(artist)) matches a
`song_catalog` row exactly. In prod that is **39 of 48**:

    standard      22
    eb_standard   16
    dadgad         1

17 of those are non-standard, i.e. 17 rows where real ground truth existed and
NULL was passed to the generator anyway. The remaining 9 rows are genuinely
off catalog — Sweet Child O' Mine, Big Love x4, Cause We've Ended as Lovers,
Beat It, Layla x2 — and this migration deliberately leaves them NULL. After it
runs, `songs.tuning IS NULL` finally means what 0014 says it means: no catalog
ground truth exists for this song.

WHY NOT A LOOSER JOIN
---------------------
Exact lower(title) + lower(artist), the same key as
`songs_user_title_artist_uidx`. FLE-108 suspected a band-vs-member credit was
missing the join ("Big Love — Fleetwood Mac" against a Lindsey Buckingham row).
It is not: there is no Big Love row under either artist, and joining on title
alone across the whole table returns zero rows where the artists differ. A
fuzzy join here would be machinery for a case with no instances, and it can
match the wrong song. The five missing titles want catalog rows instead —
Junie's roster call, ground truth in 261005-fle125-tuning-ground-truth.md §6.

WHY NO DEFAULT AND NO NOT NULL
------------------------------
Defaulting the unmatched rows to 'standard' here would destroy the only signal
that distinguishes catalog ground truth from a chosen default, which is the
whole point of FLE-125 option 1. The *generator* defaults to standard
(FLE-128), records it as `tuning_source='default'`, and that stays a per-
generation fact rather than being written back as if the catalog had said so.

IDEMPOTENT
----------
`WHERE songs.tuning IS NULL` means re-running is a no-op, and the CHECK
constraint from 0014 guarantees anything copied out of song_catalog is in the
allowed set (both columns are constrained to the same list).

downgrade() sets the column back to NULL for rows that still agree with their
catalog row — see the note on downgrade() for why it is not a blanket NULL.
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


_BACKFILL = """
UPDATE songs s
   SET tuning = c.tuning
  FROM song_catalog c
 WHERE s.tuning IS NULL
   AND lower(s.title) = lower(c.title)
   AND lower(trim(s.artist)) = lower(trim(c.artist))
"""


def upgrade() -> None:
    op.execute(_BACKFILL)


def downgrade() -> None:
    # Only un-set rows that still match their catalog row's tuning. A row whose
    # value has since diverged was set by something other than this backfill
    # (a corrected catalog row, a manual fix), and blanking it would discard
    # ground truth this migration never wrote.
    op.execute(
        """
        UPDATE songs s
           SET tuning = NULL
          FROM song_catalog c
         WHERE s.tuning IS NOT NULL
           AND lower(s.title) = lower(c.title)
           AND lower(trim(s.artist)) = lower(trim(c.artist))
           AND s.tuning = c.tuning
        """
    )
