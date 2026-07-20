"""Server test configuration.

Adds server/app to sys.path so tests can import app modules without a package install.
Uses a session-scoped event loop so the FastAPI app's module-level SQLAlchemy engine
(which binds connection pools to the event loop at creation time) is reused across tests
rather than getting bound to different per-function event loops.
"""
import asyncio
import os
import sys

import pytest

# Ensure the server directory is in sys.path for app imports
server_dir = os.path.dirname(os.path.abspath(__file__))
if server_dir not in sys.path:
    sys.path.insert(0, server_dir)

# Set DATABASE_URL for tests if not already set
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://gt:devpass@localhost:5433/guitar_trainer",
)


@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop.

    Required because the FastAPI app module creates a SQLAlchemy AsyncEngine at import
    time which binds its connection pool to the current event loop. If each test uses
    a fresh function-scoped loop, the pool's connections are attached to the old (closed)
    loop and raise 'Future attached to a different loop' on subsequent tests.

    Using a single session-scoped loop ensures the app's engine pool stays valid for
    the entire test session.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
