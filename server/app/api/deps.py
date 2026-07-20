# server/app/api/deps.py
# Reusable FastAPI dependencies for Phase 2+.
#
# D-04: X-User-ID header pattern — client-provided device UUID identity.
# POC scope: any well-formed UUID is accepted (no verification, no auth).
# Phase 4+ cost governor may attach per-UUID rate limits at this boundary.
from uuid import UUID

from fastapi import Header


async def get_user_id(x_user_id: UUID = Header(..., alias="X-User-ID")) -> UUID:
    """Read the device identity UUID from the X-User-ID header.

    Per D-04: any well-formed UUID is accepted. FastAPI's UUID type coercion
    rejects malformed values with 422 before reaching the endpoint handler.
    Phase 4+ governor will add per-UUID rate limits here.
    """
    return x_user_id
