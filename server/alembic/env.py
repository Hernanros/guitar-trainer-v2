# server/alembic/env.py
# Alembic migration environment.
# CRITICAL: Override sqlalchemy.url from DATABASE_URL env var.
# Railway injects postgres:// but SQLAlchemy async requires postgresql+asyncpg://.
# This rewrite must happen here AND in session.py (Pitfall 6).
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import ORM models so Alembic can detect schema changes via autogenerate.
from app.models.db import Base, Song, User  # noqa: F401 — needed for target_metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override sqlalchemy.url with DATABASE_URL from environment.
# Use the standard (sync) postgresql:// driver for Alembic offline migrations.
# Railway injects "postgres://"; SQLAlchemy needs "postgresql://".
_raw_url = os.environ.get("DATABASE_URL", "")
if not _raw_url:
    raise RuntimeError(
        "DATABASE_URL is not set. "
        "Export it before running alembic (e.g., export DATABASE_URL=postgresql+asyncpg://...)."
    )

# Alembic runs sync migrations — use the psycopg2-compatible scheme.
# Replace asyncpg-specific scheme with a sync-compatible scheme for Alembic.
_alembic_url = (
    _raw_url
    .replace("postgres://", "postgresql://", 1)
    .replace("postgresql+asyncpg://", "postgresql://", 1)
)
config.set_main_option("sqlalchemy.url", _alembic_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode without an actual DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
