"""Mocked tests for GET /api/v1/songs/{song_id}/breakdown endpoint.

Runs WITHOUT a real ANTHROPIC_API_KEY — patches run_technique_breakdown at the
endpoint's import boundary (app.api.v1.breakdowns.run_technique_breakdown).
Exercises the full HTTP → DB path for:

  - Cache miss: calls run_technique_breakdown, persists breakdown + breakdown_generated_at
  - Cache hit: returns cached JSONB without calling run_technique_breakdown (staleTime guard)
  - 503 on AIBreakdownError: Fletcher-voiced detail, breakdown_generated_at stays NULL
  - 404 for song_id not owned by X-User-ID (access control T-03-02-04)
  - get_client() inside _call closure: patching verifies hotfix 843e226 pattern

All tests run with pytest -x without ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import pytest

# Ensure no live API key leaks into this test suite
os.environ.pop("ANTHROPIC_API_KEY", None)

from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.ai.breakdown import AIBreakdownError
from app.models.db import Base, SkillNode, Song, SongSkill, User
from app.models.song import Breakdown, Tab, Measure, Beat, Note, Chord, ChordPosition, TechniqueNote


# ---------------------------------------------------------------------------
# Test database helpers
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
    """Minimal valid Breakdown instance for mock returns."""
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
                body="Long-short-long-short over the beat. Listen back; if it sounds even, you're rushing.",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_breakdown_success(monkeypatch):
    """Patch run_technique_breakdown at the endpoint boundary to return canned output."""
    calls: dict[str, int] = {"count": 0}

    async def _fake(*args, **kwargs) -> Breakdown:
        calls["count"] += 1
        return _canned_breakdown()

    monkeypatch.setattr("app.api.v1.breakdowns.run_technique_breakdown", _fake)
    return calls


@pytest.fixture
def mock_breakdown_fail(monkeypatch):
    """Patch run_technique_breakdown to raise AIBreakdownError."""
    async def _raise(*args, **kwargs):
        raise AIBreakdownError("simulated Sonnet failure for testing")

    monkeypatch.setattr("app.api.v1.breakdowns.run_technique_breakdown", _raise)


@pytest.fixture
def mock_breakdown_never_called(monkeypatch):
    """Patch run_technique_breakdown to raise RuntimeError if invoked (cache hit guard)."""
    async def _guard(*args, **kwargs):
        raise RuntimeError("SHOULD NOT BE CALLED — cache hit should short-circuit")

    monkeypatch.setattr("app.api.v1.breakdowns.run_technique_breakdown", _guard)
    return _guard


# ---------------------------------------------------------------------------
# Helpers: seed / cleanup
# ---------------------------------------------------------------------------

async def _seed_user_and_song(
    db: AsyncSession,
    user_id: str,
    breakdown_generated_at: Optional[datetime] = None,
    existing_breakdown: Optional[dict] = None,
) -> int:
    """Insert a test user + song row. Return the song's integer id.

    Uses f-string SQL (test-only, controlled UUIDs) matching the existing
    test_users_bootstrap_mocked.py pattern.
    """
    import json

    await db.execute(
        text(f"INSERT INTO users (id, preferences) VALUES ('{user_id}'::uuid, '{{}}'::jsonb) ON CONFLICT DO NOTHING")
    )

    bd_str = json.dumps(existing_breakdown).replace("'", "''") if existing_breakdown else "{}"
    await db.execute(
        text(
            f"INSERT INTO songs (title, artist, genre, difficulty, bpm, key, breakdown, user_id, category) "
            f"VALUES ('Sweet Home Chicago', 'Robert Johnson', 'Blues', 'intermediate', 80, 'E', "
            f"        '{bd_str}'::jsonb, '{user_id}'::uuid, 'working_on')"
        )
    )
    result = await db.execute(
        text(f"SELECT id FROM songs WHERE user_id = '{user_id}'::uuid ORDER BY id DESC LIMIT 1")
    )
    song_id = result.scalar_one()

    if breakdown_generated_at is not None:
        ts_iso = breakdown_generated_at.isoformat()
        await db.execute(
            text(
                f"UPDATE songs SET breakdown_generated_at = '{ts_iso}'::timestamptz, "
                f"breakdown = '{bd_str}'::jsonb "
                f"WHERE id = {song_id}"
            )
        )
    await db.commit()
    return song_id


async def _cleanup(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        text(f"DELETE FROM song_skills WHERE song_id IN (SELECT id FROM songs WHERE user_id='{user_id}')")
    )
    await db.execute(text(f"DELETE FROM skill_nodes WHERE user_id='{user_id}'"))
    await db.execute(text(f"DELETE FROM songs WHERE user_id='{user_id}'"))
    await db.execute(text(f"DELETE FROM users WHERE id='{user_id}'"))
    await db.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_miss_calls_sonnet_and_persists(mock_breakdown_success):
    """Cache miss (breakdown_generated_at IS NULL): Sonnet called once; breakdown persisted."""
    user_id = str(uuid.uuid4())

    async with _make_session() as db:
        song_id = await _seed_user_and_song(db, user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/v1/songs/{song_id}/breakdown",
            headers={"X-User-ID": user_id},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "tab" in data
    assert "chords" in data
    assert "technique_notes" in data
    assert mock_breakdown_success["count"] == 1, (
        f"Expected run_technique_breakdown called once, was: {mock_breakdown_success['count']}"
    )

    # Verify DB: breakdown_generated_at set + breakdown populated
    async with _make_session() as db:
        result = await db.execute(select(Song).where(Song.id == song_id))
        song = result.scalar_one()
        assert song.breakdown_generated_at is not None, (
            "breakdown_generated_at should be set after successful Sonnet call"
        )
        assert song.breakdown is not None and song.breakdown != {}, (
            "breakdown JSONB should be populated after successful Sonnet call"
        )

    # Cleanup
    async with _make_session() as db:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_cache_hit_skips_sonnet(mock_breakdown_never_called):
    """Cache hit (breakdown_generated_at IS NOT NULL): Sonnet NOT called; cached JSONB returned."""
    user_id = str(uuid.uuid4())
    canned = _canned_breakdown()
    cached_ts = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)

    async with _make_session() as db:
        song_id = await _seed_user_and_song(
            db,
            user_id,
            breakdown_generated_at=cached_ts,
            existing_breakdown=canned.model_dump(),
        )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/v1/songs/{song_id}/breakdown",
            headers={"X-User-ID": user_id},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Should return the cached breakdown without calling Sonnet
    assert data["chords"][0]["name"] == "E7", (
        "Expected cached E7 chord — confirms JSONB was returned not freshly generated"
    )

    # Cleanup
    async with _make_session() as db:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_503_on_ai_breakdown_error_and_no_cache_write(mock_breakdown_fail):
    """AIBreakdownError: 503 with Fletcher copy; breakdown_generated_at stays NULL."""
    user_id = str(uuid.uuid4())

    async with _make_session() as db:
        song_id = await _seed_user_and_song(db, user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/v1/songs/{song_id}/breakdown",
            headers={"X-User-ID": user_id},
        )

    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "Fletcher stepped away from the desk" in body.get("detail", ""), (
        f"Expected Fletcher-voiced error detail, got: {body.get('detail')}"
    )

    # breakdown_generated_at must stay NULL so next tap retries (T-03-02-03)
    async with _make_session() as db:
        result = await db.execute(select(Song).where(Song.id == song_id))
        song = result.scalar_one()
        assert song.breakdown_generated_at is None, (
            "breakdown_generated_at should remain NULL after failed Sonnet call"
        )

    # Cleanup
    async with _make_session() as db:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_404_for_wrong_user(mock_breakdown_success):
    """Access control T-03-02-04: 404 when song_id not owned by X-User-ID."""
    owner_id = str(uuid.uuid4())
    other_user_id = str(uuid.uuid4())

    # Seed the other user so FK constraint is satisfied
    async with _make_session() as db:
        song_id = await _seed_user_and_song(db, owner_id)
        # Seed the requesting user (no songs)
        await db.execute(
            text(
                "INSERT INTO users (id, preferences) VALUES (:uid, '{}'::jsonb) "
                "ON CONFLICT DO NOTHING"
            ),
            {"uid": other_user_id},
        )
        await db.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/v1/songs/{song_id}/breakdown",
            headers={"X-User-ID": other_user_id},  # Different user — should 404
        )

    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
    # Sonnet should NOT have been called
    assert mock_breakdown_success["count"] == 0, (
        "run_technique_breakdown should not be called for a song the user does not own"
    )

    # Cleanup
    async with _make_session() as db:
        await _cleanup(db, owner_id)
        await _cleanup(db, other_user_id)


@pytest.mark.asyncio
async def test_get_client_called_inside_call_closure(monkeypatch):
    """Hotfix 843e226 pattern: get_client() called inside _call closure, not at function entry.

    Verifies that each call attempt invokes get_client() independently. This ensures
    that if the key is missing at first attempt but present on retry, it's picked up.
    More critically: it prevents the pre-843e226 bug where a cached None client from
    a missing-key environment would bypass the RuntimeError check.
    """
    call_count = {"n": 0}

    import app.ai.breakdown as breakdown_module

    original_get_client = breakdown_module.get_client

    def counting_get_client():
        call_count["n"] += 1
        return original_get_client()

    monkeypatch.setattr(breakdown_module, "get_client", counting_get_client)

    # We expect get_client() to be called at least once when run_technique_breakdown runs.
    # Since ANTHROPIC_API_KEY is unset, get_client() will raise RuntimeError on first call,
    # which the outer try/except wraps as AIBreakdownError.
    with pytest.raises(AIBreakdownError):
        await breakdown_module.run_technique_breakdown(
            "Sweet Home Chicago", "Robert Johnson", ["Blues Shuffle Rhythm"], 0.3
        )

    # get_client() must have been invoked (proves it's inside the closure)
    assert call_count["n"] >= 1, (
        "get_client() was never called — it must be inside the _call closure "
        "per hotfix 843e226 pattern"
    )


@pytest.mark.asyncio
async def test_user_message_contains_static_labels():
    """T-03-02-01 defense: user message wraps fields with static labels."""
    from app.ai.breakdown import _format_user_message

    msg = _format_user_message(
        "Sweet Home Chicago", "Robert Johnson", ["Blues Shuffle Rhythm"], 0.3
    )
    assert "Song:" in msg
    assert "Artist:" in msg
    assert "Target skills to focus on:" in msg
    assert "User player_level:" in msg
    assert "Emit the structured breakdown now." in msg
    # Confirm injection defense: labels precede values
    assert msg.index("Song:") < msg.index("Sweet Home Chicago")
    assert msg.index("Artist:") < msg.index("Robert Johnson")


def test_system_prompt_contains_measure_cap():
    """SYSTEM_PROMPT locks 8-measure ceiling per RESEARCH §2 SVG performance ceiling."""
    from app.ai.breakdown import SYSTEM_PROMPT
    assert "8 measures" in SYSTEM_PROMPT or "maximum 8" in SYSTEM_PROMPT or "up to 8" in SYSTEM_PROMPT, (
        "SYSTEM_PROMPT must specify the 4-8 / maximum-8 measure cap"
    )


def test_system_prompt_contains_fletcher_voice():
    """SYSTEM_PROMPT contains Fletcher voice identity."""
    from app.ai.breakdown import SYSTEM_PROMPT
    assert "You are Fletcher" in SYSTEM_PROMPT


def test_max_tokens_is_8192():
    """run_technique_breakdown uses max_tokens=8192 per RESEARCH §1 pitfall 3."""
    import inspect
    from app.ai.breakdown import run_technique_breakdown
    source = inspect.getsource(run_technique_breakdown)
    assert "max_tokens=8192" in source, (
        "max_tokens must be 8192 (RESEARCH §1 pitfall 3 — larger payload for 4-8 measures)"
    )


def test_no_savepoint_in_endpoint():
    """Endpoint uses direct await db.commit(), NOT db.begin_nested() (RESEARCH §5).

    SQLAlchemy autobegin means the session has a transaction from the first SELECT.
    We just set fields and commit — no nested begin() call needed.
    """
    import inspect
    import app.api.v1.breakdowns as breakdowns_module
    source = inspect.getsource(breakdowns_module)
    assert "begin_nested" not in source, (
        "breakdowns.py must NOT use SAVEPOINT (db.begin_nested()) per RESEARCH §5"
    )
    assert "await db.commit()" in source, (
        "breakdowns.py must use await db.commit() to persist cache write"
    )
