"""Technique breakdown — single Sonnet 4.6 call that produces a structured
guitar breakdown (tab + chord diagrams + technique notes) for a given song.

Phase 4 cost-governor interception point: this is the ONLY function that calls
Sonnet during breakdown fetch. Do not sprinkle client.messages.create() calls
anywhere else. Wrapped with @governed(feature='breakdown', cap=BREAKDOWN_CAP).
"""
import asyncio
import logging
from typing import Any
from uuid import UUID

import pydantic
from anthropic import APIError, APITimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import SONNET_MODEL, get_client
from app.ai.governor import (
    BREAKDOWN_CAP,
    current_call_id,
    governed,
    record_actuals,
    record_estimate,
)
from app.ai.tab_verification import verify as verify_tab
from app.models.song import Breakdown, enforce_song_specific

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output token ceiling (FLE-43)
# ---------------------------------------------------------------------------
# The old ceiling of 8192 was never sized against the schema — it is a habitual
# default, not a measured number. The FLE-17 §3 eval re-run (2026-09-16) proved
# it too small: `Wonderwall` returned a tool_use input containing only `tab`, cut
# off mid-note, and the endpoint 503'd on a pydantic "chords / technique_notes
# Field required" that described the symptom and hid the cause.
#
# Sized against the WORST case, not the average:
#   - Wonderwall is a strummed 5-6 note chord on every eighth note: ~8 measures
#     x 8 beats x 6 notes = ~380 note objects. That tab alone consumed the entire
#     8192 budget before a single chord diagram was emitted.
#   - The four songs of that run that DID succeed averaged 5,669 output tokens
#     for the full schema (tab + chords + technique_notes + drills).
#   - So a dense song needs roughly 8,000 for the tab plus 4,000-6,000 for
#     chords/technique_notes/drills. Call the worst case ~14,000.
#
# 20,000 is ~1.4x that worst case. It is deliberately not larger: the Anthropic
# SDK REFUSES a non-streaming request whose max_tokens implies more than 10
# minutes of generation (_base_client._calculate_nonstreaming_timeout), which
# puts a hard cliff at 21,333 for a 128K-output model — and it raises ValueError,
# which this module's generic handler would bury as a nondescript
# AIBreakdownError. Going above 20,000 means switching this call to
# client.messages.stream() first. tests/test_breakdown_truncation.py guards it.
_MAX_OUTPUT_TOKENS = 20000

# Anthropic SDK cliff: non-streaming max_tokens above this raises ValueError,
# from 60*60 * max_tokens / 128_000 > 60*10. Named so the test can assert on it.
_SDK_NONSTREAMING_MAX_TOKENS = 21333


# ---------------------------------------------------------------------------
# Call timeout (FLE-18, re-sized by FLE-43)
# ---------------------------------------------------------------------------
# A real breakdown-with-drills call measured 72.1s mean across the 5 songs of the
# Phase 4.1 eval (360.4s total — .planning/phases/04.1-ai-drills/
# 04.1-05-EVAL-RAW-OUTPUT.txt:2072). The original default of 30.0s — doubled to
# 60.0s by the single retry below — sat UNDER that mean, so every cache-miss
# breakdown timed out twice and returned 503. The first eval run demonstrated this
# exactly: 5/5 TimeoutError in 453.3s (~5 x (30+60)).
#
# FLE-18 then set 150s against that 72.1s mean. Raising the token ceiling voids
# that margin, because a bigger budget means a longer generation: the eval's
# 5,669 tokens in ~75.9s is ~75 output tok/s, so a response that runs to the new
# 20,000 ceiling takes ~267s and would have been killed at 150s — burning a full
# Sonnet call and its governor row before the retry even started.
#
# 300s is therefore picked to COVER the token ceiling (20,000 / 75 tok/s = 267s),
# which is the invariant worth holding: nothing the model is allowed to generate
# can be cut off by our own clock. The retry doubles to 600s. This is a bound,
# not a wait — ordinary songs still return in ~75s, and only a pathologically
# dense song goes anywhere near it.
_DEFAULT_TIMEOUT_SECONDS = 300.0


class AIBreakdownError(Exception):
    """Raised when the Sonnet breakdown call fails after retry, or when structured
    output validation fails.

    Caught by the breakdowns endpoint to return HTTP 503 with Fletcher-voiced detail.
    breakdown_generated_at is NOT set when this error is raised — next tap retries.
    """


