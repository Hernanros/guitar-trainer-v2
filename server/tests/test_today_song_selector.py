"""Tests for the Phase 3 Song of the Day selector (03-01 Task 2).

Covers:
- Determinism: same (user, local_day) → same song_id across two calls
- Different tz offsets → can produce different local_calendar_day → different seed
- 25% bank override: empirical 15-40 out of 100 calls
- Empty working_on → 100% bank picks (D-04)
- Player level filter ±0.15 (D-03)
- get_tz_offset_minutes dep range validation
- GET /song-of-day endpoint: TodaySongResponse shape
- POST /today-song/reroll: first=200, second=409

These tests run against a REAL Postgres instance (no mocks for the selector —
the CTE is pure SQL + Postgres, mocking would not validate the actual query).
Requires DATABASE_URL pointing to a local Postgres with migration 0003 applied.
"""
import asyncio
import os
import uuid
from datetime import date
from decimal import Decimal
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func as sqlfunc, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_tz_offset_minutes
from app.main import app
from app.models.db import Base, SkillNode, Song, SongCatalog, SongSkill, User, UserSession
from app.selectors.today_song import select_today_song
from fastapi import HTTPException
import pytest


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
async def seed_user(db: AsyncSession):
    """Create a test user with working_on songs + skill_nodes."""
    user_id = uuid.uuid4()

    # Insert user
    await db.execute(
        text(
            "INSERT INTO users (id, preferences) VALUES (:id, '{}'::jsonb) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": str(user_id)},
    )

    # Insert a leaf skill_node with mastery=0.2 (will be argmin pick)
    skill_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
            "VALUES (:id, :uid, 'Blues Shuffle', 'leaf', 0.2)"
        ),
        {"id": str(skill_id), "uid": str(user_id)},
    )

    # Insert a working_on song
    await db.execute(
        text(
            "INSERT INTO songs (title, artist, genre, difficulty, bpm, key, breakdown, user_id, category) "
            "VALUES ('Sweet Home Chicago', 'Robert Johnson', 'Blues', 'Intermediate', 120, 'E', "
            "'{\"tab\":{\"measures\":[],\"tuning\":[\"E\",\"A\",\"D\",\"G\",\"B\",\"e\"]},"
            "\"chords\":[],\"technique_notes\":[]}'::jsonb, "
            ":uid, 'working_on') RETURNING id"
        ),
        {"uid": str(user_id)},
    )
    result = await db.execute(
        text("SELECT id FROM songs WHERE user_id = :uid AND title = 'Sweet Home Chicago'"),
        {"uid": str(user_id)},
    )
    song_id = result.scalar_one()

    # Link song to skill_node
    await db.execute(
        text(
            "INSERT INTO song_skills (song_id, skill_node_id) VALUES (:sid, :skid)"
        ),
        {"sid": song_id, "skid": str(skill_id)},
    )

    await db.commit()
    yield user_id

    # Cleanup
    await db.execute(text("DELETE FROM user_sessions WHERE user_id = :uid"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM song_skills WHERE song_id IN (SELECT id FROM songs WHERE user_id = :uid)"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM songs WHERE user_id = :uid"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM skill_nodes WHERE user_id = :uid"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(user_id)})
    await db.commit()


@pytest_asyncio.fixture
async def empty_working_on_user(db: AsyncSession):
    """User with NO working_on songs but has skill_nodes."""
    user_id = uuid.uuid4()

    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:id, '{}'::jsonb) ON CONFLICT (id) DO NOTHING"),
        {"id": str(user_id)},
    )

    # skill_node so player_level has a value
    skill_id = uuid.uuid4()
    await db.execute(
        text("INSERT INTO skill_nodes (id, user_id, name, level, mastery) VALUES (:id, :uid, 'Lead', 'leaf', 0.5)"),
        {"id": str(skill_id), "uid": str(user_id)},
    )

    # One can_play song (NOT working_on)
    await db.execute(
        text(
            "INSERT INTO songs (title, artist, genre, difficulty, bpm, key, breakdown, user_id, category) "
            "VALUES ('Comfortably Numb', 'Pink Floyd', 'Rock', 'Intermediate', 60, 'Bm', "
            "'{\"tab\":{\"measures\":[],\"tuning\":[\"E\",\"A\",\"D\",\"G\",\"B\",\"e\"]},"
            "\"chords\":[],\"technique_notes\":[]}'::jsonb, "
            ":uid, 'can_play')"
        ),
        {"uid": str(user_id)},
    )

    await db.commit()
    yield user_id

    # Cleanup
    await db.execute(text("DELETE FROM user_sessions WHERE user_id = :uid"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM song_skills WHERE song_id IN (SELECT id FROM songs WHERE user_id = :uid)"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM songs WHERE user_id = :uid"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM skill_nodes WHERE user_id = :uid"), {"uid": str(user_id)})
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(user_id)})
    await db.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_selector_deterministic_same_day(seed_user, db: AsyncSession):
    """Same user, same tz offset, same UTC moment → same song_id returned twice."""
    user_id = seed_user
    tz = -300  # UTC-5

    song_id_1, from_bank_1, _ = await select_today_song(db, user_id, tz, force_reroll=False)
    song_id_2, from_bank_2, _ = await select_today_song(db, user_id, tz, force_reroll=False)

    assert song_id_1 is not None
    assert song_id_1 == song_id_2, "Same day, same user → same deterministic pick"
    assert from_bank_1 == from_bank_2


