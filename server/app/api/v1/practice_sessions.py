# server/app/api/v1/practice_sessions.py
# The practice-session lifecycle surface — FLE-21 §5's call list.
#
# This is the HTTP boundary for pilot telemetry. FLE-13 names day-7 return and
# session-completion rate as the numbers that count, and FLE-10 forbids an analytics
# SDK in a pilot build, so every one of those numbers is derived from a row one of
# these five handlers wrote. If a handler here silently drops a write, the readout
# degrades to self-report and nobody finds out until it is being written.
#
# NOT the Phase-3 ratings endpoint. `POST /api/v1/sessions` in sessions.py is the
# Today-card rating and is untouched by all of this (FLE-21 §5.2) — it keeps serving
# ratings taken OUTSIDE the player. Do not extend it and do not route to it from here.
#
# Shared file with FLE-63. That issue owns the two generate/read endpoints —
# `POST /practice-sessions/today` (a thin wrapper over store.resolve_or_generate) and
# `GET /practice-sessions/{id}` — and they belong in this router rather than a second
# one. ⚠️ When `GET /practice-sessions/{session_id}` is added it MUST be declared
# BELOW `GET /practice-sessions/current`: FastAPI matches in declaration order, and a
# `{session_id}` path registered first would swallow `/current` and fail it as an
# invalid UUID.
#
# Failure semantics are SPLIT, and this is the part that is easy to flatten by
# accident (FLE-21 §5.2):
#
#   /enter, /skip, /complete with rating=null → fire-and-forget. The player queues and
#       retries; a permanent failure is one lost telemetry row, which is acceptable.
#   /complete with a non-null rating          → a USER-VISIBLE write. The player
#       surfaces the outcome, and 409 keeps UI-SPEC §10's existing meaning: a
#       state-sync signal, not an error toast.
#
# Which is why every handler here is idempotent and order-tolerant rather than
# strict. See lifecycle.py's header for the lattice that makes that true.
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tz_offset_minutes, get_user_id
from app.db.session import get_db
from app.models.practice_session import (
    ItemCompleteRequest,
    ItemEventResponse,
    ItemSkipRequest,
    PracticeSessionItemResponse,
    PracticeSessionResponse,
    SessionCompleteResponse,
)
from app.sessions import lifecycle
from app.sessions.lifecycle import (
    ItemNotFound,
    RatingNotPermitted,
    SessionNotFound,
    SessionRow,
)
from app.sessions.snapshot import local_day

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/practice-sessions", tags=["practice-sessions"])


def _iso(value) -> str | None:
    return None if value is None else value.isoformat()


def _session_payload(session: SessionRow, items: list) -> PracticeSessionResponse:
    return PracticeSessionResponse(
        id=session.id,
        user_id=session.user_id,
        local_calendar_day=session.local_calendar_day.isoformat(),
        tz_offset_minutes=session.tz_offset_minutes,
        song_id=session.song_id,
        target_minutes=session.target_minutes,
        mode=session.mode,
        state=session.state,
        terminal_reason=session.terminal_reason,
        generated_at=_iso(session.generated_at),
        started_at=_iso(session.started_at),
        last_activity_at=_iso(session.last_activity_at),
        ended_at=_iso(session.ended_at),
        completion_ratio=(
            None if session.completion_ratio is None else float(session.completion_ratio)
        ),
        elapsed_active_seconds=session.elapsed_active_seconds,
        item_count=session.item_count,
        last_item_index_reached=session.last_item_index_reached,
        items=[
            PracticeSessionItemResponse(
                item_index=i.item_index,
                block=i.block,
                kind=i.kind,
                state=i.state,
                drill_id=i.drill_id,
                song_id=i.song_id,
                target_skill_node_id=i.target_skill_node_id,
                planned_seconds=i.planned_seconds,
                planned_bpm=i.planned_bpm,
                planned_reps=i.planned_reps,
                completed_reps=i.completed_reps,
                rated=i.rated,
                skippable=i.skippable,
                click_enabled=i.click_enabled,
                re_entry=i.re_entry,
                repeat_ok=i.repeat_ok,
                is_consolidation=i.is_consolidation,
                started_at=_iso(i.started_at),
                ended_at=_iso(i.ended_at),
                active_seconds=i.active_seconds,
                rating=i.rating,
                advance_mode=i.advance_mode,
            )
            for i in items
        ],
    )


async def _payload(db: AsyncSession, session: SessionRow) -> PracticeSessionResponse:
    return _session_payload(session, await lifecycle.load_items(db, session.id))


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("/current", response_model=PracticeSessionResponse)
async def get_current(
    user_id: UUID = Depends(get_user_id),
    tz_offset_minutes: int = Depends(get_tz_offset_minutes),
    db: AsyncSession = Depends(get_db),
) -> PracticeSessionResponse:
    """The caller's open session for their local day, or 404.

    Read-only by contract (FLE-21 §5) — it NEVER writes, not even to bump
    `last_activity_at`. A resume check that mutated would make merely opening the app
    indistinguishable from practising, and `last_activity_at` is what the §3 sweep
    reads to decide whether a session was abandoned.

    404 is the normal answer on a day the user has not generated a session yet; it is
    not an error condition for the player.
    """
    day = await local_day(db, tz_offset_minutes)
    session = await lifecycle.get_current_session(db, user_id, today=day)
    if session is None:
        raise HTTPException(
            status_code=404, detail="No open practice session for today."
        )
    return await _payload(db, session)


# ---------------------------------------------------------------------------
# Lifecycle writes
# ---------------------------------------------------------------------------


