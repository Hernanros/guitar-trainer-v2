# server/app/sessions/sections.py — FLE-101 §3, the graduation predicate.
#
# The one question this module answers: HAS THIS PLAYER EARNED THIS PASSAGE YET?
#
# FLE-89's verdict in Hernan's words — "it's unclear how to go from the drills to
# the song itself, it should be made in a way that graduating from all drills
# enables you play a part of the song." Before this module, finishing a drill moved
# a hidden `skill_nodes.mastery` float and nothing unlocked. There was nothing to
# graduate to.
#
# ─── Three decisions worth reading before changing anything here ──────────────────
#
# 1. NO NEW STATE. A section's cleared-ness is DERIVED, never stored: it is a query
#    over `drill_progress.state` and the §2 links, computed at read time. A stored
#    `section_cleared` row would be a second source of truth that drifts the moment
#    a drill is retired, re-mastered off maintenance, or deduped — and every one of
#    those already moves the underlying rows. Deriving costs one query; storing
#    costs a reconciliation job nobody will write.
#
# 2. A SECTION WITH ZERO REQUIRED DRILLS IS CLEARED (design §3). You cannot gate a
#    reward on drills that do not exist. This single rule is also what makes §5's
#    backward compatibility work with no second code path: an unsectioned breakdown
#    is one implicit section, that section has no linked drills, so it is cleared,
#    so nothing is gated — which is exactly how the 10 cached breakdowns in prod
#    behave today. A model-emitted section that nothing prepares is a CONTENT bug;
#    it is logged (`zero_required` on the state) and never blocks the player.
#
# 3. SECTIONS UNLOCK INDEPENDENTLY, not in a chain. Clearing the Solo's drills
#    unlocks Play-the-Solo whether or not the Intro is done. A hard dependency chain
#    would let one bad model-generated drill soft-lock the rest of the song, and the
#    issue asks for an EARNING ORDER, which is a presentation question (design §6),
#    not a gating one.
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.song import Breakdown, Section, tab_beat_count

logger = logging.getLogger(__name__)

# §3 — the `drill_progress.state` values that count as "the player owns this drill".
#
# WHAT THE BAR ACTUALLY IS. Design §3 flagged this for verification rather than
# assuming it, so: `app/sessions/ladder.transition` writes 'mastered' in exactly two
# places and both mean the same thing — the player held the TOP rung of the ladder
# (rung_bpm == the drill's target_bpm) and cleared it CLEARS_TO_PUSH times in a row,
# with §7.4's consolidation veto not firing and the attempt not being a §7.3
# re-entry. "Reached target_bpm with clears, twice" is the real bar, which is the
# bar the design assumed. Section-clearing inherits it unchanged.
#
# WHY 'maintenance' IS IN THIS SET. §7.5 shelves a mastered drill into maintenance
# once its 14-day timer rolls over, and maintenance is reachable ONLY from mastered
# — it means "earned, and now due for upkeep", never "not yet earned". Counting only
# 'mastered' would therefore RE-LOCK a passage the player has already been shown and
# already played, on a clock, for doing nothing wrong. Taking a reward back is worse
# than granting it early, and this is the whole feature's credibility.
#
# Nothing writes 'maintenance' today — the shelving job is not built yet — so this
# is a latent case rather than a live one. It is handled here because the state is
# already in the enum and already an accepted INPUT to `ladder.transition`, so the
# job landing later would otherwise silently re-lock every cleared section.
OWNED_STATES = ("mastered", "maintenance")


@dataclass(frozen=True)
class SectionState:
    """One section plus this player's standing in it. What the lock/unlock card reads."""

    label: str
    start_beat: int
    end_beat: int
    required_drills: int
    mastered_drills: int
    cleared: bool

    @property
    def remaining(self) -> int:
        return max(self.required_drills - self.mastered_drills, 0)

    @property
    def zero_required(self) -> bool:
        """A section the model emitted that no drill prepares — see decision 2 above."""
        return self.required_drills == 0


def resolve_sections(breakdown: Breakdown) -> list[Section]:
    """§1/§5 — the sections of a breakdown, as the player-facing layer should see them.

    Returns the EXPLICIT sections only. An unsectioned breakdown returns `[]`, and
    that empty list is the full §5 story: there is no card to render, nothing is
    gated, and the implicit whole-song section the design describes is already-cleared
    by decision 2 — so materialising it here would add a row the UI must then learn
    to hide. "One implicit cleared section" and "no sections" are the same
    observable behaviour, and the second is the one that cannot be rendered wrong.

    The implicit section is not fictional, though: it is what `section_for_drill`
    returns None for, which is how a drill with no link is kept from ever firing an
    unlock. That is the only place the concept has to exist.

    `Breakdown` validation has already guaranteed the list is a sound partition of
    the tab or is empty, so this does no checking of its own.
    """
    return list(breakdown.sections)


