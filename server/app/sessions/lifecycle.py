# server/app/sessions/lifecycle.py — the telemetry half of the session engine (FLE-21).
#
# store.py writes the PLAN. This module writes what the user then DID with it, and it
# exists because of one sentence in FLE-21: if the server only persists the generated
# session plus final ratings, the pilot readout silently degrades to self-report.
# Participants sincerely over-report practice. The day-3/7/14 check-ins are designed to
# CORROBORATE behaviour, not to constitute it, and FLE-10 correctly forbids an analytics
# SDK in a pilot build — so these five writes are the only place the numbers can come
# from. Every one of FLE-13's questions is answered by a column this module maintains.
#
# The design is dominated by a single fact about the client: the player's transitions
# are fire-and-forget through an offline outbox. That means the writes arrive LATE, they
# arrive OUT OF ORDER, and they arrive TWICE. A handler that assumes `enter` precedes
# `complete` will strand items `in_progress` forever, and stranded items are counted as
# neither reached nor skipped — which quietly inflates every completion rate in the
# readout. So:
#
#   * Item state is a MONOTONIC LATTICE (FLE-21 §5.3, R9):
#
#         not_reached ──► in_progress ──► {completed, skipped}
#
#     A transition that would move BACKWARDS is a no-op. A late `enter` never
#     resurrects a terminal item; a dropped `enter` never costs you the `complete`.
#     `completed` ⇄ `skipped` is last-write-wins. A terminal transition landing on a
#     `not_reached` item is LEGAL and applies, back-filling started_at with the server
#     receipt time — the honest approximation, and better than an item the user
#     demonstrably finished having no record at all.
#
#   * `last_item_index_reached` is GREATEST(existing, index). Never decreases, because
#     "where did they bail" is a high-water mark, not a cursor.
#
# The second fact is that the session is a lifecycle, not an event: it can go terminal
# UNDERNEATH a player that is still running (§3.1 — practising at 23:55, still playing
# at 00:05, day-roll sweep fires). Item writes against a terminal session are therefore
# ACCEPTED AND RECORDED, and they do not resurrect it. The one exception is honest and
# deliberate: an explicit POST …/complete outranks a verdict the CLOCK reached, because
# a user who says they finished did finish. Since FLE-21 R7 deleted `superseded`, every
# abandonment is the clock's, so every abandonment is overridable this way.
#
# Why `started_at` is back-filled on item events as well as on /start (FLE-21 §1 defines
# it as "first item entered"): /start is fire-and-forget like everything else, so it can
# be the one POST the outbox loses. A session with item activity and a NULL started_at
# would be dropped from the day-7 return metric AND from the completion-rate denominator
# — the exact silent degradation this issue exists to prevent. Back-filling makes a lost
# /start cost a timestamp's precision instead of the whole session.
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.sessions import ladder

logger = logging.getLogger(__name__)

OPEN_STATES = ("planned", "in_progress")

# FLE-21 §6. `active_seconds` is NOT paused on app-background or screen-lock — the
# product's core behaviour is a phone propped up with both hands on a guitar, and
# pausing on background would systematically undercount exactly that. The cost of the
# rule is a forgotten session inflating the number, so the value is clamped on write.
#
# 4x is generous ON PURPOSE. FLE-4 §6 permits deliberate overrun and only OFFERS
# auto-advance at 1.5x, so this has to catch a phone left on a music stand overnight
# without catching a user taking their time.
ACTIVE_SECONDS_CLAMP_FACTOR = 4

# FLE-21 §3's backstop condition. Purely protection against a corrupt
# tz_offset_minutes — the day-roll rule below is the real mechanism. This should fire
# approximately never; when it does, it is a bug report and not a metric, which is why
# the sweep logs it at WARNING.
TIMEOUT_INTERVAL = "36 hours"


class SessionNotFound(Exception):
    """No such session for this user. Router maps to 404."""


class ItemNotFound(Exception):
    """No item at that index in this session. Router maps to 404."""


