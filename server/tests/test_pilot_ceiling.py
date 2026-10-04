"""FLE-92 item 2 — the global pilot dollar ceiling.

"A total dollar ceiling exists that no number of users can exceed, and you can show
the ceiling actually refusing a call in a test." That sentence is the closing
condition on the issue, and this file is the evidence for it.

The ceiling is independent of the per-user cap by construction, and these tests are
written to hold that apart from the cap rather than alongside it:

  - it refuses when the per-user cap has headroom (test_ceiling_refuses_*)
  - it refuses when the per-user cap is switched OFF entirely, which is the
    configuration prod has actually been running (test_ceiling_holds_with_cap_off)
  - one user's spend refuses a DIFFERENT user (test_ceiling_is_global_across_users)
  - it never bills calls the user did not receive (test_*_not_billed)
  - concurrent dispatches cannot all slip under it (test_concurrent_*)

Runs against a REAL Postgres — the ceiling is a SUM over governor_calls under an
advisory lock, and neither of those is meaningfully testable against a mock.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.governor import (
    PILOT_DOLLAR_CEILING,
    PilotBudgetExhaustedError,
    RESERVATION_USD,
    effective_pilot_ceiling,
    governed,
    pilot_spend,
    pilot_window_start,
)
from app.main import app

os.environ.pop("ANTHROPIC_API_KEY", None)


# ---------------------------------------------------------------------------
# Test DB helpers — mirrors test_governor.py
# ---------------------------------------------------------------------------

def _make_test_db_url() -> str:
    raw = os.environ.get(
        "DATABASE_URL", "postgresql://gt:devpass@localhost:5433/guitar_trainer"
    )
    for prefix in ("postgresql+asyncpg://",):
        if raw.startswith(prefix):
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
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()


async def _seed_user(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        text(
            "INSERT INTO users (id, preferences) VALUES (:id, '{}'::jsonb) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": user_id},
    )
    await db.commit()


async def _cleanup(db: AsyncSession, *user_ids: str) -> None:
    for user_id in user_ids:
        await db.execute(
            text("DELETE FROM governor_calls WHERE user_id = :uid"), {"uid": user_id}
        )
        await db.execute(
            text("DELETE FROM user_sessions WHERE user_id = :uid"), {"uid": user_id}
        )
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await db.commit()


async def _spend(
    db: AsyncSession,
    user_id: str,
    dollars: str,
    *,
    feature: str = "breakdown",
    error_code: str | None = None,
    abandoned: bool = False,
) -> None:
    """Write one governor_calls row that has already cost `dollars`.

    Defaults to a COMPLETED, delivered call: actuals stamped, dollars_actual set, no
    error_code. The two ways a row can be real-but-unbilled are opt-in, because they
    are the thing several of these tests are about:

      error_code   the call failed and was stamped (FLE-39's in-process arms)
      abandoned    actuals left NULL — a container death nothing could stamp
    """
    await db.execute(
        text(
            "INSERT INTO governor_calls "
            "  (id, user_id, feature, model, prompt_tokens_estimated, "
            "   prompt_tokens_actual, output_tokens_actual, "
            "   dollars_estimated, dollars_actual, error_code, created_at) "
            "VALUES (gen_random_uuid(), :uid, :feature, 'claude-sonnet-4-6', 1000, "
            "        :pt, :ot, :usd, :actual_usd, :ec, now())"
        ),
        {
            "uid": user_id,
            "feature": feature,
            "pt": None if abandoned else 1000,
            "ot": None if abandoned else 500,
            "usd": Decimal(dollars),
            "actual_usd": None if abandoned else Decimal(dollars),
            "ec": error_code,
        },
    )
    await db.commit()


def _bootstrap_body(user_id: str) -> dict:
    """POST /api/v1/users request body — mirrors tests/test_users_bootstrap_mocked.py."""
    return {
        "user_id": user_id,
        "songs": {
            "can_play": ["Sweet Home Chicago"],
            "working_on": ["Little Wing"],
            "aspirational": ["Eruption"],
        },
        "preferences": {"session_length_min": 30, "retention_format": "streak"},
        "raw_input": {
            "can_play": "Sweet Home Chicago",
            "working_on": "Little Wing",
            "aspirational": "Eruption",
        },
    }


# ---------------------------------------------------------------------------
# A governed function with no Anthropic dependency at all.
#
# The ceiling is enforced in _reserve_call_slot, strictly BEFORE the wrapped body
# runs, so proving it does not require a Sonnet mock — and not mocking one keeps
# these tests from passing for the wrong reason if breakdown.py changes shape.
# ---------------------------------------------------------------------------

_dispatches: dict[str, int] = {"n": 0}


@governed(feature="breakdown", cap=5)
async def _governed_probe(*, db: AsyncSession, user_id) -> str:
    _dispatches["n"] += 1
    return "dispatched"


@pytest.fixture(autouse=True)
def _reset_dispatch_count():
    _dispatches["n"] = 0
    yield


@pytest.fixture(autouse=True)
def _isolate_pilot_window(monkeypatch):
    """Pin FLETCHER_PILOT_START to the moment this test begins.

    pilot_spend is global on purpose — no user filter, no feature filter — so with
    the window unset it sums every governor_calls row in the dev database, including
    the ones every other test in the suite leaves mid-run. A ceiling of $10 against
    a shared database that already holds $18 of historical rows is not a test of
    anything.

    Pinning the window start makes each test's measured spend exactly the rows it
    created itself, and incidentally exercises the window-start path that the pilot
    will actually be configured with. The two tests that assert on
    pilot_window_start() override this with their own setenv, which is the behaviour
    being asserted there.
    """
    monkeypatch.setenv(
        "FLETCHER_PILOT_START", datetime.now(timezone.utc).isoformat()
    )
    yield


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def test_ceiling_defaults_to_twenty_five_dollars():
    """Hernan's number, FLE-88 card 2026-10-04. Not ours to drift."""
    assert PILOT_DOLLAR_CEILING == Decimal("25.00")
    assert effective_pilot_ceiling() == Decimal("25.00")


@pytest.mark.parametrize(
    "env_value,expected",
    [
        ("", Decimal("25.00")),       # unset → the default
        ("50", Decimal("50")),        # raised mid-pilot, which is the 80% alert's point
        ("12.50", Decimal("12.50")),  # fractional
        ("off", None),                # explicit kill switch
        ("none", None),
        ("unlimited", None),
        ("-1", None),
        ("banana", Decimal("25.00")),  # unparseable keeps the cap, never removes it
    ],
)
def test_ceiling_env_override(monkeypatch, env_value, expected):
    """FLETCHER_PILOT_CEILING_USD is read per call so it can be raised on a live deploy.

    The `banana` case is the one that matters: a typo in a cost control must fail
    toward spending LESS. Treating an unparseable value as "off" would silently
    uncap the pilot, which is the failure this whole slice exists to prevent.
    """
    if env_value:
        monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", env_value)
    else:
        monkeypatch.delenv("FLETCHER_PILOT_CEILING_USD", raising=False)
    assert effective_pilot_ceiling() == expected


def test_pilot_window_start_parses_a_bare_date(monkeypatch):
    """FLETCHER_PILOT_START is set by hand in a Railway env var, so "2026-10-06" must work."""
    monkeypatch.setenv("FLETCHER_PILOT_START", "2026-10-06")
    start = pilot_window_start()
    assert start is not None
    assert (start.year, start.month, start.day) == (2026, 10, 6)
    assert start.tzinfo is not None, "a naive value must be read as UTC, not left naive"


def test_pilot_window_start_unparseable_counts_everything(monkeypatch):
    """A bad window start must widen the measured window, not narrow it.

    Falling back to None counts every row, so the ceiling trips EARLIER than
    intended. The opposite fallback would silently stop counting spend.
    """
    monkeypatch.setenv("FLETCHER_PILOT_START", "last tuesday")
    assert pilot_window_start() is None


# ---------------------------------------------------------------------------
# What the ceiling counts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spend_sums_actuals_across_users_and_features():
    """pilot_spend is global: no user filter, no feature filter. That is the design."""
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())

    async with _make_session() as db:
        await _seed_user(db, user_a)
        await _seed_user(db, user_b)
        before = await pilot_spend(db)
        await _spend(db, user_a, "1.00")
        await _spend(db, user_b, "2.50")
        await _spend(db, user_b, "0.25", feature="onboarding")
        after = await pilot_spend(db)

    assert after - before == Decimal("3.75"), (
        "Spend must sum every user and every governed feature — onboarding re-runs "
        "draw down the same pot as breakdowns"
    )

    async with _make_session() as db:
        await _cleanup(db, user_a, user_b)


