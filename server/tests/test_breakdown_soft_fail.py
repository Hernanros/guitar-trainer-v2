"""Landmine #3 soft-fail unit tests (Plan 04.1-01, Task 3).

Tests the INTERNAL soft-fail wrapper in run_technique_breakdown: when Sonnet
emits a raw tool_use.input dict that Breakdown.model_validate rejects due to
drills-shape constraints (min_length<2, max_length>4, target_bpm<=start_bpm),
the function logs a warning, strips the `drills` key, and re-parses. Users
still see tab/chords/technique_notes — the endpoint NEVER 500s from a drills
validation failure.

If the second parse ALSO fails (structural problem in tab/chords/technique_notes,
i.e. pre-existing Phase 3 failure surface), AIBreakdownError propagates normally.

These tests mock the Anthropic client directly (unlike test_breakdowns_mocked.py
which patches run_technique_breakdown as a whole) so we exercise the ACTUAL
soft-fail branch inside run_technique_breakdown.
"""
from __future__ import annotations

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
# Test DB helpers (mirror test_breakdowns_mocked.py)
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
# Fake Anthropic client — returns pre-canned tool_use.input dicts
# ---------------------------------------------------------------------------

def _valid_tab_dict() -> dict:
    """Minimal valid Tab dict shape."""
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


def _valid_drill_dict(**overrides) -> dict:
    d = {
        "name": "Isolate slide",
        "target_skill_temp_id": str(uuid.uuid4()),
        "song_specific": True,
        "what": "Play the slide alone.",
        "tab_snippet": _valid_tab_dict(),
        "start_bpm": 60,
        "target_bpm": 70,
        "repetitions": 20,
        "success_criterion": "Clean on the beat.",
    }
    d.update(overrides)
    return d


def _breakdown_dict_with_drills(drills: list[dict]) -> dict:
    return {
        "tab": _valid_tab_dict(),
        "chords": [],
        "technique_notes": [],
        "drills": drills,
    }


class _FakeToolUse:
    """Mimics an Anthropic tool_use content block."""
    def __init__(self, payload: dict) -> None:
        self.type = "tool_use"
        self.input = payload


class _FakeMessagesCreateResponse:
    """Mimics an Anthropic messages.create() response."""
    def __init__(self, payload: dict) -> None:
        self.content = [_FakeToolUse(payload)]
        # record_actuals reads usage.input_tokens / usage.output_tokens
        self.usage = SimpleNamespace(input_tokens=100, output_tokens=200)


class _FakeCountTokensResponse:
    """Mimics client.messages.count_tokens response."""
    def __init__(self) -> None:
        self.input_tokens = 100


class _FakeMessages:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def create(self, **kwargs: Any) -> _FakeMessagesCreateResponse:
        return _FakeMessagesCreateResponse(self._payload)

    async def count_tokens(self, **kwargs: Any) -> _FakeCountTokensResponse:
        return _FakeCountTokensResponse()


class _FakeClient:
    def __init__(self, payload: dict) -> None:
        self.messages = _FakeMessages(payload)