class RatingNotPermitted(Exception):
    """A rating arrived for an item whose `rated` flag is false (FLE-21 §5.1).

    Router maps to 422 `rating_not_permitted_for_item`. It can only arise from a client
    bug, and FLE-4 §5.4 is explicit that a rating on the consolidation item "converts
    the win back into an assessment" — so this is rejected rather than stored. The
    player's outbox must treat 4xx as terminal: drop, do not retry.
    """


@dataclass(frozen=True)
class SessionRow:
    """The session header, as the readout reads it."""

    id: UUID
    user_id: UUID
    local_calendar_day: date
    tz_offset_minutes: int
    song_id: Optional[int]
    target_minutes: int
    mode: str
    # FLE-4 §7.4. Read by the §5.2 fan-out, and read from the SESSION rather than
    # recomputed per drill: "the entire session consolidates" is the rule, and a
    # per-item re-derivation would be a second place for it to disagree with itself.
    allow_push: bool
    state: str
    terminal_reason: Optional[str]
    generated_at: datetime
    started_at: Optional[datetime]
    last_activity_at: datetime
    ended_at: Optional[datetime]
    completion_ratio: Optional[Decimal]
    elapsed_active_seconds: int
    item_count: int
    last_item_index_reached: Optional[int]

    @property
    def is_terminal(self) -> bool:
        return self.state in ("completed", "abandoned")


@dataclass(frozen=True)
class FanOut:
    """What a rated `/complete` did beyond the item row — FLE-21 §5.2 steps 2-4.

    Every field is None/false on the paths that fan out to nothing: an unrated
    complete, a skip, and — importantly — a DUPLICATE rated complete. The fan-out is
    once-per-attempt (see `_terminal_item`), so a retried flush answers 200 with an
    empty FanOut rather than moving the ladder a second time.
    """

    attempt_id: Optional[UUID] = None
    outcome: Optional[str] = None
    rung_before: Optional[int] = None
    rung_after: Optional[int] = None
    progress_state: Optional[str] = None
    pushed: bool = False
    dropped: bool = False
    push_withheld: bool = False
    daily_verdict_written: bool = False
    daily_verdict_conflict: bool = False

    @property
    def is_empty(self) -> bool:
        return self.attempt_id is None and not (
            self.daily_verdict_written or self.daily_verdict_conflict
        )


@dataclass(frozen=True)
class ItemOutcome:
    """The result of one item transition, including whether it changed anything.

    `applied` is false when the lattice rejected a backwards move. The router still
    answers 200 — the write is idempotent, not failed — but the distinction is what
    keeps a retried `enter` from bumping `last_activity_at` on a finished session and
    making an abandoned row look freshly active to the sweep.
    """

    item_index: int
    state: str
    applied: bool
    active_seconds: int
    clamped: bool
    fanout: FanOut = FanOut()


_SESSION_COLUMNS = """
    id, user_id, local_calendar_day, tz_offset_minutes, song_id, target_minutes,
    mode::text AS mode, allow_push, state::text AS state,
    terminal_reason::text AS terminal_reason,
    generated_at, started_at, last_activity_at, ended_at,
    completion_ratio, elapsed_active_seconds, item_count, last_item_index_reached
"""

_LOAD_SESSION_SQL = text(
    f"SELECT {_SESSION_COLUMNS} FROM practice_sessions WHERE id = :sid AND user_id = :uid"
)

# The resume read (FLE-21 §5, `GET …/current`). Never writes — a resume check that
# mutated would make merely LOOKING at the app indistinguishable from practising.
_CURRENT_SESSION_SQL = text(
    f"""
    SELECT {_SESSION_COLUMNS}
      FROM practice_sessions
     WHERE user_id = :uid
       AND local_calendar_day = :day
       AND state = ANY(:open_states)
     ORDER BY generated_at DESC
     LIMIT 1
    """
)

_LOAD_ITEMS_SQL = text(
    """
    SELECT id, item_index, block::text AS block, kind::text AS kind, drill_id, song_id,
           target_skill_node_id, planned_seconds, planned_bpm, planned_reps,
           completed_reps, rated, skippable, click_enabled, re_entry, repeat_ok,
           is_consolidation, state::text AS state, started_at, ended_at,
           active_seconds, rating::text AS rating, advance_mode::text AS advance_mode
      FROM practice_session_items
     WHERE session_id = :sid
     ORDER BY item_index
    """
)