class TuningMismatchError(AIBreakdownError):
    """Sonnet emitted a tab.tuning that disagrees with song_catalog's verified tag.

    FLE-33: when `known_tuning` is passed, the user message states the catalog's
    ground-truth tuning instead of asking Sonnet to infer it (the inference path
    is what produced the Lenny incident — .planning/investigations/
    260908-sonnet-tuning-quality.md, where Sonnet silently re-voiced an Eb-tuned
    SRV tune into standard EADGBE). If Sonnet still emits a different tuning,
    the tab's note positions were very likely computed for the WRONG tuning —
    overwriting just the `tuning` label would leave frets that are correct for
    a tuning the response no longer claims. So this does not attempt to repair
    the output; it refuses to serve it, same contract as BreakdownTruncatedError:
    breakdown_generated_at is NOT set, and the next tap retries with a fresh
    (non-deterministic) Sonnet call rather than replaying a certain failure.
    """


class TabVerificationError(AIBreakdownError):
    """Sonnet's tab/chords failed a recording-free mechanical check (FLE-106).

    app.ai.tab_verification.verify() runs checks that need no reference
    recording because they are wrong against notation, against the guitar
    itself, or against the chords this SAME breakdown declares — unplayable
    beats, bars that don't add up, measures too wide for the mobile renderer,
    a note outside every chord the breakdown names. Same contract as
    TuningMismatchError and BreakdownTruncatedError: breakdown_generated_at is
    NOT set, so the next tap retries with a fresh (non-deterministic) Sonnet
    call rather than persisting output already known to be defective.
    """


class BreakdownTruncatedError(AIBreakdownError):
    """Sonnet hit max_tokens mid-emit, so the tool_use input is a partial object.

    FLE-43 ask #3: a truncated tool_use used to surface as a pydantic
    "chords / technique_notes Field required", which names the missing keys and
    says nothing about WHY they are missing. Raising this instead puts the cause
    — ran out of output budget — in the message and the log.

    Subclasses AIBreakdownError deliberately: the endpoint's 503 handling and the
    "do not set breakdown_generated_at" contract are unchanged, so the next tap
    still retries. Not retried in-process — truncation is deterministic at a
    fixed max_tokens, so a second identical call would truncate identically.
    """


# ---------------------------------------------------------------------------
# System prompt (verbatim from RESEARCH.md §1)
# ---------------------------------------------------------------------------
# FLE-44: the DRILLS block's SONG_SPECIFIC paragraph is a HINT, not the guarantee.
# Sonnet ignored it on Kashmir even in a maximally-mechanical wording, so
# enforce_song_specific() corrects the flag in code after parsing (see _call
# below). Keep the paragraph — it steers the `what` copy and costs nothing — but
# do not add a third restatement of the OLD wording; that approach is spent.
#
# FLE-89's measurement overturned the premise of that old wording: 8 of 16
# cached drills already quoted the song for 3+ consecutive beats, so the
# anti-slice self-check was not binding. It was, however, forbidding-by-
# incentive — the model was writing around naming the song in `what` to avoid
# the song_specific trip-wire, which is what made the drills read as bland
# generic exercises even when the notes WERE the song. FLE-99 drops the
# anti-slice rule and its self-check, requires every drill to name its passage,
# and rewords SONG_SPECIFIC to key off whether the mechanic is foundational —
# not off whether the prose happens to contain the title. enforce_song_specific()
# stays as the code-level backstop for the false→true direction; it still fires
# correctly since passage-naming does not require restating the song's title.
#
# FLE-45 replaced the DRILL ORDER five-dimension dominance rule with MECHANIC
# TIERS. The dominance rule ("no dimension may decrease, at least one must
# increase") was UNSATISFIABLE: all four gradeable songs of the 04.1-07 re-run
# failed it, and two of those failures were the rule's fault, not the model's.
# Real difficulty progressions TRADE dimensions — Little Wing's hardest drill is
# a bass-chord-melody roll that sounds FEWER strings at once than the drill
# before it. Forbidding every trade forbids most correct orderings. The tier list
# is one ordinal instead of five, and permits trades within a tier.
#
# Do not "improve" this by computing difficulty from the snippet's geometry. That
# is the shared root cause of both failed attempts (04.1-05's additive formula
# and 04.1-06's dominance rule): difficulty is not a function of the notes'
# geometry, so the model declares its tier and the eval grades the declaration.
#
# NOTE: this string is the prompt the 04.1-07 eval graded, so edits invalidate
# gate-by-gate comparison against that run's numbers.

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

