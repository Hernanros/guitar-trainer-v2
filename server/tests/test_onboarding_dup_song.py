"""Regression test for prod bug 2026-08-16 — duplicate song across wizard categories.

Bug: User `28c6b9ea-09e2-4e40-a362-910e10f0a0e5` typed "lenny" in BOTH `working_on` and
`aspirational` wizard sections. Sonnet correctly returned two `SonnetSongProposal` entries
sharing (title, artist) with different `category` values. The songs INSERT loop in
`_persist_bootstrap` flushed each song separately; the second `Lenny` INSERT hit the
DB unique index `songs_user_title_artist_uidx` (migration 0003, unique on
`(user_id, lower(title), lower(artist))` — category NOT in key). Transaction rolled back,
user got 0 songs and 0 skill_nodes.

Fix: `_coalesce_song_proposals` in server/app/api/v1/users.py normalizes duplicates
before the insert loop with precedence `working_on > aspirational > can_play` and
UNIONs the merged proposals' `skill_temp_ids`.

Test strategy (mirrors test_onboarding_governor_savepoint.py):
- Patch the Anthropic client at the `get_client` boundary so the real @governed
  decorator, real _persist_bootstrap, real DB writes all run.
- Feed a canned Sonnet response with two proposals for the same song, different
  category values (`aspirational` first in output order, `working_on` second — this
  ordering matters because it proves precedence beats first-seen).
- Assert exactly one songs row, category = working_on (highest priority), and
  song_skills rows exist for the UNION of skill_temp_ids from both proposals.

If someone regresses `_persist_bootstrap` and removes the coalesce, this test fails
with the exact prod error: `UniqueViolationError` on `songs_user_title_artist_uidx`.
"""
from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Remove ANTHROPIC_API_KEY from env before any imports that might try to init the client.
os.environ.pop("ANTHROPIC_API_KEY", None)

from app.main import app
from app.ai.onboarding import FIXED_ROOTS


# ---------------------------------------------------------------------------
# Test DB helpers (identical pattern to test_onboarding_governor_savepoint.py)
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


async def _cleanup(user_id: str) -> None:
    """Remove all test data for a user (including governor_calls rows)."""
    async with _make_session() as db:
        await db.execute(
            text(
                "DELETE FROM song_skills WHERE song_id IN "
                "(SELECT id FROM songs WHERE user_id = :uid)"
            ),
            {"uid": user_id},
        )
        await db.execute(text("DELETE FROM songs WHERE user_id = :uid"), {"uid": user_id})
        await db.execute(
            text("DELETE FROM skill_node_proposals WHERE user_id = :uid"), {"uid": user_id}
        )
        await db.execute(
            text("DELETE FROM governor_calls WHERE user_id = :uid"), {"uid": user_id}
        )
        await db.execute(text("DELETE FROM skill_nodes WHERE user_id = :uid"), {"uid": user_id})
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        await db.commit()


# ---------------------------------------------------------------------------
# Canned Sonnet responses
# ---------------------------------------------------------------------------


def _duplicate_lenny_output_dict() -> dict[str, Any]:
    """Mirrors the exact prod bug shape.

    Two `SonnetSongProposal` entries for ("Lenny", "Stevie Ray Vaughan") with
    different `category` values. Skill_temp_ids differ between proposals to exercise
    the UNION merge behavior. `aspirational` appears FIRST so we can assert that the
    precedence rule (working_on > aspirational) beats first-seen order.
    """
    skill_graph = []
    for i, root in enumerate(FIXED_ROOTS):
        skill_graph.append({
            "temp_id": f"root-{i}",
            "name": root,
            "level": "root",
            "parent_temp_id": None,
        })
    # One sub under Lead
    skill_graph.append({
        "temp_id": "sub-blues",
        "name": "Blues Lead",
        "level": "sub",
        "parent_temp_id": "root-1",  # Lead
    })
    # Two leaves — proposals will reference different subsets, forcing the UNION path.
    skill_graph.append({
        "temp_id": "leaf-double-stops",
        "name": "Blues Double Stops",
        "level": "leaf",
        "parent_temp_id": "sub-blues",
        "tempo_bin_low": 80,
        "tempo_bin_high": 85,
    })
    skill_graph.append({
        "temp_id": "leaf-string-bends",
        "name": "Whole-Step String Bends",
        "level": "leaf",
        "parent_temp_id": "sub-blues",
        "tempo_bin_low": 80,
        "tempo_bin_high": 85,
    })

    songs = [
        # Aspirational FIRST — proves precedence, not first-seen, drives category choice.
        {
            "title": "Lenny",
            "artist": "Stevie Ray Vaughan",
            "category": "aspirational",
            "skill_temp_ids": ["leaf-double-stops"],
        },
        # Working_on SECOND — must win the category tiebreak.
        {
            "title": "Lenny",
            "artist": "Stevie Ray Vaughan",
            "category": "working_on",
            "skill_temp_ids": ["leaf-string-bends"],
        },
    ]
    return {"songs": songs, "skill_graph": skill_graph}


