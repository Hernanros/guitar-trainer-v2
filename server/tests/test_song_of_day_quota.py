"""Tests for Phase 4 Slice B: breakdown_quota field on TodaySongResponse.

Verifies that GET /api/v1/song-of-day and POST /api/v1/today-song/reroll
always return breakdown_quota with correct remaining/cap/resets_at values
derived from governor_calls COUNT + MIN(created_at) queries (D-06).

Runs against a REAL Postgres instance (no mocks — COUNT + MIN are indexed SQL).
Requires DATABASE_URL pointing to a local Postgres with migration 0004 applied.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app


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
    engine = create_async_engine(_make_test_db_url(), echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Fresh AsyncSession for each test."""
    async with _make_session() as session:
        yield session


@pytest_asyncio.fixture
async def quota_user(db: AsyncSession):
    """Create a minimal test user with one working_on song for quota tests.

    Yields (user_id, song_id) so tests can INSERT governor_calls rows.
    """
    user_id = uuid.uuid4()

    await db.execute(
        text(
            "INSERT INTO users (id, preferences) VALUES (:id, '{}'::jsonb) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": str(user_id)},
    )

    # Minimal skill_node (required for CTE player_level computation)
    skill_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
            "VALUES (:id, :uid, 'Rhythm', 'leaf', 0.3)"
        ),
        {"id": str(skill_id), "uid": str(user_id)},
    )

    # One working_on song (minimal breakdown JSON for Pydantic)
    await db.execute(
        text(
            "INSERT INTO songs (title, artist, genre, difficulty, bpm, key, breakdown, user_id, category) "
            "VALUES ('Test Song', 'Test Artist', 'Rock', 'Intermediate', 100, 'A', "
            "'{\"tab\":{\"measures\":[],\"tuning\":[\"E\",\"A\",\"D\",\"G\",\"B\",\"e\"]},"
            "\"chords\":[],\"technique_notes\":[]}'::jsonb, "
            ":uid, 'working_on')"
        ),
        {"uid": str(user_id)},
    )
    result = await db.execute(
        text("SELECT id FROM songs WHERE user_id = :uid AND title = 'Test Song'"),
        {"uid": str(user_id)},
    )
    song_id = result.scalar_one()

    await db.execute(
        text(
            "INSERT INTO song_skills (song_id, skill_node_id) VALUES (:sid, :skid)"
        ),
        {"sid": song_id, "skid": str(skill_id)},
    )

    await db.commit()
    yield user_id

    # Cleanup in FK-safe order
    await db.execute(text("DELETE FROM governor_calls WHERE user_id = :uid"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM user_sessions WHERE user_id = :uid"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM song_skills WHERE song_id IN (SELECT id FROM songs WHERE user_id = :uid)"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM songs WHERE user_id = :uid"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM skill_nodes WHERE user_id = :uid"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(user_id)})
    await db.commit()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _get_today_song(user_id: uuid.UUID) -> dict:
    """Call GET /api/v1/song-of-day and return parsed JSON body."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/song-of-day",
            headers={"X-User-ID": str(user_id), "X-Timezone-Offset": "0"},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_breakdown_quota_defaults_to_three_when_no_calls(quota_user):
    """Fresh user with no governor_calls rows → breakdown_quota.remaining == 3."""
    body = await _get_today_song(quota_user)

    assert "breakdown_quota" in body, "TodaySongResponse must include breakdown_quota"
    quota = body["breakdown_quota"]
    assert quota["remaining"] == 3
    assert quota["cap"] == 3
    # resets_at must be a parseable ISO datetime string
    parsed = datetime.fromisoformat(quota["resets_at"])
    assert parsed > datetime.now(timezone.utc), "resets_at must be in the future for a fresh user"


@pytest.mark.asyncio
async def test_breakdown_quota_decrements_after_governor_row(quota_user, db: AsyncSession):
    """INSERT 1 governor_calls row for the user → remaining == 2."""
    user_id = quota_user
    await db.execute(
        text(
            "INSERT INTO governor_calls (id, user_id, feature, model, created_at) "
            "VALUES (gen_random_uuid(), :uid, 'breakdown', 'claude-sonnet-4-6', now())"
        ),
        {"uid": str(user_id)},
    )
    await db.commit()

    body = await _get_today_song(user_id)
    quota = body["breakdown_quota"]
    assert quota["remaining"] == 2
    assert quota["cap"] == 3


@pytest.mark.asyncio
async def test_breakdown_quota_zero_when_capped(quota_user, db: AsyncSession):
    """INSERT 3 governor_calls rows → remaining == 0 (capped)."""
    user_id = quota_user
    for _ in range(3):
        await db.execute(
            text(
                "INSERT INTO governor_calls (id, user_id, feature, model, created_at) "
                "VALUES (gen_random_uuid(), :uid, 'breakdown', 'claude-sonnet-4-6', now())"
            ),
            {"uid": str(user_id)},
        )
    await db.commit()

    body = await _get_today_song(user_id)
    quota = body["breakdown_quota"]
    assert quota["remaining"] == 0
    assert quota["cap"] == 3


@pytest.mark.asyncio
async def test_breakdown_quota_ignores_other_features(quota_user, db: AsyncSession):
    """INSERT 5 rows with feature='onboarding' → breakdown_quota.remaining stays 3."""
    user_id = quota_user
    for _ in range(5):
        await db.execute(
            text(
                "INSERT INTO governor_calls (id, user_id, feature, model, created_at) "
                "VALUES (gen_random_uuid(), :uid, 'onboarding', 'claude-sonnet-4-6', now())"
            ),
            {"uid": str(user_id)},
        )
    await db.commit()

    body = await _get_today_song(user_id)
    quota = body["breakdown_quota"]
    assert quota["remaining"] == 3, "Non-breakdown governor_calls must not affect breakdown quota"


@pytest.mark.asyncio
async def test_breakdown_quota_ignores_old_calls(quota_user, db: AsyncSession):
    """INSERT 3 rows with created_at 8 days ago → they age out, remaining == 3."""
    user_id = quota_user
    eight_days_ago = datetime.now(timezone.utc) - timedelta(days=8)
    for _ in range(3):
        await db.execute(
            text(
                "INSERT INTO governor_calls (id, user_id, feature, model, created_at) "
                "VALUES (gen_random_uuid(), :uid, 'breakdown', 'claude-sonnet-4-6', :ts)"
            ),
            {"uid": str(user_id), "ts": eight_days_ago},
        )
    await db.commit()

    body = await _get_today_song(user_id)
    quota = body["breakdown_quota"]
    assert quota["remaining"] == 3, "Calls older than 7 days must not count against the quota"


@pytest.mark.asyncio
async def test_breakdown_quota_resets_at_matches_oldest_plus_7d(quota_user, db: AsyncSession):
    """INSERT 1 row 2 days ago → resets_at ≈ that timestamp + 7 days (within 5s tolerance)."""
    user_id = quota_user
    two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
    await db.execute(
        text(
            "INSERT INTO governor_calls (id, user_id, feature, model, created_at) "
            "VALUES (gen_random_uuid(), :uid, 'breakdown', 'claude-sonnet-4-6', :ts)"
        ),
        {"uid": str(user_id), "ts": two_days_ago},
    )
    await db.commit()

    body = await _get_today_song(user_id)
    quota = body["breakdown_quota"]

    expected_resets_at = two_days_ago + timedelta(days=7)
    actual_resets_at = datetime.fromisoformat(quota["resets_at"])
    # Allow 5s tolerance for test execution time
    delta = abs((actual_resets_at - expected_resets_at).total_seconds())
    assert delta < 5, (
        f"resets_at should be oldest_call + 7d. "
        f"Expected ~{expected_resets_at.isoformat()}, got {quota['resets_at']}, "
        f"delta={delta}s"
    )


@pytest.mark.asyncio
async def test_reroll_endpoint_also_includes_breakdown_quota(quota_user):
    """POST /api/v1/today-song/reroll response also includes breakdown_quota."""
    user_id = quota_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/today-song/reroll",
            headers={"X-User-ID": str(user_id), "X-Timezone-Offset": "0"},
        )
    # 200 if bank has a song for this user; 404 if bank is empty for this test user
    # Either way we need to validate shape on 200
    if resp.status_code == 200:
        body = resp.json()
        assert "breakdown_quota" in body, "reroll TodaySongResponse must include breakdown_quota"
        quota = body["breakdown_quota"]
        assert "remaining" in quota
        assert "cap" in quota
        assert "resets_at" in quota
        assert quota["cap"] == 3
        assert quota["remaining"] >= 0