SONG_SPECIFIC — judge the MECHANIC, not the prose:
Every drill's `what` names the passage it prepares (see NAME THE PASSAGE
below), so the song's title or artist may legitimately show up in `what`
regardless of this flag. Do not scan `what` for that text, and do not key
song_specific off whether it appears there. `song_specific` is true when the
drill only makes sense anchored to THIS song's actual passage — the
tab_snippet IS that passage, simplified (see SIMPLIFY below) — and would
mean nothing detached from this recording. It is false only when the drill
teaches a foundational mechanic any guitarist could practice on any song in
this position; the passage you named is the example that motivated the
drill, not the thing being drilled.

For `tab_snippet`: SIMPLIFY the actual passage this drill prepares — the
same notes the main tab plays at that moment — until it isolates ONE
mechanic. Simplify by removing, never by substituting: drop strings, drop
beats, slow the subdivision, hold a shape longer, cut the passage down to
its hardest moment. Quoting the song here is the point, not something to
dodge — a drill that doesn't sound like the passage it claims to prepare
teaches the user the wrong thing. 1-2 measures maximum, one mechanic per
drill.

NAME THE PASSAGE: `what` MUST say which moment of the song this drill
prepares — "the turnaround before the second verse", "the bridge's barre
run", "the intro's hammer-on lick" — never just the mechanic in the
abstract. A user reading `what` should know exactly where in the song this
drill pays off, every time, whether or not `song_specific` is true for it.

NECK REGION — drill the mechanic where the song actually plays it:
Every drill prepares one specific passage of the main tab. Before writing
the snippet, find the lowest and highest fretted number used in that
passage, ignoring open strings. At least one fretted note in your snippet
MUST fall inside that fret span, inclusive. If the passage is played at
frets 7-10, do not drill the same idea as an open-position shape at frets
0-2: the shape, the stretch and the hand position are all different, so
the user would rehearse something the song never asks for. Move the
exercise up to the song's position instead. If that makes it too hard for
the player_level, simplify the MECHANIC at the correct position — fewer
strings, slower tempo, no shift — rather than relocating it down the neck.
The one exception is a passage that genuinely uses open strings for the
mechanic being drilled; then fret 0 is where the song lives.

Quoting the song here is expected — SIMPLIFY above already has you starting
from the song's own notes. If a simplification pass that drops notes also
drifts the snippet out of this fret span, put it back in the span rather
than keeping the drop.

MECHANIC TIERS — the ONE scale the drill order is checked on:
Every drill sits in exactly one tier. Read your own tab_snippet, find the
HARDEST moment in it, and match that moment to this ordered list:

  1. single sustained note — one fretted note at a time on one string, held
     or repeated. Slides, bends and vibrato on a single string live here.
  2. two-string alternation — still one note at a time, but the picking hand
     crosses between strings. Alternate-picked or rolled single notes.
  3. held shape struck — two or more strings sounded together, one shape,
     never changed. The work is in the attack, not the fretting hand.
  4. shape change — two or more strings together AND the fretting hand
     changes shape at least once, without leaving its position.
  5. shape change with position shift — as tier 4, and the hand also moves
     up or down the neck between shapes.
  6. polyphonic independence — a bass note, a chord and a melody must be
     voiced independently. Thumb-over bass under a moving melody,
     chord-melody, bass-chord rolls, walking bass under a held shape.

Emit `mechanic_tier` on every drill, and emit the drills in NON-DECREASING
tier order. That is the entire ordering rule.

EQUAL TIERS ARE ALLOWED, and are the right answer whenever a later drill
TRADES one difficulty for another instead of adding one. Real progressions
trade constantly, so do not distort a good drill to force the number up.

Do NOT reason about drill difficulty any other way. In particular,
difficulty is NOT the number of strings sounding and NOT the note count:
  - A tier 6 chord-melody roll may sound one string at a time and still be
    the hardest drill in the set. Fewer strings, more independence, harder.
  - Trading a position shift for a cross-string root change is a move
    WITHIN a tier, not a step backwards.
If two drills genuinely belong in the same tier, give them the same number
and move on. If a drill would go DOWN a tier from the one before it,
reorder the drills — do not rewrite a correct drill to fit the sequence.

