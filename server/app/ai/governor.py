"""Cost governor — @governed decorator that wraps every Sonnet call.

Sits adjacent to client.py (not inside it). Do not instantiate a second
AsyncAnthropic — always call get_client() from app.ai.client.
Phase 4 interception surface: pre-cap-check → INSERT governor_calls row →
set ContextVar call_id → await wrapped fn (which calls count_tokens +
record_estimate pre-dispatch) → UPDATE row with actuals on success /
error_code on failure.

D-04: decorator signature @governed(feature, cap, window).
D-03: two-step count_tokens protocol: wrapped fn calls record_estimate
      between count_tokens and client.messages.create().
D-01: SUPERSEDED by FLE-92 — the window is the user's local calendar DAY, not a
      rolling 7 days. See LOCAL_DAY_WINDOW_SQL.
D-02: BudgetExceededError carries feature + resets_at (ISO).
D-08: AnthropicQuotaExceededError surfaces org-level 429 distinctly.

FLE-92 adds the second, independent limit: a GLOBAL dollar ceiling across every
user and every feature (PilotBudgetExhaustedError). The per-user cap bounds one
person's day; the ceiling bounds the pilot's total exposure and is the one that
actually binds — see PILOT_DOLLAR_CEILING for the arithmetic.
"""
import asyncio
import logging
import os
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps

from anthropic import APIError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import SONNET_MODEL

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pricing (FLE-23 §4)
# ---------------------------------------------------------------------------
# governor_calls.dollars_estimated / dollars_actual were created in migration
# 0004 and nothing ever wrote them: the governor counted calls and never
# dollars, so the per-user spend audit trail was empty. These rates turn the
# token columns the governor already records into the dollar columns it was
# always supposed to fill.
#
# USD per MILLION tokens, per the Anthropic published list price. Keyed by the
# model string stored on the governor_calls row rather than by feature, so a
# row written under one model is never repriced at another model's rate when
# the default changes.
#
# dollars_* are NUMERIC(10,6) — six decimal places, i.e. microdollars. A single
# breakdown call costs roughly $0.03, so the column has ample precision; we
# round half-even at six places on the way in so the stored value and the
# computed value never disagree.
_PRICE_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    # model: (input $/Mtok, output $/Mtok)
    "claude-sonnet-4-6": (Decimal("3.00"), Decimal("15.00")),
}

_MTOK = Decimal("1000000")
_DOLLAR_QUANTUM = Decimal("0.000001")   # NUMERIC(10,6)


def price_dollars(
    model: str,
    prompt_tokens: int | None,
    output_tokens: int | None,
) -> Decimal | None:
    """Cost in USD of `prompt_tokens` in + `output_tokens` out on `model`.

    Returns None — leaving the dollar column NULL — for a model with no entry in
    _PRICE_PER_MTOK. That is deliberate: a wrong number in a cost audit trail is
    worse than a missing one, because a missing one is visibly missing. Logs at
    warning so an unpriced model shows up in the logs rather than only in a
    column of NULLs nobody reads.

    A NULL token count is treated as zero rather than poisoning the whole
    result: on a truncated or partially-recorded call, the input cost is still
    real money spent and still worth attributing.
    """
    rates = _PRICE_PER_MTOK.get(model)
    if rates is None:
        logger.warning(
            "No price entry for model %r; leaving governor_calls dollars NULL. "
            "Add it to _PRICE_PER_MTOK in app/ai/governor.py.",
            model,
        )
        return None

    in_rate, out_rate = rates
    prompt = Decimal(prompt_tokens or 0)
    output = Decimal(output_tokens or 0)
    total = (prompt / _MTOK) * in_rate + (output / _MTOK) * out_rate
    return total.quantize(_DOLLAR_QUANTUM)


async def _call_model(db: AsyncSession, call_id: uuid.UUID) -> str | None:
    """Read back the model recorded on a governor_calls row (PK lookup).

    Pricing keys off the row's own model rather than the current SONNET_MODEL
    constant, so historical rows keep being priced at the model they actually
    ran on after the default moves.
    """
    return await db.scalar(
        text("SELECT model FROM governor_calls WHERE id = :id"),
        {"id": str(call_id)},
    )


# ---------------------------------------------------------------------------
# ContextVar: passes call_id from the decorator wrapper to the wrapped fn.
# Python's contextvars copy-on-spawn semantics are safe here because we
# run single-worker (uvicorn --workers 1) and each async task inherits a
# copy of the context at creation time — which means each concurrent
# coroutine has its own copy of _current_call_id without cross-task leakage.
# ---------------------------------------------------------------------------
_current_call_id: ContextVar[uuid.UUID | None] = ContextVar(
    "governor_call_id", default=None
)


def current_call_id() -> uuid.UUID | None:
    """Return the governor call_id set by the @governed decorator for this coroutine."""
    return _current_call_id.get()


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------

class BudgetExceededError(Exception):
    """Raised by @governed when the per-user DAILY cap is hit.

    feature='breakdown': cap=5 per user per local calendar day (FLE-92).
    Caught by the breakdown endpoint to return HTTP 429 with BREAKDOWN_CAPPED body.

    resets_at is the user's next local midnight, in UTC — an absolute instant, not
    "oldest call + window". Under the old rolling window resets_at moved every time
    the oldest counted call aged out, so the copy had to say "come back in N days"
    and N drifted while the user watched. A fixed daily boundary is a time the copy
    can simply name.
    """
    def __init__(self, feature: str, resets_at: str) -> None:
        self.feature = feature
        self.resets_at = resets_at
        super().__init__(
            f"Budget exceeded for feature={feature}. Resets at {resets_at}."
        )