def _case_insensitive_dup_output_dict() -> dict[str, Any]:
    """Sonnet returned same song with mismatched title/artist casing.

    Real users type "lenny" and "Lenny" and "LENNY" — the DB uidx uses lower().
    Coalesce key must match the DB key exactly, otherwise duplicates slip through
    normalization and re-trigger the prod bug. This test also stresses that.
    """
    skill_graph = [
        {"temp_id": f"root-{i}", "name": root, "level": "root", "parent_temp_id": None}
        for i, root in enumerate(FIXED_ROOTS)
    ]
    skill_graph.append({
        "temp_id": "sub-rock",
        "name": "Rock Rhythm",
        "level": "sub",
        "parent_temp_id": "root-0",  # Rhythm
    })
    skill_graph.append({
        "temp_id": "leaf-power-chords",
        "name": "Power Chords",
        "level": "leaf",
        "parent_temp_id": "sub-rock",
        "tempo_bin_low": 100,
        "tempo_bin_high": 105,
    })

    songs = [
        # Same song, three casings, three categories — coalesce must collapse to ONE row.
        {
            "title": "beat it",
            "artist": "Michael Jackson",
            "category": "can_play",
            "skill_temp_ids": ["leaf-power-chords"],
        },
        {
            "title": "Beat It",
            "artist": "Michael Jackson",
            "category": "aspirational",
            "skill_temp_ids": [],
        },
        {
            "title": "BEAT IT",
            "artist": "michael jackson",
            "category": "working_on",
            "skill_temp_ids": ["leaf-power-chords"],
        },
    ]
    return {"songs": songs, "skill_graph": skill_graph}


# ---------------------------------------------------------------------------
# Mock clients (mirrors test_onboarding_governor_savepoint.py fixtures)
# ---------------------------------------------------------------------------


def _build_mock_onboarding_client(output_dict: dict[str, Any]) -> MagicMock:
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.input = output_dict

    usage = MagicMock()
    usage.input_tokens = 42
    usage.output_tokens = 21

    resp = MagicMock()
    resp.content = [tool_use]
    resp.usage = usage

    count_tokens_result = MagicMock()
    count_tokens_result.input_tokens = 17

    client = MagicMock()
    client.messages.create = AsyncMock(return_value=resp)
    client.messages.count_tokens = AsyncMock(return_value=count_tokens_result)
    return client


def _build_mock_verifier_client() -> MagicMock:
    """Verifier returns verdict='yes' for every sub/leaf → all inserted as canonicals.

    Keeps the pipeline surface simple: none of the proposals get dropped, so any
    missing song_skills row would be attributable to the coalesce logic, not the
    verifier pipeline.
    """
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.input = {"verdict": "yes", "root": "Rhythm", "reason": "regression fixture"}

    usage = MagicMock()
    usage.input_tokens = 20
    usage.output_tokens = 10

    resp = MagicMock()
    resp.content = [tool_use]
    resp.usage = usage

    count_tokens_result = MagicMock()
    count_tokens_result.input_tokens = 15

    client = MagicMock()
    client.messages.create = AsyncMock(return_value=resp)
    client.messages.count_tokens = AsyncMock(return_value=count_tokens_result)
    return client


