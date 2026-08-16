"""Regression test for the prod P0 bug shipped in Phase 4 Slice C (2026-08-13):

    sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside
    context manager. Please complete the context manager before emitting further commands.

Root cause: Slice C added @governed to run_onboarding_parse. The @governed decorator's
_insert_governor_call() runs `await db.commit()` on the passed AsyncSession. bootstrap_user
was passing its SAVEPOINT-nested `db` directly to run_onboarding_parse, so the governor's
commit closed the SAVEPOINT prematurely — and the very next db.execute() in the verifier
pipeline blew up.

Why the existing suite missed it: test_users_bootstrap_mocked.py monkey-patches
`app.api.v1.users.run_onboarding_parse` directly, replacing the entire @governed-wrapped
callable with a plain async fn. That bypasses the decorator entirely, so
_insert_governor_call() never runs → the SAVEPOINT is never closed → the test passes
even though the real code path is broken.

These tests instead patch only the Anthropic HTTP client (`app.ai.onboarding.get_client`),
letting the REAL @governed decorator and its DB commits execute against the SAVEPOINT
session. If the fix at users.py regresses (i.e. someone passes the outer `db` back into
run_onboarding_parse), these tests fail with the exact prod error.
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
from app.models.skill_node import SonnetOnboardingOutput, SonnetSkillNodeProposal


# ---------------------------------------------------------------------------
# Test DB helpers (mirror test_onboarding_verifier_pipeline.py)
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
# Mock Anthropic client — returns a canned tool_use response for onboarding.
# Patches app.ai.onboarding.get_client so the REAL @governed decorator (including
# _insert_governor_call → db.commit()) executes against the SAVEPOINT session.
# ---------------------------------------------------------------------------


def _canned_onboarding_output_dict() -> dict[str, Any]:
    """Minimal SonnetOnboardingOutput as a plain dict (what the tool_use.input carries).

    6 roots + 1 sub + 1 leaf, no songs. Sub/leaf trigger the verifier pipeline
    (which is where the prod bug's `db.execute` happened) so the failure mode is
    faithfully reproduced when the fix regresses.
    """
    skill_graph = []
    for i, root in enumerate(FIXED_ROOTS):
        skill_graph.append({
            "temp_id": f"root-{i}",
            "name": root,
            "level": "root",
            "parent_temp_id": None,
        })
    skill_graph.append({
        "temp_id": "sub-0",
        "name": "Regression Test Sub",
        "level": "sub",
        "parent_temp_id": "root-0",
    })
    skill_graph.append({
        "temp_id": "leaf-0",
        "name": "Regression Test Leaf",
        "level": "leaf",
        "parent_temp_id": "sub-0",
        "tempo_bin_low": 80,
        "tempo_bin_high": 85,
    })
    return {"songs": [], "skill_graph": skill_graph}


def _build_mock_onboarding_client() -> MagicMock:
    """Mock the Anthropic client used by run_onboarding_parse.

    Returns a client whose:
      - messages.count_tokens() returns a fake usage estimate (17 tokens)
      - messages.create() returns a tool_use response carrying the canned output
    The @governed decorator's DB writes (INSERT governor_calls + UPDATE actuals)
    are NOT mocked and run for real against the passed session.
    """
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.input = _canned_onboarding_output_dict()

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
    """Mock the Anthropic client used by run_skill_node_verify (also @governed).

    The onboarding output contains a sub + leaf, so the verifier pipeline runs
    for each of them. Verdict='yes' so both are inserted as canonicals without
    any curator-queue side effects.
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
    return {
        "user_id": user_id,
        "songs": {
            "can_play": ["Regression Song"],
            "working_on": [],
            "aspirational": [],
        },
        "preferences": {"session_length_min": 30, "retention_format": "streak"},
        "raw_input": {
            "can_play": "Regression Song",
            "working_on": "",
            "aspirational": "",
        },
    }


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_survives_real_governor_commit_inside_savepoint():
    """POST /api/v1/users returns 201, not 500, when the REAL @governed decorator runs.

    Before the fix: the decorator's _insert_governor_call() commits the caller's
    SAVEPOINT-nested session, so the immediately following db.execute() in the
    verifier pipeline raises "Can't operate on closed transaction". Endpoint 500s.
    """
    user_id = str(uuid.uuid4())

    try:
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(),
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
            f"Bootstrap must return 201 with a live governor round-trip. "
            f"Got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["mode"] == "full", (
            f"Expected mode='full' (full graph persisted), got: {data['mode']}. "
            f"A 'bootstrap' mode here would mean the SAVEPOINT rolled back."
        )
        # 6 roots + 1 sub + 1 leaf = 8 nodes from the canned output.
        assert len(data["nodes"]) == 8, (
            f"Expected 8 skill_nodes (6 roots + 1 sub + 1 leaf), got: {len(data['nodes'])}"
        )
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_bootstrap_records_onboarding_governor_row():
    """After a successful bootstrap, governor_calls has exactly 1 row with feature='onboarding'.

    This proves the governor decorator DID run (it wasn't mocked away). Combined with
    the test above returning 201, it confirms the fix works: governor writes committed,
    SAVEPOINT still usable, verifier + inserts completed cleanly.
    """
    user_id = str(uuid.uuid4())

    try:
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(),
        ):
            with patch(
                "app.ai.skill_verifier.get_client",
                return_value=_build_mock_verifier_client(),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))

        assert resp.status_code == 201, resp.text

        async with _make_session() as db:
            onboarding_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM governor_calls "
                    "WHERE user_id = :uid AND feature = 'onboarding'"
                ),
                {"uid": user_id},
            )).scalar_one()

            # The governor row must exist AND have actuals populated (proves record_actuals
            # ran successfully after the wrapped call returned).
            row = (await db.execute(
                text(
                    "SELECT prompt_tokens_estimated, prompt_tokens_actual, "
                    "output_tokens_actual, error_code FROM governor_calls "
                    "WHERE user_id = :uid AND feature = 'onboarding'"
                ),
                {"uid": user_id},
            )).mappings().one_or_none()

        assert onboarding_count == 1, (
            f"Expected exactly 1 governor_calls row with feature='onboarding' "
            f"(proves @governed ran for real, not mocked away), got: {onboarding_count}"
        )
        assert row is not None
        assert row["error_code"] is None, (
            f"Governor row should have NO error_code on successful bootstrap; "
            f"got error_code={row['error_code']!r}"
        )
        assert row["prompt_tokens_actual"] == 42, (
            f"Expected prompt_tokens_actual=42 from mock usage, got: {row['prompt_tokens_actual']}"
        )
        assert row["output_tokens_actual"] == 21, (
            f"Expected output_tokens_actual=21 from mock usage, got: {row['output_tokens_actual']}"
        )
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_rerun_survives_real_governor_commit_inside_savepoint():
    """POST /api/v1/users/{id}/re-run must not regress the same governor-vs-SAVEPOINT bug.

    re_run_onboarding has an independent call site to run_onboarding_parse inside its own
    SAVEPOINT — this test guards it against the same class of regression.
    """
    user_id = str(uuid.uuid4())

    try:
        # First bootstrap so the user + preferences exist for re-run.
        with patch(
            "app.ai.onboarding.get_client",
            return_value=_build_mock_onboarding_client(),
        ):
            with patch(
                "app.ai.skill_verifier.get_client",
                return_value=_build_mock_verifier_client(),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))
                    assert resp.status_code == 201, resp.text

                    # Now the re-run — same governor path, different endpoint + SAVEPOINT.
                    rerun_resp = await client.post(
                        f"/api/v1/users/{user_id}/re-run", json=_bootstrap_body(user_id)
                    )

        assert rerun_resp.status_code == 200, (
            f"Re-run must return 200 with a live governor round-trip. "
            f"Got {rerun_resp.status_code}: {rerun_resp.text}"
        )
        data = rerun_resp.json()
        assert data["mode"] == "full", (
            f"Expected mode='full' on re-run, got: {data['mode']}. "
            f"A 'bootstrap' mode here would mean the SAVEPOINT rolled back."
        )

        # Two 'onboarding' governor rows now: one from bootstrap, one from re-run.
        async with _make_session() as db:
            onboarding_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM governor_calls "
                    "WHERE user_id = :uid AND feature = 'onboarding'"
                ),
                {"uid": user_id},
            )).scalar_one()
        assert onboarding_count == 2, (
            f"Expected 2 governor_calls rows (bootstrap + re-run), got: {onboarding_count}"
        )
    finally:
        await _cleanup(user_id)
