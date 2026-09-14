"""Live-Anthropic drill evaluation — skip-by-default pytest wrapper.

Phase 4.1 Plan 04.1-05: This module wraps scripts/eval_drills.py as a pytest-runnable
test for reviewer-friendly output. It is NEVER run in CI.

Double-opt-in guard (T-04.1-14 mitigate):
  1. ANTHROPIC_EVAL_RUN=1 must be set explicitly (user intent).
  2. ANTHROPIC_API_KEY must be set (API access).
  3. DATABASE_URL must point to a real Postgres dev DB (W5 fix — no SQLite fallback).

Run the live eval:
    cd server
    ANTHROPIC_EVAL_RUN=1 \\
    ANTHROPIC_API_KEY=sk-ant-... \\
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/guitar_trainer \\
    python -m pytest tests/test_drill_eval_live.py -v -s

Without ANTHROPIC_EVAL_RUN=1, this file collects cleanly but the test is skipped:
    python -m pytest tests/test_drill_eval_live.py -v --collect-only  # shows 1 test
    python -m pytest tests/test_drill_eval_live.py -v                 # SKIPPED

This test does NOT assert — it prints reviewer-friendly output for each of the 5 eval
songs. The reviewer walks through 6 landmine gates per song (30 total gates) and records
their disposition in .planning/phases/04.1-ai-drills/04.1-05-EVAL-RESULTS.md.

Landmine gates (RESEARCH.md catalog, 6 per song = 30 total for 5 songs):
  L1: tab_snippet is a canonical isolated shape (NOT a slice of the main song tab)
  L2: target_skill_temp_id echoes one of the provided fake UUIDs (LIMITED per N1 caveat)
  L3: drill count in [2, 4] — drills=[] means Landmine #3 soft-fail fired (Plan 01)
  L4: song_specific=true iff `what` copy explicitly names the song
  L5: drills ordered easiest→hardest (difficulty ramps from drill 0 to drill N)
  L6: tab_snippet.measures.length in {1, 2}

N1 CAVEAT: L2 (hallucinated target_skill_temp_id) coverage is LIMITED in this eval —
target_skills are fake uuid4() values not backed by real skill_nodes rows. The eval
catches only gross hallucinations (Sonnet inventing a UUID NOT in the provided list).
Full L2 coverage lives in Task 3 device step o (real user's breakdown query).

Cost: ~$0.35 for 5 breakdowns. Well inside the $20/mo Anthropic Console cap.
"""
import asyncio
import os

import pytest


@pytest.mark.skipif(
    os.environ.get("ANTHROPIC_EVAL_RUN") != "1",
    reason=(
        "Set ANTHROPIC_EVAL_RUN=1 (and provide ANTHROPIC_API_KEY + DATABASE_URL pointing "
        "to a real Postgres dev DB) to run the live drill eval. "
        "Cost: ~$0.35 for 5 songs. This test is intentionally excluded from CI."
    ),
)
def test_drill_eval_live_all_5_songs():
    """Live-Anthropic diagnostic eval for 5 hand-picked songs.

    NOT run in CI. Requires human review of printed output.

    Prints reviewer-friendly drill JSON + a 6-gate landmine checklist per song.
    Reviewer fills in .planning/phases/04.1-ai-drills/04.1-05-EVAL-RESULTS.md.

    This test does NOT make assertions — output inspection is the test.
    """
    from scripts.eval_drills import run_eval
    asyncio.run(run_eval())
    # No assertions — this is a diagnostic, not a correctness check.
    # The reviewer inspects the printed output and fills in the EVAL-RESULTS.md gate table.