def section_for_drill(
    prepares_section: Optional[str],
    section_start_beat: Optional[int],
    section_end_beat: Optional[int],
) -> Optional[str]:
    """The section label a banked drill prepares, or None for the implicit section.

    None is the answer for every drill banked before FLE-101. It means "this drill
    belongs to the already-cleared whole-song section", and the caller must read it
    as *there is no reward gated on this drill*, never as *this drill blocks
    something*. All three columns are required to be present together
    (ck_drills_section_link_complete); a partial link is treated as no link rather
    than trusted halfway, matching `Breakdown._link_drills_to_sections`.
    """
    if prepares_section is None or section_start_beat is None or section_end_beat is None:
        return None
    return prepares_section


# §3's required-drill set, for every section of one song at once.
#
# `status = 'active'` is the whole eligibility rule and it carries two meanings that
# happen to coincide, which is worth stating because they will not always:
#   - retired and pending_review rows are not served, so gating on them would make a
#     section unclearable by a drill the player can never be handed;
#   - `ck_drills_status_matches_canonical` makes an active row canonical by
#     construction (canonical_drill_id IS NULL), so `dp.drill_id = d.id` IS the
#     canonical progress read the design asks for — no COALESCE needed. A collapsed
#     duplicate contributes nothing, which is correct: its canonical row is the one
#     the player is actually served.
#
# LEFT JOIN on drill_progress: a drill the player has never attempted has no row at
# all, and absent must read as not-mastered rather than as a missing join dropping
# the requirement entirely. An inner join here would make an untouched section
# "0 of 0 drills" and auto-clear it — the single most expensive mistake available in
# this file, since it would hand out every reward in the app for free.
_SECTION_TALLY_SQL = text(
    """
    SELECT d.prepares_section AS label,
           COUNT(*) AS required,
           COUNT(*) FILTER (
               WHERE dp.state::text = ANY(:owned)
           ) AS mastered
      FROM drills d
      LEFT JOIN drill_progress dp
             ON dp.drill_id = d.id
            AND dp.user_id = :uid
     WHERE d.origin_song_id = :sid
       AND d.prepares_section IS NOT NULL
       AND d.status = 'active'
     GROUP BY d.prepares_section
    """
)


async def section_states(
    db: AsyncSession,
    *,
    user_id: UUID,
    song_id: int,
    breakdown: Breakdown,
) -> list[SectionState]:
    """Every section of this song with this player's standing in it, in tab order.

    One query regardless of section count. Sections the breakdown declares but no
    drill prepares come back `required_drills=0, cleared=True` — decision 2.
    """
    rows = (
        await db.execute(
            _SECTION_TALLY_SQL,
            {"uid": user_id, "sid": song_id, "owned": list(OWNED_STATES)},
        )
    ).all()
    tally = {r.label: (int(r.required), int(r.mastered)) for r in rows}

    sections = resolve_sections(breakdown)
    orphaned = set(tally) - {s.label for s in sections}
    if orphaned:
        # Banked drills pointing at a label this breakdown no longer emits. Possible
        # whenever a song's breakdown is regenerated under a different segmentation
        # while its old drills stay in the bank — the drills outlive the breakdown
        # by design (origin_song_id is ON DELETE SET NULL for the same reason).
        # Logged, not repaired: silently re-pointing them would invent a claim about
        # a passage nobody measured, and they are simply never required by anything.
        logger.info(
            "song %s has banked drills preparing %s, which the current breakdown "
            "does not emit (%s) — those drills gate nothing (FLE-101 §3).",
            song_id, sorted(orphaned), sorted(s.label for s in sections),
        )

    states = []
    for s in sections:
        required, mastered = tally.get(s.label, (0, 0))
        states.append(
            SectionState(
                label=s.label,
                start_beat=s.start_beat,
                end_beat=s.end_beat,
                required_drills=required,
                mastered_drills=mastered,
                cleared=mastered >= required,
            )
        )

    # Every section of a sectioned song gating on nothing. Decision 2 means the
    # player is handed all of them for free, which is the "teaches nothing" case, so
    # it is logged at WARNING rather than left to be noticed as a happy player.
    #
    # The expected cause is not a content defect but a known hole in the pipeline:
    # NOTHING IN app/ EVER INSERTS INTO THE DRILLS BANK. A breakdown's drills live
    # in `songs.breakdown` JSONB, and `scripts/backfill_drills.py` is the only code
    # that promotes them into the `drills` table this predicate reads. Until that
    # promotion happens on the generation path, a freshly generated song's sections
    # have zero required drills and unlock on sight. Tracked separately — the fix is
    # a promotion step, not a change to this rule.
    if states and all(s.zero_required for s in states):
        logger.warning(
            "song %s emitted %d section(s) but NO banked drill prepares any of "
            "them — every section is auto-cleared and unlocks for free. Most "
            "likely its drills were never promoted into the bank "
            "(scripts/backfill_drills.py). FLE-101 §3.",
            song_id, len(states),
        )
    return states


