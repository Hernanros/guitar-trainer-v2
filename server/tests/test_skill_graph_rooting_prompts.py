"""Regression test for FLE-82 — Fingerstyle's §10.3 cells were structurally unstockable
because both skill-graph generators (bootstrap onboarding + incremental verifier) had no
explicit rule steering fingerpicking/thumb-independence content to the Fingerstyle root,
so Sonnet sometimes filed a "Fingerpicking Patterns" sub-domain under Rhythm instead
(confirmed in production: skill_nodes for one pilot user, song 76's drills).

This is a prompt-content test, not a behavioral one — there is no way to assert an LLM's
judgment without a live call. It pins the literal disambiguation rule so a future prompt
rewrite can't silently drop it. Runs with no DB and no ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import os

os.environ.pop("ANTHROPIC_API_KEY", None)

from app.ai.onboarding import SYSTEM_PROMPT as ONBOARDING_SYSTEM_PROMPT
from app.ai.skill_verifier import SYSTEM_PROMPT as VERIFIER_SYSTEM_PROMPT


def test_onboarding_prompt_roots_fingerpicking_to_fingerstyle():
    assert "Root by TECHNIQUE, not by song role" in ONBOARDING_SYSTEM_PROMPT
    assert "Fingerpicking" in ONBOARDING_SYSTEM_PROMPT
    assert "Fingerstyle" in ONBOARDING_SYSTEM_PROMPT


def test_verifier_prompt_roots_fingerpicking_to_fingerstyle():
    assert "Root by TECHNIQUE, not by song role" in VERIFIER_SYSTEM_PROMPT
    assert "fingerpicking" in VERIFIER_SYSTEM_PROMPT.lower()
    assert "Fingerstyle" in VERIFIER_SYSTEM_PROMPT
