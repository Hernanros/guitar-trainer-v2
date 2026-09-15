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

import pydantic
from anthropic import APIError, APITimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import SONNET_MODEL, get_client
from app.ai.governor import governed, current_call_id, record_estimate, record_actuals
from app.models.song import Breakdown

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Call timeout (FLE-18)
# ---------------------------------------------------------------------------
# A real breakdown-with-drills call measured 72.1s mean across the 5 songs of the
# Phase 4.1 eval (360.4s total — .planning/phases/04.1-ai-drills/
# 04.1-05-EVAL-RAW-OUTPUT.txt:2072). The previous default of 30.0s — doubled to
# 60.0s by the single retry below — sat UNDER that mean, so every cache-miss
# breakdown timed out twice and returned 503. The first eval run demonstrated this
# exactly: 5/5 TimeoutError in 453.3s (~5 x (30+60)).
#
# 150s is ~2.1x the measured mean. The eval recorded only the aggregate, not
# per-song times, so the slowest song is bounded rather than known: with 5 songs
# totalling 360.4s, the slowest cannot exceed ~140s even if the other four ran at
# an implausible ~55s floor. 150s clears that bound; the retry doubles to 300s,
# which is the value the eval harness proved sufficient via EVAL_TIMEOUT_SECONDS.
_DEFAULT_TIMEOUT_SECONDS = 150.0


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
what a competent guitarist would recognize.

TWO CATEGORIES to consider — check BOTH before defaulting to standard EADGBE:

(1) Half-step-down and whole-step-down tunings (very common in classic
    rock/blues — the "shape key vs. concert key" gap):

  - E♭ standard:  ['Eb', 'Ab', 'Db', 'Gb', 'Bb', 'Eb']
      (SRV — most of his catalog including Lenny, Pride and Joy, Texas Flood;
       Hendrix — Little Wing, Voodoo Child; Slash / GN'R; Van Halen — most tracks)
  - D standard:   ['D', 'G', 'C', 'F', 'A', 'D']
      (Alice in Chains — most catalog; Foo Fighters — some tracks;
       Motörhead; many modern rock/metal bands)

    A guitarist in these tunings still FINGERS "E-shape" or "G-shape" chords,
    but the guitar SOUNDS a half step or whole step lower. So SRV's Lenny is
    fingered in E shapes but sounds in E♭ concert. If you say a song is "in
    key of E" but it's played in E♭ standard, your tab notes are a half step
    off from the recording — a critical fidelity gap.

(2) Alternate/open tunings (used for specific arrangements):

  - Open E:     ['E', 'B', 'E', 'G#', 'B', 'E']   (Duane Allman — Statesboro Blues;
                                                     Bonnie Raitt — Something to Talk About)
  - Open D:     ['D', 'A', 'D', 'F#', 'A', 'D']   (Little Martha; some Joni Mitchell)
  - Open G:     ['D', 'G', 'D', 'G', 'B', 'D']    (Keith Richards signature —
                                                     Start Me Up, Brown Sugar, Honky Tonk Women)
  - DADGAD:     ['D', 'A', 'D', 'G', 'A', 'D']    (Kashmir, Black Mountain Side, Celtic)
  - Drop D:     ['D', 'A', 'D', 'G', 'B', 'E']    (Everlong, Moby Dick, Slither)
  - Drop C:     ['C', 'G', 'C', 'F', 'A', 'D']    (metal, hardcore — System of a Down)

Emit the `tab.tuning` array reflecting your choice. If the song is in ANY
non-standard tuning (including E♭ standard / D standard), ALSO include one
technique note titled "Tuning: <name>" that explains what to retune. Examples:

  - E♭ standard: "Tune every string DOWN by a half step. Low E → Eb, A → Ab,
    D → Db, G → Gb, B → Bb, high E → Eb. This is SRV's usual tuning — he
    fingers everything in E shapes but the guitar sounds a half step lower."
  - Open E: "Tune your A, D, and G strings UP by a whole step; leave low E,
    B, and high E alone. A → B, D → E, G → G#."

Prefer authentic tuning over simplified translations. If you MUST translate to
standard tuning for pedagogical reasons (e.g., beginner user_level), label it
explicitly in a technique note: "Simplified arrangement — original in <tuning>".

CRITICAL RULES:
- Use string numbers 1-6 (1=high e, 6=low E). Fret 0 = open string.
- For ChordPosition, fret=-1 = muted string. fret=0 = open string.
- Every leaf note's beat.notes.string is between 1 and 6.
- Duration values: "whole", "half", "quarter", "eighth", "sixteenth" only.
- Do NOT invent song content that doesn't exist. If you're not sure about a specific arrangement, use a well-known idiomatic voicing for the song's genre.
- Voice: sharp, diagnostic, next-step. Never vague. Never punitive.

DRILLS (produce 2-4):
After the main tab/chords/technique_notes, emit 2-4 short practice drills.
Each drill isolates ONE skill from the target_skills list and must be
playable in under 60 seconds per rep.

SONG_SPECIFIC — this is a mechanical test, not a judgement call:
If the drill's `what` text contains the song title or the artist name
anywhere, then `song_specific` MUST be true. There is no exception, and it
does not matter how incidental the mention is. If you intend a foundational
drill that any guitarist could use regardless of song, then do not mention
the song title or the artist in `what` at all — rewrite the copy to describe
the mechanic only. Scan `what` for both strings before you emit.

For `tab_snippet`: compose a CANONICAL EXERCISE SHAPE that isolates the
technique. It MUST NOT be a slice of the main song tab. The snippet should
be 1-2 measures maximum and exercise ONE mechanic per drill.

SELF-CHECK, run this on every drill before emitting it:
Compare the snippet against every measure of the main tab you just wrote.
Compare ONLY the ordered sequence of (string, fret) pairs — ignore the time
signature, ignore note durations, and ignore notes added or removed at
either end. If 3 or more consecutive (string, fret) pairs appear in the same
order in any main-tab measure, the snippet is a slice: rewrite it.
Relabelling the time signature, appending a note, or shifting by an octave
does NOT stop it being a slice.

BAD: `tab_snippet` is measures 3-4 from the main song tab.
BAD: main tab measure is [6/7] [5/9] [4/9] [3/8] [2/8] in 12/8, and the
     snippet is [6/7] [5/9] [4/9] [3/8] [2/8] [3/8] relabelled 4/4. Five
     pairs match in order. This is a slice wearing a disguise.
GOOD (single-note mechanic): `tab_snippet` is just the two-note slide on
     one string, played alone, no bass, no chord — isolated so the user
     drills the mechanic not the song.
GOOD (chord or barre mechanic): reduce the shape, never quote the song.
     Play only the two or three strings that carry the mechanic, or hold
     the full shape and drill the attack alone on a rhythm you invented.
     For an E-shape barre at position VII: [6/7] [5/9], then
     [6/7] [5/9] [4/9], then the full shape struck on each beat. Do not
     reproduce the song measure that contains that chord.

DRILL ORDER — easiest to hardest, defined so you can check it:
Rate every drill on these five dimensions, reading its own tab_snippet:
  (a) number of distinct fretted shapes
  (b) whether changing between shapes is required (no / yes)
  (c) the most strings sounded together in any one beat (1-6)
  (d) whether the fretting hand must shift position (no / yes)
  (e) whether the rhythm is even or displaced/syncopated (even / displaced)

Drill 1 MUST be the simplest mechanic you emit: one shape, no change, no
shift, even rhythm. Then, reading the drills in order, no dimension may go
DOWN from one drill to the next, and at least one dimension MUST go UP. If
your drills cannot be ordered that way, change the drills — simplify an
early one, or emit fewer — rather than emitting them out of order.
As a specific case of that rule: a drill that is one static shape must
NEVER appear after a drill containing a chord change.

TEMPO IS NOT THE DIFFICULTY SIGNAL. Do not try to make start_bpm or
target_bpm rise across drills. A harder mechanic is often drilled SLOWER
than an easy one, and that is correct. Set each drill's tempo from its own
note density alone: start_bpm is a comfortable warmup for that specific
mechanic, target_bpm is a stretch 10-40 BPM higher, both in 5-BPM
increments to match the skill graph's 5-bpm bins. Repetitions: 8-30 per
tempo step.

target_skill_temp_id MUST be one of the ids listed in the user message.
Do not invent ids. If none of the listed skills fit a drill you'd
naturally emit, skip that drill rather than mislabel it.
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
    target_skills: list[dict],
    user_level: float,
) -> str:
    """Wrap user-controlled fields with static labels.

    Defense-in-depth against prompt injection via song_title/artist (T-03-02-01).
    Even though song data comes from server-controlled rows, apply the mitigation
    pattern from Phase 2's _format_user_message.

    Phase 4.1 (Plan 04.1-01 Task 2): `target_skills` is now a list of
    `{"id": "<uuid-str>", "name": "<skill name>"}` dicts (evolved from
    list[str]) so Sonnet can echo the exact id back in each drill's
    `target_skill_temp_id` field (Landmine #2 defense).
    """
    if target_skills:
        skills_block = "\n".join(
            f"  - id={s['id']}  name={s['name']}" for s in target_skills
        )
        skills_str = (
            f"target_skills available:\n{skills_block}\n\n"
            f"target_skill_temp_id in each drill MUST be one of the ids listed above."
        )
    else:
        skills_str = "(no target skills specified — use idiomatic voicings for the genre)"
    return (
        f"Song: {song_title}\n"
        f"Artist: {song_artist}\n"
        f"{skills_str}\n"
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
    target_skills: list[dict],
    user_level: float,
    *,
    db: AsyncSession,
    user_id: UUID,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
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
        target_skills: Leaf skill nodes for this song+user, as list of
            {"id": "<uuid-str>", "name": "<skill name>"} dicts. Sonnet echoes
            the exact id in each drill's `target_skill_temp_id` (Plan 04.1-01
            Landmine #2 defense).
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
        song_title, song_artist, target_skills, user_level
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
        # Landmine #3 soft-fail (Plan 04.1-01 Task 3): drills-shape violations
        # (min_length=2, max_length=4, per-drill target_bpm>start_bpm model_validator)
        # should NOT bubble as AIBreakdownError and 500 the endpoint. Strip the
        # drills key and re-parse — users still see tab/chords/technique_notes.
        # Structural failures in tab/chords/technique_notes (pre-existing Phase 3
        # failure surface) still propagate as ValidationError → AIBreakdownError.
        raw_input = tool_use.input
        try:
            return Breakdown.model_validate(raw_input)
        except pydantic.ValidationError as ve:
            logger.warning(
                "Drills validation failed for song %r — dropping drills and returning "
                "breakdown without drills (Landmine #3 soft-fail). ValidationError: %s",
                song_title, ve,
            )
            raw_input_no_drills = (
                {k: v for k, v in raw_input.items() if k != "drills"}
                if isinstance(raw_input, dict)
                else raw_input
            )
            # Second parse — if THIS fails, the outer try/except of run_technique_breakdown
            # wraps it as AIBreakdownError (real structural failure, not a drills problem).
            return Breakdown.model_validate(raw_input_no_drills)

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
