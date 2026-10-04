"""FLE-23 — multi-user hardening: pool sizing, throttle, dollars, identity, TOCTOU.

One test per audit finding, in the order the issue lists them:

  §1/§2  pool sizing            test_pool_* / test_int_env_*
  §3     re-run throttle        test_throttle_*
  §3     onboarding cap         test_onboarding_cap_is_enforced_by_governed
  §4     dollar accounting      test_price_* / test_record_actuals_writes_dollars /
                                test_record_estimate_prices_the_worst_case
  §5     identity enforcement   test_*_requires_matching_user_id
  §6     cap TOCTOU             test_concurrent_calls_cannot_both_pass_the_cap

The TOCTOU test is the one that needs a real Postgres: the fix is a
transaction-scoped advisory lock, and SQLite has nothing equivalent, so it can
only be asserted against the database that actually enforces it.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.governor import (
    BudgetExceededError,
    assert_cap_available,
    governed,
    price_dollars,
    record_actuals,
    record_estimate,
)
from app.main import app

os.environ.pop("ANTHROPIC_API_KEY", None)


# ---------------------------------------------------------------------------
# Test DB helpers (same shape as tests/test_governor.py)
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


async def _seed_user(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        text(
            "INSERT INTO users (id, preferences) "
            "VALUES (:uid, '{}'::jsonb) ON CONFLICT DO NOTHING"
        ),
        {"uid": user_id},
    )
    await db.commit()


async def _cleanup_user(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        text("DELETE FROM governor_calls WHERE user_id = :uid"), {"uid": user_id}
    )
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await db.commit()


# ---------------------------------------------------------------------------
# §4 — pricing (pure function, no DB)
# ---------------------------------------------------------------------------

def test_price_dollars_uses_the_published_sonnet_rate():
    """1M in + 1M out on Sonnet = $3.00 + $15.00.

    The rate table is keyed by the model string recorded on the row, so this
    also pins that `claude-sonnet-4-6` is the key the governor actually writes.
    """
    assert price_dollars("claude-sonnet-4-6", 1_000_000, 1_000_000) == Decimal("18.000000")


def test_price_dollars_is_quantized_to_the_numeric_10_6_column():
    """dollars_* is NUMERIC(10,6): the returned value must already be at that
    precision, or the stored value and the computed value disagree."""
    priced = price_dollars("claude-sonnet-4-6", 1, 1)
    assert priced == Decimal("0.000018")
    assert -priced.as_tuple().exponent == 6


def test_price_dollars_returns_none_for_an_unpriced_model():
    """A wrong number in a cost audit trail is worse than a missing one."""
    assert price_dollars("some-future-model", 1000, 1000) is None


def test_price_dollars_treats_null_token_counts_as_zero():
    """On a truncated call the input cost is still real money spent — it must be
    attributed rather than discarded along with the missing output count."""
    assert price_dollars("claude-sonnet-4-6", 1_000_000, None) == Decimal("3.000000")
    assert price_dollars("claude-sonnet-4-6", None, None) == Decimal("0.000000")


# ---------------------------------------------------------------------------
# §4 — the dollar columns are actually written
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_actuals_writes_dollars_actual():
    """`SELECT SUM(dollars_actual) ... GROUP BY user_id` returned a column of
    NULLs before this; it is the query a per-user spend audit reads."""
    user_id = str(uuid.uuid4())
    call_id = uuid.uuid4()
    db = _make_session()
    try:
        await _seed_user(db, user_id)
        await db.execute(
            text(
                "INSERT INTO governor_calls (id, user_id, feature, model) "
                "VALUES (:id, :uid, 'breakdown', 'claude-sonnet-4-6')"
            ),
            {"id": str(call_id), "uid": user_id},
        )
        await db.commit()

        await record_actuals(call_id, prompt_tokens_actual=10_000, output_tokens_actual=2_000)

        row = (await db.execute(
            text(
                "SELECT prompt_tokens_actual, output_tokens_actual, dollars_actual "
                "FROM governor_calls WHERE id = :id"
            ),
            {"id": str(call_id)},
        )).one()
        assert row.prompt_tokens_actual == 10_000
        assert row.output_tokens_actual == 2_000
        # 10k in @ $3/Mtok = $0.03; 2k out @ $15/Mtok = $0.03
        assert Decimal(row.dollars_actual) == Decimal("0.060000")
    finally:
        await _cleanup_user(db, user_id)
        await db.close()


@pytest.mark.asyncio
async def test_record_estimate_prices_the_worst_case_not_the_prompt_alone():
    """dollars_estimated answers "can I afford to dispatch this?", so it is the
    most the call can cost — prompt tokens plus the create() max_tokens ceiling,
    not the prompt side alone (which under-reports by the 5x output rate)."""
    user_id = str(uuid.uuid4())
    call_id = uuid.uuid4()
    db = _make_session()
    try:
        await _seed_user(db, user_id)
        await db.execute(
            text(
                "INSERT INTO governor_calls (id, user_id, feature, model) "
                "VALUES (:id, :uid, 'breakdown', 'claude-sonnet-4-6')"
            ),
            {"id": str(call_id), "uid": user_id},
        )
        await db.commit()

        await record_estimate(call_id, 10_000, 8192)

        row = (await db.execute(
            text(
                "SELECT prompt_tokens_estimated, dollars_estimated "
                "FROM governor_calls WHERE id = :id"
            ),
            {"id": str(call_id)},
        )).one()
        assert row.prompt_tokens_estimated == 10_000
        expected = price_dollars("claude-sonnet-4-6", 10_000, 8192)
        assert Decimal(row.dollars_estimated) == expected
        # And it is strictly above the prompt-only figure, which is the point.
        assert expected > price_dollars("claude-sonnet-4-6", 10_000, 0)
    finally:
        await _cleanup_user(db, user_id)
        await db.close()


@pytest.mark.asyncio
async def test_record_estimate_without_a_ceiling_prices_the_prompt_only():
    """Callers that omit max_output_tokens get a floor, not a ceiling. Pinned so
    the default cannot silently become a guess at the output size."""
    user_id = str(uuid.uuid4())
    call_id = uuid.uuid4()
    db = _make_session()
    try:
        await _seed_user(db, user_id)
        await db.execute(
            text(
                "INSERT INTO governor_calls (id, user_id, feature, model) "
                "VALUES (:id, :uid, 'skill_verify', 'claude-sonnet-4-6')"
            ),
            {"id": str(call_id), "uid": user_id},
        )
        await db.commit()

        await record_estimate(call_id, 1_000_000)

        dollars = (await db.execute(
            text("SELECT dollars_estimated FROM governor_calls WHERE id = :id"),
            {"id": str(call_id)},
        )).scalar_one()
        assert Decimal(dollars) == Decimal("3.000000")
    finally:
        await _cleanup_user(db, user_id)
        await db.close()


# ---------------------------------------------------------------------------
# §6 — the cap holds under concurrency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_calls_cannot_both_pass_the_cap():
    """Two requests arriving together at the cap boundary.

    Before the advisory lock, both ran COUNT(*) = 2 against a cap of 3, both
    passed, and both inserted — the cap was advisory, which is the one thing a
    cap must not be. Each task gets its own session, because the lock is
    transaction-scoped and a shared session would serialize for the wrong reason.
    """
    user_id = uuid.uuid4()
    setup = _make_session()
    try:
        await _seed_user(setup, str(user_id))
        # Two rows already in the window, cap of 3: exactly one slot left.
        for _ in range(2):
            await setup.execute(
                text(
                    "INSERT INTO governor_calls (id, user_id, feature, model) "
                    "VALUES (:id, :uid, 'breakdown', 'claude-sonnet-4-6')"
                ),
                {"id": str(uuid.uuid4()), "uid": str(user_id)},
            )
        await setup.commit()

        @governed(feature="breakdown", cap=3)
        async def _claim(*, db: AsyncSession, user_id: uuid.UUID) -> str:
            return "ok"

        async def _attempt() -> object:
            db = _make_session()
            try:
                return await _claim(db=db, user_id=user_id)
            except BudgetExceededError as exc:
                return exc
            finally:
                await db.close()

        results = await asyncio.gather(_attempt(), _attempt())

        passed = [r for r in results if r == "ok"]
        refused = [r for r in results if isinstance(r, BudgetExceededError)]
        assert len(passed) == 1, f"expected exactly one winner, got {results}"
        assert len(refused) == 1, f"expected exactly one refusal, got {results}"

        # And the audit table agrees — the loser inserted nothing.
        total = (await setup.execute(
            text(
                "SELECT COUNT(*) FROM governor_calls "
                "WHERE user_id = :uid AND feature = 'breakdown'"
            ),
            {"uid": str(user_id)},
        )).scalar_one()
        assert total == 3
    finally:
        await _cleanup_user(setup, str(user_id))
        await setup.close()


@pytest.mark.asyncio
async def test_uncapped_feature_still_inserts_its_audit_row():
    """cap=None skips the advisory lock (nothing to enforce, so no contention
    worth paying for) — but the governor_calls row must still be written, or the
    feature drops out of the cost audit entirely."""
    user_id = uuid.uuid4()
    db = _make_session()
    try:
        await _seed_user(db, str(user_id))

        @governed(feature="skill_verify", cap=None)
        async def _uncapped(*, db: AsyncSession, user_id: uuid.UUID) -> str:
            return "ok"

        for _ in range(4):
            assert await _uncapped(db=db, user_id=user_id) == "ok"

        count = (await db.execute(
            text(
                "SELECT COUNT(*) FROM governor_calls "
                "WHERE user_id = :uid AND feature = 'skill_verify'"
            ),
            {"uid": str(user_id)},
        )).scalar_one()
        assert count == 4
    finally:
        await _cleanup_user(db, str(user_id))
        await db.close()


# ---------------------------------------------------------------------------
# §3 — assert_cap_available: a read-only probe, used before a destructive wipe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assert_cap_available_raises_at_the_cap_and_writes_nothing():
    """POST /users/{id}/re-run commits a wipe before it can reach the governed
    call, so a user at the cap would lose their graph and then get a budget
    error. The probe turns that into a refusal with the graph intact — and must
    not itself consume a slot."""
    user_id = uuid.uuid4()
    db = _make_session()
    try:
        await _seed_user(db, str(user_id))
        for _ in range(5):
            await db.execute(
                text(
                    "INSERT INTO governor_calls (id, user_id, feature, model) "
                    "VALUES (:id, :uid, 'onboarding', 'claude-sonnet-4-6')"
                ),
                {"id": str(uuid.uuid4()), "uid": str(user_id)},
            )
        await db.commit()

        with pytest.raises(BudgetExceededError):
            await assert_cap_available(db, user_id, "onboarding", 5)

        count = (await db.execute(
            text("SELECT COUNT(*) FROM governor_calls WHERE user_id = :uid"),
            {"uid": str(user_id)},
        )).scalar_one()
        assert count == 5, "the probe reserved a slot — it is supposed to be read-only"
    finally:
        await _cleanup_user(db, str(user_id))
        await db.close()


@pytest.mark.asyncio
async def test_assert_cap_available_passes_below_the_cap():
    user_id = uuid.uuid4()
    db = _make_session()
    try:
        await _seed_user(db, str(user_id))
        await db.execute(
            text(
                "INSERT INTO governor_calls (id, user_id, feature, model) "
                "VALUES (:id, :uid, 'onboarding', 'claude-sonnet-4-6')"
            ),
            {"id": str(uuid.uuid4()), "uid": str(user_id)},
        )
        await db.commit()

        await assert_cap_available(db, user_id, "onboarding", 5)  # must not raise
    finally:
        await _cleanup_user(db, str(user_id))
        await db.close()


@pytest.mark.asyncio
async def test_assert_cap_available_honours_the_env_override(monkeypatch):
    """FLETCHER_CAP_ONBOARDING=off must lift the probe as well as the governed
    check — FLE-58 is the precedent: a cap locked the only real user out, and
    support needs one switch, not two."""
    monkeypatch.setenv("FLETCHER_CAP_ONBOARDING", "off")
    user_id = uuid.uuid4()
    db = _make_session()
    try:
        await _seed_user(db, str(user_id))
        for _ in range(9):
            await db.execute(
                text(
                    "INSERT INTO governor_calls (id, user_id, feature, model) "
                    "VALUES (:id, :uid, 'onboarding', 'claude-sonnet-4-6')"
                ),
                {"id": str(uuid.uuid4()), "uid": str(user_id)},
            )
        await db.commit()

        await assert_cap_available(db, user_id, "onboarding", 5)  # must not raise
    finally:
        await _cleanup_user(db, str(user_id))
        await db.close()


@pytest.mark.asyncio
async def test_onboarding_cap_is_enforced_by_governed():
    """The durable half of the §3 fix. POST /users/{id}/re-run was
    @governed(cap=None): $0.05–$0.19 a tap, unbounded."""
    from app.ai.governor import ONBOARDING_CAP

    user_id = uuid.uuid4()
    db = _make_session()
    try:
        await _seed_user(db, str(user_id))
        for _ in range(ONBOARDING_CAP):
            await db.execute(
                text(
                    "INSERT INTO governor_calls (id, user_id, feature, model) "
                    "VALUES (:id, :uid, 'onboarding', 'claude-sonnet-4-6')"
                ),
                {"id": str(uuid.uuid4()), "uid": str(user_id)},
            )
        await db.commit()

        @governed(feature="onboarding", cap=ONBOARDING_CAP)
        async def _parse(*, db: AsyncSession, user_id: uuid.UUID) -> str:
            raise AssertionError("dispatched past the cap")

        with pytest.raises(BudgetExceededError) as exc:
            await _parse(db=db, user_id=user_id)
        assert exc.value.feature == "onboarding"
        assert exc.value.resets_at
    finally:
        await _cleanup_user(db, str(user_id))
        await db.close()


# ---------------------------------------------------------------------------
# §3 — the in-process re-run throttle
# ---------------------------------------------------------------------------

def test_throttle_allows_up_to_the_limit_then_429s():
    from fastapi import HTTPException

    from app.api.deps import _SlidingWindowThrottle

    throttle = _SlidingWindowThrottle("test", max_calls=3, window_seconds=600)
    for _ in range(3):
        throttle.check("device-a")

    with pytest.raises(HTTPException) as exc:
        throttle.check("device-a")
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers
    assert int(exc.value.headers["Retry-After"]) > 0


def test_throttle_is_per_key():
    """Keyed on the device UUID: one user hammering re-run must not refuse
    everybody else's."""
    from app.api.deps import _SlidingWindowThrottle

    throttle = _SlidingWindowThrottle("test", max_calls=1, window_seconds=600)
    throttle.check("device-a")
    throttle.check("device-b")  # must not raise


