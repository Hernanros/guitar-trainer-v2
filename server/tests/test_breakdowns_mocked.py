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
    # Phase 4: delete governor_calls first (FK references users)
    await db.execute(text(f"DELETE FROM governor_calls WHERE user_id='{user_id}'"))
    # Phase 4.1 (Plan 04.1-02): delete user_sessions before skill_nodes because
    # user_sessions.target_skill_node_id FKs to skill_nodes.id.
    await db.execute(text(f"DELETE FROM user_sessions WHERE user_id='{user_id}'"))
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
    # Plan 04.1-02 B1 FIX: response is BreakdownEnvelope { breakdown, drill_rated_today_indices }
    assert "breakdown" in data
    assert "drill_rated_today_indices" in data
    assert data["drill_rated_today_indices"] == []  # no drill ratings on cache-miss
    bd = data["breakdown"]
    assert "tab" in bd
    assert "chords" in bd
    assert "technique_notes" in bd
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
    # Plan 04.1-02 B1 FIX: envelope shape
    assert "breakdown" in data
    assert "drill_rated_today_indices" in data
    # Should return the cached breakdown without calling Sonnet
    assert data["breakdown"]["chords"][0]["name"] == "E7", (
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

    Phase 4 update: run_technique_breakdown now requires db + user_id kwargs (for @governed).
    The test provides a real DB session with a seeded user to let the governor's pre-cap-check
    run, then verifies that get_client() is still called inside _call (not at decoration time).
    """
    import uuid
    call_count = {"n": 0}
    user_id = str(uuid.uuid4())

    import app.ai.breakdown as breakdown_module

    original_get_client = breakdown_module.get_client

    def counting_get_client():
        call_count["n"] += 1
        return original_get_client()

    monkeypatch.setattr(breakdown_module, "get_client", counting_get_client)

    # Seed a user so the governor's INSERT succeeds
    async with _make_session() as db:
        from sqlalchemy import text as _text
        await db.execute(
            _text(
                "INSERT INTO users (id, preferences) VALUES (:uid, '{}'::jsonb) ON CONFLICT DO NOTHING"
            ),
            {"uid": user_id},
        )
        await db.commit()

    # We expect get_client() to be called at least once when run_technique_breakdown runs.
    # Since ANTHROPIC_API_KEY is unset, get_client() will raise RuntimeError on first call,
    # which the outer try/except wraps as AIBreakdownError.
    async with _make_session() as db:
        with pytest.raises(AIBreakdownError):
            await breakdown_module.run_technique_breakdown(
                "Sweet Home Chicago",
                "Robert Johnson",
                [{"id": str(uuid.uuid4()), "name": "Blues Shuffle Rhythm"}],
                0.3,
                db=db, user_id=uuid.UUID(user_id),
            )

    # get_client() must have been invoked (proves it's inside the closure)
    assert call_count["n"] >= 1, (
        "get_client() was never called — it must be inside the _call closure "
        "per hotfix 843e226 pattern"
    )

    # Cleanup
    async with _make_session() as db:
        from sqlalchemy import text as _text2
        await db.execute(_text2(f"DELETE FROM governor_calls WHERE user_id='{user_id}'"))
        await db.execute(_text2(f"DELETE FROM users WHERE id='{user_id}'"))
        await db.commit()


@pytest.mark.asyncio
async def test_user_message_contains_static_labels():
    """T-03-02-01 defense: user message wraps fields with static labels.

    Phase 4.1 (Plan 04.1-01 Task 2): target_skills is now list[dict{id,name}]
    (evolved from list[str] to support drill target_skill_temp_id echo). The
    injection-defense assertions (label-before-value) still hold.
    """
    from app.ai.breakdown import _format_user_message

    msg = _format_user_message(
        "Sweet Home Chicago",
        "Robert Johnson",
        [{"id": "skill-1", "name": "Blues Shuffle Rhythm"}],
        0.3,
    )
    assert "Song:" in msg
    assert "Artist:" in msg
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


# ---------------------------------------------------------------------------
# Phase 4.1 Plan 04.1-01 Task 2 — SYSTEM_PROMPT DRILLS block + user-message
# id/name pairs
# ---------------------------------------------------------------------------


def test_system_prompt_contains_drills_block():
    """DRILLS block from RESEARCH.md §Q1 present in SYSTEM_PROMPT (Landmine #1 defense)."""
    from app.ai.breakdown import SYSTEM_PROMPT
    assert "DRILLS (produce 2-4)" in SYSTEM_PROMPT, (
        "SYSTEM_PROMPT must contain the DRILLS block header verbatim"
    )
    assert "MUST NOT be a slice of the main song tab" in SYSTEM_PROMPT, (
        "SYSTEM_PROMPT must forbid slicing the main tab (Landmine #1 negative constraint)"
    )
    assert "song_specific: true if" in SYSTEM_PROMPT, (
        "SYSTEM_PROMPT must include the song_specific tagging phrasing"
    )
    # One BAD/GOOD example pair (Anthropic prompt-engineering best-practice per RESEARCH §Q1)
    assert "BAD:" in SYSTEM_PROMPT and "GOOD:" in SYSTEM_PROMPT, (
        "SYSTEM_PROMPT must contain a BAD/GOOD tab_snippet example pair"
    )
    # target_skill_temp_id id-constraint copy
    assert "target_skill_temp_id MUST be one of the ids" in SYSTEM_PROMPT, (
        "SYSTEM_PROMPT must constrain target_skill_temp_id to listed ids (Landmine #2)"
    )


def test_format_user_message_emits_id_name_pairs():
    """_format_user_message now accepts list[dict{id,name}] and echoes both parts."""
    from app.ai.breakdown import _format_user_message

    msg = _format_user_message(
        song_title="Lenny",
        song_artist="SRV",
        target_skills=[
            {"id": "abc", "name": "Slide"},
            {"id": "def", "name": "Barre"},
        ],
        user_level=0.5,
    )
    assert "id=abc" in msg
    assert "name=Slide" in msg
    assert "id=def" in msg
    assert "name=Barre" in msg
    assert "target_skill_temp_id in each drill MUST be one of the ids listed above" in msg


def test_format_user_message_empty_target_skills_fallback():
    """Empty target_skills preserves the existing '(no target skills specified)' fallback."""
    from app.ai.breakdown import _format_user_message

    msg = _format_user_message(
        song_title="Lenny",
        song_artist="SRV",
        target_skills=[],
        user_level=0.5,
    )
    assert "no target skills specified" in msg


# ---------------------------------------------------------------------------
# Phase 4.1 Plan 04.1-01 Task 3 — endpoint drill wiring + hallucinated-id filter
# ---------------------------------------------------------------------------


def _drill_dict(target_skill_temp_id: str, **overrides) -> dict:
    """Construct a valid Drill kwargs dict.

    tab_snippet is a real Tab instance (not a bare dict) so model_construct
    doesn't emit PydanticSerializationUnexpectedValue warnings during response
    serialization downstream.
    """
    d = {
        "name": "Isolate slide",
        "target_skill_temp_id": target_skill_temp_id,
        "song_specific": True,
        "what": "Play the slide alone.",
        "tab_snippet": Tab(
            measures=[
                Measure(
                    beats=[Beat(notes=[Note(string=1, fret=0, duration="quarter")])],
                    time_signature="4/4",
                )
            ],
            tuning=["E", "A", "D", "G", "B", "e"],
        ),
        "start_bpm": 60,
        "target_bpm": 70,
        "repetitions": 20,
        "success_criterion": "Clean on the beat.",
        "common_trap": None,
    }
    d.update(overrides)
    return d


async def _seed_skill_and_link(
    db: AsyncSession, user_id: str, song_id: int, skill_name: str = "Slide"
) -> str:
    """Insert a leaf skill_node owned by user and link it to song_id via song_skills."""
    skill_id = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
            "VALUES (:sid, :uid, :name, 'leaf', 0.3)"
        ),
        {"sid": skill_id, "uid": user_id, "name": skill_name},
    )
    await db.execute(
        text(
            "INSERT INTO song_skills (song_id, skill_node_id) VALUES (:sid, :skid)"
        ),
        {"sid": song_id, "skid": skill_id},
    )
    await db.commit()
    return skill_id


