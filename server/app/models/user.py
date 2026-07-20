# server/app/models/user.py
# Pydantic v2 models for user identity and onboarding (Phase 2).
# Pattern: mirrors server/app/models/song.py — BaseModel + from_attributes=True.
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel

from .skill_node import SkillNodeResponse


class UserPreferences(BaseModel):
    """User session preferences (stored in users.preferences JSONB).

    Per D-12: session_length_min is one of 15 | 30 | 45 | 60.
    Per D-13: retention_format default is 'streak'.
    """
    session_length_min: int                                          # 15 | 30 | 45 | 60
    retention_format: Literal["streak", "weekly_digest", "monthly_milestone"] = "streak"


class UserBootstrapRequest(BaseModel):
    """POST /api/v1/users request body (per D-05 + D-04).

    songs: free-text per category — Sonnet parses in 02-03.
    raw_input: verbatim wizard text kept for fail-open (D-07).
    """
    user_id: UUID
    songs: dict              # {can_play: [str], working_on: [str], aspirational: [str]}
    preferences: UserPreferences
    raw_input: dict          # verbatim wizard text, kept for fail-open (D-07)


class UserResponse(BaseModel):
    """GET /api/v1/users/{user_id} response body."""
    id: UUID
    preferences: UserPreferences
    onboarded_at: Optional[str] = None    # ISO timestamp; None until bootstrap completes

    model_config = {"from_attributes": True}


class SkillGraphResponse(BaseModel):
    """POST /api/v1/users response body + GET /api/v1/users/{user_id}/skill-graph.

    In 02-01 this was always nodes=[] (Sonnet call landed in 02-03).
    In 02-03+ nodes carries the full per-user skill tree.

    mode values:
      "full"     → Sonnet call succeeded and full DAG persisted (D-06)
      "bootstrap"→ Sonnet failed after retry; SAVEPOINT rolled back; 6-root fallback
                   graph persisted (D-07); raw_onboarding_text saved for future reprocessing
      "existing" → idempotency guard hit; user already had skill_nodes; returned graph
                   unchanged; no Sonnet call fired (Revision D guard)

    02-04 uses mode="bootstrap" to show the "I'll fill in the details as we go." copy per D-07.
    Mobile client should treat mode="existing" identically to "full".
    """
    nodes: List[SkillNodeResponse]
    mode: Literal["full", "bootstrap", "existing"] = "full"
