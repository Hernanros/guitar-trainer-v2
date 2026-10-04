"""FLE-106 — the generator verification pass is actually wired into the one
place a breakdown is born.

run_technique_breakdown is the only function that calls Sonnet during
breakdown fetch, and both write paths (the live GET /breakdown endpoint and
breakdown_backfill.py) go through it — so gating there covers both for free
without touching either caller. These tests exercise that wiring end to end
with a faked Anthropic client, the same structure as test_breakdown_truncation.py
and test_breakdown_soft_fail.py (the Anthropic client is faked so the ACTUAL
branch inside run_technique_breakdown runs, not a mock of the function itself).
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.pop("ANTHROPIC_API_KEY", None)

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


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
# Fake Anthropic client
# ---------------------------------------------------------------------------

class _FakeToolUse:
    def __init__(self, payload: dict) -> None:
        self.type = "tool_use"
        self.input = payload


class _FakeMessagesCreateResponse:
    def __init__(self, payload: dict) -> None:
        self.content = [_FakeToolUse(payload)]
        self.stop_reason = "tool_use"
        self.usage = SimpleNamespace(input_tokens=100, output_tokens=200)


class _FakeCountTokensResponse:
    def __init__(self) -> None:
        self.input_tokens = 100


class _FakeMessages:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.create_calls = 0

    async def create(self, **kwargs: Any) -> _FakeMessagesCreateResponse:
        self.create_calls += 1
        return _FakeMessagesCreateResponse(self._payload)

    async def count_tokens(self, **kwargs: Any) -> _FakeCountTokensResponse:
        return _FakeCountTokensResponse()


class _FakeClient:
    def __init__(self, payload: dict) -> None:
        self.messages = _FakeMessages(payload)


def _install_fake_client(monkeypatch, payload: dict) -> _FakeClient:
    import app.ai.breakdown as breakdown_module

    fake = _FakeClient(payload)
    monkeypatch.setattr(breakdown_module, "get_client", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Fixture payloads
# ---------------------------------------------------------------------------

_STANDARD_TUNING = ["E", "A", "D", "G", "B", "e"]


def _bad_payload() -> dict:
    """A one-beat bar that both under-fills 4/4 (METER) and plays a note
    outside its own declared Em chord (OWN_CHORDS — fret 1 on the low E
    string sounds F, foreign to {E, G, B})."""
    return {
        "tab": {
            "measures": [
                {
                    "beats": [{"notes": [{"string": 6, "fret": 1, "duration": "quarter"}]}],
                    "time_signature": "4/4",
                }
            ],
            "tuning": _STANDARD_TUNING,
        },
        "chords": [{"name": "Em", "positions": [{"string": 6, "fret": 0}]}],
        "technique_notes": [],
    }


def _good_payload() -> dict:
    """Open Em struck clean — passes every recording-free check."""
    return {
        "tab": {
            "measures": [
                {
                    "beats": [
                        {
                            "notes": [
                                {"string": 6, "fret": 0, "duration": "half"},
                                {"string": 5, "fret": 2, "duration": "half"},
                                {"string": 4, "fret": 2, "duration": "half"},
                            ]
                        },
                        {"notes": [{"string": 3, "fret": 0, "duration": "quarter"}]},
                        {"notes": [{"string": 2, "fret": 0, "duration": "quarter"}]},
                    ],
                    "time_signature": "4/4",
                }
            ],
            "tuning": _STANDARD_TUNING,
        },
        "chords": [{"name": "Em", "positions": [{"string": 6, "fret": 0}]}],
        "technique_notes": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_defective_tab_raises_tab_verification_error(monkeypatch):
    from app.ai.breakdown import (
        AIBreakdownError,
        TabVerificationError,
        run_technique_breakdown,
    )

    fake = _install_fake_client(monkeypatch, _bad_payload())

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        async with _make_session() as db:
            with pytest.raises(TabVerificationError) as excinfo:
                await run_technique_breakdown(
                    "Test Song",
                    "Test Artist",
                    [{"id": str(uuid.uuid4()), "name": "Rhythm"}],
                    0.5,
                    db=db,
                    user_id=uuid.UUID(user_id),
                )

        message = str(excinfo.value)
        assert "METER" in message or "OWN_CHORDS" in message, (
            f"message must name which check failed, got: {message!r}"
        )

        # Deterministic at a fixed input — a second identical call would fail
        # identically, so this must not be retried in-process (next tap retries).
        assert fake.messages.create_calls == 1, (
            f"expected no in-process retry on a verification failure, "
            f"saw {fake.messages.create_calls} calls"
        )

        assert isinstance(excinfo.value, AIBreakdownError), (
            "TabVerificationError must subclass AIBreakdownError so the "
            "endpoint's existing 503 handling and retry-on-next-tap contract hold."
        )
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_a_clean_tab_passes_through_unblocked(monkeypatch):
    from app.ai.breakdown import run_technique_breakdown

    _install_fake_client(monkeypatch, _good_payload())

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        async with _make_session() as db:
            breakdown = await run_technique_breakdown(
                "Test Song",
                "Test Artist",
                [{"id": str(uuid.uuid4()), "name": "Rhythm"}],
                0.5,
                db=db,
                user_id=uuid.UUID(user_id),
            )
        assert breakdown.chords[0].name == "Em"
    finally:
        await _cleanup_user(user_id)