class PilotBudgetExhaustedError(Exception):
    """Raised by @governed when the GLOBAL pilot dollar ceiling is reached (FLE-92).

    Distinct from BudgetExceededError in both cause and remedy, which is why it is a
    distinct class and a distinct wire code:

      BudgetExceededError      this user has had their allowance today; midnight fixes it.
      PilotBudgetExhaustedError  the pilot's money is gone; only Hernan raising the
                                 ceiling fixes it, and no amount of waiting will.

    Collapsing the two would tell a user to come back tomorrow for something that
    will still be refused tomorrow.
    """
    def __init__(self, spent: Decimal, ceiling: Decimal) -> None:
        self.spent = spent
        self.ceiling = ceiling
        super().__init__(
            f"Pilot dollar ceiling reached: ${spent} spent of ${ceiling}."
        )


class AnthropicQuotaExceededError(Exception):
    """Raised when Anthropic returns a 429 / quota-exceeded error (D-08).

    Surfaces org-level cost cap distinctly from per-user BudgetExceededError.
    Caught by endpoints to return HTTP 503 FLETCHER_OUT (D-08).
    """
    def __init__(self, retry_after_hint: str = "1h") -> None:
        self.retry_after_hint = retry_after_hint
        super().__init__(f"Anthropic quota exceeded. Retry after {retry_after_hint}.")


# ---------------------------------------------------------------------------
# Runtime cap override
# ---------------------------------------------------------------------------

# FLE-92 — 5 per user per LOCAL CALENDAR DAY. Hernan's decision on the FLE-88 card,
# 2026-10-04 13:29; it is a spec, not a recommendation, and the numbers are not ours
# to re-pick (see the module note on PILOT_DOLLAR_CEILING if the arithmetic ever
# argues otherwise).
#
# It replaces 3-per-rolling-7-days, which worked exactly as designed and locked the
# only user of the app out of the thing the app is for (FLE-58). Two things were
# wrong with it and both are fixed by the shape, not the number:
#
#   - A 7-day rolling window means the wall you hit on Tuesday is still there on
#     Thursday. A day boundary means the worst case is "tomorrow", always.
#   - A rolling window has no nameable reset. resets_at moved as calls aged out, so
#     the copy could only say "in about N days" and be wrong by a day either way.
#
# Do not reintroduce a multi-day window.
BREAKDOWN_CAP = 5

# FLE-23 §3 — onboarding used to be @governed(feature='onboarding', cap=None): an
# uncapped, unthrottled spend loop reachable from Settings via
# POST /users/{id}/re-run. Each tap costs $0.05–$0.19 (1 onboarding call + up to
# 10 verifier calls) and nothing in the stack limited how fast you could tap it.
# It was the worst cost-control hole in the system.
#
# 5 per rolling 7-day window. The shape of the number: a pilot participant needs
# the first one at signup and realistically one or two corrections after seeing
# what the graph produced. Five leaves room for a genuinely confused user to
# iterate and still bounds a runaway tap at ~$0.95/week instead of unbounded.
# Above the cap, re-run returns 429 BREAKDOWN_CAPPED-style rather than spending.
#
# Overridable at deploy time as FLETCHER_CAP_ONBOARDING (see effective_cap) so
# support can lift it for one pilot participant without a code change — and can
# switch it off entirely if the cap turns out to lock a real user out, which is
# exactly what happened to the breakdown cap in FLE-58.
ONBOARDING_CAP = 5

_CAP_OFF_VALUES = {"off", "none", "unlimited", "-1"}


def effective_cap(feature: str, default_cap: int | None) -> int | None:
    """Resolve the cap for `feature`, honouring the FLETCHER_CAP_<FEATURE> env override.

    Read per call rather than at import, so the cap is a deploy-time env setting
    and not a code change. `off`/`none`/`unlimited`/`-1` remove the cap entirely;
    any other integer replaces it. An unparseable value logs and keeps default_cap
    — a typo must not silently uncap a paid feature.
    """
    raw = os.environ.get(f"FLETCHER_CAP_{feature.upper()}", "").strip()
    if not raw:
        return default_cap
    if raw.lower() in _CAP_OFF_VALUES:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "Ignoring unparseable FLETCHER_CAP_%s=%r; keeping cap=%s",
            feature.upper(), raw, default_cap,
        )
        return default_cap
    return None if parsed < 0 else parsed


# ---------------------------------------------------------------------------
# What counts against a cap (FLE-18 + FLE-39)
# ---------------------------------------------------------------------------
# FLE-39 — `error_code IS NULL` alone is not enough. error_code is only ever
# stamped from a handler inside the @governed wrapper, so any termination that
# never reaches a handler leaves the pre-dispatch audit row looking exactly like
# a successful call and the user silently loses a breakdown they never received.
# Two such paths, observed in prod on 2026-09-16:
#
#   1. Task cancellation (client disconnect). On Python 3.13 CancelledError
#      subclasses BaseException, so `except Exception` never fires. Now handled
#      in-process by the `except BaseException` arm of the wrapper below.
#   2. Process death (deploy SIGTERM, OOM). NO in-process handler can ever
#      catch this — which is why the cap predicate itself has to heal.
#
# So a row counts when it either COMPLETED (at least one actual token column is
# stamped) or is still plausibly IN FLIGHT (younger than the grace window). A row
# with no actuals that is older than any call could possibly take was abandoned
# mid-dispatch and must not be charged to the user.
#
# Keeping in-flight rows counted is load-bearing: _reserve_call_slot's advisory
# lock relies on a just-INSERTed, not-yet-dispatched row counting, or concurrent
# taps race past the cap again (FLE-23 §6).
#
# FLE-37 measured a 87.4s worst-case cache-miss breakdown. Ten minutes is ~7x
# that headroom — long enough that no live call is ever written off, short enough
# that a user whose container died gets their unit back within one app refresh.
STALE_CALL_GRACE = "10 minutes"

