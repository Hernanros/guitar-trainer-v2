"""song_catalog: add tuning column, dedupe index, and expand 10 -> 64 curated songs

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-15

ORDERING NOTE: this was authored as 0006 and renumbered to 0007 after
0006_drills_bank.py (FLE-8 Task 3) claimed that slot in the same working
directory. Nothing here depends on the drills-bank tables — the chain is
sequencing only — but 0006 MUST land on main before or with this migration,
or `alembic upgrade head` breaks on a missing down_revision.

FLE-6 Task 8 — Expand the song catalog from 10 to ~60.

Why this exists
---------------
The 10-song seed from migration 0003 is one player's taste (9 of 10 are Rock or
Blues; every one is in standard or Eb tuning; none is Jazz, Country, Funk,
Classical, Latin, Reggae or Metal). The session generator draws from
song_catalog whenever the bank branch fires, so a thin catalog means a user
runs out of material they care about — content failure that reads as product
failure. Author bias is a named risk in the FLE-1 plan, so this expansion
deliberately carries material outside the curator's own taste.

Ordered steps:
0. Add song_catalog.breakdown JSONB NULL — ORM/DB drift fix, see below.
1. Add song_catalog.tuning TEXT NOT NULL DEFAULT 'standard' with a CHECK over
   the 8 tunings the breakdown SYSTEM_PROMPT already knows about.
2. Add a case-insensitive unique index on (title, artist) so the seed is
   genuinely idempotent and a curator cannot double-insert a song (a duplicate
   row would silently skew the selector's uniform random draw).
3. Backfill tuning on the 10 rows seeded by migration 0003 (the DEFAULT covers
   the column-add, but 4 of those 10 are NOT standard tuning).
4. Insert 54 new curated rows -> 64 total.

downgrade() reverses in strict reverse order.

Step 0 — pre-existing ORM/DB drift, fixed here because this migration is where
the drift becomes load-bearing:
  models/db.py's SongCatalog has declared `breakdown: Mapped[Optional[dict]]`
  since Phase 3, but migration 0003's CREATE TABLE never added the column and no
  later migration did either. Any ORM read of SongCatalog therefore raises
  UndefinedColumnError: column song_catalog.breakdown does not exist — which is
  why tests/test_alembic_0003.py::test_song_catalog_orm_dual_default has been
  failing. Nothing in the request path hit it because selectors/today_song.py
  queries song_catalog through raw SQL with an explicit column list, never the
  ORM. Adding the column (rather than dropping the ORM attribute) matches the
  declared intent and is the reversible direction.

Tagging contract (the session generator reads these — sloppy tags become bad
sessions, so each field has one meaning and one only):

  difficulty  NUMERIC(4,3) in [0,1]. This is the SELECTOR's axis, not a vibe:
              selectors/today_song.py matches ABS(difficulty - player_level)
              <= 0.15, where player_level = AVG(mastery) over the user's leaf
              skill nodes. So difficulty means "the leaf-mastery level at which
              this song is the right stretch", NOT "how impressive it sounds".
              Anchors: 0.20 beginner, 0.50 intermediate, 0.80 advanced.
              Values are spread rather than bucketed so every 0.15-wide window
              from 0.15 to 0.95 has candidates (see 260915-01-CATALOG.md §2).

  primary_skill_root  The ONE skill root the song is the best vehicle for.
              Not "every skill it touches" — the generator reads it as the
              song's teaching purpose. Hotel California is tagged `lead` for
              its arpeggiated intro even though it is also a chord workout.

  tuning      The song's CANONICAL recorded tuning, not a simplified
              standard-tuning translation. The .planning/investigations/
              260908-sonnet-tuning-quality.md incident (Sonnet silently
              re-voiced Lenny into standard EADGBE) is exactly what this
              column exists to make checkable: it is curator ground truth to
              validate the emitted tab.tuning against.
              NOTE: `tuning` is not yet plumbed into the Sonnet breakdown
              prompt — Sonnet still infers tuning on its own. Propagating this
              tag through songs -> the prompt is tracked as follow-up work;
              today the column is ground truth for curation and eval, and
              coverage data for the generator's material spread.

  genre       Free text, deliberately fine-grained (`Texas Blues` not `Blues`)
              so the spread table shows real variety rather than three buckets.

Licensing: every composition here is under copyright except the two noted in
260915-01-CATALOG.md §4. That section flags the entries whose rights-holders
have a documented history of enforcement against tab/lyric distribution, for
the v3 licensing task. No song was excluded on licensing grounds at this stage
— the flags are input to that decision, not a substitute for it.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


# The 8 tunings. Matches the alt-tuning table in the breakdown SYSTEM_PROMPT
# (server/app/ai/breakdown.py) plus `eb_standard` — half-step-down standard,
# which that table omits but which covers Hendrix, SRV, Van Halen and much of
# hard rock, and which is the single most common non-standard tuning in this
# catalog. A CHECK constraint rather than a PG enum: alt tunings are an
# open-ended set and a curator adding `open_c` should be a one-line constraint
# swap, not an ALTER TYPE dance.
ALLOWED_TUNINGS = (
    "standard",     # E A D G B e
    "eb_standard",  # Eb Ab Db Gb Bb eb — standard, half step down
    "drop_d",       # D A D G B e
    "drop_c",       # C G C F A d
    "open_d",       # D A D F# A d
    "open_e",       # E B E G# B e
    "open_g",       # D G D G B d
    "dadgad",       # D A D G A d
)

# Backfill for the 10 rows seeded by migration 0003. The column DEFAULT is
# 'standard', so only the non-standard ones need an explicit UPDATE — but all
# 10 are listed here so the curator record is complete and reviewable in one
# place rather than split between "stated" and "implied by the default".
#
#   Little Wing / Purple Haze  — Hendrix tuned down a half step near-universally.
#   Eruption                   — Van Halen's Eb, the reason the solo sits where it does.
#   Sweet Home Chicago         — standard; the common teaching arrangement in E.
#   Wonderwall                 — standard tuning with a capo at 2 (capo != tuning).
#   Hotel California           — standard tuning with a capo at 7 (capo != tuning).
_SEED_0003_TUNINGS = {
    "Sweet Home Chicago": "standard",
    "Little Wing": "eb_standard",
    "Blackbird": "standard",
    "Wonderwall": "standard",
    "Comfortably Numb": "standard",
    "Purple Haze": "eb_standard",
    "Hotel California": "standard",
    "Wish You Were Here": "standard",
    "Eruption": "eb_standard",
    "Thunderstruck": "standard",
}

# ---------------------------------------------------------------------------
# The 54 new entries: (title, artist, genre, primary_skill_root, difficulty, tuning)
#
# Ordered by difficulty so the spread is auditable by reading the file, and so
# a gap in the selector's ±0.15 window is visible as a jump in this column
# rather than something you have to run a query to notice.
# ---------------------------------------------------------------------------
_NEW_SONGS = [
    # --- Beginner: 0.15-0.30 -------------------------------------------------
    ("Knockin' on Heaven's Door", "Bob Dylan", "Folk Rock", "chord_voicings", 0.15, "standard"),
    ("Smoke on the Water", "Deep Purple", "Hard Rock", "rhythm", 0.18, "standard"),
    ("A Horse with No Name", "America", "Folk Rock", "chord_voicings", 0.19, "standard"),
    ("Seven Nation Army", "The White Stripes", "Garage Rock", "lead", 0.20, "standard"),
    ("La Bamba", "Ritchie Valens", "Latin Rock", "rhythm", 0.22, "standard"),
    ("Three Little Birds", "Bob Marley & The Wailers", "Reggae", "rhythm", 0.23, "standard"),
    ("Ring of Fire", "Johnny Cash", "Country", "rhythm", 0.26, "standard"),
    ("Zombie", "The Cranberries", "Alternative Rock", "chord_voicings", 0.28, "standard"),
    ("Redemption Song", "Bob Marley & The Wailers", "Reggae", "fingerstyle", 0.30, "standard"),
    # --- Lower intermediate: 0.32-0.47 --------------------------------------
    ("Folsom Prison Blues", "Johnny Cash", "Country", "rhythm", 0.32, "standard"),
    ("Wagon Wheel", "Old Crow Medicine Show", "Americana", "rhythm", 0.34, "standard"),
    ("Ain't No Sunshine", "Bill Withers", "Soul", "rhythm", 0.36, "standard"),
    ("No Woman, No Cry", "Bob Marley & The Wailers", "Reggae", "chord_voicings", 0.37, "standard"),
    ("Dust in the Wind", "Kansas", "Folk Rock", "fingerstyle", 0.38, "standard"),
    ("Big Yellow Taxi", "Joni Mitchell", "Folk", "rhythm", 0.39, "open_e"),
    ("Sunshine of Your Love", "Cream", "Blues Rock", "lead", 0.40, "standard"),
    ("Chan Chan", "Buena Vista Social Club", "Cuban Son", "rhythm", 0.41, "standard"),
    ("Everlong", "Foo Fighters", "Alternative Rock", "rhythm", 0.42, "drop_d"),
    ("Canon in D", "Johann Pachelbel", "Classical", "music_theory", 0.43, "standard"),
    ("Hey Joe", "Jimi Hendrix", "Psychedelic Rock", "chord_voicings", 0.44, "eb_standard"),
    ("Killing in the Name", "Rage Against the Machine", "Funk Metal", "rhythm", 0.45, "drop_d"),
    ("Autumn Leaves", "Joseph Kosma", "Jazz", "music_theory", 0.47, "standard"),
    # --- Intermediate: 0.48-0.60 --------------------------------------------
    ("Down with the Sickness", "Disturbed", "Nu Metal", "rhythm", 0.48, "drop_c"),
    ("Cissy Strut", "The Meters", "Funk", "timing", 0.50, "standard"),
    ("Nothing Else Matters", "Metallica", "Heavy Metal", "fingerstyle", 0.51, "standard"),
    ("Honky Tonk Women", "The Rolling Stones", "Rock", "rhythm", 0.52, "open_g"),
    ("Under the Bridge", "Red Hot Chili Peppers", "Alternative Rock", "chord_voicings", 0.53, "standard"),
    ("Street Fighting Man", "The Rolling Stones", "Rock", "rhythm", 0.55, "open_d"),
    ("Take the 'A' Train", "Billy Strayhorn", "Jazz", "music_theory", 0.55, "standard"),
    ("Superstition", "Stevie Wonder", "Funk", "timing", 0.56, "standard"),
    ("Message in a Bottle", "The Police", "New Wave", "chord_voicings", 0.57, "standard"),
    ("Blue Bossa", "Kenny Dorham", "Jazz", "music_theory", 0.58, "standard"),
    ("Chop Suey!", "System of a Down", "Nu Metal", "timing", 0.59, "drop_c"),
    ("The Girl from Ipanema", "Antonio Carlos Jobim", "Bossa Nova", "chord_voicings", 0.60, "standard"),
    # --- Upper intermediate: 0.61-0.78 --------------------------------------
    ("Kashmir", "Led Zeppelin", "Hard Rock", "timing", 0.61, "dadgad"),
    ("Pride and Joy", "Stevie Ray Vaughan", "Texas Blues", "rhythm", 0.64, "eb_standard"),
    ("Tears Don't Fall", "Bullet for My Valentine", "Metalcore", "lead", 0.66, "drop_c"),
    ("Death Letter", "Son House", "Delta Blues", "rhythm", 0.67, "open_g"),
    ("Europa (Earth's Cry Heaven's Smile)", "Santana", "Latin Rock", "lead", 0.69, "standard"),
    ("Manha de Carnaval", "Luiz Bonfa", "Bossa Nova", "fingerstyle", 0.70, "standard"),
    ("Voodoo Child (Slight Return)", "Jimi Hendrix", "Blues Rock", "rhythm", 0.71, "eb_standard"),
    ("Crazy Train", "Ozzy Osbourne", "Heavy Metal", "lead", 0.72, "eb_standard"),
    ("Statesboro Blues", "The Allman Brothers Band", "Blues Rock", "lead", 0.73, "open_e"),
    ("Sultans of Swing", "Dire Straits", "Rock", "lead", 0.74, "standard"),
    ("Windy and Warm", "Chet Atkins", "Country", "fingerstyle", 0.76, "standard"),
    ("Lenny", "Stevie Ray Vaughan", "Texas Blues", "chord_voicings", 0.77, "eb_standard"),
    ("Black Mountain Side", "Led Zeppelin", "Folk Rock", "fingerstyle", 0.78, "dadgad"),
    # --- Advanced: 0.82-0.93 -------------------------------------------------
    ("Jerry Was a Race Car Driver", "Primus", "Funk Metal", "timing", 0.82, "drop_d"),
    ("Scuttle Buttin'", "Stevie Ray Vaughan", "Texas Blues", "lead", 0.84, "eb_standard"),
    ("Cliffs of Dover", "Eric Johnson", "Instrumental Rock", "lead", 0.86, "standard"),
    ("Entre Dos Aguas", "Paco de Lucia", "Flamenco", "fingerstyle", 0.88, "standard"),
    ("Recuerdos de la Alhambra", "Francisco Tarrega", "Classical", "fingerstyle", 0.90, "standard"),
    ("Tornado of Souls", "Megadeth", "Thrash Metal", "lead", 0.93, "standard"),
    ("Giant Steps", "John Coltrane", "Jazz", "music_theory", 0.95, "standard"),
]


def upgrade() -> None:
    # -----------------------------------------------------------------
    # Step 0: ORM/DB drift fix — song_catalog.breakdown.
    # Declared on the SongCatalog ORM model since Phase 3, never created by any
    # migration. IF NOT EXISTS so this is a no-op on any database where it was
    # added out-of-band. Nullable with no default: NULL means "no breakdown
    # cached for this catalog entry", the same signal songs.breakdown uses.
    # -----------------------------------------------------------------
    op.execute("ALTER TABLE song_catalog ADD COLUMN IF NOT EXISTS breakdown JSONB NULL")

    # -----------------------------------------------------------------
    # Step 1: tuning column.
    # DEFAULT 'standard' so the ALTER is safe on a populated table without a
    # separate backfill pass for the NOT NULL; step 3 corrects the 4 rows the
    # default is wrong for.
    # -----------------------------------------------------------------
    allowed = ", ".join(f"'{t}'" for t in ALLOWED_TUNINGS)
    op.execute(
        f"""
        ALTER TABLE song_catalog
          ADD COLUMN tuning TEXT NOT NULL DEFAULT 'standard'
            CONSTRAINT song_catalog_tuning_check CHECK (tuning IN ({allowed}))
        """
    )

    # -----------------------------------------------------------------
    # Step 2: case-insensitive dedupe index on (title, artist).
    # Migration 0003's seed used bare `ON CONFLICT DO NOTHING`, which only
    # covers the PK — and the PK is gen_random_uuid(), which never conflicts.
    # So that seed was not actually idempotent. This index gives the inserts
    # below a real conflict target, and stops a future curator from
    # double-inserting a song. A duplicate row is not cosmetic here: the
    # selector's catalog_pick does ORDER BY random() LIMIT 1 over the matching
    # band, so a duplicated song is drawn twice as often as its neighbours.
    # -----------------------------------------------------------------
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS song_catalog_title_artist_uidx "
        "ON song_catalog (lower(title), lower(artist))"
    )

    # -----------------------------------------------------------------
    # Step 3: backfill tuning for the 10 rows from migration 0003.
    # Matched on title alone — all 10 titles are unique within the catalog
    # at this point (step 2's index would have failed the migration otherwise).
    # -----------------------------------------------------------------
    for title, tuning in _SEED_0003_TUNINGS.items():
        escaped = title.replace("'", "''")
        op.execute(
            f"UPDATE song_catalog SET tuning = '{tuning}' WHERE lower(title) = lower('{escaped}')"
        )

    # -----------------------------------------------------------------
    # Step 4: insert the 54 new curated rows.
    # ON CONFLICT on the step-2 index makes this re-runnable and makes an
    # accidental overlap with the 0003 seed a no-op rather than a duplicate.
    # -----------------------------------------------------------------
    values = ",\n            ".join(
        "(gen_random_uuid(), '{t}', '{a}', '{g}', '{s}', {d}, '{tn}')".format(
            t=title.replace("'", "''"),
            a=artist.replace("'", "''"),
            g=genre.replace("'", "''"),
            s=skill,
            d=difficulty,
            tn=tuning,
        )
        for title, artist, genre, skill, difficulty, tuning in _NEW_SONGS
    )
    op.execute(
        f"""
        INSERT INTO song_catalog (id, title, artist, genre, primary_skill_root, difficulty, tuning)
        VALUES
            {values}
        ON CONFLICT (lower(title), lower(artist)) DO NOTHING
        """
    )


def downgrade() -> None:
    # Reverse in strict reverse order of upgrade steps.

    # Step 4 reverse: delete the 54 rows this migration added, matched on
    # (title, artist) so a catalog row a curator added by hand after this
    # migration is never collateral damage.
    pairs = ", ".join(
        "(lower('{t}'), lower('{a}'))".format(
            t=title.replace("'", "''"), a=artist.replace("'", "''")
        )
        for title, artist, _g, _s, _d, _tn in _NEW_SONGS
    )
    op.execute(
        f"DELETE FROM song_catalog WHERE (lower(title), lower(artist)) IN ({pairs})"
    )

    # Step 3 reverse: no-op. The tuning backfill disappears with the column.

    # Step 2 reverse: drop the dedupe index.
    op.execute("DROP INDEX IF EXISTS song_catalog_title_artist_uidx")

    # Step 1 reverse: drop the tuning column (takes its CHECK constraint with it).
    op.execute("ALTER TABLE song_catalog DROP COLUMN IF EXISTS tuning")

    # Step 0 reverse: drop the drift-fix column. This restores the ORM/DB
    # mismatch it corrected — that is what "reverse" means here, and the
    # mismatch is exactly the state every database was in before 0007.
    op.execute("ALTER TABLE song_catalog DROP COLUMN IF EXISTS breakdown")
