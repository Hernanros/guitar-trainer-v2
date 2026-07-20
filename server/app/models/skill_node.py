# server/app/models/skill_node.py
# Pydantic response models for skill graph data.
# Follows the from_attributes=True pattern established in song.py.
#
# Phase 2 (02-01): SkillNodeResponse — API response shape for GET /skill-graph.
# Phase 2 (02-03): SonnetSkillNodeProposal, SonnetSongProposal, SonnetOnboardingOutput
#                  (Sonnet tool-use structured output shapes) — added in 02-03.
# Phase 2 (02-03): SkillNode + SongSkill ORM classes — added in 02-03 alongside the queries.
from decimal import Decimal
from typing import Any, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


class SkillNodeResponse(BaseModel):
    """API response shape for a single node in the user's skill graph.

    Deterministic-writes principle (D-11): mastery always starts at 0.0 after onboarding.
    Sonnet writes STRUCTURE (nodes + hierarchy), never mastery values.
    Mastery is earned via Phase 3 session ratings.

    level: SQLAlchemy ORM returns the Python SkillLevel enum instance from SAEnum columns
    on read-back. The field_validator coerces it to its .value string so the Literal
    constraint passes. (Rule 1 fix: auto-coercion so from_attributes=True works end-to-end.)
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

    @field_validator("level", mode="before")
    @classmethod
    def coerce_level_enum(cls, v: Any) -> str:
        """Convert SkillLevel enum to string value when reading from ORM rows.

        SQLAlchemy's SAEnum returns the Python enum instance on attribute access;
        Pydantic's Literal validator expects the raw string, not the enum instance.
        """
        if hasattr(v, "value"):
            return v.value
        return v


# ---------------------------------------------------------------------------
# Sonnet tool-use structured output shapes (Phase 2, 02-03)
# These are what Sonnet returns via the emit_onboarding_output tool;
# they are inputs to the server-side persist step (_persist_bootstrap in users.py).
# ---------------------------------------------------------------------------

class SonnetSkillNodeProposal(BaseModel):
    """A single skill node as proposed by Sonnet in the emit_onboarding_output tool.

    NO mastery field per D-11 — mastery is always 0 at bootstrap, enforced server-side.
    Sonnet uses temp_id as a local reference; the server assigns real UUIDs on persist.
    """
    temp_id: str                        # Sonnet-local id; server assigns real UUIDs (D-06)
    name: str
    level: Literal["root", "sub", "leaf"]
    parent_temp_id: Optional[str] = None   # None for roots
    tempo_bin_low: Optional[int] = None    # leaves only; 5-bpm width (SKILL-02)
    tempo_bin_high: Optional[int] = None   # leaves only; MUST equal tempo_bin_low + 5


class SonnetSongProposal(BaseModel):
    """A single song as proposed by Sonnet — canonicalized title + artist + category."""
    title: str
    artist: str
    category: Literal["can_play", "working_on", "aspirational"]
    skill_temp_ids: List[str]          # references SonnetSkillNodeProposal.temp_id


class SonnetOnboardingOutput(BaseModel):
    """Structured tool-use output contract for the Sonnet onboarding call (D-06).

    This is the return type of run_onboarding_parse() and the input to _persist_bootstrap().
    SonnetOnboardingOutput.model_json_schema() is used as the input_schema for the
    Anthropic tool-use call, forcing Sonnet to output a valid structured response.
    """
    songs: List[SonnetSongProposal]
    skill_graph: List[SonnetSkillNodeProposal]