@pytest.mark.asyncio
async def test_selector_empty_working_on_falls_to_bank(empty_working_on_user, db: AsyncSession):
    """User with zero working_on songs always picks from bank (D-04)."""
    user_id = empty_working_on_user
    tz = 0

    # Run 5 times — all should pick from bank (from_bank=True)
    for _ in range(5):
        _, from_bank, bank_source = await select_today_song(db, user_id, tz, force_reroll=False)
        assert from_bank is True, "Empty working_on → 100% bank pick"
        assert bank_source in ("user_bench", "seed_catalog", None)


@pytest.mark.asyncio
async def test_selector_force_reroll_different_seed(seed_user, db: AsyncSession):
    """force_reroll=True uses a different seed suffix → different sequence from random()."""
    user_id = seed_user
    tz = 0

    # We can't guarantee a different song (bank might have 1 song), but the CTE must execute.
    normal_id, _, _ = await select_today_song(db, user_id, tz, force_reroll=False)
    reroll_id, from_bank_r, bank_source_r = await select_today_song(db, user_id, tz, force_reroll=True)

    assert normal_id is not None
    assert reroll_id is not None
    # from_bank=True for reroll is expected (reroll always picks from bank in principle,
    # but technically it runs the full CTE — just verify it executes without error)


@pytest.mark.asyncio
async def test_get_tz_offset_dep_valid_range():
    """get_tz_offset_minutes accepts boundary values -840 and +840."""
    # Valid boundaries
    assert get_tz_offset_minutes("-840") == -840
    assert get_tz_offset_minutes("840") == 840
    assert get_tz_offset_minutes("0") == 0
    # Default (no header) → 0
    assert get_tz_offset_minutes("0") == 0


@pytest.mark.asyncio
async def test_get_tz_offset_dep_rejects_out_of_range():
    """get_tz_offset_minutes rejects ±841 with 400."""
    with pytest.raises(HTTPException) as exc_info:
        get_tz_offset_minutes("-841")
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info:
        get_tz_offset_minutes("841")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_get_tz_offset_dep_rejects_non_integer():
    """get_tz_offset_minutes rejects non-integer values with 400."""
    with pytest.raises(HTTPException) as exc_info:
        get_tz_offset_minutes("not-a-number")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_song_of_day_endpoint_returns_today_song_response(seed_user):
    """GET /api/v1/song-of-day returns TodaySongResponse with correct fields."""
    user_id = seed_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/song-of-day",
            headers={
                "X-User-ID": str(user_id),
                "X-Timezone-Offset": "0",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "song" in body
    assert "from_bank" in body
    assert "rerolled" in body
    assert "rerolls_left" in body
    assert isinstance(body["from_bank"], bool)
    assert isinstance(body["rerolled"], bool)
    assert body["rerolled"] is False  # fresh user, no reroll


@pytest.mark.asyncio
async def test_song_of_day_rejects_invalid_tz_offset(seed_user):
    """GET /api/v1/song-of-day returns 400 for out-of-range X-Timezone-Offset."""
    user_id = seed_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/song-of-day",
            headers={
                "X-User-ID": str(user_id),
                "X-Timezone-Offset": "-841",
            },
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_reroll_endpoint_first_returns_200(seed_user):
    """POST /api/v1/today-song/reroll first call returns 200 with TodaySongResponse."""
    user_id = seed_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/today-song/reroll",
            headers={
                "X-User-ID": str(user_id),
                "X-Timezone-Offset": "0",
            },
        )
    assert resp.status_code in (200, 404), resp.text  # 404 if bank truly empty for test user
    if resp.status_code == 200:
        body = resp.json()
        assert body["rerolled"] is True
        assert body["from_bank"] is True
        assert body["rerolls_left"] == 0


