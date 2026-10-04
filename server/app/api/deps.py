# server/app/api/deps.py
# Reusable FastAPI dependencies for Phase 2+.
#
# D-04: X-User-ID header pattern — client-provided device UUID identity.
# D-10: X-Timezone-Offset header pattern — client sends offset in minutes so server
#       can compute "local calendar day" for idempotency guards and daily rotation.
# D-11 (Phase 4): X-Admin-Token header dep for /admin/curator endpoints.
# POC scope: any well-formed UUID is accepted (no verification, no auth).
# Phase 4+ cost governor may attach per-UUID rate limits at this boundary.
import hmac
import logging
import os
import time
from collections import deque
from uuid import UUID

from fastapi import Depends, Header, HTTPException

logger = logging.getLogger(__name__)


async def get_admin_token(
    x_admin_token: str = Header(..., alias="X-Admin-Token"),
) -> None:
    """Validate X-Admin-Token against FLETCHER_ADMIN_TOKEN env var.

    Returns None on success.
    Raises HTTP 503 if FLETCHER_ADMIN_TOKEN is not set in the environment.
    Raises HTTP 401 if the token does not match.
    Comparison uses hmac.compare_digest for constant-time semantics (T-04-03-03).
    """
    expected = os.environ.get("FLETCHER_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin token not configured on server.",
        )
    if not hmac.compare_digest(x_admin_token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin token.")


async def get_user_id(x_user_id: UUID = Header(..., alias="X-User-ID")) -> UUID:
    """Read the device identity UUID from the X-User-ID header.

    Per D-04: any well-formed UUID is accepted. FastAPI's UUID type coercion
    rejects malformed values with 422 before reaching the endpoint handler.
    Phase 4+ governor will add per-UUID rate limits here.
    """
    return x_user_id


# ---------------------------------------------------------------------------
# Per-user request throttle (FLE-23 §3)
# ---------------------------------------------------------------------------
# No rate limiting existed anywhere in the stack before this — no middleware in
# main.py, no slowapi/limits in requirements.txt. POST /users/{id}/re-run was
# therefore reachable from Settings as fast as a finger can tap, each tap paying
# for 1 onboarding + up to 10 verifier Sonnet calls.
#
# The governor cap on `onboarding` (ONBOARDING_CAP) is the durable, DB-backed
# spend bound. This throttle is the complementary latency bound: it stops a
# double-tap or a stuck retry loop from firing several concurrent onboardings
# that each hold 7 DB connections (see app/db/session.py) before any of them has
# committed a governor_calls row for the cap to count.
#
# In-process, not Redis, and that is a deliberate fit to this deployment rather
# than a shortcut: the server runs single-worker (uvicorn --workers 1 — the same
# property app/ai/governor.py's ContextVar usage relies on), so one process sees
# every request and in-process state is authoritative. If this ever grows to
# multiple workers, this becomes per-worker and must move to a shared store; the
# governor cap keeps holding either way, because it counts rows in Postgres.
class _SlidingWindowThrottle:
    """Allow at most `max_calls` per `window_seconds` per key, else HTTP 429.

    Sliding window over a deque of monotonic timestamps per key — monotonic so a
    clock adjustment (NTP step, DST) can never widen or collapse the window.

    Memory: keys are pruned lazily on their own next request, so a key touched
    once and never again would leak one small deque. `_MAX_KEYS` bounds that: at
    the ceiling, fully-expired keys are swept, and if none are expired the
    throttle fails OPEN (allows the request) rather than rejecting real traffic
    to protect a bookkeeping structure. Failing open is right here because the
    governor cap is the actual spend bound — this layer is defence in depth.
    """

    _MAX_KEYS = 10_000

    def __init__(self, name: str, max_calls: int, window_seconds: float) -> None:
        self.name = name
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def _sweep(self, now: float) -> None:
        for key in [k for k, dq in self._hits.items() if not dq or now - dq[-1] > self.window_seconds]:
            del self._hits[key]

    def check(self, key: str) -> None:
        now = time.monotonic()
        dq = self._hits.get(key)
        if dq is None:
            if len(self._hits) >= self._MAX_KEYS:
                self._sweep(now)
            if len(self._hits) >= self._MAX_KEYS:
                logger.warning(
                    "Throttle %s at %d live keys — failing open for key %s.",
                    self.name, len(self._hits), key,
                )
                return
            dq = self._hits[key] = deque()

        cutoff = now - self.window_seconds
        while dq and dq[0] <= cutoff:
            dq.popleft()

        if len(dq) >= self.max_calls:
            retry_after = max(1, int(self.window_seconds - (now - dq[0])) + 1)
            logger.warning(
                "Throttle %s rejected key %s (%d calls in %.0fs).",
                self.name, key, len(dq), self.window_seconds,
            )
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Too many {self.name} requests. "
                    f"Limit is {self.max_calls} per {int(self.window_seconds)}s. "
                    f"Retry in {retry_after}s."
                ),
                headers={"Retry-After": str(retry_after)},
            )

        dq.append(now)


# Re-run is the expensive one: it wipes the user's graph and then spends
# $0.05–$0.19 rebuilding it. 3 per 10 minutes is well above any legitimate
# "I got my answers wrong, let me redo the wizard" pattern and well below
# anything that can burn budget or exhaust the connection pool.
_re_run_throttle = _SlidingWindowThrottle("onboarding re-run", max_calls=3, window_seconds=600)


async def throttle_re_run(user_id: UUID = Depends(get_user_id)) -> None:
    """FastAPI dependency: per-device throttle for POST /users/{id}/re-run.

    Keys on the X-User-ID device UUID rather than the path user_id, so the limit
    cannot be sidestepped by varying the path — and so it composes with the
    identity check on the same endpoint instead of duplicating it.
    """
    _re_run_throttle.check(str(user_id))


async def get_tz_offset_minutes(
    x_timezone_offset: int = Header(0, alias="X-Timezone-Offset"),
) -> int:
    """Read the client's timezone offset in minutes from the X-Timezone-Offset header.

    Per D-10: client sends minutes (e.g. -300 = UTC-5, +330 = UTC+5:30).
    Valid range: [-840, +840] (±14 hours, the furthest real timezone extents).
    Server uses this to compute the user's local calendar day for rotation + idempotency.
    """
    if not (-840 <= x_timezone_offset <= 840):
        raise HTTPException(
            status_code=400,
            detail=(
                f"X-Timezone-Offset must be in [-840, 840] minutes (got {x_timezone_offset}). "
                "Check device timezone."
            ),
        )
    return x_timezone_offset
