"""song_catalog: add rights_status column and tag all 64 curated rows

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-04

FLE-51 — backfill per-entry cover/clip-eligibility flags on song_catalog.

NUMBERING NOTE: the FLE-51 spec says "migration 0008". That number was claimed by
0008_daily_pick_marker.py long before this issue was written; the chain head at
authoring time is 0014. The spec's "0008" is a stale reference to the slot that
was next when the issue was filed, not a constraint. This is 0015.

Why this exists
---------------
FLE-16's v3 social-landscape research (§3-§4) found that YouTube Content ID is
the only major-platform cover mechanism that monetises rather than blocks — but
covers built over a commercial sound recording, instrumental, karaoke track or
concert audio are explicitly INELIGIBLE. That is exactly what this app produces:
someone playing along to a backing track. Unaccompanied playing is eligible;
playing along to the thing that makes practice work is not. Instagram is worse:
AI-assisted fingerprinting, enforcement by muting, and retroactive — a Reel can
lose its audio months after posting.

So before v3 ships any "share your clip" affordance, the roster/check-in work
(FLE-16) needs to know, per song, what a user is walking into. This column
records that.

What this column is NOT
-----------------------
It is NOT a duplicate of the licensing tiers in
.planning/quick/260915-01-expand-song-catalog/260915-01-CATALOG.md §4.

  §4 tiers COMPOSITION COPYRIGHT, ranked by how likely a rights-holder is to act
  against *v3 distributing generated tab*. Different actor (us), different
  artefact (tab), different forum.

  rights_status records whether *a user's own clip* is postable. A §4 "Tier 3 —
  standard commercial risk" song is still un-postable to Instagram.

Related inputs, not substitutes. Do not collapse them.

Name: `rights_status`, not `post_safe`
--------------------------------------
FLE-16 §4 offers the two as equivalent ("a single `post_safe` / `rights_status`
field populated during curation"). They are not. The value set has four members
and is not boolean, so `post_safe` would read as a flag and mislead every caller
that touches it. Taken as a decision by the catalog owner under the FLE-6
comment's "entirely your call whether it earns a column."

The four values come from FLE-6 comment e34732df (2026-09-15, Junie):
"Something as coarse as public_domain / traditional / copyrighted / unknown is
enough."

Column shape — follows the `tuning` precedent from 0007
-------------------------------------------------------
TEXT + CHECK rather than a PG enum, for the same reason 0007 gave for tunings:
the value set is expected to drift as v3 licensing gets priced, and a CHECK is
cheap to widen where an ALTER TYPE is not. Dual default mirrors 0007 as well
(`default=` for ORM inserts, `server_default=` for raw-SQL inserts) so the
column is never None on read.

Tagging contract
----------------
  public_domain  The underlying COMPOSITION is out of copyright outright.
                 Exactly 2 rows, both already named and dated in CATALOG §4.

  traditional    PD melody carried by a copyrighted ARRANGEMENT. The user's clip
                 is of the arrangement, so this is not a green light — it means
                 "the tune underneath is free, the version you learned is not."
                 Distinct from public_domain precisely so a future caller can
                 treat them differently without re-deriving the distinction.

  copyrighted    The default landing zone. CATALOG §4's own words: "every
                 composition in the catalog is under copyright except" the two
                 public_domain entries. Tiers 1/2/3 all land here — including
                 Tier 2's contested-OWNERSHIP songs, because a dispute over
                 *which* rights-holder owns a live copyright does not make the
                 clip any more postable. That is a licensing-counterparty
                 problem (FLE-84), not a rights_status one.

  unknown        Provenance is genuinely contested as to whether the underlying
                 composition is authored at all. Reserved, not a dumping ground.

DO NOT INFER public_domain FROM AGE OR FEEL. CATALOG §4 specifically catches
*Sweet Home Chicago*, widely assumed PD, actually under copyright until 2033.
That trap is the reason this column is worth having at all.

Scope boundary (FLE-51 comment d88a6aee): this migration is the backfill, not a
re-litigation of the licensing posture. The posture decision lives in FLE-84.
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


# The four values, from FLE-6 comment e34732df. Ordered most-free to least-known
# rather than alphabetically, so the CHECK reads as a scale.
ALLOWED_RIGHTS_STATUSES = (
    "public_domain",  # composition itself is out of copyright
    "traditional",    # PD melody, copyrighted arrangement
    "copyrighted",    # composition under copyright — the default landing zone
    "unknown",        # provenance genuinely contested
)

# The column DEFAULT below is 'unknown', which makes the ALTER safe on a
# populated table without a separate NOT NULL backfill pass. But a row left at
# the default is a row nobody looked at, and that is the exact failure this
# column exists to prevent. So all 64 titles are listed explicitly — including
# the ~60 that resolve to 'copyrighted' — and the contract test asserts the map
# covers the catalog exactly. "Stated" and "implied by the default" are not the
# same curator record.
#
# Keyed on title alone: 0007 step 2 added a case-insensitive unique index on
# (lower(title), lower(artist)), and all 64 titles are distinct within it, so
# title is a safe match key here for the same reason it was in 0007 step 3.
_RIGHTS_STATUS = {
    # --- public_domain (2) ---------------------------------------------------
    # Both named and dated in CATALOG §4 as "firmly public domain". These are the
    # only two §4 carves out, and this migration adds no others.
    "Canon in D": "public_domain",                 # Pachelbel, d. 1706
    "Recuerdos de la Alhambra": "public_domain",   # Tárrega, d. 1909

    # --- traditional (1) -----------------------------------------------------
    # La Bamba is the one unambiguous "PD melody, copyrighted arrangement" in the
    # catalog: a son jarocho from Veracruz in circulation long before Ritchie
    # Valens, whose 1958 arrangement — the thing anyone actually learns and the
    # thing a clip would be of — is under copyright. Tagged traditional rather
    # than copyrighted because the melody genuinely is free, and rather than
    # public_domain because the arrangement in the user's hands is not.
    "La Bamba": "traditional",

    # --- unknown (1) ---------------------------------------------------------
    # Black Mountain Side is the case CATALOG §4 flags as provenance-contested:
    # the Bert Jansch / "Down by Blackwaterside" dispute turns on whether the
    # piece is Page's composition at all or a traditional Irish tune he arranged
    # from Jansch's arrangement. That is a live question about whether an
    # authored composition exists underneath — unlike Hey Joe / Wagon Wheel /
    # Girl from Ipanema, where the dispute is over WHICH living author owns an
    # undisputedly-live copyright. Those three are copyrighted below.
    #
    # This is a deliberate, reasoned 'unknown', not an untouched default.
    "Black Mountain Side": "unknown",

    # --- copyrighted (60) ----------------------------------------------------
    # CATALOG §4: "every composition in the catalog is under copyright except"
    # the two public_domain rows above. Listed alphabetically.
    #
    # Includes every §4 Tier 1 entry (Blackbird; the four Hendrix titles; Hotel
    # California; Kashmir; Nothing Else Matters) and every Tier 2 entry (Hey Joe;
    # The Girl from Ipanema; Wagon Wheel). Tier membership changes how hard the
    # song is to LICENCE, not whether a user's clip is postable, so it does not
    # change the value here.
    "A Horse with No Name": "copyrighted",
    "Ain't No Sunshine": "copyrighted",
    "Autumn Leaves": "copyrighted",                # Kosma, 1945
    "Big Yellow Taxi": "copyrighted",
    "Blackbird": "copyrighted",                    # §4 Tier 1
    "Blue Bossa": "copyrighted",
    "Chan Chan": "copyrighted",                    # Compay Segundo, 1987 — composed, not folk
    "Chop Suey!": "copyrighted",
    "Cissy Strut": "copyrighted",
    "Cliffs of Dover": "copyrighted",
    "Comfortably Numb": "copyrighted",
    "Crazy Train": "copyrighted",
    "Death Letter": "copyrighted",                 # credited to Son House — blues idiom, but authored
    "Down with the Sickness": "copyrighted",
    "Dust in the Wind": "copyrighted",
    "Entre Dos Aguas": "copyrighted",              # Paco de Lucía, 1973 — a composed rumba
    "Eruption": "copyrighted",
    "Europa (Earth's Cry Heaven's Smile)": "copyrighted",
    "Everlong": "copyrighted",
    "Folsom Prison Blues": "copyrighted",
    "Giant Steps": "copyrighted",
    "Hey Joe": "copyrighted",                      # §4 Tier 1 + Tier 2 (Roberts vs. Valenti)
    "Honky Tonk Women": "copyrighted",
    "Hotel California": "copyrighted",             # §4 Tier 1
    "Jerry Was a Race Car Driver": "copyrighted",
    "Kashmir": "copyrighted",                      # §4 Tier 1
    "Killing in the Name": "copyrighted",
    "Knockin' on Heaven's Door": "copyrighted",
    "Lenny": "copyrighted",
    "Little Wing": "copyrighted",                  # §4 Tier 1
    "Manha de Carnaval": "copyrighted",            # Bonfá, 1959
    "Message in a Bottle": "copyrighted",
    "No Woman, No Cry": "copyrighted",
    "Nothing Else Matters": "copyrighted",         # §4 Tier 1
    "Pride and Joy": "copyrighted",
    "Purple Haze": "copyrighted",                  # §4 Tier 1
    "Redemption Song": "copyrighted",
    "Ring of Fire": "copyrighted",
    "Scuttle Buttin'": "copyrighted",
    "Seven Nation Army": "copyrighted",
    "Smoke on the Water": "copyrighted",
    "Statesboro Blues": "copyrighted",             # Blind Willie McTell's composition, not a folk tune
    "Street Fighting Man": "copyrighted",
    "Sultans of Swing": "copyrighted",
    "Sunshine of Your Love": "copyrighted",
    "Superstition": "copyrighted",
    "Sweet Home Chicago": "copyrighted",           # §4's named trap — PD only in 2033
    "Take the 'A' Train": "copyrighted",
    "Tears Don't Fall": "copyrighted",
    "The Girl from Ipanema": "copyrighted",        # §4 Tier 2 (Jobim/Moraes heirs)
    "Three Little Birds": "copyrighted",
    "Thunderstruck": "copyrighted",
    "Tornado of Souls": "copyrighted",
    "Under the Bridge": "copyrighted",
    "Voodoo Child (Slight Return)": "copyrighted", # §4 Tier 1
    "Wagon Wheel": "copyrighted",                  # §4 Tier 2 (Dylan/Secor)
    "Windy and Warm": "copyrighted",               # John D. Loudermilk, 1961
    "Wish You Were Here": "copyrighted",
    "Wonderwall": "copyrighted",
    "Zombie": "copyrighted",
}


def upgrade() -> None:
    # -----------------------------------------------------------------
    # Step 1: add the column.
    # DEFAULT 'unknown' so the ALTER is safe on a populated table without a
    # separate backfill pass for the NOT NULL; step 2 then states every row
    # explicitly, so nothing is left sitting on the default.
    # Named constraint (song_catalog_rights_status_check) so downgrade and the
    # contract test can both address it by name rather than by guessing at a
    # PostgreSQL-generated identifier.
    # -----------------------------------------------------------------
    allowed = ", ".join(f"'{s}'" for s in ALLOWED_RIGHTS_STATUSES)
    op.execute(
        f"""
        ALTER TABLE song_catalog
          ADD COLUMN rights_status TEXT NOT NULL DEFAULT 'unknown'
            CONSTRAINT song_catalog_rights_status_check
            CHECK (rights_status IN ({allowed}))
        """
    )

    # -----------------------------------------------------------------
    # Step 2: tag all 64 rows.
    # Grouped into one UPDATE per status rather than 64 single-row UPDATEs:
    # same curator record, four statements instead of sixty-four.
    # -----------------------------------------------------------------
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
    # Reverse order. Dropping the column drops its attached CHECK with it, but
    # the constraint is dropped explicitly first so the intent is readable and
    # so a partially-applied upgrade (column added, backfill failed) still
    # downgrades cleanly.
    op.execute(
        "ALTER TABLE song_catalog "
        "DROP CONSTRAINT IF EXISTS song_catalog_rights_status_check"
    )
    op.execute("ALTER TABLE song_catalog DROP COLUMN IF EXISTS rights_status")
