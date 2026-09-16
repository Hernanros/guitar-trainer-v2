"""FLE-43: max_tokens truncation is diagnosed, not disguised.

The FLE-17 §3 eval re-run hit a live 503 on `Wonderwall`: Sonnet ran out of
output budget at max_tokens=8192 and emitted a tool_use input containing only
`tab`, cut off mid-note. The endpoint surfaced it as a pydantic
"chords / technique_notes Field required" — a message that names the missing
keys and says nothing about why they are missing. Nothing logged `stop_reason`,
so the cause had to be inferred from token arithmetic across the whole run.

These tests pin the three asks of FLE-43:
  1. `stop_reason` is logged on every breakdown call.
  2. The ceiling is sized against the dense-song worst case, and stays below the
     Anthropic SDK's non-streaming cliff (above which the SDK raises ValueError).
  3. A truncated tool_use raises BreakdownTruncatedError, whose message names the
     budget as the cause — and which is NOT flattened back into a generic
     AIBreakdownError by the outer handler.

Structure mirrors test_breakdown_soft_fail.py: the Anthropic client is faked so
the ACTUAL branch inside run_technique_breakdown is exercised, rather than
patching run_technique_breakdown as a whole.
"""
from __future__ import annotations

import logging
import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

# Ensure no live API key leaks into this test suite
os.environ.pop("ANTHROPIC_API_KEY", None)

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# ---------------------------------------------------------------------------
# Test DB helpers (mirror test_breakdown_soft_fail.py)
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
                "INSERT INTO users (id, preferences) VALUES (:uid, '{}'::jsonb) "
                "ON CONFLICT DO NOTHING"
            ),
            {"uid": user_id},
        )
        await db.commit()


async def _cleanup_user(user_id: str) -> None:
    async with _make_session() as db:
        await db.execute(text(f"DELETE FROM governor_calls WHERE user_id='{user_id}'"))
        await db.execute(text(f"DELETE FROM users WHERE id='{user_id}'"))
        await db.commit()


# ---------------------------------------------------------------------------
# Fake Anthropic client — lets each test pick stop_reason and the tool_use input
# ---------------------------------------------------------------------------

def _valid_tab_dict() -> dict:
    return {
        "measures": [
            {
                "beats": [
                    {"notes": [{"string": 1, "fret": 0, "duration": "quarter"}]}
                ],
                "time_signature": "4/4",
            }
        ],
        "tuning": ["E", "A", "D", "G", "B", "e"],
    }


def _truncated_payload() -> dict:
    """What Wonderwall actually came back as: `tab` only, everything else lost.

    Reproduces the shape from 04.1-07-EVAL-RERUN-RESULTS.md §4 — the two required
    keys `chords` and `technique_notes` never made it into the output budget.
    """
    return {"tab": _valid_tab_dict()}


class _FakeToolUse:
    def __init__(self, payload: dict) -> None:
        self.type = "tool_use"
        self.input = payload


class _FakeMessagesCreateResponse:
    def __init__(self, payload: dict, stop_reason: str, output_tokens: int) -> None:
        self.content = [_FakeToolUse(payload)]
        self.stop_reason = stop_reason
        self.usage = SimpleNamespace(
            input_tokens=100, output_tokens=output_tokens
        )


class _FakeCountTokensResponse:
    def __init__(self) -> None:
        self.input_tokens = 100


class _FakeMessages:
    def __init__(self, payload: dict, stop_reason: str, output_tokens: int) -> None:
        self._payload = payload
        self._stop_reason = stop_reason
        self._output_tokens = output_tokens
        self.create_calls = 0

    async def create(self, **kwargs: Any) -> _FakeMessagesCreateResponse:
        self.create_calls += 1
        self.last_kwargs = kwargs
        return _FakeMessagesCreateResponse(
            self._payload, self._stop_reason, self._output_tokens
        )

    async def count_tokens(self, **kwargs: Any) -> _FakeCountTokensResponse:
        return _FakeCountTokensResponse()


class _FakeClient:
    def __init__(self, payload: dict, stop_reason: str, output_tokens: int) -> None:
        self.messages = _FakeMessages(payload, stop_reason, output_tokens)