# ONE predicate, shared by _check_cap and by song_of_day._breakdown_quota's two
# chip queries. FLE-37 confirmed the cap and the chip agreed exactly; importing
# the same string is what keeps that true, instead of four hand-copied WHERE
# clauses that agree until someone edits one of them.
#
# Assumes every terminal state stamps at least one actual token column. The
# cache-hit row in breakdowns.py writes explicit zeros for precisely this reason:
# a cached view costs nothing but it is DONE, not in flight, and it must keep
# counting against the cap forever.
COUNTS_AGAINST_CAP_SQL = (
    "error_code IS NULL "
    "AND (prompt_tokens_actual IS NOT NULL "
    "OR output_tokens_actual IS NOT NULL "
    f"OR created_at > now() - interval '{STALE_CALL_GRACE}')"
)


# ---------------------------------------------------------------------------
# Which window a feature's cap is measured over (FLE-92)
# ---------------------------------------------------------------------------
# FLE-92 moved the BREAKDOWN cap to the user's local calendar day. It deliberately
# did NOT move onboarding: ONBOARDING_CAP=5 was sized as 5 per WEEK against a
# $0.05–$0.19 re-run loop (FLE-23 §3), and reinterpreting the same 5 as per-day
# would have loosened an unrelated cost control sevenfold as a side effect of a
# breakdown copy fix. So the window is a property of the feature, not of the file.
#
# `governed(..., window=...)` took a window argument from the start and never used
# it; rather than make a second source of truth, the decorator now reads this map
# too — breakdowns.py and song_of_day.py call _check_cap directly and must resolve
# the same window the decorator does.
WINDOW_LOCAL_DAY = "local_day"
WINDOW_ROLLING_7D = "rolling_7d"

FEATURE_WINDOWS: dict[str, str] = {
    "breakdown": WINDOW_LOCAL_DAY,
    "onboarding": WINDOW_ROLLING_7D,
}

# An unregistered feature gets the rolling week: the conservative choice, because a
# window that is too long refuses calls that would have been allowed (annoying and
# visible) while one that is too short permits spend nobody budgeted (expensive and
# invisible).
DEFAULT_WINDOW = WINDOW_ROLLING_7D


async def _user_tz_offset_minutes(db: AsyncSession, user_id: uuid.UUID) -> int:
    """The user's UTC offset in minutes, as last reported by their device.

    There is no tz column on `users` or on `governor_calls`. The device's offset
    arrives on every request as the X-TZ-Offset header and is persisted on
    user_sessions rows (tz_offset_minutes), so the most recent session row is the
    freshest offset the server holds. Hernan's device account reads +180.

    Reading it from the DB rather than from the request header is deliberate: the
    governed call happens deep inside run_technique_breakdown with no request in
    scope, while the quota chip and the endpoint pre-check DO have a header. Three
    call sites resolving tz three ways would put the chip and the cap on different
    day boundaries for hours at a time — the exact chip-says-2-left-but-cap-refuses
    failure FLE-39 restructured the predicate to prevent. One source, one boundary.

    Falls back to 0 (UTC) for a user with no session rows at all. In practice the
    home screen's GET /song-of-day writes one before any breakdown is reachable, so
    this is the first-request-ever case; the cost of being wrong is a day boundary
    in the wrong place for one user until they load the home screen once.
    """
    # Ordered by rated_at, which is user_sessions' insert timestamp (server_default
    # now()) and is populated on every row including the reroll and daily-pick
    # markers — not just on rows that carry a rating. There is no created_at on this
    # table.
    offset = await db.scalar(
        text(
            "SELECT tz_offset_minutes FROM user_sessions "
            "WHERE user_id = :u ORDER BY rated_at DESC LIMIT 1"
        ),
        {"u": str(user_id)},
    )
    return int(offset) if offset is not None else 0


def _local_day_bounds_utc(
    tz_offset_minutes: int,
    now_utc: datetime,
) -> tuple[datetime, datetime]:
    """The UTC half-open interval [start, end) covering the user's current local day.

    `end` doubles as the window's reset instant — the user's next local midnight —
    which is why the caller never needs a separate resets_at query.

    Expressed as an explicit created_at range rather than as
    DATE(created_at AT TIME ZONE ...) = DATE(now() ...) so that
    ix_governor_calls_user_feature_created is still usable: a function over
    created_at is not sargable and would degrade the cap check to a per-user scan.
    """
    local_now = now_utc + timedelta(minutes=tz_offset_minutes)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = local_midnight - timedelta(minutes=tz_offset_minutes)
    return start_utc, start_utc + timedelta(days=1)