def _row_to_session(row: Any) -> SessionRow:
    return SessionRow(
        id=row.id,
        user_id=row.user_id,
        local_calendar_day=row.local_calendar_day,
        tz_offset_minutes=row.tz_offset_minutes,
        song_id=row.song_id,
        target_minutes=row.target_minutes,
        mode=row.mode,
        allow_push=row.allow_push,
        state=row.state,
        terminal_reason=row.terminal_reason,
        generated_at=row.generated_at,
        started_at=row.started_at,
        last_activity_at=row.last_activity_at,
        ended_at=row.ended_at,
        completion_ratio=row.completion_ratio,
        elapsed_active_seconds=row.elapsed_active_seconds,
        item_count=row.item_count,
        last_item_index_reached=row.last_item_index_reached,
    )


async def load_session(db: AsyncSession, user_id: UUID, session_id: UUID) -> SessionRow:
    """The session, or SessionNotFound.

    The user_id predicate is access control, not a convenience: without it a crafted
    session UUID lets one pilot participant write telemetry onto another's record, and
    the readout has no way to tell afterwards.
    """
    row = (
        await db.execute(_LOAD_SESSION_SQL, {"sid": session_id, "uid": user_id})
    ).first()
    if row is None:
        raise SessionNotFound(f"session {session_id} not found for user {user_id}")
    return _row_to_session(row)


async def load_items(db: AsyncSession, session_id: UUID) -> list[Any]:
    """The ordered plan rows. Every item exists from generation time (FLE-21 §2)."""
    return list((await db.execute(_LOAD_ITEMS_SQL, {"sid": session_id})).all())


async def get_current_session(
    db: AsyncSession, user_id: UUID, *, today: date
) -> Optional[SessionRow]:
    """The caller's open session for `today`, or None. Read-only by contract."""
    row = (
        await db.execute(
            _CURRENT_SESSION_SQL,
            {"uid": user_id, "day": today, "open_states": list(OPEN_STATES)},
        )
    ).first()
    return None if row is None else _row_to_session(row)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

# Write-once by COALESCE rather than by a guarded UPDATE (FLE-21 R5/F5). The player
# calls this on entering the first item, and the outbox may call it again on a retry
# or after a resume; the second call must not move the timestamp, because started_at
# is the day-7 return metric and a resume would otherwise re-date the session.
#
# The state CASE promotes only from 'planned'. A terminal session is NOT resurrected
# (§3.1) — but its started_at is still back-filled, because a /start that arrives late
# is evidence the user was there, and the alternative is dropping the session from the
# completion-rate denominator entirely.
_START_SQL = text(
    f"""
    UPDATE practice_sessions
       SET started_at = COALESCE(started_at, now()),
           last_activity_at = now(),
           state = CASE WHEN state = 'planned'
                        THEN 'in_progress'::session_state
                        ELSE state END
     WHERE id = :sid AND user_id = :uid
    RETURNING {_SESSION_COLUMNS}
    """
)


async def start_session(db: AsyncSession, user_id: UUID, session_id: UUID) -> SessionRow:
    """Mark the session opened. Idempotent; a second call is a no-op 200."""
    row = (await db.execute(_START_SQL, {"sid": session_id, "uid": user_id})).first()
    if row is None:
        raise SessionNotFound(f"session {session_id} not found for user {user_id}")
    return _row_to_session(row)


# ---------------------------------------------------------------------------
# Item transitions
# ---------------------------------------------------------------------------

# `enter` is the ONLY transition with a state guard, because it is the only one that
# moves UP the lattice into a non-terminal state. Everything terminal is allowed to
# land on anything (§5.3), so only this one can arrive "backwards".
_ENTER_ITEM_SQL = text(
    """
    UPDATE practice_session_items
       SET state = 'in_progress'::session_item_state,
           started_at = COALESCE(started_at, now())
     WHERE session_id = :sid
       AND item_index = :idx
       AND state = 'not_reached'
    RETURNING item_index, state::text AS state, active_seconds, planned_seconds
    """
)

