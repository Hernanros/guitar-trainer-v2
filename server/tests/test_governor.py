"""Tests for Phase 4 cost governor (governor.py + @governed decorator).

TDD RED phase: all tests written before governor.py exists.
Tests cover:
  - test_governor_inserts_row_with_prompt_tokens_estimated
  - test_governor_records_actual_usage_on_success
  - test_governor_records_error_code_on_failure
  - test_governor_blocks_fourth_breakdown
  - test_governor_seven_day_window_slides
  - test_governor_uncapped_when_cap_is_none
  - test_governor_extracts_user_id_from_kwargs
  - test_anthropic_429_maps_to_quota_exceeded_error
  - test_endpoint_returns_429_breakdown_capped
  - test_endpoint_returns_503_fletcher_out
  - test_governor_record_estimate_populates_prompt_tokens (D-03 / SC-1)
  - test_endpoint_429_days_remaining_is_integer

Runs against a REAL Postgres test database (governor_calls requires real rows).
Patches client.messages.create to avoid real Anthropic calls.
Patches client.messages.count_tokens to return a controlled estimate.

All tests use the session-scoped event loop from conftest.py.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.models.db import GovernorCall, User
from app.models.song import Breakdown, Tab, Measure, Beat, Note, Chord, ChordPosition, TechniqueNote

# ---------------------------------------------------------------------------
# Ensure no live API key leaks into this test suite
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Canned data
# ---------------------------------------------------------------------------

def _canned_breakdown() -> Breakdown:
    return Breakdown(
        tab=Tab(
            measures=[
                Measure(
                    beats=[
                        Beat(notes=[Note(string=1, fret=0, duration="quarter")]),
                        Beat(notes=[Note(string=2, fret=2, duration="quarter")]),
                        Beat(notes=[Note(string=3, fret=2, duration="quarter")]),
                        Beat(notes=[Note(string=4, fret=0, duration="quarter")]),
                    ],
                    time_signature="4/4",
                )
            ],
            tuning=["E", "A", "D", "G", "B", "e"],
        ),
        chords=[
            Chord(
                name="E7",
                positions=[
                    ChordPosition(string=1, fret=0),
                    ChordPosition(string=2, fret=0),
                    ChordPosition(string=3, fret=1, finger=1),
                    ChordPosition(string=4, fret=0),
                    ChordPosition(string=5, fret=2, finger=2),
                    ChordPosition(string=6, fret=0),
                ],
                base_fret=1,
            )
        ],
        technique_notes=[
            TechniqueNote(
                heading="Shuffle Feel",
                body="Long-short. If it sounds even, you're rushing.",
            )
        ],
    )


def _fake_sonnet_response(input_tokens: int = 100, output_tokens: int = 200) -> Any:
    """Build a fake Anthropic response that mimics client.messages.create() output."""
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.input = _canned_breakdown().model_dump()

    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens

    resp = MagicMock()
    resp.content = [tool_use]
    resp.usage = usage
    return resp


def _fake_count_tokens_response(input_tokens: int = 42) -> Any:
    """Build a fake count_tokens response."""
    est = MagicMock()
    est.input_tokens = input_tokens
    return est


# ---------------------------------------------------------------------------
# DB seed / cleanup helpers
# ---------------------------------------------------------------------------

async def _seed_user(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        text(
            "INSERT INTO users (id, preferences) "
            "VALUES (:uid, '{}'::jsonb) ON CONFLICT DO NOTHING"
        ),
        {"uid": user_id},
    )
    await db.commit()


async def _seed_song(db: AsyncSession, user_id: str) -> int:
    """Insert a minimal song row for this user and return the song id."""
    await db.execute(
        text(
            "INSERT INTO songs (title, artist, genre, difficulty, bpm, key, breakdown, user_id, category) "
            "VALUES ('Test Song', 'Test Artist', 'Blues', 'intermediate', 80, 'E', "
            "        '{}'::jsonb, :uid, 'working_on')"
        ),
        {"uid": user_id},
    )
    result = await db.execute(
        text("SELECT id FROM songs WHERE user_id = :uid ORDER BY id DESC LIMIT 1"),
        {"uid": user_id},
    )
    song_id = result.scalar_one()
    await db.commit()
    return song_id


async def _insert_governor_call(
    db: AsyncSession,
    user_id: str,
    feature: str = "breakdown",
    created_at: datetime | None = None,
) -> str:
    """Insert a governor_calls row directly for setup. Returns the row id."""
    row_id = str(uuid.uuid4())
    ts = created_at or datetime.now(timezone.utc)
    await db.execute(
        text(
            "INSERT INTO governor_calls (id, user_id, feature, model, created_at) "
            "VALUES (:id, :uid, :feature, 'claude-sonnet-4-6', :ts)"
        ),
        {"id": row_id, "uid": user_id, "feature": feature, "ts": ts},
    )
    await db.commit()
    return row_id


async def _cleanup_user(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        text("DELETE FROM governor_calls WHERE user_id = :uid"), {"uid": user_id}
    )
    await db.execute(
        text("DELETE FROM song_skills WHERE song_id IN (SELECT id FROM songs WHERE user_id = :uid)"),
        {"uid": user_id},
    )
    await db.execute(
        text("DELETE FROM songs WHERE user_id = :uid"), {"uid": user_id}
    )
    await db.execute(
        text("DELETE FROM skill_nodes WHERE user_id = :uid"), {"uid": user_id}
    )
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await db.commit()


# ---------------------------------------------------------------------------
# Shared mock factories
# ---------------------------------------------------------------------------

def _patch_client_for_success(monkeypatch, input_tokens: int = 100, output_tokens: int = 200) -> None:
    """Patch app.ai.breakdown's get_client() to return a mock that succeeds."""
    import app.ai.breakdown as breakdown_mod

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_fake_sonnet_response(input_tokens, output_tokens)
    )
    mock_client.messages.count_tokens = AsyncMock(
        return_value=_fake_count_tokens_response(42)
    )
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)


