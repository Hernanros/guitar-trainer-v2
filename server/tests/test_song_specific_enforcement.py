"""FLE-44 — `drill.song_specific` is enforced in code, not asked for in the prompt.

Background (.planning/phases/04.1-ai-drills/04.1-07-EVAL-RERUN-RESULTS.md §5): the
FLE-17 §2 patch restated the L4 rule as mechanically as English allows and Sonnet
still emitted Kashmir D3 ("the engine of the Kashmir groove") and D4 ("the core of
the Kashmir riff") with song_specific=false. The guarantee now lives in
app.models.song.enforce_song_specific, applied by run_technique_breakdown.

No live Anthropic spend: the pure-function tests need nothing, and the end-to-end
test drives run_technique_breakdown through the same fake-client fixture pattern as
test_breakdown_soft_fail.py (which does need the dev Postgres for @governed).
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

from app.models.song import Breakdown, enforce_song_specific


# ---------------------------------------------------------------------------
# Fixtures — plain dicts, no network, no DB
# ---------------------------------------------------------------------------

def _valid_tab_dict() -> dict:
    return {
        "measures": [
            {
                "beats": [
                    {"notes": [{"string": 1, "fret": 0, "duration": "quarter"}]}
                ],
                "time_signature": "4/4",
            }
        ],
        "tuning": ["D", "A", "D", "G", "A", "D"],
    }


def _drill_dict(**overrides) -> dict:
    d = {
        "name": "Drone the low D",
        "target_skill_temp_id": str(uuid.uuid4()),
        "song_specific": False,
        "what": "Hold the shape and strike it on every beat.",
        "tab_snippet": _valid_tab_dict(),
        "start_bpm": 60,
        "target_bpm": 70,
        "repetitions": 20,
        "success_criterion": "Every string rings.",
    }
    d.update(overrides)
    return d


def _breakdown(drills: list[dict]) -> Breakdown:
    return Breakdown.model_validate(
        {
            "tab": _valid_tab_dict(),
            "chords": [],
            "technique_notes": [],
            "drills": drills,
        }
    )


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------

def test_kashmir_regression_title_in_what_forces_true():
    """The exact 04.1-07 failure: `what` names the song, Sonnet said false."""
    bd = _breakdown(
        [
            _drill_dict(
                name="Lock the 3-against-4",
                song_specific=False,
                what="This is the engine of the Kashmir groove — count it out loud.",
            ),
            _drill_dict(
                name="Walk the riff shape",
                song_specific=False,
                what="The core of the Kashmir riff, one string at a time.",
            ),
        ]
    )

    enforce_song_specific(bd, "Kashmir", "Led Zeppelin")

    assert [d.song_specific for d in bd.drills] == [True, True], (
        "FLE-44: a drill whose `what` names the song MUST come back song_specific=True"
    )


def test_artist_in_what_forces_true():
    """The artist field counts too, not just the title."""
    bd = _breakdown(
        [
            _drill_dict(song_specific=False, what="Led Zeppelin lean on this feel."),
            _drill_dict(song_specific=False, what="Alternate-pick four to the bar."),
        ]
    )

    enforce_song_specific(bd, "Kashmir", "Led Zeppelin")

    assert [d.song_specific for d in bd.drills] == [True, False]


def test_match_is_case_insensitive():
    bd = _breakdown(
        [
            _drill_dict(song_specific=False, what="Drill the KASHMIR sus4 stack."),
            _drill_dict(song_specific=False, what="the little wing turnaround, slowly."),
        ]
    )

    enforce_song_specific(bd, "Little Wing", "Jimi Hendrix")

    # Drill 1 does not name Little Wing / Jimi Hendrix; drill 2 does, lowercased.
    assert [d.song_specific for d in bd.drills] == [False, True]


def test_generic_drill_is_left_alone():
    """No mention of title or artist → the flag is untouched. This is the whole
    point of the one-way rule: the function never invents a song reference."""
    bd = _breakdown(
        [
            _drill_dict(song_specific=False, what="Two-string alternation, no shifts."),
            _drill_dict(song_specific=False, what="Hold the barre, strike on 2 and 4."),
        ]
    )

    enforce_song_specific(bd, "Kashmir", "Led Zeppelin")

    assert [d.song_specific for d in bd.drills] == [False, False]


def test_true_without_a_mention_is_not_cleared():
    """ONE-WAY ONLY. Gate L4 grades the flag as an IFF and this direction can still
    fail the eval — deliberately. Deciding that "the opening riff" is NOT about the
    song is exactly the semantic judgement this function refuses to make."""
    bd = _breakdown(
        [
            _drill_dict(song_specific=True, what="Drill the opening riff's first bar."),
            _drill_dict(song_specific=True, what="The outro turnaround, half speed."),
        ]
    )

    enforce_song_specific(bd, "Kashmir", "Led Zeppelin")

    assert [d.song_specific for d in bd.drills] == [True, True], (
        "enforce_song_specific must never clear the flag"
    )


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_title_and_artist_do_not_match_everything(blank):
    """"" is a substring of every string. A songs row with a blank artist must not
    flip every drill in the breakdown to song_specific=True."""
    bd = _breakdown(
        [
            _drill_dict(song_specific=False, what="Two-string alternation, no shifts."),
            _drill_dict(song_specific=False, what="Hold the barre, strike on 2 and 4."),
        ]
    )

    enforce_song_specific(bd, blank, blank)

    assert [d.song_specific for d in bd.drills] == [False, False]


def test_empty_drills_is_a_noop():
    """Pre-4.1 cached rows read back as drills=[] — must not raise."""
    bd = Breakdown.model_validate(
        {"tab": _valid_tab_dict(), "chords": [], "technique_notes": []}
    )

    assert enforce_song_specific(bd, "Kashmir", "Led Zeppelin").drills == []


def test_returns_the_same_instance():
    """Documented contract: mutates in place, returns the same object for chaining."""
    bd = _breakdown([_drill_dict(), _drill_dict()])

    assert enforce_song_specific(bd, "Kashmir", "Led Zeppelin") is bd


def test_enforced_flag_agrees_with_the_grader_substring_test():
    """The validator and scripts/grade_eval.py gate L4 must apply the SAME test, or
    the enforced value and the graded value drift and the gate lies."""
    title, artist = "Kashmir", "Led Zeppelin"
    whats = [
        "the engine of the Kashmir groove",
        "Led Zeppelin lean on this feel",
        "two-string alternation, no shifts",
        "the KASHMIR sus4 stack",
    ]
    bd = _breakdown(
        [_drill_dict(song_specific=False, what=w) for w in whats[:4]]
    )

    enforce_song_specific(bd, title, artist)

    for drill in bd.drills:
        w = drill.what.lower()
        graded_named = title.lower() in w or artist.lower() in w
        if graded_named:
            assert drill.song_specific, (
                f"grade_eval.py L4 would read names=True for {drill.what!r} — "
                "enforcement must have set the flag to match"
            )


# ---------------------------------------------------------------------------
# End-to-end through run_technique_breakdown (fake Anthropic client, dev Postgres)
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


class _FakeToolUse:
    def __init__(self, payload: dict) -> None:
        self.type = "tool_use"
        self.input = payload


class _FakeMessagesCreateResponse:
    def __init__(self, payload: dict) -> None:
        self.content = [_FakeToolUse(payload)]
        self.usage = SimpleNamespace(input_tokens=100, output_tokens=200)


class _FakeMessages:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def create(self, **kwargs: Any) -> _FakeMessagesCreateResponse:
        return _FakeMessagesCreateResponse(self._payload)

    async def count_tokens(self, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(input_tokens=100)


class _FakeClient:
    def __init__(self, payload: dict) -> None:
        self.messages = _FakeMessages(payload)


@pytest.mark.asyncio
async def test_run_technique_breakdown_enforces_song_specific(monkeypatch):
    """The Kashmir payload verbatim from the 04.1-07 eval, replayed through the real
    run_technique_breakdown code path with a fake Anthropic client."""
    import app.ai.breakdown as breakdown_module
    from app.ai.breakdown import run_technique_breakdown

    payload = {
        "tab": _valid_tab_dict(),
        "chords": [],
        "technique_notes": [],
        "drills": [
            _drill_dict(
                song_specific=False,
                what="This is the engine of the Kashmir groove — count it out loud.",
            ),
            _drill_dict(
                song_specific=False,
                what="Two-string alternation, no position shifts.",
            ),
        ],
    }
    monkeypatch.setattr(
        breakdown_module, "get_client", lambda: _FakeClient(payload)
    )

    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    try:
        async with _make_session() as db:
            breakdown = await run_technique_breakdown(
                "Kashmir",
                "Led Zeppelin",
                [{"id": str(uuid.uuid4()), "name": "Odd-meter feel"}],
                0.5,
                db=db,
                user_id=uuid.UUID(user_id),
            )

        assert [d.song_specific for d in breakdown.drills] == [True, False], (
            "FLE-44: the drill naming Kashmir must be corrected to True on the way "
            "out of run_technique_breakdown; the generic one must be left alone"
        )
    finally:
        await _cleanup_user(user_id)
