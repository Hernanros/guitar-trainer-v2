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
# Shared file with FLE-63, now landed here too: `POST /practice-sessions/today` (a
# thin wrapper over store.resolve_or_generate) and `GET /practice-sessions/{id}`.
# ⚠️ `GET /{session_id}` is declared BELOW `GET /current` and must stay there —
# FastAPI matches in declaration order, and a `{session_id}` path registered first
# would swallow `/current` and fail it as an invalid UUID.
#
# ITEM CONTENT IS EMBEDDED, not referenced (FLE-63 decision 1, ruled 2026-09-24).
# Every item carries its drill's prose and tab, or its song's breakdown, inline —
# see app/sessions/content.py for why the wire shape diverges from the table shape.
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
from typing import Mapping, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tz_offset_minutes, get_user_id
from app.db.session import get_db
from app.models.practice_session import (
    EmbeddedDrill,
    EmbeddedSong,
    ItemCompleteRequest,
    ItemEventResponse,
    ItemSkipRequest,
    LadderMoveResponse,
    PracticeSessionItemResponse,
    PracticeSessionResponse,
    SessionCompleteResponse,
)
from app.sessions import lifecycle
from app.sessions.assemble import NoMaterialError
from app.sessions.content import load_item_content
from app.sessions.ladder import DrillNotFound
from app.sessions.lifecycle import (
    ItemNotFound,
    RatingNotPermitted,
    SessionNotFound,
    SessionRow,
)
from app.sessions.snapshot import SnapshotError, local_day
from app.sessions.store import resolve_or_generate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/practice-sessions", tags=["practice-sessions"])


def _iso(value) -> str | None:
    return None if value is None else value.isoformat()


def _embedded_drill(row) -> EmbeddedDrill | None:
    if row is None:
        return None
    return EmbeddedDrill(
        drill_id=row.id,
        name=row.name,
        what=row.what,
        tab_snippet=row.tab_snippet,
        start_bpm=row.start_bpm,
        target_bpm=row.target_bpm,
        repetitions=row.repetitions,
        success_criterion=row.success_criterion,
        common_trap=row.common_trap,
        song_specific=row.song_specific,
    )


def _embedded_song(row) -> EmbeddedSong | None:
    if row is None:
        return None
    return EmbeddedSong(
        song_id=row.id,
        title=row.title,
        artist=row.artist,
        genre=row.genre,
        difficulty=row.difficulty,
        bpm=row.bpm,
        key=row.key,
        breakdown=row.breakdown,
    )


def _session_payload(
    session: SessionRow,
    items: list,
    drills: Mapping | None = None,
    songs: Mapping | None = None,
) -> PracticeSessionResponse:
    drills = drills or {}
    songs = songs or {}
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
                drill=_embedded_drill(drills.get(i.drill_id)),
                song=_embedded_song(songs.get(i.song_id)),
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
    """The full session, items embedded.

    Every response on this router goes through here, INCLUDING the fire-and-forget
    lifecycle writes. That is deliberate rather than wasteful: the player's outbox
    replays a queued `/enter` on reconnect, and the response is its chance to
    re-hydrate a session whose content it may have lost to an app restart. Two extra
    batch reads on a write path that runs a handful of times per session is a price
    worth paying to make every response self-sufficient.
    """
    items = await lifecycle.load_items(db, session.id)
    drills, songs = await load_item_content(db, items)
    return _session_payload(session, items, drills, songs)


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------


class TodayRequest(BaseModel):
    """Optional overrides for today's generation. An empty body is the normal call.

    Both fields are IGNORED when an open session is resolved rather than generated
    (store.resolve_or_generate). FLE-4 §5.3 says a session never re-rolls its song,
    and honouring a new `song_id` here would let a mid-day re-pick rewrite a plan the
    user is halfway through.
    """

    song_id: Optional[int] = None
    target_minutes: Optional[int] = Field(default=None, ge=5, le=180)