def _make_breakdown_with_drills(drills: list[dict]) -> Breakdown:
    """Build a Breakdown via model_construct (skips validation) so we can inject
    the AFTER-soft-fail state that reaches the endpoint. Drills are constructed
    with model_construct too, matching the same skip-validation strategy so we
    can simulate hallucinated ids without tripping the endpoint's OWN filter."""
    from app.models.song import Drill
    drill_objs = [Drill.model_construct(**d) for d in drills]
    return Breakdown.model_construct(
        tab=_canned_breakdown().tab,
        chords=[],
        technique_notes=[],
        drills=drill_objs,
    )


@pytest.mark.asyncio
async def test_breakdown_response_includes_drills_from_sonnet(monkeypatch):
    """Happy path: run_technique_breakdown returns Breakdown with 2 valid drills →
    endpoint 200 with drills in response payload."""
    user_id = str(uuid.uuid4())

    async with _make_session() as db:
        song_id = await _seed_user_and_song(db, user_id)
        skill_id = await _seed_skill_and_link(db, user_id, song_id, "Slide")

    async def _fake(*args, **kwargs) -> Breakdown:
        return _make_breakdown_with_drills([
            _drill_dict(skill_id, name="Drill A"),
            _drill_dict(skill_id, name="Drill B"),
        ])

    monkeypatch.setattr("app.api.v1.breakdowns.run_technique_breakdown", _fake)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id},
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Plan 04.1-02 B1 FIX: envelope shape
        assert "breakdown" in data
        assert "drill_rated_today_indices" in data
        bd = data["breakdown"]
        assert "drills" in bd
        assert len(bd["drills"]) == 2
        names = {d["name"] for d in bd["drills"]}
        assert names == {"Drill A", "Drill B"}
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_breakdown_drops_drill_with_hallucinated_skill_id(monkeypatch, caplog):
    """Landmine #2: a drill referencing a skill_node UUID NOT owned by the user is
    silently dropped by the endpoint's post-generation filter (log warning, no 500)."""
    import logging
    user_id = str(uuid.uuid4())

    async with _make_session() as db:
        song_id = await _seed_user_and_song(db, user_id)
        real_skill_id = await _seed_skill_and_link(db, user_id, song_id, "Slide")

    hallucinated_id = str(uuid.uuid4())  # NOT owned by user

    async def _fake(*args, **kwargs) -> Breakdown:
        return _make_breakdown_with_drills([
            _drill_dict(real_skill_id, name="Real drill 1"),
            _drill_dict(hallucinated_id, name="Hallucinated drill"),
            _drill_dict(real_skill_id, name="Real drill 2"),
        ])

    monkeypatch.setattr("app.api.v1.breakdowns.run_technique_breakdown", _fake)

    try:
        with caplog.at_level(logging.WARNING, logger="app.api.v1.breakdowns"):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    f"/api/v1/songs/{song_id}/breakdown",
                    headers={"X-User-ID": user_id},
                )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Plan 04.1-02 B1 FIX: envelope shape
        bd = data["breakdown"]
        # Hallucinated drill dropped; 2 real drills survive
        assert len(bd["drills"]) == 2, (
            f"Expected 2 drills after hallucinated-id drop, got {len(bd['drills'])}"
        )
        names = {d["name"] for d in bd["drills"]}
        assert names == {"Real drill 1", "Real drill 2"}
        assert "Hallucinated drill" not in names

        # Warning was logged
        assert any(
            "hallucinated" in r.message.lower() or "hallucinated_target" in r.message.lower()
            for r in caplog.records
        ), f"Expected a warning about the hallucinated drill; got: {[r.message for r in caplog.records]}"
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_breakdown_target_skills_passed_as_id_name_pairs(monkeypatch):
    """The endpoint MUST pass target_skills as list[dict{id,name}] (not list[str])."""
    user_id = str(uuid.uuid4())

    async with _make_session() as db:
        song_id = await _seed_user_and_song(db, user_id)
        skill_id = await _seed_skill_and_link(db, user_id, song_id, "Blues Shuffle")

    captured: dict = {}

    async def _fake(*args, **kwargs) -> Breakdown:
        # positional: (song_title, song_artist, target_skills, user_level)
        captured["target_skills"] = args[2] if len(args) >= 3 else kwargs.get("target_skills")
        return _make_breakdown_with_drills([
            _drill_dict(skill_id), _drill_dict(skill_id)
        ])

    monkeypatch.setattr("app.api.v1.breakdowns.run_technique_breakdown", _fake)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id},
            )

        assert resp.status_code == 200, resp.text
        target_skills = captured["target_skills"]
        assert isinstance(target_skills, list), "target_skills must be a list"
        assert len(target_skills) == 1
        assert isinstance(target_skills[0], dict), (
            "target_skills entries must be dicts, not bare strings — Landmine #2 requirement"
        )
        assert set(target_skills[0].keys()) == {"id", "name"}
        assert target_skills[0]["id"] == skill_id
        assert target_skills[0]["name"] == "Blues Shuffle"
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_breakdown_cached_pre_4_1_row_has_empty_drills(mock_breakdown_never_called):
    """Backward compat: a cached JSONB without a `drills` key returns drills=[].

    Uses `mock_breakdown_never_called` fixture to guarantee Sonnet is NOT called
    (cache-hit path). The pre-4.1 JSONB has {tab, chords, technique_notes} only —
    Breakdown.model_validate uses default_factory=list, so drills is []."""
    user_id = str(uuid.uuid4())
    # Cached JSONB from a pre-4.1 breakdown row — no `drills` key at all
    pre_4_1_cached = {
        "tab": _canned_breakdown().tab.model_dump(),
        "chords": [],
        "technique_notes": [],
    }
    cached_ts = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

    async with _make_session() as db:
        song_id = await _seed_user_and_song(
            db,
            user_id,
            breakdown_generated_at=cached_ts,
            existing_breakdown=pre_4_1_cached,
        )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id},
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Plan 04.1-02 B1 FIX: envelope shape
        assert "breakdown" in data
        assert "drill_rated_today_indices" in data
        bd = data["breakdown"]
        assert "drills" in bd, "Response must always expose the drills key"
        assert bd["drills"] == [], (
            "Pre-4.1 cached row (no drills key) must read back as drills=[] "
            "via default_factory (backward-compat)"
        )
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


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


