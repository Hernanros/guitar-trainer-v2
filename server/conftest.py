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
from datetime import datetime, timezone

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


@pytest.fixture(autouse=True)
def _isolate_pilot_window(monkeypatch):
    """Pin FLETCHER_PILOT_START to the moment each test begins (FLE-139).

    The pilot dollar ceiling is a SUM over `governor_calls` with no user filter and
    no feature filter, measured against the window FLETCHER_PILOT_START opens. With
    the window unset that sum is every row the database has ever held — and the line
    above points the suite at the shared local dev database, whose accumulated rows
    crossed the $25 PILOT_DOLLAR_CEILING in October 2026. Past that point `@governed`
    refuses on every developer machine, the breakdown endpoint 429s
    PILOT_BUDGET_SPENT, and every test asserting a 200 through a cache miss fails for
    a reason that has nothing to do with the code under test. It does not self-heal:
    local spend only grows.

    Pinning the window start makes each test's measured spend exactly the rows that
    test created itself. This is deliberately NOT a way of switching the ceiling off:
    the ceiling still defaults to $25, still runs its real SUM under the real global
    advisory lock, and still refuses once a test's OWN rows cross it — which is what
    tests/test_pilot_ceiling.py exercises, including against the unmodified $25
    default. Raising or disabling the ceiling globally here (e.g. via
    FLETCHER_PILOT_CEILING_USD) would neuter that coverage; pinning the window does
    not, because it only changes WHICH rows count, never the limit.

    This lifts tests/test_pilot_ceiling.py's own `_isolate_pilot_window` to the whole
    suite — it needed this first and solved it locally. That module keeps its copy:
    it is the file where the window is itself under test, and the two tests that
    assert on pilot_window_start() override both layers with their own setenv.
    """
    monkeypatch.setenv("FLETCHER_PILOT_START", datetime.now(timezone.utc).isoformat())
    yield
