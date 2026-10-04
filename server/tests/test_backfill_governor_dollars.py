"""The contracts scripts/backfill_governor_dollars.py must not lose (FLE-95).

Three things make this backfill safe, and all three are invisible from the SQL alone:

  1. It prices through `app.ai.governor.price_dollars`, so history can never end up at
     a rate the live write path disagrees with.
  2. It never touches a cache-hit view row. Alembic 0013 prices those at 0 rather than
     NULL specifically so $0.00 stays distinguishable from unpriced; a backfill that
     repriced them would undo that distinction.
  3. `apply=True` executes the UPDATEs without committing — the shared contract with
     backfill_drills / backfill_drill_taxonomy (see test_backfill_drill_taxonomy.py).

Plus the one genuinely reconstructed input: the output ceiling behind
`dollars_estimated`, which is not on the row and has to come from the feature's
history.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.ai.governor import price_dollars
from scripts.backfill_governor_dollars import (
    _ACTUAL_ROWS_SQL,
    _ESTIMATED_ROWS_SQL,
    backfill,
    output_ceiling,
)

_SONNET = "claude-sonnet-4-6"
_BEFORE_RAISE = datetime(2026, 9, 16, 10, 41, 0, tzinfo=timezone.utc)
_AFTER_RAISE = datetime(2026, 9, 17, 8, 43, 0, tzinfo=timezone.utc)


def _actual_row(**overrides):
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "feature": "breakdown",
        "model": _SONNET,
        "created_at": _AFTER_RAISE,
        "prompt_tokens_actual": 6554,
        "output_tokens_actual": 6111,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _estimated_row(**overrides):
    base = {
        "id": "22222222-2222-2222-2222-222222222222",
        "feature": "skill_verify",
        "model": _SONNET,
        "created_at": _BEFORE_RAISE,
        "prompt_tokens_estimated": 49,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeSession:
    """Answers the two SELECTs by matching the statement text, records the UPDATEs."""

    def __init__(self, actual_rows=(), estimated_rows=()):
        self._actual = list(actual_rows)
        self._estimated = list(estimated_rows)
        self.updates: list[tuple[str, dict]] = []
        self.committed = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        if sql == _ACTUAL_ROWS_SQL:
            return SimpleNamespace(all=lambda: self._actual)
        if sql == _ESTIMATED_ROWS_SQL:
            return SimpleNamespace(all=lambda: self._estimated)
        self.updates.append((sql, params or {}))
        return SimpleNamespace()

    async def commit(self):
        self.committed += 1


@pytest.mark.asyncio
async def test_dollars_actual_matches_the_live_write_paths_price():
    """The whole point: history priced by the same helper record_actuals uses."""
    row = _actual_row()
    db = _FakeSession(actual_rows=[row])

    counters = await backfill(db, apply=True, skip_estimated=True)

    expected = price_dollars(
        _SONNET, row.prompt_tokens_actual, row.output_tokens_actual
    )
    assert counters.actual_priced == 1
    assert counters.actual_usd == expected
    assert len(db.updates) == 1
    sql, params = db.updates[0]
    assert "dollars_actual" in sql
    assert params == {"usd": expected, "id": row.id}


@pytest.mark.asyncio
async def test_apply_executes_the_updates_but_does_not_commit():
    """Shared contract: the caller owns the transaction, so a chained dry run stays dry."""
    db = _FakeSession(actual_rows=[_actual_row()], estimated_rows=[_estimated_row()])

    counters = await backfill(db, apply=True)

    assert counters.rows_written == 2, "both sides must still run their UPDATE"
    assert db.committed == 0, "committing is main()'s job, gated on --apply"


@pytest.mark.asyncio
async def test_a_dry_run_prices_everything_and_writes_nothing():
    """A dry run must report the figures the apply would write, not an approximation."""
    db = _FakeSession(actual_rows=[_actual_row()], estimated_rows=[_estimated_row()])

    counters = await backfill(db, apply=False)

    assert counters.actual_priced == 1 and counters.estimated_priced == 1
    assert counters.actual_usd > 0 and counters.estimated_usd > 0
    assert db.updates == [], "apply=False must not execute a single UPDATE"


def test_the_predicates_exclude_cache_hit_view_rows():
    """Alembic 0013's $0.00 rows must stay at 0, never be repriced as unpriced.

    Asserted on the SQL because that is where the exclusion lives: a row whose
    prompt_tokens_estimated is a literal 0 is breakdowns.py's cache-hit signature, and
    COALESCE(..., -1) is what keeps a NULL estimate (count_tokens failed, dispatch
    still happened) from falling into the same unknown-truth hole.
    """
    assert "COALESCE(prompt_tokens_estimated, -1) <> 0" in _ACTUAL_ROWS_SQL
    assert "prompt_tokens_estimated > 0" in _ESTIMATED_ROWS_SQL


@pytest.mark.asyncio
async def test_an_unpriced_model_is_skipped_not_guessed():
    """price_dollars returns None for an unknown model; the row must stay NULL."""
    db = _FakeSession(actual_rows=[_actual_row(model="claude-some-future-model")])

    counters = await backfill(db, apply=True, skip_estimated=True)

    assert counters.actual_matched == 1
    assert counters.actual_priced == 0
    assert db.updates == []
    assert counters.skipped and counters.skipped[0][3] == "unpriced model"


@pytest.mark.asyncio
async def test_a_feature_with_no_ceiling_history_is_skipped_not_guessed():
    """Same doctrine on the estimated side: no recovered ceiling means no number."""
    db = _FakeSession(estimated_rows=[_estimated_row(feature="some_new_feature")])

    counters = await backfill(db, apply=True)

    assert counters.estimated_matched == 1
    assert counters.estimated_priced == 0
    assert db.updates == []
    assert "no output-ceiling history" in counters.skipped[0][3]


def test_breakdown_rows_are_priced_at_the_ceiling_in_force_when_they_ran():
    """a6adbaa raised breakdown's max_tokens mid-history; both eras must survive.

    Pricing all nine affected rows at today's 20000 would overstate the worst case for
    the three that ran on 2026-09-16 by the output rate on 11808 tokens apiece.
    """
    assert output_ceiling("breakdown", _BEFORE_RAISE) == 8192
    assert output_ceiling("breakdown", _AFTER_RAISE) == 20000
    assert output_ceiling("skill_verify", _BEFORE_RAISE) == 1024
    assert output_ceiling("onboarding", _BEFORE_RAISE) == 8192
    assert output_ceiling("not_a_feature", _AFTER_RAISE) is None


def test_the_ceiling_table_tracks_the_modules_it_describes():
    """The CURRENT ceilings are imported, not copied, so the table cannot rot.

    If someone changes a module's _MAX_OUTPUT_TOKENS, the newest era here must move
    with it — otherwise a later backfill reconstructs a ceiling that never shipped.
    """
    from app.ai.breakdown import _MAX_OUTPUT_TOKENS as live_breakdown
    from app.ai.onboarding import _MAX_OUTPUT_TOKENS as live_onboarding
    from app.ai.skill_verifier import _MAX_OUTPUT_TOKENS as live_skill_verify

    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert output_ceiling("breakdown", now) == live_breakdown
    assert output_ceiling("onboarding", now) == live_onboarding
    assert output_ceiling("skill_verify", now) == live_skill_verify


@pytest.mark.asyncio
async def test_the_measured_prod_backlog_sums_to_the_reported_shortfall():
    """FLE-95 was filed on a number: ~$2.105 of real spend unpriced.

    Pinned as one synthetic row carrying prod's measured totals (224141 in / 95502 out
    across the 143 rows, re-measured 2026-10-04), so a rate or rounding change that
    moves the figure fails here instead of silently landing in the audit trail.
    """
    db = _FakeSession(
        actual_rows=[
            _actual_row(prompt_tokens_actual=224141, output_tokens_actual=95502)
        ]
    )

    counters = await backfill(db, apply=False, skip_estimated=True)

    assert counters.actual_usd == Decimal("2.104953")
