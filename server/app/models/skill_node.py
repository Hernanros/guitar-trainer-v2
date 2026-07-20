# server/app/models/skill_node.py
# Pydantic response models for skill graph data.
# Follows the from_attributes=True pattern established in song.py.
#
# Phase 2 (02-01): SkillNodeResponse — API response shape for GET /skill-graph.
# Phase 2 (02-03): SonnetSkillNodeProposal, SonnetSongProposal, SonnetOnboardingOutput
#                  (Sonnet tool-use structured output shapes) — added in 02-03.
# Phase 2 (02-03): SkillNode + SongSkill ORM classes — added in 02-03 alongside the queries.
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class SkillNodeResponse(BaseModel):
    """API response shape for a single node in the user's skill graph.

    Deterministic-writes principle (D-11): mastery always starts at 0.0 after onboarding.
    Sonnet writes STRUCTURE (nodes + hierarchy), never mastery values.
    Mastery is earned via Phase 3 session ratings.
    """
    id: UUID
    user_id: UUID
    name: str
    level: Literal["root", "sub", "leaf"]
    parent_id: Optional[UUID] = None
    tempo_bin_low: Optional[int] = None    # leaves only (D-09: 5-bpm bins)
    tempo_bin_high: Optional[int] = None   # leaves only
    mastery: Decimal = Decimal("0.0")      # default 0 — never Sonnet-populated (D-11)

    model_config = {"from_attributes": True}