@pytest.mark.asyncio
async def test_failed_calls_are_not_billed():
    """FLE-92 item 3, the half that depended on FLE-39 landing first.

    A ceiling that bills failed calls hits $25 early and for the wrong reason — a
    run of Anthropic timeouts would close the pilot over breakdowns nobody received.
    """
    user_id = str(uuid.uuid4())

    async with _make_session() as db:
        await _seed_user(db, user_id)
        before = await pilot_spend(db)
        await _spend(db, user_id, "5.00", error_code="APITimeoutError")
        await _spend(db, user_id, "5.00", error_code="CancelledError")
        after = await pilot_spend(db)

    assert after == before, (
        f"Errored rows must not bill against the ceiling. before=${before} "
        f"after=${after}"
    )

    async with _make_session() as db:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_abandoned_calls_are_not_billed_once_stale():
    """A container death stamps nothing, so the ceiling has to heal the same way the cap does."""
    user_id = str(uuid.uuid4())

    async with _make_session() as db:
        await _seed_user(db, user_id)
        before = await pilot_spend(db)
        await _spend(db, user_id, "9.00", abandoned=True)
        # Fresh, so still plausibly in flight → MUST still bill, or concurrent
        # dispatches overshoot the ceiling.
        inflight = await pilot_spend(db)
        assert inflight > before, (
            "A call still in flight must bill against the ceiling — otherwise the "
            "ceiling is blind to exactly the calls it is trying to gate"
        )

        await db.execute(
            text(
                "UPDATE governor_calls SET created_at = now() - interval '2 hours' "
                "WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        await db.commit()
        stale = await pilot_spend(db)

    assert stale == before, (
        f"An unstamped row older than any call could take was abandoned mid-dispatch "
        f"and must stop billing. before=${before} stale=${stale}"
    )

    async with _make_session() as db:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_inflight_row_bills_at_the_reservation_before_pricing_lands():
    """The INSERT itself writes RESERVATION_USD into dollars_estimated.

    This is the whole mechanism behind "no number of users can exceed the ceiling".
    Between the INSERT and record_estimate a row has no priced figure, and without a
    reservation it would contribute $0 for that window — so N simultaneous callers
    could each read the same under-ceiling total and all pass.
    """
    user_id = str(uuid.uuid4())

    async with _make_session() as db:
        await _seed_user(db, user_id)

    async with _make_session() as db:
        await _governed_probe(db=db, user_id=uuid.UUID(user_id))

    async with _make_session() as db:
        reserved = await db.scalar(
            text(
                "SELECT dollars_estimated FROM governor_calls WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )

    assert reserved == RESERVATION_USD, (
        f"The audit row must bill from the instant it exists; expected "
        f"${RESERVATION_USD}, got {reserved!r}"
    )

    async with _make_session() as db:
        await _cleanup(db, user_id)


# ---------------------------------------------------------------------------
# The ceiling refusing a call — the issue's closing condition
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ceiling_refuses_a_governed_call(monkeypatch):
    """THE test the issue asks for: the ceiling refusing a dispatch.

    The per-user cap has four of its five units free, so the only thing that can
    refuse here is the ceiling.
    """
    user_id = str(uuid.uuid4())
    monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", "1.00")

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _spend(db, user_id, "1.00")

    async with _make_session() as db:
        with pytest.raises(PilotBudgetExhaustedError) as exc_info:
            await _governed_probe(db=db, user_id=uuid.UUID(user_id))

    assert exc_info.value.ceiling == Decimal("1.00")
    assert exc_info.value.spent >= Decimal("1.00")
    assert _dispatches["n"] == 0, "A refused call must never reach the wrapped body"

    # And it wrote no audit row, so a refusal costs the user nothing against their cap.
    async with _make_session() as db:
        rows = await db.scalar(
            text("SELECT COUNT(*) FROM governor_calls WHERE user_id = :uid"),
            {"uid": user_id},
        )
    assert rows == 1, (
        f"A ceiling refusal must not insert a governor_calls row — expected only "
        f"the seeded row, found {rows}"
    )

    async with _make_session() as db:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_ceiling_holds_with_the_per_user_cap_switched_off(monkeypatch):
    """FLETCHER_CAP_BREAKDOWN=off must not disable the ceiling.

    Not hypothetical: `off` is what prod has been running since FLE-58, and the
    ceiling being independent of the per-user cap is the reason the issue says to
    build item 2 "either way". If this test ever fails, the pilot is uncapped.
    """
    user_id = str(uuid.uuid4())
    monkeypatch.setenv("FLETCHER_CAP_BREAKDOWN", "off")
    monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", "1.00")

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _spend(db, user_id, "1.50")

    async with _make_session() as db:
        with pytest.raises(PilotBudgetExhaustedError):
            await _governed_probe(db=db, user_id=uuid.UUID(user_id))

    assert _dispatches["n"] == 0

    async with _make_session() as db:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_ceiling_is_global_across_users(monkeypatch):
    """One user's spend refuses a different user. That is what "global" means."""
    spender, newcomer = str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", "1.00")

    async with _make_session() as db:
        await _seed_user(db, spender)
        await _seed_user(db, newcomer)
        await _spend(db, spender, "1.20")

    async with _make_session() as db:
        with pytest.raises(PilotBudgetExhaustedError):
            # A user with zero calls of their own and a full daily allowance.
            await _governed_probe(db=db, user_id=uuid.UUID(newcomer))

    assert _dispatches["n"] == 0

    async with _make_session() as db:
        await _cleanup(db, spender, newcomer)


@pytest.mark.asyncio
async def test_under_the_ceiling_the_call_goes_through(monkeypatch):
    """The complement, so the tests above cannot pass by refusing everything."""
    user_id = str(uuid.uuid4())
    monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", "1000000")

    async with _make_session() as db:
        await _seed_user(db, user_id)

    async with _make_session() as db:
        result = await _governed_probe(db=db, user_id=uuid.UUID(user_id))

    assert result == "dispatched"
    assert _dispatches["n"] == 1

    async with _make_session() as db:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_ceiling_off_does_not_refuse(monkeypatch):
    """FLETCHER_PILOT_CEILING_USD=off is a real kill switch, for raising it in an emergency."""
    user_id = str(uuid.uuid4())
    monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", "off")

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _spend(db, user_id, "999.00")

    async with _make_session() as db:
        result = await _governed_probe(db=db, user_id=uuid.UUID(user_id))

    assert result == "dispatched"

    async with _make_session() as db:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_concurrent_dispatches_cannot_all_slip_under_the_ceiling(monkeypatch):
    """"No number of users can exceed the ceiling" — under genuine concurrency.

    FLE-23 §6 found the per-user cap was advisory because COUNT and INSERT had
    nothing between them. The ceiling has the identical hazard with a wider blast
    radius, so it takes a global advisory lock around its own check+INSERT and the
    INSERT reserves dollars immediately.

    Ten callers, each a DIFFERENT user so no per-user cap is in play, against a
    ceiling with room for exactly two reservations. If the lock or the reservation is
    missing, most of the ten get through.

    The ceiling is set RELATIVE to whatever the dev DB already holds: pilot_spend has
    no user filter and no window by default, so it legitimately sees rows left by
    other tests in this database. An absolute ceiling here would be a flaky test
    rather than a strict one.
    """
    user_ids = [str(uuid.uuid4()) for _ in range(10)]
    async with _make_session() as db:
        for uid in user_ids:
            await _seed_user(db, uid)
        baseline = await pilot_spend(db)

    room_for = 2
    ceiling = baseline + RESERVATION_USD * room_for
    monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", str(ceiling))

    async def _attempt(uid: str) -> bool:
        async with _make_session() as db:
            try:
                await _governed_probe(db=db, user_id=uuid.UUID(uid))
                return True
            except PilotBudgetExhaustedError:
                return False

    results = await asyncio.gather(*(_attempt(uid) for uid in user_ids))
    admitted = sum(results)

    assert admitted == room_for, (
        f"A ceiling with room for exactly {room_for} reservations admitted "
        f"{admitted} of 10 concurrent callers. The global advisory lock in "
        f"_reserve_call_slot and the reservation written at INSERT are what bound "
        f"this; check both."
    )

    async with _make_session() as db:
        total = await pilot_spend(db)

    # The headroom check in _check_pilot_ceiling refuses the call that WOULD cross,
    # so concurrency cannot push measured spend past the ceiling at all. (A single
    # call whose actual cost lands above its reservation still can — that bound is
    # one call's overrun and is documented on _check_pilot_ceiling, not testable
    # here where the probe never prices anything.)
    assert total <= ceiling, (
        f"Measured spend ${total} exceeded the ${ceiling} ceiling — the reservation "
        f"or the global lock is not holding"
    )

    async with _make_session() as db:
        await _cleanup(db, *user_ids)


# ---------------------------------------------------------------------------
# The 80% alert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eighty_percent_alert_logs_a_warning(monkeypatch, caplog):
    """Hernan asked to hear about $20 of $25 before a tester hits $25.

    The channel is deliberately a log line plus scripts/pilot_spend.py — he ruled out
    building notification infrastructure for a one-shot pilot threshold. What this
    pins is that the line is emitted at all, carries the grep marker, and does NOT
    refuse the call: an alert that also blocks is just a lower ceiling.
    """
    from app.ai.governor import PILOT_CEILING_ALERT_MARKER

    user_id = str(uuid.uuid4())
    monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", "10.00")

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _spend(db, user_id, "8.50")   # 85% — over the 80% threshold, under 100%

    with caplog.at_level(logging.WARNING, logger="app.ai.governor"):
        async with _make_session() as db:
            result = await _governed_probe(db=db, user_id=uuid.UUID(user_id))

    assert result == "dispatched", "The 80% alert warns; it must not refuse"
    assert any(
        PILOT_CEILING_ALERT_MARKER in r.getMessage() for r in caplog.records
    ), (
        f"No {PILOT_CEILING_ALERT_MARKER} line was logged at 85% of the ceiling. "
        f"Captured: {[r.getMessage() for r in caplog.records]}"
    )

    async with _make_session() as db:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_no_alert_below_the_threshold(monkeypatch, caplog):
    """So the alert means something when it does fire."""
    from app.ai.governor import PILOT_CEILING_ALERT_MARKER

    user_id = str(uuid.uuid4())
    monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", "1000.00")

    async with _make_session() as db:
        await _seed_user(db, user_id)

    with caplog.at_level(logging.WARNING, logger="app.ai.governor"):
        async with _make_session() as db:
            await _governed_probe(db=db, user_id=uuid.UUID(user_id))

    assert not any(
        PILOT_CEILING_ALERT_MARKER in r.getMessage() for r in caplog.records
    ), "A ceiling nowhere near spent must stay quiet"

    async with _make_session() as db:
        await _cleanup(db, user_id)


# ---------------------------------------------------------------------------
# The endpoint surface
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_endpoint_returns_429_pilot_budget_spent(monkeypatch):
    """The user-visible half: a distinct code and copy that does not blame the user.

    Separate from BREAKDOWN_CAPPED on purpose. The cap clears at midnight; the
    ceiling does not clear at all without the ceiling being raised, so reusing the
    cap's message would tell a user to come back tomorrow for a refusal that will
    still be there tomorrow.
    """
    from app.ai.breakdown import run_technique_breakdown  # noqa: F401  (import side effects)

    user_id = str(uuid.uuid4())
    monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", "1.00")

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _spend(db, user_id, "1.00")
        # An uncached song, so the request takes the dispatch path the ceiling gates.
        # songs.breakdown is NOT NULL, so the empty object stands in for "no snapshot
        # yet"; breakdown_generated_at left NULL is what makes it a cache MISS.
        song_id = await db.scalar(
            text(
                "INSERT INTO songs (title, artist, genre, difficulty, bpm, key, "
                "                   breakdown, user_id, category) "
                "VALUES ('Ceiling Probe', 'Nobody', 'Rock', 'Intermediate', 100, 'A', "
                "        '{}'::jsonb, :uid, 'working_on') RETURNING id"
            ),
            {"uid": user_id},
        )
        await db.commit()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "180"},
            )

        assert resp.status_code == 429, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "PILOT_BUDGET_SPENT", detail
        message = detail["message"]

        # The copy contract. The user did nothing wrong and must be told so.
        assert "Not you" in message, message
        assert "nothing's broken" in message, message
        # And it must not promise a reset that is not coming.
        assert "midnight" not in message, message
        assert "today" not in message, message
        assert detail["resets_at"] is None, (
            "There is no reset instant for a spent budget; sending one would have "
            "the client render a countdown to nothing"
        )
    finally:
        async with _make_session() as db:
            await db.execute(
                text("DELETE FROM songs WHERE user_id = :uid"), {"uid": user_id}
            )
            await db.commit()
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_cached_views_are_not_refused_by_the_ceiling(monkeypatch):
    """A free cache hit must still serve when the budget is spent.

    The ceiling gates SPEND. Refusing a cached breakdown costs the pilot nothing and
    takes away work the user already paid for out of their daily five — and it is
    what would make the PILOT_BUDGET_SPENT copy's "everything you've already pulled
    apart still opens" a lie.
    """
    from app.models.song import Breakdown

    user_id = str(uuid.uuid4())
    monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", "1.00")

    canned = Breakdown(
        tab={"measures": [], "tuning": ["E", "A", "D", "G", "B", "e"]},
        chords=[],
        technique_notes=[
            {"heading": "Keep the wrist loose", "body": "Anchor the thumb behind the neck."}
        ],
    )

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _spend(db, user_id, "5.00")
        song_id = await db.scalar(
            text(
                "INSERT INTO songs (title, artist, genre, difficulty, bpm, key, "
                "                   breakdown, breakdown_generated_at, user_id, category) "
                "VALUES ('Cached Probe', 'Nobody', 'Rock', 'Intermediate', 100, 'A', "
                "        CAST(:bd AS jsonb), now(), :uid, 'working_on') RETURNING id"
            ),
            {"uid": user_id, "bd": canned.model_dump_json()},
        )
        await db.commit()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "180"},
            )

        assert resp.status_code == 200, (
            f"A cached breakdown costs nothing to serve and must not be refused by "
            f"the dollar ceiling. Got {resp.status_code}: {resp.text}"
        )
        assert resp.json()["breakdown"]["technique_notes"], resp.text
    finally:
        async with _make_session() as db:
            await db.execute(
                text("DELETE FROM songs WHERE user_id = :uid"), {"uid": user_id}
            )
            await db.commit()
            await _cleanup(db, user_id)