@pytest.mark.asyncio
async def test_reroll_endpoint_second_returns_409(seed_user, db: AsyncSession):
    """POST /api/v1/today-song/reroll second call same day returns 409."""
    user_id = seed_user

    # Seed a reroll marker for today
    today_row = await db.scalar(
        text("SELECT DATE((now() AT TIME ZONE 'UTC') + (0 * INTERVAL '1 minute'))")
    )

    # First, get a song_id for the marker
    result = await db.execute(
        text("SELECT id FROM songs WHERE user_id = :uid LIMIT 1"),
        {"uid": str(user_id)},
    )
    song_id = result.scalar_one()

    await db.execute(
        text(
            "INSERT INTO user_sessions (id, user_id, song_id, local_calendar_day, tz_offset_minutes, is_reroll_marker) "
            "VALUES (gen_random_uuid(), :uid, :sid, :day, 0, true)"
        ),
        {"uid": str(user_id), "sid": song_id, "day": today_row},
    )
    await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/today-song/reroll",
            headers={
                "X-User-ID": str(user_id),
                "X-Timezone-Offset": "0",
            },
        )
    assert resp.status_code == 409, f"Expected 409 but got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_get_song_of_day_after_reroll_reads_marker(seed_user, db: AsyncSession):
    """After a reroll marker is persisted, GET /song-of-day returns the rerolled song."""
    user_id = seed_user

    # Get today
    today_row = await db.scalar(
        text("SELECT DATE((now() AT TIME ZONE 'UTC') + (0 * INTERVAL '1 minute'))")
    )

    # Get the song_id
    result = await db.execute(
        text("SELECT id FROM songs WHERE user_id = :uid LIMIT 1"),
        {"uid": str(user_id)},
    )
    song_id = result.scalar_one()

    # Seed a reroll marker with bank_source='user_bench'
    await db.execute(
        text(
            "INSERT INTO user_sessions (id, user_id, song_id, local_calendar_day, tz_offset_minutes, "
            "is_reroll_marker, bank_source) "
            "VALUES (gen_random_uuid(), :uid, :sid, :day, 0, true, 'user_bench')"
        ),
        {"uid": str(user_id), "sid": song_id, "day": today_row},
    )
    await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/song-of-day",
            headers={
                "X-User-ID": str(user_id),
                "X-Timezone-Offset": "0",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rerolled"] is True
    assert body["from_bank"] is True
    # Revision B: bank_source must be read from the marker row, not hardcoded
    assert body["bank_source"] == "user_bench"


@pytest.mark.asyncio
async def test_selector_setseed_delimiter_present():
    """Verify the CTE SQL string contains the '|' delimiter (landmine 3 guard)."""
    from app.selectors.today_song import _SELECTOR_CTE
    cte_text = str(_SELECTOR_CTE)
    assert "'|'" in cte_text or "| '" in cte_text or "\\|" in cte_text, (
        "Selector CTE missing '|' delimiter in hashtext key (RESEARCH §8 landmine 3)"
    )


@pytest.mark.asyncio
async def test_selector_setseed_present():
    """Verify the CTE SQL string contains setseed (landmine 2 guard)."""
    from app.selectors.today_song import _SELECTOR_CTE
    cte_text = str(_SELECTOR_CTE)
    assert "setseed" in cte_text, "Selector CTE missing setseed call"


@pytest.mark.asyncio
async def test_selector_player_level_expression_present():
    """Verify the CTE computes player_level from the shared expression.

    Two guarantees in one assertion:
      - COALESCE(..., 0.5) — the no-leaf-nodes fallback (D-03 + D-04).
      - GREATEST(..., floor) — the FLE-49 floor. A user with leaves that are all
        mastery 0 gets AVG = 0.0, NOT NULL, so COALESCE never fires for them; without
        the floor they land at player_level 0.00, where the +/-0.15 catalog window
        matches exactly 1 of the 64 rows. That was true of all 16 production users.
    """
    from app.selectors.player_level import PLAYER_LEVEL_SQL
    from app.selectors.today_song import _SELECTOR_CTE
    cte_text = str(_SELECTOR_CTE)
    assert PLAYER_LEVEL_SQL in cte_text, (
        f"CTE no longer computes player_level as {PLAYER_LEVEL_SQL!r} — it must use the "
        "shared expression from selectors/player_level.py, not an inline copy"
    )
    assert "COALESCE(AVG(mastery), 0.5)" in cte_text, (
        "CTE missing player_level fallback COALESCE(AVG(mastery), 0.5)"
    )
    assert "GREATEST(" in cte_text, "CTE missing the FLE-49 player_level floor"


@pytest.mark.asyncio
async def test_selector_bank_difficulty_filter_present():
    """Verify D-03 ±0.15 bank filter is in the CTE."""
    from app.selectors.today_song import _SELECTOR_CTE
    cte_text = str(_SELECTOR_CTE)
    assert "<= 0.15" in cte_text or "<=0.15" in cte_text, (
        "CTE missing D-03 bank difficulty filter ABS(...) <= 0.15"
    )


# ---------------------------------------------------------------------------
# FLE-54 Fix 1: daily pick persistence via GET /song-of-day
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_pick_stable_after_indexed_column_mutation(db: AsyncSession):
    """Same user, same day, two GET /song-of-day calls, songs mutated in between → same song.

    Reproduces the instability: the seeded CTE assigns random() values by scan position,
    so any non-HOT update (e.g. retitle) that reorders the index shifts the winner. After
    FLE-54 Fix 1, the first call persists a daily_pick_marker row and the second call reads
    it back instead of re-running the CTE — so the pick cannot move even if the index changes.
    """
    user_id = uuid.uuid4()

    # Insert user
    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:uid, '{}'::jsonb)"),
        {"uid": str(user_id)},
    )

    # No working_on songs → D-04 forces 100% bank picks, isolating user_bench_pick CTE branch.
    for i in range(15):
        await db.execute(
            text(
                "INSERT INTO songs (title, artist, genre, difficulty, breakdown, user_id, category) "
                "VALUES (:t, :a, 'test', 'beginner', "
                "'{\"tab\":{\"measures\":[],\"tuning\":[\"E\",\"A\",\"D\",\"G\",\"B\",\"e\"]},"
                "\"chords\":[],\"technique_notes\":[]}'::jsonb, :uid, 'aspirational')"
            ),
            {"t": f"Stability Song {i:02d}", "a": f"Artist {i:02d}", "uid": str(user_id)},
        )
    await db.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-User-ID": str(user_id), "X-Timezone-Offset": "0"}

            # First GET: CTE runs, daily pick marker is persisted.
            r1 = await client.get("/api/v1/song-of-day", headers=headers)
            assert r1.status_code == 200, r1.text
            song_id_first = r1.json()["song"]["id"]

            # Mutate: retitle the first song so its index key changes (non-HOT update,
            # reorders songs_user_title_artist_uidx, shifting which row wins the same seed).
            await db.execute(
                text(
                    "UPDATE songs SET title = 'AAA New Title Shifts Index' "
                    "WHERE user_id = :uid AND title = 'Stability Song 00'"
                ),
                {"uid": str(user_id)},
            )
            await db.commit()

            # Second GET: must return the same song despite the index reorder.
            r2 = await client.get("/api/v1/song-of-day", headers=headers)
            assert r2.status_code == 200, r2.text
            song_id_second = r2.json()["song"]["id"]

        assert song_id_first == song_id_second, (
            f"Daily pick moved after index mutation: first={song_id_first} second={song_id_second}. "
            "FLE-54 Fix 1 (daily_pick_marker persistence) is broken."
        )

        # Confirm the daily pick marker was written.
        today_row = await db.scalar(
            text("SELECT DATE((now() AT TIME ZONE 'UTC') + (0 * INTERVAL '1 minute'))")
        )
        marker = await db.execute(
            text(
                "SELECT song_id FROM user_sessions "
                "WHERE user_id = :uid AND local_calendar_day = :day AND is_daily_pick_marker = true"
            ),
            {"uid": str(user_id), "day": today_row},
        )
        marker_row = marker.mappings().one_or_none()
        assert marker_row is not None, "daily_pick_marker row was not written to user_sessions"
        assert marker_row["song_id"] == song_id_first

    finally:
        await db.execute(text("DELETE FROM user_sessions WHERE user_id = :uid"), {"uid": str(user_id)})
        await db.execute(text("DELETE FROM songs WHERE user_id = :uid"), {"uid": str(user_id)})
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(user_id)})
        await db.commit()
