"""Integration tests for the Phase 4 verifier pipeline in _run_verifier_pipeline / _persist_bootstrap.

Tests the dedup → verifier → DB routing logic against a real Postgres test DB.
All Sonnet verifier calls are mocked — no real API calls.

Test coverage:
  - test_high_score_reuses_canonical
  - test_mid_score_queues_and_inserts_local
  - test_low_score_calls_verifier (verdict='yes')
  - test_verifier_no_verdict_drops_and_records_rejection
  - test_verifier_uncertain_queues_and_inserts_local
  - test_root_nodes_bypass_pipeline
  - test_verifier_call_failure_queues_gracefully
  - test_pipeline_governor_row_per_verifier_invocation
  - test_pipeline_caps_verifier_fanout_at_ten
  - test_pipeline_semaphore_bounds_concurrent_verifier_calls
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.ai.onboarding import AIParseError, FIXED_ROOTS
from app.ai.skill_verifier import AISkillVerifierError, SkillNodeVerifyOutput
from app.models.db import Base, SkillNode, Song, SongSkill, User
from app.models.skill_node import SonnetOnboardingOutput, SonnetSkillNodeProposal, SonnetSongProposal

# Ensure no live API key leaks into this test suite
os.environ.pop("ANTHROPIC_API_KEY", None)


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
    engine = create_async_engine(
        _make_test_db_url(), echo=False, pool_size=1, max_overflow=0
    )
    return async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )()


async def _seed_user(user_id: str) -> None:
    """Create a user row for testing."""
    async with _make_session() as db:
        await db.execute(
            text(
                "INSERT INTO users (id, preferences) "
                "VALUES (:uid, '{}'::jsonb) ON CONFLICT DO NOTHING"
            ),
            {"uid": user_id},
        )
        await db.commit()


async def _seed_canonical_node(
    name: str,
    level: str = "sub",
) -> tuple[str, str]:
    """Insert a canonical skill_node row (canonical_node_id = self.id).

    Uses a separate 'canonical owner' user so the idempotency guard on the
    real test user_id is not triggered. Returns (canonical_user_id, node_id).
    """
    node_id = str(uuid.uuid4())
    canonical_user_id = str(uuid.uuid4())
    async with _make_session() as db:
        # Seed the canonical owner user
        await db.execute(
            text(
                "INSERT INTO users (id, preferences) "
                "VALUES (:uid, '{}'::jsonb) ON CONFLICT DO NOTHING"
            ),
            {"uid": canonical_user_id},
        )
        await db.execute(
            text(
                "INSERT INTO skill_nodes (id, user_id, name, level, canonical_node_id, mastery) "
                "VALUES (:id, :uid, :name, :level, :canonical_id, 0.0)"
            ),
            {
                "id": node_id,
                "uid": canonical_user_id,
                "name": name,
                "level": level,
                "canonical_id": node_id,  # self-referential = canonical
            },
        )
        await db.commit()
    return canonical_user_id, node_id


async def _cleanup_canonical(canonical_user_id: str, node_id: str) -> None:
    """Remove a canonical node and its owner user."""
    async with _make_session() as db:
        await db.execute(text("DELETE FROM skill_nodes WHERE id = :nid"), {"nid": node_id})
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": canonical_user_id})
        await db.commit()


async def _cleanup(user_id: str) -> None:
    """Remove all test data for a user."""
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
# Canned mock data builders
# ---------------------------------------------------------------------------

def _canned_output_with_proposals(proposals: list[dict]) -> SonnetOnboardingOutput:
    """Build a SonnetOnboardingOutput with root + the given sub/leaf proposals."""
    skill_graph: list[SonnetSkillNodeProposal] = []
    for i, root in enumerate(FIXED_ROOTS):
        skill_graph.append(SonnetSkillNodeProposal(
            temp_id=f"root-{i}",
            name=root,
            level="root",
            parent_temp_id=None,
        ))
    for j, p in enumerate(proposals):
        skill_graph.append(SonnetSkillNodeProposal(
            temp_id=f"prop-{j}",
            name=p["name"],
            level=p.get("level", "sub"),
            parent_temp_id="root-0",  # all proposals under Rhythm root for simplicity
            tempo_bin_low=p.get("tempo_bin_low"),
            tempo_bin_high=p.get("tempo_bin_high"),
        ))
    return SonnetOnboardingOutput(
        songs=[],
        skill_graph=skill_graph,
    )


def _build_mock_verifier_client(verdict: str, root: str | None = "Rhythm", reason: str = "test reason") -> MagicMock:
    """Build a mock get_client() that returns a controlled verifier verdict."""
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.input = {"verdict": verdict, "root": root, "reason": reason}

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
            "can_play": ["Blues Shuffle"],
            "working_on": [],
            "aspirational": [],
        },
        "preferences": {"session_length_min": 30, "retention_format": "streak"},
        "raw_input": {
            "can_play": "Blues Shuffle",
            "working_on": "",
            "aspirational": "",
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_high_score_reuses_canonical():
    """Score >= 85: user-scoped skill_nodes row gets canonical_node_id = existing canonical.id."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    # Seed a canonical "Blues Shuffle" — "Blues Shuffle Rhythm" should score >= 85 against it.
    # Use a separate canonical owner user to avoid triggering the idempotency guard.
    canonical_owner_id, canonical_id = await _seed_canonical_node("Blues Shuffle", level="sub")

    # Mock onboarding to return a proposal that dedupes to the canonical
    onboarding_output = _canned_output_with_proposals([
        {"name": "Blues Shuffle Rhythm", "level": "sub"},
    ])

    try:
        with patch("app.api.v1.users.run_onboarding_parse", AsyncMock(return_value=onboarding_output)):
            with patch("app.ai.skill_verifier.get_client", return_value=_build_mock_verifier_client("yes")):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))
        assert resp.status_code == 201, resp.text

        # Check DB: the inserted "Blues Shuffle Rhythm" node has canonical_node_id = existing canonical
        async with _make_session() as db:
            row = (await db.execute(
                text(
                    "SELECT canonical_node_id FROM skill_nodes "
                    "WHERE user_id = :uid AND name = 'Blues Shuffle Rhythm'"
                ),
                {"uid": user_id},
            )).mappings().one_or_none()
        assert row is not None, "Blues Shuffle Rhythm node not inserted"
        assert str(row["canonical_node_id"]) == canonical_id, (
            f"Expected canonical_node_id={canonical_id!r}, got {row['canonical_node_id']!r}"
        )
    finally:
        await _cleanup(user_id)
        await _cleanup_canonical(canonical_owner_id, canonical_id)


