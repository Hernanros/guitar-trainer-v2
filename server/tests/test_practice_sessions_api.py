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
    _link_song_skill,
    _make_drill,
    _make_root_chain,
    _make_song,
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


# ---------------------------------------------------------------------------
# FLE-63 — generate + read-back, and the embed
# ---------------------------------------------------------------------------


async def _seed_with_song(db: AsyncSession, uid: uuid.UUID) -> int:
    """`_seed` plus a working-on song, so build_plan emits repertoire items.

    The default fixture produces an all-drill plan, which cannot prove the song half
    of the embed. Returns the song id.
    """
    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:id, CAST(:p AS jsonb))"),
        {"id": uid, "p": '{"session_length_min": 30}'},
    )
    rhythm = await _make_root_chain(db, uid, "Rhythm", 0.2, 0.35)
    lead = await _make_root_chain(db, uid, "Lead", 0.6, 0.55)
    for leaf in rhythm + lead:
        for _ in range(2):
            await _make_drill(db, uid, leaf)
    song_id = await _make_song(db, uid, category="working_on", bpm=120)
    for leaf in rhythm + lead:
        await _link_song_skill(db, song_id, leaf)
    await db.commit()
    return song_id


@pytest.mark.asyncio
async def test_today_generates_then_resolves_one_session():
    """FLE-63's done-when, and the double-tap the partial unique exists to arbitrate.

    201 then 200 is not cosmetic: a resolved session may be one the user is halfway
    through, so the player must be able to tell it from a fresh plan without guessing.
    """
    uid = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, uid)
    try:
        async with _client() as c:
            first = await c.post(
                "/api/v1/practice-sessions/today", headers=_headers(uid)
            )
            second = await c.post(
                "/api/v1/practice-sessions/today", headers=_headers(uid)
            )

        assert first.status_code == 201, first.text
        assert second.status_code == 200, second.text
        assert first.json()["id"] == second.json()["id"], (
            "two plans for one day — the open-day unique is not arbitrating"
        )
        assert first.json()["state"] == "planned"
        assert first.json()["started_at"] is None, (
            "generating must not look like starting (FLE-21 §1)"
        )

        async with _make_session() as db:
            count = await db.scalar(
                text("SELECT count(*) FROM practice_sessions WHERE user_id = :u"),
                {"u": uid},
            )
        assert count == 1
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_today_embeds_drill_content_inline():
    """FLE-63 decision 1, ruled EMBED on 2026-09-24.

    The session must be runnable with no further network. A drill item that carries
    only `drill_id` forces a fetch at the item boundary — i.e. mid-practice, in a room
    with bad wifi — which is exactly what the ruling rejected.
    """
    uid = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, uid)
    try:
        async with _client() as c:
            resp = await c.post(
                "/api/v1/practice-sessions/today", headers=_headers(uid)
            )
        assert resp.status_code == 201, resp.text
        items = resp.json()["items"]
        drill_items = [i for i in items if i["kind"] == "drill"]
        assert drill_items, "fixture produced no drill items — scenario is wrong"

        for item in drill_items:
            d = item["drill"]
            assert d is not None, f"item {item['item_index']} embeds no drill content"
            assert d["drill_id"] == item["drill_id"]
            for field in (
                "name",
                "what",
                "tab_snippet",
                "start_bpm",
                "target_bpm",
                "repetitions",
                "success_criterion",
            ):
                assert d[field] is not None, f"{field} missing — the item cannot render"
            # FLE-4 §12.1: a warm-up item can resolve to a GLOBAL seed drill. If
            # ownership leaked onto the wire the player would need a kind-of-drill
            # branch on top of the kind-of-item branch it already has.
            assert "user_id" not in d, (
                "embedded drill leaks ownership — seed and generated drills must be "
                "structurally identical on the wire"
            )
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_today_embeds_song_content_inline():
    """The song half. `breakdown` ships inline rather than relying on the client's
    per-song useBreakdown cache, which is only warm if the user opened the Today card
    first — a resumed session has no such guarantee."""
    uid = uuid.uuid4()
    async with _make_session() as db:
        song_id = await _seed_with_song(db, uid)
    try:
        async with _client() as c:
            resp = await c.post(
                "/api/v1/practice-sessions/today",
                json={"song_id": song_id},
                headers=_headers(uid),
            )
        assert resp.status_code == 201, resp.text
        items = resp.json()["items"]
        song_items = [i for i in items if i["kind"] in ("song_section", "song_play")]
        assert song_items, "fixture produced no repertoire items — scenario is wrong"

        for item in song_items:
            s = item["song"]
            assert s is not None, f"item {item['item_index']} embeds no song content"
            assert s["song_id"] == item["song_id"] == song_id
            assert s["title"] and s["artist"]
            assert "breakdown" in s
            assert item["drill"] is None, "a song item must not carry drill content"
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_current_carries_the_same_embedded_content_as_today():
    """The resume path must be self-sufficient too.

    FLE-10 resumes through `GET /current`, not through `/today`. If only the generate
    response embedded content, every resumed session would be a blank player.
    """
    uid = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, uid)
    try:
        async with _client() as c:
            generated = await c.post(
                "/api/v1/practice-sessions/today", headers=_headers(uid)
            )
            current = await c.get(
                "/api/v1/practice-sessions/current", headers=_headers(uid)
            )
        assert current.status_code == 200, current.text
        assert [i["drill"] for i in generated.json()["items"]] == [
            i["drill"] for i in current.json()["items"]
        ]
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_get_session_by_id_reads_back_and_does_not_shadow_current():
    """Route ordering. `/current` is a literal and must not be parsed as a UUID —
    if `GET /{session_id}` is ever declared above it, this fails instead of the pilot.
    """
    uid = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, uid)
    try:
        async with _client() as c:
            sid = (
                await c.post("/api/v1/practice-sessions/today", headers=_headers(uid))
            ).json()["id"]
            by_id = await c.get(
                f"/api/v1/practice-sessions/{sid}", headers=_headers(uid)
            )
            current = await c.get(
                "/api/v1/practice-sessions/current", headers=_headers(uid)
            )

        assert by_id.status_code == 200, by_id.text
        assert by_id.json()["id"] == sid
        assert by_id.json()["items"] == current.json()["items"]
        assert current.status_code == 200, (
            "/current was swallowed by the {session_id} route"
        )
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_get_another_users_session_is_404():
    """Same access-control rule as the lifecycle writes: a crafted UUID must not read
    another participant's plan."""
    owner = uuid.uuid4()
    intruder = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, owner)
        await _seed(db, intruder)
        sid = await _generate_today(db, owner)
    try:
        async with _client() as c:
            resp = await c.get(
                f"/api/v1/practice-sessions/{sid}", headers=_headers(intruder)
            )
        assert resp.status_code == 404, resp.text
    finally:
        await _cleanup(owner)
        await _cleanup(intruder)


