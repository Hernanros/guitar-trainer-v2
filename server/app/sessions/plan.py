# server/app/sessions/plan.py
#
# PART I of the FLE-4 spec — session shape. Pure arithmetic, no DB, no I/O, no LLM.
#
# These are the functions the generator calls to decide the SKELETON of a session:
# how many seconds each block gets, how many drill slots fit, which mode the session
# runs in, and how many reps of a given drill actually fit a given time box.
#
# Everything here is deliberately free of SQLAlchemy and of `datetime.now()` so it
# can be unit-tested offline against the instantiated table in spec §3.1. Candidate
# selection (§5.1/§5.2/§11), the ladder controller (§7) and the drill taxonomy
# (§8/§9) land in sibling modules; they need schema FLE-8 has not shipped yet.
#
# Spec section references below are to the `Session Structure & Drill Taxonomy`
# document on FLE-4. Any change to a constant in this file is a GENERATOR_VERSION
# bump — old sessions have to stay explicable.

from __future__ import annotations

import enum
import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Optional, Sequence

GENERATOR_VERSION = "session-gen/1.0.0"

# --- §1 Constants -----------------------------------------------------------
# One block, tunable. These are the numbers to argue with after the pilot.
RUNG_STEP_BPM = 5
CLEARS_TO_PUSH = 2
MISSES_TO_DROP = 2
CLEAR_REP_THRESHOLD = 0.80
MAX_TECHNIQUE_SLOTS = 5
NOMINAL_SLOT_SECONDS = 240
MIN_SLOT_SECONDS = 150
MIN_PLANNED_REPS = 6
REP_DUTY_CYCLE = 0.75
LAYOFF_REENTRY_DAYS = 10
MASTERY_MAINTENANCE_DAYS = 14
BAIL_COMPLETION_THRESHOLD = 0.60
MAX_RATING_TAPS = 6
AUTO_ADVANCE_FACTOR = 1.5

# §0 — the supported session lengths. Deliberately NOT extended to 20; the brief's
# "20-minute session" is the T=15 shape. Adding a value is a migration plus a
# mobile picker change plus a re-onboarding question.
SUPPORTED_TARGET_MINUTES = (15, 30, 45, 60)

# §5.1 — warm-up is planned below the current rung, at reduced reps.
WARMUP_RUNG_OFFSET_BPM = 10
WARMUP_REP_FACTOR = 0.60


class SessionMode(str, enum.Enum):
    """§4 — the technique-vs-repertoire balance for one session."""

    BUILD = "BUILD"
    BALANCED = "BALANCED"
    PERFORM = "PERFORM"


class Block(str, enum.Enum):
    """§2 — the four phases, in fixed order. The generator never shuffles blocks."""

    WARMUP = "warmup"
    TECHNIQUE = "technique"
    REPERTOIRE = "repertoire"
    CONSOLIDATION = "consolidation"


# §3 / §5.3 — per-mode shares.
TECHNIQUE_SHARE = {
    SessionMode.BUILD: 0.60,
    SessionMode.BALANCED: 0.45,
    SessionMode.PERFORM: 0.30,
}
SECTION_SHARE = {
    SessionMode.BUILD: 0.65,
    SessionMode.BALANCED: 0.60,
    SessionMode.PERFORM: 0.40,
}

# §5.3 — section-work tempo is scaled down when the player isn't ready for the song.
_TEMPO_FACTOR_BANDS = ((0.40, 0.70), (0.70, 0.85))


def _round_half_up(value: float) -> int:
    """Round half away from zero.

    Python's built-in round() is banker's rounding, which makes round(0.5) == 0.
    A session budget that changes depending on whether a boundary lands on an even
    number is not reproducible in the sense the spec means, so all spec arithmetic
    goes through this instead.
    """
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def round_to_5(bpm: float) -> int:
    """§1 — every planned tempo is a multiple of 5, matching the skill graph's bins."""
    return _round_half_up(bpm / 5) * 5


# ---------------------------------------------------------------------------
# §3 — Time budget
# ---------------------------------------------------------------------------

class BlockBudget:
    """Seconds per block for one session. Blocks sum to exactly T x 60.

    At T=15 the consolidation block gets zero budget and is folded into the tail of
    the play-through item (§2, exception) — a 1-minute consolidation block is
    theatre. `folds_consolidation` is what the item builder keys off to set
    `is_consolidation` on the final 90s of the play-through.
    """

    __slots__ = ("target_minutes", "mode", "total", "warmup", "technique",
                 "repertoire", "consolidation")

    def __init__(self, target_minutes: int, mode: SessionMode, total: int,
                 warmup: int, technique: int, repertoire: int, consolidation: int):
        self.target_minutes = target_minutes
        self.mode = mode
        self.total = total
        self.warmup = warmup
        self.technique = technique
        self.repertoire = repertoire
        self.consolidation = consolidation

    @property
    def folds_consolidation(self) -> bool:
        return self.consolidation == 0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"BlockBudget(T={self.target_minutes}, mode={self.mode.value}, "
                f"W={self.warmup}, Tb={self.technique}, Rb={self.repertoire}, "
                f"C={self.consolidation})")


