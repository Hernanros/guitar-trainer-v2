# server/app/sessions/ladder.py — FLE-4 §7, the tempo ladder controller (FLE-64).
#
# lifecycle.py records WHAT THE USER DID. This module is the only place where what
# they did changes what they are asked to do NEXT. Without it a pilot participant
# rates a drill, the rating lands on the item row, and tomorrow's generator plans
# exactly the same tempo — which is a session generator being tested, not a product.
#
# Three rules from FLE-4, and the constants they hang on live in plan.py (§1) rather
# than here, because §1 is explicitly "the numbers to argue with after the pilot" and
# splitting them across two modules is how one copy gets tuned and the other doesn't:
#
#   §7.1  Classify the attempt.     CLEAR / HOLD / MISS / SKIP.
#   §7.2  Apply ONE transition.     Nail it twice and you move up, miss it twice and
#                                   you drop back. Symmetric on purpose: one clear can
#                                   be luck and one miss can be a bad night.
#   §7.4  allow_push=false vetoes the promotion, and ONLY the promotion. Clears still
#         accumulate, so the push lands in the next session. Drops are never vetoed —
#         a player who is bailing out of sessions should not also be held at a tempo
#         they have now missed twice.
#
# The classifier's output is STORED on drill_attempts.outcome rather than recomputed
# at read time (§13). Recomputing would silently rewrite history every time the rubric
# is retuned, and the rubric is explicitly expected to be retuned after the pilot.
#
# ─── The failure this module is written against ────────────────────────────────────
#
# §7.1's second HOLD clause — a top rating after 5 of 20 reps is NOT a clear — is the
# whole reason completed_reps exists as a column. Get it wrong and the ladder inflates
# on partial attempts, the player ends up stranded three rungs above what they can
# hold, and the symptom is "the tempos feel arbitrary" reported weeks later by someone
# who has already stopped practising. So: missing data can never promote a rung.
# `completed_reps IS NULL` classifies HOLD, and so does `planned_reps IS NULL` — in
# both cases the fraction §7.1 asks about is unanswerable, and unanswerable is not a
# clear (FLE-21 §2.1, which pins the first case and whose reasoning covers the second).
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.sessions.plan import (
    CLEAR_REP_THRESHOLD,
    CLEARS_TO_PUSH,
    MISSES_TO_DROP,
    RUNG_STEP_BPM,
    round_to_5,
)

logger = logging.getLogger(__name__)

CLEAR = "CLEAR"
HOLD = "HOLD"
MISS = "MISS"
SKIP = "SKIP"

TOP_RATING = "thats_what_im_looking_for"
MID_RATING = "getting_closer"
LOW_RATING = "not_my_tempo"

# The drill_progress CHECK constraints from migration 0009, mirrored here so a drill
# with an eccentric start_bpm produces a clamped ladder rather than an IntegrityError
# that rolls back the user's whole rating write. drills.start_bpm has no range check
# of its own and is not guaranteed to be a multiple of 5.
MIN_RUNG_BPM = 20
MAX_RUNG_BPM = 400


def _clamp_rung(bpm: int) -> int:
    return max(MIN_RUNG_BPM, min(MAX_RUNG_BPM, bpm))


def initial_rung(start_bpm: int) -> int:
    """§7 — a ladder starts at the drill's floor, snapped to the 5-BPM grid.

    round_to_5 rather than a raw copy because `ck_drill_progress_rung_multiple_of_5`
    is a hard constraint and §1's grid is what makes the ±5 steps closed: a rung of
    63 would push to 68, 73, 78 and never land on a tempo the skill graph bins.
    """
    return _clamp_rung(round_to_5(start_bpm))


# ---------------------------------------------------------------------------
# §7.1 — classifying an attempt
# ---------------------------------------------------------------------------