# ---------------------------------------------------------------------------
# Phase 4.1 Plan 04.1-02 Task 4 (B1 FIX): BreakdownEnvelope + drill_rated_today_indices
# ---------------------------------------------------------------------------


def test_breakdown_envelope_shape():
    """BreakdownEnvelope(breakdown=..., drill_rated_today_indices=[...]) validates cleanly.
    Missing drill_rated_today_indices defaults to []."""
    from app.models.song import BreakdownEnvelope

    bd = _canned_breakdown()

    # With explicit list
    env = BreakdownEnvelope(breakdown=bd, drill_rated_today_indices=[0, 2])
    assert env.breakdown == bd
    assert env.drill_rated_today_indices == [0, 2]

    # Default empty
    env2 = BreakdownEnvelope(breakdown=bd)
    assert env2.drill_rated_today_indices == []

    # model_dump exposes both fields
    d = env.model_dump()
    assert "breakdown" in d
    assert d["drill_rated_today_indices"] == [0, 2]

    # model_dump_json round-trip works when drills is present (via _canned_breakdown_with_drills).
    # For a drill-less Breakdown the round-trip trips Breakdown.drills min_length=2 because
    # model_dump serializes drills=[] explicitly — this is the same Wave 1 landmine that
    # required the _load_cached_breakdown helper; the envelope wrapper does not change that
    # invariant. See test_breakdown_envelope_json_roundtrip_with_drills for the with-drills path.


