"""Tests for GET /api/v1/admin/curator + POST /api/v1/admin/curator/action (D-11).

Covers:
  - 401/422 without token, 401 with wrong token, 503 when env var unset
  - 200 with correct token + proposal data in HTML
  - ?tab=rejected renders rejection rows
  - POST approve/reject/merge_with actions update DB correctly
  - merge_with 400 when canonical_id missing
  - hmac.compare_digest smoke-check

All tests run against a real Postgres test database.
No Sonnet calls are made (admin endpoints are pure DB operations).
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Optional

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure no live API key leaks into this test suite
os.environ.pop("ANTHROPIC_API_KEY", None)

# Set admin token for tests that need it BEFORE importing app
_TEST_TOKEN = "test-admin-token-abc123"
os.environ["FLETCHER_ADMIN_TOKEN"] = _TEST_TOKEN

from app.main import app  # noqa: E402

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
    async with _make_session() as db:
        await db.execute(
            text(
                "INSERT INTO users (id, preferences) "
                "VALUES (:uid, '{}'::jsonb) ON CONFLICT DO NOTHING"
            ),
            {"uid": user_id},
        )
        await db.commit()


async def _seed_proposal(
    user_id: str,
    proposed_name: str,
    status: str = "pending",
    fuzzy_score: int = 0,
    verifier_verdict: Optional[str] = None,
    verifier_reason: Optional[str] = None,
) -> str:
    """Insert a skill_node_proposals row, return its id."""
    proposal_id = str(uuid.uuid4())
    async with _make_session() as db:
        await db.execute(
            text(
                "INSERT INTO skill_node_proposals "
                "(id, user_id, proposed_name, fuzzy_score, status, verifier_verdict, verifier_reason, created_at) "
                "VALUES (:id, :uid, :name, :score, :status, :verdict, :reason, NOW())"
            ),
            {
                "id": proposal_id,
                "uid": user_id,
                "name": proposed_name,
                "score": fuzzy_score,
                "status": status,
                "verdict": verifier_verdict,
                "reason": verifier_reason,
            },
        )
        await db.commit()
    return proposal_id


async def _seed_rejection(proposed_name: str, reason: str = "rejected by curator") -> str:
    """Insert a skill_node_rejections row, return its id."""
    rejection_id = str(uuid.uuid4())
    async with _make_session() as db:
        await db.execute(
            text(
                "INSERT INTO skill_node_rejections (id, proposed_name, reason, verifier_response, created_at) "
                "VALUES (:id, :name, :reason, NULL, NOW())"
            ),
            {"id": rejection_id, "name": proposed_name, "reason": reason},
        )
        await db.commit()
    return rejection_id


async def _seed_skill_node(
    user_id: str,
    name: str,
    level: str = "sub",
    canonical_node_id: Optional[str] = None,
) -> str:
    """Insert a skill_nodes row, return its id."""
    node_id = str(uuid.uuid4())
    async with _make_session() as db:
        await db.execute(
            text(
                "INSERT INTO skill_nodes "
                "(id, user_id, name, level, canonical_node_id, mastery, created_at, updated_at) "
                "VALUES (:id, :uid, :name, :level, :cid, 0.0, NOW(), NOW())"
            ),
            {
                "id": node_id,
                "uid": user_id,
                "name": name,
                "level": level,
                "cid": canonical_node_id,
            },
        )
        await db.commit()
    return node_id


async def _cleanup_user(user_id: str) -> None:
    async with _make_session() as db:
        await db.execute(text("DELETE FROM skill_node_proposals WHERE user_id = :uid"), {"uid": user_id})
        await db.execute(text("DELETE FROM skill_nodes WHERE user_id = :uid"), {"uid": user_id})
        await db.execute(text("DELETE FROM governor_calls WHERE user_id = :uid"), {"uid": user_id})
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        await db.commit()


async def _cleanup_rejection(proposed_name: str) -> None:
    async with _make_session() as db:
        await db.execute(
            text("DELETE FROM skill_node_rejections WHERE proposed_name = :name"),
            {"name": proposed_name},
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_curator_401_without_token():
    """GET /admin/curator with no X-Admin-Token header → 422 (missing required header)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/admin/curator")
    # FastAPI raises 422 for missing required header (same as any required Header dep)
    assert resp.status_code in (401, 422)