@pytest.mark.asyncio
async def test_today_for_an_unknown_user_is_404():
    """SnapshotError — no such user row. Distinct from 409 (user exists, nothing to
    practise), which is a real product state with a screen behind it."""
    async with _client() as c:
        resp = await c.post(
            "/api/v1/practice-sessions/today", headers=_headers(uuid.uuid4())
        )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_today_with_no_material_is_409_not_500():
    """A user with no drills and no song. The outbox drops 4xx and retries the rest,
    so a 500 here would be an infinite retry loop against a user who simply has
    nothing banked yet."""
    uid = uuid.uuid4()
    async with _make_session() as db:
        await db.execute(
            text("INSERT INTO users (id, preferences) VALUES (:id, '{}'::jsonb)"),
            {"id": uid},
        )
        await db.commit()
    try:
        async with _client() as c:
            resp = await c.post(
                "/api/v1/practice-sessions/today", headers=_headers(uid)
            )
        assert resp.status_code == 409, resp.text
    finally:
        await _cleanup(uid)


# ---------------------------------------------------------------------------
# FLE-64 — §5.2's fan-out, at the HTTP boundary.
#
# test_session_ladder.py proves the transitions. What only exists HERE is the status
# code and the response shape, and both are outbox control flow: the player drops
# 4xx and retries the rest, so a duplicate daily verdict answering 500 would be an
# infinite retry loop, and one answering 200 would hide a state desync the UI is
# supposed to resolve by repopulating.
# ---------------------------------------------------------------------------


async def _first_rated_index(db: AsyncSession, sid: uuid.UUID, kind: str) -> int:
    idx = await db.scalar(
        text(
            "SELECT item_index FROM practice_session_items "
            "WHERE session_id = :s AND rated = true AND kind::text = :k "
            "ORDER BY item_index LIMIT 1"
        ),
        {"s": sid, "k": kind},
    )
    assert idx is not None, f"no rated {kind} item in the generated plan"
    return idx