# ---------------------------------------------------------------------------
# Tests: governor.py unit behavior (direct DB + decorator calls)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_governor_blocks_fourth_breakdown(monkeypatch):
    """4th call to @governed(cap=3) for same user raises BudgetExceededError — no Sonnet dispatch."""
    from app.ai.governor import BudgetExceededError
    import app.ai.breakdown as breakdown_mod

    user_id = str(uuid.uuid4())
    sonnet_call_count = {"n": 0}

    mock_client = MagicMock()

    async def _track_create(**kwargs):
        sonnet_call_count["n"] += 1
        return _fake_sonnet_response()

    mock_client.messages.create = _track_create
    mock_client.messages.count_tokens = AsyncMock(return_value=_fake_count_tokens_response(42))
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _seed_song(db, user_id)

    from app.ai.breakdown import run_technique_breakdown

    # 3 successful calls
    for _ in range(3):
        async with _make_session() as db:
            result = await run_technique_breakdown(
                "Sweet Home Chicago", "Robert Johnson", [], 0.3,
                db=db, user_id=uuid.UUID(user_id),
            )
        assert result is not None

    assert sonnet_call_count["n"] == 3, (
        f"Expected 3 Sonnet calls after 3 successful breakdowns, got {sonnet_call_count['n']}"
    )

    # 4th call — must raise BudgetExceededError without dispatching Sonnet
    async with _make_session() as db:
        with pytest.raises(BudgetExceededError) as exc_info:
            await run_technique_breakdown(
                "Sweet Home Chicago", "Robert Johnson", [], 0.3,
                db=db, user_id=uuid.UUID(user_id),
            )

    assert exc_info.value.feature == "breakdown"
    assert exc_info.value.resets_at is not None, "BudgetExceededError must have resets_at"
    # resets_at should be parse-able as ISO
    from datetime import datetime
    dt = datetime.fromisoformat(exc_info.value.resets_at)
    assert dt > datetime.now(timezone.utc), "resets_at should be in the future"

    # Sonnet call count should still be 3 — no 4th dispatch
    assert sonnet_call_count["n"] == 3, (
        f"4th call should not dispatch Sonnet — expected count=3, got {sonnet_call_count['n']}"
    )

    # Cleanup
    async with _make_session() as db:
        await _cleanup_user(db, user_id)