def block_budget(target_minutes: int, mode: SessionMode) -> BlockBudget:
    """§3 — split T minutes into the four block budgets, in seconds.

    Reproduces the §3.1 table exactly at T in {15, 30, 45, 60}; the model is
    continuous in T so a fifth session length would need no code change here.
    """
    if target_minutes <= 0:
        raise ValueError(f"target_minutes must be positive, got {target_minutes}")

    b = target_minutes * 60
    warmup = _clamp(_round_half_up(0.12 * b), 120, 360)
    consolidation = 0 if target_minutes == 15 else _clamp(_round_half_up(0.08 * b), 120, 300)
    core = b - warmup - consolidation
    technique = _round_half_up(core * TECHNIQUE_SHARE[mode])
    repertoire = core - technique
    return BlockBudget(target_minutes, mode, b, warmup, technique, repertoire, consolidation)


# ---------------------------------------------------------------------------
# §3.3 — Slot count
# ---------------------------------------------------------------------------

def technique_slots(technique_seconds: int, eligible_candidates: Optional[int] = None) -> int:
    """§3.3 — how many drill slots fit the technique block.

    MAX_TECHNIQUE_SLOTS is a hard cap regardless of T: past five technique items a
    session reads as a checklist. A 60-minute session buys LONGER slots, not more of
    them, which is also the better pedagogy — more reps per mechanic beats more
    mechanics.

    `eligible_candidates` is degradation step 1 (§12): a thin bank shrinks N rather
    than emitting placeholder items.
    """
    if technique_seconds < MIN_SLOT_SECONDS:
        n = 1
    else:
        n = _clamp(_round_half_up(technique_seconds / NOMINAL_SLOT_SECONDS), 1, MAX_TECHNIQUE_SLOTS)
        n = max(1, min(n, technique_seconds // MIN_SLOT_SECONDS))
    if eligible_candidates is not None:
        n = max(0, min(n, eligible_candidates))
    return n


def slot_seconds(technique_seconds: int, slots: int) -> int:
    """§3.3 — floor division; the remainder lands on the block's last item (§3)."""
    if slots <= 0:
        return 0
    return technique_seconds // slots


def has_stretch_slot(slots: int) -> bool:
    """§11.1 — the last slot is the stretch slot, but only if there's more than one.

    T=15 sessions have a single drill and no stretch slot: the one drill a short
    session gets should not be the hard one.
    """
    return slots > 1


def rating_tap_budget(slots: int) -> int:
    """§6 — taps land on technique items plus repertoire item 1, capped at 6.

    Returns how many technique slots get rated. If N + 1 would exceed the cap, the
    lowest-scored technique slots go unrated. Rating fatigue is a real failure
    mode: six taps is a session, twelve is a form.
    """
    return max(0, min(slots, MAX_RATING_TAPS - 1))


# ---------------------------------------------------------------------------
# §5.3 — Repertoire split
# ---------------------------------------------------------------------------

def repertoire_split(repertoire_seconds: int, mode: SessionMode) -> tuple[int, int]:
    """§5.3 — (section_work_seconds, play_through_seconds)."""
    section = _round_half_up(repertoire_seconds * SECTION_SHARE[mode])
    return section, repertoire_seconds - section


def section_tempo_factor(song_readiness: float) -> float:
    """§5.3 — slow the section down when the player is a long way off the song."""
    for threshold, factor in _TEMPO_FACTOR_BANDS:
        if song_readiness < threshold:
            return factor
    return 1.00


def section_bpm(song_bpm: float, song_readiness: float) -> int:
    return round_to_5(song_bpm * section_tempo_factor(song_readiness))


# ---------------------------------------------------------------------------
# §4 — Session mode
# ---------------------------------------------------------------------------

class ModeDecision:
    """The chosen mode plus the rule that chose it, so a session is explicable."""

    __slots__ = ("mode", "allow_push", "rule")

    def __init__(self, mode: SessionMode, allow_push: bool, rule: str):
        self.mode = mode
        self.allow_push = allow_push
        self.rule = rule

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, ModeDecision) and other.mode == self.mode
                and other.allow_push == self.allow_push and other.rule == self.rule)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ModeDecision({self.mode.value}, allow_push={self.allow_push}, rule={self.rule!r})"


