# server/app/models/session.py
# Pydantic v2 models for session rating write (Phase 3 Slice C).
# SessionCreate: request body for POST /api/v1/sessions.
# SessionResponse: response body (201 Created).
# RatingLiteral: locked Fletcher-voiced values per D-06.
import uuid
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

RatingLiteral = Literal["not_my_tempo", "getting_closer", "thats_what_im_looking_for"]


class SessionCreate(BaseModel):
    song_id: int
    rating: RatingLiteral


class SessionResponse(BaseModel):
    id: UUID
    user_id: UUID
    song_id: int
    rating: RatingLiteral
    local_calendar_day: str  # ISO date string "YYYY-MM-DD"
    rated_at: str            # ISO datetime string

    model_config = {"from_attributes": True}
