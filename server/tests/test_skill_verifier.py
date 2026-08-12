"""Tests for server/app/ai/skill_verifier.py.

Runs WITHOUT a real ANTHROPIC_API_KEY — patches app.ai.client.get_client() to
return a MagicMock. Exercises:

  - test_verifier_returns_yes_verdict_for_valid_proposal
  - test_verifier_returns_no_verdict_for_invalid_proposal
  - test_verifier_returns_uncertain_verdict_for_ambiguous
  - test_verifier_wrapped_with_governed_logs_call (governor_calls row with feature='skill_verify')
  - test_verifier_raises_ai_skill_verifier_error_on_retry_failure
  - test_verifier_uses_correct_sonnet_model

All Sonnet calls are mocked — no real API calls.
Runs against a REAL Postgres test database for governor_calls assertions.
"""
from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.skill_verifier import AISkillVerifierError, SkillNodeVerifyOutput, run_skill_node_verify
from app.ai.client import SONNET_MODEL

# Ensure no live API key leaks into this test suite
os.environ.pop("ANTHROPIC_API_KEY", None)


# ---------------------------------------------------------------------------
# Test DB helpers
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
            text("DELETE FROM governor_calls WHERE user_id = :uid"),
            {"uid": user_id},
        )
        await db.execute(
            text("DELETE FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Mock builder helpers
# ---------------------------------------------------------------------------

def _build_mock_client(verdict: str, root: str | None, reason: str) -> MagicMock:
    """Return a mock get_client() result that emits the given verdict as tool_use output."""
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.input = {"verdict": verdict, "root": root, "reason": reason}

    usage = MagicMock()
    usage.input_tokens = 50
    usage.output_tokens = 30

    resp = MagicMock()
    resp.content = [tool_use]
    resp.usage = usage

    count_tokens_result = MagicMock()
    count_tokens_result.input_tokens = 40

    client = MagicMock()
    client.messages.create = AsyncMock(return_value=resp)
    client.messages.count_tokens = AsyncMock(return_value=count_tokens_result)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verifier_returns_yes_verdict_for_valid_proposal():
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    mock_client = _build_mock_client(
        verdict="yes",
        root="Rhythm",
        reason="clear rhythm skill",
    )

    try:
        with patch("app.ai.skill_verifier.get_client", return_value=mock_client):
            async with _make_session() as db:
                result = await run_skill_node_verify(
                    "Blues Shuffle Pattern",
                    [],
                    db=db,
                    user_id=uuid.UUID(user_id),
                )
        assert isinstance(result, SkillNodeVerifyOutput)
        assert result.verdict == "yes"
        assert result.root == "Rhythm"
        assert result.reason == "clear rhythm skill"
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_verifier_returns_no_verdict_for_invalid_proposal():
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    mock_client = _build_mock_client(
        verdict="no",
        root=None,
        reason="not a guitar skill",
    )

    try:
        with patch("app.ai.skill_verifier.get_client", return_value=mock_client):
            async with _make_session() as db:
                result = await run_skill_node_verify(
                    "Random Topic",
                    [],
                    db=db,
                    user_id=uuid.UUID(user_id),
                )
        assert result.verdict == "no"
        assert result.root is None
        assert result.reason == "not a guitar skill"
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_verifier_returns_uncertain_verdict_for_ambiguous():
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    mock_client = _build_mock_client(
        verdict="uncertain",
        root="Lead",
        reason="could fit under Lead or Fingerstyle",
    )

    try:
        with patch("app.ai.skill_verifier.get_client", return_value=mock_client):
            async with _make_session() as db:
                result = await run_skill_node_verify(
                    "Melodic Fingerpicking Runs",
                    ["Classical Fingerpicking"],
                    db=db,
                    user_id=uuid.UUID(user_id),
                )
        assert result.verdict == "uncertain"
        assert result.root == "Lead"
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_verifier_wrapped_with_governed_logs_call():
    """After invocation, governor_calls has a new row with feature='skill_verify'."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    mock_client = _build_mock_client(
        verdict="yes",
        root="Lead",
        reason="clear technique",
    )

    try:
        with patch("app.ai.skill_verifier.get_client", return_value=mock_client):
            async with _make_session() as db:
                await run_skill_node_verify(
                    "String Bending",
                    [],
                    db=db,
                    user_id=uuid.UUID(user_id),
                )

        # Check governor_calls for the row with feature='skill_verify'
        async with _make_session() as db:
            count = await db.scalar(
                text(
                    "SELECT COUNT(*) FROM governor_calls "
                    "WHERE user_id = :uid AND feature = 'skill_verify'"
                ),
                {"uid": user_id},
            )
        assert count == 1, f"Expected 1 governor_calls row for skill_verify, got {count}"
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_verifier_raises_ai_skill_verifier_error_on_retry_failure():
    """Mock Sonnet to raise RuntimeError on both attempts → raises AISkillVerifierError.

    RuntimeError (e.g., 'no tool_use block') is wrapped by the except Exception block
    and re-raised as AISkillVerifierError. This mirrors breakdown.py's exact behavior:
    APIError propagates directly (to @governed), non-API errors become AISkillVerifierError.
    """
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    count_tokens_result = MagicMock()
    count_tokens_result.input_tokens = 10

    # Return a response with NO tool_use block — triggers RuntimeError inside _call
    usage = MagicMock()
    usage.input_tokens = 10
    usage.output_tokens = 5
    resp = MagicMock()
    resp.content = []  # No tool_use block → RuntimeError
    resp.usage = usage

    client = MagicMock()
    client.messages.create = AsyncMock(return_value=resp)
    client.messages.count_tokens = AsyncMock(return_value=count_tokens_result)

    try:
        with patch("app.ai.skill_verifier.get_client", return_value=client):
            async with _make_session() as db:
                with pytest.raises(AISkillVerifierError):
                    await run_skill_node_verify(
                        "Some Skill",
                        [],
                        db=db,
                        user_id=uuid.UUID(user_id),
                    )
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_verifier_uses_correct_sonnet_model():
    """Assert model=SONNET_MODEL is passed to client.messages.create."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    mock_client = _build_mock_client(
        verdict="yes",
        root="Timing",
        reason="clear timing skill",
    )

    try:
        with patch("app.ai.skill_verifier.get_client", return_value=mock_client):
            async with _make_session() as db:
                await run_skill_node_verify(
                    "Metronome Practice",
                    [],
                    db=db,
                    user_id=uuid.UUID(user_id),
                )

        # Verify the model arg passed to messages.create
        call_kwargs = mock_client.messages.create.call_args
        model_used = call_kwargs.kwargs.get("model") or call_kwargs.args[0] if call_kwargs.args else None
        if model_used is None and call_kwargs.kwargs:
            model_used = call_kwargs.kwargs.get("model")
        assert model_used == SONNET_MODEL, (
            f"Expected model={SONNET_MODEL!r}, got {model_used!r}"
        )
    finally:
        await _cleanup_user(user_id)
