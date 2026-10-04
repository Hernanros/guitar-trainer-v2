# server/app/db/session.py
# Async SQLAlchemy engine and session factory.
#
# Railway may inject DATABASE_URL with either scheme:
#   - "postgres://"    (legacy, pre-Postgres 15 style)
#   - "postgresql://"  (current, Postgres 15+ recommended form)
# SQLAlchemy 2.0 async with asyncpg requires "postgresql+asyncpg://".
# We normalize whichever form Railway hands us.
import logging
import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Connection pool sizing (FLE-23 §1/§2)
# ---------------------------------------------------------------------------
# Before this, the engine passed only pool_pre_ping and inherited SQLAlchemy's
# AsyncAdaptedQueuePool defaults: pool_size=5 + max_overflow=10 = a hard ceiling
# of 15 concurrent connections. That ceiling does not survive the pilot, because
# a single request is not a single connection here:
#
#   one onboarding / re-run  → up to 7 connections
#       1 request session (get_db)
#     + 1 fresh session for @governed bookkeeping (_run_onboarding_parse_bounded)
#     + up to _VERIFIER_SEMAPHORE_LIMIT (5) fresh sessions for the verifier fan-out
#       (api/v1/users.py::_bounded_verify — one AsyncSessionLocal per concurrent verify)
#
#   one breakdown            → 1 connection, held for the FULL ~76s Sonnet call
#       The request session is open across the dispatch, so ten concurrent
#       breakdowns pin ten connections for over a minute. FLE-23 §2 explicitly
#       prefers sizing the pool over restructuring the request path before a
#       trial, so that is what this does — the connection is still held, there
#       are simply enough of them.
#
# Measured against the pilot's worst realistic minute — 5 concurrent onboardings
# (35) + 10 concurrent breakdowns (10) = 45 — the defaults below leave headroom:
#
#   pool_size 15 + max_overflow 35 = ceiling 50
#
# Two invariants worth keeping in mind if you change these numbers:
#   - Ceiling must exceed _VERIFIER_SEMAPHORE_LIMIT-driven fan-out. Raising the
#     semaphore in api/v1/users.py multiplies onboarding's 7-connection cost.
#   - Ceiling must stay under the Postgres server's max_connections minus
#     whatever else connects — the APScheduler jobs and the startup seed each
#     take one. Prod's Postgres 18 reports max_connections = 500 (measured, not
#     assumed — Railway's template default is lower, so do not infer it from the
#     docs), which leaves this ceiling an order of magnitude of headroom.
#
# All three are env-overridable so Railway can retune without a deploy of code.
_DEFAULT_POOL_SIZE = 15
_DEFAULT_MAX_OVERFLOW = 35
_DEFAULT_POOL_TIMEOUT = 30


def _int_env(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back to `default`.

    A typo must not silently shrink the pool back to something that deadlocks
    under the pilot's load, so an unparseable or non-positive value logs loudly
    and keeps the default. pool_size=0 means "unlimited" to SQLAlchemy's
    QueuePool, which is the opposite of what someone typing a bad value wants.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning("Ignoring unparseable %s=%r; keeping %d.", name, raw, default)
        return default
    if parsed <= 0:
        logger.warning("Ignoring non-positive %s=%d; keeping %d.", name, parsed, default)
        return default
    return parsed


POOL_SIZE = _int_env("FLETCHER_DB_POOL_SIZE", _DEFAULT_POOL_SIZE)
MAX_OVERFLOW = _int_env("FLETCHER_DB_MAX_OVERFLOW", _DEFAULT_MAX_OVERFLOW)
POOL_TIMEOUT = _int_env("FLETCHER_DB_POOL_TIMEOUT", _DEFAULT_POOL_TIMEOUT)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # set to True for SQL debug logging
    pool_pre_ping=True,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT,
)

logger.info(
    "DB pool sized: pool_size=%d max_overflow=%d (ceiling %d) pool_timeout=%ds.",
    POOL_SIZE, MAX_OVERFLOW, POOL_SIZE + MAX_OVERFLOW, POOL_TIMEOUT,
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