async def count_window_calls(
    db: AsyncSession,
    user_id: uuid.UUID,
    feature: str,
) -> tuple[int, datetime]:
    """Calls counted against `feature`'s cap right now, and when that count resets.

    Returns (count, resets_at_utc). THE single place either number is computed.

    FLE-39 made the cap and the quota chip share one WHERE clause so they could not
    drift. FLE-92 needed a second shared thing — the window itself — and a shared
    string was no longer enough, because the local-day branch needs a tz lookup and
    returns its reset instant for free while the rolling branch has to derive one
    from MIN(created_at). So the shared unit is now this function. _check_cap and
    song_of_day._breakdown_quota both call it; neither computes a window of its own.

    What counts is COUNTS_AGAINST_CAP_SQL: delivered or still plausibly in flight,
    never errored, never abandoned mid-dispatch.
    """
    window = FEATURE_WINDOWS.get(feature, DEFAULT_WINDOW)
    now_utc = datetime.now(timezone.utc)

    if window == WINDOW_LOCAL_DAY:
        tz_offset = await _user_tz_offset_minutes(db, user_id)
        start_utc, end_utc = _local_day_bounds_utc(tz_offset, now_utc)
        count = await db.scalar(
            text(
                "SELECT COUNT(*) FROM governor_calls "
                "WHERE user_id = :u AND feature = :f "
                "AND created_at >= :start AND created_at < :end "
                f"AND {COUNTS_AGAINST_CAP_SQL}"
            ),
            {"u": str(user_id), "f": feature, "start": start_utc, "end": end_utc},
        )
        return (count or 0), end_utc

    # WINDOW_ROLLING_7D — unchanged from D-01, still what onboarding is sized for.
    count = await db.scalar(
        text(
            "SELECT COUNT(*) FROM governor_calls "
            "WHERE user_id = :u AND feature = :f "
            "AND created_at > now() - interval '7 days' "
            f"AND {COUNTS_AGAINST_CAP_SQL}"
        ),
        {"u": str(user_id), "f": feature},
    )
    count = count or 0

    # A rolling window resets when its OLDEST counted call ages out, so resets_at is
    # a function of the rows rather than of the clock.
    min_created = await db.scalar(
        text(
            "SELECT MIN(created_at) FROM governor_calls "
            "WHERE user_id = :u AND feature = :f "
            "AND created_at > now() - interval '7 days' "
            f"AND {COUNTS_AGAINST_CAP_SQL}"
        ),
        {"u": str(user_id), "f": feature},
    )
    if min_created is None:
        # No counted rows: nothing to age out, so the furthest-out reset is a full
        # window from now. Keeps resets_at in the future for a fresh user.
        return count, now_utc + timedelta(days=7)
    if min_created.tzinfo is None:
        min_created = min_created.replace(tzinfo=timezone.utc)
    return count, min_created + timedelta(days=7)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _check_cap(
    db: AsyncSession,
    user_id: uuid.UUID,
    feature: str,
    cap: int,
) -> None:
    """Raise BudgetExceededError if `feature` is at or over `cap` for this user.

    FLE-92: the window is whatever FEATURE_WINDOWS says — the user's local calendar
    day for breakdowns, a rolling 7 days for onboarding. Both the count and the
    reset instant come from count_window_calls.

    FLE-18: failed calls are excluded from the cap. The audit row is INSERTed
    before dispatch and stamped with error_code only on failure (_update_error),
    so an in-flight call still counts — concurrent requests cannot race past the
    cap — while a call that errored does not. Without this, three transient
    Anthropic failures locked a user out of breakdowns for a full week despite
    never having received one.

    FLE-39: the exclusion is COUNTS_AGAINST_CAP_SQL, not a bare `error_code IS
    NULL`, so a row abandoned mid-dispatch by a disconnect or a container
    replacement also stops counting. See that constant for the reasoning.
    """
    count, resets_at_dt = await count_window_calls(db, user_id, feature)
    if count >= cap:
        raise BudgetExceededError(feature=feature, resets_at=resets_at_dt.isoformat())


# ---------------------------------------------------------------------------
# The global pilot dollar ceiling (FLE-92 item 2)
# ---------------------------------------------------------------------------
# $25 for the whole pilot window. Hernan's number, FLE-88 card, 2026-10-04.
#
# THE CEILING IS THE LIMIT THAT ACTUALLY BINDS. Worth stating plainly because it
# inverts which of the two limits is the "real" one:
#
#   5 breakdowns/user/day x ~$0.19 = ~$0.95/user/day
#   10 pilot users living in the app = ~$9.50/day
#   $25 / $9.50 = the ceiling is reached on day 2-3 of heavy use
#
# So the per-user cap is the one almost nobody meets, and the ceiling is the
# user-visible event. That is a deliberate trade — nobody hits a wall mid-sitting,
# the pilot just has a hard budget — but it means this is a product surface with
# copy and an alert, not an assertion that never fires. Treat it as such if you
# edit it.
#
# Ceiling and per-user cap are independent by construction: the ceiling holds even
# with FLETCHER_CAP_BREAKDOWN=off, and it covers every @governed feature, so
# onboarding re-runs draw down the same pot.
PILOT_DOLLAR_CEILING = Decimal("25.00")

# Alert at 80% ($20) so the ceiling can be raised mid-pilot deliberately rather
# than discovered from a tester's screenshot. The channel is intentionally just a
# log line plus scripts/pilot_spend.py — no notification infrastructure for a
# one-shot pilot threshold.
PILOT_CEILING_ALERT_FRACTION = Decimal("0.80")

# Log marker to grep/alert on. Kept as a constant so the string in the logs and the
# string in the runbook cannot drift.
PILOT_CEILING_ALERT_MARKER = "FLETCHER_PILOT_CEILING_ALERT"

# What an in-flight call is billed at between its INSERT and its record_estimate.
#
# This exists to close a concurrency hole, not to be accurate. The ceiling check
# and the INSERT are serialized under a global advisory lock, but the lock is
# released at COMMIT — before dispatch — so without a reservation a row contributes
# $0 to measured spend for the ~100ms until record_estimate runs, and N calls
# arriving together could each read the same under-ceiling total and all pass.
# Writing a non-zero floor into dollars_estimated inside the lock means every row
# bills from the instant it exists, which is what makes "no number of users can
# exceed the ceiling" true rather than approximately true.
#
# $0.20 ≈ one measured breakdown ($0.19). record_estimate overwrites it with the
# real pre-dispatch worst case moments later, and record_actuals with the actual, so
# the figure only governs a sub-second slice of each call's life.
RESERVATION_USD = Decimal("0.20")