PROSE MUST MATCH THE TAB. Before you emit a drill, read its `what` next to
its `tab_snippet` and check they describe the same exercise. If `what`
promises a chord, a barre, a voicing or a strum, then at least one beat of
the snippet MUST sound two or more strings together. A drill whose `what`
says "move between an E-shape barre at fret 5 and fret 7, then strike the
full chord" and whose snippet is four single notes on one string is broken:
the user would practise something the tab cannot produce. Fix the snippet
to match the promise, or rewrite the promise to match the snippet.

TEMPO IS NOT THE DIFFICULTY SIGNAL. Do not try to make start_bpm or
target_bpm rise across drills. A harder mechanic is often drilled SLOWER
than an easy one, and that is correct. Set each drill's tempo from its own
note density alone: start_bpm is a comfortable warmup for that specific
mechanic, target_bpm is a stretch 10-15 BPM higher, both in 5-BPM
increments to match the skill graph's 5-bpm bins. Repetitions: 6-12 per
tempo step.

KEEP THE LADDER SHORT. The user plays every 5-BPM rung from start_bpm to
target_bpm, `repetitions` times each, in one sitting, and cannot rate the
drill until the top rung is done. A +15 BPM stretch at 12 reps is 48 reps
of work — that is the CEILING, not the target. Prefer +10 at 8 reps. Never
buy difficulty by widening the span or raising the rep count; buy it with
the mechanic and the note density.

