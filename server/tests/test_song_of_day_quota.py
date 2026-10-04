"""Tests for Phase 4 Slice B: breakdown_quota field on TodaySongResponse.

Verifies that GET /api/v1/song-of-day and POST /api/v1/today-song/reroll
always return breakdown_quota with correct remaining/cap/resets_at values
derived from governor.count_window_calls (D-06, rewritten by FLE-92).

FLE-92 changed the quota from 3 per rolling 7 days to 5 per the user's local
calendar day, and resets_at from "oldest counted call + 7 days" to "next local
midnight". Both numbers and both window edges move here as a result.

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
async def test_breakdown_quota_defaults_to_five_when_no_calls(quota_user):
    """Fresh user with no governor_calls rows → breakdown_quota.remaining == 5 (FLE-92)."""
    body = await _get_today_song(quota_user)

    assert "breakdown_quota" in body, "TodaySongResponse must include breakdown_quota"
    quota = body["breakdown_quota"]
    assert quota["remaining"] == 5
    assert quota["cap"] == 5
    # resets_at must be a parseable ISO datetime string
    parsed = datetime.fromisoformat(quota["resets_at"])
    assert parsed > datetime.now(timezone.utc), "resets_at must be in the future for a fresh user"


@pytest.mark.asyncio
async def test_breakdown_quota_decrements_after_governor_row(quota_user, db: AsyncSession):
    """INSERT 1 governor_calls row for the user today → remaining == 4."""
    user_id = quota_user
    await db.execute(
        text(
            "INSERT INTO governor_calls (id, user_id, feature, model, "
            "  prompt_tokens_actual, output_tokens_actual, created_at) "
            "VALUES (gen_random_uuid(), :uid, 'breakdown', 'claude-sonnet-4-6', 1000, 500, now())"
        ),
        {"uid": str(user_id)},
    )
    await db.commit()

    body = await _get_today_song(user_id)
    quota = body["breakdown_quota"]
    assert quota["remaining"] == 4
    assert quota["cap"] == 5


@pytest.mark.asyncio
async def test_breakdown_quota_zero_when_capped(quota_user, db: AsyncSession):
    """INSERT 5 governor_calls rows today → remaining == 0 (capped)."""
    user_id = quota_user
    for _ in range(5):
        await db.execute(
            text(
                "INSERT INTO governor_calls (id, user_id, feature, model, "
                "  prompt_tokens_actual, output_tokens_actual, created_at) "
                "VALUES (gen_random_uuid(), :uid, 'breakdown', 'claude-sonnet-4-6', 1000, 500, now())"
            ),
            {"uid": str(user_id)},
        )
    await db.commit()

    body = await _get_today_song(user_id)
    quota = body["breakdown_quota"]
    assert quota["remaining"] == 0
    assert quota["cap"] == 5


@pytest.mark.asyncio
async def test_breakdown_quota_is_null_when_cap_switched_off(
    quota_user, db: AsyncSession, monkeypatch
):
    """FLETCHER_CAP_BREAKDOWN=off → breakdown_quota is null even with a full window.

    Null is what the shipped client reads as "no chip, CTA enabled" — it accesses
    breakdown_quota?.remaining, so an absent quota never disables the button.
    """
    user_id = quota_user
    monkeypatch.setenv("FLETCHER_CAP_BREAKDOWN", "off")
    for _ in range(5):
        await db.execute(
            text(
                "INSERT INTO governor_calls (id, user_id, feature, model, "
                "  prompt_tokens_actual, output_tokens_actual, created_at) "
                "VALUES (gen_random_uuid(), :uid, 'breakdown', 'claude-sonnet-4-6', 1000, 500, now())"
            ),
            {"uid": str(user_id)},
        )
    await db.commit()

    body = await _get_today_song(user_id)
    assert body["breakdown_quota"] is None


@pytest.mark.asyncio
async def test_breakdown_quota_follows_raised_cap(quota_user, db: AsyncSession, monkeypatch):
    """FLETCHER_CAP_BREAKDOWN=10 → remaining/cap reported against 10, not 5."""
    user_id = quota_user
    monkeypatch.setenv("FLETCHER_CAP_BREAKDOWN", "10")
    await db.execute(
        text(
            "INSERT INTO governor_calls (id, user_id, feature, model, "
            "  prompt_tokens_actual, output_tokens_actual, created_at) "
            "VALUES (gen_random_uuid(), :uid, 'breakdown', 'claude-sonnet-4-6', 1000, 500, now())"
        ),
        {"uid": str(user_id)},
    )
    await db.commit()

    quota = (await _get_today_song(user_id))["breakdown_quota"]
    assert quota["cap"] == 10
    assert quota["remaining"] == 9


@pytest.mark.asyncio
async def test_breakdown_quota_ignores_other_features(quota_user, db: AsyncSession):
    """INSERT 5 rows with feature='onboarding' → breakdown_quota.remaining stays 5.

    Worth keeping sharp after FLE-92: onboarding is still capped over a rolling 7-day
    window while breakdowns are capped per local day, so the two features now
    disagree about what "the window" even means. If FEATURE_WINDOWS were ever read
    with the wrong feature, this is where it shows up.
    """
    user_id = quota_user
    for _ in range(5):
        await db.execute(
            text(
                "INSERT INTO governor_calls (id, user_id, feature, model, "
                "  prompt_tokens_actual, output_tokens_actual, created_at) "
                "VALUES (gen_random_uuid(), :uid, 'onboarding', 'claude-sonnet-4-6', 1000, 500, now())"
            ),
            {"uid": str(user_id)},
        )
    await db.commit()

    body = await _get_today_song(user_id)
    quota = body["breakdown_quota"]
    assert quota["remaining"] == 5, "Non-breakdown governor_calls must not affect breakdown quota"


@pytest.mark.asyncio
async def test_breakdown_quota_ignores_yesterdays_calls(quota_user, db: AsyncSession):
    """A full cap's worth yesterday → today's chip still reads 5 left (FLE-92).

    Was test_breakdown_quota_ignores_old_calls, which only proved rows aged out at 8
    days. The daily window has to clear at midnight and the chip has to clear with
    it — a chip still reading "0 left" the morning after is the same lockout FLE-58
    was filed about, just with a correct cap sitting behind it.
    """
    user_id = quota_user
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    for _ in range(5):
        await db.execute(
            text(
                "INSERT INTO governor_calls (id, user_id, feature, model, "
                "  prompt_tokens_actual, output_tokens_actual, created_at) "
                "VALUES (gen_random_uuid(), :uid, 'breakdown', 'claude-sonnet-4-6', 1000, 500, :ts)"
            ),
            {"uid": str(user_id), "ts": yesterday},
        )
    await db.commit()

    body = await _get_today_song(user_id)
    quota = body["breakdown_quota"]
    assert quota["remaining"] == 5, (
        "Yesterday's calls must not count against today's chip"
    )


@pytest.mark.asyncio
async def test_breakdown_quota_resets_at_is_next_local_midnight(quota_user, db: AsyncSession):
    """resets_at is the next local midnight, and does NOT move with the oldest call.

    Replaces test_breakdown_quota_resets_at_matches_oldest_plus_7d. Under the rolling
    window resets_at was a function of the rows, so it drifted every time a call aged
    out and any copy built from it could only approximate. Under a daily window it is
    a property of the clock alone — which is what lets the UI name midnight instead
    of counting days.

    Asserted by requiring the same resets_at before and after inserting calls at two
    different times today: under the old rule those inserts would have moved it.

    The fixture's requests declare X-Timezone-Offset: 0, so local midnight is UTC
    midnight here; the tz-sensitive half is
    test_governor.test_day_boundary_follows_the_users_timezone.
    """
    user_id = quota_user

    midnight_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    expected = midnight_utc + timedelta(days=1)

    async def _resets_at() -> datetime:
        quota = (await _get_today_song(user_id))["breakdown_quota"]
        parsed = datetime.fromisoformat(quota["resets_at"])
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    first = await _resets_at()
    assert abs((first - expected).total_seconds()) < 1, (
        f"Expected next UTC midnight {expected.isoformat()} for a fresh user at "
        f"offset 0, got {first.isoformat()}"
    )

    for ts in (midnight_utc + timedelta(minutes=1), datetime.now(timezone.utc)):
        await db.execute(
            text(
                "INSERT INTO governor_calls (id, user_id, feature, model, "
                "  prompt_tokens_actual, output_tokens_actual, created_at) "
                "VALUES (gen_random_uuid(), :uid, 'breakdown', 'claude-sonnet-4-6', 1000, 500, :ts)"
            ),
            {"uid": str(user_id), "ts": ts},
        )
    await db.commit()

    after = await _resets_at()
    assert after == first, (
        f"resets_at must not depend on when today's calls happened — it is the day "
        f"boundary, not oldest_call + window. Was {first.isoformat()}, now "
        f"{after.isoformat()}"
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
        assert quota["cap"] == 5
        assert quota["remaining"] >= 0