def classify(
    rating: Optional[str],
    completed_reps: Optional[int],
    planned_reps: Optional[int],
) -> str:
    """One attempt → CLEAR | HOLD | MISS, per FLE-4 §7.1.

    SKIP is not produced here. A skip is the absence of an attempt ("a skip is not
    data", §7.2) and it arrives on a different endpoint, so a caller that wants to
    record one passes SKIP itself rather than asking the classifier to invent it
    from a null rating.
    """
    if rating == LOW_RATING:
        return MISS
    if rating == TOP_RATING:
        if completed_reps is None or planned_reps is None or planned_reps <= 0:
            # Unanswerable, therefore not a clear. See the module header.
            return HOLD
        return CLEAR if completed_reps >= CLEAR_REP_THRESHOLD * planned_reps else HOLD
    # getting_closer, and any rating the enum grows later, holds the rung. A verdict
    # the ladder does not understand must never move it.
    return HOLD


# ---------------------------------------------------------------------------
# §7.2 / §7.5 — the transition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LadderState:
    """The mutable half of a `drill_progress` row."""

    rung_bpm: int
    consecutive_clears: int
    consecutive_misses: int
    state: str
    mastered_at_set: bool = False


@dataclass(frozen=True)
class LadderTransition:
    outcome: str
    before: LadderState
    after: LadderState

    @property
    def pushed(self) -> bool:
        return self.after.rung_bpm > self.before.rung_bpm

    @property
    def dropped(self) -> bool:
        return self.after.rung_bpm < self.before.rung_bpm

    @property
    def newly_mastered(self) -> bool:
        return self.after.state == "mastered" and self.before.state != "mastered"


def transition(
    before: LadderState,
    outcome: str,
    *,
    allow_push: bool,
    re_entry: bool,
    start_bpm: int,
    target_bpm: int,
) -> LadderTransition:
    """Apply exactly one §7.2 transition. Pure — the caller owns the row write.

    Pure because every boundary in §7.2 is an off-by-one waiting to happen (does the
    SECOND clear push, or the third?) and those are cheap to test in Python and
    expensive to test through a transaction.
    """
    if outcome == SKIP:
        # §7.2 — a skip leaves every counter alone. Not even `attempts` moves; the
        # caller does that, and it does not call here for a skip.
        return LadderTransition(outcome=outcome, before=before, after=before)

    if outcome == HOLD:
        # Both counters reset. A hold is not evidence for OR against the rung, and
        # leaving `consecutive_clears` standing would let two clears separated by a
        # failed night push — which is precisely the "nail it twice" rule's claim
        # that it wants two clears IN A ROW.
        return LadderTransition(
            outcome=outcome,
            before=before,
            after=replace(before, consecutive_clears=0, consecutive_misses=0),
        )

    if outcome == MISS:
        misses = before.consecutive_misses + 1
        after = replace(before, consecutive_clears=0, consecutive_misses=misses)
        if misses >= MISSES_TO_DROP:
            # §7.2's floor is `drill.start_bpm`, taken here through initial_rung so
            # the floor is the same grid value the ladder was created at. A raw
            # start_bpm of 63 would otherwise make the floor un-pushable: 63 + 5 is
            # not a multiple of 5 and the rung CHECK would reject it.
            dropped = max(before.rung_bpm - RUNG_STEP_BPM, initial_rung(start_bpm))
            after = replace(after, rung_bpm=_clamp_rung(dropped), consecutive_misses=0)
            if before.state == "maintenance":
                # §7.5 — two misses in maintenance is the drill coming back off the
                # shelf. It re-enters `active` a rung down, and mastered_at is
                # already NULL on a maintenance row, so the CHECK pairing holds.
                after = replace(after, state="active")
        return LadderTransition(outcome=outcome, before=before, after=after)

    if outcome != CLEAR:  # pragma: no cover — defensive, the enum has four values
        raise ValueError(f"unknown outcome {outcome!r}")

    clears = before.consecutive_clears + 1
    after = replace(before, consecutive_clears=clears, consecutive_misses=0)
    if clears < CLEARS_TO_PUSH:
        return LadderTransition(outcome=outcome, before=before, after=after)

    # §7.4 — the session-level veto, checked FIRST and deliberately above every
    # other branch, exactly as §7.2's pseudocode orders it. When the whole session
    # is consolidating, nothing in it may promote: not a rung, not to mastered. The
    # clears stay banked on the row, so the push lands in the next session.
    if not allow_push:
        return LadderTransition(outcome=outcome, before=before, after=after)

    # §7.3 — a re-entry attempt was planned a rung BELOW the record. Clearing it
    # earns the record back, never more than it: layoff costs a rung of confidence,
    # never a rung of record.
    if re_entry:
        return LadderTransition(outcome=outcome, before=before, after=after)

    if before.state == "maintenance":
        # §7.5 — two clears in maintenance re-masters the drill and re-arms the
        # 14-day timer.
        return LadderTransition(
            outcome=outcome,
            before=before,
            after=replace(
                after, state="mastered", consecutive_clears=0, mastered_at_set=True
            ),
        )

    if before.state not in ("active", "maintenance"):
        # 'mastered' or 'retired'. §7.5 excludes mastered drills from technique
        # selection, so a rated attempt on one means it arrived as some other block's
        # item or the row changed underneath a stale plan. Record the clear, move
        # nothing: a mastered drill has no rung left to push and re-mastering an
        # already-mastered drill would re-arm its maintenance timer for free.
        return LadderTransition(outcome=outcome, before=before, after=after)

    if before.rung_bpm < target_bpm:
        pushed = _clamp_rung(before.rung_bpm + RUNG_STEP_BPM)
        return LadderTransition(
            outcome=outcome,
            before=before,
            after=replace(after, rung_bpm=pushed, consecutive_clears=0),
        )

    # At the top of the ladder, twice. §7.2 — that is what mastered means.
    return LadderTransition(
        outcome=outcome,
        before=before,
        after=replace(after, state="mastered", mastered_at_set=True),
    )