def _bootstrap_body(user_id: str) -> dict:
    """Mirrors the exact prod raw_onboarding_text shape from user 28c6b9ea-..."""
    return {
        "user_id": user_id,
        "songs": {
            "can_play": [],
            "working_on": ["Lenny"],
            "aspirational": ["Lenny"],
        },
        "preferences": {"session_length_min": 30, "retention_format": "streak"},
        "raw_input": {
            "can_play": "",
            "working_on": "lenny",
            "aspirational": "lenny",
        },
    }


def _bootstrap_body_case_insensitive(user_id: str) -> dict:
    return {
        "user_id": user_id,
        "songs": {
            "can_play": ["beat it"],
            "working_on": ["BEAT IT"],
            "aspirational": ["Beat It"],
        },
        "preferences": {"session_length_min": 30, "retention_format": "streak"},
        "raw_input": {
            "can_play": "beat it",
            "working_on": "BEAT IT",
            "aspirational": "Beat It",
        },
    }


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_coalesces_duplicate_song_across_categories():
    """POST /api/v1/users returns 201 (not 500) when Sonnet emits duplicate song proposals.

    Before the fix: the songs INSERT loop hit `songs_user_title_artist_uidx` on the
    second proposal, the SAVEPOINT rolled back, and the endpoint returned 500
    (UniqueViolationError). After the fix: `_coalesce_song_proposals` folds the two
    proposals into one before insert; the endpoint succeeds with exactly one songs row.
    """
    user_id = str(uuid.uuid4())

    try:
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(_duplicate_lenny_output_dict()),
        ):
            with patch(
                "app.ai.skill_verifier.get_client",
                return_value=_build_mock_verifier_client(),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))

        assert resp.status_code == 201, (
            f"Bootstrap must return 201 when a song appears in multiple wizard "
            f"categories. Before the coalesce fix this was a 500 "
            f"(UniqueViolationError on songs_user_title_artist_uidx). "
            f"Got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["mode"] == "full", (
            f"Expected mode='full' (full graph persisted), got: {data['mode']}. "
            f"mode='bootstrap' here would mean the SAVEPOINT still rolled back."
        )

        # Verify DB state — exactly one songs row with the higher-priority category.
        async with _make_session() as db:
            song_rows = (await db.execute(
                text(
                    "SELECT id, title, artist, category FROM songs "
                    "WHERE user_id = :uid ORDER BY id"
                ),
                {"uid": user_id},
            )).mappings().all()

            song_skill_rows = (await db.execute(
                text(
                    "SELECT ss.skill_node_id, sn.name FROM song_skills ss "
                    "JOIN songs s ON s.id = ss.song_id "
                    "JOIN skill_nodes sn ON sn.id = ss.skill_node_id "
                    "WHERE s.user_id = :uid"
                ),
                {"uid": user_id},
            )).mappings().all()

        assert len(song_rows) == 1, (
            f"Expected exactly 1 songs row for user (Sonnet's 2 duplicate proposals "
            f"must coalesce). Got {len(song_rows)}: "
            f"{[dict(r) for r in song_rows]}"
        )
        assert song_rows[0]["title"] == "Lenny"
        assert song_rows[0]["artist"] == "Stevie Ray Vaughan"
        assert song_rows[0]["category"] == "working_on", (
            f"Category precedence must be working_on > aspirational > can_play. "
            f"The aspirational proposal was first in Sonnet's output; the working_on "
            f"proposal was second. working_on must win. "
            f"Got category={song_rows[0]['category']!r}."
        )

        # song_skills must contain the UNION of skill_temp_ids across both proposals.
        skill_names = {row["name"] for row in song_skill_rows}
        assert skill_names == {"Blues Double Stops", "Whole-Step String Bends"}, (
            f"song_skills junction must contain the UNION of skill_temp_ids from "
            f"both merged proposals (Blues Double Stops from the aspirational proposal, "
            f"Whole-Step String Bends from the working_on proposal). No skill mapping "
            f"may be lost during coalesce. Got: {sorted(skill_names)}"
        )
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_bootstrap_coalesces_case_insensitive_and_across_all_three_categories():
    """Three casings of the same song across all three wizard categories → one row.

    The DB unique index uses lower(title), lower(artist). Coalesce must match on the
    same normalization. Also verifies that when working_on is present anywhere,
    it wins over both aspirational AND can_play.
    """
    user_id = str(uuid.uuid4())

    try:
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(_case_insensitive_dup_output_dict()),
        ):
            with patch(
                "app.ai.skill_verifier.get_client",
                return_value=_build_mock_verifier_client(),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post(
                        "/api/v1/users", json=_bootstrap_body_case_insensitive(user_id)
                    )

        assert resp.status_code == 201, (
            f"Expected 201 with three case-varying proposals for the same song. "
            f"Got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["mode"] == "full"

        async with _make_session() as db:
            song_rows = (await db.execute(
                text(
                    "SELECT title, artist, category FROM songs WHERE user_id = :uid"
                ),
                {"uid": user_id},
            )).mappings().all()

        assert len(song_rows) == 1, (
            f"Case-insensitive dupes across all three categories must coalesce to 1 row. "
            f"Got {len(song_rows)}: {[dict(r) for r in song_rows]}"
        )
        assert song_rows[0]["category"] == "working_on", (
            f"working_on must beat both aspirational and can_play. "
            f"Got category={song_rows[0]['category']!r}."
        )
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_bootstrap_non_duplicate_songs_unaffected():
    """Sanity check — distinct songs pass through the coalesce unchanged.

    Guards against a future refactor over-aggressively merging on partial matches
    (e.g. treating "Lenny" and "Lenny (Live)" as the same song, or dropping songs
    entirely when the coalesce key changes).
    """
    user_id = str(uuid.uuid4())

    distinct_songs_output = {
        "songs": [
            {
                "title": "Little Wing",
                "artist": "Jimi Hendrix",
                "category": "can_play",
                "skill_temp_ids": [],
            },
            {
                "title": "Lenny",
                "artist": "Stevie Ray Vaughan",
                "category": "working_on",
                "skill_temp_ids": [],
            },
            {
                "title": "Free Bird",
                "artist": "Lynyrd Skynyrd",
                "category": "aspirational",
                "skill_temp_ids": [],
            },
        ],
        "skill_graph": [
            {"temp_id": f"root-{i}", "name": root, "level": "root", "parent_temp_id": None}
            for i, root in enumerate(FIXED_ROOTS)
        ],
    }

    try:
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(distinct_songs_output),
        ):
            with patch(
                "app.ai.skill_verifier.get_client",
                return_value=_build_mock_verifier_client(),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post(
                        "/api/v1/users",
                        json={
                            "user_id": user_id,
                            "songs": {
                                "can_play": ["Little Wing"],
                                "working_on": ["Lenny"],
                                "aspirational": ["Free Bird"],
                            },
                            "preferences": {
                                "session_length_min": 30,
                                "retention_format": "streak",
                            },
                            "raw_input": {
                                "can_play": "Little Wing",
                                "working_on": "Lenny",
                                "aspirational": "Free Bird",
                            },
                        },
                    )

        assert resp.status_code == 201, resp.text

        async with _make_session() as db:
            song_rows = (await db.execute(
                text(
                    "SELECT title, category FROM songs "
                    "WHERE user_id = :uid ORDER BY id"
                ),
                {"uid": user_id},
            )).mappings().all()

        assert len(song_rows) == 3, (
            f"Distinct songs must NOT be collapsed by coalesce. Got: "
            f"{[dict(r) for r in song_rows]}"
        )
        titles = [r["title"] for r in song_rows]
        assert titles == ["Little Wing", "Lenny", "Free Bird"], (
            f"Insertion order must be preserved. Got: {titles}"
        )
    finally:
        await _cleanup(user_id)


# ---------------------------------------------------------------------------
# Unit test — _coalesce_song_proposals directly (no DB, fast feedback)
# ---------------------------------------------------------------------------


def test_coalesce_song_proposals_precedence_and_union():
    """Direct unit test of _coalesce_song_proposals covering all precedence cases.

    Runs without a DB — fast feedback loop for the coalesce logic in isolation.
    The end-to-end tests above prove the wiring; this test proves the pure logic.
    """
    from app.api.v1.users import _coalesce_song_proposals
    from app.models.skill_node import SonnetSongProposal

    # Case 1: precedence — working_on beats aspirational (second-seen wins on priority)
    props = [
        SonnetSongProposal(
            title="Lenny", artist="Stevie Ray Vaughan",
            category="aspirational", skill_temp_ids=["a"],
        ),
        SonnetSongProposal(
            title="Lenny", artist="Stevie Ray Vaughan",
            category="working_on", skill_temp_ids=["b"],
        ),
    ]
    out = _coalesce_song_proposals(props)
    assert len(out) == 1
    assert out[0].category == "working_on"
    assert out[0].skill_temp_ids == ["a", "b"], (
        f"UNION must preserve first-seen order. Got: {out[0].skill_temp_ids}"
    )

    # Case 2: precedence — aspirational beats can_play (second-seen wins on priority)
    props = [
        SonnetSongProposal(
            title="Free Bird", artist="Lynyrd Skynyrd",
            category="can_play", skill_temp_ids=["x"],
        ),
        SonnetSongProposal(
            title="Free Bird", artist="Lynyrd Skynyrd",
            category="aspirational", skill_temp_ids=["y"],
        ),
    ]
    out = _coalesce_song_proposals(props)
    assert len(out) == 1
    assert out[0].category == "aspirational"

    # Case 3: first-seen wins when incoming is lower or equal priority
    props = [
        SonnetSongProposal(
            title="Beat It", artist="Michael Jackson",
            category="working_on", skill_temp_ids=["z"],
        ),
        SonnetSongProposal(
            title="Beat It", artist="Michael Jackson",
            category="aspirational", skill_temp_ids=["w"],
        ),
    ]
    out = _coalesce_song_proposals(props)
    assert len(out) == 1
    assert out[0].category == "working_on", (
        f"working_on (first, higher priority) must beat aspirational (second, lower). "
        f"Got: {out[0].category}"
    )
    assert out[0].skill_temp_ids == ["z", "w"]

    # Case 4: case-insensitive title/artist match (DB uidx uses lower()).
    props = [
        SonnetSongProposal(
            title="lenny", artist="stevie ray vaughan",
            category="can_play", skill_temp_ids=["a"],
        ),
        SonnetSongProposal(
            title="Lenny", artist="Stevie Ray Vaughan",
            category="working_on", skill_temp_ids=["b"],
        ),
    ]
    out = _coalesce_song_proposals(props)
    assert len(out) == 1
    assert out[0].category == "working_on"
    # First-seen title/artist casing preserved on the merged row.
    assert out[0].title == "lenny"
    assert out[0].artist == "stevie ray vaughan"

    # Case 5: whitespace normalization (DB uidx sees the raw column, but Sonnet may
    # emit trailing spaces from user input; coalesce strips before keying).
    props = [
        SonnetSongProposal(
            title="Lenny ", artist=" Stevie Ray Vaughan",
            category="aspirational", skill_temp_ids=["a"],
        ),
        SonnetSongProposal(
            title="Lenny", artist="Stevie Ray Vaughan",
            category="working_on", skill_temp_ids=["b"],
        ),
    ]
    out = _coalesce_song_proposals(props)
    assert len(out) == 1
    assert out[0].category == "working_on"

    # Case 6: skill_temp_ids UNION drops duplicates within the union set.
    props = [
        SonnetSongProposal(
            title="X", artist="Y",
            category="can_play", skill_temp_ids=["a", "b"],
        ),
        SonnetSongProposal(
            title="X", artist="Y",
            category="working_on", skill_temp_ids=["b", "c"],
        ),
    ]
    out = _coalesce_song_proposals(props)
    assert len(out) == 1
    assert out[0].skill_temp_ids == ["a", "b", "c"], (
        f"UNION must dedupe within itself. Got: {out[0].skill_temp_ids}"
    )

    # Case 7: distinct songs pass through unchanged, insertion order preserved.
    props = [
        SonnetSongProposal(title="A", artist="X", category="can_play", skill_temp_ids=[]),
        SonnetSongProposal(title="B", artist="X", category="working_on", skill_temp_ids=[]),
        SonnetSongProposal(title="C", artist="X", category="aspirational", skill_temp_ids=[]),
    ]
    out = _coalesce_song_proposals(props)
    assert [s.title for s in out] == ["A", "B", "C"]
    assert [s.category for s in out] == ["can_play", "working_on", "aspirational"]

    # Case 8: empty input.
    assert _coalesce_song_proposals([]) == []