@pytest.mark.asyncio
async def test_governor_inserts_row_with_prompt_tokens_estimated(monkeypatch):
    """On 1st call: governor_calls table has 1 row after dispatch."""
    import app.ai.breakdown as breakdown_mod

    user_id = str(uuid.uuid4())
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_fake_sonnet_response())
    mock_client.messages.count_tokens = AsyncMock(return_value=_fake_count_tokens_response(42))
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _seed_song(db, user_id)

    from app.ai.breakdown import run_technique_breakdown

    async with _make_session() as db:
        await run_technique_breakdown(
            "Blackbird", "The Beatles", [], 0.5,
            db=db, user_id=uuid.UUID(user_id),
        )

    # Verify governor_calls row
    async with _make_session() as db:
        result = await db.execute(
            text(
                "SELECT COUNT(*) as cnt FROM governor_calls "
                "WHERE user_id = :uid AND feature = 'breakdown'"
            ),
            {"uid": user_id},
        )
        count = result.scalar_one()
    assert count == 1, f"Expected 1 governor_calls row, got {count}"

    # Cleanup
    async with _make_session() as db:
        await _cleanup_user(db, user_id)


@pytest.mark.asyncio
async def test_governor_records_actual_usage_on_success(monkeypatch):
    """After successful dispatch: prompt_tokens_actual + output_tokens_actual populated."""
    import app.ai.breakdown as breakdown_mod

    user_id = str(uuid.uuid4())
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_fake_sonnet_response(input_tokens=150, output_tokens=300)
    )
    mock_client.messages.count_tokens = AsyncMock(return_value=_fake_count_tokens_response(42))
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _seed_song(db, user_id)

    from app.ai.breakdown import run_technique_breakdown

    async with _make_session() as db:
        await run_technique_breakdown(
            "Blackbird", "The Beatles", [], 0.5,
            db=db, user_id=uuid.UUID(user_id),
        )

    async with _make_session() as db:
        result = await db.execute(
            text(
                "SELECT prompt_tokens_actual, output_tokens_actual FROM governor_calls "
                "WHERE user_id = :uid AND feature = 'breakdown' LIMIT 1"
            ),
            {"uid": user_id},
        )
        row = result.one_or_none()

    assert row is not None, "No governor_calls row found"
    assert row.prompt_tokens_actual == 150, (
        f"Expected prompt_tokens_actual=150, got {row.prompt_tokens_actual}"
    )
    assert row.output_tokens_actual == 300, (
        f"Expected output_tokens_actual=300, got {row.output_tokens_actual}"
    )

    async with _make_session() as db:
        await _cleanup_user(db, user_id)


@pytest.mark.asyncio
async def test_governor_records_error_code_on_failure(monkeypatch):
    """On wrapped fn raising an error: governor_calls row has error_code, prompt_tokens_actual is NULL."""
    import app.ai.breakdown as breakdown_mod
    from app.ai.breakdown import AIBreakdownError

    user_id = str(uuid.uuid4())
    mock_client = MagicMock()

    async def _fail(**kwargs):
        raise RuntimeError("forced test failure")

    mock_client.messages.create = _fail
    mock_client.messages.count_tokens = AsyncMock(return_value=_fake_count_tokens_response(42))
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _seed_song(db, user_id)

    from app.ai.breakdown import run_technique_breakdown

    async with _make_session() as db:
        with pytest.raises(AIBreakdownError):
            await run_technique_breakdown(
                "Blackbird", "The Beatles", [], 0.5,
                db=db, user_id=uuid.UUID(user_id),
            )

    async with _make_session() as db:
        result = await db.execute(
            text(
                "SELECT error_code, prompt_tokens_actual FROM governor_calls "
                "WHERE user_id = :uid AND feature = 'breakdown' LIMIT 1"
            ),
            {"uid": user_id},
        )
        row = result.one_or_none()

    assert row is not None, "No governor_calls row found after failure"
    assert row.error_code is not None, "error_code should be set on failure"
    assert row.prompt_tokens_actual is None, "prompt_tokens_actual should be NULL on failure"

    async with _make_session() as db:
        await _cleanup_user(db, user_id)