# ---------------------------------------------------------------------------
# The write path
# ---------------------------------------------------------------------------

# INSERT … ON CONFLICT DO UPDATE rather than DO NOTHING: DO UPDATE takes the row
# lock and RETURNINGs the existing row in one statement, so the read-modify-write
# below is serialised against a concurrent duplicate flush of the same rating.
# DO NOTHING returns no row on conflict and would need a second, unlocked SELECT —
# the exact gap through which two retries each apply a transition and the ladder
# moves twice for one attempt.
_UPSERT_PROGRESS_SQL = text(
    """
    INSERT INTO drill_progress (id, user_id, drill_id, rung_bpm)
    VALUES (:id, :uid, :did, :rung)
    ON CONFLICT ON CONSTRAINT uq_drill_progress_user_drill
    DO UPDATE SET updated_at = now()
    RETURNING id, rung_bpm, consecutive_clears, consecutive_misses, attempts,
              state::text AS state, mastered_at
    """
)

_APPLY_PROGRESS_SQL = text(
    """
    UPDATE drill_progress
       SET rung_bpm = :rung,
           consecutive_clears = :clears,
           consecutive_misses = :misses,
           attempts = attempts + 1,
           last_practiced_on = :day,
           state = CAST(:state AS drill_progress_state),
           mastered_at = CASE WHEN :state = 'mastered'
                              THEN COALESCE(mastered_at, now())
                              ELSE NULL END,
           updated_at = now()
     WHERE id = :pid
    RETURNING rung_bpm, consecutive_clears, consecutive_misses, attempts,
              state::text AS state
    """
)

# The append-only history row. FLE-4 §13's full record: what we asked for, what we
# got, and the verdict the classifier assigned — stored, not derived.
#
# `tempo_reached_bpm` is written ONLY on a CLEAR. It is 0006's column and means "the
# tempo the user actually held"; a MISS at 80 BPM is evidence they did NOT hold 80,
# and writing planned_bpm there regardless would turn the one honest tempo column in
# the table into a copy of the plan.
_INSERT_ATTEMPT_SQL = text(
    """
    INSERT INTO drill_attempts (
        id, drill_id, user_id, local_calendar_day, session_item_id,
        rung_bpm, planned_bpm, planned_reps, reps_completed, tempo_reached_bpm,
        rating, outcome, re_entry, duration_s, target_skill_node_id
    ) VALUES (
        :id, :did, :uid, :day, :item_id,
        :rung, :planned_bpm, :planned_reps, :completed_reps, :tempo_reached,
        CAST(:rating AS rating_level), CAST(:outcome AS drill_attempt_outcome),
        :re_entry, :duration_s, :node_id
    )
    RETURNING id
    """
)

