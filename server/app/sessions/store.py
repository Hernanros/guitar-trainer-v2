# server/app/sessions/store.py — the write half of the session engine (FLE-9).
#
# snapshot.py reads, build_plan decides, this module persists. The split is not
# ceremony: it is what lets a session be PREVIEWED without existing. Everything
# upstream of here is free of side effects, so a generated plan can be inspected,
# diffed against yesterday's, or asserted in a test without a row appearing.
#
# The one operation the app actually needs is `resolve_or_generate`, and its whole
# design is a response to a single fact: the user will double-tap. Two requests land
# together on a cold day and the naive shape — SELECT, see nothing, INSERT — gives
# one user two plans for one day, which is not a cosmetic bug. Whichever plan the
# device keeps, the other is an orphaned row that FLE-21's telemetry counts as an
# abandoned session and the pilot reads as "the user quit twice".
#
# The fix is not a lock or a mutex. Migration 0009 ships a PARTIAL UNIQUE index,
# `uq_practice_sessions_open_day`, over (user_id, local_calendar_day) WHERE state IN
# ('planned','in_progress'). The database refuses the second open session. This module
# therefore writes optimistically inside a SAVEPOINT and treats the unique violation
# as the EXPECTED outcome of a race rather than an error: the loser rolls back to the
# savepoint and re-reads the winner's session. Both callers get the same plan, which
# is the correct answer to "generate today's session" asked twice.
#
# Why the index and not SELECT ... FOR UPDATE: there is no row to lock yet. The whole
# race is about the absence of a row, and only a unique index can arbitrate that.
#
# Day-rolling. An open session from a previous local day is not resumable — its plan
# was built from a snapshot that is now stale, and §5.2's recency window has moved.
# It is closed as 'abandoned' with terminal_reason 'day_rolled', which is the split
# FLE-21 R4 asked for: distinguishing a user who walked away from a day that simply
# ended. This is the only state this module changes on a session it did not create.
#
# ONE SESSION PER LOCAL DAY, IN ANY STATE (FLE-76). The partial unique only arbitrates
# OPEN sessions, and for a while this module read no further: a completed day matched
# nothing, fell through to build_plan, and minted a second session. On device that read
# as "finishing today's session makes it unreachable" — the Today card handed back the
# walker at item 0 instead of the summary the user had just earned. Worse, it was an
# unlimited supply: every re-tap after a completion bought another 45-minute plan, and
# every one of them landed in FLE-21's completion-rate denominator.
#
# So resolution is deliberately WIDER than the index. `_find_today` matches the day's
# session whatever state it is in, and only a day with NO session at all reaches
# build_plan. The client decides what a terminal session means — the player routes it
# to the summary — because that is a presentation question and this module has no
# business answering it. The index still does its job underneath: it stops two OPEN
# plans racing into existence. This rule stops a finished day being re-planned. They
# are different failures and both need their own guard.
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.sessions.assemble import SessionPlan, build_plan, plan_as_rows
from app.sessions.snapshot import load_snapshot, local_day

# The index that arbitrates the race. Named here so the except-branch can prove it is
# catching the conflict it expects rather than swallowing any IntegrityError — a
# NOT NULL violation in the item rows must still surface as the bug it is.
OPEN_SESSION_INDEX = "uq_practice_sessions_open_day"

OPEN_STATES = ("planned", "in_progress")


@dataclass(frozen=True)
class StoredSession:
    """A persisted session and its items, plus how it came to be.

    `created` is the half callers keep getting wrong when it is left implicit: a
    resolved session and a freshly generated one are both "today's session", but only
    one of them should emit a `session_generated` telemetry event or re-animate a
    progress bar the user already moved.
    """

    session_id: UUID
    local_calendar_day: date
    created: bool
    state: str
    plan: Optional[SessionPlan] = None

    @property
    def resolved(self) -> bool:
        return not self.created


class SessionExists(Exception):
    """An open session already exists for this user and day. Internal to the retry."""


