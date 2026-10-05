"""song_catalog: repair missing rights_status column on prod

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-05

FLE-129 — forward repair for the schema-drift found on 2026-10-05.

WHY THIS EXISTS
---------------
Prod `song_catalog` is stamped `alembic_version = 0015` but does not have the
`rights_status` column that migration 0015 adds. The stamp was applied (or the
migration partially applied and the DDL failed) without committing the column
effect. Whatever happened, `alembic upgrade head` from 0015 runs 0016, 0017,
and 0018, and skips 0015 forever — so the column must be added by a new
migration rather than by re-running 0015.

WHY NOT STAMP-BACK-AND-RETRY
-----------------------------
0015's ALTER TABLE has no `IF NOT EXISTS`. On any environment where the column
*does* exist (dev, staging, any DB that ran 0015 cleanly), a rollback to 0014
and re-run would fail:

    ERROR: column "rights_status" of relation "song_catalog" already exists

A forward migration with `IF NOT EXISTS` is the only approach that is safe on
both prod (missing column) and every other environment (column already there).

WHAT THIS MIGRATION DOES
-------------------------
1. `ADD COLUMN IF NOT EXISTS` — no-op where 0015 already ran; adds the column
   where it is absent.
2. Adds the named CHECK constraint conditionally — skipped if a constraint
   named `song_catalog_rights_status_check` already exists (which it does on
   dev after a clean 0015 run).
3. Re-applies 0015's full row-tagging (all 64 rows stated explicitly). On dev,
   this is a no-op because the values are already correct; on prod step 1 has
   just put every row on the 'unknown' DEFAULT, and this restores the curated
   values 0015 was supposed to have written.

WHY THE MAP IS COPIED RATHER THAN IMPORTED
-------------------------------------------
This file carries its own frozen copy of the 64 titles instead of importing
0015's `_RIGHTS_STATUS`. A migration has to keep meaning what it meant on the
day it ran, and 0015's map is still being amended as the catalog grows
(FLE-130/FLE-131 adds five titles to it). Importing would make this migration's
effect change retroactively every time a curator edits 0015.

The cost of the copy is that it only covers the 64 rows prod actually has.
Verified read-only against prod on 2026-10-05, immediately before this
migration was written:

    alembic_version                 = 0015
    song_catalog rows               = 64   (64 distinct titles)
    live rows not named in this map = 0
    map titles not live in prod     = 0

So the repair leaves zero rows on the 'unknown' DEFAULT. Rows inserted *later*
than this migration are not this migration's business: 0019 sets rights_status
inline on its own INSERT, and test_alembic_0015.py::test_no_row_left_on_the_default
is the standing guard that catches any future row that forgets to.

IDEMPOTENT
----------
Each step is `IF NOT EXISTS` or a SET-to-the-same-value UPDATE, so re-running
this migration against any DB in any state is safe.

DOWNGRADE
---------
Downgrade does nothing, and that is deliberate. On dev the column and
constraint belong to 0015, so dropping them here would destroy state this
migration never created and leave 0015 stamped over a table missing its own
column — the very drift being repaired, manufactured in reverse. Since the
steps are conditional, this migration cannot know which environment it is on,
so it declines to guess. `downgrade 0014` still removes rights_status properly,
via 0015's own downgrade, which is where that responsibility lives.
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

ALLOWED_RIGHTS_STATUSES = (
    "public_domain",
    "traditional",
    "copyrighted",
    "unknown",
)

_RIGHTS_STATUS = {
    "Canon in D": "public_domain",
    "Recuerdos de la Alhambra": "public_domain",
    "La Bamba": "traditional",
    "Black Mountain Side": "unknown",
    "A Horse with No Name": "copyrighted",
    "Ain't No Sunshine": "copyrighted",
    "Autumn Leaves": "copyrighted",
    "Big Yellow Taxi": "copyrighted",
    "Blackbird": "copyrighted",
    "Blue Bossa": "copyrighted",
    "Chan Chan": "copyrighted",
    "Chop Suey!": "copyrighted",
    "Cissy Strut": "copyrighted",
    "Cliffs of Dover": "copyrighted",
    "Comfortably Numb": "copyrighted",
    "Crazy Train": "copyrighted",
    "Death Letter": "copyrighted",
    "Down with the Sickness": "copyrighted",
    "Dust in the Wind": "copyrighted",
    "Entre Dos Aguas": "copyrighted",
    "Eruption": "copyrighted",
    "Europa (Earth's Cry Heaven's Smile)": "copyrighted",
    "Everlong": "copyrighted",
    "Folsom Prison Blues": "copyrighted",
    "Giant Steps": "copyrighted",
    "Hey Joe": "copyrighted",
    "Honky Tonk Women": "copyrighted",
    "Hotel California": "copyrighted",
    "Jerry Was a Race Car Driver": "copyrighted",
    "Kashmir": "copyrighted",
    "Killing in the Name": "copyrighted",
    "Knockin' on Heaven's Door": "copyrighted",
    "Lenny": "copyrighted",
    "Little Wing": "copyrighted",
    "Manha de Carnaval": "copyrighted",
    "Message in a Bottle": "copyrighted",
    "No Woman, No Cry": "copyrighted",
    "Nothing Else Matters": "copyrighted",
    "Pride and Joy": "copyrighted",
    "Purple Haze": "copyrighted",
    "Redemption Song": "copyrighted",
    "Ring of Fire": "copyrighted",
    "Scuttle Buttin'": "copyrighted",
    "Seven Nation Army": "copyrighted",
    "Smoke on the Water": "copyrighted",
    "Statesboro Blues": "copyrighted",
    "Street Fighting Man": "copyrighted",
    "Sultans of Swing": "copyrighted",
    "Sunshine of Your Love": "copyrighted",
    "Superstition": "copyrighted",
    "Sweet Home Chicago": "copyrighted",
    "Take the 'A' Train": "copyrighted",
    "Tears Don't Fall": "copyrighted",
    "The Girl from Ipanema": "copyrighted",
    "Three Little Birds": "copyrighted",
    "Thunderstruck": "copyrighted",
    "Tornado of Souls": "copyrighted",
    "Under the Bridge": "copyrighted",
    "Voodoo Child (Slight Return)": "copyrighted",
    "Wagon Wheel": "copyrighted",
    "Windy and Warm": "copyrighted",
    "Wish You Were Here": "copyrighted",
    "Wonderwall": "copyrighted",
    "Zombie": "copyrighted",
}


def upgrade() -> None:
    # Step 1: add column if absent (no-op on dev where 0015 ran cleanly).
    # The DEFAULT is what makes this safe on a populated table: the 64 existing
    # prod rows land on 'unknown' rather than violating NOT NULL, and step 3
    # then overwrites every one of them with its curated value.
    op.execute(
        """
        ALTER TABLE song_catalog
          ADD COLUMN IF NOT EXISTS rights_status TEXT NOT NULL DEFAULT 'unknown'
        """
    )

    # Step 2: add the named CHECK constraint if it does not already exist.
    # pg_constraint lookup is safe here — we own the table and constraint name.
    # The name must match 0015's exactly: 0015's downgrade drops it by name, and
    # test_alembic_0015.py::test_check_constraint_is_named asserts it.
    allowed = ", ".join(f"'{s}'" for s in ALLOWED_RIGHTS_STATUSES)
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = 'song_catalog_rights_status_check'
               AND conrelid = 'song_catalog'::regclass
          ) THEN
            ALTER TABLE song_catalog
              ADD CONSTRAINT song_catalog_rights_status_check
              CHECK (rights_status IN ({allowed}));
          END IF;
        END;
        $$
        """
    )

    # Step 3: re-tag all rows. On dev, the values are already set so these
    # UPDATE statements touch zero rows. On prod the column just defaulted to
    # 'unknown' for every row and this restores the curated values from 0015.
    by_status: dict[str, list[str]] = {}
    for title, status in _RIGHTS_STATUS.items():
        by_status.setdefault(status, []).append(title)

    for status, titles in by_status.items():
        in_list = ", ".join("lower('{}')".format(t.replace("'", "''")) for t in titles)
        op.execute(
            f"UPDATE song_catalog SET rights_status = '{status}' "
            f"WHERE lower(title) IN ({in_list})"
        )


def downgrade() -> None:
    # Intentionally empty — see module docstring.
    pass
