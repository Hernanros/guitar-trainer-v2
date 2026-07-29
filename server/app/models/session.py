# server/app/models/session.py
# Pydantic v2 models for session ratings (Phase 3 Slice C consumer).
#
# Slice A (03-01) ships this scaffold so Slice C can import directly.
# SessionCreate: request body for POST /api/v1/sessions.
# SessionResponse: response for POST /api/v1/sessions.
# RatingLiteral: re-exported for use in session-related code and tests.
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

# The 3-tier Fletcher-voiced rating (D-06).
# Do NOT change these strings — they map directly to the RatingLevel DB enum
# and to the RatingPills UI labels in UI-SPEC §5.
RatingLiteral = Literal["not_my_tempo", "getting_closer", "thats_what_im_looking_for"]


class SessionCreate(BaseModel):
    """Request body for POST /api/v1/sessions (Slice C endpoint)."""
    song_id: int
    rating: RatingLiteral


class SessionResponse(BaseModel):
    """Response for POST /api/v1/sessions (Slice C endpoint).

    local_calendar_day and rated_at are ISO strings so the OpenAPI → TypeScript
    codegen produces string types (not Record<string, never> which bare dict would give).
    """
    id: UUID
    user_id: UUID
    song_id: int
    rating: RatingLiteral
    local_calendar_day: str   # "YYYY-MM-DD" ISO date
    rated_at: str             # ISO 8601 timestamp string

    model_config = {"from_attributes": True}