@pytest.mark.asyncio
async def test_rating_a_drill_item_returns_the_ladder_move_it_caused():
    """FLE-10 R5 — the client is TOLD the outcome, it never classifies one."""
    uid = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, uid)
        sid = await _generate_today(db, uid)
        idx = await _first_rated_index(db, sid, "drill")
        reps = await db.scalar(
            text(
                "SELECT planned_reps FROM practice_session_items "
                "WHERE session_id = :s AND item_index = :i"
            ),
            {"s": sid, "i": idx},
        )
    try:
        async with _client() as c:
            h = _headers(uid)
            first = await c.post(
                f"/api/v1/practice-sessions/{sid}/items/{idx}/complete",
                json={
                    "active_seconds": 210,
                    "rating": "thats_what_im_looking_for",
                    "completed_reps": reps,
                },
                headers=h,
            )
            # The outbox flushes twice. The second must not move anything.
            second = await c.post(
                f"/api/v1/practice-sessions/{sid}/items/{idx}/complete",
                json={
                    "active_seconds": 210,
                    "rating": "thats_what_im_looking_for",
                    "completed_reps": reps,
                },
                headers=h,
            )

        assert first.status_code == 200, first.text
        move = first.json()["ladder"]
        assert move is not None
        assert move["outcome"] == "CLEAR"
        assert move["rung_before"] == move["rung_after"], (
            "one clear banks, it does not push — §7.2 wants two in a row"
        )
        assert move["progress_state"] == "active"

        assert second.status_code == 200, second.text
        assert second.json()["ladder"] is None, (
            "a duplicate flush must fan out to nothing; a second rung_after would "
            "report a move that did not happen"
        )
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_an_unrated_complete_carries_no_ladder():
    """Warm-up and song_play are unrated by design; `ladder` must stay null."""
    uid = uuid.uuid4()
    async with _make_session() as db:
        await _seed(db, uid)
        sid = await _generate_today(db, uid)
    try:
        async with _client() as c:
            resp = await c.post(
                f"/api/v1/practice-sessions/{sid}/items/0/complete",
                json={"active_seconds": 90, "rating": None},
                headers=_headers(uid),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ladder"] is None
        assert resp.json()["daily_verdict_recorded"] is False
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_the_repertoire_rating_records_the_daily_verdict():
    """§5.2 step 4 — one rating call. The player never also POSTs /api/v1/sessions."""
    uid = uuid.uuid4()
    async with _make_session() as db:
        song_id = await _seed_with_song(db, uid)
    try:
        async with _client() as c:
            h = _headers(uid)
            sid = (
                await c.post(
                    "/api/v1/practice-sessions/today",
                    json={"song_id": song_id},
                    headers=h,
                )
            ).json()["id"]
            idx = None
            async with _make_session() as db:
                idx = await _first_rated_index(db, uuid.UUID(sid), "song_section")
            resp = await c.post(
                f"/api/v1/practice-sessions/{sid}/items/{idx}/complete",
                json={"active_seconds": 300, "rating": "getting_closer"},
                headers=h,
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["daily_verdict_recorded"] is True
        assert resp.json()["ladder"] is None, "a song is not a drill"
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_a_duplicate_daily_verdict_is_409_with_the_rating_still_committed():
    """The user already rated the song on the Today card.

    409 keeps UI-SPEC §10's meaning — invalidate and repopulate, no error toast — and
    the body is the full ItemEventResponse, because the item's rating, state and
    active_seconds all COMMITTED. Answering 409 by rolling them back would discard
    telemetry the player will never send again in order to report a row that was
    already there.
    """
    uid = uuid.uuid4()
    async with _make_session() as db:
        song_id = await _seed_with_song(db, uid)
    try:
        async with _client() as c:
            h = _headers(uid)
            sid = (
                await c.post(
                    "/api/v1/practice-sessions/today",
                    json={"song_id": song_id},
                    headers=h,
                )
            ).json()["id"]
            async with _make_session() as db:
                idx = await _first_rated_index(db, uuid.UUID(sid), "song_section")
            today_card = await c.post(
                "/api/v1/sessions",
                json={"song_id": song_id, "rating": "thats_what_im_looking_for"},
                headers=h,
            )
            in_player = await c.post(
                f"/api/v1/practice-sessions/{sid}/items/{idx}/complete",
                json={"active_seconds": 300, "rating": "getting_closer"},
                headers=h,
            )

        assert today_card.status_code == 201, today_card.text
        assert in_player.status_code == 409, in_player.text
        body = in_player.json()
        assert body["state"] == "completed", "the item write is not rolled back"
        assert body["daily_verdict_recorded"] is False
        assert body["session"]["elapsed_active_seconds"] == 300

        async with _make_session() as db:
            stored = await db.scalar(
                text(
                    "SELECT rating::text FROM practice_session_items "
                    "WHERE session_id = :s AND item_index = :i"
                ),
                {"s": uuid.UUID(sid), "i": idx},
            )
        assert stored == "getting_closer"
    finally:
        await _cleanup(uid)
