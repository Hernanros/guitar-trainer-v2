"""HTTP tests for the practice-session lifecycle surface (FLE-21 §5).

test_session_lifecycle.py proves the state machine. This file proves the things that
only exist at the router and that FLE-10's offline outbox makes decisions on:

- **Status codes are the outbox's control flow.** FLE-21 §5.2 tells the player to treat
  4xx as TERMINAL (drop, never retry) and everything else as retryable. So a handler
  answering 500 where it should answer 422 turns one malformed write into an infinite
  retry loop on a pilot participant's phone, and a handler answering 200 where it
  should answer 404 makes a lost session look delivered.
- **`GET /current` returning 404 is the NORMAL answer** on a day with no session yet,
  not an error. Worth pinning, because the obvious "fix" is to make it 200-with-null
  and that would break the player's resume check.
- **Route ordering.** `/current` must resolve as a literal, not as a `{session_id}`
  UUID. FLE-63 adds `GET /practice-sessions/{id}` to this same router; if it is ever
  declared above `/current`, this file fails instead of the pilot.

These commit, because ASGI handlers open their own sessions through `get_db` and
cannot see an uncommitted fixture transaction. Each test cleans up its own user and
the cascade takes the sessions with it.
"""
import os
import uuid

import pytest

os.environ.pop("ANTHROPIC_API_KEY", None)

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.main import app  # noqa: E402
from app.sessions.store import resolve_or_generate  # noqa: E402

from tests.test_session_snapshot import (  # noqa: E402
    TEST_DB_URL,
    _make_drill,
    _make_root_chain,
    _make_user,
)


def _make_session() -> AsyncSession:
    engine = create_async_engine(TEST_DB_URL, echo=False, pool_size=1, max_overflow=0)
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db: AsyncSession, uid: uuid.UUID) -> None:
    """A user with enough material for build_plan, committed."""
    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:id, CAST(:p AS jsonb))"),
        {"id": uid, "p": '{"session_length_min": 30}'},
    )
    rhythm = await _make_root_chain(db, uid, "Rhythm", 0.2, 0.35)
    lead = await _make_root_chain(db, uid, "Lead", 0.6, 0.55)
    for leaf in rhythm + lead:
        for _ in range(2):
            await _make_drill(db, uid, leaf)
    await db.commit()


async def _generate_today(db: AsyncSession, uid: uuid.UUID) -> uuid.UUID:
    """Today's session for the caller, committed. FLE-63 will expose this over HTTP;
    until then the tests drive the writer directly so the lifecycle surface can be
    tested without waiting on that issue."""
    stored = await resolve_or_generate(db, uid, tz_offset_minutes=0)
    await db.commit()
    return stored.session_id


# Explicit and ordered. `skill_nodes.user_id` is NOT ON DELETE CASCADE, so a bare
# `DELETE FROM users` fails the FK and leaves every row behind — including the
# practice_sessions this file just wrote, which would then be visible to the sweep
# tests in test_session_lifecycle.py. Sessions go first so their items' SET NULL
# references to drills and skill_nodes are gone before those tables are touched.
_CLEANUP_STATEMENTS = (
    "DELETE FROM practice_sessions WHERE user_id = :u",
    "DELETE FROM drill_attempts WHERE user_id = :u",
    "DELETE FROM drill_progress WHERE user_id = :u",
    "DELETE FROM drills WHERE user_id = :u",
    "DELETE FROM user_sessions WHERE user_id = :u",
    "DELETE FROM song_skills WHERE skill_node_id IN "
    "    (SELECT id FROM skill_nodes WHERE user_id = :u)",
    "DELETE FROM songs WHERE user_id = :u",
    "DELETE FROM skill_nodes WHERE user_id = :u",
    "DELETE FROM users WHERE id = :u",
)


async def _cleanup(uid: uuid.UUID) -> None:
    async with _make_session() as db:
        for stmt in _CLEANUP_STATEMENTS:
            await db.execute(text(stmt), {"u": uid})
        await db.commit()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _headers(uid: uuid.UUID) -> dict:
    return {"X-User-ID": str(uid), "X-Timezone-Offset": "0"}


