"""Regression tests for prod bug 2026-08-17 — orphan-leaf FK violation after verifier drop.

Prod stack trace (from Railway `/tmp/railway-rerun.txt`):

    asyncpg.exceptions.ForeignKeyViolationError: insert or update on table "skill_nodes"
      violates foreign key constraint "fk_skill_nodes_parent"
    DETAIL:  Key (parent_id)=(b564b549-47d8-4fb1-8c34-c5674dc1b4c4)
           is not present in table "skill_nodes".

Fires at COMMIT time (fk_skill_nodes_parent is DEFERRABLE INITIALLY DEFERRED per
alembic migration 0002), so the whole SAVEPOINT rolls back and the endpoint 500s.

Preceding log context confirms the verifier ran and dropped nodes:

    WARNING:app.api.v1.users:Skipping song_skill for dropped proposal
      temp_id 'leaf-open-chord-voicings-100' in song 'Big Love'.

Root cause (see .planning/debug/onboarding-fk-orphan-leaves.md):
  1. _persist_bootstrap builds `temp_to_uuid` for ALL proposals, including ones the
     verifier will later drop.
  2. The skill_nodes insert loop correctly skips proposals marked `_DROPPED_ATTR=True`
     via `continue`.
  3. BUT the loop still inserts the DROPPED sub's LEAF CHILDREN with
     `parent_id = temp_to_uuid.get(prop.parent_temp_id)` — a valid UUID that points
     to a dropped-and-never-inserted parent.
  4. PostgreSQL only checks the FK at commit (deferred) → orphan detection blows up.

Fix (approved: cascade drop): between `_run_verifier_pipeline` and the insert loop,
cascade-mark any proposal whose parent is dropped. Iterate to a fixed point so deeper
future sub→sub trees also cascade correctly.

Test strategy (mirrors test_onboarding_governor_savepoint.py + test_onboarding_dup_song.py):
  - Patch Anthropic clients at `get_client` boundary so the real @governed decorator,
    real _persist_bootstrap, real cascade pass, real DB writes all execute.
  - Route the verifier's verdict per-call by inspecting the tool_use payload — different
    proposals get different verdicts to reproduce the exact orphan-leaf topology.
  - Assert: 201 response, no FK violation, no orphan skill_nodes rows, no orphan
    song_skills rows.

If someone regresses `_persist_bootstrap` and removes the cascade pass, these tests
fail with the exact prod error: `ForeignKeyViolationError on fk_skill_nodes_parent`.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, MagicMock, patch

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


async def _cleanup(user_id: str, extra_rejection_names: list[str] | None = None) -> None:
    """Remove all test data for a user (including governor_calls + rejections)."""
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
        # skill_node_rejections has no user_id FK — clean by proposed_name if given.
        for name in extra_rejection_names or []:
            await db.execute(
                text("DELETE FROM skill_node_rejections WHERE proposed_name = :name"),
                {"name": name},
            )
        await db.commit()


# ---------------------------------------------------------------------------
# Canned Sonnet outputs — each reproduces a specific dropped-sub topology.
# ---------------------------------------------------------------------------


def _base_root_graph() -> list[dict]:
    """The 6 FIXED_ROOTS as tool_use.input skill_graph entries."""
    return [
        {
            "temp_id": f"root-{i}",
            "name": root,
            "level": "root",
            "parent_temp_id": None,
        }
        for i, root in enumerate(FIXED_ROOTS)
    ]


def _one_sub_two_leaves_output_dict(sub_name: str) -> dict[str, Any]:
    """Sonnet returns 1 sub with 2 leaf children under Rhythm (root-0).

    Used by Tests 1, 2, 3 — the verifier verdict for the sub determines whether
    the leaves cascade-drop or persist normally.
    """
    skill_graph = _base_root_graph()
    skill_graph.append({
        "temp_id": "sub-target",
        "name": sub_name,
        "level": "sub",
        "parent_temp_id": "root-0",
    })
    skill_graph.append({
        "temp_id": "leaf-child-a",
        "name": "Child Leaf Alpha",
        "level": "leaf",
        "parent_temp_id": "sub-target",
        "tempo_bin_low": 80,
        "tempo_bin_high": 85,
    })
    skill_graph.append({
        "temp_id": "leaf-child-b",
        "name": "Child Leaf Beta",
        "level": "leaf",
        "parent_temp_id": "sub-target",
        "tempo_bin_low": 90,
        "tempo_bin_high": 95,
    })
    # One song, mapping to both leaves — exercises the song_skills cascade too.
    songs = [{
        "title": "Cascade Test Song",
        "artist": "Fixture Artist",
        "category": "working_on",
        "skill_temp_ids": ["leaf-child-a", "leaf-child-b"],
        "genre": "Rock",
        "difficulty": "intermediate",
        "bpm": 120,
        "key": "Am",
    }]
    return {"songs": songs, "skill_graph": skill_graph}


def _multi_drop_output_dict() -> dict[str, Any]:
    """Two subs under different roots, each with distinct leaf children.

    Used by Test 4 — verifier drops BOTH subs; cascade must catch all 4 leaves.
    """
    skill_graph = _base_root_graph()
    # Sub 1 under Rhythm — will be dropped.
    skill_graph.append({
        "temp_id": "sub-drop-1",
        "name": "Not A Real Guitar Skill One",
        "level": "sub",
        "parent_temp_id": "root-0",  # Rhythm
    })
    skill_graph.append({
        "temp_id": "leaf-1a",
        "name": "Orphan Leaf 1A",
        "level": "leaf",
        "parent_temp_id": "sub-drop-1",
        "tempo_bin_low": 80,
        "tempo_bin_high": 85,
    })
    skill_graph.append({
        "temp_id": "leaf-1b",
        "name": "Orphan Leaf 1B",
        "level": "leaf",
        "parent_temp_id": "sub-drop-1",
        "tempo_bin_low": 85,
        "tempo_bin_high": 90,
    })
    # Sub 2 under Lead — will be dropped.
    skill_graph.append({
        "temp_id": "sub-drop-2",
        "name": "Not A Real Guitar Skill Two",
        "level": "sub",
        "parent_temp_id": "root-1",  # Lead
    })
    skill_graph.append({
        "temp_id": "leaf-2a",
        "name": "Orphan Leaf 2A",
        "level": "leaf",
        "parent_temp_id": "sub-drop-2",
        "tempo_bin_low": 100,
        "tempo_bin_high": 105,
    })
    skill_graph.append({
        "temp_id": "leaf-2b",
        "name": "Orphan Leaf 2B",
        "level": "leaf",
        "parent_temp_id": "sub-drop-2",
        "tempo_bin_low": 105,
        "tempo_bin_high": 110,
    })
    # One song referencing leaves from BOTH dropped subtrees.
    songs = [{
        "title": "Multi Drop Test Song",
        "artist": "Fixture Artist",
        "category": "working_on",
        "skill_temp_ids": ["leaf-1a", "leaf-1b", "leaf-2a", "leaf-2b"],
        "genre": "Rock",
        "difficulty": "intermediate",
        "bpm": 120,
        "key": "Am",
    }]
    return {"songs": songs, "skill_graph": skill_graph}


def _fanout_overflow_output_dict() -> dict[str, Any]:
    """Deferred-overflow scenario: leaf survives verification, its parent sub overflows.

    Used by Test 2 — the cascade pass must extend to deferred_overflow drops (not just
    verdict='no'). The verifier fan-out cap is 10 total proposals per onboarding.

    Fixture layout (proposal insertion order matters — the pipeline processes
    verifier-eligible proposals in list order, so the first 10 non-root proposals
    hit the verifier and any surplus go to deferred_overflow):

      slots 1..9   → 9 filler subs (verified 'yes' by the mock → kept)
      slot  10     → the TARGET LEAF (verified 'yes' by the mock → kept)
      slot  11     → the TARGET SUB (overflows the cap → dropped as deferred_overflow)

    After the pipeline: target leaf is kept, target sub is dropped. Without the
    cascade pass, the leaf's INSERT would carry parent_id = temp_to_uuid[target-sub]
    → orphan FK → commit-time ForeignKeyViolationError. The cascade pass must
    cascade-drop the target leaf.
    """
    skill_graph = _base_root_graph()

    # Slots 1..9: nine filler subs (verified 'yes').
    for i in range(9):
        skill_graph.append({
            "temp_id": f"filler-sub-{i}",
            "name": f"Filler Unique Sub Skill Number {i}",
            "level": "sub",
            "parent_temp_id": "root-0",
        })

    # Slot 10: the target LEAF (points to sub that will be in overflow).
    # Placed BEFORE its parent so it enters the verifier-eligible list first.
    skill_graph.append({
        "temp_id": "target-leaf",
        "name": "Target Leaf Should Cascade Drop",
        "level": "leaf",
        "parent_temp_id": "target-sub",
        "tempo_bin_low": 80,
        "tempo_bin_high": 85,
    })

    # Slot 11: the target SUB — pushed past the cap, marked deferred_overflow.
    skill_graph.append({
        "temp_id": "target-sub",
        "name": "Target Sub Should Overflow And Drop",
        "level": "sub",
        "parent_temp_id": "root-0",
    })

    return {"songs": [], "skill_graph": skill_graph}


# ---------------------------------------------------------------------------
# Mock client builders (identical shape to test_onboarding_governor_savepoint.py)
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


def _build_verifier_client_by_name(verdict_by_name: dict[str, str]) -> MagicMock:
    """Return a verifier mock that dispatches verdicts based on the proposed_name in the payload.

    The onboarding parse dispatches one verifier call per sub/leaf; each call sends
    the proposed_name in the user message. We inspect the outgoing payload, match on
    the proposed_name substring, and return the corresponding verdict.

    verdict_by_name: {proposed_name_substring: verdict} — falls back to 'yes' if no match.
    """
    def _make_resp(verdict: str, reason: str = "test dispatch") -> MagicMock:
        tool_use = MagicMock()
        tool_use.type = "tool_use"
        tool_use.input = {"verdict": verdict, "root": "Rhythm", "reason": reason}

        usage = MagicMock()
        usage.input_tokens = 20
        usage.output_tokens = 10

        resp = MagicMock()
        resp.content = [tool_use]
        resp.usage = usage
        return resp

    async def _create(*args, **kwargs) -> MagicMock:
        # The proposed_name shows up in the messages payload — messages is a kwarg.
        messages = kwargs.get("messages", [])
        # messages is a list of {'role', 'content'} dicts; content may be str or list.
        blob = ""
        for m in messages:
            c = m.get("content", "") if isinstance(m, dict) else ""
            if isinstance(c, str):
                blob += c
            elif isinstance(c, list):
                for chunk in c:
                    if isinstance(chunk, dict):
                        blob += str(chunk.get("text", ""))
        for name_sub, verdict in verdict_by_name.items():
            if name_sub in blob:
                return _make_resp(verdict, reason=f"matched '{name_sub}'")
        return _make_resp("yes", reason="fallback yes")

    count_tokens_result = MagicMock()
    count_tokens_result.input_tokens = 15

    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=_create)
    client.messages.count_tokens = AsyncMock(return_value=count_tokens_result)
    return client


def _bootstrap_body(user_id: str) -> dict:
    return {
        "user_id": user_id,
        "songs": {
            "can_play": ["Cascade Test Song"],
            "working_on": [],
            "aspirational": [],
        },
        "preferences": {"session_length_min": 30, "retention_format": "streak"},
        "raw_input": {
            "can_play": "Cascade Test Song",
            "working_on": "",
            "aspirational": "",
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cascade_drop_sub_verdict_no_drops_leaf_children():
    """Test 1 — verifier drops a sub via verdict='no'; both leaves cascade-drop.

    Before the fix: skill_nodes insert loop skips the sub (correct) but still inserts
    the two leaves with parent_id pointing to the dropped sub. FK violates at commit.

    After the fix: cascade pass marks both leaves as dropped; insert loop skips all 3
    dropped nodes; transaction commits cleanly; response is 201.
    """
    user_id = str(uuid.uuid4())
    sub_name = "Not A Real Guitar Skill Verdict No"

    onboarding_dict = _one_sub_two_leaves_output_dict(sub_name)
    verifier_client = _build_verifier_client_by_name({sub_name: "no"})

    try:
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(onboarding_dict),
        ):
            with patch(
                "app.ai.skill_verifier.get_client",
                return_value=verifier_client,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post(
                        "/api/v1/users", json=_bootstrap_body(user_id)
                    )

        assert resp.status_code == 201, (
            f"Cascade-drop fix must let onboarding return 201 even when a sub with "
            f"leaf children is dropped. Got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["mode"] == "full", (
            f"Expected mode='full' (SAVEPOINT held), got: {data['mode']}. "
            f"'bootstrap' here would indicate the FK violation still rolled back the tx."
        )

        # DB assertions: no skill_nodes for the dropped sub or either leaf.
        async with _make_session() as db:
            skill_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM skill_nodes "
                    "WHERE user_id = :uid AND name IN (:sub, 'Child Leaf Alpha', 'Child Leaf Beta')"
                ),
                {"uid": user_id, "sub": sub_name},
            )).scalar_one()
        assert skill_count == 0, (
            f"Expected 0 skill_nodes for dropped sub + its 2 cascade-dropped leaves, "
            f"got {skill_count}"
        )

        # Only the 6 root nodes should be present for this user.
        async with _make_session() as db:
            total = (await db.execute(
                text("SELECT COUNT(*) FROM skill_nodes WHERE user_id = :uid"),
                {"uid": user_id},
            )).scalar_one()
        assert total == 6, (
            f"Expected exactly 6 skill_nodes (the roots), got {total}"
        )
    finally:
        await _cleanup(user_id, extra_rejection_names=[sub_name])


@pytest.mark.asyncio
async def test_cascade_drop_deferred_overflow_drops_leaf_children():
    """Test 2 — deferred_overflow drops must cascade to leaves that survived the verifier.

    Reproduces the trickier variant of the prod bug: a LEAF gets verified inside the
    fan-out cap (verdict='yes' → kept), but its PARENT SUB overflows the cap and is
    marked deferred_overflow (dropped). Without the cascade pass, the kept leaf would
    insert with `parent_id = temp_to_uuid[target-sub]` — a UUID that never gets a row
    → FK violates at commit.

    Fixture invariant: `_fanout_overflow_output_dict` places the target leaf in slot 10
    (last inside the cap) and the target sub in slot 11 (first overflow slot).
    """
    user_id = str(uuid.uuid4())

    onboarding_dict = _fanout_overflow_output_dict()
    # Verifier answers 'yes' for any name that reaches it (filler subs 0..8 + target leaf).
    verifier_client = _build_verifier_client_by_name({})  # falls back to 'yes'

    try:
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(onboarding_dict),
        ):
            with patch(
                "app.ai.skill_verifier.get_client",
                return_value=verifier_client,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post(
                        "/api/v1/users", json=_bootstrap_body(user_id)
                    )

        assert resp.status_code == 201, (
            f"Deferred-overflow cascade must not FK-violate. "
            f"Got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["mode"] == "full", (
            f"Expected mode='full', got: {data['mode']}. "
            f"Anything else means the SAVEPOINT rolled back — cascade regression."
        )

        # Confirm exactly the fan-out cap was consumed: 10 governor rows for skill_verify.
        async with _make_session() as db:
            verify_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM governor_calls "
                    "WHERE user_id = :uid AND feature = 'skill_verify'"
                ),
                {"uid": user_id},
            )).scalar_one()
        assert verify_count == 10, (
            f"Expected exactly 10 verifier invocations (fan-out cap), got {verify_count}. "
            f"If <10, filler size is wrong; if >10, cap not enforced."
        )

        # The target sub overflowed → must NOT be inserted.
        async with _make_session() as db:
            target_sub_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM skill_nodes "
                    "WHERE user_id = :uid AND name = 'Target Sub Should Overflow And Drop'"
                ),
                {"uid": user_id},
            )).scalar_one()
        assert target_sub_count == 0, (
            f"Target sub was in the overflow slot — must not be inserted, "
            f"got {target_sub_count}."
        )

        # The target leaf reached the verifier (verdict='yes') BUT its parent overflowed.
        # Cascade must drop the leaf so no orphan skill_nodes row exists.
        async with _make_session() as db:
            target_leaf_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM skill_nodes "
                    "WHERE user_id = :uid AND name = 'Target Leaf Should Cascade Drop'"
                ),
                {"uid": user_id},
            )).scalar_one()
        assert target_leaf_count == 0, (
            f"Cascade drop failed: target leaf was inserted ({target_leaf_count} row) "
            f"even though its parent sub was overflow-dropped. Before this fix, this leaf "
            f"insert carried an orphan parent_id → FK violation at commit."
        )

        # Sanity: the 9 filler subs (all verified 'yes') should all be persisted.
        async with _make_session() as db:
            filler_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM skill_nodes "
                    "WHERE user_id = :uid AND name LIKE 'Filler Unique Sub Skill Number %'"
                ),
                {"uid": user_id},
            )).scalar_one()
        assert filler_count == 9, (
            f"Expected 9 filler subs (all verified 'yes'), got {filler_count}"
        )

        # Sanity: 6 roots + 9 filler subs = 15 total nodes.
        async with _make_session() as db:
            total = (await db.execute(
                text("SELECT COUNT(*) FROM skill_nodes WHERE user_id = :uid"),
                {"uid": user_id},
            )).scalar_one()
        assert total == 15, f"Expected 6 roots + 9 filler subs = 15, got {total}"
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_no_cascade_when_sub_kept():
    """Test 3 — verifier keeps the sub (verdict='yes'); cascade does nothing; leaves inserted.

    Guard against over-aggressive cascade: the pass must ONLY drop children of DROPPED
    proposals, not children of kept ones.
    """
    user_id = str(uuid.uuid4())
    sub_name = "Kept Sub With Two Leaves"

    onboarding_dict = _one_sub_two_leaves_output_dict(sub_name)
    verifier_client = _build_verifier_client_by_name({sub_name: "yes"})

    try:
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(onboarding_dict),
        ):
            with patch(
                "app.ai.skill_verifier.get_client",
                return_value=verifier_client,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post(
                        "/api/v1/users", json=_bootstrap_body(user_id)
                    )

        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["mode"] == "full", data["mode"]

        async with _make_session() as db:
            # Sub + both leaves present.
            kept = (await db.execute(
                text(
                    "SELECT name FROM skill_nodes "
                    "WHERE user_id = :uid AND name IN (:sub, 'Child Leaf Alpha', 'Child Leaf Beta') "
                    "ORDER BY name"
                ),
                {"uid": user_id, "sub": sub_name},
            )).scalars().all()

        assert set(kept) == {sub_name, "Child Leaf Alpha", "Child Leaf Beta"}, (
            f"Expected kept sub + both leaves, got {set(kept)}"
        )
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_multi_drop_cascade_catches_all_orphans_across_subtrees():
    """Test 4 — two dropped subs under different roots; all 4 orphaned leaves cascade.

    Exercises the cascade pass on multiple independent dropped-subtrees in the same
    onboarding output. Neither leaf under sub-drop-1 nor under sub-drop-2 must be
    inserted; response must be 201 with no FK violation.
    """
    user_id = str(uuid.uuid4())
    sub1 = "Not A Real Guitar Skill One"
    sub2 = "Not A Real Guitar Skill Two"

    onboarding_dict = _multi_drop_output_dict()
    verifier_client = _build_verifier_client_by_name({
        sub1: "no",
        sub2: "no",
    })

    try:
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(onboarding_dict),
        ):
            with patch(
                "app.ai.skill_verifier.get_client",
                return_value=verifier_client,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post(
                        "/api/v1/users", json=_bootstrap_body(user_id)
                    )

        assert resp.status_code == 201, (
            f"Multi-drop cascade must not FK-violate. Got {resp.status_code}: {resp.text}"
        )
        assert resp.json()["mode"] == "full"

        # None of the dropped subs or their leaves should be present.
        orphan_names = [
            sub1, sub2,
            "Orphan Leaf 1A", "Orphan Leaf 1B",
            "Orphan Leaf 2A", "Orphan Leaf 2B",
        ]
        async with _make_session() as db:
            present = (await db.execute(
                text(
                    "SELECT name FROM skill_nodes "
                    "WHERE user_id = :uid AND name = ANY(:names)"
                ),
                {"uid": user_id, "names": orphan_names},
            )).scalars().all()
        assert list(present) == [], (
            f"Expected NO dropped-sub or orphan-leaf rows, found: {list(present)}"
        )

        # Only the 6 root nodes should exist for this user.
        async with _make_session() as db:
            total = (await db.execute(
                text("SELECT COUNT(*) FROM skill_nodes WHERE user_id = :uid"),
                {"uid": user_id},
            )).scalar_one()
        assert total == 6, f"Expected 6 roots only, got {total}"
    finally:
        await _cleanup(user_id, extra_rejection_names=[sub1, sub2])


@pytest.mark.asyncio
async def test_song_skills_skipped_for_cascade_dropped_leaves():
    """Test 5 — song_skills references to cascade-dropped leaves must be skipped.

    The existing `_DROPPED_ATTR` check in the song_skills junction loop must apply
    automatically to cascade-dropped leaves too — asserting it here so a future
    refactor that changes drop semantics doesn't silently break this contract.

    Setup: song references BOTH leaves under a dropped sub. After the cascade pass
    marks the leaves as dropped, the song_skills loop must skip both.
    Result: song row is inserted (songs are unaffected by cascade), but 0 song_skills.
    """
    user_id = str(uuid.uuid4())
    sub_name = "Dropped Sub With Song Referenced Leaves"

    onboarding_dict = _one_sub_two_leaves_output_dict(sub_name)
    verifier_client = _build_verifier_client_by_name({sub_name: "no"})

    try:
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(onboarding_dict),
        ):
            with patch(
                "app.ai.skill_verifier.get_client",
                return_value=verifier_client,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post(
                        "/api/v1/users", json=_bootstrap_body(user_id)
                    )

        assert resp.status_code == 201, resp.text

        async with _make_session() as db:
            # Song row exists (songs are unaffected by cascade drop).
            song_row = (await db.execute(
                text(
                    "SELECT id FROM songs "
                    "WHERE user_id = :uid AND title = 'Cascade Test Song'"
                ),
                {"uid": user_id},
            )).mappings().one_or_none()
            assert song_row is not None, "Song row must still be inserted"

            # But 0 song_skills for this song — both referenced leaves were cascade-dropped.
            junction_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM song_skills WHERE song_id = :sid"
                ),
                {"sid": song_row["id"]},
            )).scalar_one()

        assert junction_count == 0, (
            f"Expected 0 song_skills for song referencing only cascade-dropped leaves, "
            f"got {junction_count}. The song_skills loop must honor cascade-drop markers "
            f"(via the existing _DROPPED_ATTR check)."
        )
    finally:
        await _cleanup(user_id, extra_rejection_names=[sub_name])
