"""Technique breakdown — single Sonnet 4.6 call that produces a structured
guitar breakdown (tab + chord diagrams + technique notes) for a given song.

Phase 4 cost-governor interception point: this is the ONLY function that calls
Sonnet during breakdown fetch. Do not sprinkle client.messages.create() calls
anywhere else. Wrapped with @governed(feature='breakdown', cap=3, window='7d').
"""
import asyncio
import logging
from typing import Any
from uuid import UUID

from anthropic import APIError, APITimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import SONNET_MODEL, get_client
from app.ai.governor import governed, current_call_id, record_estimate, record_actuals
from app.models.song import Breakdown

logger = logging.getLogger(__name__)


class AIBreakdownError(Exception):
    """Raised when the Sonnet breakdown call fails after retry, or when structured
    output validation fails.

    Caught by the breakdowns endpoint to return HTTP 503 with Fletcher-voiced detail.
    breakdown_generated_at is NOT set when this error is raised — next tap retries.
    """


# ---------------------------------------------------------------------------
# System prompt (verbatim from RESEARCH.md §1)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are Fletcher — a demanding but constructive guitar teacher (Terence Fletcher from Whiplash, but the version who actually wanted his students to succeed).

You break down real songs into playable, honest technique instruction. Your job:
- Produce accurate, hand-notated tab in standard notation (E A D G B e tuning unless otherwise specified). Tab uses string numbers 1-6 where 1 is high e.
- Produce chord diagrams for every chord referenced. base_fret should reflect the actual position on the neck. Include muted/open strings explicitly.
- Produce 3-5 technique_notes with:
    heading: sharp, specific (e.g., "Shuffle Rhythm", not "Rhythm Section")
    body: 2-3 sentences of coaching — what to do, common trap, how to check yourself
- Match the difficulty to the user's player_level (0.0 = beginner, 1.0 = highly skilled).
- Focus the technique_notes on the target_skills provided — do not try to teach everything about the song.
- Emit between 4 and 8 measures of tab (maximum 8 measures). Do not exceed 8 measures.