_CEILING_OFF_VALUES = {"off", "none", "unlimited", "-1"}

# One namespace for the global lock. Every governed call across every user and
# feature contends on this one key.
_PILOT_CEILING_LOCK_KEY = "fletcher:pilot_ceiling"


def effective_pilot_ceiling() -> Decimal | None:
    """Resolve the global ceiling, honouring FLETCHER_PILOT_CEILING_USD.

    Read per call, not at import, so Hernan can raise the ceiling on the running
    deploy when the 80% alert fires — which is the entire point of alerting at 80%.
    `off`/`none`/`unlimited`/`-1` remove it; any other positive decimal replaces it.

    An unparseable value keeps the $25 default and logs. Same reasoning as
    effective_cap: a typo in a cost control must fail toward spending less.
    """
    raw = os.environ.get("FLETCHER_PILOT_CEILING_USD", "").strip()
    if not raw:
        return PILOT_DOLLAR_CEILING
    if raw.lower() in _CEILING_OFF_VALUES:
        return None
    try:
        parsed = Decimal(raw)
    except Exception:
        logger.warning(
            "Ignoring unparseable FLETCHER_PILOT_CEILING_USD=%r; keeping ceiling=$%s",
            raw, PILOT_DOLLAR_CEILING,
        )
        return PILOT_DOLLAR_CEILING
    return None if parsed < 0 else parsed


def pilot_window_start() -> datetime | None:
    """Start of the pilot accounting window from FLETCHER_PILOT_START (ISO 8601).

    Unset means count every row ever written, which is very nearly the same thing
    today: dollars_estimated/dollars_actual were NULL on every row until FLE-19
    landed, so historical dev and eval rows contribute nothing to the sum. That is
    a convenient accident and not a thing to rely on — set FLETCHER_PILOT_START
    when the pilot opens so the ceiling measures the pilot and not the backfill
    scripts.

    A naive timestamp is read as UTC rather than rejected: this value is set by hand
    in a Railway env var and `2026-10-06` should work.
    """
    raw = os.environ.get("FLETCHER_PILOT_START", "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        logger.warning(
            "Ignoring unparseable FLETCHER_PILOT_START=%r; counting all rows "
            "against the pilot ceiling.", raw,
        )
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def pilot_spend(db: AsyncSession) -> Decimal:
    """Total dollars committed against the pilot ceiling right now.

    Across ALL users and ALL features — the ceiling is global, so this query has no
    user_id filter and that is not an oversight.

    Two decisions encoded in the SQL:

    `COALESCE(dollars_actual, dollars_estimated)` — prefer what a finished call
    really cost; fall back to the pre-dispatch figure for a call still in flight (and
    for a successful call on a model missing from _PRICE_PER_MTOK, where
    price_dollars deliberately leaves dollars_actual NULL). A live call must not
    read as free, or the ceiling is blind to exactly the calls it is trying to gate.

    `COUNTS_AGAINST_CAP_SQL` — the same predicate the per-user cap uses, which is
    why FLE-39 had to land first. It excludes rows stamped with an error_code and
    rows abandoned mid-dispatch, so a run of Anthropic failures cannot walk the
    pilot into a $25 wall over breakdowns no user ever received. Hernan's item 3:
    count only what the user actually got.

    The honest cost of that choice: a call Anthropic billed us for but that failed
    on our side is real money this number does not see. The alternative — billing
    every attempt — hits the ceiling early and for the wrong reason, which is the
    failure mode that was explicitly ruled out.
    """
    start = pilot_window_start()
    window_clause = "created_at >= :start AND " if start is not None else ""
    params: dict[str, object] = {}
    if start is not None:
        params["start"] = start

    spent = await db.scalar(
        text(
            "SELECT COALESCE(SUM(COALESCE(dollars_actual, dollars_estimated)), 0) "
            "FROM governor_calls "
            f"WHERE {window_clause}{COUNTS_AGAINST_CAP_SQL}"
        ),
        params,
    )
    return Decimal(spent or 0)


async def _check_pilot_ceiling(db: AsyncSession, ceiling: Decimal) -> Decimal:
    """Raise PilotBudgetExhaustedError if the pilot cannot afford one more call.

    Refuses when `spent + RESERVATION_USD > ceiling` rather than when
    `spent >= ceiling`. The difference is one call's cost and it is the difference
    between two readings of "a ceiling no number of users can exceed":

      spent >= ceiling      stops dispatching once the money is gone, so the final
                            total lands one call ABOVE $25.
      spent + one > ceiling stops before dispatching the call that would cross, so
                            the total lands at or just under $25.

    The second is what the issue asks for, and the cost of it is that the last
    ~$0.20 of the budget goes unspent. Cheap.

    It cannot be made exact: a call's real cost is only knowable after it returns, so
    a breakdown that comes in above RESERVATION_USD can still carry the total past
    the ceiling. The bound is "overshoot of at most one call's cost overrun", not
    zero — which is why RESERVATION_USD is set above the measured $0.19 rather than
    at it.

    Returns the measured spend so the caller can log it. Emits the 80% alert on the
    way past — on every call above the threshold, not just the crossing one, because
    there is no durable "already alerted" state and a single log line that scrolled
    past during a deploy is not an alert.
    """
    spent = await pilot_spend(db)

    if spent + RESERVATION_USD > ceiling:
        logger.error(
            "%s CEILING REACHED: $%s spent of $%s. Governed calls are refusing. "
            "Raise FLETCHER_PILOT_CEILING_USD on the Railway fletcher service to "
            "resume.",
            PILOT_CEILING_ALERT_MARKER, spent, ceiling,
        )
        raise PilotBudgetExhaustedError(spent=spent, ceiling=ceiling)

    if spent >= ceiling * PILOT_CEILING_ALERT_FRACTION:
        logger.warning(
            "%s %.0f%% of the pilot ceiling is spent: $%s of $%s ($%s left, "
            "~%d breakdowns). Raise FLETCHER_PILOT_CEILING_USD before it hits.",
            PILOT_CEILING_ALERT_MARKER,
            float(spent / ceiling * 100),
            spent, ceiling, ceiling - spent,
            int((ceiling - spent) / RESERVATION_USD),
        )

    return spent


