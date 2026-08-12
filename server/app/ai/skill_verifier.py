"""Skill node verifier — single Sonnet call that validates a proposed skill node
against the canonical taxonomy and existing nodes.

Phase 4 interception point: wrapped in @governed(feature='skill_verify', cap=None).
Only called when rapidfuzz score < 70 (new proposal, not a near-duplicate).
"""
import asyncio
import logging
from typing import Any, Literal
from uuid import UUID

from anthropic import APIError, APITimeoutError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import SONNET_MODEL, get_client
from app.ai.governor import governed, current_call_id, record_estimate, record_actuals
from app.ai.onboarding import FIXED_ROOTS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error class
# ---------------------------------------------------------------------------

class AISkillVerifierError(Exception):
    """Raised when the Sonnet skill-verify call fails after retry.

    Caught by the verifier pipeline in run_onboarding_parse to trigger graceful
    degradation of the proposal (D-14: onboarding still succeeds; proposal is
    queued as 'uncertain' for curator review).
    """


# ---------------------------------------------------------------------------
# Pydantic output model
# ---------------------------------------------------------------------------

class SkillNodeVerifyOutput(BaseModel):
    """Structured output from run_skill_node_verify.

    verdict: 'yes' = insert as new canonical; 'no' = drop + log rejection;
             'uncertain' = queue for curator review.
    root: which of the 6 fixed roots this skill belongs to (None only for verdict='no').
    reason: one-sentence explanation of the verdict.
    """
    verdict: Literal["yes", "no", "uncertain"]
    root: Literal["Rhythm", "Lead", "Chord Voicings", "Fingerstyle", "Music Theory", "Timing"] | None
    reason: str


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    f"You are a guitar teaching assistant validating proposed skill-tree node names.\n\n"
    f"The fixed root taxonomy is exactly: {', '.join(FIXED_ROOTS)}.\n\n"
    "For each proposed skill name you receive, you must judge whether it:\n"
    "  (a) IS a legitimate, specific guitar-technique concept that belongs in the skill tree "
    "→ verdict='yes'\n"
    "  (b) is NOT a guitar skill (too vague, not technique-related, or a near-duplicate of a "
    "name already in the candidate list) → verdict='no'\n"
    "  (c) might be valid but is genuinely ambiguous (could fit under multiple roots, or you "
    "are unsure whether it is distinct enough) → verdict='uncertain'\n\n"
    "For verdict='yes' or 'uncertain', assign the single best root from the fixed taxonomy.\n"
    "For verdict='no', set root=null.\n"
    "Always provide a concise one-sentence reason.\n\n"
    "CRITICAL: output via the emit_skill_verify tool only. Do not emit free text."
)


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

_TOOL_NAME = "emit_skill_verify"
_TOOL_DEF: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": "Emit a verdict on whether the proposed skill node belongs in the canonical graph.",
    "input_schema": SkillNodeVerifyOutput.model_json_schema(),
}


# ---------------------------------------------------------------------------
# User message formatter (prompt-injection defense T-04-03-01)
# ---------------------------------------------------------------------------

def _format_user_message(
    proposed_name: str,
    existing_canonical_names: list[str],
) -> str:
    """Wrap user-derived inputs with static labels.

    Defense-in-depth: proposed_name comes from Sonnet-onboarding output which is
    server-mediated but ultimately user-derived via raw_onboarding_text. Wrapping
    with static labels prevents injection into the verifier's verdict logic.
    Structured tool-use further constrains the response shape so injections cannot
    hijack the verdict field (T-04-03-01).
    """
    candidates_str = (
        "\n".join(f"  - {name}" for name in existing_canonical_names)
        if existing_canonical_names
        else "  (none — this is a fresh taxonomy)"
    )
    return (
        f"Proposed skill name:\n{proposed_name}\n\n"
        f"Existing canonical names at the same level (for near-duplicate check):\n"
        f"{candidates_str}\n\n"
        f"Emit the verdict now."
    )


# ---------------------------------------------------------------------------
# Main call
# ---------------------------------------------------------------------------

@governed(feature="skill_verify", cap=None)
async def run_skill_node_verify(
    proposed_name: str,
    existing_canonical_names: list[str],
    *,
    db: AsyncSession,
    user_id: UUID,
    timeout_seconds: float = 15.0,
) -> SkillNodeVerifyOutput:
    """Single Sonnet call to validate a proposed skill_node name (D-10).

    Mirrors run_technique_breakdown structure verbatim:
    - get_client() called INSIDE _call closure (hotfix pattern from breakdown.py)
    - count_tokens pre-dispatch → record_estimate (D-03)
    - record_actuals post-dispatch for full audit
    - One retry with doubled timeout on transient failure
    - Final failure wraps as AISkillVerifierError (caught by pipeline, D-14)

    Args:
        proposed_name: The skill name to validate (from Sonnet onboarding output).
        existing_canonical_names: Names of existing canonical nodes at the same level.
        db: AsyncSession — required by @governed for audit row.
        user_id: UUID — required by @governed for row attribution.
        timeout_seconds: Per-attempt timeout (doubled on retry).

    Returns:
        SkillNodeVerifyOutput with verdict + root + reason.

    Raises:
        AISkillVerifierError: If both Sonnet call attempts fail.
    """
    user_content = _format_user_message(proposed_name, existing_canonical_names)
    messages = [{"role": "user", "content": user_content}]

    # Retrieve call_id set by @governed decorator via ContextVar (D-03 two-step protocol)
    call_id = current_call_id()

    async def _call(timeout: float) -> SkillNodeVerifyOutput:
        # get_client() MUST be called inside _call (hotfix pattern per breakdown.py).
        # If ANTHROPIC_API_KEY is missing, RuntimeError is wrapped as AISkillVerifierError
        # by the outer except block — triggers D-14 graceful degradation.
        client = get_client()

        # D-03 pre-dispatch: estimate token count + record estimate in governor_calls row.
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
                    "count_tokens failed for skill_verify call_id=%s: %s",
                    call_id,
                    est_exc,
                )

        resp = await asyncio.wait_for(
            client.messages.create(
                model=SONNET_MODEL,
                max_tokens=1024,  # verdicts are short — much smaller than breakdown's 8192
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
                logger.warning(
                    "record_actuals failed for skill_verify call_id=%s: %s",
                    call_id,
                    act_exc,
                )

        tool_use = next(
            (b for b in resp.content if getattr(b, "type", None) == "tool_use"), None
        )
        if tool_use is None:
            raise RuntimeError(
                "Sonnet did not emit a tool_use block despite forced tool_choice."
            )
        return SkillNodeVerifyOutput.model_validate(tool_use.input)

    try:
        try:
            return await _call(timeout_seconds)
        except (APITimeoutError, asyncio.TimeoutError, APIError) as e:
            # Anthropic 429 is not retryable — propagate immediately so @governed
            # maps it to AnthropicQuotaExceededError (D-08 pattern from breakdown.py)
            if isinstance(e, APIError) and getattr(e, "status_code", None) == 429:
                raise
            logger.warning(
                "Sonnet skill_verify call failed once (%s). Retrying with widened timeout.",
                type(e).__name__,
            )
            return await _call(timeout_seconds * 2)
    except APIError as e:
        # Let Anthropic APIErrors (including 429) propagate to the @governed decorator
        # without wrapping — the decorator handles AnthropicQuotaExceededError mapping.
        raise
    except Exception as e:
        raise AISkillVerifierError(
            f"Sonnet skill_verify failed: {type(e).__name__}: {e}"
        ) from e