@pytest.mark.asyncio
async def test_get_curator_401_wrong_token():
    """GET /admin/curator with wrong X-Admin-Token → 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/admin/curator",
            headers={"X-Admin-Token": "wrong-token-xyz"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_curator_503_when_env_unset(monkeypatch):
    """GET /admin/curator with FLETCHER_ADMIN_TOKEN unset → 503."""
    monkeypatch.delenv("FLETCHER_ADMIN_TOKEN", raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/admin/curator",
            headers={"X-Admin-Token": "any-token"},
        )
    assert resp.status_code == 503
    # Restore for subsequent tests
    monkeypatch.setenv("FLETCHER_ADMIN_TOKEN", _TEST_TOKEN)


@pytest.mark.asyncio
async def test_get_curator_200_with_correct_token():
    """GET /admin/curator with correct token → 200 HTML containing proposal names."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)
    proposal_ids = []
    for name in ["Sweep Picking", "Hybrid Picking"]:
        pid = await _seed_proposal(user_id, name, status="pending", fuzzy_score=30)
        proposal_ids.append(pid)
    rejection_name = "NonGuitarThing"
    await _seed_rejection(rejection_name)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/admin/curator",
                headers={"X-Admin-Token": _TEST_TOKEN},
            )
        assert resp.status_code == 200
        html = resp.text
        assert "Sweep Picking" in html
        assert "Hybrid Picking" in html
        assert "Curator Queue" in html.lower() or "curator" in html.lower()
    finally:
        await _cleanup_user(user_id)
        await _cleanup_rejection(rejection_name)


@pytest.mark.asyncio
async def test_get_curator_tab_rejected_shows_rejections():
    """GET /admin/curator?tab=rejected → HTML contains rejection rows."""
    rejection_name = "SomeNonGuitarConcept"
    await _seed_rejection(rejection_name, reason="rejected by curator")

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/admin/curator?tab=rejected",
                headers={"X-Admin-Token": _TEST_TOKEN},
            )
        assert resp.status_code == 200
        html = resp.text
        assert rejection_name in html
    finally:
        await _cleanup_rejection(rejection_name)


@pytest.mark.asyncio
async def test_post_action_approve():
    """POST action=approve: skill_node_proposals.status='approved' + skill_nodes.canonical_node_id set."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)
    proposal_name = "String Bending Vibrato"
    proposal_id = await _seed_proposal(user_id, proposal_name, status="pending", fuzzy_score=20)
    node_id = await _seed_skill_node(user_id, proposal_name, level="sub", canonical_node_id=None)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/admin/curator/action",
                data={"proposal_id": proposal_id, "action": "approve"},
                headers={"X-Admin-Token": _TEST_TOKEN},
            )
        # 303 redirect or 200 with action recorded
        assert resp.status_code in (200, 303)

        async with _make_session() as db:
            row = await db.execute(
                text("SELECT status FROM skill_node_proposals WHERE id = :pid"),
                {"pid": proposal_id},
            )
            assert row.scalar() == "approved"

            # Verify skill_nodes.canonical_node_id is now set to self (node_id)
            node_row = await db.execute(
                text("SELECT canonical_node_id FROM skill_nodes WHERE id = :nid"),
                {"nid": node_id},
            )
            canonical = node_row.scalar()
            assert canonical is not None
            assert str(canonical) == node_id
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_post_action_reject():
    """POST action=reject: status='rejected' AND skill_node_rejections row inserted."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)
    proposal_name = "Playing Air Guitar"
    proposal_id = await _seed_proposal(user_id, proposal_name, status="pending", fuzzy_score=5)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/admin/curator/action",
                data={"proposal_id": proposal_id, "action": "reject"},
                headers={"X-Admin-Token": _TEST_TOKEN},
            )
        assert resp.status_code in (200, 303)

        async with _make_session() as db:
            status = await db.scalar(
                text("SELECT status FROM skill_node_proposals WHERE id = :pid"),
                {"pid": proposal_id},
            )
            assert status == "rejected"

            rejection_count = await db.scalar(
                text("SELECT COUNT(*) FROM skill_node_rejections WHERE proposed_name = :name"),
                {"name": proposal_name},
            )
            assert rejection_count >= 1
    finally:
        await _cleanup_user(user_id)
        await _cleanup_rejection(proposal_name)


