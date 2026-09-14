# server/app/models/session.py
# Pydantic v2 models for session rating write (Phase 3 Slice C).
# SessionCreate: request body for POST /api/v1/sessions.
# SessionResponse: response body (201 Created).
# RatingLiteral: locked Fletcher-voiced values per D-06.
#
# Phase 4.1 (Plan 04.1-02): SessionCreate + SessionResponse gain optional drill_index
# and target_skill_node_id fields for per-drill rating writes. Both-or-neither is
# enforced at the API boundary by the @model_validator on SessionCreate (defense in
# depth alongside the DB CHECK constraint ck_drill_index_pairs_skill_node from
# migration 0005).
import uuid
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, model_validator

RatingLiteral = Literal["not_my_tempo", "getting_closer", "thats_what_im_looking_for"]


class SessionCreate(BaseModel):
    """Request body for POST /api/v1/sessions.

    Phase 4.1: drill_index + target_skill_node_id both default to None (whole-song
    rating — existing behavior). When both are set, this is a drill rating and the
    handler writes mastery only to the target_skill_node_id row (not all song leaves).

    Both-or-neither is enforced by _drill_pair_both_or_neither. Providing exactly one
    of the two fields raises ValidationError → 422 at the endpoint.
    """
    song_id: int
    rating: RatingLiteral
    drill_index: Optional[int] = None
    target_skill_node_id: Optional[UUID] = None

    @model_validator(mode="after")
    def _drill_pair_both_or_neither(self) -> "SessionCreate":
        has_index = self.drill_index is not None
        has_node = self.target_skill_node_id is not None
        if has_index != has_node:
            raise ValueError(
                "drill_index and target_skill_node_id must be provided together or both omitted."
            )
        return self


class SessionResponse(BaseModel):
    """Response body for POST /api/v1/sessions (201 Created).

    Phase 4.1: drill_index + target_skill_node_id echo whatever was submitted so
    the mobile client can confirm the write shape without a follow-up read.
    """
    id: UUID
    user_id: UUID
    song_id: int
    rating: RatingLiteral
    local_calendar_day: str  # ISO date string "YYYY-MM-DD"
    rated_at: str            # ISO datetime string
    drill_index: Optional[int] = None
    target_skill_node_id: Optional[UUID] = None

    model_config = {"from_attributes": True}