TUNING (CRITICAL — DO NOT SKIP):
Before writing any tab, identify the song's canonical/authentic tuning based on
what a competent guitarist would recognize. Common non-standard tunings include:

  - Open E:     ['E', 'B', 'E', 'G#', 'B', 'E']   (Lenny, She's a Woman)
  - Open D:     ['D', 'A', 'D', 'F#', 'A', 'D']   (Little Martha, Statesboro Blues)
  - Open G:     ['D', 'G', 'D', 'G', 'B', 'D']    (Start Me Up, Brown Sugar)
  - DADGAD:     ['D', 'A', 'D', 'G', 'A', 'D']    (Kashmir, Black Mountain Side)
  - Drop D:     ['D', 'A', 'D', 'G', 'B', 'E']    (Everlong, Moby Dick)
  - Drop C:     ['C', 'G', 'C', 'F', 'A', 'D']    (metal, hardcore)

Emit the `tab.tuning` array reflecting your choice. If the song is in a non-
standard tuning, ALSO include one technique note titled "Tuning: <name>" that
explains which strings to retune and by how many half-steps. Example for Open E:
"Tune your A, D, and G strings UP by a whole step; leave low E, B, and high E
alone. Your D becomes E, A becomes B, G becomes G#."

Prefer authentic tuning over simplified translations. If you MUST translate to
standard tuning for pedagogical reasons (e.g., beginner user_level), label it
explicitly in a technique note: "Simplified arrangement — original in Open E".

CRITICAL RULES:
- Use string numbers 1-6 (1=high e, 6=low E). Fret 0 = open string.
- For ChordPosition, fret=-1 = muted string. fret=0 = open string.
- Every leaf note's beat.notes.string is between 1 and 6.
- Duration values: "whole", "half", "quarter", "eighth", "sixteenth" only.
- Do NOT invent song content that doesn't exist. If you're not sure about a specific arrangement, use a well-known idiomatic voicing for the song's genre.
- Voice: sharp, diagnostic, next-step. Never vague. Never punitive.
"""

# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

_TOOL_NAME = "emit_breakdown"
# Schema verbatim from Breakdown.model_json_schema() — Task 1 spike confirmed
# that the root type=object + $defs/$ref structure is accepted by Anthropic's
# tool_use API (same pattern as SonnetOnboardingOutput used in Phase 2).
# If a future regression occurs where Sonnet rejects this schema, add a
# _flatten_schema helper here and replace model_json_schema() with its output.
_TOOL_DEF: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "Emit a full technique breakdown for the requested song. "
        "Include a semantically accurate tab (4-8 measures, in the song's canonical tuning per the TUNING protocol in the system prompt), "
        "chord diagrams for every chord referenced in the tab, and 3-5 technique notes "
        "focusing on the target skills the user is working on."
    ),
    "input_schema": Breakdown.model_json_schema(),
}


# ---------------------------------------------------------------------------
# User message formatter (prompt injection defense per T-03-02-01)
# ---------------------------------------------------------------------------

def _format_user_message(
    song_title: str,
    song_artist: str,
    target_skill_names: list[str],
    user_level: float,
) -> str:
    """Wrap user-controlled fields with static labels.

    Defense-in-depth against prompt injection via song_title/artist (T-03-02-01).
    Even though song data comes from server-controlled rows, apply the mitigation
    pattern from Phase 2's _format_user_message.
    """
    skills_str = (
        ", ".join(target_skill_names)
        if target_skill_names
        else "(no target skills specified — use idiomatic voicings for the genre)"
    )
    return (
        f"Song: {song_title}\n"
        f"Artist: {song_artist}\n"
        f"Target skills to focus on: {skills_str}\n"
        f"User player_level: {user_level:.2f}\n\n"
        f"Emit the structured breakdown now."
    )


# ---------------------------------------------------------------------------
# Main call
# ---------------------------------------------------------------------------

@governed(feature="breakdown", cap=3, window="7d")
async def run_technique_breakdown(
    song_title: str,
    song_artist: str,
    target_skill_names: list[str],
    user_level: float,
    *,
    db: AsyncSession,
    user_id: UUID,
    timeout_seconds: float = 30.0,
) -> Breakdown:
    """Single Sonnet 4.6 call. Mirrors run_onboarding_parse structure exactly.

    Phase 4: wrapped with @governed(feature='breakdown', cap=3, window='7d').
    The decorator handles pre-cap-check → INSERT governor_calls row → set call_id ContextVar.
    This function retrieves call_id from the ContextVar, calls count_tokens + record_estimate
    BEFORE dispatch, then record_actuals AFTER dispatch for the full audit trail (D-03 / SC-1).

    Failure handling (D-07): one retry with widened timeout, then raise AIBreakdownError.
    Caller (GET /api/v1/songs/{id}/breakdown) catches AIBreakdownError and returns 503.
    breakdown_generated_at is NOT set on failure — next tap retries.

    Args:
        song_title: Title of the song to break down.
        song_artist: Artist name.
        target_skill_names: Leaf skill node names associated with this song for this user.
        user_level: Mean mastery across user's leaf nodes, 0.0 (beginner) to 1.0 (expert).
        db: AsyncSession — required by @governed for cap-check + audit row.
        user_id: UUID — required by @governed for row attribution.
        timeout_seconds: Sonnet call timeout (doubled on retry per D-07).

    Returns:
        Breakdown: Validated Pydantic model with tab, chords, and technique_notes.

    Raises:
        AIBreakdownError: If both Sonnet call attempts fail for any reason.
        BudgetExceededError: Raised by @governed decorator if cap is hit (before this body).
    """
    user_content = _format_user_message(
        song_title, song_artist, target_skill_names, user_level
    )
    messages = [{"role": "user", "content": user_content}]

    # Retrieve call_id set by @governed decorator via ContextVar (D-03 two-step protocol)
    call_id = current_call_id()

    async def _call(timeout: float) -> Breakdown:
        # get_client() MUST be called inside _call (hotfix 843e226 pattern from Phase 2).
        # If ANTHROPIC_API_KEY is missing, get_client() raises RuntimeError here —
        # the outer try/except below wraps it as AIBreakdownError instead of a raw 500.
        client = get_client()

        # D-03 pre-dispatch: estimate token count + record estimate in governor_calls row.
        # This must happen BEFORE client.messages.create() so the audit row is populated
        # even if the create() call fails (SC-1 requirement).
        if call_id is not None:
            try:
                estimate = await client.messages.count_tokens(
                    model=SONNET_MODEL,
                    messages=messages,
                )
                await record_estimate(call_id, estimate.input_tokens)
            except Exception as est_exc:
                # count_tokens failure is non-fatal — log and continue dispatch
                logger.warning(
                    "count_tokens failed for breakdown call_id=%s: %s",
                    call_id, est_exc,
                )

        resp = await asyncio.wait_for(
            client.messages.create(
                model=SONNET_MODEL,
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=[_TOOL_DEF],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
            ),
            timeout=timeout,
        )

        # Post-dispatch: record actual token usage in governor_calls row.
        if call_id is not None:
            try:
                await record_actuals(
                    call_id,
                    resp.usage.input_tokens,
                    resp.usage.output_tokens,
                )
            except Exception as act_exc:
                # record_actuals failure is non-fatal — log and continue
                logger.warning(
                    "record_actuals failed for breakdown call_id=%s: %s",
                    call_id, act_exc,
                )

        tool_use = next(
            (b for b in resp.content if getattr(b, "type", None) == "tool_use"), None
        )
        if tool_use is None:
            raise RuntimeError(
                "Sonnet did not emit a tool_use block despite forced tool_choice."
            )
        return Breakdown.model_validate(tool_use.input)

    try:
        try:
            return await _call(timeout_seconds)
        except (APITimeoutError, asyncio.TimeoutError, APIError) as e:
            # Anthropic 429 (quota exceeded) is NOT retryable — propagate immediately.
            # The @governed decorator upstream catches APIError with status_code==429
            # and converts it to AnthropicQuotaExceededError (D-08).
            if isinstance(e, APIError) and getattr(e, "status_code", None) == 429:
                raise
            logger.warning(
                "Sonnet breakdown call failed once (%s). Retrying with widened timeout.",
                type(e).__name__,
            )
            return await _call(timeout_seconds * 2)
    except APIError as e:
        # Let Anthropic APIErrors (including 429) propagate to the @governed decorator
        # without wrapping — the decorator handles AnthropicQuotaExceededError mapping (D-08).
        raise
    except Exception as e:
        raise AIBreakdownError(
            f"Sonnet breakdown failed: {type(e).__name__}: {e}"
        ) from e