# The terminal write. `completed` and `skipped` share it because they differ only in
# the value stored — both are rank-2 in the lattice, both accept the terminal-only
# mid-item progress fields (§5.3), and both are last-write-wins against each other.
#
# COALESCE on rating/completed_reps/advance_mode rather than straight assignment: a
# duplicate flush of an UNRATED complete must not erase a rating the user actually
# tapped. Losing a rating to a retry is a data loss the user can see.
_TERMINAL_ITEM_SQL = text(
    """
    UPDATE practice_session_items
       SET state = CAST(:new_state AS session_item_state),
           started_at = COALESCE(started_at, now()),
           ended_at = now(),
           active_seconds = LEAST(
               :active_seconds, :clamp_factor * planned_seconds
           ),
           completed_reps = COALESCE(:completed_reps, completed_reps),
           rating = COALESCE(CAST(:rating AS rating_level), rating),
           advance_mode = COALESCE(CAST(:advance_mode AS advance_mode), advance_mode)
     WHERE session_id = :sid AND item_index = :idx
    RETURNING item_index, state::text AS state, active_seconds, planned_seconds,
              completed_reps
    """
)

_ITEM_FLAGS_SQL = text(
    """
    SELECT state::text AS state, rated, planned_seconds
      FROM practice_session_items
     WHERE session_id = :sid AND item_index = :idx
    """
)

# The terminal path's read, and it is a DIFFERENT statement from the one above for
# exactly one reason: FOR UPDATE.
#
# FLE-21 §5.2's fan-out must be applied ONCE per attempt (FLE-4 §7.2 says so in the
# section heading), and the once-guard is "this item had no rating before now".
# Unlocked, two duplicate flushes of the same rated complete both read rating = NULL,
# both pass the guard, and the ladder moves twice for one attempt — a silent double
# push that looks exactly like a player improving faster than they are. The row lock
# makes the second flush wait, re-read its own rating, and fan out to nothing.
#
# Held only for the remainder of the caller's transaction, and contention is per
# (session, item): two writers for one item is precisely the duplicate this serialises.
_ITEM_PLAN_FOR_UPDATE_SQL = text(
    """
    SELECT id, state::text AS state, rated, planned_seconds,
           kind::text AS kind, block::text AS block,
           drill_id, song_id, target_skill_node_id,
           planned_bpm, planned_reps, re_entry,
           rating::text AS rating
      FROM practice_session_items
     WHERE session_id = :sid AND item_index = :idx
     FOR UPDATE
    """
)

# Bumped after every applied item event. Four things move together here and they are
# together on purpose — a caller that forgot one would leave the readout with a
# high-water mark that disagrees with the item rows.
#
# elapsed_active_seconds is RECOMPUTED as the sum rather than incremented, so a
# duplicate flush cannot double-count it. FLE-21 §4 leans on this number being
# comparable with `ended_at - started_at`; an accumulator that drifts upward on every
# retry would make every participant look like they practise in fragments.
_BUMP_SESSION_SQL = text(
    f"""
    UPDATE practice_sessions
       SET last_activity_at = now(),
           last_item_index_reached = GREATEST(
               COALESCE(last_item_index_reached, 0), :idx
           ),
           started_at = COALESCE(started_at, now()),
           elapsed_active_seconds = (
               SELECT COALESCE(SUM(active_seconds), 0)
                 FROM practice_session_items WHERE session_id = :sid
           ),
           state = CASE WHEN state = 'planned'
                        THEN 'in_progress'::session_state
                        ELSE state END
     WHERE id = :sid
    RETURNING {_SESSION_COLUMNS}
    """
)


async def _bump_session(db: AsyncSession, session_id: UUID, item_index: int) -> SessionRow:
    row = (
        await db.execute(_BUMP_SESSION_SQL, {"sid": session_id, "idx": item_index})
    ).first()
    assert row is not None  # caller already proved the session exists and is owned
    return _row_to_session(row)