@pytest.mark.asyncio
async def test_governor_seven_day_window_slides(monkeypatch):
    """Call that would be the 4th succeeds if the oldest of 3 calls is >7 days old."""
    import app.ai.breakdown as breakdown_mod

    user_id = str(uuid.uuid4())
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_fake_sonnet_response())
    mock_client.messages.count_tokens = AsyncMock(return_value=_fake_count_tokens_response(42))
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _seed_song(db, user_id)

    # Insert 3 governor_calls rows with the oldest one >7 days ago
    eight_days_ago = datetime.now(timezone.utc) - timedelta(days=8)
    two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
    one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)

    async with _make_session() as db:
        await _insert_governor_call(db, user_id, created_at=eight_days_ago)
        await _insert_governor_call(db, user_id, created_at=two_days_ago)
        await _insert_governor_call(db, user_id, created_at=one_day_ago)

    # The oldest (8 days ago) is outside the 7-day window → effective count = 2 → 4th call should succeed
    from app.ai.breakdown import run_technique_breakdown

    async with _make_session() as db:
        result = await run_technique_breakdown(
            "Wonderwall", "Oasis", [], 0.2,
            db=db, user_id=uuid.UUID(user_id),
        )
    assert result is not None, "Expected successful breakdown when oldest call is aged out"

    async with _make_session() as db:
        await _cleanup_user(db, user_id)


@pytest.mark.asyncio
async def test_governor_uncapped_when_cap_is_none():
    """@governed(cap=None) never raises BudgetExceededError regardless of call count."""
    from app.ai.governor import BudgetExceededError, governed

    user_id = str(uuid.uuid4())

    # Create a dummy wrapped fn that just returns "ok"
    @governed(feature="test_uncapped", cap=None)
    async def _dummy_fn(*, db: AsyncSession, user_id, **kwargs) -> str:
        return "ok"

    async with _make_session() as db:
        await _seed_user(db, str(user_id))

    # Insert 100 rows manually — cap=None should never trip
    async with _make_session() as db:
        for _ in range(5):
            await _insert_governor_call(db, str(user_id), feature="test_uncapped")

    # Call with cap=None — should succeed without raising BudgetExceededError
    async with _make_session() as db:
        try:
            result = await _dummy_fn(db=db, user_id=uuid.UUID(str(user_id)))
            assert result == "ok", f"Expected 'ok', got {result}"
        except BudgetExceededError:
            pytest.fail("BudgetExceededError raised for cap=None — should never raise")

    async with _make_session() as db:
        await _cleanup_user(db, str(user_id))


@pytest.mark.asyncio
async def test_governor_extracts_user_id_from_kwargs():
    """@governed raises TypeError with clear message if wrapped fn called without user_id kwarg."""
    from app.ai.governor import governed

    @governed(feature="test_no_user_id", cap=3)
    async def _needs_user_id(*, db: AsyncSession, user_id, **kwargs) -> str:
        return "ok"

    async with _make_session() as db:
        with pytest.raises(TypeError) as exc_info:
            # Call without user_id — must raise TypeError
            await _needs_user_id(db=db)

    error_msg = str(exc_info.value)
    assert "user_id" in error_msg.lower(), (
        f"TypeError message should mention 'user_id' — got: {error_msg}"
    )


