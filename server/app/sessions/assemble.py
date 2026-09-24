# server/app/sessions/assemble.py
#
# FLE-9 increment 4a — the assembler. Everything above this module answers a piece
# of the question; this is the module that produces A SESSION.
#
#   plan.py      §3-§6   how long each block is, how many slots, at what tempo
#   taxonomy.py  §8-§9   what a drill IS (family, tier)
#   select.py    §5.1-§5.2, §11-§12  WHICH drills
#   assemble.py  §2, §5.3-§5.4, §6, §7.3, §12 rung 5  -> the ordered plan
#
# `build_plan()` is PURE. It takes a snapshot dataclass and returns a frozen plan;
# it never touches a session, a connection or a clock. That is deliberate and it is
# the whole reason the §13 promise ("same inputs -> byte-identical plan") is testable
# at all: the loader that fills GeneratorInputs needs Postgres, this does not. Every
# rule in this file is covered by an offline test.
#
# No LLM. Fifth module in a row, same line: Sonnet writes drills, plain code
# assembles sessions.
#
# --- Three things this module decides that the spec does not say outright ---
#
# (a) SECONDS ARE NEVER LOST. §3's budget sums to exactly T x 60, and several rules
#     here move seconds between items (§12 rung 5, the T=15 consolidation fold, a
#     warm-up that cannot be filled). Rather than special-casing each one, any
#     second left without a home lands on the plan's LAST item -- which is §3's own
#     rule for the technique block's floor-division remainder, applied to the whole
#     session. `sum(item.planned_seconds) == target_minutes * 60` is invariant and
#     pinned by test for every shape, including the degraded ones.
#
# (b) A SESSION WITH NO SONG spends its repertoire seconds on the technique block,
#     and does so BEFORE the fill runs so the slots are sized for the real budget.
#     §12 covers the thin-bank direction; this is the other one, and it happens to
#     the genuinely new user whose song bank is still empty.
#
# (c) UNKNOWN SONG READINESS PLANS SLOW. §5.3's tempo_factor is a function of
#     song_readiness, which is None when today's song has no song_skills rows. None
#     is read as 0.0 -> factor 0.70, not as 1.00. Planning a player at full song
#     tempo on zero evidence is the worse of the two errors by a wide margin.

from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Mapping, Optional, Sequence

from .plan import (
    GENERATOR_VERSION,
    LAYOFF_REENTRY_DAYS,
    MIN_PLANNED_REPS,
    RUNG_STEP_BPM,
    SUPPORTED_TARGET_MINUTES,
    WARMUP_RUNG_OFFSET_BPM,
    Block,
    SessionMode,
    block_budget,
    choose_mode,
    planned_reps,
    rating_tap_budget,
    repertoire_split,
    section_bpm,
)
from .select import (
    Degradation,
    DrillCandidate,
    SelectionContext,
    TechniquePick,
    fill_technique_block,
    select_warmup,
    stable_index,
)

__all__ = [
    "ItemKind",
    "PlannedItem",
    "SessionPlan",
    "SongMaterial",
    "ConsolidationSong",
    "GeneratorInputs",
    "NoMaterialError",
    "build_plan",
    "session_seed",
    "SPEC_GAPS",
]


class ItemKind(str, enum.Enum):
    """§13 `session_items.kind`. Values match the `session_item_kind` PG enum."""

    DRILL = "drill"
    SONG_SECTION = "song_section"
    SONG_PLAY = "song_play"


class NoMaterialError(Exception):
    """Raised when the snapshot yields ZERO items — no drills, no song, no can_play.

    §12 forbids emitting a placeholder item or an empty block, so the generator's
    only honest answer here is "there is nothing to practise yet". A one-item
    session is a session; a session of three 'coming soon' cards is not. The API
    layer turns this into a "finish onboarding" response, not a 500.
    """