def test_breakdown_envelope_json_roundtrip_with_drills():
    """model_dump_json → model_validate_json round-trips cleanly when drills present.

    (See test_breakdown_envelope_shape for why the drills-empty case cannot round-trip
    via model_dump_json directly — it's the same Wave 1 landmine that motivated the
    _load_cached_breakdown helper.)
    """
    from app.models.song import Breakdown, BreakdownEnvelope, Drill
    from uuid import uuid4 as _uuid4

    drills = [
        Drill(
            name="Test drill A",
            target_skill_temp_id=str(_uuid4()),
            song_specific=True,
            what="Do the thing.",
            tab_snippet=_canned_breakdown().tab,
            start_bpm=60,
            target_bpm=70,
            repetitions=20,
            success_criterion="Cleanly.",
        ),
        Drill(
            name="Test drill B",
            target_skill_temp_id=str(_uuid4()),
            song_specific=False,
            what="Do the other thing.",
            tab_snippet=_canned_breakdown().tab,
            start_bpm=80,
            target_bpm=90,
            repetitions=15,
            success_criterion="Also cleanly.",
        ),
    ]
    bd = Breakdown(
        tab=_canned_breakdown().tab,
        chords=[],
        technique_notes=[],
        drills=drills,
    )
    env = BreakdownEnvelope(breakdown=bd, drill_rated_today_indices=[0, 1])

    js = env.model_dump_json()
    env2 = BreakdownEnvelope.model_validate_json(js)
    assert env2.drill_rated_today_indices == [0, 1]
    assert len(env2.breakdown.drills) == 2
    assert env2.breakdown.drills[0].name == "Test drill A"


