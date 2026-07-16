# server/app/db/session.py
# Async SQLAlchemy engine and session factory.
#
# CRITICAL: Railway injects DATABASE_URL with the scheme "postgres://", but
# SQLAlchemy 2.0 async with asyncpg requires "postgresql+asyncpg://".
# We rewrite the scheme at startup (Pitfall 3 from RESEARCH.md).
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
    # Rewrite Railway's postgres:// scheme for SQLAlchemy async (Pitfall 3)
    return raw_url.replace("postgres://", "postgresql+asyncpg://", 1)


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