# ---------------------------------------------------------------------------
# The snapshot — what the loader hands this module
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SongMaterial:
    """Today's song, as the assembler needs it. Inherited from the day's selector.

    The session NEVER runs its own song selection and never re-rolls (§5.3); this
    arrives already decided by app/selectors/today_song.py.
    """

    song_id: int
    bpm: int
    # §4 / §5.3 — mean mastery over leaf nodes joined to the song via song_skills.
    # None when the song has no song_skills rows; see note (c) in the header.
    readiness: Optional[float] = None
    # §5.3 — the section tied to the LOWEST-mastery node among the song's skills.
    section_skill_node_id: Optional[str] = None
    # §11.2 — feeds the song_relevance term of the selection score.
    skill_node_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ConsolidationSong:
    """§5.4 — a `category = 'can_play'` song. End on a win."""

    song_id: int
    bpm: int


@dataclass(frozen=True)
class GeneratorInputs:
    """One DB snapshot, frozen. `build_plan` is a pure function of this object.

    Everything optional has a default that means "no history", never a default that
    silently fabricates history: `days_since_last=None` is a user with no prior
    session, which must not read as a 0-day layoff (or a 7-day one).
    """

    user_id: str
    local_calendar_day: date
    tz_offset_minutes: int
    target_minutes: int
    # --- §4 mode inputs ---
    sessions_count: int = 0
    days_since_last: Optional[int] = None
    completion_3: Optional[float] = None
    player_level: float = 0.0
    # Raw (unfloored) mean mastery over ALL leaf skill_nodes — the same scale as
    # `root_mastery`'s values. This is DELIBERATELY separate from `player_level`,
    # which is floored for catalog selection (FLE-49) and would manufacture a fake
    # root_deficit if reused for rule 5's self-relative comparison (FLE-59). None
    # only when the player has no leaf skill_nodes at all, the same condition under
    # which `weakest_root_mastery` is also None.
    player_mastery_raw: Optional[float] = None
    # root value -> mean mastery over its leaves. Roots with no leaves are ABSENT,
    # not zero: an untouched root is unknown, not weak (§4).
    root_mastery: Mapping[str, float] = field(default_factory=dict)
    # --- material ---
    song: Optional[SongMaterial] = None
    candidates: Sequence[DrillCandidate] = ()
    seed_drills: Sequence[DrillCandidate] = ()
    can_play_songs: Sequence[ConsolidationSong] = ()
    # §5.2 — drills practised in the last two TERMINAL sessions.
    recent_drill_ids: frozenset[str] = frozenset()

    @property
    def weakest_root_mastery(self) -> Optional[float]:
        """§4 rule 5. None when no root has any leaves — the rule then cannot fire."""
        return min(self.root_mastery.values()) if self.root_mastery else None

    def selection_context(self) -> SelectionContext:
        return SelectionContext(
            user_id=self.user_id,
            local_calendar_day=self.local_calendar_day,
            song_skill_node_ids=self.song.skill_node_ids if self.song else frozenset(),
            recent_drill_ids=self.recent_drill_ids,
            root_mastery=self.root_mastery,
            player_level=self.player_level,
        )


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlannedItem:
    """One row of `practice_session_items`, before it has an id.

    Every boolean is decided HERE and shipped to the device (FLE-10 R5). The client
    derives none of them, which is what keeps this document's versioning discipline
    alive after the trip to the phone.
    """

    item_index: int
    block: Block
    kind: ItemKind
    planned_seconds: int
    rated: bool
    skippable: bool
    click_enabled: bool
    drill_id: Optional[str] = None
    song_id: Optional[int] = None
    target_skill_node_id: Optional[str] = None
    planned_bpm: Optional[int] = None
    planned_reps: Optional[int] = None
    re_entry: bool = False
    repeat_ok: bool = False
    is_consolidation: bool = False


@dataclass(frozen=True)
class SessionPlan:
    """The generator's output: a session header plus its items, in order.

    `notes` is the explicability channel. Every rule that moved a second or stood on
    a degradation rung leaves a name in it, so a session that came out odd can be
    explained from its own record rather than by re-deriving the snapshot.
    """

    user_id: str
    local_calendar_day: date
    tz_offset_minutes: int
    target_minutes: int
    mode: SessionMode
    mode_rule: str
    allow_push: bool
    seed: str
    items: tuple[PlannedItem, ...]
    song_id: Optional[int] = None
    generator_version: str = GENERATOR_VERSION
    degradation: int = Degradation.STRICT
    notes: tuple[str, ...] = ()

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def planned_seconds(self) -> int:
        return sum(i.planned_seconds for i in self.items)

    @property
    def degradation_name(self) -> str:
        return Degradation.NAMES[self.degradation]

    @property
    def rated_item_count(self) -> int:
        return sum(1 for i in self.items if i.rated)


