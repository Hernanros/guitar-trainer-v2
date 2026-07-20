"""Users, songs categorization, and skill graph schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-20

Phase 2 (02-01) — Ordered steps per D-14:
1. Create users table (UUID PK)
2. Create song_category Postgres enum type
3. Add user_id + category columns to songs (nullable — seed row backfilled below)
4. Seed system user 00000000-0000-0000-0000-000000000000
5. Backfill existing songs rows to system user with category=can_play
6. Create skill_level Postgres enum type
7. Create skill_nodes table with DEFERRABLE INITIALLY DEFERRED self-referential parent_id FK
   (deferrable so 02-03 _persist_bootstrap can INSERT nodes in any order within a transaction)
8. Create song_skills junction table (song_id Integer FK songs.id, skill_node_id UUID FK skill_nodes.id)

downgrade() reverses in strict reverse order.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# System user UUID — sentinel for seed/system content (per <specifics> in 02-CONTEXT.md)
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    # -----------------------------------------------------------------
    # Step 1: Create users table
    # -----------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("preferences", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("raw_onboarding_text", JSONB, nullable=True),
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # -----------------------------------------------------------------
    # Step 2 + 3: Create song_category enum and add columns to songs via raw SQL.
    # Raw SQL avoids SQLAlchemy re-emitting CREATE TYPE when op.add_column
    # processes an sa.Enum column that SQLAlchemy hasn't seen in this session.
    # Both columns are nullable per D-14 step 1 to preserve existing seed row.
    # -----------------------------------------------------------------
    op.execute("CREATE TYPE song_category AS ENUM ('can_play', 'working_on', 'aspirational')")
    op.execute("ALTER TABLE songs ADD COLUMN user_id UUID REFERENCES users(id)")
    op.execute("ALTER TABLE songs ADD COLUMN category song_category")

    # -----------------------------------------------------------------
    # Step 4: Seed the system user (idempotent via ON CONFLICT DO NOTHING)
    # -----------------------------------------------------------------
    op.execute(
        f"INSERT INTO users (id, preferences, onboarded_at, created_at) "
        f"VALUES ('{SYSTEM_USER_ID}', '{{}}'::jsonb, now(), now()) "
        f"ON CONFLICT (id) DO NOTHING"
    )

    # -----------------------------------------------------------------
    # Step 5: Backfill existing songs rows to the system user
    # UPDATE ... WHERE user_id IS NULL is idempotent — safe to re-run
    # -----------------------------------------------------------------
    op.execute(
        f"UPDATE songs SET user_id = '{SYSTEM_USER_ID}', category = 'can_play' "
        f"WHERE user_id IS NULL"
    )

    # -----------------------------------------------------------------
    # Step 6 + 7: Create skill_level enum and skill_nodes table via raw SQL.
    #
    # Why raw SQL? SQLAlchemy's sa.Enum inside op.create_table fires an
    # _on_table_create event that re-emits CREATE TYPE regardless of create_type=False
    # when the ORM metadata hasn't seen the type before in this migration session.
    # Raw SQL avoids this edge case entirely.
    #
    # CRITICAL (Revision E / success_criteria #11):
    # parent_id FK is DEFERRABLE INITIALLY DEFERRED so that 02-03's
    # _persist_bootstrap can INSERT all nodes in any order within a single
    # transaction without IntegrityError mid-insert (Postgres checks deferred FKs
    # only at COMMIT time).
    # -----------------------------------------------------------------
    op.execute("CREATE TYPE skill_level AS ENUM ('root', 'sub', 'leaf')")
    op.execute("""
        CREATE TABLE skill_nodes (
            id UUID PRIMARY KEY NOT NULL,
            user_id UUID NOT NULL REFERENCES users(id),
            name VARCHAR(255) NOT NULL,
            level skill_level NOT NULL,
            parent_id UUID,
            tempo_bin_low INTEGER,
            tempo_bin_high INTEGER,
            mastery NUMERIC(4,3) NOT NULL DEFAULT 0.0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT fk_skill_nodes_parent
                FOREIGN KEY (parent_id) REFERENCES skill_nodes(id)
                DEFERRABLE INITIALLY DEFERRED
        )
    """)

    # -----------------------------------------------------------------
    # Step 8: Create song_skills junction table
    # song_id is Integer (songs.id is Integer from 0001 — backward compat preserved)
    # -----------------------------------------------------------------
    op.create_table(
        "song_skills",
        sa.Column("song_id", sa.Integer(), sa.ForeignKey("songs.id"), nullable=False),
        sa.Column(
            "skill_node_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("skill_nodes.id"),
            nullable=False,
        ),
        sa.Column(
            "weight",
            sa.Numeric(4, 3),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
        sa.PrimaryKeyConstraint("song_id", "skill_node_id"),
    )


def downgrade() -> None:
    # Reverse in strict reverse order of upgrade

    # Step 8 reverse: drop song_skills
    op.drop_table("song_skills")

    # Step 7 reverse: drop skill_nodes (self-ref FK dropped with table)
    op.drop_table("skill_nodes")

    # Step 6 reverse: drop skill_level enum type (use raw SQL — checkfirst via IF EXISTS)
    op.execute("DROP TYPE IF EXISTS skill_level")

    # Steps 3-5 reverse: remove columns added to songs
    op.drop_column("songs", "category")
    op.drop_column("songs", "user_id")

    # Step 2 reverse: drop song_category enum type (use raw SQL — checkfirst via IF EXISTS)
    op.execute("DROP TYPE IF EXISTS song_category")

    # Step 1 reverse: drop users table
    # Note: songs.user_id FK is already gone (column dropped above) so cascade is not needed
    op.drop_table("users")