async def _item_flags(db: AsyncSession, session_id: UUID, item_index: int) -> Any:
    row = (
        await db.execute(_ITEM_FLAGS_SQL, {"sid": session_id, "idx": item_index})
    ).first()
    if row is None:
        raise ItemNotFound(f"no item at index {item_index} in session {session_id}")
    return row


async def _item_plan_locked(db: AsyncSession, session_id: UUID, item_index: int) -> Any:
    row = (
        await db.execute(
            _ITEM_PLAN_FOR_UPDATE_SQL, {"sid": session_id, "idx": item_index}
        )
    ).first()
    if row is None:
        raise ItemNotFound(f"no item at index {item_index} in session {session_id}")
    return row


async def enter_item(
    db: AsyncSession, user_id: UUID, session_id: UUID, item_index: int
) -> tuple[ItemOutcome, SessionRow]:
    """Item → in_progress. A late arrival on a terminal item is a no-op 200."""
    await load_session(db, user_id, session_id)  # ownership + existence
    flags = await _item_flags(db, session_id, item_index)

    row = (
        await db.execute(_ENTER_ITEM_SQL, {"sid": session_id, "idx": item_index})
    ).first()
    if row is None:
        # The lattice rejected it: the item is already in_progress or terminal. The
        # session is deliberately NOT bumped — see ItemOutcome.applied.
        return (
            ItemOutcome(
                item_index=item_index,
                state=flags.state,
                applied=False,
                active_seconds=0,
                clamped=False,
            ),
            await load_session(db, user_id, session_id),
        )

    session = await _bump_session(db, session_id, item_index)
    return (
        ItemOutcome(
            item_index=item_index,
            state=row.state,
            applied=True,
            active_seconds=row.active_seconds,
            clamped=False,
        ),
        session,
    )


async def _terminal_item(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
    item_index: int,
    *,
    new_state: str,
    active_seconds: int,
    completed_reps: Optional[int],
    rating: Optional[str],
    advance_mode: Optional[str],
) -> tuple[ItemOutcome, SessionRow]:
    session_before = await load_session(db, user_id, session_id)
    flags = await _item_plan_locked(db, session_id, item_index)

    # FLE-21 §5.1. Checked against the SERVER's `rated` flag, never the client's idea
    # of it — FLE-10 R5 is that the client derives no flags, and this is the boundary
    # where that stops being a slogan.
    if rating is not None and not flags.rated:
        raise RatingNotPermitted(
            f"item {item_index} of session {session_id} is not a rated item"
        )

    # §5.2's fan-out fires on the FIRST rating this item ever carried, and only there.
    # `_TERMINAL_ITEM_SQL` COALESCEs the rating precisely so a retry cannot overwrite
    # one, so "the row had no rating a moment ago, under lock" is the same condition
    # as "this write is the one that set it" — and it holds for the out-of-order case
    # too, where the complete lands on a `not_reached` item.
    fans_out = rating is not None and flags.rating is None and new_state == "completed"

    row = (
        await db.execute(
            _TERMINAL_ITEM_SQL,
            {
                "sid": session_id,
                "idx": item_index,
                "new_state": new_state,
                "active_seconds": active_seconds,
                "clamp_factor": ACTIVE_SECONDS_CLAMP_FACTOR,
                "completed_reps": completed_reps,
                "rating": rating,
                "advance_mode": advance_mode,
            },
        )
    ).first()
    assert row is not None  # _item_flags already proved the row exists

    clamped = row.active_seconds < active_seconds
    if clamped:
        # §6 requires the clamp to be logged. The readout must EXCLUDE clamped items
        # from the duration comparison rather than average them in, and it identifies
        # them in SQL as `active_seconds = 4 * planned_seconds` — no flag column
        # exists, so this log line is the corroborating evidence that the rule fired.
        logger.warning(
            "active_seconds clamped: session=%s item=%s reported=%s stored=%s "
            "planned=%s (FLE-21 §6)",
            session_id,
            item_index,
            active_seconds,
            row.active_seconds,
            row.planned_seconds,
        )

    session = await _bump_session(db, session_id, item_index)
    fanout = (
        await _fan_out(db, session_before, flags, rating=rating, item_row=row)
        if fans_out
        else FanOut()
    )
    return (
        ItemOutcome(
            item_index=item_index,
            state=row.state,
            applied=True,
            active_seconds=row.active_seconds,
            clamped=clamped,
            fanout=fanout,
        ),
        session,
    )