def session_seed(user_id: str, local_calendar_day: date) -> str:
    """§13 — `hashtext('<user_id>|<day>')`, kept as the pre-hash key.

    Stored verbatim rather than hashed: the point of the column is that a session can
    be REPLAYED, and the key is what `stable_index` consumes. A 64-bit digest in the
    column would be a fingerprint of the inputs, not the inputs.
    """
    return f"{user_id}|{local_calendar_day.isoformat()}"


# ---------------------------------------------------------------------------
# §7.3 — layoff re-entry, applied at generation time
# ---------------------------------------------------------------------------

def _reentry_bpm(candidate: DrillCandidate) -> int:
    return max(candidate.planned_bpm - RUNG_STEP_BPM, candidate.start_bpm)


def _is_reentry(candidate: DrillCandidate, today: date) -> bool:
    """§7.3 — 10 days off this drill costs a rung of confidence, never of record."""
    if candidate.last_practiced_on is None:
        return False
    return (today - candidate.last_practiced_on).days >= LAYOFF_REENTRY_DAYS


def _reps_at(candidate: DrillCandidate, bpm: int, seconds: int, fallback: int) -> int:
    """Re-fit reps after the tempo moved, with a FLOOR instead of a drop.

    `planned_reps` returns None to mean "drop this drill from candidates" (§5.5), and
    at selection time that is exactly right. Here the drill is already in the plan and
    dropping it would leave a hole in the block, so a re-entry tempo that no longer
    fits the box gets MIN_PLANNED_REPS and overruns instead. §6 permits that
    explicitly: planned_seconds is advisory and an item is never hard-cut.
    """
    fitted = planned_reps(candidate.tab_snippet, bpm, seconds, candidate.repetitions)
    if fitted is not None:
        return fitted
    return max(min(fallback, candidate.repetitions), MIN_PLANNED_REPS)


# ---------------------------------------------------------------------------
# §6 — which technique items carry a rating tap
# ---------------------------------------------------------------------------

def _unrated_technique_ids(picks: Sequence[TechniquePick]) -> frozenset[str]:
    """§6 — taps land on technique items plus repertoire item 1, capped at 6.

    When N + 1 would exceed the cap the LOWEST-SCORED technique slots go unrated.
    With MAX_TECHNIQUE_SLOTS = 5 that cannot currently fire — N + 1 <= 6 always — so
    this is written against the rule rather than against today's constants, and it is
    tested by calling it directly with six picks.
    """
    budget = rating_tap_budget(len(picks))
    surplus = len(picks) - budget
    if surplus <= 0:
        return frozenset()
    ranked = sorted(picks, key=lambda p: (p.score, p.candidate.drill_id))
    return frozenset(p.candidate.drill_id for p in ranked[:surplus])


# ---------------------------------------------------------------------------
# §5.4 — the consolidation item
# ---------------------------------------------------------------------------

def _consolidation_song(
    songs: Sequence[ConsolidationSong], *, user_id: str, day: date
) -> Optional[ConsolidationSong]:
    """§5.4 — `setseed(hashtext('<user_id>|<day>|consolidation'))` over can_play songs."""
    if not songs:
        return None
    ordered = sorted(songs, key=lambda s: s.song_id)
    return ordered[stable_index(f"{user_id}|{day.isoformat()}|consolidation", len(ordered))]


