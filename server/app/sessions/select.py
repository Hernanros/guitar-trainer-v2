# server/app/sessions/select.py
#
# PART II of the FLE-4 spec — WHICH drills go in the session. Pure functions over an
# in-memory snapshot: no DB, no I/O, no clock, no LLM. The loader that turns a
# database into a `Snapshot` is a separate module, so everything in here is
# unit-testable offline and a session is reproducible from its inputs alone.
#
# The three things this module is responsible for:
#
#   §5.1  the warm-up preference ladder
#   §5.2 + §11  candidate filtering, the selection score, and greedy fill
#   §12   the degradation ladder, in order, for a bank too thin to fill the session
#
# NOTHING HERE CALLS AN LLM. Sonnet writes drills; this code assembles sessions.
# That line has held for five phases and is what makes a session instant, free,
# reproducible and debuggable. Do not put a model in this path without an explicit
# product decision from Miagi.
#
# --- Two places this runs ahead of the schema ------------------------------
#
# FLE-8 shipped the bank WITHOUT §13's `family`, `tier`, `tier_raw_score` and
# `status` columns, and with `drills.user_id` NOT NULL (so §12.1's global seed
# drills cannot exist yet). Rather than block, this module degrades in two
# documented ways, both of which become no-ops the day those columns land:
#
#   family  -> None. The family is used for the tier-range clamp (§9.1), the
#              family_repeat penalty (§11.2) and the warm-up's family inheritance
#              (§5.1a). With no column, `DrillCandidate.family_key` falls back to
#              the drill's skill-node ROOT, which is the axis families are hung off
#              anyway (§8). The penalty is coarser -- it stops two lead drills
#              stacking, not two alternate-picking drills -- and the band filter is
#              UNAFFECTED, because §11.1's band is computed from root mastery and
#              never reads the family itself.
#   status  -> `canonical_drill_id IS NULL` is the faithful proxy. That is exactly
#              what dedup suppression sets today (app/ai/drill_dedupe.py), and it is
#              what §13's status='duplicate' will mean.
#
# Both are tracked as follow-ups on FLE-9; see SPEC_GAPS at the bottom of this file.

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from .plan import (
    MIN_PLANNED_REPS,
    has_stretch_slot,
    planned_reps,
    slot_seconds,
    technique_slots,
    warmup_bpm,
    warmup_reps,
)
from .taxonomy import (
    SkillRoot,
    TechniqueFamily,
    Tier,
    band,
    compute_tier,
    in_band,
    stretch_tier,
)

__all__ = [
    "DrillCandidate",
    "SelectionContext",
    "TechniqueFill",
    "TechniquePick",
    "WarmupPick",
    "Degradation",
    "stable_index",
    "score",
    "fill_technique_block",
    "select_warmup",
    "SPEC_GAPS",
]

# §11.2 — the score weights. Tunable; every one of them is a version bump.
W_DEFICIT = 100.0
W_STALENESS = 40.0
W_SONG_RELEVANCE = 25.0
W_LADDER_MOMENTUM = 15.0
W_RECENCY = -60.0
W_FAMILY_REPEAT = -30.0

STALENESS_HORIZON_DAYS = 30
NEVER_PRACTISED_STALENESS = 0.50

# §5.2 — set-level caps inside one technique block.
MAX_MAINTENANCE_DRILLS = 1
MAX_SONG_SPECIFIC_DRILLS = 1

# The states a drill's per-user progress row may be in and still be selectable.
SELECTABLE_PROGRESS_STATES = frozenset({"active", "maintenance"})


class Degradation:
    """§12 — the thin-bank ladder, as relaxation LEVELS applied in order.

    Level 0 is the spec's hard constraint set. Each level above it adds exactly one
    relaxation and keeps everything below. Rung 1 of the written ladder ("shrink N")
    is not a level: it is the N search in `fill_technique_block`, which runs at every
    level. Rung 5 ("convert unfilled technique seconds into repertoire") is not a
    level either -- it is what the caller does when this module returns no picks at
    all.

    Week 1 of the pilot runs at the top of this ladder, and the point of it is that
    every rung still produces a real session. A 15-minute session with one drill and
    10 minutes of song work is a good session. A session with three "coming soon"
    cards is not a session.
    """

    STRICT = 0
    WIDEN_BAND = 1       # §12.2 — band(f) +/- 1 tier
    ALLOW_RECENCY = 2    # §12.3 — a drill from the last 2 sessions may return
    ALLOW_SONG_SPECIFIC = 3  # §12.4 — lift the 1-per-session song_specific cap

    ALL = (STRICT, WIDEN_BAND, ALLOW_RECENCY, ALLOW_SONG_SPECIFIC)

    NAMES = {
        STRICT: "strict",
        WIDEN_BAND: "widen_band",
        ALLOW_RECENCY: "allow_recency",
        ALLOW_SONG_SPECIFIC: "allow_song_specific",
    }


