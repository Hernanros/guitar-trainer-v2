"""FLE-49 — onboarding must seed leaf mastery, so player_level is never 0.00.

The bug, measured against production on 2026-09-16: 16 users, 250 leaf skill_nodes,
3 of them with mastery > 0, max mastery anywhere 0.050. Every user therefore resolved
to player_level = AVG(mastery) = 0.00, and at 0.00 the selector's
ABS(difficulty - player_level) <= 0.15 window matches exactly ONE of the 64 catalog
rows (Knockin' on Heaven's Door, 0.15). Same song every day, reroll included, for
every account that exists.

Root cause: leaves were created at mastery 0.0 and stayed there until the first
session rating. But mastery 0 is a *knowledge* state ("we have never observed this
person play"), not a *skill* state ("this person can play nothing") — and onboarding
already collects the answer that distinguishes them, as can_play / working_on /
aspirational. `_initial_leaf_mastery` turns that answer into a starting position.

Two layers under test here:
  - Unit: _initial_leaf_mastery's category mapping and max-precedence rule.
  - End-to-end: POST /api/v1/users with a canned Sonnet response, asserting the
    leaves actually land non-zero in Postgres and the resulting player_level clears
    the one-song window.

The selector's own floor (selectors/player_level.py) is the backstop for users who
predate this and for mastery decay; it is tested in test_alembic_0007.py. This file
is about the root cause, not the backstop.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Remove ANTHROPIC_API_KEY before importing anything that may init a live client.
os.environ.pop("ANTHROPIC_API_KEY", None)

from app.main import app
from app.ai.onboarding import FIXED_ROOTS
from app.api.v1.users import _ONBOARDING_SEED_MASTERY, _initial_leaf_mastery
from app.models.skill_node import SonnetSongProposal
from app.selectors.player_level import PLAYER_LEVEL_FLOOR

CAN_PLAY_MASTERY = _ONBOARDING_SEED_MASTERY["can_play"]
WORKING_ON_MASTERY = _ONBOARDING_SEED_MASTERY["working_on"]


# ---------------------------------------------------------------------------
# Test DB helpers (same pattern as test_onboarding_dup_song.py)
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
# 1. Unit — the category mapping
# ---------------------------------------------------------------------------


def _song(category: str, skill_temp_ids: list[str], title: str = "T") -> SonnetSongProposal:
    return SonnetSongProposal(
        title=title,
        artist="A",
        category=category,
        skill_temp_ids=skill_temp_ids,
        genre="Rock",
        difficulty="intermediate",
        bpm=120,
        key="Em",
    )


def test_can_play_outranks_working_on_for_a_shared_leaf():
    """A leaf reachable from both a can_play and a working_on song takes the higher value.

    This is the case `_coalesce_song_proposals` would get backwards if we read the
    coalesced list: its precedence is working_on > can_play (correct for deciding
    which single songs row to store), which would rewrite a 0.50 leaf down to 0.30.
    _initial_leaf_mastery reads the RAW proposals for exactly this reason.
    """
    seeded = _initial_leaf_mastery([
        _song("working_on", ["leaf-bends"], title="Working"),
        _song("can_play", ["leaf-bends"], title="Known"),
    ])
    assert seeded["leaf-bends"] == CAN_PLAY_MASTERY

    # Order must not matter — same result with the can_play proposal seen first.
    reversed_order = _initial_leaf_mastery([
        _song("can_play", ["leaf-bends"], title="Known"),
        _song("working_on", ["leaf-bends"], title="Working"),
    ])
    assert reversed_order["leaf-bends"] == CAN_PLAY_MASTERY


def test_aspirational_seeds_nothing():
    """Wanting to play a song is evidence about taste, not about hands.

    An aspirational-only leaf must stay absent from the map so it keeps the 0.0
    server_default — the honest value for "never observed".
    """
    seeded = _initial_leaf_mastery([_song("aspirational", ["leaf-sweep-picking"])])
    assert "leaf-sweep-picking" not in seeded, (
        f"aspirational must not seed mastery, got {seeded}"
    )


def test_unreferenced_leaves_are_not_seeded():
    """Only leaves a song actually maps to get a starting value."""
    seeded = _initial_leaf_mastery([_song("can_play", ["leaf-a"])])
    assert set(seeded) == {"leaf-a"}


def test_seed_values_are_below_mastery_ceiling():
    """Seeded values are a prior, not a claim of mastery — keep them mid-scale.

    If someone later raises can_play toward 1.0, an onboarding answer would outrank
    every real session rating and the skill graph would stop meaning anything.
    """
    for category, value in _ONBOARDING_SEED_MASTERY.items():
        assert PLAYER_LEVEL_FLOOR <= value <= Decimal("0.6"), (
            f"{category} seeds mastery {value}, outside the sane prior range "
            f"[{PLAYER_LEVEL_FLOOR}, 0.6]"
        )


# ---------------------------------------------------------------------------
# 2. End-to-end — the leaves actually land non-zero in Postgres
# ---------------------------------------------------------------------------


def _intermediate_player_output_dict() -> dict[str, Any]:
    """A self-described intermediate player: two can_play songs, one working_on.

    This is the user the bug hurt most — someone who told us at signup that they can
    play Sweet Child o' Mine, and got handed four open chords every day anyway.
    """
    skill_graph: list[dict[str, Any]] = [
        {"temp_id": f"root-{i}", "name": root, "level": "root", "parent_temp_id": None}
        for i, root in enumerate(FIXED_ROOTS)
    ]
    skill_graph.append({
        "temp_id": "sub-lead",
        "name": "Rock Lead",
        "level": "sub",
        "parent_temp_id": "root-1",
    })
    for temp_id, name in (
        ("leaf-bends", "Whole-Step String Bends"),
        ("leaf-pentatonic", "Pentatonic Box Shapes"),
        ("leaf-sweep", "Sweep Picking"),
    ):
        skill_graph.append({
            "temp_id": temp_id,
            "name": name,
            "level": "leaf",
            "parent_temp_id": "sub-lead",
            "tempo_bin_low": 100,
            "tempo_bin_high": 105,
        })

    songs = [
        {
            "title": "Sweet Child o' Mine", "artist": "Guns N' Roses",
            "category": "can_play", "skill_temp_ids": ["leaf-bends", "leaf-pentatonic"],
            "genre": "Rock", "difficulty": "intermediate", "bpm": 125, "key": "Db",
        },
        {
            "title": "Comfortably Numb", "artist": "Pink Floyd",
            "category": "working_on", "skill_temp_ids": ["leaf-bends"],
            "genre": "Rock", "difficulty": "advanced", "bpm": 63, "key": "Bm",
        },
        {
            "title": "Eruption", "artist": "Van Halen",
            "category": "aspirational", "skill_temp_ids": ["leaf-sweep"],
            "genre": "Rock", "difficulty": "advanced", "bpm": 150, "key": "Am",
        },
    ]
    return {"songs": songs, "skill_graph": skill_graph}


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
    """verdict='yes' for every sub/leaf, so nothing is dropped and the only variable
    under test is the mastery seeding."""
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.input = {"verdict": "yes", "root": "Lead", "reason": "regression fixture"}

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
    return {
        "user_id": user_id,
        "songs": {
            "can_play": ["Sweet Child o' Mine"],
            "working_on": ["Comfortably Numb"],
            "aspirational": ["Eruption"],
        },
        "preferences": {"session_length_min": 30, "retention_format": "streak"},
        "raw_input": {
            "can_play": "sweet child o mine",
            "working_on": "comfortably numb",
            "aspirational": "eruption",
        },
    }


@pytest.mark.asyncio
async def test_bootstrap_seeds_leaf_mastery_from_onboarding_categories():
    """After onboarding, an intermediate player's leaves are non-zero and
    player_level clears the single-song catalog window.

    Before the fix every leaf here would be 0.000 and AVG would be 0.000 — the exact
    production state that collapsed the 64-song catalog to 1 reachable song.
    """
    user_id = str(uuid.uuid4())

    try:
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(_intermediate_player_output_dict()),
        ):
            with patch(
                "app.ai.skill_verifier.get_client",
                return_value=_build_mock_verifier_client(),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))

        assert resp.status_code == 201, f"Bootstrap failed {resp.status_code}: {resp.text}"
        assert resp.json()["mode"] == "full", (
            "Expected mode='full'; 'bootstrap' means the SAVEPOINT rolled back and "
            "this test proves nothing about seeding."
        )

        async with _make_session() as db:
            leaves = (await db.execute(
                text(
                    "SELECT name, mastery FROM skill_nodes "
                    "WHERE user_id = :uid AND level = 'leaf' ORDER BY name"
                ),
                {"uid": user_id},
            )).mappings().all()
            player_level = await db.scalar(
                text(
                    "SELECT AVG(mastery) FROM skill_nodes "
                    "WHERE user_id = :uid AND level = 'leaf'"
                ),
                {"uid": user_id},
            )

        by_name = {r["name"]: r["mastery"] for r in leaves}
        assert set(by_name) == {
            "Whole-Step String Bends", "Pentatonic Box Shapes", "Sweep Picking",
        }, f"Unexpected leaf set: {sorted(by_name)}"

        # Reachable from a can_play song (and also a working_on one) -> the higher value.
        assert by_name["Whole-Step String Bends"] == CAN_PLAY_MASTERY, (
            f"Leaf shared by a can_play and a working_on song must take the can_play "
            f"value {CAN_PLAY_MASTERY}, got {by_name['Whole-Step String Bends']}"
        )
        assert by_name["Pentatonic Box Shapes"] == CAN_PLAY_MASTERY

        # Aspirational-only -> untouched. Eruption is a wish, not a skill claim.
        assert by_name["Sweep Picking"] == Decimal("0.000"), (
            f"Aspirational-only leaf must stay at 0.0, got {by_name['Sweep Picking']}"
        )

        # The measurement that matters: raw AVG, before any selector floor.
        assert player_level > Decimal("0.05"), (
            f"player_level came back {player_level}. Production sat at 0.000 with the "
            "whole catalog collapsed to one song; seeding is what has to move this off "
            "the floor on its own, without leaning on the selector's GREATEST()."
        )
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_seeded_player_level_opens_the_catalog_window():
    """The seeded player_level must reach more than one catalog row.

    Ties the unit-level seeding back to the user-visible symptom: 63 of 64 songs
    unreachable. Counts real song_catalog rows inside the selector's +/-0.15 window
    at the player_level this user's onboarding produces.

    Deliberately uses the RAW AVG, not PLAYER_LEVEL_SQL. Wrapping it in the selector's
    GREATEST() floor makes this pass whether or not seeding works at all — the floor
    alone lifts an unseeded 0.00 user to 0.20, where 13 rows are in range. That would
    be a test of the backstop wearing this test's name. The floor has its own coverage
    in test_alembic_0007.py::test_selector_floors_player_level; here the seeding has
    to carry the window on its own.
    """
    user_id = str(uuid.uuid4())

    try:
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(_intermediate_player_output_dict()),
        ):
            with patch(
                "app.ai.skill_verifier.get_client",
                return_value=_build_mock_verifier_client(),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))
        assert resp.status_code == 201, f"Bootstrap failed: {resp.text}"

        async with _make_session() as db:
            candidates = await db.scalar(
                text(
                    "SELECT COUNT(*) FROM song_catalog sc, "
                    "  (SELECT AVG(mastery) AS lvl "
                    "     FROM skill_nodes "
                    "    WHERE user_id = CAST(:uid AS uuid) AND level = 'leaf') pl "
                    "WHERE ABS(sc.difficulty - pl.lvl) <= 0.15"
                ),
                {"uid": user_id},
            )

        assert candidates >= 3, (
            f"Only {candidates} catalog songs reachable for a freshly-onboarded "
            "intermediate player. At player_level 0.00 this was 1, which is what made "
            "the daily reroll a no-op for every production user."
        )
    finally:
        await _cleanup(user_id)