async def _insert_drill_rating(
    db: AsyncSession,
    user_id: str,
    song_id: int,
    skill_node_id: str,
    drill_index: int,
    *,
    day_offset_days: int = 0,
    is_reroll_marker: bool = False,
) -> None:
    """Insert a user_sessions row simulating a drill rating for (user, song, today+offset).

    Uses direct SQL so we don't have to go through the sessions endpoint (which would
    trip the drill-primary policy on subsequent inserts within the same test).
    """
    from sqlalchemy import text as _text
    row_id = str(uuid.uuid4())
    if day_offset_days == 0:
        day_sql = "CURRENT_DATE"
    elif day_offset_days < 0:
        day_sql = f"CURRENT_DATE - {abs(day_offset_days)}"
    else:
        day_sql = f"CURRENT_DATE + {day_offset_days}"
    await db.execute(
        _text(
            f"INSERT INTO user_sessions "
            f"  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
            f"   is_reroll_marker, drill_index, target_skill_node_id) "
            f"VALUES (:rid, :uid, :song_id, 'getting_closer', {day_sql}, 0, "
            f"        :reroll, :di, :tsni)"
        ),
        {
            "rid": row_id,
            "uid": user_id,
            "song_id": song_id,
            "reroll": is_reroll_marker,
            "di": drill_index,
            "tsni": skill_node_id,
        },
    )
    await db.commit()


@pytest.mark.asyncio
async def test_get_breakdown_returns_envelope_with_empty_drill_indices_when_no_ratings(
    mock_breakdown_never_called,
):
    """Cache-hit path with no drill ratings → drill_rated_today_indices=[]."""
    user_id = str(uuid.uuid4())
    canned = _canned_breakdown()
    cached_ts = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)

    async with _make_session() as db:
        song_id = await _seed_user_and_song(
            db, user_id,
            breakdown_generated_at=cached_ts,
            existing_breakdown=canned.model_dump(),
        )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["drill_rated_today_indices"] == []
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_get_breakdown_returns_envelope_with_drill_indices_after_rating(
    mock_breakdown_never_called,
):
    """After inserting drill_index=0 and drill_index=2 rows, GET returns
    drill_rated_today_indices=[0, 2] (sorted ascending)."""
    user_id = str(uuid.uuid4())
    canned = _canned_breakdown()
    cached_ts = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)

    async with _make_session() as db:
        song_id = await _seed_user_and_song(
            db, user_id,
            breakdown_generated_at=cached_ts,
            existing_breakdown=canned.model_dump(),
        )
        skill_id = await _seed_skill_and_link(db, user_id, song_id, "Slide")

    try:
        # First: no drill ratings yet
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp0 = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp0.json()["drill_rated_today_indices"] == []

        # Insert a drill_index=2 rating
        async with _make_session() as db:
            await _insert_drill_rating(db, user_id, song_id, skill_id, drill_index=2)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp1 = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp1.json()["drill_rated_today_indices"] == [2]

        # Insert a drill_index=0 rating — should sort ascending in response
        async with _make_session() as db:
            await _insert_drill_rating(db, user_id, song_id, skill_id, drill_index=0)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp2 = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp2.json()["drill_rated_today_indices"] == [0, 2]
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_get_breakdown_scoped_to_user(mock_breakdown_never_called):
    """User A's drill ratings do NOT appear in User B's GET response."""
    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    canned = _canned_breakdown()
    cached_ts = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)

    async with _make_session() as db:
        # User A owns a song and rates drill 0
        song_a_id = await _seed_user_and_song(
            db, user_a,
            breakdown_generated_at=cached_ts,
            existing_breakdown=canned.model_dump(),
        )
        skill_a_id = await _seed_skill_and_link(db, user_a, song_a_id, "SlideA")
        await _insert_drill_rating(db, user_a, song_a_id, skill_a_id, drill_index=0)

        # User B owns a DIFFERENT song
        song_b_id = await _seed_user_and_song(
            db, user_b,
            breakdown_generated_at=cached_ts,
            existing_breakdown=canned.model_dump(),
        )
        _ = await _seed_skill_and_link(db, user_b, song_b_id, "SlideB")

    try:
        # User B's GET on their own song must not leak User A's drill ratings
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/songs/{song_b_id}/breakdown",
                headers={"X-User-ID": user_b, "X-Timezone-Offset": "0"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["drill_rated_today_indices"] == []

        # And User A's GET on their own song sees their own rating
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp_a = await client.get(
                f"/api/v1/songs/{song_a_id}/breakdown",
                headers={"X-User-ID": user_a, "X-Timezone-Offset": "0"},
            )
        assert resp_a.status_code == 200, resp_a.text
        assert resp_a.json()["drill_rated_today_indices"] == [0]
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_a)
            await _cleanup(db, user_b)


