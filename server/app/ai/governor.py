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
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import wraps

from anthropic import APIError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import SONNET_MODEL

logger = logging.getLogger(__name__)


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
    """
    count = await db.scalar(
        text(
            "SELECT COUNT(*) FROM governor_calls "
            "WHERE user_id = :u AND feature = :f "
            "AND created_at > now() - interval '7 days'"
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
                "AND created_at > now() - interval '7 days'"
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
    await db.commit()


async def _update_success(
    db: AsyncSession,
    call_id: uuid.UUID,
    prompt_tokens_actual: int | None,
    output_tokens_actual: int | None,
) -> None:
    """Update governor_calls row with actual usage post-dispatch."""
    await db.execute(
        text(
            "UPDATE governor_calls "
            "SET prompt_tokens_actual = :pt, output_tokens_actual = :ot "
            "WHERE id = :id"
        ),
        {
            "id": str(call_id),
            "pt": prompt_tokens_actual,
            "ot": output_tokens_actual,
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
# Public: record_estimate helper (called by wrapped fns pre-dispatch)
# ---------------------------------------------------------------------------

async def record_estimate(call_id: uuid.UUID, prompt_tokens: int) -> None:
    """Called by @governed-wrapped functions just before their client.messages.create() call.

    Updates governor_calls.prompt_tokens_estimated for the row created by @governed's
    pre-cap-check. The wrapped fn is responsible for computing the estimate via
    `estimate = await client.messages.count_tokens(model=SONNET_MODEL, messages=messages)`
    and passing `estimate.input_tokens` as the second argument.

    Uses a fresh DB session per call to avoid cross-session state pollution.
    """
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE governor_calls "
                "SET prompt_tokens_estimated = :n "
                "WHERE id = :call_id"
            ),
            {"n": prompt_tokens, "call_id": str(call_id)},
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
    """
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE governor_calls "
                "SET prompt_tokens_actual = :pt, output_tokens_actual = :ot "
                "WHERE id = :call_id"
            ),
            {
                "call_id": str(call_id),
                "pt": prompt_tokens_actual,
                "ot": output_tokens_actual,
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

    Steps:
    (a) Extract db + user_id from kwargs (fail-loud TypeError if missing).
    (b) If cap is not None: _check_cap → raises BudgetExceededError if COUNT >= cap.
    (c) INSERT governor_calls row with prompt_tokens_estimated=NULL; capture call_id.
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

            # (b) Cap check
            if cap is not None:
                await _check_cap(db, user_id, feature, cap)

            # (c) INSERT governor_calls row
            call_id = uuid.uuid4()
            await _insert_governor_call(db, call_id, user_id, feature)

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
