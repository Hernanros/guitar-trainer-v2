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
import os
from uuid import UUID

from fastapi import Header, HTTPException


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