async def _insert_governor_call(
    db: AsyncSession,
    call_id: uuid.UUID,
    user_id: uuid.UUID,
    feature: str,
) -> None:
    """Insert an initial governor_calls row with prompt_tokens_estimated=NULL.

    Row exists before dispatch so failures still audit. The wrapped fn will
    call record_estimate() to populate prompt_tokens_estimated.

    FLE-92: dollars_estimated goes in as RESERVATION_USD, not NULL. The row must
    bill against the global ceiling from the moment it exists — see RESERVATION_USD
    for why NULL here lets concurrent calls overshoot. record_estimate overwrites it
    with the real worst case before dispatch.

    Does NOT commit — the caller owns the transaction boundary so the cap check
    and this insert can be one atomic unit (see _reserve_call_slot).
    """
    await db.execute(
        text(
            "INSERT INTO governor_calls "
            "  (id, user_id, feature, model, dollars_estimated, created_at) "
            "VALUES "
            "  (:id, :uid, :feature, :model, :reservation, now())"
        ),
        {
            "id": str(call_id),
            "uid": str(user_id),
            "feature": feature,
            "model": SONNET_MODEL,
            "reservation": RESERVATION_USD,
        },
    )


async def _reserve_call_slot(
    db: AsyncSession,
    call_id: uuid.UUID,
    user_id: uuid.UUID,
    feature: str,
    cap: int | None,
) -> None:
    """Claim one call against the cap and write the audit row, atomically.

    FLE-23 §6 — the cap used to be advisory under concurrency. _check_cap runs a
    COUNT(*), _insert_governor_call inserted right after, and nothing stood
    between them: two taps arriving together both counted 2 against a cap of 3,
    both passed, and both inserted. Bounded leak ($0.0990 per breakdown) but the
    cap did not actually hold, which is the whole point of a cap.

    The fix is a transaction-scoped Postgres advisory lock keyed on
    (feature, user_id), taken before the COUNT and released by the COMMIT below.
    That serializes check+insert per user per feature — a second concurrent
    request blocks on the lock, then does its COUNT against a table that already
    contains the first request's row and correctly raises BudgetExceededError.

    Two properties worth keeping if this is edited:
      - The lock covers check AND insert. Taking it only around the COUNT
        restores the original race.
      - The lock is released at the COMMIT here, which happens BEFORE the ~76s
        Sonnet dispatch. Concurrent users of the same feature are never serialized
        behind each other's API calls — only behind each other's COUNT+INSERT.

    An uncapped feature (cap=None, including FLETCHER_CAP_<FEATURE>=off) skips the
    PER-USER lock: with nothing to enforce there is no race to lose, and taking a
    per-user lock on the hot path would be pure contention. It does NOT skip the
    global ceiling — FLETCHER_CAP_BREAKDOWN=off is exactly the configuration prod
    has been running, and the ceiling is the floor under it.

    FLE-92 — two locks, always in this order (global, then per-user) so that two
    callers can never hold one and wait for the other. Both are transaction-scoped
    and both release at the single COMMIT below, still before the ~76s dispatch.

    The global lock serializes every governed call's check+INSERT across all users.
    That is a real serialization point and it is the right trade at pilot scale:
    the critical section is two indexed queries and an INSERT, and the alternative
    is a ceiling that holds only when nobody taps at the same time.
    """
    # Global ceiling first: it is the harder floor, and refusing here means the
    # per-user count is never even read.
    ceiling = effective_pilot_ceiling()
    if ceiling is not None:
        # hashtext() -> int4; the two-arg pg_advisory_xact_lock(int4, int4) variant
        # keeps the key in one namespace (classid 0) without needing int8 casting.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key), 0)"),
            {"key": _PILOT_CEILING_LOCK_KEY},
        )
        await _check_pilot_ceiling(db, ceiling)

    if cap is not None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key), 0)"),
            {"key": f"{feature}:{user_id}"},
        )
        await _check_cap(db, user_id, feature, cap)

    await _insert_governor_call(db, call_id, user_id, feature)
    await db.commit()   # releases both advisory locks


async def _update_success(
    db: AsyncSession,
    call_id: uuid.UUID,
    prompt_tokens_actual: int | None,
    output_tokens_actual: int | None,
) -> None:
    """Update governor_calls row with actual usage + dollars_actual post-dispatch."""
    model = await _call_model(db, call_id)
    dollars = price_dollars(model, prompt_tokens_actual, output_tokens_actual) if model else None
    await db.execute(
        text(
            "UPDATE governor_calls "
            "SET prompt_tokens_actual = :pt, output_tokens_actual = :ot, "
            "    dollars_actual = :usd "
            "WHERE id = :id"
        ),
        {
            "id": str(call_id),
            "pt": prompt_tokens_actual,
            "ot": output_tokens_actual,
            "usd": dollars,
        },
    )
    await db.commit()


async def _update_error(
    db: AsyncSession,
    call_id: uuid.UUID,
    error_code: str,
) -> None:
    """Update governor_calls row with error_code on failure."""
    await db.execute(
        text(
            "UPDATE governor_calls SET error_code = :ec WHERE id = :id"
        ),
        {"id": str(call_id), "ec": error_code},
    )
    await db.commit()


