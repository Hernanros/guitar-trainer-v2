"""Server test configuration.

Adds server/app to sys.path so tests can import app modules without a package install.

The whole session runs on ONE event loop. That is required — app/db/session.py builds
its AsyncEngine at import time and a pooled asyncpg connection belongs to the loop that
opened it — but it is configured in pytest.ini via asyncio_default_fixture_loop_scope
and asyncio_default_test_loop_scope, NOT by overriding the `event_loop` fixture here.
The override was deprecated in pytest-asyncio 0.23 and removed in 1.0, and it only ever
moved fixtures onto the session loop while test bodies stayed on per-function loops —
which is the cross-loop split it was meant to prevent. See pytest.ini for the detail.
"""
import os
import sys

# Ensure the server directory is in sys.path for app imports
server_dir = os.path.dirname(os.path.abspath(__file__))
if server_dir not in sys.path:
    sys.path.insert(0, server_dir)

# Set DATABASE_URL for tests if not already set
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://gt:devpass@localhost:5433/guitar_trainer",
)
