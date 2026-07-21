"""Onboarding parse — single Sonnet call that turns free-text song lists into
a canonicalized songs list + a 3-level DAG skill graph.

Phase 4 cost-governor interception point: this is the ONLY function that calls
Sonnet during onboarding. Do not sprinkle client.messages.create() calls anywhere else.
"""
import asyncio
import logging
from typing import Any

from anthropic import APITimeoutError, APIError

from app.ai.client import get_client, SONNET_MODEL
from app.models.skill_node import SonnetOnboardingOutput

logger = logging.getLogger(__name__)


class AIParseError(Exception):
    """Raised when Sonnet call fails after retry, or when structured output validation fails.

    Caught by bootstrap_user to trigger the D-07 SAVEPOINT-based fail-open path.
    The parent transaction (user row + preferences) remains alive when this is caught
    because it is raised after a SAVEPOINT rollback cleans up any partial skill_nodes/songs.
    """


# Fixed root taxonomy per D-08. Sonnet MUST use exactly these names verbatim.
FIXED_ROOTS = ["Rhythm", "Lead", "Chord Voicings", "Fingerstyle", "Music Theory", "Timing"]

SYSTEM_PROMPT = f"""You are a guitar-teaching assistant helping bootstrap a personalized skill graph for a new user.

You will receive three lists of songs describing what the user can play, is working on, and aspires to play.

Your job: return a structured payload with two parts.

PART 1 — SONGS: for each song the user mentioned, produce a canonicalized entry with title + artist + category + a list of skill_temp_ids that describe the techniques required.

PART 2 — SKILL_GRAPH: a 3-level tree of skill nodes.

CRITICAL RULES:
1. Root-level nodes MUST be exactly these six, no more, no fewer: {', '.join(FIXED_ROOTS)}. Use these names verbatim.
2. Sub-domain nodes (level='sub') are your choice — you decide the meaningful subdivisions under each root. Aim for 2-5 sub-domains per root that you actually populate with leaves. Sub-domains SHOULD reflect the genres/styles the user's songs imply (e.g., Blues, Rock, Fingerstyle, Jazz) — this is how style/genre inference is realized.
3. Leaf nodes (level='leaf') represent specific playable skills. Each leaf MUST have tempo_bin_low and tempo_bin_high. tempo_bin_high MUST equal tempo_bin_low + 5 (5-bpm bins). Pick a reasonable tempo range for the skill.
4. Every leaf MUST have a parent_temp_id pointing to a sub-domain. Every sub-domain MUST have a parent_temp_id pointing to one of the six roots. Roots have parent_temp_id=null.
5. Do NOT include mastery values — mastery is always 0 at bootstrap (managed server-side, not by you).
6. Every skill_temp_id in a song's skill_temp_ids MUST reference an existing SonnetSkillNodeProposal.temp_id in your skill_graph.
7. Assign temp_ids as short unique strings within this response (e.g., "root-rhythm", "sub-blues-shuffle", "leaf-shuffle-e-100").

If a song list is empty, produce roots only for that category's implied focus but do NOT invent songs.

If ALL THREE song lists are empty, return the six roots only with NO sub-domains or leaves — the user has provided no signal to build from."""

# Anthropic tool-use definition — Sonnet returns structured output via this tool.
_TOOL_NAME = "emit_onboarding_output"
_TOOL_DEF: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": "Emit the canonicalized songs + initial skill graph for this user.",
    "input_schema": SonnetOnboardingOutput.model_json_schema(),
}


async def run_onboarding_parse(
    raw_input: dict,
    *,
    timeout_seconds: float = 30.0,
) -> SonnetOnboardingOutput:
    """
    Single Sonnet call per onboarding (D-05). Structured tool-use output (D-06).
    Failure handling (D-07): one retry with widened timeout, then raise AIParseError.
    Caller (POST /api/v1/users) catches AIParseError and applies fail-open (bootstrap graph)
    via SAVEPOINT rollback.

    Phase 4 cost-governor interception point — the ONLY place in the codebase that
    calls Sonnet during onboarding. Phase 4 will wrap this function to track usage.

    raw_input shape: { "can_play": str, "working_on": str, "aspirational": str }

    Returns SonnetOnboardingOutput with canonicalized songs + skill_graph proposals.
    Raises AIParseError if both attempts fail.
    """
    user_content = _format_user_message(raw_input)

    async def _call(timeout: float) -> SonnetOnboardingOutput:
        # get_client() may raise RuntimeError if ANTHROPIC_API_KEY is unset.
        # Calling it inside _call (which is inside the outer try/except below)
        # means the RuntimeError gets wrapped as AIParseError and triggers the
        # D-07 fail-open path — instead of a raw 500 to the client.
        client = get_client()
        resp = await asyncio.wait_for(
            client.messages.create(
                model=SONNET_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                tools=[_TOOL_DEF],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
            ),
            timeout=timeout,
        )
        tool_use = next(
            (b for b in resp.content if getattr(b, "type", None) == "tool_use"), None
        )
        if tool_use is None:
            raise RuntimeError("Sonnet did not emit a tool_use block despite forced tool_choice.")
        return SonnetOnboardingOutput.model_validate(tool_use.input)

    try:
        try:
            return await _call(timeout_seconds)
        except (APITimeoutError, asyncio.TimeoutError, APIError) as e:
            logger.warning(
                "Sonnet onboarding call failed once (%s). Retrying with widened timeout.",
                type(e).__name__,
            )
            return await _call(timeout_seconds * 2)      # D-07 widened retry
    except Exception as e:
        # Wrap any failure (network, validation, tool_use-missing, etc.) as AIParseError so
        # bootstrap_user's SAVEPOINT-based fail-open path catches one well-defined exception type.
        raise AIParseError(f"Sonnet onboarding parse failed: {type(e).__name__}: {e}") from e


def _format_user_message(raw_input: dict) -> str:
    """Format the user's raw text into the Sonnet message content.

    Prompt-injection mitigation: wraps user text with static labels. The system prompt
    establishes that user text is DATA not INSTRUCTIONS. Acceptable for POC scope.
    """
    can_play = raw_input.get("can_play", "") or "(none)"
    working = raw_input.get("working_on", "") or "(none)"
    aspire = raw_input.get("aspirational", "") or "(none)"
    return (
        f"Songs the user says they can play:\n{can_play}\n\n"
        f"Songs the user is working on:\n{working}\n\n"
        f"Songs the user aspires to play:\n{aspire}\n\n"
        f"Emit the structured onboarding output now."
    )