# ---------------------------------------------------------------------------
# The onboarding surfaces — the ceiling reaches every @governed feature, so
# every call site that only knew about BudgetExceededError had to learn this one
# or 500. These pin the three choices made there, because they are NOT uniform.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fresh_bootstrap_fails_open_at_the_ceiling(monkeypatch):
    """A new participant's FIRST launch must still produce a working app.

    POST /users/bootstrap fails OPEN to the 6-root fallback rather than 429ing. A
    user who installed the app an hour after the budget ran out has done nothing
    wrong, and a 429 would strand them at the wizard with no graph and no way
    forward. The fallback is a local write, so the ceiling is still honoured.
    """
    user_id = str(uuid.uuid4())
    monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", "1.00")

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _spend(db, user_id, "1.00", feature="onboarding")

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/users",
                json=_bootstrap_body(user_id),
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "180"},
            )

        assert resp.status_code in (200, 201), (
            f"A spent budget must not strand a new user at the wizard. Got "
            f"{resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["mode"] == "bootstrap", (
            f"The ceiling must route to the 6-root fallback, not the Sonnet path: {body['mode']}"
        )
        assert len(body["nodes"]) == 6, (
            f"The 6-root fallback must still hand the user a graph, got "
            f"{len(body['nodes'])} nodes"
        )
    finally:
        async with _make_session() as db:
            await db.execute(
                text(
                    "DELETE FROM song_skills WHERE skill_node_id IN "
                    "  (SELECT id FROM skill_nodes WHERE user_id = :uid)"
                ),
                {"uid": user_id},
            )
            await db.execute(
                text("DELETE FROM skill_node_proposals WHERE user_id = :uid"),
                {"uid": user_id},
            )
            await db.execute(
                text("DELETE FROM songs WHERE user_id = :uid"), {"uid": user_id}
            )
            await db.execute(
                text("DELETE FROM skill_nodes WHERE user_id = :uid"), {"uid": user_id}
            )
            await db.commit()
            await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_onboarding_rerun_refuses_and_keeps_the_graph(monkeypatch):
    """POST /users/{id}/re-run REFUSES at the ceiling — the opposite of bootstrap.

    Deliberately asymmetric with the test above, and the asymmetry is the point. A
    re-run is a user with a WORKING graph asking to rebuild it. Failing open would
    replace a real graph with the generic 6-root one over a budget condition, which
    is strictly worse than declining. Declining leaves them exactly as they were.

    resets_at must be null: this clears when the ceiling is raised, not on a clock.
    """
    user_id = str(uuid.uuid4())
    monkeypatch.setenv("FLETCHER_PILOT_CEILING_USD", "1.00")

    async with _make_session() as db:
        await _seed_user(db, user_id)
        await _spend(db, user_id, "2.00", feature="onboarding")
        await db.execute(
            text(
                "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
                "VALUES (gen_random_uuid(), :uid, 'Blues Rhythm', 'leaf', 0.4)"
            ),
            {"uid": user_id},
        )
        await db.execute(
            text("UPDATE users SET onboarded_at = now() WHERE id = :uid"),
            {"uid": user_id},
        )
        await db.commit()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/v1/users/{user_id}/re-run",
                json=_bootstrap_body(user_id),
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "180"},
            )

        assert resp.status_code == 429, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "PILOT_BUDGET_SPENT", detail
        assert detail["resets_at"] is None, detail
        assert "has not been changed" in detail["message"], detail["message"]

        # And the graph really is untouched — the whole reason to refuse here.
        async with _make_session() as db:
            remaining = await db.scalar(
                text("SELECT COUNT(*) FROM skill_nodes WHERE user_id = :uid"),
                {"uid": user_id},
            )
        assert remaining == 1, (
            f"Refusing before the wipe must leave the existing graph intact, found "
            f"{remaining} skill_nodes"
        )
    finally:
        async with _make_session() as db:
            await db.execute(
                text("DELETE FROM skill_nodes WHERE user_id = :uid"), {"uid": user_id}
            )
            await db.commit()
            await _cleanup(db, user_id)