@pytest.mark.asyncio
async def test_mid_score_queues_and_inserts_local():
    """Score in [70, 85): skill_node_proposals row created AND user-scoped skill_nodes row inserted."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    # "Classical Fingerpicking" canonical; "Fingerstyle Fingerpicking" should score in [70,85).
    # Use a separate canonical owner user to avoid triggering the idempotency guard.
    canonical_owner_id, canonical_id = await _seed_canonical_node("Classical Fingerpicking", level="sub")

    onboarding_output = _canned_output_with_proposals([
        {"name": "Fingerstyle Fingerpicking", "level": "sub"},
    ])

    try:
        with patch("app.api.v1.users.run_onboarding_parse", AsyncMock(return_value=onboarding_output)):
            with patch("app.ai.skill_verifier.get_client", return_value=_build_mock_verifier_client("yes")):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))
        assert resp.status_code == 201, resp.text

        async with _make_session() as db:
            # User-scoped skill_nodes row exists
            skill_row = (await db.execute(
                text(
                    "SELECT id, canonical_node_id FROM skill_nodes "
                    "WHERE user_id = :uid AND name = 'Fingerstyle Fingerpicking'"
                ),
                {"uid": user_id},
            )).mappings().one_or_none()
            assert skill_row is not None, "Expected user-scoped skill_nodes row for mid-score"
            assert skill_row["canonical_node_id"] is None, (
                "Mid-score proposals should have canonical_node_id=NULL (user-scoped)"
            )

            # skill_node_proposals row exists
            proposal_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM skill_node_proposals "
                    "WHERE user_id = :uid AND proposed_name = 'Fingerstyle Fingerpicking'"
                ),
                {"uid": user_id},
            )).scalar_one()
            assert proposal_count == 1, (
                f"Expected 1 skill_node_proposals row for mid-score, got {proposal_count}"
            )
    finally:
        await _cleanup(user_id)
        await _cleanup_canonical(canonical_owner_id, canonical_id)


@pytest.mark.asyncio
async def test_low_score_calls_verifier():
    """Score < 70 or no candidates: verifier called; verdict='yes' inserts with self-canonical."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    # No canonical nodes — fresh install; all proposals fall through to verifier
    onboarding_output = _canned_output_with_proposals([
        {"name": "Sweep Picking", "level": "sub"},
    ])

    mock_client = _build_mock_verifier_client("yes", root="Lead", reason="distinct lead technique")

    try:
        with patch("app.api.v1.users.run_onboarding_parse", AsyncMock(return_value=onboarding_output)):
            with patch("app.ai.skill_verifier.get_client", return_value=mock_client):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))
        assert resp.status_code == 201, resp.text

        async with _make_session() as db:
            row = (await db.execute(
                text(
                    "SELECT id, canonical_node_id FROM skill_nodes "
                    "WHERE user_id = :uid AND name = 'Sweep Picking'"
                ),
                {"uid": user_id},
            )).mappings().one_or_none()
        assert row is not None, "Expected Sweep Picking node inserted"
        # Verdict='yes': canonical_node_id = self.id (self-canonical)
        assert row["canonical_node_id"] == row["id"], (
            f"verdict='yes' should set canonical_node_id = self.id, "
            f"got canonical={row['canonical_node_id']!r}, id={row['id']!r}"
        )
        # Verifier was called (mock.messages.create called at least once)
        assert mock_client.messages.create.call_count >= 1
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_verifier_no_verdict_drops_and_records_rejection():
    """verdict='no': skill_nodes NOT inserted; skill_node_rejections row created."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    onboarding_output = _canned_output_with_proposals([
        {"name": "Random Topic Not Guitar", "level": "sub"},
    ])

    mock_client = _build_mock_verifier_client("no", root=None, reason="not a guitar skill")

    try:
        with patch("app.api.v1.users.run_onboarding_parse", AsyncMock(return_value=onboarding_output)):
            with patch("app.ai.skill_verifier.get_client", return_value=mock_client):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))
        assert resp.status_code == 201, resp.text

        async with _make_session() as db:
            # No skill_nodes row for the rejected proposal
            skill_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM skill_nodes "
                    "WHERE user_id = :uid AND name = 'Random Topic Not Guitar'"
                ),
                {"uid": user_id},
            )).scalar_one()
            assert skill_count == 0, (
                f"Expected 0 skill_nodes for verdict='no', got {skill_count}"
            )

            # skill_node_rejections row exists
            rejection_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM skill_node_rejections "
                    "WHERE proposed_name = 'Random Topic Not Guitar'"
                ),
            )).scalar_one()
            assert rejection_count == 1, (
                f"Expected 1 skill_node_rejections row, got {rejection_count}"
            )
    finally:
        await _cleanup(user_id)
        # Also clean up any rejections (no user_id FK on rejections table)
        async with _make_session() as db:
            await db.execute(
                text("DELETE FROM skill_node_rejections WHERE proposed_name = 'Random Topic Not Guitar'")
            )
            await db.commit()


@pytest.mark.asyncio
async def test_verifier_uncertain_queues_and_inserts_local():
    """verdict='uncertain': skill_nodes row inserted (canonical_node_id=NULL) + proposal row."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    onboarding_output = _canned_output_with_proposals([
        {"name": "Melodic Fingerpicking Runs", "level": "sub"},
    ])

    mock_client = _build_mock_verifier_client("uncertain", root="Lead", reason="could fit multiple roots")

    try:
        with patch("app.api.v1.users.run_onboarding_parse", AsyncMock(return_value=onboarding_output)):
            with patch("app.ai.skill_verifier.get_client", return_value=mock_client):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))
        assert resp.status_code == 201, resp.text

        async with _make_session() as db:
            # User-scoped skill_nodes row exists with canonical_node_id=NULL
            skill_row = (await db.execute(
                text(
                    "SELECT canonical_node_id FROM skill_nodes "
                    "WHERE user_id = :uid AND name = 'Melodic Fingerpicking Runs'"
                ),
                {"uid": user_id},
            )).mappings().one_or_none()
            assert skill_row is not None, "Expected skill_nodes row for uncertain verdict"
            assert skill_row["canonical_node_id"] is None, (
                "uncertain verdict should have canonical_node_id=NULL"
            )

            # skill_node_proposals row exists
            proposal_row = (await db.execute(
                text(
                    "SELECT verifier_verdict, status FROM skill_node_proposals "
                    "WHERE user_id = :uid AND proposed_name = 'Melodic Fingerpicking Runs'"
                ),
                {"uid": user_id},
            )).mappings().one_or_none()
            assert proposal_row is not None, "Expected skill_node_proposals row for uncertain"
            assert proposal_row["verifier_verdict"] == "uncertain"
            assert proposal_row["status"] == "pending"
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_root_nodes_bypass_pipeline():
    """Root nodes (level='root') always insert; verifier NOT called for roots."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    # Output with only roots (no sub/leaf)
    onboarding_output = SonnetOnboardingOutput(
        songs=[],
        skill_graph=[
            SonnetSkillNodeProposal(
                temp_id=f"root-{i}",
                name=root,
                level="root",
                parent_temp_id=None,
            )
            for i, root in enumerate(FIXED_ROOTS)
        ],
    )

    mock_client = _build_mock_verifier_client("no", root=None, reason="should not be called")

    try:
        with patch("app.api.v1.users.run_onboarding_parse", AsyncMock(return_value=onboarding_output)):
            with patch("app.ai.skill_verifier.get_client", return_value=mock_client):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))
        assert resp.status_code == 201, resp.text

        # All 6 roots should be inserted
        async with _make_session() as db:
            root_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM skill_nodes "
                    "WHERE user_id = :uid AND level = 'root'"
                ),
                {"uid": user_id},
            )).scalar_one()
        assert root_count == 6, f"Expected 6 root nodes, got {root_count}"

        # Verifier should NOT have been called for roots
        assert mock_client.messages.create.call_count == 0, (
            f"Verifier mock was called {mock_client.messages.create.call_count} times — "
            "expected 0 (roots bypass pipeline)"
        )
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_verifier_call_failure_queues_gracefully():
    """AISkillVerifierError from verifier → proposal queued as 'uncertain' + 201 response."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    onboarding_output = _canned_output_with_proposals([
        {"name": "Some New Technique", "level": "sub"},
    ])

    try:
        with patch("app.api.v1.users.run_onboarding_parse", AsyncMock(return_value=onboarding_output)):
            with patch(
                "app.api.v1.users.run_skill_node_verify",
                AsyncMock(side_effect=AISkillVerifierError("simulated verifier failure")),
            ):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))

        # D-14: onboarding still succeeds despite verifier failure
        assert resp.status_code == 201, f"Expected 201 (fail-graceful), got {resp.status_code}: {resp.text}"

        # Proposal queued as uncertain
        async with _make_session() as db:
            proposal = (await db.execute(
                text(
                    "SELECT verifier_verdict, verifier_reason FROM skill_node_proposals "
                    "WHERE user_id = :uid AND proposed_name = 'Some New Technique'"
                ),
                {"uid": user_id},
            )).mappings().one_or_none()
        assert proposal is not None, "Expected skill_node_proposals row after verifier failure"
        assert proposal["verifier_verdict"] == "uncertain", (
            f"Expected verifier_verdict='uncertain', got {proposal['verifier_verdict']!r}"
        )
        assert "verifier call failed" in (proposal["verifier_reason"] or ""), (
            f"Expected 'verifier call failed' in reason, got {proposal['verifier_reason']!r}"
        )
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_pipeline_governor_row_per_verifier_invocation():
    """3 low-score proposals → 3 governor_calls rows with feature='skill_verify'."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    # 3 unique low-score proposals (no canonicals to dedupe against)
    onboarding_output = _canned_output_with_proposals([
        {"name": "Alpha Technique X", "level": "sub"},
        {"name": "Beta Technique Y", "level": "sub"},
        {"name": "Gamma Technique Z", "level": "sub"},
    ])

    mock_client = _build_mock_verifier_client("yes", root="Rhythm", reason="valid")

    try:
        with patch("app.api.v1.users.run_onboarding_parse", AsyncMock(return_value=onboarding_output)):
            with patch("app.ai.skill_verifier.get_client", return_value=mock_client):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))
        assert resp.status_code == 201, resp.text

        async with _make_session() as db:
            skill_verify_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM governor_calls "
                    "WHERE user_id = :uid AND feature = 'skill_verify'"
                ),
                {"uid": user_id},
            )).scalar_one()
        assert skill_verify_count == 3, (
            f"Expected 3 governor_calls rows for skill_verify, got {skill_verify_count}"
        )
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_pipeline_caps_verifier_fanout_at_ten():
    """15 low-score proposals → exactly 10 governor_calls for skill_verify + 5 deferred_overflow."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    # 15 unique proposals — all score < 70 (no canonicals)
    proposals = [{"name": f"Unique Technique {i}", "level": "sub"} for i in range(15)]
    onboarding_output = _canned_output_with_proposals(proposals)

    mock_client = _build_mock_verifier_client("yes", root="Rhythm", reason="valid")

    try:
        with patch("app.api.v1.users.run_onboarding_parse", AsyncMock(return_value=onboarding_output)):
            with patch("app.ai.skill_verifier.get_client", return_value=mock_client):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))
        assert resp.status_code == 201, resp.text

        async with _make_session() as db:
            skill_verify_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM governor_calls "
                    "WHERE user_id = :uid AND feature = 'skill_verify'"
                ),
                {"uid": user_id},
            )).scalar_one()

            overflow_count = (await db.execute(
                text(
                    "SELECT COUNT(*) FROM skill_node_proposals "
                    "WHERE user_id = :uid AND verifier_verdict = 'deferred_overflow'"
                ),
                {"uid": user_id},
            )).scalar_one()

        assert skill_verify_count == 10, (
            f"Expected exactly 10 governor_calls for skill_verify (fan-out cap), got {skill_verify_count}"
        )
        assert overflow_count == 5, (
            f"Expected 5 deferred_overflow proposals, got {overflow_count}"
        )
    finally:
        await _cleanup(user_id)