def test_throttle_window_slides(monkeypatch):
    """Monotonic clock, so an NTP step or DST change can neither widen nor
    collapse the window. Driven here by faking time.monotonic rather than
    sleeping 600 seconds."""
    from fastapi import HTTPException

    import app.api.deps as deps

    now = [1_000.0]
    monkeypatch.setattr(deps.time, "monotonic", lambda: now[0])

    throttle = deps._SlidingWindowThrottle("test", max_calls=2, window_seconds=60)
    throttle.check("device-a")
    throttle.check("device-a")
    with pytest.raises(HTTPException):
        throttle.check("device-a")

    now[0] += 61.0  # both hits now fall outside the window
    throttle.check("device-a")  # must not raise


def test_throttle_fails_open_at_the_key_ceiling(monkeypatch):
    """The throttle is defence in depth; the governor cap is the real spend
    bound. At the bookkeeping ceiling it allows the request rather than
    rejecting real traffic to protect a dict."""
    import app.api.deps as deps

    now = [1_000.0]
    monkeypatch.setattr(deps.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(deps._SlidingWindowThrottle, "_MAX_KEYS", 4)

    throttle = deps._SlidingWindowThrottle("test", max_calls=1, window_seconds=600)
    for i in range(4):
        throttle.check(f"device-{i}")

    # Ceiling reached and nothing expired — the new key is allowed through.
    throttle.check("device-overflow")
    assert "device-overflow" not in throttle._hits


def test_throttle_sweeps_expired_keys_at_the_ceiling(monkeypatch):
    """Lazy pruning plus a ceiling sweep: a key touched once and never again
    must not leak a deque forever."""
    import app.api.deps as deps

    now = [1_000.0]
    monkeypatch.setattr(deps.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(deps._SlidingWindowThrottle, "_MAX_KEYS", 4)

    throttle = deps._SlidingWindowThrottle("test", max_calls=1, window_seconds=60)
    for i in range(4):
        throttle.check(f"device-{i}")

    now[0] += 61.0  # every existing key is now fully expired
    throttle.check("device-fresh")
    assert set(throttle._hits) == {"device-fresh"}


# ---------------------------------------------------------------------------
# §5 — identity enforcement on the three path-only endpoints
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path_suffix",
    ["", "/skill-graph"],
    ids=["get_user", "get_skill_graph"],
)
@pytest.mark.asyncio
async def test_get_endpoints_require_matching_user_id(path_suffix):
    """Before this, any device could read any user's preferences and skill graph
    by editing one path segment."""
    victim = str(uuid.uuid4())
    attacker = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/v1/users/{victim}{path_suffix}",
            headers={"X-User-ID": attacker},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_re_run_requires_matching_user_id():
    """re-run is both destructive and a spend trigger — the sharpest of the
    three. 403 must land before the wipe, so an unseeded victim still 403s
    rather than 404ing.

    The body is a fully valid UserBootstrapRequest on purpose: a 422 from a
    malformed body would pass this assertion for the wrong reason.
    """
    victim = str(uuid.uuid4())
    attacker = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/v1/users/{victim}/re-run",
            headers={"X-User-ID": attacker},
            json={
                "user_id": victim,
                "songs": {"can_play": [], "working_on": [], "aspirational": []},
                "preferences": {"session_length_min": 30, "retention_format": "streak"},
                "raw_input": {"can_play": "", "working_on": "", "aspirational": ""},
            },
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_user_still_404s_for_your_own_unbootstrapped_id():
    """The identity check must not shadow the existing 404 contract — the app
    reads a 404 here as "not onboarded yet"."""
    user_id = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/v1/users/{user_id}", headers={"X-User-ID": user_id}
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# §5b — identity enforcement on POST /users, the body-only endpoint
# ---------------------------------------------------------------------------
# The §5 fix above swept the three endpoints that took user_id from the PATH.
# POST /users took it from the BODY, so it did not match that pattern and was
# left behind — it was the only user-scoped route in the codebase with no
# Depends(get_user_id) at all. Two reachable consequences, both tested here.

@pytest.mark.asyncio
async def test_bootstrap_requires_matching_user_id():
    """A caller may only bootstrap their own device UUID.

    The body is a fully valid UserBootstrapRequest on purpose: a 422 from a
    malformed body would pass this assertion for the wrong reason.
    """
    victim = str(uuid.uuid4())
    attacker = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/users",
            headers={"X-User-ID": attacker},
            json={
                "user_id": victim,
                "songs": {"can_play": [], "working_on": [], "aspirational": []},
                "preferences": {"session_length_min": 30, "retention_format": "streak"},
                "raw_input": {"can_play": "", "working_on": "", "aspirational": ""},
            },
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_bootstrap_does_not_leak_an_existing_users_graph():
    """The read half, and the sharper of the two.

    The idempotency guard short-circuits when skill_nodes already exist and
    returns them with mode='existing'. Reached with someone else's user_id in
    the body, that made POST /users an unauthenticated read of any bootstrapped
    user's full skill graph — no header required at all, since the endpoint took
    none. The 403 must land BEFORE the guard runs, so the victim's node names
    never reach the response.
    """
    victim = str(uuid.uuid4())
    attacker = str(uuid.uuid4())
    db = _make_session()
    try:
        await _seed_user(db, victim)
        await db.execute(
            text(
                "INSERT INTO skill_nodes (id, user_id, name, level) "
                "VALUES (:nid, :uid, 'Victim Private Skill', 'root')"
            ),
            {"nid": str(uuid.uuid4()), "uid": victim},
        )
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/users",
                headers={"X-User-ID": attacker},
                json={
                    "user_id": victim,
                    "songs": {"can_play": [], "working_on": [], "aspirational": []},
                    "preferences": {
                        "session_length_min": 30,
                        "retention_format": "streak",
                    },
                    "raw_input": {
                        "can_play": "",
                        "working_on": "",
                        "aspirational": "",
                    },
                },
            )
        assert resp.status_code == 403
        assert "Victim Private Skill" not in resp.text
    finally:
        await db.execute(
            text("DELETE FROM skill_nodes WHERE user_id = :uid"), {"uid": victim}
        )
        await db.commit()
        await _cleanup_user(db, victim)
        await db.close()


