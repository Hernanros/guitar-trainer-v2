"""The transaction contract for scripts/backfill_drill_taxonomy.py.

`backfill()` used to `await db.commit()` whenever `apply=True`, while its sibling
`backfill_drills.backfill()` documents the opposite — "the caller owns the
transaction". Chaining the two in one session to preview a load therefore committed
the *first* script's inserts as a side effect of the second script's apply, turning a
dry run into a production write. These tests pin the shared contract so that the two
can be composed without reading both implementations first.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.backfill_drill_taxonomy import backfill

pytestmark = pytest.mark.asyncio


def _row(**overrides):
    """One `_ROWS_SQL` row: unclassified and untiered, so both passes have work."""
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Three-String Pentatonic Box Run",
        "what": "Play the minor pentatonic box ascending and descending.",
        "success_criterion": "Clean at 90bpm, no buzz.",
        "common_trap": "Rushing the string crossing.",
        "tab_snippet": {"tuning": "EADGBE", "measures": []},
        "start_bpm": 60,
        "target_bpm": 90,
        "family": None,
        "tier": None,
        "node_name": "Minor Pentatonic Box 1",
        "root_name": "Lead Playing",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeSession:
    """Records calls. `execute` answers the row query, then swallows UPDATEs."""

    def __init__(self, rows):
        self._rows = rows
        self.executed = 0
        self.committed = 0
        self.rolled_back = 0

    async def execute(self, statement, params=None):
        self.executed += 1
        if params is None:  # the SELECT
            return SimpleNamespace(all=lambda: self._rows)
        return SimpleNamespace()

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1


async def test_apply_executes_the_updates_but_does_not_commit():
    """apply=True means "run the UPDATEs", not "make them permanent"."""
    db = _FakeSession([_row()])

    counters, _ = await backfill(db, apply=True)

    assert counters.rows_written == 1, "the UPDATE must still be executed"
    assert db.committed == 0, (
        "backfill() must leave the commit to its caller — committing here promotes a "
        "chained dry run into a real write"
    )
    assert db.rolled_back == 0, "it must not roll the caller's transaction back either"


async def test_dry_run_writes_nothing_and_still_classifies():
    """The plan is computed either way; only the UPDATE is gated on apply."""
    db = _FakeSession([_row()])

    counters, unclassified = await backfill(db, apply=False)

    assert counters.scanned == 1
    assert counters.family_filled + counters.family_declined == 1
    assert counters.rows_written == 0
    assert db.committed == 0
    assert unclassified == [] or unclassified[0]["id"] == _row().id


async def test_an_empty_bank_is_not_a_commit():
    """The zero-row path must not commit either — that was the original bug's shape."""
    db = _FakeSession([])

    counters, _ = await backfill(db, apply=True)

    assert counters.scanned == 0
    assert db.committed == 0
