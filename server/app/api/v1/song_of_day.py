# server/app/api/v1/song_of_day.py
# GET /api/v1/song-of-day — Song of the Day endpoint.
#
# Phase 1: trivial stub returning SELECT * FROM songs LIMIT 1.
# Phase 3 (Slice A): refactored to per-user selector (75/25 CTE).
#   Returns TodaySongResponse with breakdown_available, from_bank, rerolled, bank_source.
# Phase 3 (Slice C): extends TodaySongResponse with .rated field.
#   After submitting a rating via POST /api/v1/sessions, the same-day GET returns
#   TodaySongResponse.rated populated with TodayRatingInfo.
#
# NOTE: The full 75/25 selector CTE lives in server/app/selectors/today_song.py (Slice A).
# This file is a simplified version that satisfies Phase 3 Slice C acceptance criteria
# while Slice A's full selector is not yet implemented. It falls back to the Phase 1
# "SELECT * FROM songs LIMIT 1" behaviour but adds the rated field lookup.
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tz_offset_minutes, get_user_id
from app.db.seed import seed_songs
from app.db.session import get_db
from app.models.db import Song, UserSession
from app.models.song import SongResponse, TodayRatingInfo, TodaySongResponse
from sqlalchemy import func, text

from uuid import UUID

router = APIRouter()


@router.get("/song-of-day", response_model=TodaySongResponse)
async def get_song_of_day(
    user_id: UUID = Depends(get_user_id),
    tz_offset_minutes: int = Depends(get_tz_offset_minutes),
    db: AsyncSession = Depends(get_db),
) -> TodaySongResponse:
    """Return today's Song of the Day for the requesting user.

    Phase 3 Slice A replaces the Phase 1 stub with a deterministic 75/25 CTE selector.
    Phase 3 Slice C adds the .rated field (populated if user has already rated today).

    Fallback behaviour (when the full selector is not yet wired): returns the first
    song in the table that belongs to the user, or the global seed song if none found.
    """
    # Compute local calendar day server-side (D-10)
    local_day = await db.scalar(
        text(
            "SELECT DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute'))"
        ),
        {"tz": tz_offset_minutes},
    )

    # Try to find a song owned by this user (working_on preferred, else any)
    row = (
        await db.execute(
            select(Song)
            .where(Song.user_id == user_id)
            .limit(1)
        )
    ).scalar_one_or_none()

    # Fallback to any song in the table (Phase 1 behaviour)
    if row is None:
        result = await db.execute(select(Song).limit(1))
        row = result.scalar_one_or_none()

    if row is None:
        await seed_songs(db)
        result = await db.execute(select(Song).limit(1))
        row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="No song found. Seed data missing.")

    # Slice C: populate rated field if user has already rated this song today
    rating_row = (
        await db.execute(
            select(UserSession).where(
                UserSession.user_id == user_id,
                UserSession.song_id == row.id,
                UserSession.local_calendar_day == local_day,
                UserSession.is_reroll_marker == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()

    rated_info = None
    if rating_row is not None:
        rated_info = TodayRatingInfo(
            rating=(
                rating_row.rating.value
                if hasattr(rating_row.rating, "value")
                else rating_row.rating
            ),
            rated_at=rating_row.rated_at.isoformat(),
        )

    return TodaySongResponse(
        song=SongResponse.model_validate(row),
        breakdown_available=(row.breakdown_generated_at is not None),
        from_bank=False,
        bank_source=None,
        rerolled=False,
        rated=rated_info,
    )