@pytest.mark.asyncio
async def test_bootstrap_does_not_overwrite_an_existing_users_preferences():
    """The mutate half.

    When the victim has a users row but NO skill_nodes — a real state, since
    bootstrap commits the users row in Step 2 before the graph writes in Step 3,
    and fail-open leaves it that way if Sonnet dies — the idempotency guard does
    not fire and the ON CONFLICT DO UPDATE upsert runs. That overwrote the
    victim's `preferences` and `raw_onboarding_text` with the attacker's, then
    wrote a graph derived from the attacker's wizard text under the victim's id.
    """
    victim = str(uuid.uuid4())
    attacker = str(uuid.uuid4())
    db = _make_session()
    try:
        await db.execute(
            text(
                "INSERT INTO users (id, preferences, raw_onboarding_text) VALUES "
                "(:uid, '{\"session_length_min\": 45}'::jsonb, "
                "'{\"can_play\": \"victim text\"}'::jsonb)"
            ),
            {"uid": victim},
        )
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/users",
                headers={"X-User-ID": attacker},
                json={
                    "user_id": victim,
                    "songs": {"can_play": [], "working_on": [], "aspirational": []},
                    "preferences": {
                        "session_length_min": 15,
                        "retention_format": "streak",
                    },
                    "raw_input": {
                        "can_play": "attacker text",
                        "working_on": "",
                        "aspirational": "",
                    },
                },
            )
        assert resp.status_code == 403

        row = (await db.execute(
            text(
                "SELECT preferences, raw_onboarding_text FROM users WHERE id = :uid"
            ),
            {"uid": victim},
        )).one()
        assert row.preferences["session_length_min"] == 45
        assert row.raw_onboarding_text["can_play"] == "victim text"
    finally:
        await db.execute(
            text("DELETE FROM skill_nodes WHERE user_id = :uid"), {"uid": victim}
        )
        await db.commit()
        await _cleanup_user(db, victim)
        await db.close()


