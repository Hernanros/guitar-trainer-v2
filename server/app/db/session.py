# server/app/db/session.py
# Async SQLAlchemy engine and session factory.
#
# Railway may inject DATABASE_URL with either scheme:
#   - "postgres://"    (legacy, pre-Postgres 15 style)
#   - "postgresql://"  (current, Postgres 15+ recommended form)
# SQLAlchemy 2.0 async with asyncpg requires "postgresql+asyncpg://".
# We normalize whichever form Railway hands us.
import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _get_database_url() -> str:
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Set it to your Postgres connection string before starting the server."
        )
    if raw_url.startswith("postgresql+asyncpg://"):
        return raw_url
    if raw_url.startswith("postgres://"):
        return raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
    if raw_url.startswith("postgresql://"):
        return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    raise RuntimeError(
        f"DATABASE_URL has an unrecognized scheme (expected postgres://, "
        f"postgresql://, or postgresql+asyncpg://): {raw_url[:20]}..."
    )


DATABASE_URL = _get_database_url()

engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # set to True for SQL debug logging
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with AsyncSessionLocal() as session:
        yield session