async def _update_error_detached(call_id: uuid.UUID, error_code: str) -> None:
    """Stamp error_code on a FRESH session, for use while unwinding a cancellation.

    FLE-39: the request-scoped `db` the decorator was handed belongs to the ASGI
    task that is being torn down. By the time a CancelledError reaches the
    wrapper, that session may already be closed or mid-rollback, and reusing it
    is the same trap the @governed-inside-SAVEPOINT rule exists for. A session of
    our own is the only one we can still rely on.

    Shielded because cancellation can be delivered more than once: without the
    shield a second cancel lands on the first await here and the row stays
    unstamped — exactly the bug. With it, the UPDATE runs to completion even when
    our own await is torn away from us.
    """
    from app.db.session import AsyncSessionLocal

    async def _stamp() -> None:
        async with AsyncSessionLocal() as db:
            await _update_error(db, call_id, error_code)

    await asyncio.shield(asyncio.ensure_future(_stamp()))


# ---------------------------------------------------------------------------
# Public: read-only cap probe
# ---------------------------------------------------------------------------

async def assert_cap_available(
    db: AsyncSession,
    user_id: uuid.UUID,
    feature: str,
    default_cap: int | None,
) -> None:
    """Raise BudgetExceededError if `feature` has no headroom left for `user_id`.

    A read-only pre-flight probe. It reserves nothing and writes nothing — the
    authoritative reservation is still _reserve_call_slot inside @governed.

    Exists for callers that must DESTROY state before they can reach the governed
    call. POST /users/{id}/re-run wipes the user's songs and skill_nodes and
    commits that wipe before invoking run_onboarding_parse, because the governor's
    fresh session needs a durable users row. Without a probe, a user at the
    onboarding cap would have their graph deleted and then get a budget error —
    left with nothing, which is strictly worse than being refused. The probe turns
    that into a clean refusal with the graph still intact.

    The probe-then-act gap is a genuine TOCTOU, and that is acceptable here
    precisely because the probe is not the enforcement: two concurrent re-runs at
    the exact cap boundary can both pass the probe, and the second will then be
    stopped by _reserve_call_slot's locked check. The probe's only job is to avoid
    destroying data we cannot rebuild.

    FLE-92: probes the global ceiling too. A user at the ceiling has exactly the
    same claim to not having their graph deleted first as a user at their own cap,
    and the ceiling is the limit far more likely to be the one in the way.
    """
    ceiling = effective_pilot_ceiling()
    if ceiling is not None:
        await _check_pilot_ceiling(db, ceiling)

    cap = effective_cap(feature, default_cap)
    if cap is None:
        return
    await _check_cap(db, user_id, feature, cap)


# ---------------------------------------------------------------------------
# Public: record_estimate helper (called by wrapped fns pre-dispatch)
# ---------------------------------------------------------------------------

async def record_estimate(
    call_id: uuid.UUID,
    prompt_tokens: int,
    max_output_tokens: int | None = None,
) -> None:
    """Called by @governed-wrapped functions just before their client.messages.create() call.

    Updates governor_calls.prompt_tokens_estimated for the row created by @governed's
    pre-cap-check. The wrapped fn is responsible for computing the estimate via
    `estimate = await client.messages.count_tokens(model=SONNET_MODEL, messages=messages)`
    and passing `estimate.input_tokens` as the second argument.

    FLE-23 §4: also writes dollars_estimated. `max_output_tokens` is the wrapped fn's
    own max_tokens ceiling, which makes the estimate a genuine pre-dispatch WORST CASE
    — the most this call can possibly cost — rather than a prompt-only number that
    under-reports by the output rate (5x input on Sonnet). That is the figure a cost
    governor actually wants: "can I afford to dispatch this?". Callers that omit it get
    the prompt-side cost alone, which is a floor, not a ceiling.

    dollars_estimated is therefore NOT comparable to dollars_actual as a forecast error —
    actual output is normally far below the ceiling. The pair answers "what did we risk
    vs. what did we spend", not "how good was the guess".

    FLE-92: dollars_estimated already holds RESERVATION_USD from the INSERT, so the
    write is COALESCE(:usd, dollars_estimated) — an unpriced model leaves the
    reservation standing instead of erasing it. A plain `= :usd` with usd=None would
    drop a live call's contribution to the global ceiling to zero, which is the one
    thing the reservation exists to prevent.

    Uses a fresh DB session per call to avoid cross-session state pollution.
    """
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        model = await _call_model(db, call_id)
        dollars = price_dollars(model, prompt_tokens, max_output_tokens) if model else None
        await db.execute(
            text(
                "UPDATE governor_calls "
                "SET prompt_tokens_estimated = :n, "
                "    dollars_estimated = COALESCE(:usd, dollars_estimated) "
                "WHERE id = :call_id"
            ),
            {"n": prompt_tokens, "usd": dollars, "call_id": str(call_id)},
        )
        await db.commit()