@pytest.mark.asyncio
async def test_bootstrap_rejects_a_missing_identity_header():
    """POST /users was the one user-scoped route reachable with no X-User-ID at
    all. It must now behave like every other one: 422 from FastAPI's required
    header coercion, before any handler logic.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/users",
            json={
                "user_id": str(uuid.uuid4()),
                "songs": {"can_play": [], "working_on": [], "aspirational": []},
                "preferences": {"session_length_min": 30, "retention_format": "streak"},
                "raw_input": {"can_play": "", "working_on": "", "aspirational": ""},
            },
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# §1/§2 — pool sizing
# ---------------------------------------------------------------------------

def test_pool_ceiling_covers_the_pilot_worst_minute():
    """5 concurrent onboardings (7 connections each) + 10 concurrent breakdowns
    (1 each, held for the full ~76s Sonnet call) = 45. The default ceiling must
    clear that.

    The upper bound is deliberately 100 rather than prod's measured
    max_connections of 500: it is the conservative figure a smaller Postgres
    would give us, so this test keeps failing if someone sizes the pool for one
    specific server rather than for the floor.
    """
    from app.db.session import MAX_OVERFLOW, POOL_SIZE

    ceiling = POOL_SIZE + MAX_OVERFLOW
    assert ceiling >= 45
    assert ceiling < 100


def test_pool_ceiling_covers_the_verifier_fanout():
    """Raising _VERIFIER_SEMAPHORE_LIMIT multiplies onboarding's connection
    cost; this fails if the two drift apart."""
    from app.api.v1.users import _VERIFIER_SEMAPHORE_LIMIT
    from app.db.session import MAX_OVERFLOW, POOL_SIZE

    per_onboarding = 2 + _VERIFIER_SEMAPHORE_LIMIT
    assert POOL_SIZE + MAX_OVERFLOW >= per_onboarding * 2


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("25", 25),
        ("", 15),          # unset — keep the default
        ("abc", 15),       # typo must not silently shrink the pool
        ("0", 15),         # 0 means "unlimited" to QueuePool — the opposite of intent
        ("-5", 15),
        ("  30  ", 30),
    ],
)
def test_int_env_rejects_values_that_would_break_the_pool(monkeypatch, raw, expected):
    from app.db.session import _int_env

    monkeypatch.setenv("FLETCHER_TEST_POOL_VALUE", raw)
    assert _int_env("FLETCHER_TEST_POOL_VALUE", 15) == expected


def test_engine_is_created_with_the_configured_pool():
    """Guards against the sizing being computed and then not passed to
    create_async_engine — the exact shape of the original bug."""
    from app.db.session import MAX_OVERFLOW, POOL_SIZE, engine

    pool = engine.pool
    assert pool.size() == POOL_SIZE
    assert pool._max_overflow == MAX_OVERFLOW
