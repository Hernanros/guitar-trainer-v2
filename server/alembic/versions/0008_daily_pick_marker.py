"""user_sessions: add is_daily_pick_marker + partial unique index for daily song persistence

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-17

FLE-54 Fix 1 — daily song pick is unstable because the seeded-random CTE assigns
random() values positionally (by scan order), not by row identity. Any non-HOT update
to an indexed column (e.g. adding/retitling a song) reorders the index and shifts which
song wins the same seed.

Fix: persist the first non-reroll pick of the day to user_sessions, mirroring the
existing reroll marker pattern. Subsequent GET /song-of-day calls for the same user+day
read the persisted row instead of rerunning the CTE.

Ordered steps:
1. Add is_daily_pick_marker BOOLEAN NOT NULL DEFAULT FALSE.
2. Drop + recreate uq_user_sessions_daily_rating to exclude daily-pick-marker rows
   from the rating uniqueness constraint (otherwise a user rating their daily song on
   the same day would collide with the marker's (user_id, song_id, day, -1) tuple).
3. Add uq_user_sessions_daily_pick partial unique index: (user_id, local_calendar_day)
   WHERE is_daily_pick_marker = true — one persisted daily pick per user per day.

downgrade() reverses in strict reverse order.
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 1: Add is_daily_pick_marker BOOLEAN NOT NULL DEFAULT FALSE.
    # Existing rows (reroll markers and rating rows) all get FALSE — correct.
    # ------------------------------------------------------------------
    op.add_column(
        "user_sessions",
        sa.Column(
            "is_daily_pick_marker",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # ------------------------------------------------------------------
    # Step 2: Drop + recreate uq_user_sessions_daily_rating.
    # Previous: (user_id, song_id, local_calendar_day, COALESCE(drill_index, -1))
    #           WHERE is_reroll_marker = false
    # New:      same key + WHERE is_reroll_marker = false AND is_daily_pick_marker = false
    #
    # Rationale: the daily pick marker has is_reroll_marker=false and rating=NULL (like
    # a whole-song slot, drill_index=NULL maps to COALESCE sentinel -1). Without the
    # extra clause, a user who rates their daily song on the same day would collide with
    # the marker on (user_id, song_id, day, -1) and get an IntegrityError.
    # ------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS uq_user_sessions_daily_rating")
    op.execute(
        "CREATE UNIQUE INDEX uq_user_sessions_daily_rating "
        "ON user_sessions (user_id, song_id, local_calendar_day, COALESCE(drill_index, -1)) "
        "WHERE is_reroll_marker = false AND is_daily_pick_marker = false"
    )

    # ------------------------------------------------------------------
    # Step 3: Partial unique index for daily pick idempotency.
    # One persisted daily pick per (user, day). A concurrent second GET on the same day
    # raises IntegrityError on insert → the handler ignores it and uses the already-
    # committed pick.
    # ------------------------------------------------------------------
    op.execute(
        "CREATE UNIQUE INDEX uq_user_sessions_daily_pick "
        "ON user_sessions (user_id, local_calendar_day) "
        "WHERE is_daily_pick_marker = true"
    )


def downgrade() -> None:
    # Reverse in strict reverse order.

    # Step 3 reverse: drop daily pick index
    op.execute("DROP INDEX IF EXISTS uq_user_sessions_daily_pick")

    # Step 2 reverse: drop new rating index, restore previous (from 0005)
    op.execute("DROP INDEX IF EXISTS uq_user_sessions_daily_rating")
    op.execute(
        "CREATE UNIQUE INDEX uq_user_sessions_daily_rating "
        "ON user_sessions (user_id, song_id, local_calendar_day, COALESCE(drill_index, -1)) "
        "WHERE is_reroll_marker = false"
    )

    # Step 1 reverse: drop column
    op.drop_column("user_sessions", "is_daily_pick_marker")
