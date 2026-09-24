"""deliberately-broken migration — TEMPORARY, reverted in the very next commit

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-24

FLE-68 done-when #2: "A deliberately-broken migration fails the deploy rather than
producing a running container on an un-migrated DB." This file exists to be pushed
once, observed failing, and deleted. If you are reading it on main, the revert did
not land — delete it.

It is built to test the WORSE hazard, not the easy one. A migration that fails on its
first statement proves little; the dangerous shape is one that has already started
rewriting the schema when it dies. So upgrade() does a genuine ADD COLUMN and only
then hits an undefined function. Two things must hold afterwards:

  1. Postgres transactional DDL + alembic's `with context.begin_transaction()`
     (see alembic/env.py::run_migrations_online) roll the ADD COLUMN back, so the
     schema is untouched and alembic_version still reads 0010.
  2. Railway's pre-deploy container exits non-zero, which aborts the deploy. The
     previously-deployed container keeps serving, so /healthz stays 200 throughout.

Target is practice_sessions, which holds 0 rows in prod — if assumption (1) were
somehow wrong, the fallout is one nullable throwaway column on an empty table, and
`ALTER TABLE practice_sessions DROP COLUMN fle68_rollback_canary` is the whole repair.

The failing statement calls a function that does not exist, rather than raising a
Python error, on purpose: it has to fail on the DATABASE side, mid-transaction, to
exercise the rollback. A Python-side raise would abort before Postgres ever saw the
ADD COLUMN and would not test (1) at all.
"""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: a real schema change, so the rollback has something to undo.
    op.add_column(
        "practice_sessions",
        sa.Column("fle68_rollback_canary", sa.Integer(), nullable=True),
    )

    # Step 2: die on the database side, inside the same transaction as step 1.
    # UndefinedFunction -> alembic propagates -> `alembic upgrade head` exits non-zero
    # -> Railway aborts the deploy. Step 1 rolls back with it.
    op.execute("SELECT fle68_this_function_does_not_exist()")


def downgrade() -> None:
    # Unreachable in practice: upgrade() can never commit. Present so the revision is
    # well-formed rather than a trap for anyone who runs `alembic downgrade`.
    op.drop_column("practice_sessions", "fle68_rollback_canary")