# ---------------------------------------------------------------------------
# FLE-21 §5.2 — the fan-out (FLE-64)
# ---------------------------------------------------------------------------


async def _fan_out(
    db: AsyncSession,
    session: SessionRow,
    item: Any,
    *,
    rating: str,
    item_row: Any,
) -> FanOut:
    """Steps 2-4 of §5.2, in the caller's transaction.

    Which steps run is decided by what the ITEM is, never by what the client sent:

      * a `drill` item  → `drill_attempts` + the §7.2 ladder (steps 2 and 3).
      * a song item     → the `user_sessions` daily verdict (step 4).

    Those are the only two shapes a rated item can have. FLE-4 §5.3 makes exactly one
    repertoire item rated (`song_section`), §5.1/§5.4 make warm-up and consolidation
    unrated, and §6 drops the lowest technique slots past MAX_RATING_TAPS — so this
    branch is total over `rated = true`, and an unrated item never reaches here.
    """
    if item.drill_id is not None:
        record = await ladder.record_attempt(
            db,
            user_id=session.user_id,
            drill_id=item.drill_id,
            session_item_id=item.id,
            local_calendar_day=session.local_calendar_day,
            planned_bpm=item.planned_bpm,
            planned_reps=item.planned_reps,
            # From the row we just wrote, not from the request: `_TERMINAL_ITEM_SQL`
            # COALESCEs completed_reps, so a rating arriving in a second flush that
            # omits the reps still classifies against the reps the first one stored.
            completed_reps=item_row.completed_reps,
            rating=rating,
            re_entry=bool(item.re_entry),
            allow_push=bool(session.allow_push),
            duration_s=item_row.active_seconds,
            target_skill_node_id=item.target_skill_node_id,
        )
        return FanOut(
            attempt_id=record.attempt_id,
            outcome=record.outcome,
            rung_before=record.rung_before,
            rung_after=record.rung_after,
            progress_state=record.progress_state,
            pushed=record.pushed,
            dropped=record.dropped,
            push_withheld=record.push_withheld,
        )

    if item.song_id is not None:
        verdict = await ladder.record_daily_verdict(
            db,
            user_id=session.user_id,
            song_id=item.song_id,
            rating=rating,
            local_calendar_day=session.local_calendar_day,
            tz_offset_minutes=session.tz_offset_minutes,
        )
        return FanOut(
            daily_verdict_written=verdict.verdict_id is not None,
            daily_verdict_conflict=verdict.conflicted,
        )

    # A rated item with neither a drill nor a song. The generator cannot produce one
    # (every PlannedItem carries one or the other), so this is a corrupted row rather
    # than a case — and the rating is already safely on the item either way.
    logger.warning(
        "rated item %s of session %s has neither drill_id nor song_id — "
        "§5.2 fan-out skipped",
        item.id,
        session.id,
    )
    return FanOut()


async def complete_item(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
    item_index: int,
    *,
    active_seconds: int,
    rating: Optional[str] = None,
    completed_reps: Optional[int] = None,
    advance_mode: Optional[str] = None,
) -> tuple[ItemOutcome, SessionRow]:
    """Item → completed. `rating=None` is a first-class value (FLE-21 R1).

    Four item classes are unrated BY DESIGN — warm-up (FLE-4 §5.1), song_play (§5.3),
    consolidation (§5.4), and any technique slot dropped by MAX_RATING_TAPS (§6) — and
    in a 15-minute session that is most of them. Requiring a rating would force the
    player to fabricate data or strand the item `in_progress`.

    Auto-advance lands here too, not in skip: `advance_mode='auto'` with a null rating.
    The user did not choose to pass (so it is not `skipped`) and they were present (so
    it is not `not_reached`).
    """
    return await _terminal_item(
        db,
        user_id,
        session_id,
        item_index,
        new_state="completed",
        active_seconds=active_seconds,
        completed_reps=completed_reps,
        rating=rating,
        advance_mode=advance_mode,
    )