def _install_fake_client(monkeypatch, payload: dict) -> None:
    """Replace get_client() with a fake that returns the canned Sonnet payload."""
    import app.ai.breakdown as breakdown_module
    monkeypatch.setattr(breakdown_module, "get_client", lambda: _FakeClient(payload))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_soft_fail_when_sonnet_emits_one_drill(monkeypatch, caplog):
    """LANDMINE #3: Sonnet emits 1 drill (below min_length=2). Soft-fail to drills=[]."""
    import logging
    from app.ai.breakdown import run_technique_breakdown

    payload = _breakdown_dict_with_drills([_valid_drill_dict()])  # only 1 drill
    _install_fake_client(monkeypatch, payload)

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        with caplog.at_level(logging.WARNING, logger="app.ai.breakdown"):
            async with _make_session() as db:
                breakdown = await run_technique_breakdown(
                    "Lenny",
                    "SRV",
                    [{"id": str(uuid.uuid4()), "name": "Slide"}],
                    0.5,
                    db=db,
                    user_id=uuid.UUID(user_id),
                )

        assert breakdown.drills == [], (
            "Landmine #3: 1-drill emit must soft-fail to drills=[], not raise"
        )
        assert any("drills" in r.message.lower() for r in caplog.records), (
            "Expected a warning log about drills validation failure"
        )
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_soft_fail_when_sonnet_emits_five_drills(monkeypatch, caplog):
    """LANDMINE #3: Sonnet emits 5 drills (above max_length=4). Soft-fail to drills=[]."""
    import logging
    from app.ai.breakdown import run_technique_breakdown

    payload = _breakdown_dict_with_drills([_valid_drill_dict() for _ in range(5)])
    _install_fake_client(monkeypatch, payload)

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        with caplog.at_level(logging.WARNING, logger="app.ai.breakdown"):
            async with _make_session() as db:
                breakdown = await run_technique_breakdown(
                    "Lenny",
                    "SRV",
                    [{"id": str(uuid.uuid4()), "name": "Slide"}],
                    0.5,
                    db=db,
                    user_id=uuid.UUID(user_id),
                )

        assert breakdown.drills == [], (
            "Landmine #3: 5-drill emit must soft-fail to drills=[]"
        )
        assert any("drills" in r.message.lower() for r in caplog.records)
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_soft_fail_when_target_bpm_le_start_bpm(monkeypatch, caplog):
    """LANDMINE #3 (W2): drill target_bpm <= start_bpm triggers @model_validator, soft-fail."""
    import logging
    from app.ai.breakdown import run_technique_breakdown

    bad_drill = _valid_drill_dict(start_bpm=70, target_bpm=70)  # equal → invalid
    good_drill = _valid_drill_dict()
    payload = _breakdown_dict_with_drills([bad_drill, good_drill])
    _install_fake_client(monkeypatch, payload)

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        with caplog.at_level(logging.WARNING, logger="app.ai.breakdown"):
            async with _make_session() as db:
                breakdown = await run_technique_breakdown(
                    "Lenny",
                    "SRV",
                    [{"id": str(uuid.uuid4()), "name": "Slide"}],
                    0.5,
                    db=db,
                    user_id=uuid.UUID(user_id),
                )

        assert breakdown.drills == [], (
            "Landmine #3: target_bpm <= start_bpm must soft-fail to drills=[]"
        )
        assert any("drills" in r.message.lower() for r in caplog.records)
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_valid_drills_pass_through_soft_fail_layer(monkeypatch):
    """Sanity: a valid 2-drill payload passes through — no soft-fail engaged."""
    from app.ai.breakdown import run_technique_breakdown

    payload = _breakdown_dict_with_drills([_valid_drill_dict(), _valid_drill_dict()])
    _install_fake_client(monkeypatch, payload)

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        async with _make_session() as db:
            breakdown = await run_technique_breakdown(
                "Lenny",
                "SRV",
                [{"id": str(uuid.uuid4()), "name": "Slide"}],
                0.5,
                db=db,
                user_id=uuid.UUID(user_id),
            )
        assert len(breakdown.drills) == 2, (
            "Happy path: 2 valid drills should reach the caller"
        )
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_structural_failure_still_raises(monkeypatch):
    """If the SECOND parse (drills-stripped) ALSO fails, AIBreakdownError propagates.

    Sonnet emitting a broken tab structure IS a real failure — do not silently
    return an empty breakdown for pre-existing Phase 3 failure modes.
    """
    from app.ai.breakdown import AIBreakdownError, run_technique_breakdown

    # tab is invalid (wrong type for measures) — will fail Breakdown parse regardless of drills
    payload = {
        "tab": {"measures": "not a list at all", "tuning": ["E", "A", "D", "G", "B", "e"]},
        "chords": [],
        "technique_notes": [],
        "drills": [_valid_drill_dict()],  # also drills-invalid, so both parses fail
    }
    _install_fake_client(monkeypatch, payload)

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        async with _make_session() as db:
            with pytest.raises(AIBreakdownError):
                await run_technique_breakdown(
                    "Lenny",
                    "SRV",
                    [{"id": str(uuid.uuid4()), "name": "Slide"}],
                    0.5,
                    db=db,
                    user_id=uuid.UUID(user_id),
                )
    finally:
        await _cleanup_user(user_id)
