"""Governor calls, skill_node_proposals, skill_node_rejections, decay_runs;
canonical_node_id + last_decayed_at on skill_nodes.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-30

Ordered steps:
1. CREATE TABLE governor_calls with index ix_governor_calls_user_feature_created
2. CREATE TABLE skill_node_proposals with FK to users + nullable FK to skill_nodes
3. CREATE TABLE skill_node_rejections with verifier_response JSONB nullable
4. CREATE TABLE decay_runs
5. op.add_column skill_nodes.canonical_node_id UUID nullable + FK constraint
6. CREATE INDEX ix_skill_nodes_canonical on skill_nodes(canonical_node_id)
7. op.add_column skill_nodes.last_decayed_at TIMESTAMPTZ nullable

downgrade() reverses in strict reverse order.
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # Step 1: CREATE TABLE governor_calls
    # Schema per D-Claude-schema: {id, user_id FK, feature TEXT, model TEXT,
    # prompt_tokens_estimated INT, prompt_tokens_actual INT NULL,
    # output_tokens_actual INT NULL, dollars_estimated NUMERIC NULL,
    # dollars_actual NUMERIC NULL, error_code TEXT NULL, created_at TIMESTAMPTZ}
    # feature + model are plain TEXT to avoid ALTER TYPE complexity.
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE governor_calls (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id),
            feature TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens_estimated INTEGER NULL,
            prompt_tokens_actual INTEGER NULL,
            output_tokens_actual INTEGER NULL,
            dollars_estimated NUMERIC(10,6) NULL,
            dollars_actual NUMERIC(10,6) NULL,
            error_code TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Index for cap-check query: WHERE user_id=X AND feature=Y AND created_at > ...
    # DESC on created_at improves rolling-window queries.
    op.execute(
        "CREATE INDEX ix_governor_calls_user_feature_created "
        "ON governor_calls (user_id, feature, created_at DESC)"
    )

    # -----------------------------------------------------------------
    # Step 2: CREATE TABLE skill_node_proposals
    # D-13/D-14: status plain TEXT with CHECK constraint; canonical_id nullable FK.
    # fuzzy_score INT stores the rapidfuzz token_set_ratio score (0-100).
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE skill_node_proposals (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id),
            proposed_name TEXT NOT NULL,
            fuzzy_score INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected', 'merged')),
            canonical_id UUID NULL REFERENCES skill_nodes(id),
            verifier_verdict TEXT NULL,
            verifier_reason TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # -----------------------------------------------------------------
    # Step 3: CREATE TABLE skill_node_rejections
    # D-14: verifier_response JSONB nullable (stores full verifier response JSON).
    # No FK to users — rejected proposals are global audit records.
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE skill_node_rejections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            proposed_name TEXT NOT NULL,
            reason TEXT NOT NULL,
            verifier_response JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # -----------------------------------------------------------------
    # Step 4: CREATE TABLE decay_runs
    # D-Claude-decay: one row per nightly decay job run; finished_at + nodes_affected
    # NULL until job completes; error TEXT for failure audit.
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE decay_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ NULL,
            nodes_affected INTEGER NULL,
            error TEXT NULL
        )
    """)

    # -----------------------------------------------------------------
    # Step 5: Add skill_nodes.canonical_node_id + FK constraint
    # D-12: nullable UUID pointing back to skill_nodes(id) — self-referential FK.
    # Existing rows stay NULL (no backfill at migration time).
    # -----------------------------------------------------------------
    op.add_column(
        "skill_nodes",
        sa.Column("canonical_node_id", sa.UUID(), nullable=True),
    )
    op.execute(
        "ALTER TABLE skill_nodes ADD CONSTRAINT fk_skill_nodes_canonical "
        "FOREIGN KEY (canonical_node_id) REFERENCES skill_nodes(id)"
    )

    # -----------------------------------------------------------------
    # Step 6: Create index on skill_nodes.canonical_node_id
    # D-12: supports JOIN queries from verifier pipeline.
    # -----------------------------------------------------------------
    op.execute(
        "CREATE INDEX ix_skill_nodes_canonical "
        "ON skill_nodes (canonical_node_id)"
    )

    # -----------------------------------------------------------------
    # Step 7: Add skill_nodes.last_decayed_at
    # D-Claude-decay: per-node forensics timestamp; NULL = never decayed.
    # -----------------------------------------------------------------
    op.add_column(
        "skill_nodes",
        sa.Column("last_decayed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Reverse in strict reverse order of upgrade steps.

    # Step 7 reverse: drop skill_nodes.last_decayed_at
    op.drop_column("skill_nodes", "last_decayed_at")

    # Step 6 reverse: drop ix_skill_nodes_canonical
    op.execute("DROP INDEX IF EXISTS ix_skill_nodes_canonical")

    # Step 5 reverse: drop FK constraint + drop canonical_node_id column
    op.execute(
        "ALTER TABLE skill_nodes DROP CONSTRAINT IF EXISTS fk_skill_nodes_canonical"
    )
    op.drop_column("skill_nodes", "canonical_node_id")

    # Step 4 reverse: drop decay_runs table
    op.drop_table("decay_runs")

    # Step 3 reverse: drop skill_node_rejections table
    op.drop_table("skill_node_rejections")

    # Step 2 reverse: drop skill_node_proposals table
    op.drop_table("skill_node_proposals")

    # Step 1 reverse: drop governor_calls index + table
    op.execute("DROP INDEX IF EXISTS ix_governor_calls_user_feature_created")
    op.drop_table("governor_calls")