# Primary keys are generated HERE, not by Postgres. `practice_sessions.id` and
# `practice_session_items.id` carry a Python-side default on the ORM model and no
# server default in 0009, so a raw INSERT that omits them hits a NOT NULL violation.
# Supplying them explicitly also means the item rows can be built with their session
# id in hand, in one executemany, rather than a RETURNING round trip per row.
_INSERT_SESSION_SQL = text(
    """
    INSERT INTO practice_sessions
        (id, user_id, local_calendar_day, tz_offset_minutes, target_minutes, mode,
         allow_push, generator_version, seed, mode_rule, state, item_count, song_id)
    VALUES
        (:id, :user_id, :day, :tz, :target_minutes, CAST(:mode AS session_mode),
         :allow_push, :generator_version, :seed, :mode_rule,
         'planned'::session_state, :item_count, :song_id)
    """
)

# Items are written in one executemany rather than a loop of round trips. The plan is
# already ordered and item_index is carried explicitly, so nothing here depends on
# insertion order surviving the driver.
_INSERT_ITEM_SQL = text(
    """
    INSERT INTO practice_session_items
        (id, session_id, item_index, block, kind, drill_id, song_id,
         target_skill_node_id, planned_seconds, planned_bpm, planned_reps,
         rated, skippable, click_enabled, re_entry, repeat_ok, is_consolidation,
         state)
    VALUES
        (:id, :session_id, :item_index, CAST(:block AS session_item_block),
         CAST(:kind AS session_item_kind), CAST(:drill_id AS uuid), :song_id,
         CAST(:target_skill_node_id AS uuid), :planned_seconds, :planned_bpm,
         :planned_reps, :rated, :skippable, :click_enabled, :re_entry, :repeat_ok,
         :is_consolidation, 'not_reached'::session_item_state)
    """
)

# Today's session in ANY state — see the FLE-76 note in the header for why this is
# wider than the index it backs up.
#
# The ORDER BY is not decoration. A day can hold more than one row: historically from
# the FLE-76 bug, and legitimately from the partial unique, which permits an open
# session alongside a terminal one. Open wins because a session the user is halfway
# through is the one they mean; `generated_at DESC` breaks the remaining tie toward the
# most recent, and `id` makes the answer stable rather than merely usually-stable, so
# two requests a millisecond apart cannot disagree about which session today is.
_FIND_TODAY_SQL = text(
    """
    SELECT id, state::text AS state
      FROM practice_sessions
     WHERE user_id = :user_id
       AND local_calendar_day = :day
     ORDER BY (state = ANY(:open_states)) DESC, generated_at DESC, id DESC
     LIMIT 1
    """
)

# Close every open session from a PREVIOUS day. `ended_at` is set because 0009's
# ck_practice_sessions_ended_at_pairs_state makes it mandatory for a terminal state,
# and started_at is left alone: a session that was never started stays never-started,
# and ck_practice_sessions_started_at_when_active permits that for 'abandoned'.
_ROLL_DAY_SQL = text(
    """
    UPDATE practice_sessions
       SET state = 'abandoned'::session_state,
           terminal_reason = 'day_rolled'::session_terminal_reason,
           ended_at = now()
     WHERE user_id = :user_id
       AND local_calendar_day < :day
       AND state = ANY(:open_states)
    RETURNING id
    """
)


def _is_open_session_conflict(exc: IntegrityError) -> bool:
    """True only for the partial unique this module races against.

    Matching on the constraint NAME rather than the exception type is the difference
    between 'another request won' and 'the item rows are malformed'. The second must
    not be retried into silence.
    """
    return OPEN_SESSION_INDEX in str(getattr(exc, "orig", exc))


async def _find_today(db: AsyncSession, user_id: UUID, day: date):
    """This user's session for this day, in any state, or None.

    A named function rather than two inline copies of the same SELECT because the
    two callers mean different things — one is the pre-check, the other is the
    post-conflict re-read — and because it is the only seam at which a test can
    simulate the race: returning None from the pre-check while the row really
    exists is exactly the state the losing writer finds.
    """
    return (
        await db.execute(
            _FIND_TODAY_SQL,
            {"user_id": user_id, "day": day, "open_states": list(OPEN_STATES)},
        )
    ).first()


async def close_rolled_over_sessions(
    db: AsyncSession, user_id: UUID, *, today: date
) -> list[UUID]:
    """Abandon open sessions from earlier local days. Returns what was closed.

    Deliberately separate from resolve_or_generate so a nightly job can call it for a
    user who never opens the app again — otherwise their last session stays 'planned'
    forever and every completion-rate denominator in FLE-21 is quietly wrong.
    """
    rows = (
        await db.execute(
            _ROLL_DAY_SQL,
            {"user_id": user_id, "day": today, "open_states": list(OPEN_STATES)},
        )
    ).all()
    return [r.id for r in rows]