async def skip_item(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
    item_index: int,
    *,
    active_seconds: int = 0,
    completed_reps: Optional[int] = None,
) -> tuple[ItemOutcome, SessionRow]:
    """Item → skipped. Its own call, never inferred from navigation (FLE-21 §5.4).

    Advancing past an item is not a skip unless the user CHOSE to pass. That
    distinction is the entire content of FLE-13's "which block do they skip", and it
    is only representable because §2 writes every item row at generation time — so
    `skipped` means the user passed and `not_reached` means the session ended first.
    """
    return await _terminal_item(
        db,
        user_id,
        session_id,
        item_index,
        new_state="skipped",
        active_seconds=active_seconds,
        completed_reps=completed_reps,
        rating=None,
        advance_mode=None,
    )


# ---------------------------------------------------------------------------
# Session termination
# ---------------------------------------------------------------------------

# completion_ratio counts COMPLETED items only — skipped items are deliberately not
# credited, because a session the user skipped four items out of is not 100% complete
# in any sense the readout wants. The response also carries done/skipped/planned
# separately (FLE-10 R8) so the readout can compute any other variant without a
# migration, which is the point of keeping all three numbers.
_COMPLETION_RATIO_SQL = """
    (SELECT ROUND(
                COUNT(*) FILTER (WHERE i.state = 'completed')::numeric
                / GREATEST(practice_sessions.item_count, 1), 3)
       FROM practice_session_items i WHERE i.session_id = practice_sessions.id)
"""

_ELAPSED_SQL = """
    (SELECT COALESCE(SUM(i.active_seconds), 0)
       FROM practice_session_items i WHERE i.session_id = practice_sessions.id)
"""

# `state <> 'completed'` makes a repeat call idempotent rather than re-dating ended_at.
#
# Note what is NOT in the WHERE clause: `state <> 'abandoned'`. FLE-21 §3.1 — an
# explicit user completion OUTRANKS a verdict the clock reached. A user practising
# across midnight gets day-rolled underneath them by the sweep, and when they then tap
# "done" that is the truth and the sweep's guess is not. R7 deleted `superseded`, so
# the clock is the only thing that can abandon a session, so every abandonment is
# overridable here.
_COMPLETE_SESSION_SQL = text(
    f"""
    UPDATE practice_sessions
       SET state = 'completed'::session_state,
           terminal_reason = 'user_completed'::session_terminal_reason,
           ended_at = now(),
           started_at = COALESCE(started_at, now()),
           last_activity_at = now(),
           elapsed_active_seconds = {_ELAPSED_SQL},
           completion_ratio = {_COMPLETION_RATIO_SQL}
     WHERE id = :sid AND user_id = :uid AND state <> 'completed'
    RETURNING {_SESSION_COLUMNS}
    """
)

_ITEM_TALLY_SQL = text(
    """
    SELECT count(*) FILTER (WHERE state = 'completed') AS done_items,
           count(*) FILTER (WHERE state = 'skipped')   AS skipped_items,
           count(*)                                    AS planned_items
      FROM practice_session_items
     WHERE session_id = :sid
    """
)


@dataclass(frozen=True)
class SessionTally:
    done_items: int
    skipped_items: int
    planned_items: int


async def tally_items(db: AsyncSession, session_id: UUID) -> SessionTally:
    row = (await db.execute(_ITEM_TALLY_SQL, {"sid": session_id})).first()
    assert row is not None
    return SessionTally(
        done_items=row.done_items,
        skipped_items=row.skipped_items,
        planned_items=row.planned_items,
    )


async def complete_session(
    db: AsyncSession, user_id: UUID, session_id: UUID
) -> tuple[SessionRow, SessionTally]:
    """Session → completed. Idempotent, and outranks the clock's abandonment."""
    row = (
        await db.execute(_COMPLETE_SESSION_SQL, {"sid": session_id, "uid": user_id})
    ).first()
    if row is None:
        # Either it was already completed (idempotent no-op) or it does not exist /
        # is not owned. load_session tells the two apart and raises for the second.
        session = await load_session(db, user_id, session_id)
    else:
        session = _row_to_session(row)
    return session, await tally_items(db, session_id)


