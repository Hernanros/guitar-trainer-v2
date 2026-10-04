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

import asyncio
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
    abandoned: bool = False,
) -> str:
    """Insert a governor_calls row directly for setup. Returns the row id.

    Stamps actual token counts by default, because the thing these fixtures stand
    in for is a COMPLETED call. FLE-39 made that distinction load-bearing: the cap
    predicate now reads an old row with NULL actuals as a call that was killed
    mid-dispatch and refunds it, so a fixture that leaves them NULL is no longer
    describing a prior success.

    abandoned=True leaves the actuals NULL — the unstamped, never-completed row a
    client disconnect or a container replacement leaves behind.
    """
    row_id = str(uuid.uuid4())
    ts = created_at or datetime.now(timezone.utc)
    await db.execute(
        text(
            "INSERT INTO governor_calls "
            "  (id, user_id, feature, model, prompt_tokens_actual, output_tokens_actual, created_at) "
            "VALUES (:id, :uid, :feature, 'claude-sonnet-4-6', :pt, :ot, :ts)"
        ),
        {
            "id": row_id,
            "uid": user_id,
            "feature": feature,
            "pt": None if abandoned else 1000,
            "ot": None if abandoned else 500,
            "ts": ts,
        },
    )
    await db.commit()
    return row_id