@pytest.mark.asyncio
async def test_pipeline_semaphore_bounds_concurrent_verifier_calls():
    """Peak in-flight verifier calls during 10-call fan-out is <= 5 (Semaphore(5))."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)

    # 10 unique proposals — all score < 70 (no canonicals)
    proposals = [{"name": f"Peak Test Technique {i}", "level": "sub"} for i in range(10)]
    onboarding_output = _canned_output_with_proposals(proposals)

    peak_concurrent: list[int] = [0]
    current_concurrent: list[int] = [0]

    async def _mock_verifier(*args, **kwargs) -> SkillNodeVerifyOutput:
        current_concurrent[0] += 1
        peak_concurrent[0] = max(peak_concurrent[0], current_concurrent[0])
        # Small yield to allow other coroutines to enter
        await asyncio.sleep(0)
        current_concurrent[0] -= 1
        return SkillNodeVerifyOutput(verdict="yes", root="Rhythm", reason="valid")

    try:
        with patch("app.api.v1.users.run_onboarding_parse", AsyncMock(return_value=onboarding_output)):
            with patch("app.api.v1.users.run_skill_node_verify", side_effect=_mock_verifier):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/users", json=_bootstrap_body(user_id))
        assert resp.status_code == 201, resp.text

        assert peak_concurrent[0] <= 5, (
            f"Peak concurrent verifier calls was {peak_concurrent[0]} — expected <= 5 (Semaphore(5))"
        )
    finally:
        await _cleanup(user_id)