def _consolidation_drill(
    candidates: Sequence[DrillCandidate], *, already_planned: frozenset[str]
) -> Optional[DrillCandidate]:
    """§5.4 fallback — "the drill with the highest historical clear rate".

    Two assembler judgments on top of the spec's one line:

    - Drills ALREADY IN THIS PLAN are preferred against, but not excluded. A session
      that hands you the same drill three times (warm-up fallback, technique slot,
      then the win) reads as a bug even when every individual rule is satisfied. If
      the plan is the whole bank, reuse is still better than no consolidation item.
    - Ties break on attempts then drill_id, so a drill cleared 3-of-3 loses to one
      cleared 30-of-30. Confidence should follow evidence, and at the end of a
      session the point is the drill the player OWNS, not the one with the luckiest
      short history.
    """
    usable = [c for c in candidates if c.is_canonical and c.attempts >= 1]
    if not usable:
        return None
    fresh = [c for c in usable if c.drill_id not in already_planned]
    pool = fresh or usable
    return sorted(pool, key=lambda c: (-c.clear_rate, -c.attempts, c.drill_id))[0]


# ---------------------------------------------------------------------------
# The assembler
# ---------------------------------------------------------------------------

def build_plan(inputs: GeneratorInputs) -> SessionPlan:
    """§2 + §5 + §6 — snapshot in, ordered plan out. Pure; no DB, no clock, no LLM.

    Block order is FIXED and never shuffled (§2): warm-up, technique, repertoire,
    consolidation. `item_index` is 0-based and contiguous over whatever blocks the
    snapshot could actually fill.

    Raises NoMaterialError when nothing at all can be planned.
    """
    if inputs.target_minutes not in SUPPORTED_TARGET_MINUTES:
        raise ValueError(
            f"target_minutes must be one of {SUPPORTED_TARGET_MINUTES}, "
            f"got {inputs.target_minutes}. §0 locked the four onboarding values; a "
            f"fifth needs a generator_version bump, not a silent round."
        )

    today = inputs.local_calendar_day
    notes: list[str] = []

    # --- §4: mode. One decision, first-match-wins, recorded with its rule name. ---
    decision = choose_mode(
        sessions_count=inputs.sessions_count,
        days_since_last=inputs.days_since_last,
        completion_3=inputs.completion_3,
        # Raw, not the floored `inputs.player_level` — rule 5 compares two leaf-
        # mastery averages on the same scale (FLE-59). Falls back to the floored
        # value only in the unreachable case where it's None but
        # weakest_root_mastery isn't (no leaves => no root rows either), so the
        # subtraction is never actually evaluated against it.
        player_level=(
            inputs.player_mastery_raw
            if inputs.player_mastery_raw is not None
            else inputs.player_level
        ),
        weakest_root_mastery=inputs.weakest_root_mastery,
        song_readiness=inputs.song.readiness if inputs.song else None,
    )
    budget = block_budget(inputs.target_minutes, decision.mode)

    # --- §3 + header note (b): the technique pool is sized BEFORE the fill. ---
    technique_pool = budget.technique
    if inputs.song is None:
        technique_pool += budget.repertoire
        notes.append("no_song_repertoire_to_technique")

    context = inputs.selection_context()
    fill = fill_technique_block(
        inputs.candidates, context, technique_seconds=technique_pool
    )
    slot_one = fill.picks[0] if fill.picks else None
    warmup = select_warmup(
        inputs.candidates,
        context,
        seconds=budget.warmup,
        slot_one=slot_one,
        seed_drills=inputs.seed_drills,
    )

    if fill.degradation != Degradation.STRICT:
        notes.append(f"degradation:{fill.degradation_name}")

    # `unspent` is header note (a): seconds whose block could not be filled. They are
    # re-homed below, and whatever is left over lands on the plan's last item.
    unspent = 0
    if warmup is None:
        unspent += budget.warmup
        notes.append("warmup_unfilled")
    if not fill.picks:
        unspent += technique_pool
        notes.append("rung5_technique_to_repertoire")

    # --- §5.4: decide the consolidation item before sizing the repertoire block, ---
    # --- because a missing one folds its seconds into the play-through.          ---
    planned_drill_ids = frozenset(
        [p.candidate.drill_id for p in fill.picks]
        + ([warmup.candidate.drill_id] if warmup else [])
    )
    cons_song: Optional[ConsolidationSong] = None
    cons_drill: Optional[DrillCandidate] = None
    if budget.consolidation > 0:
        cons_song = _consolidation_song(
            inputs.can_play_songs, user_id=inputs.user_id, day=today
        )
        if cons_song is None:
            cons_drill = _consolidation_drill(
                inputs.candidates, already_planned=planned_drill_ids
            )
            if cons_drill is not None:
                notes.append("consolidation_fallback_drill")

    fold_consolidation = budget.folds_consolidation or (
        budget.consolidation > 0 and cons_song is None and cons_drill is None
    )
    consolidation_seconds = 0 if fold_consolidation else budget.consolidation
    if fold_consolidation and budget.consolidation > 0:
        notes.append("consolidation_folded_into_play_through")

    items: list[PlannedItem] = []

    # -----------------------------------------------------------------
    # §5.1 — warm-up. Exactly one item, unrated, skippable, never the ladder.
    # -----------------------------------------------------------------
    if warmup is not None:
        items.append(
            PlannedItem(
                item_index=len(items),
                block=Block.WARMUP,
                kind=ItemKind.DRILL,
                planned_seconds=budget.warmup,
                rated=False,
                skippable=True,
                click_enabled=True,
                drill_id=warmup.candidate.drill_id,
                target_skill_node_id=warmup.candidate.skill_node_id,
                planned_bpm=warmup.planned_bpm,
                planned_reps=warmup.planned_reps,
            )
        )

    # -----------------------------------------------------------------
    # §5.2 — technique. Easiest first, stretch slot last (ordered by select.py).
    # -----------------------------------------------------------------
    unrated = _unrated_technique_ids(fill.picks)
    remainder = technique_pool - fill.slot_seconds * len(fill.picks) if fill.picks else 0
    for offset, pick in enumerate(fill.picks):
        seconds = fill.slot_seconds
        if offset == len(fill.picks) - 1:
            seconds += remainder  # §3 — the remainder lands on the block's last item.
        candidate = pick.candidate
        re_entry = _is_reentry(candidate, today)
        bpm = _reentry_bpm(candidate) if re_entry else pick.planned_bpm
        reps = (
            _reps_at(candidate, bpm, seconds, pick.planned_reps)
            if re_entry or seconds != fill.slot_seconds
            else pick.planned_reps
        )
        items.append(
            PlannedItem(
                item_index=len(items),
                block=Block.TECHNIQUE,
                kind=ItemKind.DRILL,
                planned_seconds=seconds,
                rated=candidate.drill_id not in unrated,
                skippable=True,
                click_enabled=True,
                drill_id=candidate.drill_id,
                target_skill_node_id=candidate.skill_node_id,
                planned_bpm=bpm,
                planned_reps=reps,
                re_entry=re_entry,
                repeat_ok=pick.repeat_ok,
            )
        )

    # -----------------------------------------------------------------
    # §5.3 — repertoire. Two items: section work, then the play-through.
    # -----------------------------------------------------------------
    if inputs.song is not None:
        song = inputs.song
        section_seconds, play_seconds = repertoire_split(budget.repertoire, decision.mode)
        # §12 rung 5 — unfilled technique seconds become SECTION work specifically,
        # not a longer play-through. The block that lost time was the working block.
        section_seconds += unspent
        unspent = 0
        if fold_consolidation:
            play_seconds += budget.consolidation
        readiness = song.readiness if song.readiness is not None else 0.0
        if song.readiness is None:
            notes.append("song_readiness_unknown_planned_slow")
        items.append(
            PlannedItem(
                item_index=len(items),
                block=Block.REPERTOIRE,
                kind=ItemKind.SONG_SECTION,
                planned_seconds=section_seconds,
                rated=True,
                skippable=False,  # §6 — the one unskippable item in the session.
                click_enabled=True,
                song_id=song.song_id,
                target_skill_node_id=song.section_skill_node_id,
                planned_bpm=section_bpm(song.bpm, readiness),
            )
        )
        items.append(
            PlannedItem(
                item_index=len(items),
                block=Block.REPERTOIRE,
                kind=ItemKind.SONG_PLAY,
                planned_seconds=play_seconds,
                rated=False,
                skippable=True,
                # §5.3 — click is a player toggle here and defaults OFF.
                click_enabled=False,
                song_id=song.song_id,
                planned_bpm=song.bpm,
                # §2 exception at T=15, and the fold-in when §5.4 has no material.
                is_consolidation=fold_consolidation,
            )
        )

    # -----------------------------------------------------------------
    # §5.4 — consolidation. Unrated and un-clicked: a tap here converts the win
    # back into an assessment.
    # -----------------------------------------------------------------
    if consolidation_seconds > 0 and cons_song is not None:
        items.append(
            PlannedItem(
                item_index=len(items),
                block=Block.CONSOLIDATION,
                kind=ItemKind.SONG_PLAY,
                planned_seconds=consolidation_seconds,
                rated=False,
                skippable=True,
                click_enabled=False,
                song_id=cons_song.song_id,
                planned_bpm=cons_song.bpm,
                is_consolidation=True,
            )
        )
    elif consolidation_seconds > 0 and cons_drill is not None:
        bpm = max(cons_drill.planned_bpm - WARMUP_RUNG_OFFSET_BPM, cons_drill.start_bpm)
        items.append(
            PlannedItem(
                item_index=len(items),
                block=Block.CONSOLIDATION,
                kind=ItemKind.DRILL,
                planned_seconds=consolidation_seconds,
                rated=False,
                skippable=True,
                click_enabled=False,
                drill_id=cons_drill.drill_id,
                target_skill_node_id=cons_drill.skill_node_id,
                planned_bpm=bpm,
                planned_reps=_reps_at(
                    cons_drill, bpm, consolidation_seconds, MIN_PLANNED_REPS
                ),
                is_consolidation=True,
            )
        )

    if not items:
        raise NoMaterialError(
            "No drills, no song and no can_play songs — nothing to plan. "
            "§12 forbids a placeholder item, so there is no session to generate."
        )

    # Header note (a) — nothing is lost. Whatever had no home lands on the last item.
    if unspent > 0:
        items[-1] = replace(
            items[-1], planned_seconds=items[-1].planned_seconds + unspent
        )
        notes.append("unspent_seconds_on_last_item")

    return SessionPlan(
        user_id=inputs.user_id,
        local_calendar_day=today,
        tz_offset_minutes=inputs.tz_offset_minutes,
        target_minutes=inputs.target_minutes,
        mode=decision.mode,
        mode_rule=decision.rule,
        allow_push=decision.allow_push,
        seed=session_seed(inputs.user_id, today),
        items=tuple(items),
        song_id=inputs.song.song_id if inputs.song else None,
        degradation=fill.degradation,
        notes=tuple(notes),
    )