async def _cleanup_user(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        text("DELETE FROM governor_calls WHERE user_id = :uid"), {"uid": user_id}
    )
    # FLE-92: user_sessions references songs, so it has to go before the songs
    # DELETE below or cleanup dies on user_sessions_song_id_fkey. Nothing in this
    # file wrote a session row until the governor started reading tz_offset_minutes
    # off one, which is why the gap went unnoticed.
    await db.execute(
        text("DELETE FROM user_sessions WHERE user_id = :uid"), {"uid": user_id}
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
async def test_governor_blocks_sixth_breakdown_same_day(monkeypatch):
    """FLE-92: the 6th call in one local day raises BudgetExceededError — no dispatch.

    Was test_governor_blocks_fourth_breakdown against cap=3 per rolling 7 days. The
    number and the window both moved (5 per local calendar day, Hernan's decision on
    the FLE-88 card), so this pins the new boundary: five calls land, the sixth does
    not, and the sixth costs nothing.
    """
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

    # 5 successful calls — the whole day's allowance
    for _ in range(5):
        async with _make_session() as db:
            result = await run_technique_breakdown(
                "Sweet Home Chicago", "Robert Johnson", [], 0.3,
                db=db, user_id=uuid.UUID(user_id),
            )
        assert result is not None

    assert sonnet_call_count["n"] == 5, (
        f"Expected 5 Sonnet calls after 5 successful breakdowns, got {sonnet_call_count['n']}"
    )

    # 6th call — must raise BudgetExceededError without dispatching Sonnet
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

    # Sonnet call count should still be 5 — no 6th dispatch
    assert sonnet_call_count["n"] == 5, (
        f"6th call should not dispatch Sonnet — expected count=5, got {sonnet_call_count['n']}"
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
async def test_yesterdays_calls_do_not_count_today(monkeypatch):
    """FLE-92: a full day's worth of yesterday's calls leaves today's allowance intact.

    Replaces test_governor_seven_day_window_slides. That test proved the rolling
    7-day window aged its oldest row out; this proves the thing the daily window
    exists for, which is strictly stronger from the user's point of view: being
    refused yesterday tells you nothing about today.

    This is the half of FLE-58 that was actually hostile. Under 3-per-7-days the wall
    you hit on Tuesday was still standing on Thursday, which is how the cap locked
    Hernan out of his own app for most of a week.
    """
    import app.ai.breakdown as breakdown_mod

    user_id = str(uuid.uuid4())
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_fake_sonnet_response())
    mock_client.messages.count_tokens = AsyncMock(return_value=_fake_count_tokens_response(42))
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _seed_song(db, user_id)

    # A full cap's worth yesterday, plus more the day before — under the old rolling
    # window this user was locked out until the middle of next week.
    async with _make_session() as db:
        for _ in range(5):
            await _insert_governor_call(
                db, user_id, created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        for _ in range(5):
            await _insert_governor_call(
                db, user_id, created_at=datetime.now(timezone.utc) - timedelta(days=3),
            )

    from app.ai.breakdown import run_technique_breakdown

    async with _make_session() as db:
        result = await run_technique_breakdown(
            "Wonderwall", "Oasis", [], 0.2,
            db=db, user_id=uuid.UUID(user_id),
        )
    assert result is not None, (
        "Yesterday's calls must not count against today — a daily window whose wall "
        "survives midnight is the rolling window again under another name"
    )

    async with _make_session() as db:
        await _cleanup_user(db, user_id)


@pytest.mark.asyncio
async def test_day_boundary_follows_the_users_timezone():
    """FLE-92: "today" is the USER's local day, not the server's UTC day.

    Hernan's device reports +180 (UTC+3), so for three hours after 21:00 UTC his
    local day has already turned over while the server's has not. A UTC-day window
    would hold his wall up through those hours.

    Constructed so the two interpretations disagree: the row sits just inside today
    in UTC but inside YESTERDAY for a user at -660 (UTC-11), whose local midnight is
    11:00 UTC. A UTC-based window would count it.
    """
    from app.ai.governor import count_window_calls

    user_id = str(uuid.uuid4())
    tz_far_west = -660  # UTC-11

    async with _make_session() as db:
        await _seed_user(db, user_id)
        song_id = await _seed_song(db, user_id)
        # The device's reported offset — where the governor reads tz from, since
        # neither users nor governor_calls carries one.
        await db.execute(
            text(
                "INSERT INTO user_sessions "
                "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes) "
                "VALUES (gen_random_uuid(), :uid, :sid, NULL, "
                "        DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute')), :tz)"
            ),
            {"uid": user_id, "sid": song_id, "tz": tz_far_west},
        )
        await db.commit()

    # 00:30 UTC today: inside the UTC day, but 13:30 YESTERDAY at UTC-11.
    utc_today_early = datetime.now(timezone.utc).replace(
        hour=0, minute=30, second=0, microsecond=0
    )

    async with _make_session() as db:
        await _insert_governor_call(db, user_id, created_at=utc_today_early)

    async with _make_session() as db:
        count, resets_at = await count_window_calls(db, user_id, "breakdown")

    # The suite can legitimately run before 00:30 UTC, in which case the row is in
    # the future for every timezone and there is no distinction left to draw.
    if datetime.now(timezone.utc) >= utc_today_early:
        assert count == 0, (
            f"A call at 00:30 UTC is yesterday for a user at UTC-11 and must not "
            f"count against their today, got count={count}"
        )

    assert resets_at.hour == 11 and resets_at.minute == 0, (
        f"Next local midnight for UTC-11 is 11:00 UTC, got {resets_at}"
    )
    assert resets_at > datetime.now(timezone.utc), "resets_at must be in the future"
    assert resets_at - datetime.now(timezone.utc) <= timedelta(days=1), (
        f"A daily window cannot reset more than 24h out, got {resets_at}"
    )

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


@pytest.mark.parametrize(
    "env_value,expected",
    [
        (None, 3),          # unset — hardcoded default stands
        ("", 3),            # blank — treated as unset
        ("off", None),      # the temporary-kill-switch spelling
        ("none", None),
        ("UNLIMITED", None),
        ("-1", None),
        ("10", 10),         # raise the cap instead of removing it
        ("0", 0),           # 0 blocks every call — a real value, not "off"
        ("banana", 3),      # unparseable must never silently uncap
    ],
)
def test_effective_cap_env_override(monkeypatch, env_value, expected):
    """FLETCHER_CAP_BREAKDOWN overrides the decorator's cap at call time."""
    from app.ai.governor import effective_cap

    if env_value is None:
        monkeypatch.delenv("FLETCHER_CAP_BREAKDOWN", raising=False)
    else:
        monkeypatch.setenv("FLETCHER_CAP_BREAKDOWN", env_value)

    assert effective_cap("breakdown", 3) == expected


@pytest.mark.asyncio
async def test_env_override_lifts_breakdown_cap(monkeypatch):
    """With FLETCHER_CAP_BREAKDOWN=off, a 4th breakdown dispatches instead of raising."""
    from app.ai.governor import BudgetExceededError

    monkeypatch.setenv("FLETCHER_CAP_BREAKDOWN", "off")
    _patch_client_for_success(monkeypatch)

    user_id = str(uuid.uuid4())
    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _seed_song(db, user_id)

    # Pre-load the window past the default cap of 3
    async with _make_session() as db:
        for _ in range(3):
            await _insert_governor_call(db, user_id, feature="breakdown")

    from app.ai.breakdown import run_technique_breakdown

    async with _make_session() as db:
        try:
            result = await run_technique_breakdown(
                "Sweet Home Chicago", "Robert Johnson", [], 0.3,
                db=db, user_id=uuid.UUID(user_id),
            )
        except BudgetExceededError:
            pytest.fail("Cap still enforced with FLETCHER_CAP_BREAKDOWN=off")
    assert result is not None

    async with _make_session() as db:
        await _cleanup_user(db, user_id)


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
    """After 5 prior breakdowns today, the 6th returns 429 BREAKDOWN_CAPPED.

    FLE-92 also rewrote the copy. The old assertion required the message to start
    with "Not my tempo." — which is the label on a rating pill in the player, so the
    refusal read as the app grading the user's playing. This now asserts the two
    things the copy has to do: name the daily allowance, and name when it lifts.
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
        # 5 successful requests to spend the day's allowance
        for _ in range(5):
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id},
            )
            assert resp.status_code == 200, f"Expected 200 on call {_+1}, got {resp.status_code}: {resp.text}"

        # 6th request — must return 429
        resp = await client.get(
            f"/api/v1/songs/{song_id}/breakdown",
            headers={"X-User-ID": user_id},
        )

    assert resp.status_code == 429, (
        f"Expected 429 BREAKDOWN_CAPPED after 5 successful calls, got {resp.status_code}: {resp.text}"
    )
    detail = resp.json().get("detail", {})
    assert detail.get("code") == "BREAKDOWN_CAPPED", f"Expected code='BREAKDOWN_CAPPED', got: {detail}"
    message = detail.get("message", "")
    assert "5 breakdowns for today" in message, (
        f"Copy must name the daily allowance, got: {message!r}"
    )
    assert "midnight" in message, (
        f"Copy must name when the allowance returns, got: {message!r}"
    )
    assert "Not my tempo" not in message, (
        "'Not my tempo' is a rating-pill label; reusing it here reads as the app "
        f"grading the player rather than reporting a quota. Got: {message!r}"
    )
    assert "this week" not in message, (
        f"The window is one local day, not a week. Got: {message!r}"
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
async def test_endpoint_429_resets_at_is_the_next_local_midnight(monkeypatch):
    """429 body resets_at is a real, parseable midnight within the next 24h.

    Replaces test_endpoint_429_days_remaining_is_integer, which asserted the message
    matched "Come back in N days" for N in [1, 8]. That assertion cannot survive a
    daily window — every refusal would read "come back in 1 days" — and the copy it
    was guarding is gone. What still needs guarding is the field the client renders
    off, so this pins resets_at instead of the sentence built from it: an absolute
    instant, in the future, at most a day out, and on a midnight boundary for the
    offset the request declared.

    Also asserts no literal format placeholder survived into the message, which was
    the original test's other job.
    """
    import app.ai.breakdown as breakdown_mod

    user_id = str(uuid.uuid4())
    tz_offset = 180          # Hernan's device reads +180
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_fake_sonnet_response())
    mock_client.messages.count_tokens = AsyncMock(return_value=_fake_count_tokens_response(42))
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        song_id = await _seed_song(db, user_id)
        await db.execute(
            text(
                "INSERT INTO user_sessions "
                "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes) "
                "VALUES (gen_random_uuid(), :uid, :sid, NULL, "
                "        DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute')), :tz)"
            ),
            {"uid": user_id, "sid": song_id, "tz": tz_offset},
        )
        await db.commit()

    headers = {"X-User-ID": user_id, "X-Timezone-Offset": str(tz_offset)}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _ in range(5):
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown", headers=headers,
            )
            assert resp.status_code == 200

        resp = await client.get(
            f"/api/v1/songs/{song_id}/breakdown", headers=headers,
        )

    assert resp.status_code == 429
    detail = resp.json().get("detail", {})
    message = detail.get("message", "")

    assert "{" not in message and "}" not in message, (
        f"An unformatted placeholder survived into the user-facing copy: {message!r}"
    )

    resets_at = datetime.fromisoformat(detail["resets_at"])
    if resets_at.tzinfo is None:
        resets_at = resets_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    assert now < resets_at <= now + timedelta(days=1), (
        f"resets_at must be the next local midnight — in the future and at most 24h "
        f"out. now={now.isoformat()} resets_at={resets_at.isoformat()}"
    )

    local_reset = resets_at + timedelta(minutes=tz_offset)
    assert (local_reset.hour, local_reset.minute, local_reset.second) == (0, 0, 0), (
        f"resets_at must land on midnight in the user's own offset (+{tz_offset}), "
        f"got {local_reset.isoformat()} local"
    )

    async with _make_session() as db:
        await _cleanup_user(db, user_id)


@pytest.mark.asyncio
async def test_failed_breakdown_does_not_consume_cap(monkeypatch):
    """FLE-18: a call that errors must NOT decrement the 3-per-7d allowance.

    The audit row is INSERTed before dispatch and stamped with error_code by
    _update_error on failure. _check_cap filters on `error_code IS NULL`, so the
    failed row stays in the table for audit but stops counting against the cap.

    Regression guard for the user-visible bug: three transient Anthropic failures
    used to lock a user out of breakdowns for a full week without ever having
    delivered one.
    """
    import app.ai.breakdown as breakdown_mod
    from app.ai.breakdown import AIBreakdownError, run_technique_breakdown

    user_id = str(uuid.uuid4())
    sonnet_call_count = {"n": 0}

    mock_client = MagicMock()

    # Fail every dispatch. run_technique_breakdown retries once, so each
    # run_technique_breakdown() call increments this twice.
    async def _always_fail(**kwargs):
        sonnet_call_count["n"] += 1
        raise asyncio.TimeoutError("simulated Anthropic timeout")

    mock_client.messages.create = _always_fail
    mock_client.messages.count_tokens = AsyncMock(
        return_value=_fake_count_tokens_response(42)
    )
    monkeypatch.setattr(breakdown_mod, "get_client", lambda: mock_client)

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _seed_song(db, user_id)

    # 3 failed calls — under the old behaviour these consumed the entire cap.
    for _ in range(3):
        async with _make_session() as db:
            with pytest.raises(AIBreakdownError):
                await run_technique_breakdown(
                    "Sweet Home Chicago", "Robert Johnson", [], 0.3,
                    db=db, user_id=uuid.UUID(user_id),
                )

    # All three rows must be present for audit, and all three must carry error_code.
    async with _make_session() as db:
        total = await db.scalar(
            text(
                "SELECT COUNT(*) FROM governor_calls "
                "WHERE user_id = :u AND feature = 'breakdown'"
            ),
            {"u": user_id},
        )
        errored = await db.scalar(
            text(
                "SELECT COUNT(*) FROM governor_calls "
                "WHERE user_id = :u AND feature = 'breakdown' "
                "AND error_code IS NOT NULL"
            ),
            {"u": user_id},
        )
    assert total == 3, f"All 3 failed calls must still be audited, got {total}"
    assert errored == 3, f"All 3 audit rows must carry error_code, got {errored}"

    # The 4th call must still be allowed through to Sonnet — the cap is intact.
    dispatches_before = sonnet_call_count["n"]
    mock_client.messages.create = AsyncMock(return_value=_fake_sonnet_response())

    async with _make_session() as db:
        result = await run_technique_breakdown(
            "Sweet Home Chicago", "Robert Johnson", [], 0.3,
            db=db, user_id=uuid.UUID(user_id),
        )
    assert result is not None, (
        "4th call after 3 FAILED calls must succeed — failures must not consume the cap"
    )
    assert mock_client.messages.create.await_count == 1, (
        "4th call must actually dispatch to Sonnet, not be short-circuited by the cap"
    )
    assert dispatches_before == 6, (
        f"Expected 3 failed calls x 2 attempts (1 retry) = 6 dispatches, got {dispatches_before}"
    )

    async with _make_session() as db:
        await _cleanup_user(db, user_id)


# ---------------------------------------------------------------------------
# FLE-39 — terminations that never reach an `except Exception` handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancelled_call_does_not_consume_cap():
    """FLE-39: a client disconnect must not cost the user one of their 3 breakdowns.

    This is the hole test_failed_breakdown_does_not_consume_cap cannot cover: it
    raises asyncio.TimeoutError, an ordinary Exception, so arm (g) of the wrapper
    catches it and stamps error_code. On Python 3.13 asyncio.CancelledError
    subclasses BaseException instead, so arm (g) never fired and the row was left
    indistinguishable from a delivered breakdown.

    Cancels a real asyncio Task mid-await rather than raising CancelledError by
    hand, because the bug IS the real cancellation path: the ASGI task dies and
    the request-scoped session goes with it.
    """
    from app.ai.governor import governed

    user_id = str(uuid.uuid4())
    entered = asyncio.Event()

    @governed(feature="breakdown", cap=3)
    async def _slow_governed_call(*, db: AsyncSession, user_id: uuid.UUID) -> str:
        entered.set()
        await asyncio.sleep(3600)   # stands in for the ~87s Sonnet dispatch
        return "never reached"

    async with _make_session() as db:
        await _seed_user(db, user_id)

    # Three disconnects. Pre-fix every one of these left a NULL-error_code row and
    # the user was locked out for a week having received nothing.
    for _ in range(3):
        async with _make_session() as db:
            entered.clear()
            task = asyncio.ensure_future(
                _slow_governed_call(db=db, user_id=uuid.UUID(user_id))
            )
            await entered.wait()        # row is INSERTed, dispatch is in flight
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    async with _make_session() as db:
        total = await db.scalar(
            text(
                "SELECT COUNT(*) FROM governor_calls "
                "WHERE user_id = :u AND feature = 'breakdown'"
            ),
            {"u": user_id},
        )
        stamped = await db.scalar(
            text(
                "SELECT COUNT(*) FROM governor_calls "
                "WHERE user_id = :u AND feature = 'breakdown' "
                "AND error_code = 'CancelledError'"
            ),
            {"u": user_id},
        )
    assert total == 3, f"All 3 cancelled calls must still be audited, got {total}"
    assert stamped == 3, (
        f"All 3 rows must carry error_code='CancelledError', got {stamped}. "
        "An unstamped row counts against the 3-per-7d cap."
    )

    # The cap must be intact: a 4th call still gets through.
    async with _make_session() as db:
        await _check_cap_or_fail(db, user_id)
        await _cleanup_user(db, user_id)


async def _check_cap_or_fail(db: AsyncSession, user_id: str) -> None:
    """Assert _check_cap sees headroom for `user_id` at the breakdown cap of 3."""
    from app.ai.governor import BudgetExceededError, _check_cap

    try:
        await _check_cap(db, uuid.UUID(user_id), "breakdown", 3)
    except BudgetExceededError as exc:
        pytest.fail(f"Cap must have headroom left, but _check_cap raised: {exc}")


@pytest.mark.asyncio
async def test_abandoned_inflight_rows_stop_counting_after_grace():
    """FLE-39: rows orphaned by process death must stop counting against the cap.

    A deploy SIGTERM or an OOM kill runs NO handler at all, so no in-process
    `except` arm can ever stamp these — the cap predicate itself has to heal them.
    Two such rows were found in prod on 2026-09-16, one of them holding a unit of
    the real pilot user's weekly allowance hostage indefinitely.

    Also pins the two properties the heal must not break:
      - a row that COMPLETED keeps counting however old it gets, or the cap leaks;
      - a row still plausibly IN FLIGHT keeps counting, or concurrent taps race
        past the cap (the FLE-23 §6 property).
    """
    from app.ai.governor import BudgetExceededError, _check_cap

    long_ago = datetime.now(timezone.utc) - timedelta(hours=2)

    # (1) Three rows killed mid-dispatch two hours ago → all three refunded.
    abandoned_user = str(uuid.uuid4())
    async with _make_session() as db:
        await _seed_user(db, abandoned_user)
        for _ in range(3):
            await _insert_governor_call(
                db, abandoned_user, created_at=long_ago, abandoned=True
            )
    async with _make_session() as db:
        await _check_cap_or_fail(db, abandoned_user)

    # (2) Same age, but they completed → they must still count. This is the
    #     regression that would turn the refund into a free-breakdown exploit.
    completed_user = str(uuid.uuid4())
    async with _make_session() as db:
        await _seed_user(db, completed_user)
        for _ in range(3):
            await _insert_governor_call(db, completed_user, created_at=long_ago)
    async with _make_session() as db:
        with pytest.raises(BudgetExceededError):
            await _check_cap(db, uuid.UUID(completed_user), "breakdown", 3)

    # (3) Unstamped but only seconds old → still in flight, must still count, or
    #     three simultaneous taps all pass a cap of 3.
    inflight_user = str(uuid.uuid4())
    async with _make_session() as db:
        await _seed_user(db, inflight_user)
        for _ in range(3):
            await _insert_governor_call(db, inflight_user, abandoned=True)
    async with _make_session() as db:
        with pytest.raises(BudgetExceededError):
            await _check_cap(db, uuid.UUID(inflight_user), "breakdown", 3)

    async with _make_session() as db:
        for uid in (abandoned_user, completed_user, inflight_user):
            await _cleanup_user(db, uid)


@pytest.mark.asyncio
async def test_cap_and_quota_chip_share_one_computation():
    """FLE-37 confirmed the cap and the chip agreed exactly; nothing may regress it.

    The chip saying "2 left" over a cap that refuses is worse than either bug alone,
    and hand-copied query logic is how that happens.

    FLE-39 enforced this structurally by making both sides interpolate one shared
    WHERE clause. FLE-92 raised the bar: the window itself is now per-feature and
    needs a timezone lookup, so a shared string is no longer sufficient — the
    predicate could match while the two sides measured different days. Both sides
    now call governor.count_window_calls and do no querying of their own, so this
    asserts that rather than counting interpolations.
    """
    import inspect

    import app.api.v1.song_of_day as song_of_day_mod
    from app.ai.governor import (
        COUNTS_AGAINST_CAP_SQL,
        _check_cap,
        count_window_calls,
    )

    assert "prompt_tokens_actual IS NOT NULL" in COUNTS_AGAINST_CAP_SQL
    assert "error_code IS NULL" in COUNTS_AGAINST_CAP_SQL

    def _body(fn) -> str:
        """Source of `fn` with its docstring lines removed.

        Both functions discuss governor_calls, the shared predicate and the index by
        name in prose. Prose is documentation, not a second source of truth — read
        only the code, or this test fails on a comment and passes on a copy-pasted
        query.
        """
        src = inspect.getsource(fn)
        for line in (inspect.getdoc(fn) or "").splitlines():
            if line.strip():
                src = src.replace(line, "")
        return src

    cap_src = _body(_check_cap)
    chip_src = _body(song_of_day_mod._breakdown_quota)
    shared_src = inspect.getsource(count_window_calls)

    # Each side delegates; neither builds a window or a predicate.
    for name, src in (("_check_cap", cap_src), ("_breakdown_quota", chip_src)):
        assert "count_window_calls(" in src, (
            f"{name} must read its count from count_window_calls, not its own query"
        )
        assert "governor_calls" not in src, (
            f"{name} queries governor_calls directly; that is how the cap and the "
            f"chip drift apart. Go through count_window_calls."
        )
        # The interpolation form, not the bare name — both docstrings reference the
        # constant in prose, and prose is not a second source of truth.
        assert "{COUNTS_AGAINST_CAP_SQL}" not in src, (
            f"{name} must not interpolate the predicate into a query of its own"
        )
        assert "INTERVAL" not in src.upper(), (
            f"{name} must not define a window of its own"
        )

    # And the one place that does query carries the shared predicate on every branch
    # (local-day COUNT, rolling COUNT, rolling MIN).
    assert shared_src.count("AND {COUNTS_AGAINST_CAP_SQL}") == 3, (
        "Every query in count_window_calls must interpolate the shared predicate"
    )
