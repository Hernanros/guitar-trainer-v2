"""FLE-96: count_tokens must price the same payload create() dispatches.

The prod bug: all three @governed Sonnet call sites passed only `messages` to
count_tokens while the dispatch two lines later also passed `system=` and
`tools=`. Both are billable input, so prompt_tokens_estimated priced the user
turn alone — 56 estimated vs 6642 actual on a breakdown (118x), 55 vs 2926 on
onboarding (53x).

The guard here is argument-list parity, not an estimated-vs-actual ratio.
With the Anthropic client mocked there is no tokenizer, so any ratio assertion
would only be comparing two numbers this file made up. What actually prevents
the regression is that the two argument lists cannot drift: every
prompt-shaping kwarg create() receives must also reach count_tokens.

_EXPECTED_PROMPT_SHAPING_KEYS is deliberately a closed set. Adding a new
prompt-shaping argument to a create() call (another tool, mcp_servers, a cached
system block) fails these tests until the same argument is handed to
count_tokens too — which is the drift this issue is about.

All Sonnet calls are mocked; no real API calls. Runs against the real Postgres
test DB because @governed writes governor_calls rows for real.
"""
from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure no live API key leaks into this test suite
os.environ.pop("ANTHROPIC_API_KEY", None)

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.breakdown import AIBreakdownError, run_technique_breakdown
from app.ai.onboarding import AIParseError, run_onboarding_parse
from app.ai.skill_verifier import AISkillVerifierError, run_skill_node_verify


# Everything create() passes that shapes the billable prompt. `max_tokens` is
# deliberately excluded — it bounds output, not input, and record_estimate
# already carries it separately (FLE-23 §4).
_EXPECTED_PROMPT_SHAPING_KEYS = {
    "model",
    "system",
    "messages",
    "tools",
    "tool_choice",
}
_OUTPUT_ONLY_KEYS = {"max_tokens"}


# ---------------------------------------------------------------------------
# Test DB helpers (mirror test_skill_verifier.py)
# ---------------------------------------------------------------------------

def _make_test_db_url() -> str:
    raw = os.environ.get(
        "DATABASE_URL",
        "postgresql://gt:devpass@localhost:5433/guitar_trainer",
    )
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    return raw


def _make_session() -> AsyncSession:
    engine = create_async_engine(
        _make_test_db_url(), echo=False, pool_size=1, max_overflow=0
    )
    return async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )()


async def _seed_user(user_id: str) -> None:
    async with _make_session() as db:
        await db.execute(
            text(
                "INSERT INTO users (id, preferences) "
                "VALUES (:uid, '{}'::jsonb) ON CONFLICT DO NOTHING"
            ),
            {"uid": user_id},
        )
        await db.commit()


async def _cleanup_user(user_id: str) -> None:
    async with _make_session() as db:
        await db.execute(
            text("DELETE FROM governor_calls WHERE user_id = :uid"), {"uid": user_id}
        )
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        await db.commit()


# ---------------------------------------------------------------------------
# Mock client
# ---------------------------------------------------------------------------

def _build_mock_client() -> MagicMock:
    """A client whose create() returns no tool_use block.

    Each call site then raises its own AI*Error after the D-07 retry — which is
    exactly what we want. Both count_tokens and create still run on every
    attempt, so the kwargs are captured, and the assertions stay decoupled from
    the Breakdown / SonnetOnboardingOutput / SkillNodeVerifyOutput schemas.
    Those schemas churn; the argument lists are what this test is pinning.
    """
    usage = MagicMock()
    usage.input_tokens = 6642
    usage.output_tokens = 500

    resp = MagicMock()
    resp.content = []  # no tool_use → forces the failure path
    resp.usage = usage
    resp.stop_reason = "end_turn"

    count_tokens_result = MagicMock()
    count_tokens_result.input_tokens = 6500

    client = MagicMock()
    client.messages.create = AsyncMock(return_value=resp)
    client.messages.count_tokens = AsyncMock(return_value=count_tokens_result)
    return client