async def _write_plan(db: AsyncSession, plan: SessionPlan) -> UUID:
    """INSERT the header and its items. Raises IntegrityError on the open-day race."""
    session_id = uuid4()
    await db.execute(
        _INSERT_SESSION_SQL,
        {
            "id": session_id,
            "user_id": UUID(plan.user_id),
            "day": plan.local_calendar_day,
            "tz": plan.tz_offset_minutes,
            "target_minutes": plan.target_minutes,
            "mode": plan.mode.value,
            "allow_push": plan.allow_push,
            "generator_version": plan.generator_version,
            "seed": plan.seed,
            "mode_rule": plan.mode_rule,
            "item_count": plan.item_count,
            "song_id": plan.song_id,
        },
    )

    rows: list[dict[str, Any]] = []
    for row in plan_as_rows(plan):
        row["id"] = uuid4()
        row["session_id"] = session_id
        rows.append(row)
    if rows:
        await db.execute(_INSERT_ITEM_SQL, rows)
    return session_id


async def resolve_or_generate(
    db: AsyncSession,
    user_id: UUID,
    *,
    tz_offset_minutes: int,
    song_id: Optional[int] = None,
    target_minutes: Optional[int] = None,
    local_calendar_day: Optional[date] = None,
) -> StoredSession:
    """Today's session: the existing one in any state, or a new one written now.

    Idempotent by construction, and idempotent ACROSS COMPLETION (FLE-76). Calling
    this twice for the same user and day returns the same session both times — the
    second call either finds the first's row or loses the unique and then finds it —
    and finishing the session in between changes nothing about that. A day gets one
    plan; once it exists, this only ever hands it back.

    The caller decides what a terminal session means. The player routes one to the
    summary rather than the walker; nothing here needs to know that.

    Args:
        song_id: today's song, ALREADY CHOSEN upstream (snapshot.py rule 2). Ignored
            when an existing session is resolved: §5.3 says a session never re-rolls
            its song, and honouring a new one here would let a mid-day re-pick rewrite
            a plan the user is halfway through.

    Raises:
        SnapshotError: no such user.
        NoMaterialError: the user exists and has nothing to practise.
    """
    day = local_calendar_day or await local_day(db, tz_offset_minutes)

    await close_rolled_over_sessions(db, user_id, today=day)

    # The generate gate. Everything below it — the snapshot read, build_plan, the
    # INSERT — is reachable only on a day with no session at all. A completed day
    # stops here.
    existing = await _find_today(db, user_id, day)
    if existing is not None:
        return StoredSession(
            session_id=existing.id,
            local_calendar_day=day,
            created=False,
            state=existing.state,
        )

    inputs = await load_snapshot(
        db,
        user_id,
        tz_offset_minutes=tz_offset_minutes,
        song_id=song_id,
        local_calendar_day=day,
        target_minutes=target_minutes,
    )
    plan = build_plan(inputs)

    # The optimistic write. SAVEPOINT rather than a bare try/except because a unique
    # violation aborts the transaction in Postgres: without a nested block to roll
    # back to, the re-read below would fail with InFailedSQLTransaction and the race
    # would surface as a 500 instead of the resolved session it actually is.
    try:
        async with db.begin_nested():
            session_id = await _write_plan(db, plan)
    except IntegrityError as exc:
        if not _is_open_session_conflict(exc):
            raise
        winner = await _find_today(db, user_id, day)
        if winner is None:
            # The winner was DELETED between the INSERT and this read — it cannot
            # merely have gone terminal, because `_find_today` resolves terminal rows
            # too (FLE-76). Nothing sane produces that state, so the honest response is
            # the conflict itself rather than a retry loop or a third plan for one day.
            raise SessionExists(f"session for {user_id} on {day} vanished") from exc
        return StoredSession(
            session_id=winner.id,
            local_calendar_day=day,
            created=False,
            state=winner.state,
        )

    return StoredSession(
        session_id=session_id,
        local_calendar_day=day,
        created=True,
        state="planned",
        plan=plan,
    )
