"""Song catalog, user sessions, breakdown_generated_at column

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-29

Ordered steps:
1. Create primary_skill_root enum (rhythm, lead, chord_voicings, fingerstyle, music_theory, timing)
2. Create rating_level enum (not_my_tempo, getting_closer, thats_what_im_looking_for)
3. Create song_catalog table with primary_skill_root + difficulty numeric(4,3)
4. Seed 10 hand-curated catalog songs
5. Create user_sessions table with rating_level enum + bank_source + is_reroll_marker
6. Create partial-unique index uq_user_sessions_daily_reroll (WHERE is_reroll_marker = true)
7. Create partial-unique index uq_user_sessions_daily_rating (WHERE is_reroll_marker = false) — Revision C
8. Add songs.breakdown_generated_at nullable timestamptz
9. Add songs_user_title_artist_uidx unique index on songs(user_id, lower(title), lower(artist)) — Revision F

downgrade() reverses in strict reverse order.
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # Step 1: Create primary_skill_root enum via raw SQL.
    # Raw SQL avoids SQLAlchemy re-emitting CREATE TYPE when op.add_column
    # processes an sa.Enum column that SQLAlchemy hasn't seen in this session.
    # -----------------------------------------------------------------
    op.execute(
        "CREATE TYPE primary_skill_root AS ENUM "
        "('rhythm', 'lead', 'chord_voicings', 'fingerstyle', 'music_theory', 'timing')"
    )

    # -----------------------------------------------------------------
    # Step 2: Create rating_level enum via raw SQL.
    # -----------------------------------------------------------------
    op.execute(
        "CREATE TYPE rating_level AS ENUM "
        "('not_my_tempo', 'getting_closer', 'thats_what_im_looking_for')"
    )

    # -----------------------------------------------------------------
    # Step 3: Create song_catalog table.
    # difficulty CHECK (difficulty BETWEEN 0 AND 1) enforces the [0,1] range.
    # DEFAULT 0.0 is the server_default; D-03 difficulty filter uses this column.
    # No foreign key to users — this is global seed data readable by all users.
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE song_catalog (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            genre TEXT NOT NULL,
            primary_skill_root primary_skill_root NOT NULL,
            difficulty NUMERIC(4,3) NOT NULL DEFAULT 0.0
                CHECK (difficulty BETWEEN 0 AND 1),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # -----------------------------------------------------------------
    # Step 4: Seed 10 hand-curated catalog songs.
    # ON CONFLICT DO NOTHING makes the seed idempotent on re-run.
    # Difficulty anchors: 0.20 = beginner, 0.50 = intermediate, 0.80 = advanced.
    # Fine-grained intra-tier tuning reflects curator judgment.
    #
    # Curator notes:
    #   Sweet Home Chicago (0.30): Accessible blues shuffle; good first working_on song.
    #   Little Wing (0.65): Chord-melody hybrid requires solid chord voicing control.
    #   Blackbird (0.50): Fingerstyle benchmark — alternating bass + melody.
    #   Wonderwall (0.20): Open chord strumming; lowest barrier, still musically rewarding.
    #   Comfortably Numb (solo) (0.70): Pentatonic lead benchmark with bend control.
    #   Purple Haze (0.75): Fast lead + whammy; technically demanding.
    #   Hotel California (0.65): Arpeggiated lead intro; right-hand precision.
    #   Wish You Were Here (0.40): Simple fingerpicking + recognizable chord sequence.
    #   Eruption (0.90): Van Halen two-hand tap; top of the difficulty scale.
    #   Thunderstruck (0.55): Fast alternate-picked rhythm; stamina + precision.
    # -----------------------------------------------------------------
    op.execute("""
        INSERT INTO song_catalog (id, title, artist, genre, primary_skill_root, difficulty)
        VALUES
            (gen_random_uuid(), 'Sweet Home Chicago', 'Robert Johnson', 'Blues', 'rhythm', 0.30),
            (gen_random_uuid(), 'Little Wing', 'Jimi Hendrix', 'Rock', 'chord_voicings', 0.65),
            (gen_random_uuid(), 'Blackbird', 'The Beatles', 'Folk', 'fingerstyle', 0.50),
            (gen_random_uuid(), 'Wonderwall', 'Oasis', 'Rock', 'chord_voicings', 0.20),
            (gen_random_uuid(), 'Comfortably Numb', 'Pink Floyd', 'Rock', 'lead', 0.70),
            (gen_random_uuid(), 'Purple Haze', 'Jimi Hendrix', 'Rock', 'lead', 0.75),
            (gen_random_uuid(), 'Hotel California', 'Eagles', 'Rock', 'lead', 0.65),
            (gen_random_uuid(), 'Wish You Were Here', 'Pink Floyd', 'Rock', 'fingerstyle', 0.40),
            (gen_random_uuid(), 'Eruption', 'Van Halen', 'Rock', 'lead', 0.90),
            (gen_random_uuid(), 'Thunderstruck', 'AC/DC', 'Rock', 'rhythm', 0.55)
        ON CONFLICT DO NOTHING
    """)

    # -----------------------------------------------------------------
    # Step 5: Create user_sessions table.
    #
    # Design decisions:
    #   - rating is NULLABLE because reroll markers (is_reroll_marker=true) have no rating.
    #   - bank_source (Revision B): persists which bank branch produced a reroll pick.
    #     NULL for rating rows; 'user_bench' or 'seed_catalog' for reroll marker rows.
    #   - is_reroll_marker: false = real rating row, true = reroll marker (D-05).
    #   - tz_offset_minutes CHECK enforces the same [-840, 840] range as get_tz_offset_minutes dep.
    #   - NO plain UNIQUE (user_id, song_id, local_calendar_day) — replaced by two partial-unique
    #     indexes in steps 6/7 so a reroll marker AND a rating can coexist for the same
    #     (user, song, day) (Revision C).
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE user_sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id),
            song_id INTEGER NOT NULL REFERENCES songs(id),
            rating rating_level NULL,
            local_calendar_day DATE NOT NULL,
            tz_offset_minutes INTEGER NOT NULL
                CHECK (tz_offset_minutes BETWEEN -840 AND 840),
            rated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            is_reroll_marker BOOLEAN NOT NULL DEFAULT false,
            bank_source VARCHAR(16) NULL
        )
    """)

    # -----------------------------------------------------------------
    # Step 6: Partial-unique index for reroll idempotency (one reroll per user per day).
    # WHERE is_reroll_marker = true so only reroll markers count.
    # A second concurrent POST /today-song/reroll raises IntegrityError → 409.
    # -----------------------------------------------------------------
    op.execute(
        "CREATE UNIQUE INDEX uq_user_sessions_daily_reroll "
        "ON user_sessions (user_id, local_calendar_day) "
        "WHERE is_reroll_marker = true"
    )

    # -----------------------------------------------------------------
    # Step 7 (Revision C): Partial-unique index for rating idempotency.
    # WHERE is_reroll_marker = false so only rating rows are constrained.
    # This allows a reroll marker AND a rating to coexist for the same (user, song, day).
    # A second rating for the same (user_id, song_id, local_calendar_day) raises IntegrityError → 409.
    # -----------------------------------------------------------------
    op.execute(
        "CREATE UNIQUE INDEX uq_user_sessions_daily_rating "
        "ON user_sessions (user_id, song_id, local_calendar_day) "
        "WHERE is_reroll_marker = false"
    )

    # -----------------------------------------------------------------
    # Step 8: Add songs.breakdown_generated_at nullable timestamptz.
    # NULL = "no breakdown generated yet" (cache miss signal for Phase 3 lazy fetch).
    # No server_default — the null itself is the signal (per RESEARCH anti-patterns).
    # -----------------------------------------------------------------
    op.add_column(
        "songs",
        sa.Column("breakdown_generated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # -----------------------------------------------------------------
    # Step 9 (Revision F): Unique index on songs(user_id, lower(title), lower(artist)).
    # Required for _ensure_catalog_song_as_user_song's ON CONFLICT DO NOTHING path.
    # Case-insensitive so "Blackbird" and "blackbird" are treated as the same song.
    # IF NOT EXISTS makes it safe to re-run (idempotent).
    # -----------------------------------------------------------------
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS songs_user_title_artist_uidx "
        "ON songs (user_id, lower(title), lower(artist))"
    )


def downgrade() -> None:
    # Reverse in strict reverse order of upgrade steps.

    # Step 9 reverse (Revision F): drop songs unique index
    op.execute("DROP INDEX IF EXISTS songs_user_title_artist_uidx")

    # Step 8 reverse: drop songs.breakdown_generated_at
    op.drop_column("songs", "breakdown_generated_at")

    # Step 7 reverse (Revision C): drop partial-unique rating index
    op.execute("DROP INDEX IF EXISTS uq_user_sessions_daily_rating")

    # Step 6 reverse: drop partial-unique reroll index
    op.execute("DROP INDEX IF EXISTS uq_user_sessions_daily_reroll")

    # Step 5 reverse: drop user_sessions table (drops FKs + check constraints with it)
    op.drop_table("user_sessions")

    # Step 2 reverse: drop rating_level enum type
    op.execute("DROP TYPE IF EXISTS rating_level")

    # Step 3 reverse: drop song_catalog table
    op.drop_table("song_catalog")

    # Step 1 reverse: drop primary_skill_root enum type
    op.execute("DROP TYPE IF EXISTS primary_skill_root")