# ---------------------------------------------------------------------------
# The sweep (FLE-21 §3, backstop)
# ---------------------------------------------------------------------------

# Load-bearing, not hygiene. store.py closes stale sessions inline for a user who comes
# BACK — that is the common path and it is transactional. This is for the user who
# never comes back: without it their last session stays `in_progress` forever, counted
# as neither completed nor abandoned, and FLE-21 §4's completion rate — a ratio whose
# denominator is `state IN ('completed','abandoned')` — silently drifts UP as the pilot
# runs. A metric that improves because data is missing is the worst failure available
# here, because it looks like success.
#
# Each row is judged by its OWN tz_offset_minutes, because "the day rolled" is a
# statement about the user's local clock and the pilot roster is not in one timezone.
_DAY_ROLLED_PREDICATE = """
    ps.local_calendar_day
        < ((now() AT TIME ZONE 'UTC')
           + (ps.tz_offset_minutes * INTERVAL '1 minute'))::date
"""

_SWEEP_SQL = text(
    f"""
    UPDATE practice_sessions AS ps
       SET state = 'abandoned'::session_state,
           terminal_reason = CASE WHEN {_DAY_ROLLED_PREDICATE}
                                  THEN 'day_rolled'::session_terminal_reason
                                  ELSE 'timeout'::session_terminal_reason END,
           ended_at = now(),
           elapsed_active_seconds = (
               SELECT COALESCE(SUM(i.active_seconds), 0)
                 FROM practice_session_items i WHERE i.session_id = ps.id
           ),
           completion_ratio = (
               SELECT ROUND(
                          COUNT(*) FILTER (WHERE i.state = 'completed')::numeric
                          / GREATEST(ps.item_count, 1), 3)
                 FROM practice_session_items i WHERE i.session_id = ps.id
           )
     WHERE ps.state = ANY(:open_states)
       AND ( {_DAY_ROLLED_PREDICATE}
             OR now() - ps.last_activity_at > INTERVAL '{TIMEOUT_INTERVAL}' )
    RETURNING ps.id, ps.user_id, ps.terminal_reason::text AS terminal_reason,
              ps.started_at
    """
)


@dataclass(frozen=True)
class SweepResult:
    day_rolled: int
    timed_out: int

    @property
    def total(self) -> int:
        return self.day_rolled + self.timed_out


async def sweep_open_sessions(db: AsyncSession) -> SweepResult:
    """Close every open session whose local day has rolled. Returns what it closed.

    Never-started sessions are closed too, and that is correct rather than sloppy:
    FLE-21 §1 keeps `generated_at` and `started_at` separate precisely so an IGNORED
    PLAN stays distinguishable from a bail. The row goes `abandoned` with `started_at`
    still NULL, and §4's completion-rate query filters on `started_at IS NOT NULL`, so
    it lands in the ignored-plan bucket and not in the denominator.
    """
    rows = (await db.execute(_SWEEP_SQL, {"open_states": list(OPEN_STATES)})).all()
    day_rolled = sum(1 for r in rows if r.terminal_reason == "day_rolled")
    timed_out = len(rows) - day_rolled

    for r in rows:
        if r.terminal_reason == "timeout":
            # §3: this branch is protection against a corrupt tz_offset_minutes, not a
            # metric. If it fires during the pilot, file a bug — do not report it.
            logger.warning(
                "session %s (user %s) closed by the 36h timeout backstop, not the day "
                "roll — suspect a corrupt tz_offset_minutes (FLE-21 §3)",
                r.id,
                r.user_id,
            )
    if rows:
        logger.info(
            "session sweep closed %d session(s): %d day_rolled, %d timeout",
            len(rows),
            day_rolled,
            timed_out,
        )
    return SweepResult(day_rolled=day_rolled, timed_out=timed_out)
