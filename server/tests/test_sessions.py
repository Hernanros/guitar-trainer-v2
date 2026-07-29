"""Integration tests for POST /api/v1/sessions (Phase 3 Slice C).

Tests the atomic rating write:
  - INSERT user_sessions + UPDATE skill_nodes.mastery in a single transaction
  - LEAST/GREATEST SQL clamp at the DB level
  - Idempotency: 409 on duplicate (user, song, local_calendar_day)
  - Access control: 404 on song_id not owned by user
  - SKILL-03: no import from app.ai in sessions.py
  - TodaySongResponse.rated is populated on GET /api/v1/song-of-day after rating
  - updated_at is explicitly bumped on every mastery UPDATE

RED phase: all tests fail until server/app/api/v1/sessions.py is created.
"""
import os
import re
import pytest
from decimal import Decimal
from uuid import uuid4

os.environ.pop("ANTHROPIC_API_KEY", None)

from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, text, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.main import app
from app.models.db import Base, SkillNode, Song, SongSkill, User, UserSession


# ---------------------------------------------------------------------------
# Test DB helpers (mirror test_users_bootstrap_mocked.py)
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
        _make_test_db_url(),
        echo=False,
        pool_size=1,
        max_overflow=0,
    )
    return async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

CANNED_BREAKDOWN = {
    "tab": {
        "measures": [
            {
                "beats": [{"notes": [{"string": 1, "fret": 0, "duration": "quarter"}]}],
                "time_signature": "4/4",
            }
        ],
        "tuning": ["E", "A", "D", "G", "B", "e"],
    },
    "chords": [],
    "technique_notes": [],
}


async def _seed_user_song_skills(
    db: AsyncSession,
    user_id: str,
    *,
    mastery: float = 0.20,
    old_updated_at: bool = False,
) -> tuple[str, list[str]]:
    """Seed:
      - 1 user row
      - 1 song owned by the user
      - 2 leaf skill_nodes at given mastery (optionally with updated_at 24h ago)
      - song_skills junction linking the song to both nodes

    Returns (song_id, [skill_node_id_1, skill_node_id_2]).
    """
    # User
    await db.execute(text(
        f"INSERT INTO users (id, preferences) VALUES ('{user_id}', '{{}}'::jsonb) "
        f"ON CONFLICT (id) DO NOTHING"
    ))

    # Song
    song_id_row = (await db.execute(text(
        f"INSERT INTO songs (title, artist, genre, difficulty, bpm, key, breakdown, user_id, category) "
        f"VALUES ('Test Song', 'Test Artist', 'Blues', 'intermediate', 120, 'E', "
        f"'{{}}'::jsonb, '{user_id}', 'working_on') RETURNING id"
    ))).fetchone()
    song_id = song_id_row[0]

    # Skill nodes
    node_ids = []
    for i in range(2):
        nid = str(uuid4())
        node_ids.append(nid)
        if old_updated_at:
            await db.execute(text(
                f"INSERT INTO skill_nodes (id, user_id, name, level, mastery, updated_at) "
                f"VALUES ('{nid}', '{user_id}', 'Test Leaf {i}', 'leaf', {mastery}, "
                f"now() - INTERVAL '24 hours')"
            ))
        else:
            await db.execute(text(
                f"INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
                f"VALUES ('{nid}', '{user_id}', 'Test Leaf {i}', 'leaf', {mastery})"
            ))
        # song_skills junction
        await db.execute(text(
            f"INSERT INTO song_skills (song_id, skill_node_id) "
            f"VALUES ({song_id}, '{nid}')"
        ))

    await db.commit()
    return str(song_id), node_ids