@pytest.mark.asyncio
async def test_current_is_404_before_a_session_exists():
    """The normal answer on a fresh day — not an error for the player."""
    uid = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, uid)
    try:
        async with _client() as c:
            resp = await c.get("/api/v1/practice-sessions/current", headers=_headers(uid))
        assert resp.status_code == 404, resp.text
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_current_returns_the_open_session_with_its_items():
    """Also pins route ordering: `/current` must not be parsed as a {session_id}."""
    uid = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, uid)
        sid = await _generate_today(db, uid)
    try:
        async with _client() as c:
            resp = await c.get("/api/v1/practice-sessions/current", headers=_headers(uid))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == str(sid)
        assert body["state"] == "planned"
        assert body["started_at"] is None, (
            "generating a session must not look like starting one (FLE-21 §1)"
        )
        assert len(body["items"]) == body["item_count"]
        assert all(i["state"] == "not_reached" for i in body["items"]), (
            "every item row exists from generation time (FLE-21 §2)"
        )
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_start_is_200_and_idempotent():
    uid = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, uid)
        sid = await _generate_today(db, uid)
    try:
        async with _client() as c:
            first = await c.post(
                f"/api/v1/practice-sessions/{sid}/start", headers=_headers(uid)
            )
            second = await c.post(
                f"/api/v1/practice-sessions/{sid}/start", headers=_headers(uid)
            )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["state"] == "in_progress"
        assert first.json()["started_at"] == second.json()["started_at"], (
            "a retried /start moved started_at — the day-7 return metric is now wrong"
        )
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_item_flow_enter_complete_skip():
    uid = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, uid)
        sid = await _generate_today(db, uid)
    try:
        async with _client() as c:
            h = _headers(uid)
            await c.post(f"/api/v1/practice-sessions/{sid}/start", headers=h)

            enter = await c.post(
                f"/api/v1/practice-sessions/{sid}/items/0/enter", headers=h
            )
            done = await c.post(
                f"/api/v1/practice-sessions/{sid}/items/0/complete",
                json={"active_seconds": 90, "rating": None, "advance_mode": "user_tap"},
                headers=h,
            )
            skip = await c.post(
                f"/api/v1/practice-sessions/{sid}/items/1/skip",
                json={"active_seconds": 4},
                headers=h,
            )
            late_enter = await c.post(
                f"/api/v1/practice-sessions/{sid}/items/0/enter", headers=h
            )

        assert enter.status_code == 200 and enter.json()["applied"] is True
        assert done.status_code == 200, done.text
        assert done.json()["state"] == "completed"
        assert skip.status_code == 200 and skip.json()["state"] == "skipped"
        # Out-of-order delivery: the outbox can flush an enter behind its own complete.
        assert late_enter.status_code == 200, "a late enter must not be an error"
        assert late_enter.json()["applied"] is False
        assert late_enter.json()["state"] == "completed"
        assert late_enter.json()["session"]["elapsed_active_seconds"] == 94
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_rating_an_unrated_item_is_422_not_500():
    """FLE-21 §5.1. The outbox drops 4xx and retries everything else, so this MUST be
    a 4xx — a 500 here is an infinite retry loop on a participant's phone."""
    uid = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, uid)
        sid = await _generate_today(db, uid)
        idx = await db.scalar(
            text(
                "SELECT item_index FROM practice_session_items "
                "WHERE session_id = :s AND NOT rated ORDER BY item_index LIMIT 1"
            ),
            {"s": sid},
        )
    assert idx is not None, "fixture produced no unrated item — scenario is wrong"
    try:
        async with _client() as c:
            resp = await c.post(
                f"/api/v1/practice-sessions/{sid}/items/{idx}/complete",
                json={"active_seconds": 60, "rating": "thats_what_im_looking_for"},
                headers=_headers(uid),
            )
        assert resp.status_code == 422, resp.text
        assert "rating_not_permitted_for_item" in resp.text
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_complete_session_returns_the_summary_numbers():
    """FLE-10 R8 — the summary screen needs all four, and the readout should never
    have to re-derive a ratio from counts it did not see."""
    uid = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, uid)
        sid = await _generate_today(db, uid)
    try:
        async with _client() as c:
            h = _headers(uid)
            await c.post(f"/api/v1/practice-sessions/{sid}/start", headers=h)
            await c.post(
                f"/api/v1/practice-sessions/{sid}/items/0/complete",
                json={"active_seconds": 60},
                headers=h,
            )
            await c.post(
                f"/api/v1/practice-sessions/{sid}/items/1/skip",
                json={"active_seconds": 0},
                headers=h,
            )
            resp = await c.post(
                f"/api/v1/practice-sessions/{sid}/complete", headers=h
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["done_items"] == 1
        assert body["skipped_items"] == 1
        assert body["planned_items"] == body["session"]["item_count"]
        assert body["session"]["state"] == "completed"
        assert body["session"]["terminal_reason"] == "user_completed"
        assert body["completion_ratio"] is not None
        # done + skipped != planned. The remainder is not_reached, and keeping that gap
        # visible is the point of writing every item row at generation time.
        assert body["done_items"] + body["skipped_items"] < body["planned_items"]
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_another_users_session_is_404():
    """Access control at the HTTP boundary. A crafted session UUID must not let one
    pilot participant write telemetry onto another's record."""
    owner = uuid.uuid4()
    intruder = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, owner)
        await _seed(db, intruder)
        sid = await _generate_today(db, owner)
    try:
        async with _client() as c:
            resp = await c.post(
                f"/api/v1/practice-sessions/{sid}/start", headers=_headers(intruder)
            )
        assert resp.status_code == 404, resp.text
    finally:
        await _cleanup(owner)
        await _cleanup(intruder)