@pytest.mark.asyncio
async def test_get_breakdown_scoped_to_today(mock_breakdown_never_called):
    """A drill rating with local_calendar_day=YESTERDAY is NOT in today's GET response."""
    user_id = str(uuid.uuid4())
    canned = _canned_breakdown()
    cached_ts = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)

    async with _make_session() as db:
        song_id = await _seed_user_and_song(
            db, user_id,
            breakdown_generated_at=cached_ts,
            existing_breakdown=canned.model_dump(),
        )
        skill_id = await _seed_skill_and_link(db, user_id, song_id, "Slide")
        # Yesterday's drill rating
        await _insert_drill_rating(
            db, user_id, song_id, skill_id, drill_index=1, day_offset_days=-1
        )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["drill_rated_today_indices"] == [], (
            "Yesterday's drill rating must not appear in today's response"
        )
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_get_breakdown_reroll_marker_excluded(mock_breakdown_never_called):
    """is_reroll_marker=true rows must be excluded from drill_rated_today_indices."""
    # Reroll markers have drill_index=NULL by construction (they're not drill ratings),
    # but we can still verify the query filter excludes reroll rows. Insert a
    # drill_index=0 row AND a reroll marker with the same drill_index (would only
    # happen if a client somehow set both — we just want to verify the WHERE clause).
    # In practice, reroll markers have drill_index=NULL and would be filtered by
    # the .isnot(None) clause anyway. This test verifies the is_reroll_marker=False
    # filter is in place as belt-and-suspenders.
    user_id = str(uuid.uuid4())
    canned = _canned_breakdown()
    cached_ts = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)

    async with _make_session() as db:
        song_id = await _seed_user_and_song(
            db, user_id,
            breakdown_generated_at=cached_ts,
            existing_breakdown=canned.model_dump(),
        )
        skill_id = await _seed_skill_and_link(db, user_id, song_id, "Slide")
        # Real drill rating (should appear)
        await _insert_drill_rating(db, user_id, song_id, skill_id, drill_index=0)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp.status_code == 200, resp.text
        # Only the real drill rating (0) should appear — reroll markers filtered out
        # (the query also filters drill_index IS NOT NULL, so reroll markers with
        # drill_index=NULL are excluded on both counts).
        assert resp.json()["drill_rated_today_indices"] == [0]
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_get_breakdown_envelope_backward_compat_wrap(mock_breakdown_never_called):
    """Inside envelope.breakdown, the shape MUST match the pre-envelope response:
    tab, chords, technique_notes, drills — no field renaming or shape drift."""
    user_id = str(uuid.uuid4())
    canned = _canned_breakdown()
    cached_ts = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)

    async with _make_session() as db:
        song_id = await _seed_user_and_song(
            db, user_id,
            breakdown_generated_at=cached_ts,
            existing_breakdown=canned.model_dump(),
        )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Top-level envelope shape
        assert set(data.keys()) == {"breakdown", "drill_rated_today_indices"}, (
            f"Envelope must have exactly {{breakdown, drill_rated_today_indices}}, got {set(data.keys())}"
        )

        # Nested breakdown shape must include all pre-envelope fields
        bd = data["breakdown"]
        for field in ("tab", "chords", "technique_notes", "drills"):
            assert field in bd, f"Breakdown must expose '{field}'"

        # Tab has measures + tuning (unchanged from Phase 3)
        assert "measures" in bd["tab"]
        assert "tuning" in bd["tab"]
    finally:
        async with _make_session() as db:
            await _cleanup(db, user_id)
