"""Initial songs table

Revision ID: 0001
Revises:
Create Date: 2026-07-16

Creates the songs table with a JSONB breakdown column.
The breakdown stores the full Phase 3-ready payload shape (D-01).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "songs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("artist", sa.String(255), nullable=False),
        sa.Column("genre", sa.String(100), nullable=True),
        sa.Column("difficulty", sa.String(50), nullable=True),
        sa.Column("bpm", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(10), nullable=True),
        sa.Column("breakdown", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("songs")