# The one drill's own row — origin song and section link — read back after its
# ladder moved. Narrow on purpose: this runs inside the rating transaction on the
# player's critical path, so it reads three columns by primary key.
_DRILL_LINK_SQL = text(
    """
    SELECT origin_song_id, prepares_section, section_start_beat, section_end_beat
      FROM drills
     WHERE id = :did
    """
)


@dataclass(frozen=True)
class SectionUnlock:
    """A section that this attempt just earned. The payload behind the unlock moment."""

    song_id: int
    label: str
    start_beat: int
    end_beat: int


async def unlock_for_mastered_drill(
    db: AsyncSession,
    *,
    user_id: UUID,
    drill_id: UUID,
) -> Optional[SectionUnlock]:
    """The section this just-mastered drill completed, if it completed one.

    CALL ONLY WHEN THE LADDER REPORTED `newly_mastered`. That precondition is what
    makes "cleared" and "NEWLY cleared" the same answer here, with no previous state
    to compare against: this drill is required by the section (it carries the link),
    and a moment ago it was not mastered — so the section cannot have been cleared
    before this attempt. Calling it on an already-mastered drill would re-fire an
    unlock the player has already been shown.

    Returns None, meaning "no reward moment", in all the ordinary cases:
      * the drill has no section link — the implicit §5 section, already cleared;
      * its origin song is gone (origin_song_id SET NULL) or its breakdown no longer
        emits that label — nothing to open;
      * siblings in the section are still unmastered.

    Never raises on the player's path. A rating that has already been committed must
    not be rolled back because a reward could not be computed, so a surprise here is
    logged and swallowed by the caller's own guard — see lifecycle `_fan_out`.
    """
    row = (await db.execute(_DRILL_LINK_SQL, {"did": drill_id})).first()
    if row is None:
        return None
    label = section_for_drill(
        row.prepares_section, row.section_start_beat, row.section_end_beat
    )
    if label is None or row.origin_song_id is None:
        return None

    song_id = int(row.origin_song_id)
    breakdown = await _load_breakdown(db, song_id)
    if breakdown is None:
        return None

    for state in await section_states(
        db, user_id=user_id, song_id=song_id, breakdown=breakdown
    ):
        if state.label != label:
            continue
        if not state.cleared:
            return None
        logger.info(
            "section unlocked: user=%s song=%s section=%r (%d drills) — FLE-101 §3",
            user_id, song_id, label, state.required_drills,
        )
        return SectionUnlock(
            song_id=song_id,
            label=state.label,
            start_beat=state.start_beat,
            end_beat=state.end_beat,
        )
    # The label the drill prepares is not among the breakdown's current sections —
    # the orphan case `section_states` already logged. Nothing to unlock.
    return None


_SONG_BREAKDOWN_SQL = text("SELECT breakdown FROM songs WHERE id = :sid")


async def _load_breakdown(db: AsyncSession, song_id: int) -> Optional[Breakdown]:
    """Parse `songs.breakdown`, or None if it is absent or not a full breakdown.

    Placeholder JSONB is a real stored value (see `is_renderable_breakdown`), and a
    row cached before any given schema change is the normal case rather than the
    exception, so a parse failure here is a reason to award no unlock — never to
    fail the rating that triggered it.
    """
    raw = (await db.execute(_SONG_BREAKDOWN_SQL, {"sid": song_id})).scalar_one_or_none()
    if not isinstance(raw, dict):
        return None
    try:
        return Breakdown.model_validate(raw)
    except Exception as e:
        logger.warning(
            "song %s breakdown did not parse while resolving a section unlock "
            "(%s) — no unlock awarded.", song_id, e,
        )
        return None


def section_beat_window(breakdown: Breakdown, label: str) -> Optional[tuple[int, int]]:
    """The (start, end) beat columns the *Play the Section* screen renders.

    Separate from `SectionState` so the player screen can resolve a passage from the
    breakdown alone, with no per-user query — it is reached from an already-unlocked
    card and re-deriving the lock state to draw a tab would be a second read of the
    same answer. Bounds are clamped to the tab, which costs nothing and means a
    renderer cannot be handed a slice past the end of the measures.
    """
    n = tab_beat_count(breakdown.tab)
    for s in breakdown.sections:
        if s.label == label:
            return max(s.start_beat, 0), min(s.end_beat, n - 1)
    return None