@pytest.mark.asyncio
async def test_anthropic_429_maps_to_quota_exceeded_error(monkeypatch):
    """When wrapped fn raises anthropic.APIError with status_code==429, governor re-raises
    as AnthropicQuotaExceededError; governor_calls row has error_code='AnthropicQuotaExceededError'."""
    import app.ai.breakdown as breakdown_mod
    from app.ai.governor import AnthropicQuotaExceededError
    import anthropic

    user_id = str(uuid.uuid4())
    mock_client = MagicMock()

    async def _raise_anthropic_429(**kwargs):
        # Simulate Anthropic returning a 429 / quota-exceeded error
        err = anthropic.APIStatusError(
            message="quota exceeded",
            response=MagicMock(status_code=429, headers={}),
            body={"error": {"type": "quota_exceeded"}},
        )
        raise err

    mock_client.messages.create = _raise_anthropic_429
    mock_client.messages.count_tokens = AsyncMock(return_value=_fake_count_tokens_response(42))
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _seed_song(db, user_id)

    from app.ai.breakdown import run_technique_breakdown

    async with _make_session() as db:
        with pytest.raises(AnthropicQuotaExceededError):
            await run_technique_breakdown(
                "Purple Haze", "Jimi Hendrix", [], 0.7,
                db=db, user_id=uuid.UUID(user_id),
            )

    async with _make_session() as db:
        result = await db.execute(
            text(
                "SELECT error_code FROM governor_calls "
                "WHERE user_id = :uid AND feature = 'breakdown' LIMIT 1"
            ),
            {"uid": user_id},
        )
        row = result.one_or_none()

    assert row is not None, "No governor_calls row found after 429 error"
    assert row.error_code == "AnthropicQuotaExceededError", (
        f"Expected error_code='AnthropicQuotaExceededError', got {row.error_code}"
    )

    async with _make_session() as db:
        await _cleanup_user(db, user_id)


@pytest.mark.asyncio
async def test_governor_record_estimate_populates_prompt_tokens(monkeypatch):
    """D-03 / SC-1: after successful dispatch, governor_calls.prompt_tokens_estimated == 42.

    Mocks count_tokens to return input_tokens=42; asserts the row's prompt_tokens_estimated
    equals 42, proving record_estimate was called pre-dispatch.
    """
    import app.ai.breakdown as breakdown_mod

    user_id = str(uuid.uuid4())
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_fake_sonnet_response())
    # count_tokens returns 42 — the exact value we verify in the DB
    mock_client.messages.count_tokens = AsyncMock(return_value=_fake_count_tokens_response(42))
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _seed_song(db, user_id)

    from app.ai.breakdown import run_technique_breakdown

    async with _make_session() as db:
        await run_technique_breakdown(
            "Little Wing", "Jimi Hendrix", [], 0.6,
            db=db, user_id=uuid.UUID(user_id),
        )

    async with _make_session() as db:
        result = await db.execute(
            text(
                "SELECT prompt_tokens_estimated FROM governor_calls "
                "WHERE user_id = :uid AND feature = 'breakdown' LIMIT 1"
            ),
            {"uid": user_id},
        )
        row = result.one_or_none()

    assert row is not None, "No governor_calls row found"
    assert row.prompt_tokens_estimated == 42, (
        f"Expected prompt_tokens_estimated=42 (from count_tokens mock), "
        f"got {row.prompt_tokens_estimated}"
    )

    async with _make_session() as db:
        await _cleanup_user(db, user_id)