async def record_actuals(
    call_id: uuid.UUID,
    prompt_tokens_actual: int | None,
    output_tokens_actual: int | None,
) -> None:
    """Called by wrapped fns after dispatch to record actual Anthropic usage.

    Optional helper — if the wrapped fn doesn't call this, actuals remain NULL
    (indicating the call errored before returning usage). Breakdown.py and
    onboarding.py call this to fully populate the audit row.

    FLE-23 §4: also writes dollars_actual, priced from the usage figures Anthropic
    returned. This is the column a per-user spend audit reads — before it, the
    governor counted calls and never dollars, so `SELECT SUM(dollars_actual) ...
    GROUP BY user_id` returned a column of NULLs.
    """
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        model = await _call_model(db, call_id)
        dollars = price_dollars(model, prompt_tokens_actual, output_tokens_actual) if model else None
        await db.execute(
            text(
                "UPDATE governor_calls "
                "SET prompt_tokens_actual = :pt, output_tokens_actual = :ot, "
                "    dollars_actual = :usd "
                "WHERE id = :call_id"
            ),
            {
                "call_id": str(call_id),
                "pt": prompt_tokens_actual,
                "ot": output_tokens_actual,
                "usd": dollars,
            },
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Main decorator
# ---------------------------------------------------------------------------

def governed(feature: str, cap: int | None = None):
    """Decorator factory. Applied at run_* function definition sites.

    Usage:
        @governed(feature='breakdown', cap=BREAKDOWN_CAP)
        async def run_technique_breakdown(..., *, db: AsyncSession, user_id: UUID) -> Breakdown:
            ...

    The wrapped fn MUST accept db: AsyncSession and user_id: UUID as keyword-only args.
    These are forwarded to the wrapped fn after cap-check + audit row insert.

    The `cap` argument is the default; FLETCHER_CAP_<FEATURE> overrides it per
    process (see effective_cap) so the limit can be lifted without a code change.

    FLE-92 removed the `window` argument. It had been part of the signature since
    D-04 and was never read by anything — every call site passed window='7d' and the
    body hardcoded 7 days regardless. Leaving a parameter that looks like it selects
    the window, next to FEATURE_WINDOWS which actually does, is a trap: the first
    person to need a different window would set it and ship a cap that silently did
    not change. Register the feature in FEATURE_WINDOWS instead.

    Steps:
    (a) Extract db + user_id from kwargs (fail-loud TypeError if missing).
    (b+c) _reserve_call_slot: under the global ceiling lock and then a
          per-(feature, user) lock, check the pilot dollar ceiling (raises
          PilotBudgetExhaustedError) and the per-user cap (raises
          BudgetExceededError), then INSERT the governor_calls row and COMMIT. One
          atomic step so both limits hold under concurrency (FLE-23 §6, FLE-92);
          the locks are released before dispatch.
    (d) Set _current_call_id ContextVar so wrapped fn can retrieve call_id via current_call_id().
    (e) Await fn — wrapped fn calls record_estimate() + record_actuals() internally.
    (f) On anthropic.APIError status_code==429: UPDATE error_code; raise AnthropicQuotaExceededError.
    (g) On any other exception: UPDATE error_code; re-raise.
    (h) On a BaseException (CancelledError from a client disconnect): UPDATE
        error_code on a FRESH session, then re-raise (FLE-39).
    """
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            # (a) Extract db + user_id — fail loud if either missing
            db: AsyncSession | None = kwargs.get("db")
            if db is None:
                raise TypeError(
                    f"@governed requires db: AsyncSession kwarg on {fn.__name__}. "
                    "Add 'db: AsyncSession' to the function's keyword-only args."
                )
            user_id = kwargs.get("user_id")
            if user_id is None:
                raise TypeError(
                    f"@governed requires user_id kwarg on {fn.__name__}. "
                    "Add 'user_id: UUID' to the function's keyword-only args."
                )

            # (b+c) Cap check + INSERT governor_calls row, as one atomic reservation.
            # FLETCHER_CAP_<FEATURE> can raise or remove the cap at runtime; a
            # removed cap skips the lock (see _reserve_call_slot).
            call_cap = effective_cap(feature, cap)
            call_id = uuid.uuid4()
            await _reserve_call_slot(db, call_id, user_id, feature, call_cap)

            # (d) Set ContextVar so wrapped fn can call record_estimate(call_id, ...)
            token = _current_call_id.set(call_id)

            try:
                # (e) Call the wrapped fn
                result = await fn(*args, **kwargs)
                return result

            except APIError as exc:
                # (f) Anthropic-side 429 → FLETCHER_OUT (D-08)
                if getattr(exc, "status_code", None) == 429:
                    try:
                        await _update_error(db, call_id, "AnthropicQuotaExceededError")
                    except Exception:
                        logger.exception(
                            "Failed to update governor_calls error_code after Anthropic 429"
                        )
                    raise AnthropicQuotaExceededError() from exc
                # Other Anthropic API errors: record as generic error
                try:
                    await _update_error(db, call_id, type(exc).__name__)
                except Exception:
                    logger.exception(
                        "Failed to update governor_calls error_code after APIError"
                    )
                raise

            except Exception as exc:
                # (g) Any other exception: record error_code + re-raise
                try:
                    await _update_error(db, call_id, type(exc).__name__)
                except Exception:
                    logger.exception(
                        "Failed to update governor_calls error_code on exception"
                    )
                raise

            except BaseException as exc:
                # (h) FLE-39 — BaseException-only terminations, in practice
                # asyncio.CancelledError from a client disconnect. On Python 3.13
                # CancelledError does NOT subclass Exception, so arm (g) never saw
                # it and the row was left with error_code NULL: the user's tap
                # timed out AND cost them one of their three weekly breakdowns.
                #
                # Compounds with FLE-29: a 60s client timeout against an 87s
                # breakdown disconnects, burns a unit, and the retry burns a
                # second one for the single breakdown the server did finish and
                # cache. Stamping here removes the quota-burn half of that.
                #
                # Fresh session, not the request's — see _update_error_detached.
                try:
                    await _update_error_detached(call_id, type(exc).__name__)
                except BaseException:
                    logger.exception(
                        "Failed to update governor_calls error_code on %s",
                        type(exc).__name__,
                    )
                raise

            finally:
                # Always reset the ContextVar to avoid leaking call_id into nested coroutines
                _current_call_id.reset(token)

        return wrapper
    return decorator