_DRILL_BOUNDS_SQL = text("SELECT start_bpm, target_bpm FROM drills WHERE id = :did")


class DrillNotFound(Exception):
    """The item points at a drill that no longer exists. Caller decides the code."""


@dataclass(frozen=True)
class AttemptRecord:
    """What one rated drill item did to the bank. Returned so the router can say so."""

    attempt_id: UUID
    outcome: str
    rung_before: int
    rung_after: int
    progress_state: str
    pushed: bool
    dropped: bool
    push_withheld: bool


async def record_attempt(
    db: AsyncSession,
    *,
    user_id: UUID,
    drill_id: UUID,
    session_item_id: Optional[UUID],
    local_calendar_day: date,
    planned_bpm: Optional[int],
    planned_reps: Optional[int],
    completed_reps: Optional[int],
    rating: Optional[str],
    re_entry: bool,
    allow_push: bool,
    duration_s: Optional[int],
    target_skill_node_id: Optional[UUID],
) -> AttemptRecord:
    """Steps 2 and 3 of FLE-21 §5.2's fan-out, in the caller's transaction.

    Writes one `drill_attempts` row and applies exactly one §7.2 ladder transition.
    Both or neither — the caller holds the transaction open, so a failure here takes
    the item row's rating down with it rather than leaving history that disagrees
    with the ladder.
    """
    bounds = (await db.execute(_DRILL_BOUNDS_SQL, {"did": drill_id})).first()
    if bounds is None:
        raise DrillNotFound(f"drill {drill_id} no longer exists")

    row = (
        await db.execute(
            _UPSERT_PROGRESS_SQL,
            {
                "id": uuid.uuid4(),
                "uid": user_id,
                "did": drill_id,
                # Only used when the row is being created: §7's `init = start_bpm`.
                # A drill the user has never practised has no ladder until its first
                # rated attempt, which is why nothing before this point writes one.
                "rung": initial_rung(int(bounds.start_bpm)),
            },
        )
    ).first()
    assert row is not None  # ON CONFLICT DO UPDATE always returns the row

    before = LadderState(
        rung_bpm=int(row.rung_bpm),
        consecutive_clears=int(row.consecutive_clears),
        consecutive_misses=int(row.consecutive_misses),
        state=row.state,
        mastered_at_set=row.mastered_at is not None,
    )
    outcome = classify(rating, completed_reps, planned_reps)
    moved = transition(
        before,
        outcome,
        allow_push=allow_push,
        re_entry=re_entry,
        start_bpm=int(bounds.start_bpm),
        target_bpm=int(bounds.target_bpm),
    )

    applied = (
        await db.execute(
            _APPLY_PROGRESS_SQL,
            {
                "pid": row.id,
                "rung": moved.after.rung_bpm,
                "clears": moved.after.consecutive_clears,
                "misses": moved.after.consecutive_misses,
                "state": moved.after.state,
                "day": local_calendar_day,
            },
        )
    ).first()
    assert applied is not None

    attempt_id = (
        await db.execute(
            _INSERT_ATTEMPT_SQL,
            {
                "id": uuid.uuid4(),
                "did": drill_id,
                "uid": user_id,
                "day": local_calendar_day,
                "item_id": session_item_id,
                # The tempo of RECORD is the rung BEFORE this attempt moved it —
                # an attempt is evidence about the tempo it was taken at, and
                # storing the post-push rung would date every clear one rung high.
                "rung": before.rung_bpm,
                "planned_bpm": planned_bpm,
                "planned_reps": planned_reps,
                "completed_reps": completed_reps,
                "tempo_reached": planned_bpm if outcome == CLEAR else None,
                "rating": rating,
                "outcome": outcome,
                "re_entry": re_entry,
                "duration_s": duration_s,
                "node_id": target_skill_node_id,
            },
        )
    ).scalar_one()

    # §7.4's veto is invisible in the row it did not change, so it is logged. During
    # the pilot "why did my tempo not move after two good nights" is a question with
    # a real answer — the session was consolidating — and this is where that answer
    # is recoverable from.
    push_withheld = (
        outcome == CLEAR
        and moved.after.consecutive_clears >= CLEARS_TO_PUSH
        and not moved.pushed
        and not moved.newly_mastered
    )
    if push_withheld:
        logger.info(
            "ladder push withheld: drill=%s user=%s rung=%s clears=%s "
            "(allow_push=%s re_entry=%s state=%s) — FLE-4 §7.4/§7.3",
            drill_id,
            user_id,
            before.rung_bpm,
            moved.after.consecutive_clears,
            allow_push,
            re_entry,
            before.state,
        )

    return AttemptRecord(
        attempt_id=attempt_id,
        outcome=outcome,
        rung_before=before.rung_bpm,
        rung_after=moved.after.rung_bpm,
        progress_state=moved.after.state,
        pushed=moved.pushed,
        dropped=moved.dropped,
        push_withheld=push_withheld,
    )