def _assert_payload_parity(client: MagicMock) -> None:
    """count_tokens priced exactly the payload create() dispatched."""
    assert client.messages.count_tokens.await_count >= 1, (
        "count_tokens was never called — the pre-dispatch estimate is the whole "
        "point of the D-03 two-step protocol"
    )
    assert client.messages.create.await_count >= 1

    count_kwargs: dict[str, Any] = client.messages.count_tokens.call_args_list[0].kwargs
    create_kwargs: dict[str, Any] = client.messages.create.call_args_list[0].kwargs

    # Positional args would slip past the kwargs comparison below.
    assert not client.messages.count_tokens.call_args_list[0].args
    assert not client.messages.create.call_args_list[0].args

    # If create() grows a new prompt-shaping argument, this is where it surfaces.
    assert set(create_kwargs) == _EXPECTED_PROMPT_SHAPING_KEYS | _OUTPUT_ONLY_KEYS, (
        "create() passes an argument this test does not classify. Decide whether "
        "it shapes the billable prompt: if it does, pass it to count_tokens too "
        "and add it to _EXPECTED_PROMPT_SHAPING_KEYS; if it only bounds output, "
        "add it to _OUTPUT_ONLY_KEYS."
    )

    assert set(count_kwargs) == _EXPECTED_PROMPT_SHAPING_KEYS, (
        "count_tokens is not being handed every prompt-shaping argument — this "
        "is the FLE-96 undercount. Missing: "
        f"{sorted(_EXPECTED_PROMPT_SHAPING_KEYS - set(count_kwargs))}"
    )

    for key in sorted(_EXPECTED_PROMPT_SHAPING_KEYS):
        assert count_kwargs[key] == create_kwargs[key], (
            f"count_tokens priced a different {key!r} than create() dispatched"
        )

    # Parity would also hold if both were empty; these are the two that were
    # actually dropped in prod, and they dominate the prompt.
    assert count_kwargs["system"], "system prompt is billable input and must be counted"
    assert count_kwargs["tools"], "tool schema is billable input and must be counted"


# ---------------------------------------------------------------------------
# Tests — one per @governed Sonnet call site
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_breakdown_count_tokens_prices_the_dispatched_payload():
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)
    mock_client = _build_mock_client()

    try:
        with patch("app.ai.breakdown.get_client", return_value=mock_client):
            async with _make_session() as db:
                with pytest.raises(AIBreakdownError):
                    await run_technique_breakdown(
                        "Wonderwall",
                        "Oasis",
                        [{"id": str(uuid.uuid4()), "name": "Strumming"}],
                        0.5,
                        db=db,
                        user_id=uuid.UUID(user_id),
                    )
        _assert_payload_parity(mock_client)
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_onboarding_count_tokens_prices_the_dispatched_payload():
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)
    mock_client = _build_mock_client()

    try:
        with patch("app.ai.onboarding.get_client", return_value=mock_client):
            async with _make_session() as db:
                with pytest.raises(AIParseError):
                    await run_onboarding_parse(
                        {
                            "can_play": "Wonderwall",
                            "working_on": "barre chords",
                            "aspirational": "Little Wing",
                        },
                        db=db,
                        user_id=uuid.UUID(user_id),
                    )
        _assert_payload_parity(mock_client)
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_skill_verifier_count_tokens_prices_the_dispatched_payload():
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)
    mock_client = _build_mock_client()

    try:
        with patch("app.ai.skill_verifier.get_client", return_value=mock_client):
            async with _make_session() as db:
                with pytest.raises(AISkillVerifierError):
                    await run_skill_node_verify(
                        "Blues Shuffle Pattern",
                        [],
                        db=db,
                        user_id=uuid.UUID(user_id),
                    )
        _assert_payload_parity(mock_client)
    finally:
        await _cleanup_user(user_id)
