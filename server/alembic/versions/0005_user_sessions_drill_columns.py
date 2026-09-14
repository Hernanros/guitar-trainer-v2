"""user_sessions: drill_index + target_skill_node_id + recreated partial-unique index

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-14

Phase 4.1, Plan 04.1-02 — server per-drill rating write path.

Ordered steps:
1. op.add_column user_sessions.drill_index INTEGER NULL
2. op.add_column user_sessions.target_skill_node_id UUID NULL + FK to skill_nodes.id
3. DROP existing uq_user_sessions_daily_rating (from migration 0003)
4. CREATE UNIQUE INDEX uq_user_sessions_daily_rating on
   (user_id, song_id, local_calendar_day, COALESCE(drill_index, -1))
   WHERE is_reroll_marker = false
   Rationale: COALESCE(-1) treats a whole-song rating (drill_index=NULL) as its own
   "slot" that coexists with drill_index=0,1,2,3. This preserves the "one rating per
   scope per day" idempotency guarantee at the DB layer while allowing multiple drill
   ratings for the same song+day.
5. ADD CONSTRAINT ck_drill_index_pairs_skill_node CHECK enforcing both-or-neither
   on (drill_index, target_skill_node_id). Defense in depth alongside the Pydantic
   @model_validator on SessionCreate.

downgrade() reverses in strict reverse order:
  - DROP CHECK ck_drill_index_pairs_skill_node
  - DROP recreated uq_user_sessions_daily_rating (COALESCE variant)
  - CREATE original uq_user_sessions_daily_rating (from 0003, no drill_index in key)
  - drop_column target_skill_node_id (drops the FK constraint automatically)
  - drop_column drill_index
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # Step 1: Add drill_index INTEGER NULL
    # NULL = whole-song rating. INT = 0-based index into Breakdown.drills.
    # -----------------------------------------------------------------
    op.add_column(
        "user_sessions",
        sa.Column("drill_index", sa.Integer(), nullable=True),
    )

    # -----------------------------------------------------------------
    # Step 2: Add target_skill_node_id UUID NULL + FK to skill_nodes.id
    # NULL when drill_index is NULL. When set, points to the single
    # skill_node that this drill rating writes mastery to.
    # -----------------------------------------------------------------
    op.add_column(
        "user_sessions",
        sa.Column(
            "target_skill_node_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("skill_nodes.id"),
            nullable=True,
        ),
    )

    # -----------------------------------------------------------------
    # Step 3+4: Drop and recreate uq_user_sessions_daily_rating
    # Original (0003): (user_id, song_id, local_calendar_day) WHERE is_reroll_marker=false
    # New: (user_id, song_id, local_calendar_day, COALESCE(drill_index, -1)) WHERE is_reroll_marker=false
    #
    # COALESCE(drill_index, -1) rationale (RESEARCH.md §Q2):
    # - Postgres partial-unique indexes support expression indexes on COALESCE(col, sentinel).
    # - The sentinel -1 is safe because drill_index is 0-based (0..3 for 2-4 drills),
    #   so -1 can never collide with a real drill_index value.
    # - Multiple drill ratings for the same (user, song, day) succeed as long as their
    #   drill_index values differ; a whole-song rating (drill_index=NULL) coexists with
    #   drill ratings because -1 is its slot.
    # - Duplicate drill_index for the same (user, song, day) still 409s via IntegrityError.
    # - Duplicate whole-song ratings (both NULL, both mapping to -1) still 409.
    # -----------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS uq_user_sessions_daily_rating")
    op.execute(
        "CREATE UNIQUE INDEX uq_user_sessions_daily_rating "
        "ON user_sessions (user_id, song_id, local_calendar_day, COALESCE(drill_index, -1)) "
        "WHERE is_reroll_marker = false"
    )

    # -----------------------------------------------------------------
    # Step 5: CHECK constraint — both-or-neither on (drill_index, target_skill_node_id).
    # Defense in depth alongside the Pydantic @model_validator on SessionCreate.
    # -----------------------------------------------------------------
    op.execute(
        "ALTER TABLE user_sessions "
        "ADD CONSTRAINT ck_drill_index_pairs_skill_node "
        "CHECK ((drill_index IS NULL AND target_skill_node_id IS NULL) "
        "    OR (drill_index IS NOT NULL AND target_skill_node_id IS NOT NULL))"
    )


def downgrade() -> None:
    # Reverse in strict reverse order.

    # Step 5 reverse: drop CHECK constraint
    op.execute(
        "ALTER TABLE user_sessions "
        "DROP CONSTRAINT IF EXISTS ck_drill_index_pairs_skill_node"
    )

    # Step 3+4 reverse: drop recreated index, restore original (from 0003)
    op.execute("DROP INDEX IF EXISTS uq_user_sessions_daily_rating")
    op.execute(
        "CREATE UNIQUE INDEX uq_user_sessions_daily_rating "
        "ON user_sessions (user_id, song_id, local_calendar_day) "
        "WHERE is_reroll_marker = false"
    )

    # Step 2 reverse: drop target_skill_node_id column (FK constraint drops with the column)
    op.drop_column("user_sessions", "target_skill_node_id")

    # Step 1 reverse: drop drill_index column
    op.drop_column("user_sessions", "drill_index")