target_skill_temp_id MUST be one of the ids listed in the user message.
Do not invent ids. If none of the listed skills fit a drill you'd
naturally emit, skip that drill rather than mislabel it.
"""

# ---------------------------------------------------------------------------
# Known-tuning ground truth (FLE-33)
# ---------------------------------------------------------------------------
# song_catalog.tuning (migration 0007) carries the CANONICAL recorded tuning for
# 64 catalog songs — ground truth, not a guess. Until this map existed, nothing
# downstream ever read it: _ensure_catalog_song_as_user_song dropped it on the
# floor, and Sonnet kept inferring tuning from the title alone (the path that
# produced the Lenny incident above). When a caller passes `known_tuning` below,
# the user message STATES this table's label/notes instead of asking Sonnet to
# work it out, and the emitted tab.tuning is checked against `notes`.
#
# Keys MUST stay a superset-equal mirror of ALLOWED_TUNINGS in both
# 0007_song_catalog_tuning_and_expansion.py and
# 0014_songs_tuning.py — tests/test_alembic_0014.py checks all three against
# each other. `notes` is the low-to-high string array a correct tab.tuning
# should match (case/enharmonic-normalized by _normalize_tuning_notes before
# comparison, so "D#" and "Eb" are treated as equal).
TUNING_NOTE_MAP: dict[str, dict[str, Any]] = {
    "standard": {
        "label": "Standard",
        "notes": ["E", "A", "D", "G", "B", "E"],
        "retune": None,
    },
    "eb_standard": {
        "label": "E♭ standard (half step down)",
        "notes": ["Eb", "Ab", "Db", "Gb", "Bb", "Eb"],
        "retune": (
            "Tune every string DOWN a half step: low E→Eb, A→Ab, D→Db, G→Gb, "
            "B→Bb, high E→Eb."
        ),
    },
    "drop_d": {
        "label": "Drop D",
        "notes": ["D", "A", "D", "G", "B", "E"],
        "retune": "Tune only the low E string DOWN a whole step to D; A, D, G, B, high E stay the same.",
    },
    "drop_c": {
        "label": "Drop C",
        "notes": ["C", "G", "C", "F", "A", "D"],
        "retune": (
            "Tune every string DOWN a whole step from standard, then drop the "
            "low string a further whole step: low E→C, A→G, D→C, G→F, B→A, high E→D."
        ),
    },
    "open_d": {
        "label": "Open D",
        "notes": ["D", "A", "D", "F#", "A", "D"],
        "retune": (
            "Tune low E DOWN a whole step to D, G DOWN a half step to F#, and B "
            "DOWN a whole step to A; A and D strings stay the same."
        ),
    },
    "open_e": {
        "label": "Open E",
        "notes": ["E", "B", "E", "G#", "B", "E"],
        "retune": "Tune A, D, and G strings UP: A→B, D→E, G→G#; low E, B, high E stay the same.",
    },
    "open_g": {
        "label": "Open G",
        "notes": ["D", "G", "D", "G", "B", "D"],
        "retune": (
            "Tune low E, A, and high E strings DOWN a whole step: low E→D, A→G, "
            "high E→D; D, G, B stay the same."
        ),
    },
    "dadgad": {
        "label": "DADGAD",
        "notes": ["D", "A", "D", "G", "A", "D"],
        "retune": (
            "Tune low E, B, and high E strings DOWN a whole step: low E→D, B→A, "
            "high E→D; A, D, G stay the same."
        ),
    },
}

# Flat/sharp enharmonic equivalence so "D#" and "Eb" compare equal — Sonnet is
# not told which spelling to use, only which pitch.
_FLAT_TO_SHARP = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"}


def _normalize_tuning_notes(notes: list[str]) -> list[str]:
    out = []
    for n in notes:
        key = n.strip().upper().replace("♭", "B").replace("♯", "#")
        out.append(_FLAT_TO_SHARP.get(key, key))
    return out


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
    known_tuning: str | None = None,
) -> str:
    """Wrap user-controlled fields with static labels.

    Defense-in-depth against prompt injection via song_title/artist (T-03-02-01).
    Even though song data comes from server-controlled rows, apply the mitigation
    pattern from Phase 2's _format_user_message.

    Phase 4.1 (Plan 04.1-01 Task 2): `target_skills` is now a list of
    `{"id": "<uuid-str>", "name": "<skill name>"}` dicts (evolved from
    list[str]) so Sonnet can echo the exact id back in each drill's
    `target_skill_temp_id` field (Landmine #2 defense).

    FLE-33: `known_tuning` is the song_catalog.tuning tag (None for
    user-onboarded songs with no catalog row — the only case where Sonnet still
    infers tuning from the title, per the SYSTEM_PROMPT's TUNING protocol).
    When set, this states the ground truth as a fact rather than leaving it to
    inference — the SYSTEM_PROMPT's TUNING protocol is a fallback for songs
    without a catalog tag, not a second opinion to weigh against this one.
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

    if known_tuning is not None:
        info = TUNING_NOTE_MAP.get(known_tuning)
        if info is not None:
            retune_line = f" {info['retune']}" if info["retune"] else ""
            tuning_str = (
                f"KNOWN TUNING (verified ground truth from the catalog — this is "
                f"NOT a guess, do not infer a different one): {info['label']} "
                f"({', '.join(info['notes'])}, low to high).{retune_line}\n"
                f"Emit tab.tuning as exactly {info['notes']!r}. If this tuning is "
                f"non-standard, include the matching \"Tuning: {info['label']}\" "
                f"technique note.\n\n"
            )
        else:
            tuning_str = ""
    else:
        tuning_str = ""

    return (
        f"Song: {song_title}\n"
        f"Artist: {song_artist}\n"
        f"{skills_str}\n"
        f"User player_level: {user_level:.2f}\n\n"
        f"{tuning_str}"
        f"Emit the structured breakdown now."
    )


# ---------------------------------------------------------------------------
# Main call
# ---------------------------------------------------------------------------

# FLE-92: cap comes from governor.BREAKDOWN_CAP (5/user/local-day) rather than a
# literal. The literal 3 here was a second copy of the number that did not move
# when BREAKDOWN_CAP did, so the endpoint's pre-check and the decorator's
# authoritative check could disagree by two calls.
@governed(feature="breakdown", cap=BREAKDOWN_CAP)
async def run_technique_breakdown(
    song_title: str,
    song_artist: str,
    target_skills: list[dict],
    user_level: float,
    known_tuning: str | None = None,
    *,
    db: AsyncSession,
    user_id: UUID,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> Breakdown:
    """Single Sonnet 4.6 call. Mirrors run_onboarding_parse structure exactly.

    Phase 4: wrapped with @governed(feature='breakdown', cap=BREAKDOWN_CAP).
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
        known_tuning: song_catalog.tuning tag (FLE-33), or None for a song with no
            catalog row (user-onboarded) — the only case Sonnet still infers tuning.
            One of TUNING_NOTE_MAP's keys when set.
        db: AsyncSession — required by @governed for cap-check + audit row.
        user_id: UUID — required by @governed for row attribution.
        timeout_seconds: Sonnet call timeout (doubled on retry per D-07).

    Returns:
        Breakdown: Validated Pydantic model with tab, chords, and technique_notes.

    Raises:
        AIBreakdownError: If both Sonnet call attempts fail for any reason.
        TuningMismatchError: If known_tuning was given and the emitted tab.tuning
            disagrees with it (subclasses AIBreakdownError).
        BudgetExceededError: Raised by @governed decorator if cap is hit (before this body).
    """
    user_content = _format_user_message(
        song_title, song_artist, target_skills, user_level, known_tuning
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
                # Every prompt-shaping argument the create() call below passes
                # must also be passed here, or the estimate prices a payload we
                # never send. System prompt + tool schema dominate this prompt:
                # counting the user turn alone under-reported input by ~118x in
                # prod (56 estimated vs 6642 actual, FLE-96).
                estimate = await client.messages.count_tokens(
                    model=SONNET_MODEL,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tools=[_TOOL_DEF],
                    tool_choice={"type": "tool", "name": _TOOL_NAME},
                )
                # Passing the create() call's own max_tokens makes
                # governor_calls.dollars_estimated this call's worst-case cost
                # rather than its prompt cost alone (FLE-23 §4).
                await record_estimate(
                    call_id, estimate.input_tokens, _MAX_OUTPUT_TOKENS
                )
            except Exception as est_exc:
                # count_tokens failure is non-fatal — log and continue dispatch
                logger.warning(
                    "count_tokens failed for breakdown call_id=%s: %s",
                    call_id, est_exc,
                )

        resp = await asyncio.wait_for(
            client.messages.create(
                model=SONNET_MODEL,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=[_TOOL_DEF],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
            ),
            timeout=timeout,
        )

        # FLE-43 ask #1: log stop_reason on EVERY call, not just the failure path.
        # Nothing logged it before, which is why the Wonderwall truncation had to
        # be inferred from token arithmetic across the whole eval run instead of
        # read off one line. This is the diagnostic for the entire class of
        # "breakdown came back wrong" bugs — `tool_use` is the healthy value under
        # forced tool_choice, `max_tokens` means we ran out of output budget.
        stop_reason = getattr(resp, "stop_reason", None)
        output_tokens = getattr(getattr(resp, "usage", None), "output_tokens", None)
        logger.info(
            "Sonnet breakdown returned for song %r: stop_reason=%s output_tokens=%s "
            "max_tokens=%s",
            song_title, stop_reason, output_tokens, _MAX_OUTPUT_TOKENS,
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

        # FLE-43 ask #3: catch truncation HERE, where the cause is still known.
        # A tool_use block cut off at max_tokens still arrives, and its `input` is
        # a partial object — Wonderwall's held only `tab`. Letting that fall
        # through to model_validate produces "chords / technique_notes Field
        # required", which describes the missing keys and hides the reason. Check
        # AFTER record_actuals so the governor row still reflects the tokens we
        # were billed for.
        if stop_reason == "max_tokens":
            logger.error(
                "Sonnet breakdown TRUNCATED for song %r by artist %r: hit the "
                "max_tokens ceiling of %s (output_tokens=%s). The tool_use input is "
                "a partial object. This song is denser than the ceiling allows — "
                "raise _MAX_OUTPUT_TOKENS (see FLE-43 sizing note), which above "
                "%s requires switching this call to streaming.",
                song_title, song_artist, _MAX_OUTPUT_TOKENS, output_tokens,
                _SDK_NONSTREAMING_MAX_TOKENS,
            )
            raise BreakdownTruncatedError(
                f"Sonnet ran out of output budget for {song_title!r}: hit the "
                f"max_tokens ceiling of {_MAX_OUTPUT_TOKENS} mid-emit, so the "
                f"breakdown is incomplete."
            )

        tool_use = next(
            (b for b in resp.content if getattr(b, "type", None) == "tool_use"), None
        )
        if tool_use is None:
            raise RuntimeError(
                "Sonnet did not emit a tool_use block despite forced tool_choice "
                f"(stop_reason={stop_reason})."
            )
        # Landmine #3 soft-fail (Plan 04.1-01 Task 3): drills-shape violations
        # (min_length=2, max_length=4, per-drill target_bpm>start_bpm model_validator)
        # should NOT bubble as AIBreakdownError and 500 the endpoint. Strip the
        # drills key and re-parse — users still see tab/chords/technique_notes.
        # Structural failures in tab/chords/technique_notes (pre-existing Phase 3
        # failure surface) still propagate as ValidationError → AIBreakdownError.
        raw_input = tool_use.input
        try:
            parsed = Breakdown.model_validate(raw_input)
        except pydantic.ValidationError as ve:
            logger.warning(
                "Drills validation failed for song %r (stop_reason=%s, "
                "output_tokens=%s) — dropping drills and returning breakdown "
                "without drills (Landmine #3 soft-fail). ValidationError: %s",
                song_title, stop_reason, output_tokens, ve,
            )
            raw_input_no_drills = (
                {k: v for k, v in raw_input.items() if k != "drills"}
                if isinstance(raw_input, dict)
                else raw_input
            )
            # Second parse — if THIS fails, the outer try/except of run_technique_breakdown
            # wraps it as AIBreakdownError (real structural failure, not a drills problem).
            parsed = Breakdown.model_validate(raw_input_no_drills)

        # FLE-33: catch a silently re-voiced tab HERE, where we still know what
        # the catalog claims. known_tuning is None for songs with no catalog row
        # (user-onboarded) — that path is unchanged, Sonnet's inference stands.
        if known_tuning is not None:
            expected = TUNING_NOTE_MAP.get(known_tuning)
            if expected is not None:
                emitted_norm = _normalize_tuning_notes(parsed.tab.tuning)
                expected_norm = _normalize_tuning_notes(expected["notes"])
                if emitted_norm != expected_norm:
                    logger.error(
                        "Tuning mismatch for song %r by %r: catalog says %r (%s) "
                        "but Sonnet emitted tab.tuning=%r.",
                        song_title, song_artist, known_tuning, expected["notes"],
                        parsed.tab.tuning,
                    )
                    raise TuningMismatchError(
                        f"Sonnet emitted tab.tuning={parsed.tab.tuning!r} for "
                        f"{song_title!r}, but the catalog's verified tuning is "
                        f"{known_tuning!r} ({expected['notes']!r}). Not serving a "
                        f"silently re-voiced tab."
                    )

        # FLE-44: song_specific is enforced in code, not asked for in the prompt.
        # The SYSTEM_PROMPT DRILLS block still states the rule as a hint, but the
        # guarantee lives in this pass — the FLE-17 §3 eval re-run showed Sonnet
        # overriding the instruction with its own semantics (Kashmir D3/D4 named
        # the song in `what` and still emitted song_specific=false).
        #
        # This is the ONLY point where drills are born, so it is the only place the
        # normalisation is needed: run_technique_breakdown feeds both the live
        # endpoint (which persists the result to songs.breakdown) and
        # scripts/eval_drills.py. Rows cached BEFORE this fix keep whatever flag
        # Sonnet emitted — they are not re-validated on read.
        parsed = enforce_song_specific(parsed, song_title, song_artist)

        # FLE-106: the generator verification pass. Runs on every call, cached
        # or not — this is the only place a breakdown is born, same reasoning
        # as the song_specific enforcement above. A blocking defect raises
        # TabVerificationError here, before song.breakdown is ever assigned by
        # either write path (the live endpoint or breakdown_backfill.py), so a
        # tab that fails its own chord list or overflows the renderer is never
        # persisted. Warnings (today: only the C4 uniform-duration grid, a feel
        # judgement and not a notation error) are logged, not blocking.
        result = verify_tab(parsed)
        if result.warnings:
            logger.warning(
                "Breakdown for %r passed verification with %d warning(s): %s",
                song_title, len(result.warnings), "; ".join(result.warnings),
            )
        if result.blocking:
            logger.error(
                "Breakdown for %r by %r FAILED verification (%d defect(s)): %s",
                song_title, song_artist, len(result.blocking), "; ".join(result.blocking),
            )
            raise TabVerificationError(
                f"Sonnet's breakdown for {song_title!r} failed {len(result.blocking)} "
                f"recording-free check(s), not serving a defective tab: "
                f"{'; '.join(result.blocking)}"
            )

        return parsed

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
    except AIBreakdownError:
        # FLE-43: BreakdownTruncatedError is already the precise diagnosis. Re-wrapping
        # it in a generic AIBreakdownError would flatten it back into the vague message
        # this issue exists to remove. The endpoint catches the base class either way.
        raise
    except Exception as e:
        raise AIBreakdownError(
            f"Sonnet breakdown failed: {type(e).__name__}: {e}"
        ) from e