def _install_fake_client(
    monkeypatch,
    payload: dict,
    stop_reason: str = "tool_use",
    output_tokens: int = 200,
) -> _FakeClient:
    """Replace get_client() with a fake. Returns it so tests can read call counts."""
    import app.ai.breakdown as breakdown_module

    fake = _FakeClient(payload, stop_reason, output_tokens)
    monkeypatch.setattr(breakdown_module, "get_client", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Ask #2 — ceiling sizing. No DB, no network: pure constant assertions.
# ---------------------------------------------------------------------------

def test_max_output_tokens_clears_the_dense_song_worst_case():
    """The ceiling must exceed the worst case FLE-43 sized, not the average.

    Wonderwall's tab alone consumed the full 8192. Worst case is ~8,000 for a
    dense tab plus ~4,000-6,000 for chords/technique_notes/drills = ~14,000.
    """
    from app.ai.breakdown import _MAX_OUTPUT_TOKENS

    assert _MAX_OUTPUT_TOKENS > 14000, (
        "FLE-43: the ceiling is sized against the dense-song worst case (~14,000 "
        "output tokens), not run 2's 5,669-token average. Lowering it below that "
        "re-opens the Wonderwall 503."
    )


def test_max_output_tokens_stays_under_the_sdk_nonstreaming_cliff():
    """Above the cliff the SDK raises ValueError instead of calling the API.

    anthropic._base_client._calculate_nonstreaming_timeout refuses any
    non-streaming request whose max_tokens implies >10 minutes of generation
    (60*60 * max_tokens / 128_000 > 60*10). That ValueError would be swallowed by
    this module's generic handler and resurface as a nondescript
    AIBreakdownError — the exact failure mode FLE-43 exists to eliminate. Raising
    the ceiling past this point requires switching to client.messages.stream().
    """
    from app.ai.breakdown import _MAX_OUTPUT_TOKENS, _SDK_NONSTREAMING_MAX_TOKENS

    assert _MAX_OUTPUT_TOKENS <= _SDK_NONSTREAMING_MAX_TOKENS, (
        f"max_tokens={_MAX_OUTPUT_TOKENS} exceeds the SDK's non-streaming cliff "
        f"of {_SDK_NONSTREAMING_MAX_TOKENS}. Switch this call to streaming first."
    )


def test_sdk_nonstreaming_cliff_matches_the_installed_sdk():
    """Pin our constant to the SDK's real behaviour, so an SDK bump can't drift it.

    Probes the private helper directly: our ceiling must be accepted and the
    value one above our recorded cliff must be rejected.
    """
    from anthropic import AsyncAnthropic

    from app.ai.breakdown import _MAX_OUTPUT_TOKENS, _SDK_NONSTREAMING_MAX_TOKENS

    client = AsyncAnthropic(api_key="test-key-not-used")

    # Our ceiling is accepted by the SDK's guard.
    client._calculate_nonstreaming_timeout(_MAX_OUTPUT_TOKENS, None)

    # One token past the recorded cliff is refused.
    with pytest.raises(ValueError):
        client._calculate_nonstreaming_timeout(_SDK_NONSTREAMING_MAX_TOKENS + 1, None)


def test_timeout_covers_the_token_ceiling():
    """FLE-18 interaction: a bigger budget means a longer generation.

    The eval measured ~5,669 output tokens in ~75.9s = ~75 tok/s. The timeout
    must cover a response that runs all the way to the ceiling, or the first
    attempt burns a full Sonnet call before the retry starts.
    """
    from app.ai.breakdown import _DEFAULT_TIMEOUT_SECONDS, _MAX_OUTPUT_TOKENS

    measured_tokens_per_second = 5669 / 75.9
    seconds_to_fill_the_ceiling = _MAX_OUTPUT_TOKENS / measured_tokens_per_second

    assert _DEFAULT_TIMEOUT_SECONDS >= seconds_to_fill_the_ceiling, (
        f"_DEFAULT_TIMEOUT_SECONDS={_DEFAULT_TIMEOUT_SECONDS} is under the "
        f"~{seconds_to_fill_the_ceiling:.0f}s a max_tokens={_MAX_OUTPUT_TOKENS} "
        f"response takes at the eval-measured ~75 tok/s. Raising the token "
        f"ceiling without raising this re-introduces the FLE-18 timeout 503."
    )


# ---------------------------------------------------------------------------
# Asks #1 and #3 — runtime behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_truncated_tool_use_raises_a_named_truncation_error(monkeypatch):
    """The Wonderwall case: stop_reason=max_tokens with a partial tool_use input.

    Must raise BreakdownTruncatedError naming the output budget — NOT a pydantic
    "chords / technique_notes Field required".
    """
    from app.ai.breakdown import (
        AIBreakdownError,
        BreakdownTruncatedError,
        _MAX_OUTPUT_TOKENS,
        run_technique_breakdown,
    )

    fake = _install_fake_client(
        monkeypatch,
        _truncated_payload(),
        stop_reason="max_tokens",
        output_tokens=_MAX_OUTPUT_TOKENS,
    )

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        async with _make_session() as db:
            with pytest.raises(BreakdownTruncatedError) as excinfo:
                await run_technique_breakdown(
                    "Wonderwall",
                    "Oasis",
                    [{"id": str(uuid.uuid4()), "name": "Strumming"}],
                    0.5,
                    db=db,
                    user_id=uuid.UUID(user_id),
                )

        message = str(excinfo.value)
        assert "budget" in message.lower() or "max_tokens" in message.lower(), (
            f"FLE-43 ask #3: the message must name the CAUSE (ran out of output "
            f"budget), not just the symptom. Got: {message!r}"
        )
        assert "Field required" not in message, (
            "The pydantic symptom must not be what the caller sees."
        )

        # Truncation is deterministic at a fixed max_tokens — retrying the identical
        # call would truncate identically, so it must not be retried in-process.
        assert fake.messages.create_calls == 1, (
            f"Expected no retry on truncation, saw {fake.messages.create_calls} calls"
        )

        # Endpoint compatibility: breakdowns.py catches AIBreakdownError → 503.
        assert isinstance(excinfo.value, AIBreakdownError), (
            "BreakdownTruncatedError must subclass AIBreakdownError so the "
            "endpoint's existing 503 handling and retry-on-next-tap contract hold."
        )
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_stop_reason_is_logged_on_every_call(monkeypatch, caplog):
    """FLE-43 ask #1: stop_reason must be readable off a log line, always.

    Nothing logged it before, which is why the truncation had to be inferred from
    token arithmetic. The healthy path logs it too — not only the failure path.
    """
    from app.ai.breakdown import run_technique_breakdown

    payload = {
        "tab": _valid_tab_dict(),
        "chords": [],
        "technique_notes": [],
    }
    _install_fake_client(
        monkeypatch, payload, stop_reason="tool_use", output_tokens=5669
    )

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        with caplog.at_level(logging.INFO, logger="app.ai.breakdown"):
            async with _make_session() as db:
                await run_technique_breakdown(
                    "Lenny",
                    "SRV",
                    [{"id": str(uuid.uuid4()), "name": "Slide"}],
                    0.5,
                    db=db,
                    user_id=uuid.UUID(user_id),
                )

        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "stop_reason=tool_use" in logged, (
            f"Expected stop_reason on a log line for the healthy path. Got: {logged!r}"
        )
        assert "output_tokens=5669" in logged, (
            "Log the output token count alongside stop_reason — together they are "
            "what made the FLE-43 truncation diagnosable after the fact."
        )
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_the_request_actually_sends_the_raised_ceiling(monkeypatch):
    """Guard against the constant existing but the call site still hardcoding 8192."""
    from app.ai.breakdown import _MAX_OUTPUT_TOKENS, run_technique_breakdown

    payload = {
        "tab": _valid_tab_dict(),
        "chords": [],
        "technique_notes": [],
    }
    fake = _install_fake_client(monkeypatch, payload)

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        async with _make_session() as db:
            await run_technique_breakdown(
                "Lenny",
                "SRV",
                [{"id": str(uuid.uuid4()), "name": "Slide"}],
                0.5,
                db=db,
                user_id=uuid.UUID(user_id),
            )

        assert fake.messages.last_kwargs["max_tokens"] == _MAX_OUTPUT_TOKENS, (
            "The call site must send _MAX_OUTPUT_TOKENS, not a hardcoded literal."
        )
    finally:
        await _cleanup_user(user_id)