# ---------------------------------------------------------------------------
# Endpoint-level tests (HTTP via ASGITransport)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_endpoint_returns_429_breakdown_capped(monkeypatch):
    """After 3 prior breakdowns, 4th call returns 429 BREAKDOWN_CAPPED with Fletcher copy."""
    import app.ai.breakdown as breakdown_mod

    user_id = str(uuid.uuid4())
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_fake_sonnet_response())
    mock_client.messages.count_tokens = AsyncMock(return_value=_fake_count_tokens_response(42))
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        song_id = await _seed_song(db, user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 3 successful requests to hit the cap
        for _ in range(3):
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id},
            )
            assert resp.status_code == 200, f"Expected 200 on call {_+1}, got {resp.status_code}: {resp.text}"

        # 4th request — must return 429
        resp = await client.get(
            f"/api/v1/songs/{song_id}/breakdown",
            headers={"X-User-ID": user_id},
        )

    assert resp.status_code == 429, (
        f"Expected 429 BREAKDOWN_CAPPED after 3 successful calls, got {resp.status_code}: {resp.text}"
    )
    detail = resp.json().get("detail", {})
    assert detail.get("code") == "BREAKDOWN_CAPPED", f"Expected code='BREAKDOWN_CAPPED', got: {detail}"
    assert detail.get("message", "").startswith("Not my tempo."), (
        f"Expected Fletcher voice starting with 'Not my tempo.', got: {detail.get('message')}"
    )
    assert "resets_at" in detail, "429 body must include resets_at"

    async with _make_session() as db:
        await _cleanup_user(db, user_id)


@pytest.mark.asyncio
async def test_endpoint_returns_503_fletcher_out(monkeypatch):
    """When Anthropic raises 429, endpoint returns 503 FLETCHER_OUT with Fletcher copy."""
    import app.ai.breakdown as breakdown_mod
    import anthropic

    user_id = str(uuid.uuid4())
    mock_client = MagicMock()

    async def _raise_anthropic_429(**kwargs):
        err = anthropic.APIStatusError(
            message="quota exceeded",
            response=MagicMock(status_code=429, headers={}),
            body={"error": {"type": "quota_exceeded"}},
        )
        raise err

    mock_client.messages.create = _raise_anthropic_429
    mock_client.messages.count_tokens = AsyncMock(return_value=_fake_count_tokens_response(42))
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        song_id = await _seed_song(db, user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/v1/songs/{song_id}/breakdown",
            headers={"X-User-ID": user_id},
        )

    assert resp.status_code == 503, (
        f"Expected 503 FLETCHER_OUT on Anthropic 429, got {resp.status_code}: {resp.text}"
    )
    detail = resp.json().get("detail", {})
    assert detail.get("code") == "FLETCHER_OUT", f"Expected code='FLETCHER_OUT', got: {detail}"
    assert "Fletcher's on a break" in detail.get("message", ""), (
        f"Expected Fletcher voice 'Fletcher's on a break', got: {detail.get('message')}"
    )

    async with _make_session() as db:
        await _cleanup_user(db, user_id)


@pytest.mark.asyncio
async def test_endpoint_429_days_remaining_is_integer(monkeypatch):
    """429 body detail.message contains 'Come back in N days' where N is an integer in [1, 8].

    Verifies no literal {N} string slipped through, and the integer is plausible.
    """
    import app.ai.breakdown as breakdown_mod

    user_id = str(uuid.uuid4())
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_fake_sonnet_response())
    mock_client.messages.count_tokens = AsyncMock(return_value=_fake_count_tokens_response(42))
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        song_id = await _seed_song(db, user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _ in range(3):
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id},
            )
            assert resp.status_code == 200

        resp = await client.get(
            f"/api/v1/songs/{song_id}/breakdown",
            headers={"X-User-ID": user_id},
        )

    assert resp.status_code == 429
    message = resp.json().get("detail", {}).get("message", "")

    # Must match 'Come back in <N> days' with an integer N (no literal {N})
    match = re.search(r"Come back in (\d+) days", message)
    assert match is not None, (
        f"Expected 'Come back in N days' with integer N in 429 message, got: {message!r}"
    )
    days = int(match.group(1))
    assert 1 <= days <= 8, (
        f"Expected days_remaining in [1, 8], got {days}. Message: {message!r}"
    )

    async with _make_session() as db:
        await _cleanup_user(db, user_id)