# The `responses=` block is not documentation. FLE-10 generates `schema.d.ts` from
# this spec, and a 201 that only exists at runtime is a status the generated client
# does not know about — so the branch that tells "generated" from "resolved" would be
# unwritable on the client that needs it most.
@router.post(
    "/today",
    response_model=PracticeSessionResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Resolved — an open session already existed for today."},
        201: {
            "description": "Generated — a new plan was written.",
            "model": PracticeSessionResponse,
        },
        404: {"description": "No such user."},
        409: {"description": "The user exists but has nothing to practise."},
    },
)
async def generate_today(
    response: Response,
    body: Optional[TodayRequest] = None,
    user_id: UUID = Depends(get_user_id),
    tz_offset_minutes: int = Depends(get_tz_offset_minutes),
    db: AsyncSession = Depends(get_db),
) -> PracticeSessionResponse:
    """Today's session: the existing open one, or a new one written now.

    **201 means generated, 200 means resolved.** That distinction is the entire reason
    this is a POST and not a GET, and the player must not flatten it: a resolved
    session is one the user may already be halfway through, so re-animating a progress
    bar or emitting a `session_generated` event on a 200 would both be wrong.

    NO DEBOUNCE, NO LOCK, deliberately (FLE-63 decision 2). The double-tap is already
    handled one layer down by migration 0009's partial unique
    `uq_practice_sessions_open_day`; a guard here would mask the index that is doing
    the real work and, being per-process, would not survive two Railway replicas.

    Status codes are the player's outbox control flow (FLE-21 §5.2):
        201 — generated. 200 — resolved, already existed.
        404 — no such user (SnapshotError).
        409 — the user exists and has nothing to practise (NoMaterialError). A real
              product state with a screen behind it, NOT an error to retry.
    """
    body = body or TodayRequest()
    try:
        async with db.begin():
            stored = await resolve_or_generate(
                db,
                user_id,
                tz_offset_minutes=tz_offset_minutes,
                song_id=body.song_id,
                target_minutes=body.target_minutes,
            )
    except SnapshotError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoMaterialError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    response.status_code = (
        status.HTTP_201_CREATED if stored.created else status.HTTP_200_OK
    )
    session = await lifecycle.load_session(db, user_id, stored.session_id)
    return await _payload(db, session)


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


# ⚠️ DECLARATION ORDER IS LOAD-BEARING. FastAPI matches in declaration order, so this
# MUST stay below `/current` — registered first, `{session_id}` would swallow the
# literal and fail it as an invalid UUID. test_practice_sessions_api.py pins it.
@router.get("/{session_id}", response_model=PracticeSessionResponse)
async def get_session(
    session_id: UUID,
    user_id: UUID = Depends(get_user_id),
    db: AsyncSession = Depends(get_db),
) -> PracticeSessionResponse:
    """Read back one session with its ordered items, terminal or not.

    Unlike `/current` this does not care about the local day: the player uses it to
    re-read a session it already holds an id for, which includes a completed one it is
    showing a summary of.

    404 covers "no such session" AND "not yours" with one message, which is
    deliberate — `lifecycle.load_session` filters by user_id as access control, and
    distinguishing the two here would confirm the existence of another participant's
    session id.
    """
    try:
        session = await lifecycle.load_session(db, user_id, session_id)
    except SessionNotFound as exc:
        raise _not_found(exc) from exc
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
    "/{session_id}/items/{item_index}/complete",
    response_model=ItemEventResponse,
    responses={
        200: {"description": "Recorded. `ladder` is set on a drill item's first rating."},
        404: {"description": "No such session, item, or drill."},
        409: {
            "description": (
                "The rated repertoire item's daily verdict already existed. A "
                "state-sync signal, NOT a failure — every other write committed."
            ),
            "model": ItemEventResponse,
        },
        422: {"description": "rating_not_permitted_for_item — the item is unrated."},
    },
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

    **A non-null rating fans out (§5.2, FLE-64).** One transaction writes the item
    row, then — for a drill item — a `drill_attempts` row with a server-classified
    §7.1 outcome and the §7.2 ladder transition, or — for the rated repertoire item —
    the `user_sessions` daily verdict. This is the player's ONLY rating call; it does
    not also POST /api/v1/sessions. The fan-out is once per attempt: a duplicate
    flush answers 200 with `ladder: null` rather than moving the rung twice.

    422 `rating_not_permitted_for_item` when a rating arrives for an item whose
    server-side `rated` flag is false. That can only come from a client bug, and the
    player's outbox must treat it as terminal — drop it, do not retry.

    409 when the repertoire item's daily verdict was already recorded — e.g. the user
    also rated the song on the Today card. Per FLE-21 §5.2 that keeps UI-SPEC §10's
    meaning: invalidate and let the rated state repopulate, no error toast. Note what
    it does NOT mean here — the item's rating is COMMITTED before the status is
    chosen. Rolling the write back to report a row that was already there would throw
    away telemetry the player will never send again.
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
    except DrillNotFound as exc:
        raise _not_found(exc) from exc
    except RatingNotPermitted as exc:
        raise HTTPException(
            status_code=422, detail=f"rating_not_permitted_for_item: {exc}"
        ) from exc

    fan = outcome.fanout
    payload = ItemEventResponse(
        item_index=outcome.item_index,
        state=outcome.state,
        applied=outcome.applied,
        active_seconds=outcome.active_seconds,
        clamped=outcome.clamped,
        ladder=(
            None
            if fan.outcome is None
            else LadderMoveResponse(
                outcome=fan.outcome,
                rung_before=fan.rung_before,
                rung_after=fan.rung_after,
                progress_state=fan.progress_state,
                pushed=fan.pushed,
                dropped=fan.dropped,
                push_withheld=fan.push_withheld,
            )
        ),
        daily_verdict_recorded=fan.daily_verdict_written,
        session=await _payload(db, session),
    )
    if fan.daily_verdict_conflict:
        # Same body, different status. Everything above is already committed.
        return JSONResponse(status_code=409, content=jsonable_encoder(payload))
    return payload


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