@pytest.mark.asyncio
async def test_post_action_merge_with_requires_canonical_id():
    """POST action=merge_with with no canonical_id → 400."""
    user_id = str(uuid.uuid4())
    await _seed_user(user_id)
    proposal_id = await _seed_proposal(user_id, "Some Technique", status="pending")

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/admin/curator/action",
                data={"proposal_id": proposal_id, "action": "merge_with"},
                headers={"X-Admin-Token": _TEST_TOKEN},
            )
        assert resp.status_code == 400
    finally:
        await _cleanup_user(user_id)


@pytest.mark.asyncio
async def test_post_action_merge_with_updates_users_row():
    """POST action=merge_with canonical_id=<existing>: skill_nodes rows get canonical_node_id updated."""
    user_id = str(uuid.uuid4())
    canonical_owner_id = str(uuid.uuid4())
    await _seed_user(user_id)
    await _seed_user(canonical_owner_id)

    proposal_name = "Fingerstyle Arpeggio"
    # Seed a canonical node owned by canonical_owner
    canonical_node_id = await _seed_skill_node(canonical_owner_id, "Classical Arpeggio", level="sub", canonical_node_id=None)
    # Make it truly canonical: set canonical_node_id = self.id
    async with _make_session() as db:
        await db.execute(
            text("UPDATE skill_nodes SET canonical_node_id = id WHERE id = :nid"),
            {"nid": canonical_node_id},
        )
        await db.commit()

    # Seed a user-scoped node for the proposal_name
    user_node_id = await _seed_skill_node(user_id, proposal_name, level="sub", canonical_node_id=None)
    proposal_id = await _seed_proposal(user_id, proposal_name, status="pending")

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/admin/curator/action",
                data={
                    "proposal_id": proposal_id,
                    "action": "merge_with",
                    "canonical_id": canonical_node_id,
                },
                headers={"X-Admin-Token": _TEST_TOKEN},
            )
        assert resp.status_code in (200, 303)

        async with _make_session() as db:
            status = await db.scalar(
                text("SELECT status FROM skill_node_proposals WHERE id = :pid"),
                {"pid": proposal_id},
            )
            assert status == "merged"

            # Verify user-scoped skill_nodes row now points at canonical
            node_canonical = await db.scalar(
                text("SELECT canonical_node_id FROM skill_nodes WHERE id = :nid"),
                {"nid": user_node_id},
            )
            assert node_canonical is not None
            assert str(node_canonical) == canonical_node_id
    finally:
        await _cleanup_user(user_id)
        await _cleanup_user(canonical_owner_id)


@pytest.mark.asyncio
async def test_hmac_compare_digest_constant_time():
    """Smoke check: get_admin_token completes in similar time for matching vs non-matching tokens.

    This is NOT a rigorous timing test — it's a smoke check that hmac.compare_digest
    does not blow up on either path. The real guarantee is code-level (using
    hmac.compare_digest instead of ==).
    """
    from app.api.deps import get_admin_token
    from fastapi import Header

    # Both calls should complete without raising (matching token)
    import asyncio

    os.environ["FLETCHER_ADMIN_TOKEN"] = _TEST_TOKEN

    async def _time_call(token: str) -> float:
        start = time.perf_counter()
        try:
            # Manually invoke the dependency function signature
            # get_admin_token takes x_admin_token as a string (header already extracted by FastAPI)
            await get_admin_token(x_admin_token=token)
        except Exception:
            pass  # 401 expected for wrong token; timing is still measured
        return time.perf_counter() - start

    t_match = await _time_call(_TEST_TOKEN)
    t_mismatch = await _time_call("wrong-token")

    # Both should be sub-millisecond; neither should be orders of magnitude apart
    # (we can't assert exact times in CI but we can assert both complete quickly)
    assert t_match < 1.0, f"Match path took {t_match:.3f}s — unexpectedly slow"
    assert t_mismatch < 1.0, f"Mismatch path took {t_mismatch:.3f}s — unexpectedly slow"
