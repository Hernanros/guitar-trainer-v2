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
    """Minimal valid Tab dict shape — 4 quarter-note beats so FLE-106's METER
    check (bar duration must sum to the time signature) doesn't block these
    unrelated soft-fail tests."""
    return {
        "measures": [
            {
                "beats": [
                    {"notes": [{"string": 1, "fret": 0, "duration": "quarter"}]},
                    {"notes": [{"string": 1, "fret": 0, "duration": "quarter"}]},
                    {"notes": [{"string": 1, "fret": 0, "duration": "quarter"}]},
                    {"notes": [{"string": 1, "fret": 0, "duration": "quarter"}]},
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


# ---------------------------------------------------------------------------
# FLE-143 — the same degrade path, generalised from `drills` to the tab
#
# The soft-fail above covered drills only, so ONE unrecognised articulation
# value anywhere in the tab still 503'd the whole session. The live FLE-89
# measurement caught it twice in ten breakdowns: link_to_next="bend" (a legal
# Bend.kind in the wrong slot) killed Pride and Joy and Comfortably Numb.
#
# The repair lives in the model (app/models/song.py §6.2) rather than in a
# second strip-and-re-parse here, because that is where every other value
# clamp in the schema lives and it covers the cached-read and drill-snippet
# paths too, which a fix at this layer would miss.
# ---------------------------------------------------------------------------

def _tab_dict_with_misrouted_bend() -> dict:
    """`_valid_tab_dict` with the exact defect from both FLE-143 casualties."""
    tab = _valid_tab_dict()
    tab["measures"][0]["beats"][0]["notes"][0]["link_to_next"] = "bend"
    return tab


@pytest.mark.asyncio
async def test_a_misrouted_link_to_next_no_longer_503s_the_breakdown(monkeypatch, caplog):
    """FLE-143: the session is served, and it keeps its drills.

    The drills assertion is the one that proves the fix is at the right layer.
    A tab defect used to fail the FIRST parse, which sent the request down the
    drills-strip path, where it failed again and raised — so the old behaviour
    cost the user the tab AND the drills AND the session. Now the first parse
    succeeds and nothing is stripped.
    """
    import logging
    from app.ai.breakdown import run_technique_breakdown

    payload = _breakdown_dict_with_drills([_valid_drill_dict(), _valid_drill_dict()])
    payload["tab"] = _tab_dict_with_misrouted_bend()
    _install_fake_client(monkeypatch, payload)

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        with caplog.at_level(logging.WARNING, logger="app.models.song"):
            async with _make_session() as db:
                breakdown = await run_technique_breakdown(
                    "Pride and Joy",
                    "Stevie Ray Vaughan",
                    [{"id": str(uuid.uuid4()), "name": "Bending"}],
                    0.5,
                    db=db,
                    user_id=uuid.UUID(user_id),
                )

        note = breakdown.tab.measures[0].beats[0].notes[0]
        assert note.link_to_next is None, "the unrecognised value must be dropped"
        assert note.fret == 0 and note.duration == "quarter", "the note itself survives"
        assert len(breakdown.drills) == 2, (
            "FLE-143: a TAB defect must not cost the drills — that was the old "
            "behaviour, where the tab failure fell into the drills-strip path"
        )
        assert any("link_to_next" in r.getMessage() for r in caplog.records), (
            "the drop must warn, per the repair policy"
        )
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_a_genuinely_broken_tab_still_raises(monkeypatch):
    """§6.2 repairs a wrong VALUE, never a wrong SHAPE — that still refuses."""
    from app.ai.breakdown import AIBreakdownError, run_technique_breakdown

    payload = _breakdown_dict_with_drills([_valid_drill_dict(), _valid_drill_dict()])
    # A note with no string at all has nothing to serve and no honest repair.
    payload["tab"]["measures"][0]["beats"][0]["notes"][0].pop("string")
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


@pytest.mark.asyncio
async def test_a_refusal_persists_the_raw_payload_when_the_dir_is_set(
    monkeypatch, tmp_path
):
    """FLE-143: make the next refusal diagnosable without re-spending.

    The eight refusals in the FLE-89 measurement threw away the only copy of
    what Sonnet said, so the follow-up questions could not be answered without
    paying for the same calls again.
    """
    import json
    from app.ai.breakdown import AIBreakdownError, run_technique_breakdown

    monkeypatch.setenv("FLETCHER_RAW_PAYLOAD_DIR", str(tmp_path))

    payload = _breakdown_dict_with_drills([_valid_drill_dict(), _valid_drill_dict()])
    payload["tab"]["measures"] = "not a list at all"
    _install_fake_client(monkeypatch, payload)

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        async with _make_session() as db:
            with pytest.raises(AIBreakdownError):
                await run_technique_breakdown(
                    "Comfortably Numb",
                    "Pink Floyd",
                    [{"id": str(uuid.uuid4()), "name": "Bending"}],
                    0.5,
                    db=db,
                    user_id=uuid.UUID(user_id),
                )

        dumps = list(tmp_path.glob("*.json"))
        assert len(dumps) == 1, f"expected one persisted payload, got {dumps}"
        saved = json.loads(dumps[0].read_text())
        assert saved["song_title"] == "Comfortably Numb"
        assert saved["raw_input"]["tab"]["measures"] == "not a list at all", (
            "the point is the RAW payload, verbatim"
        )
        assert saved["error_type"] and saved["error"]
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_nothing_is_written_when_the_dir_is_unset(monkeypatch, tmp_path):
    """Opt-in per environment — it must never write to a path nobody chose."""
    from app.ai.breakdown import AIBreakdownError, run_technique_breakdown

    monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)

    payload = _breakdown_dict_with_drills([_valid_drill_dict(), _valid_drill_dict()])
    payload["tab"]["measures"] = "not a list at all"
    _install_fake_client(monkeypatch, payload)

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        async with _make_session() as db:
            with pytest.raises(AIBreakdownError):
                await run_technique_breakdown(
                    "Lenny", "SRV",
                    [{"id": str(uuid.uuid4()), "name": "Slide"}],
                    0.5, db=db, user_id=uuid.UUID(user_id),
                )
        assert list(tmp_path.iterdir()) == []
    finally:
        await _cleanup_user(user_id)