# ---------------------------------------------------------------------------
# Snapshot types — what the loader hands this module
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DrillCandidate:
    """One drill plus this user's progress on it. The unit of selection.

    Everything the score and the filters read is on this object, so selection can be
    tested against a list of literals and a session can be replayed from its inputs.
    """

    drill_id: str
    skill_node_id: str
    tab_snippet: Mapping[str, Any]
    start_bpm: int
    target_bpm: int
    repetitions: int
    song_specific: bool = False
    # `canonical_drill_id IS NULL` today; `status == 'active'` once §13 ships.
    is_canonical: bool = True
    # §8. None until FLE-8 backfills the column; see the module header.
    family: Optional[TechniqueFamily] = None
    # The drill's skill node's primary root. The family proxy while family is None.
    root: Optional[SkillRoot] = None
    # Mastery of target_skill_node_id, [0,1]. Drives `deficit`, the dominant term.
    node_mastery: float = 0.5
    # --- drill_progress, absent for a drill this user has never touched ---
    progress_state: str = "active"
    rung_bpm: Optional[int] = None
    consecutive_clears: int = 0
    attempts: int = 0
    last_practiced_on: Optional[date] = None
    # Lifetime CLEAR count from drill_attempts. Read ONLY by §5.4's consolidation
    # fallback ("the drill with the highest historical clear rate"); no selection
    # rule scores on it, so it stays 0 for every candidate the loader doesn't need
    # it for.
    lifetime_clears: int = 0

    @property
    def planned_bpm(self) -> int:
        """The tempo of record. A drill with no progress row is planned at its floor."""
        return self.rung_bpm if self.rung_bpm is not None else self.start_bpm

    @property
    def family_key(self) -> str:
        """The axis `family_repeat` is measured on. Degrades family -> root -> node.

        Never returns a constant: falling all the way through to the skill node
        means the penalty simply never fires for that drill, rather than firing
        against every other family-less drill in the bank.
        """
        if self.family is not None:
            return f"family:{self.family.value}"
        if self.root is not None:
            return f"root:{self.root.value}"
        return f"node:{self.skill_node_id}"

    @property
    def is_maintenance(self) -> bool:
        return self.progress_state == "maintenance"

    @property
    def never_practised(self) -> bool:
        return self.attempts <= 0 or self.last_practiced_on is None

    @property
    def clear_rate(self) -> float:
        """§5.4 — lifetime clears / attempts. Zero attempts is 0.0, not undefined.

        A drill with no history has no evidence of being owned, and §5.4 is looking
        for the drill the player owns MOST. Returning 0.0 keeps it sortable and puts
        untouched drills last, which is the right answer for an end-on-a-win item.
        """
        if self.attempts <= 0:
            return 0.0
        return min(self.lifetime_clears / self.attempts, 1.0)


@dataclass(frozen=True)
class SelectionContext:
    """Everything about the user and the day that selection depends on.

    `root_mastery` and `player_level` feed §11.1's band; `song_skill_node_ids` feeds
    song_relevance; `recent_drill_ids` is the union of the drills practised in the
    last two TERMINAL sessions, which is what §5.2's recency constraint means.
    """

    user_id: str
    local_calendar_day: date
    song_skill_node_ids: frozenset[str] = frozenset()
    recent_drill_ids: frozenset[str] = frozenset()
    root_mastery: Mapping[str, float] = field(default_factory=dict)
    player_level: Optional[float] = None

    def band_for(self, candidate: DrillCandidate) -> tuple[Tier, Tier]:
        return band(root_mastery=self._mastery_for(candidate), player_level=self.player_level)

    def stretch_tier_for(self, candidate: DrillCandidate) -> Tier:
        return stretch_tier(
            root_mastery=self._mastery_for(candidate), player_level=self.player_level
        )

    def _mastery_for(self, candidate: DrillCandidate) -> Optional[float]:
        root = candidate.family.root if candidate.family is not None else candidate.root
        if root is None:
            return None
        return self.root_mastery.get(root.value)


