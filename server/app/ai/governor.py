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
D-01: rolling 7-day window via SQL: created_at > now() - interval '7 days'.
D-02: BudgetExceededError carries feature + resets_at (ISO).
D-08: AnthropicQuotaExceededError surfaces org-level 429 distinctly.
"""
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
    """Raised by @governed when the per-user weekly cap is hit.

    feature='breakdown': cap=3 per 7-day rolling window (D-01/D-02).
    Caught by the breakdown endpoint to return HTTP 429 with BREAKDOWN_CAPPED body.
    """
    def __init__(self, feature: str, resets_at: str) -> None:
        self.feature = feature
        self.resets_at = resets_at
        super().__init__(
            f"Budget exceeded for feature={feature}. Resets at {resets_at}."
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

BREAKDOWN_CAP = 3

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
# Internal helpers
# ---------------------------------------------------------------------------

async def _check_cap(
    db: AsyncSession,
    user_id: uuid.UUID,
    feature: str,
    cap: int,
) -> None:
    """COUNT current window rows; if >= cap, fetch MIN(created_at) for resets_at and raise.

    D-01: rolling 7-day window using created_at > now() - interval '7 days'.

    FLE-18: `AND error_code IS NULL` excludes failed calls from the cap. The audit
    row is INSERTed before dispatch and stamped with error_code only on failure
    (_update_error), so an in-flight call still counts — concurrent requests cannot
    race past the cap — while a call that errored does not. Without this, three
    transient Anthropic failures locked a user out of breakdowns for a full week
    despite never having received one.
    """
    count = await db.scalar(
        text(
            "SELECT COUNT(*) FROM governor_calls "
            "WHERE user_id = :u AND feature = :f "
            "AND created_at > now() - interval '7 days' "
            "AND error_code IS NULL"
        ),
        {"u": str(user_id), "f": feature},
    )
    if count is None:
        count = 0

    if count >= cap:
        # Fetch oldest call in current window to compute resets_at
        min_created = await db.scalar(
            text(
                "SELECT MIN(created_at) FROM governor_calls "
                "WHERE user_id = :u AND feature = :f "
                "AND created_at > now() - interval '7 days' "
                "AND error_code IS NULL"
            ),
            {"u": str(user_id), "f": feature},
        )
        if min_created is None:
            # Fallback: shouldn't happen since count >= cap, but be defensive
            min_created = datetime.now(timezone.utc)

        # Ensure timezone-aware datetime
        if min_created.tzinfo is None:
            min_created = min_created.replace(tzinfo=timezone.utc)

        resets_at_dt = min_created + timedelta(days=7)
        resets_at_iso = resets_at_dt.isoformat()
        raise BudgetExceededError(feature=feature, resets_at=resets_at_iso)


async def _insert_governor_call(
    db: AsyncSession,
    call_id: uuid.UUID,
    user_id: uuid.UUID,
    feature: str,
) -> None:
    """Insert an initial governor_calls row with prompt_tokens_estimated=NULL.

    Row exists before dispatch so failures still audit. The wrapped fn will
    call record_estimate() to populate prompt_tokens_estimated.

    Does NOT commit — the caller owns the transaction boundary so the cap check
    and this insert can be one atomic unit (see _reserve_call_slot).
    """
    await db.execute(
        text(
            "INSERT INTO governor_calls "
            "  (id, user_id, feature, model, created_at) "
            "VALUES "
            "  (:id, :uid, :feature, :model, now())"
        ),
        {
            "id": str(call_id),
            "uid": str(user_id),
            "feature": feature,
            "model": SONNET_MODEL,
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
    lock entirely: with nothing to enforce there is no race to lose, and taking a
    per-user lock on the hot path would be pure contention.
    """
    if cap is None:
        await _insert_governor_call(db, call_id, user_id, feature)
        await db.commit()
        return

    # hashtext() -> int4; the two-arg pg_advisory_xact_lock(int4, int4) variant
    # keeps the key in one namespace (classid 0) without needing int8 casting.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key), 0)"),
        {"key": f"{feature}:{user_id}"},
    )
    await _check_cap(db, user_id, feature, cap)
    await _insert_governor_call(db, call_id, user_id, feature)
    await db.commit()   # releases the advisory lock


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
    """
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

    Uses a fresh DB session per call to avoid cross-session state pollution.
    """
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        model = await _call_model(db, call_id)
        dollars = price_dollars(model, prompt_tokens, max_output_tokens) if model else None
        await db.execute(
            text(
                "UPDATE governor_calls "
                "SET prompt_tokens_estimated = :n, dollars_estimated = :usd "
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

def governed(feature: str, cap: int | None = None, window: str = "7d"):
    """Decorator factory. Applied at run_* function definition sites.

    Usage:
        @governed(feature='breakdown', cap=3, window='7d')
        async def run_technique_breakdown(..., *, db: AsyncSession, user_id: UUID) -> Breakdown:
            ...

    The wrapped fn MUST accept db: AsyncSession and user_id: UUID as keyword-only args.
    These are forwarded to the wrapped fn after cap-check + audit row insert.

    The `cap` argument is the default; FLETCHER_CAP_<FEATURE> overrides it per
    process (see effective_cap) so the limit can be lifted without a code change.

    Steps:
    (a) Extract db + user_id from kwargs (fail-loud TypeError if missing).
    (b+c) _reserve_call_slot: under a per-(feature, user) advisory lock, _check_cap
          (raises BudgetExceededError if COUNT >= cap) then INSERT the governor_calls
          row with prompt_tokens_estimated=NULL, and COMMIT. One atomic step so the
          cap holds under concurrency (FLE-23 §6); the lock is released before dispatch.
    (d) Set _current_call_id ContextVar so wrapped fn can retrieve call_id via current_call_id().
    (e) Await fn — wrapped fn calls record_estimate() + record_actuals() internally.
    (f) On anthropic.APIError status_code==429: UPDATE error_code; raise AnthropicQuotaExceededError.
    (g) On any other exception: UPDATE error_code; re-raise.
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

            finally:
                # Always reset the ContextVar to avoid leaking call_id into nested coroutines
                _current_call_id.reset(token)

        return wrapper
    return decorator
