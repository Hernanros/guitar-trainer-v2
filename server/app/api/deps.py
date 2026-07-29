# server/app/api/deps.py
# Reusable FastAPI dependencies for Phase 2+.
#
# D-04: X-User-ID header pattern — client-provided device UUID identity.
# POC scope: any well-formed UUID is accepted (no verification, no auth).
# Phase 4+ cost governor may attach per-UUID rate limits at this boundary.
#
# Phase 3 (03-01): get_tz_offset_minutes — validates X-Timezone-Offset header.
# Range [-840, +840] matches ±14h max UTC offset (Postgres INTERVAL bound).
# T-03-01-01 threat mitigation: range-check rejects malicious clock-shift exploits.
from typing import Annotated
from uuid import UUID

from fastapi import Header, HTTPException


async def get_user_id(x_user_id: UUID = Header(..., alias="X-User-ID")) -> UUID:
    """Read the device identity UUID from the X-User-ID header.

    Per D-04: any well-formed UUID is accepted. FastAPI's UUID type coercion
    rejects malformed values with 422 before reaching the endpoint handler.
    Phase 4+ governor will add per-UUID rate limits here.
    """
    return x_user_id


def get_tz_offset_minutes(
    x_timezone_offset: Annotated[str, Header(alias="X-Timezone-Offset")] = "0",
) -> int:
    """Parse and validate the X-Timezone-Offset header (minutes from UTC).

    Valid range: [-840, +840] matching ±14h (Baker Island UTC-12 to Line Islands UTC+14).
    Postgres INTERVAL '1 minute' math is used to convert this to a local calendar day
    on the server side — never trusting client-side clock for the idempotency check.

    Per T-03-01-01: integer-only parsing + range gate prevents clock-shift manipulation.
    Missing header defaults to 0 (UTC) per RESEARCH §8 landmine 8.
    """
    try:
        v = int(x_timezone_offset)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="X-Timezone-Offset must be an integer number of minutes.",
        )
    if not -840 <= v <= 840:
        raise HTTPException(
            status_code=400,
            detail="X-Timezone-Offset out of range. Must be between -840 and 840 (±14 hours).",
        )
    return v