def choose_mode(
    *,
    sessions_count: int,
    days_since_last: Optional[int],
    completion_3: Optional[float],
    player_level: float,
    weakest_root_mastery: Optional[float],
    song_readiness: Optional[float],
) -> ModeDecision:
    """§4 — evaluated once at generation time. FIRST MATCH WINS; the order is the rule.

    `days_since_last`, `completion_3`, `weakest_root_mastery` and `song_readiness`
    are None when there isn't enough history to compute them (no prior sessions,
    fewer than two terminal sessions, no leaf nodes under any root, no song skills).
    A None input never fires its rule — it falls through to the next one.

    `player_level` feeds rule 5's self-relative comparison, so it must be on the
    SAME scale as `weakest_root_mastery`: the raw, unfloored mean mastery over the
    player's leaf skill_nodes. It is NOT `selectors/player_level.floor_player_level`'s
    output — that floor exists to keep catalog selection sane for a brand-new player
    (FLE-49) and is a different question from "does this player have a hole
    relative to their own average" (FLE-59). Feeding the floored value here lifts
    only one side of the subtraction and manufactures a deficit that isn't there.
    """
    # 1 — the first session decides whether there's a third. Lead with music.
    if sessions_count < 2:
        return ModeDecision(SessionMode.PERFORM, True, "cold_start")
    # 2 — a week off means come back to playing, not to a test.
    if days_since_last is not None and days_since_last >= 7:
        return ModeDecision(SessionMode.PERFORM, False, "layoff")
    # 3 — bailing means LESS homework, not more. This is the loop that makes the
    #     generator respond to a player who keeps abandoning sessions.
    if completion_3 is not None and completion_3 < BAIL_COMPLETION_THRESHOLD:
        return ModeDecision(SessionMode.PERFORM, False, "bailing")
    # 4 — a song at 0.70 readiness is close; push it over the line.
    if song_readiness is not None and song_readiness >= 0.70:
        return ModeDecision(SessionMode.PERFORM, True, "song_nearly_ready")
    # 5 — 0.25 below the player's OWN average is a real hole, not noise. Both sides
    #     must be raw (unfloored) mastery averages — see the docstring above.
    if weakest_root_mastery is not None and player_level - weakest_root_mastery >= 0.25:
        return ModeDecision(SessionMode.BUILD, True, "root_deficit")
    return ModeDecision(SessionMode.BALANCED, True, "default")


# ---------------------------------------------------------------------------
# §5.5 — Sizing reps from the time box
# ---------------------------------------------------------------------------

# Notes-per-beat for each subdivision the Tab schema can express.
_SUBDIVISION_NOTES_PER_BEAT = {
    "whole": 0.25,
    "half": 0.5,
    "quarter": 1.0,
    "eighth": 2.0,
    "sixteenth": 4.0,
}


def _time_signature_numerator(measure: Mapping[str, Any]) -> int:
    """Beats per measure from a '4/4'-style time_signature. Defaults to 4."""
    raw = measure.get("time_signature") or "4/4"
    try:
        return int(str(raw).split("/")[0])
    except (ValueError, IndexError):
        return 4


def beats_per_rep(tab_snippet: Mapping[str, Any]) -> int:
    """§5.5 — one rep is one pass of the snippet: the sum of its measures' beats."""
    measures: Sequence[Mapping[str, Any]] = tab_snippet.get("measures") or []
    return sum(_time_signature_numerator(m) for m in measures)


def rep_seconds(tab_snippet: Mapping[str, Any], bpm: int) -> float:
    """§5.5 — wall-clock for one rep. Floored at 4s: nothing is really shorter."""
    if bpm <= 0:
        raise ValueError(f"bpm must be positive, got {bpm}")
    return max(4.0, beats_per_rep(tab_snippet) * 60 / bpm)


def planned_reps(
    tab_snippet: Mapping[str, Any],
    bpm: int,
    seconds: int,
    repetitions_ceiling: int,
) -> Optional[int]:
    """§5.5 — fit reps to the clock. Returns None when the drill does not fit.

    The drill's own `repetitions` (6-12 since FLE-72; 8-30 on rows generated before
    it, which are not re-validated on read) is a CEILING, not a plan. REP_DUTY_CYCLE
    concedes a quarter of the slot to inter-rep reset and breath.

    The FLE-72 floor is MIN_PLANNED_REPS on purpose, so the ceiling can no longer be
    the reason a drill plans below the floor: min(fits, ceiling) >= MIN_PLANNED_REPS
    for every post-FLE-72 drill that clears the `fits` gate.

    None means "drop this drill from candidates and take the next-highest score" —
    NOT "plan it for 3 reps". A slow 8-measure drill squeezed into a 15-minute
    session is the single most likely way a technically-valid session comes out
    pedagogically worthless.
    """
    fits = int((seconds * REP_DUTY_CYCLE) // rep_seconds(tab_snippet, bpm))
    if fits < MIN_PLANNED_REPS:
        return None
    return min(fits, repetitions_ceiling)


def warmup_bpm(rung_bpm: int, start_bpm: int) -> int:
    """§5.1 — the warm-up is never new material and never at the working tempo."""
    return max(rung_bpm - WARMUP_RUNG_OFFSET_BPM, start_bpm)


def warmup_reps(normal_planned_reps: int) -> int:
    """§5.1 — ceil(0.60 x the reps a full slot would plan). Never below 1."""
    return max(1, math.ceil(normal_planned_reps * WARMUP_REP_FACTOR))


def completion_ratio(done_items: int, planned_items: int) -> float:
    """§6 — skips count AGAINST completion.

    This number feeds mode rule 3, which is the whole feedback loop: a player who
    bails gets a shorter, more musical session next time.
    """
    if planned_items <= 0:
        return 0.0
    return done_items / planned_items
