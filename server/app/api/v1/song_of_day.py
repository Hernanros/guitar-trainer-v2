# server/app/api/v1/song_of_day.py
# Phase 3 (03-01): Per-user deterministic Song of the Day selector.
#
# Replaces Phase 1's trivial SELECT * FROM songs LIMIT 1 with the 75/25 CTE
# selector (RESEARCH §4), reroll endpoint (D-05), and timezone support (D-10).
#
# Endpoints on this router (co-located per RESEARCH §Recommended Project Structure):
#   GET  /api/v1/song-of-day        → TodaySongResponse (per-user deterministic)
#   POST /api/v1/today-song/reroll  → TodaySongResponse (one per day, 409 on repeat)
#
# Security notes (03-01 threat model):
#   T-03-01-04: Song load after selector filters Song.user_id == user_id (no cross-user reads).
#   T-03-01-01: tz_offset_minutes validated by get_tz_offset_minutes dep (range [-840, +840]).
#   T-03-01-02: Reroll insert filtered by user_id; DB partial-unique enforces one-per-day.
#   T-03-01-03: Race-condition reroll → IntegrityError → 409 at DB level.
import logging
import uuid as uuid_module

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api.deps import get_tz_offset_minutes, get_user_id
from app.db.session import get_db
from app.models.db import Song, UserSession
from app.models.song import SongResponse, TodaySongResponse
from app.selectors.today_song import select_today_song

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/v1/song-of-day
# ---------------------------------------------------------------------------

@router.get("/song-of-day", response_model=TodaySongResponse)
async def get_song_of_day(
    user_id: UUID = Depends(get_user_id),
    tz_offset_minutes: int = Depends(get_tz_offset_minutes),
    db: AsyncSession = Depends(get_db),
) -> TodaySongResponse:
    """Return today's song of the day for the requesting user.

    Per-user deterministic selector (75/25 CTE with setseed per RESEARCH §4).
    If the user already re-rolled today, serves the persisted reroll pick (Revision B).

    Response shape: TodaySongResponse (song + selector metadata for mobile UI).
    """
    # Compute today's local calendar day (server-side; never trust client clock).
    today = await db.scalar(
        text(
            "SELECT DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute'))"
        ),
        {"tz": tz_offset_minutes},
    )

    # Check if the user already re-rolled today — if yes, serve the reroll marker's song_id.
    # Revision B: read bank_source from the persisted reroll marker (do NOT hardcode).
    reroll_row = (
        await db.execute(
            select(UserSession).where(
                UserSession.user_id == user_id,
                UserSession.local_calendar_day == today,
                UserSession.is_reroll_marker.is_(True),
            )
        )
    ).scalar_one_or_none()

    if reroll_row is not None:
        song_id = reroll_row.song_id
        from_bank = True
        # Revision B: read back the bank_source stored on INSERT (not hardcoded).
        bank_source = reroll_row.bank_source  # 'user_bench' | 'seed_catalog' | None
        rerolled = True
    else:
        song_id, from_bank, bank_source = await select_today_song(
            db, user_id, tz_offset_minutes, force_reroll=False
        )
        rerolled = False

    if song_id is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Nothing to hand you today. "
                "Add some songs from Settings and Fletcher will pick up tomorrow."
            ),
        )

    # T-03-01-04: access-control filter — only return songs owned by this user.
    song_row = (
        await db.execute(
            select(Song).where(Song.id == song_id, Song.user_id == user_id)
        )
    ).scalar_one_or_none()

    if song_row is None:
        raise HTTPException(
            status_code=404,
            detail="Song not found or not owned by user.",
        )

    return TodaySongResponse(
        song=SongResponse.model_validate(song_row),
        breakdown_available=(song_row.breakdown_generated_at is not None),
        from_bank=from_bank,
        bank_source=bank_source,
        rerolled=rerolled,
        rerolls_left=0 if rerolled else 1,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/today-song/reroll
# ---------------------------------------------------------------------------

@router.post("/today-song/reroll", response_model=TodaySongResponse)
async def reroll_today_song(
    user_id: UUID = Depends(get_user_id),
    tz_offset_minutes: int = Depends(get_tz_offset_minutes),
    db: AsyncSession = Depends(get_db),
) -> TodaySongResponse:
    """Re-roll today's song (one per day limit, D-05).

    Uses force_reroll=True so the CTE seed includes a suffix that produces a
    different random() sequence than the initial pick.

    409 if already re-rolled today — enforced at both application level (COUNT) and
    DB level (partial-unique index uq_user_sessions_daily_reroll WHERE is_reroll_marker=true).

    Revision B: persists bank_source on the reroll marker INSERT so GET /song-of-day
    can read it back without hardcoding a value.

    Revision C note: reroll marker and a subsequent rating row CAN coexist for the same
    (user, song, day) because partial-unique index only covers is_reroll_marker=true for
    rerolls and is_reroll_marker=false for ratings separately.
    """
    today = await db.scalar(
        text(
            "SELECT DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute'))"
        ),
        {"tz": tz_offset_minutes},
    )

    # Application-level idempotency guard (legibility layer before DB enforcement).
    already = await db.scalar(
        select(func.count(UserSession.id)).where(
            UserSession.user_id == user_id,
            UserSession.local_calendar_day == today,
            UserSession.is_reroll_marker.is_(True),
        )
    )
    if already:
        raise HTTPException(status_code=409, detail="Already used your reroll today.")

    # Run selector with reroll suffix so the new pick differs from the initial pick.
    new_song_id, from_bank, bank_source = await select_today_song(
        db, user_id, tz_offset_minutes, force_reroll=True
    )

    if new_song_id is None:
        raise HTTPException(
            status_code=404,
            detail="Nothing else to hand you today.",
        )

    # Persist the reroll marker (Revision B: bank_source stored here).
    try:
        async with db.begin():
            db.add(
                UserSession(
                    id=uuid_module.uuid4(),
                    user_id=user_id,
                    song_id=new_song_id,
                    rating=None,
                    local_calendar_day=today,
                    tz_offset_minutes=tz_offset_minutes,
                    is_reroll_marker=True,
                    bank_source=bank_source,  # Revision B: persists which branch fired
                )
            )
    except IntegrityError:
        # Race condition: partial-unique index caught a concurrent reroll INSERT.
        raise HTTPException(status_code=409, detail="Already used your reroll today.")

    # T-03-01-04: access-control filter on song load.
    song_row = (
        await db.execute(
            select(Song).where(Song.id == new_song_id, Song.user_id == user_id)
        )
    ).scalar_one()

    return TodaySongResponse(
        song=SongResponse.model_validate(song_row),
        breakdown_available=(song_row.breakdown_generated_at is not None),
        from_bank=True,
        bank_source=bank_source,
        rerolled=True,
        rerolls_left=0,
    )