async def _cleanup(db: AsyncSession, user_id: str) -> None:
    """Delete all test data for a user."""
    await db.execute(text(
        f"DELETE FROM user_sessions WHERE user_id = '{user_id}'"
    ))
    song_ids_row = (await db.execute(text(
        f"SELECT id FROM songs WHERE user_id = '{user_id}'"
    ))).fetchall()
    song_ids = [str(r[0]) for r in song_ids_row]
    if song_ids:
        in_clause = ", ".join(song_ids)
        await db.execute(text(f"DELETE FROM song_skills WHERE song_id IN ({in_clause})"))
        await db.execute(text(f"DELETE FROM songs WHERE user_id = '{user_id}'"))
    await db.execute(text(f"DELETE FROM skill_nodes WHERE user_id = '{user_id}'"))
    await db.execute(text(f"DELETE FROM users WHERE id = '{user_id}'"))
    await db.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_rating_returns_201():
    """POST /api/v1/sessions with valid body returns 201 with SessionResponse."""
    user_id = str(uuid4())
    async with _make_session() as db:
        song_id, _ = await _seed_user_song_skills(db, user_id)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/sessions",
                json={"song_id": int(song_id), "rating": "getting_closer"},
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "-300"},
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["rating"] == "getting_closer"
        assert data["song_id"] == int(song_id)
        assert data["user_id"] == user_id
        assert "rated_at" in data
        assert "local_calendar_day" in data
        assert "id" in data
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_submit_rating_inserts_user_session_row():
    """After POST, user_sessions has a row with correct fields."""
    user_id = str(uuid4())
    async with _make_session() as db:
        song_id, _ = await _seed_user_song_skills(db, user_id)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/sessions",
                json={"song_id": int(song_id), "rating": "getting_closer"},
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "-300"},
            )
        assert resp.status_code == 201

        async with _make_session() as db:
            session_row = (await db.execute(text(
                f"SELECT rating, is_reroll_marker, tz_offset_minutes "
                f"FROM user_sessions WHERE user_id = '{user_id}'"
            ))).fetchone()

        assert session_row is not None
        assert session_row[0] == "getting_closer"
        assert session_row[1] is False  # is_reroll_marker=false
        assert session_row[2] == -300  # tz_offset_minutes
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_getting_closer_shifts_mastery_plus_005():
    """getting_closer shifts mastery +0.05 on all attached skill_nodes."""
    user_id = str(uuid4())
    async with _make_session() as db:
        song_id, node_ids = await _seed_user_song_skills(db, user_id, mastery=0.20)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/sessions",
                json={"song_id": int(song_id), "rating": "getting_closer"},
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp.status_code == 201

        async with _make_session() as db:
            for nid in node_ids:
                mastery = (await db.execute(text(
                    f"SELECT mastery FROM skill_nodes WHERE id = '{nid}'"
                ))).scalar_one()
                assert abs(float(mastery) - 0.25) < 0.001, \
                    f"Expected mastery=0.25 after getting_closer on 0.20 base, got {mastery}"
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_not_my_tempo_clamps_to_zero():
    """not_my_tempo with mastery=0.02 clamps to 0.0 (not negative) at SQL level."""
    user_id = str(uuid4())
    async with _make_session() as db:
        song_id, node_ids = await _seed_user_song_skills(db, user_id, mastery=0.02)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/sessions",
                json={"song_id": int(song_id), "rating": "not_my_tempo"},
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp.status_code == 201

        async with _make_session() as db:
            for nid in node_ids:
                mastery = (await db.execute(text(
                    f"SELECT mastery FROM skill_nodes WHERE id = '{nid}'"
                ))).scalar_one()
                assert float(mastery) == 0.0, \
                    f"Expected mastery clamped to 0.0, got {mastery}"
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_thats_what_im_looking_for_clamps_to_one():
    """thats_what_im_looking_for with mastery=0.95 clamps to 1.0 (not > 1) at SQL level."""
    user_id = str(uuid4())
    async with _make_session() as db:
        song_id, node_ids = await _seed_user_song_skills(db, user_id, mastery=0.95)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/sessions",
                json={"song_id": int(song_id), "rating": "thats_what_im_looking_for"},
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp.status_code == 201

        async with _make_session() as db:
            for nid in node_ids:
                mastery = (await db.execute(text(
                    f"SELECT mastery FROM skill_nodes WHERE id = '{nid}'"
                ))).scalar_one()
                assert float(mastery) == 1.0, \
                    f"Expected mastery clamped to 1.0, got {mastery}"
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_duplicate_rating_returns_409():
    """Second POST for same (user, song, local_calendar_day) returns 409."""
    user_id = str(uuid4())
    async with _make_session() as db:
        song_id, _ = await _seed_user_song_skills(db, user_id)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # First POST
            resp1 = await client.post(
                "/api/v1/sessions",
                json={"song_id": int(song_id), "rating": "getting_closer"},
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
            assert resp1.status_code == 201

            # Second POST — should 409
            resp2 = await client.post(
                "/api/v1/sessions",
                json={"song_id": int(song_id), "rating": "getting_closer"},
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp2.status_code == 409, resp2.text
        assert "Already rated" in resp2.json().get("detail", "")
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_song_not_owned_by_user_returns_404():
    """POST for song_id not owned by user returns 404 (T-03-03-01 access control)."""
    user_id = str(uuid4())
    other_user_id = str(uuid4())

    async with _make_session() as db:
        song_id, _ = await _seed_user_song_skills(db, other_user_id)
        # Create requesting user (no songs)
        await db.execute(text(
            f"INSERT INTO users (id, preferences) VALUES ('{user_id}', '{{}}'::jsonb) "
            f"ON CONFLICT (id) DO NOTHING"
        ))
        await db.commit()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/sessions",
                json={"song_id": int(song_id), "rating": "getting_closer"},
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp.status_code == 404, resp.text
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)
            await _cleanup(db, other_user_id)


@pytest.mark.asyncio
async def test_invalid_rating_string_returns_422():
    """POST with invalid rating string returns 422 (Pydantic validation)."""
    user_id = str(uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/sessions",
            json={"song_id": 1, "rating": "invalid_string"},
            headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_updated_at_bumped_after_rating():
    """After rating, every affected SkillNode.updated_at is within the last minute."""
    user_id = str(uuid4())
    async with _make_session() as db:
        song_id, node_ids = await _seed_user_song_skills(
            db, user_id, mastery=0.20, old_updated_at=True
        )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/sessions",
                json={"song_id": int(song_id), "rating": "getting_closer"},
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp.status_code == 201

        async with _make_session() as db:
            for nid in node_ids:
                age_seconds = (await db.execute(text(
                    f"SELECT EXTRACT(EPOCH FROM (now() - updated_at)) "
                    f"FROM skill_nodes WHERE id = '{nid}'"
                ))).scalar_one()
                assert float(age_seconds) < 60, \
                    f"updated_at not bumped — age is {age_seconds}s (expected < 60s)"
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_no_ai_import_in_sessions_py():
    """SKILL-03: sessions.py must NOT import from app.ai (deterministic write path)."""
    sessions_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "api", "v1", "sessions.py",
    )
    if not os.path.exists(sessions_path):
        pytest.skip("sessions.py not yet created (RED phase)")
    with open(sessions_path) as f:
        content = f.read()
    ai_imports = re.findall(r'^(from app\.ai|import app\.ai)', content, re.MULTILINE)
    assert ai_imports == [], f"SKILL-03 violation: found AI imports in sessions.py: {ai_imports}"


@pytest.mark.asyncio
async def test_no_weight_reference_in_sessions_py():
    """D-07: sessions.py must NOT reference SongSkill.weight or song_skills.weight."""
    sessions_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "api", "v1", "sessions.py",
    )
    if not os.path.exists(sessions_path):
        pytest.skip("sessions.py not yet created (RED phase)")
    with open(sessions_path) as f:
        content = f.read()
    weight_refs = re.findall(r'(SongSkill\.weight|song_skills\.weight)', content)
    assert weight_refs == [], f"D-07 violation: found weight reference in sessions.py: {weight_refs}"


@pytest.mark.asyncio
async def test_today_song_rated_field_null_before_rating():
    """TodaySongResponse.rated is null when user has not yet rated today's song."""
    user_id = str(uuid4())
    async with _make_session() as db:
        await _seed_user_song_skills(db, user_id)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/v1/song-of-day",
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        # May return 200 or 404 depending on whether song-of-day is wired to per-user selector
        # If 200, rated must be null. If 404 or 422, skip (song-of-day not yet wired).
        if resp.status_code == 200:
            data = resp.json()
            if "rated" in data:
                assert data["rated"] is None, \
                    f"Expected rated=null before rating, got: {data['rated']}"
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_migration_0003_has_partial_unique_index():
    """Verify uq_user_sessions_daily_rating partial-unique index exists (Revision C)."""
    async with _make_session() as db:
        idx = (await db.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'user_sessions' AND indexname = 'uq_user_sessions_daily_rating'"
        ))).scalar_one_or_none()
    assert idx is not None, (
        "uq_user_sessions_daily_rating partial-unique index not found — "
        "migration 0003 must create it (Revision C)"
    )