def plan_as_rows(plan: SessionPlan) -> list[dict]:
    """Items as plain dicts, for the writer and for golden-file comparison.

    The §13 promise is "same inputs -> byte-identical plan", and this is the form
    that claim is asserted against: a list of dicts is diffable and orderable in a
    way a tuple of dataclasses is not.
    """
    rows = []
    for item in plan.items:
        row = dataclasses.asdict(item)
        row["block"] = item.block.value
        row["kind"] = item.kind.value
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Where this module knowingly runs ahead of the spec
# ---------------------------------------------------------------------------

SPEC_GAPS = (
    "§5.1 does not say whether the warm-up carries the click. Set TRUE here: the "
    "warm-up is planned at a specific bpm below the rung, and a tempo you are told "
    "to hold without a reference is not a tempo. Flip it in one place if wrong.",
    "§5.3's tempo_factor is undefined for a song with no song_skills rows. Read as "
    "readiness 0.0 -> 0.70, not 1.00 (header note (c)).",
    "§5.4 does not say what to do when the user has neither a can_play song nor a "
    "drill with history. Folded into the play-through with is_consolidation = true, "
    "which is exactly §2's own T=15 treatment rather than a new behaviour.",
    "§12 covers a thin BANK. A user with no SONG is the other direction and is not "
    "in the spec; the repertoire budget moves to the technique block before the "
    "fill so the slots are sized for it (header note (b)).",
)