# ---------------------------------------------------------------------------
# §5.2 step 4 — the repertoire item's daily verdict
# ---------------------------------------------------------------------------

# ON CONFLICT DO NOTHING against the partial expression index, NOT a pre-SELECT plus
# a caught IntegrityError. The difference matters: an IntegrityError inside the
# caller's `async with db.begin()` poisons the transaction, so the user's rating —
# already written to the item row above — would roll back with it. A duplicate daily
# verdict is a state-sync signal (FLE-21 §5.2, UI-SPEC §10), never a reason to lose
# telemetry the player will not send again.
#
# The conflict target repeats the index's expression AND its predicate because
# `uq_user_sessions_daily_rating` is both partial and expression-based; Postgres
# infers it only from an exact match.
_INSERT_VERDICT_SQL = text(
    """
    INSERT INTO user_sessions (
        id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes,
        is_reroll_marker, is_daily_pick_marker
    ) VALUES (
        :id, :uid, :song_id, CAST(:rating AS rating_level), :day, :tz, false, false
    )
    ON CONFLICT (user_id, song_id, local_calendar_day, COALESCE(drill_index, -1))
        WHERE is_reroll_marker = false AND is_daily_pick_marker = false
    DO NOTHING
    RETURNING id
    """
)


@dataclass(frozen=True)
class VerdictRecord:
    """Whether the daily verdict landed, or a verdict for the day already existed."""

    verdict_id: Optional[UUID]
    conflicted: bool


async def record_daily_verdict(
    db: AsyncSession,
    *,
    user_id: UUID,
    song_id: int,
    rating: str,
    local_calendar_day: date,
    tz_offset_minutes: int,
) -> VerdictRecord:
    """Step 4 — the rated repertoire item's `user_sessions` row.

    `drill_index` and `target_skill_node_id` are both left NULL. This is the
    WHOLE-SONG verdict slot (the COALESCE(-1) sentinel), and the table's
    `ck_drill_index_pairs_skill_node` enforces both-or-neither, so writing the
    item's section node here without a drill index would be rejected — correctly:
    a section node is not a drill slot.
    """
    row = (
        await db.execute(
            _INSERT_VERDICT_SQL,
            {
                "id": uuid.uuid4(),
                "uid": user_id,
                "song_id": song_id,
                "rating": rating,
                "day": local_calendar_day,
                "tz": tz_offset_minutes,
            },
        )
    ).first()
    if row is None:
        logger.info(
            "daily verdict already recorded for user=%s song=%s day=%s — "
            "answering 409 as a state-sync signal (FLE-21 §5.2)",
            user_id,
            song_id,
            local_calendar_day,
        )
        return VerdictRecord(verdict_id=None, conflicted=True)
    return VerdictRecord(verdict_id=row.id, conflicted=False)
