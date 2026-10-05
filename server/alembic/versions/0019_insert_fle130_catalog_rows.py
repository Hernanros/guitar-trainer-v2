"""song_catalog: insert the six FLE-130 rows Junie accepted

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-05

FLE-131 — the migration half of FLE-130. Junie's roster decision (FLE-130
comment, 2026-10-05) accepted all six candidate titles and supplied every
column; this migration does nothing but write them down.

Why this is a separate migration from 0018
------------------------------------------
Prod `song_catalog` is stamped alembic 0015 but does NOT have the
`rights_status` column 0015 adds (FLE-112/FLE-129, verified read-only
2026-10-05). An INSERT naming `rights_status` therefore raises UndefinedColumn
on prod until 0018's forward repair lands.

`down_revision = "0018"` is what makes that safe, and it is the whole reason
this is 0019 rather than part of 0018: alembic guarantees 0018 (add the column)
runs before 0019 (name it in an INSERT) on every database, in the one
`upgrade head` that FLE-124 runs. The ordering constraint is enforced by the
chain, not by remembering it.

The alternative that was explicitly rejected: omitting `rights_status` from the
INSERT so it works on prod today. That relies on a schema defect and leaves six
rows on the 'unknown' DEFAULT once the column appears — the exact failure 0015's
contract test exists to catch.

Why these rows are not just a tuning tag
----------------------------------------
A `song_catalog` row makes the song a song-of-day candidate for every user
subject to the difficulty gate (`ABS(sc.difficulty - pl.player_level) <= 0.15`
in selectors/today_song.py). So `difficulty` is load-bearing and is Junie's
call, not a default: the column's own DEFAULT is 0.0, which would not make a row
inert — it would serve it to players at level <= 0.15 and only those, i.e. to
day-one users. Every row below carries an explicit difficulty from FLE-130.

The immediate user-visible effect is *Sweet Child O' Mine*: FLE-128 made a
catalog miss default to `standard` rather than infer, which is right in general
but is knowingly wrong for this song (Appetite-era GN'R tuned down a half step).
This row at `eb_standard` is what stops that.

No backfill run is needed. `resolve_known_tuning` lazily backfills
`songs.tuning` from the catalog at request time, and `songs.tuning` (0014) is
already live on prod, so each row takes effect on the next tap for that song.

Companion edit, required for the test suite to pass
---------------------------------------------------
`test_alembic_0015.py::test_no_row_left_on_the_default` reads 0015's
`_RIGHTS_STATUS` map by AST and asserts every LIVE catalog title appears in it;
its sibling asserts the reverse. So the five new titles were added to that map
in the same change ("do not widen this assertion" — the map is the register of
rows a curator looked at, not a log of what 0015 wrote). Five titles, six rows:
both *Big Love* rows share one map key, which is only sound because they share
one rights_status — see the AMENDED note above that map.

0018's duplicated copy of the map is deliberately left at the original 64. It
exists to repair rows that 0015 failed to tag on prod; these six rows do not
exist when it runs, and 0019 tags them inline.
"""
from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


# (title, artist, genre, primary_skill_root, difficulty, tuning, rights_status)
#
# Verbatim from Junie's FLE-130 decision table. Tunings are Fret's FLE-125 §6
# ground-truth calls. Do not re-derive either half here: this migration is the
# transcription of a roster decision, and a value changed in passing at this
# layer is a decision made by whoever happened to be editing the SQL.
#
# Two *Big Love* rows, not an alias: the Fleetwood Mac credit is the *Tango in
# the Night* studio cut (textural/chordal in a full-band production) and the
# Lindsey Buckingham credit is the unaccompanied solo fingerstyle arrangement.
# Same composition, same tuning, genuinely different teaching vehicles — which
# is why they differ in primary_skill_root and difficulty rather than being
# deduplicated. This also resolves FLE-107's open "which arrangement do we
# teach" question by teaching both.
_ROWS: list[tuple[str, str, str, str, str, str, str]] = [
    ("Cause We've Ended as Lovers", "Jeff Beck",         "Jazz Fusion", "lead",           "0.800", "standard",    "copyrighted"),
    ("Big Love",                    "Fleetwood Mac",     "Pop Rock",    "chord_voicings", "0.550", "standard",    "copyrighted"),
    ("Big Love",                    "Lindsey Buckingham", "Pop Rock",   "fingerstyle",    "0.880", "standard",    "copyrighted"),
    ("Layla",                       "Eric Clapton",      "Blues Rock",  "lead",           "0.700", "standard",    "copyrighted"),
    ("Beat It",                     "Michael Jackson",   "Pop Rock",    "rhythm",         "0.480", "standard",    "copyrighted"),
    ("Sweet Child O' Mine",         "Guns N' Roses",     "Hard Rock",   "lead",           "0.580", "eb_standard", "copyrighted"),
]


def upgrade() -> None:
    values = ",\n            ".join(
        "(gen_random_uuid(), '{t}', '{a}', '{g}', '{s}', {d}, '{tn}', '{r}')".format(
            t=title.replace("'", "''"),
            a=artist.replace("'", "''"),
            g=genre.replace("'", "''"),
            s=skill,
            d=difficulty,
            tn=tuning,
            r=rights,
        )
        for title, artist, genre, skill, difficulty, tuning, rights in _ROWS
    )
    # ON CONFLICT targets song_catalog_title_artist_uidx (0007 step 2), which is
    # the only real conflict target on this table — the PK is gen_random_uuid()
    # and never conflicts, which is why 0003's bare ON CONFLICT DO NOTHING was
    # not actually idempotent. Every column is named explicitly rather than
    # relying on a DEFAULT, for `difficulty` the reason given in the docstring
    # and for `rights_status` because inheriting 'unknown' is the failure 0015
    # was built to prevent.
    op.execute(
        f"""
        INSERT INTO song_catalog
            (id, title, artist, genre, primary_skill_root, difficulty, tuning, rights_status)
        VALUES
            {values}
        ON CONFLICT (lower(title), lower(artist)) DO NOTHING
        """
    )


def downgrade() -> None:
    # Matched on (title, artist) rather than on id, so a row a curator added by
    # hand after this migration is never collateral damage — same reasoning as
    # 0007's downgrade. Both *Big Love* rows are addressed individually here,
    # which a title-only match could not do.
    pairs = ", ".join(
        "(lower('{t}'), lower('{a}'))".format(
            t=title.replace("'", "''"), a=artist.replace("'", "''")
        )
        for title, artist, *_rest in _ROWS
    )
    op.execute(
        f"DELETE FROM song_catalog WHERE (lower(title), lower(artist)) IN ({pairs})"
    )