@router.post("/{session_id}/start", response_model=PracticeSessionResponse)
async def start(
    session_id: UUID,
    user_id: UUID = Depends(get_user_id),
    db: AsyncSession = Depends(get_db),
) -> PracticeSessionResponse:
    """Mark the session opened. Write-once, idempotent (FLE-21 R5).

    `started_at` is the day-7 return metric, so a second call — a retry, or a resume
    after the user put the phone down — must NOT move it. Answers 200 either way; a
    no-op is a success, not a conflict.
    """
    try:
        async with db.begin():
            session = await lifecycle.start_session(db, user_id, session_id)
    except SessionNotFound as exc:
        raise _not_found(exc) from exc
    return await _payload(db, session)


@router.post("/{session_id}/items/{item_index}/enter", response_model=ItemEventResponse)
async def enter_item(
    session_id: UUID,
    item_index: int,
    user_id: UUID = Depends(get_user_id),
    db: AsyncSession = Depends(get_db),
) -> ItemEventResponse:
    """Item → in_progress; bump the session's high-water mark.

    A late `enter` arriving behind its own `complete` (the outbox can flush out of
    order) is a no-op 200 with `applied=false` — it must not resurrect a finished item.
    """
    try:
        async with db.begin():
            outcome, session = await lifecycle.enter_item(
                db, user_id, session_id, item_index
            )
    except SessionNotFound as exc:
        raise _not_found(exc) from exc
    except ItemNotFound as exc:
        raise _not_found(exc) from exc
    return ItemEventResponse(
        item_index=outcome.item_index,
        state=outcome.state,
        applied=outcome.applied,
        active_seconds=outcome.active_seconds,
        clamped=outcome.clamped,
        session=await _payload(db, session),
    )


@router.post(
    "/{session_id}/items/{item_index}/complete", response_model=ItemEventResponse
)
async def complete_item(
    body: ItemCompleteRequest,
    session_id: UUID,
    item_index: int,
    user_id: UUID = Depends(get_user_id),
    db: AsyncSession = Depends(get_db),
) -> ItemEventResponse:
    """Item → completed, with the rating if there is one (FLE-21 §5.1).

    `rating: null` is valid and still lands `completed` (R1) — warm-up, `song_play`,
    consolidation and any slot dropped by MAX_RATING_TAPS are unrated BY DESIGN, and
    in a 15-minute session that is most of the items.

    422 `rating_not_permitted_for_item` when a rating arrives for an item whose
    server-side `rated` flag is false. That can only come from a client bug, and the
    player's outbox must treat it as terminal — drop it, do not retry.
    """
    try:
        async with db.begin():
            outcome, session = await lifecycle.complete_item(
                db,
                user_id,
                session_id,
                item_index,
                active_seconds=body.active_seconds,
                rating=body.rating,
                completed_reps=body.completed_reps,
                advance_mode=body.advance_mode,
            )
    except SessionNotFound as exc:
        raise _not_found(exc) from exc
    except ItemNotFound as exc:
        raise _not_found(exc) from exc
    except RatingNotPermitted as exc:
        raise HTTPException(
            status_code=422, detail=f"rating_not_permitted_for_item: {exc}"
        ) from exc
    return ItemEventResponse(
        item_index=outcome.item_index,
        state=outcome.state,
        applied=outcome.applied,
        active_seconds=outcome.active_seconds,
        clamped=outcome.clamped,
        session=await _payload(db, session),
    )


@router.post("/{session_id}/items/{item_index}/skip", response_model=ItemEventResponse)
async def skip_item(
    body: ItemSkipRequest,
    session_id: UUID,
    item_index: int,
    user_id: UUID = Depends(get_user_id),
    db: AsyncSession = Depends(get_db),
) -> ItemEventResponse:
    """Item → skipped. Its own call, never inferred from navigation (FLE-21 §5.4).

    This is the endpoint FLE-13's "which block do they skip" is made of. Advancing
    past an item is not a skip unless the user chose to pass — an auto-advance goes to
    `/complete` with `advance_mode='auto'` instead.
    """
    try:
        async with db.begin():
            outcome, session = await lifecycle.skip_item(
                db,
                user_id,
                session_id,
                item_index,
                active_seconds=body.active_seconds,
                completed_reps=body.completed_reps,
            )
    except SessionNotFound as exc:
        raise _not_found(exc) from exc
    except ItemNotFound as exc:
        raise _not_found(exc) from exc
    return ItemEventResponse(
        item_index=outcome.item_index,
        state=outcome.state,
        applied=outcome.applied,
        active_seconds=outcome.active_seconds,
        clamped=outcome.clamped,
        session=await _payload(db, session),
    )


@router.post("/{session_id}/complete", response_model=SessionCompleteResponse)
async def complete_session(
    session_id: UUID,
    user_id: UUID = Depends(get_user_id),
    db: AsyncSession = Depends(get_db),
) -> SessionCompleteResponse:
    """Session → completed, with the summary numbers (FLE-10 R8).

    Outranks the clock (FLE-21 §3.1): a user who practised across midnight gets
    day-rolled underneath them by the sweep, and when they then tap "done" that is the
    truth. Since R7 deleted `superseded`, the clock is the only thing that can abandon
    a session — so every abandonment is overridable here, and only here.
    """
    try:
        async with db.begin():
            session, tally = await lifecycle.complete_session(db, user_id, session_id)
    except SessionNotFound as exc:
        raise _not_found(exc) from exc
    return SessionCompleteResponse(
        session=await _payload(db, session),
        completion_ratio=(
            None if session.completion_ratio is None else float(session.completion_ratio)
        ),
        done_items=tally.done_items,
        skipped_items=tally.skipped_items,
        planned_items=tally.planned_items,
    )