@dataclass(frozen=True)
class TechniquePick:
    """One filled technique slot. Carries WHY it was picked, so a session is explicable."""

    candidate: DrillCandidate
    tier: Tier
    score: float
    planned_bpm: int
    planned_reps: int
    is_stretch: bool = False
    # §12.3 — set when the drill is a recency violation the ladder allowed through.
    repeat_ok: bool = False


@dataclass(frozen=True)
class TechniqueFill:
    """The technique block: its picks, the slot size they were sized for, and the
    highest degradation rung the generator had to stand on to produce them."""

    picks: tuple[TechniquePick, ...]
    slot_seconds: int
    degradation: int = Degradation.STRICT

    @property
    def slots(self) -> int:
        return len(self.picks)

    @property
    def degradation_name(self) -> str:
        return Degradation.NAMES[self.degradation]


@dataclass(frozen=True)
class WarmupPick:
    """§5.1 — exactly one item, unrated, skippable, never touches the ladder."""

    candidate: DrillCandidate
    planned_bpm: int
    planned_reps: int
    rule: str  # 'a'..'d', or 'fallback_slot1'


# ---------------------------------------------------------------------------
# Deterministic choice
# ---------------------------------------------------------------------------

def stable_index(seed_key: str, length: int) -> int:
    """A reproducible index into a sorted list — the §11.1 `setseed(hashtext(...))`.

    Python's built-in hash() is salted per process (PYTHONHASHSEED), so using it
    here would make a session irreproducible across a server restart, which is
    exactly the property the spec's `seed` column exists to guarantee. blake2b is
    stable across processes, machines and Python versions.
    """
    if length <= 0:
        raise ValueError("length must be positive")
    digest = hashlib.blake2b(seed_key.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % length


# ---------------------------------------------------------------------------
# §11.2 — the score
# ---------------------------------------------------------------------------

def _staleness(candidate: DrillCandidate, today: date) -> float:
    """Never-practised scores 0.50, not 1.0 — deliberately (§11.2).

    A brand-new drill shouldn't automatically outrank a genuinely stale one the
    player has history with, but it shouldn't starve either. 0.50 keeps new material
    entering the rotation without letting a freshly-backfilled bank flush everything
    familiar out of a player's sessions overnight.
    """
    if candidate.never_practised:
        return NEVER_PRACTISED_STALENESS
    days = (today - candidate.last_practiced_on).days  # type: ignore[operator]
    return min(max(days, 0), STALENESS_HORIZON_DAYS) / STALENESS_HORIZON_DAYS


def score(
    candidate: DrillCandidate,
    context: SelectionContext,
    *,
    filled_family_keys: frozenset[str] = frozenset(),
) -> float:
    """§11.2 — the selection score. `deficit` dominates at 100: the bank exists to
    serve weaknesses and everything else is a modifier.

    `filled_family_keys` is recomputed after every pick, which is what makes the
    family_repeat penalty do its job — it is a property of the session so far, not
    of the drill.
    """
    deficit = 1.0 - candidate.node_mastery
    staleness = _staleness(candidate, context.local_calendar_day)
    song_relevance = 1.0 if candidate.skill_node_id in context.song_skill_node_ids else 0.0
    ladder_momentum = 1.0 if candidate.consecutive_clears == 1 else 0.0
    recency = 1.0 if candidate.drill_id in context.recent_drill_ids else 0.0
    family_repeat = 1.0 if candidate.family_key in filled_family_keys else 0.0
    return (
        W_DEFICIT * deficit
        + W_STALENESS * staleness
        + W_SONG_RELEVANCE * song_relevance
        + W_LADDER_MOMENTUM * ladder_momentum
        + W_RECENCY * recency
        + W_FAMILY_REPEAT * family_repeat
    )


# ---------------------------------------------------------------------------
# §5.2 — candidate filtering
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Scored:
    """A candidate with its tier and fitted reps resolved once, not per comparison."""

    candidate: DrillCandidate
    tier: Tier
    reps: int
    base_score: float
    is_stretch: bool = False


def _tier_of(candidate: DrillCandidate) -> Tier:
    tier, _raw = compute_tier(
        candidate.tab_snippet,
        start_bpm=candidate.start_bpm,
        target_bpm=candidate.target_bpm,
        family=candidate.family,
    )
    return tier


def _eligible(
    candidates: Sequence[DrillCandidate],
    context: SelectionContext,
    *,
    seconds: int,
    level: int,
) -> tuple[list[_Scored], list[_Scored]]:
    """Hard constraints from §5.2, at one degradation level.

    Returns (in_band, stretch_band). The stretch list is the tier ABOVE the band and
    is only ever drawn on for the single stretch slot (§11.1).

    The two constraints that are NOT here are the set-level caps (one maintenance,
    one song-specific, no two drills sharing a skill node) and family_repeat. Those
    depend on what has already been picked, so they live in the greedy fill.
    """
    in_band_out: list[_Scored] = []
    stretch_out: list[_Scored] = []
    for c in candidates:
        if c.progress_state not in SELECTABLE_PROGRESS_STATES:
            continue
        if not c.is_canonical:
            continue
        if level < Degradation.ALLOW_RECENCY and c.drill_id in context.recent_drill_ids:
            continue
        # §5.5 — a drill that cannot get MIN_PLANNED_REPS reps in this slot does not
        # go in. Not planned short: dropped. This is the single most likely way a
        # technically-valid session comes out pedagogically worthless.
        reps = planned_reps(c.tab_snippet, c.planned_bpm, seconds, c.repetitions)
        if reps is None:
            continue
        tier = _tier_of(c)
        low, high = context.band_for(c)
        if level >= Degradation.WIDEN_BAND:
            low, high = low.shifted(-1), high.shifted(1)
        scored = _Scored(c, tier, reps, score(c, context))
        if in_band(tier, (low, high)):
            in_band_out.append(scored)
        elif tier == context.stretch_tier_for(c):
            stretch_out.append(_Scored(c, tier, reps, scored.base_score, is_stretch=True))
    return in_band_out, stretch_out


def _sort_key(scored: _Scored, filled: frozenset[str], context: SelectionContext):
    """§5.2 — deterministic tie-break: (score desc, tier asc, drill_id asc).

    The score is recomputed against `filled` rather than reused from _Scored, because
    family_repeat moves after every pick.
    """
    s = score(scored.candidate, context, filled_family_keys=filled)
    return (-s, scored.tier.ordinal, scored.candidate.drill_id)


def _greedy_fill(
    pool: Sequence[_Scored],
    context: SelectionContext,
    *,
    limit: int,
    level: int,
    taken_nodes: frozenset[str] = frozenset(),
) -> list[TechniquePick]:
    """§5.2 — greedy by score, recomputing family_repeat after each pick.

    Enforces the three set-level caps as it goes. A candidate blocked by a cap is
    skipped, not dropped: the next-highest score takes the slot.
    """
    picks: list[TechniquePick] = []
    filled_families: set[str] = set()
    used_nodes: set[str] = set(taken_nodes)
    maintenance_used = 0
    song_specific_used = 0
    remaining = list(pool)

    while remaining and len(picks) < limit:
        remaining.sort(key=lambda s: _sort_key(s, frozenset(filled_families), context))
        chosen: Optional[_Scored] = None
        for scored in remaining:
            c = scored.candidate
            if c.skill_node_id in used_nodes:
                continue
            if c.is_maintenance and maintenance_used >= MAX_MAINTENANCE_DRILLS:
                continue
            if (
                c.song_specific
                and level < Degradation.ALLOW_SONG_SPECIFIC
                and song_specific_used >= MAX_SONG_SPECIFIC_DRILLS
            ):
                continue
            chosen = scored
            break
        if chosen is None:
            break
        c = chosen.candidate
        remaining = [s for s in remaining if s.candidate.drill_id != c.drill_id]
        used_nodes.add(c.skill_node_id)
        filled_families.add(c.family_key)
        if c.is_maintenance:
            maintenance_used += 1
        if c.song_specific:
            song_specific_used += 1
        picks.append(
            TechniquePick(
                candidate=c,
                tier=chosen.tier,
                score=score(c, context, filled_family_keys=frozenset()),
                planned_bpm=c.planned_bpm,
                planned_reps=chosen.reps,
                is_stretch=chosen.is_stretch,
                repeat_ok=c.drill_id in context.recent_drill_ids,
            )
        )
    return picks


def _fill_at(
    candidates: Sequence[DrillCandidate],
    context: SelectionContext,
    *,
    slots: int,
    seconds: int,
    level: int,
) -> list[TechniquePick]:
    """Fill exactly `slots` slots of `seconds` each, at one degradation level.

    The stretch slot (§11.1) is reserved LAST and drawn from the tier above the band,
    chosen by a seeded index so it varies day to day while staying reproducible. It
    is skipped entirely when N == 1: the one drill a 15-minute session gets should
    not be the hard one.
    """
    in_band_pool, stretch_pool = _eligible(candidates, context, seconds=seconds, level=level)

    stretch_pick: Optional[TechniquePick] = None
    body_limit = slots
    if has_stretch_slot(slots) and stretch_pool:
        ordered = sorted(stretch_pool, key=lambda s: s.candidate.drill_id)
        idx = stable_index(f"{context.user_id}|{context.local_calendar_day}|stretch", len(ordered))
        chosen = ordered[idx]
        c = chosen.candidate
        stretch_pick = TechniquePick(
            candidate=c,
            tier=chosen.tier,
            score=score(c, context),
            planned_bpm=c.planned_bpm,
            planned_reps=chosen.reps,
            is_stretch=True,
            repeat_ok=c.drill_id in context.recent_drill_ids,
        )
        body_limit = slots - 1

    taken = frozenset({stretch_pick.candidate.skill_node_id}) if stretch_pick else frozenset()
    body = _greedy_fill(
        [s for s in in_band_pool if not (stretch_pick and s.candidate.drill_id == stretch_pick.candidate.drill_id)],
        context,
        limit=body_limit,
        level=level,
        taken_nodes=taken,
    )

    # §5.2 — easiest tier first, hardest last; the stretch slot always goes last.
    # Difficulty ramps; the session doesn't open on its hardest thing.
    body.sort(key=lambda p: (p.tier.ordinal, -p.score, p.candidate.drill_id))
    if stretch_pick is not None:
        body.append(stretch_pick)
    return body


def fill_technique_block(
    candidates: Sequence[DrillCandidate],
    context: SelectionContext,
    *,
    technique_seconds: int,
) -> TechniqueFill:
    """§5.2 + §12 — the technique block, N included.

    N and the slot size are mutually dependent: fewer slots means longer slots, and a
    longer slot lets more drills clear MIN_PLANNED_REPS. So this searches N downward
    from §3.3's nominal count and takes the LARGEST N that can actually be filled --
    which is §12 rung 1 ("shrink N to the number of eligible candidates") read as a
    fixed point rather than as a single subtraction. Taking the largest is what stops
    a bank of 4 eligible drills producing a 2-slot session.

    Only if N cannot reach 1 does it climb the rest of the ladder: widen the band,
    then allow a recency violation, then lift the song-specific cap. An empty result
    is rung 5 -- the caller converts the technique seconds into repertoire section
    work. It never emits a placeholder item and never emits an empty block.
    """
    nominal = technique_slots(technique_seconds)
    for level in Degradation.ALL:
        for n in range(nominal, 0, -1):
            seconds = slot_seconds(technique_seconds, n)
            picks = _fill_at(candidates, context, slots=n, seconds=seconds, level=level)
            if len(picks) >= n:
                return TechniqueFill(tuple(picks), seconds, level)
    return TechniqueFill((), slot_seconds(technique_seconds, nominal), Degradation.ALLOW_SONG_SPECIFIC)


# ---------------------------------------------------------------------------
# §5.1 — the warm-up
# ---------------------------------------------------------------------------

def _warmup_reps_for(candidate: DrillCandidate, bpm: int, seconds: int) -> int:
    """§5.1 — 60% of the reps a full slot would plan, floored at 1.

    planned_reps() returns None when the drill can't clear MIN_PLANNED_REPS in the
    box. For the warm-up that is not disqualifying -- the warm-up is unrated and
    below tempo, so a short one is still a warm-up -- so the floor is used instead.
    """
    normal = planned_reps(candidate.tab_snippet, bpm, seconds, candidate.repetitions)
    return warmup_reps(normal if normal is not None else MIN_PLANNED_REPS)


def select_warmup(
    candidates: Sequence[DrillCandidate],
    context: SelectionContext,
    *,
    seconds: int,
    slot_one: Optional[TechniquePick] = None,
    seed_drills: Sequence[DrillCandidate] = (),
) -> Optional[WarmupPick]:
    """§5.1 — exactly one item, by the first preference rule that yields a candidate.

    The warm-up is never new material. Novelty at minute zero is friction;
    familiarity at minute zero is a ramp. And because it inherits slot 1's family it
    PRELOADS the session's main mechanic at a tempo that's already owned, which is
    what a warm-up is actually for.

    Rule (d) -- the three hand-authored seed drills of §12.1 -- cannot fire yet:
    seed drills are global rows (`user_id IS NULL`) and `drills.user_id` is NOT NULL
    today. `seed_drills` is the seam for them. Until they ship, rule `fallback_slot1`
    stands in: slot 1's own drill, replanned at warm-up tempo and reps. That is a
    real warm-up rather than an empty block, which §12 forbids outright -- but it is
    a deviation from the written ladder and is flagged in SPEC_GAPS.
    """
    family_key = slot_one.candidate.family_key if slot_one else None
    tier_ceiling = slot_one.tier if slot_one else None
    picked_id = slot_one.candidate.drill_id if slot_one else None

    def usable(c: DrillCandidate) -> bool:
        return (
            c.is_canonical
            and c.progress_state in SELECTABLE_PROGRESS_STATES
            and c.drill_id != picked_id
        )

    def most_practised(pool: Sequence[DrillCandidate]) -> DrillCandidate:
        # max(attempts), tie-break most recent last_practiced_on, then drill_id asc.
        return sorted(
            pool,
            key=lambda c: (
                -c.attempts,
                -(c.last_practiced_on.toordinal() if c.last_practiced_on else 0),
                c.drill_id,
            ),
        )[0]

    practised = [c for c in candidates if usable(c) and c.attempts >= 1]

    # (a) same family as slot 1, at or below slot 1's tier.
    if family_key is not None and tier_ceiling is not None:
        pool = [
            c for c in practised
            if c.family_key == family_key and _tier_of(c) <= tier_ceiling
        ]
        if pool:
            return _build_warmup(most_practised(pool), seconds, "a")

    # (b) any family, tier <= D2.
    pool = [c for c in practised if _tier_of(c) <= Tier.D2]
    if pool:
        return _build_warmup(most_practised(pool), seconds, "b")

    # (c) lowest-tier active drill in slot 1's family — practised or not.
    if family_key is not None:
        pool = [c for c in candidates if usable(c) and c.family_key == family_key]
        if pool:
            chosen = sorted(pool, key=lambda c: (_tier_of(c).ordinal, c.drill_id))[0]
            return _build_warmup(chosen, seconds, "c")

    # (d) a seed warm-up drill. Always resolves once §12.1's rows exist.
    if seed_drills:
        chosen = sorted(seed_drills, key=lambda c: c.drill_id)[0]
        return _build_warmup(chosen, seconds, "d")

    # Last resort — slot 1's own drill, below tempo. Never an empty block.
    if slot_one is not None:
        return _build_warmup(slot_one.candidate, seconds, "fallback_slot1")
    return None


def _build_warmup(candidate: DrillCandidate, seconds: int, rule: str) -> WarmupPick:
    bpm = warmup_bpm(candidate.planned_bpm, candidate.start_bpm)
    return WarmupPick(
        candidate=candidate,
        planned_bpm=bpm,
        planned_reps=_warmup_reps_for(candidate, bpm, seconds),
        rule=rule,
    )


# ---------------------------------------------------------------------------
# Where this module knowingly runs ahead of the schema
# ---------------------------------------------------------------------------

SPEC_GAPS = (
    "drills.family / tier / tier_raw_score / status — §13 columns FLE-8 did not "
    "ship. family degrades to the skill-node root (family_key), status degrades to "
    "canonical_drill_id IS NULL, tier is computed on read instead of stored.",
    "drills.user_id is NOT NULL, so §12.1's three global seed warm-up drills cannot "
    "exist. §5.1 rule (d) is unreachable; `fallback_slot1` stands in for it.",
)
