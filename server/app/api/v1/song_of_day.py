# server/app/api/v1/song_of_day.py
# GET /api/v1/song-of-day — Song of the Day endpoint (Phase 3 per-user selector).
# POST /api/v1/today-song/reroll — One-per-day reroll endpoint (Phase 3 gap-closure 03-04).
#
# Phase 1: trivial stub returning SELECT * FROM songs LIMIT 1.
# Phase 3 (Slice A): TodaySongResponse shape with breakdown_available, from_bank, rerolled, bank_source.
# Phase 3 (Slice C): TodaySongResponse extended with .rated field.
# Phase 3 (03-04 gap-closure): Wires select_today_song CTE; adds POST /today-song/reroll;
#   propagates bank_source from selector on fresh picks and reads it back from the reroll
#   marker row on same-day GETs; adds rerolls_left field (D-05 one-per-day contract).
#
# Design decisions: D-01 through D-05, D-10, Revision B (bank_source persistence + read-back).
# Threat mitigations: T-03-04-01 (user_id from dep, never body), T-03-04-02 (DB index + app
# pre-check), T-03-04-04 (cross-user marker read filter), T-03-04-05 (bank_source server-computed).
from uuid import UUID

import sqlalchemy.exc
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tz_offset_minutes, get_user_id
from app.db.session import get_db
from app.models.db import Song
from app.models.song import SongResponse, TodayRatingInfo, TodaySongResponse
from app.selectors.today_song import select_today_song

router = APIRouter()


@router.get("/song-of-day", response_model=TodaySongResponse)
async def get_song_of_day(
    user_id: UUID = Depends(get_user_id),
    tz_offset_minutes: int = Depends(get_tz_offset_minutes),
    db: AsyncSession = Depends(get_db),
) -> TodaySongResponse:
    """Return today's Song of the Day for the requesting user.

    Calls select_today_song (75/25 CTE, D-01 through D-04) to pick the song.
    If the user has already rerolled today, reads the reroll marker row and returns
    that song with bank_source propagated from the persisted marker (Revision B).

    Threat T-03-04-04 mitigation: marker row SELECT filters on user_id.
    """
    # Compute local calendar day server-side (D-10)
    local_day = await db.scalar(
        text(
            "SELECT DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute'))"
        ),
        {"tz": tz_offset_minutes},
    )

    # Check for a reroll marker for today (threat T-03-04-04: user_id filter mandatory)
    reroll_marker = (
        await db.execute(
            text(
                "SELECT song_id, bank_source FROM user_sessions "
                "WHERE user_id = :user_id AND local_calendar_day = :day AND is_reroll_marker = true "
                "LIMIT 1"
            ),
            {"user_id": str(user_id), "day": local_day},
        )
    ).mappings().one_or_none()

    rerolled = reroll_marker is not None
    rerolls_left = 0 if rerolled else 1

    if reroll_marker is not None:
        # Reroll path: use the song_id + bank_source from the persisted reroll marker (Revision B)
        song_id = reroll_marker["song_id"]
        from_bank = True  # rerolls are always bank picks per D-05 selector contract
        bank_source = reroll_marker["bank_source"]

        # Load the songs row, filtered by user_id for defense-in-depth (T-03-04-04)
        row = (
            await db.execute(
                select(Song).where(Song.id == song_id, Song.user_id == user_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Rerolled song not found.")
    else:
        # Fresh pick path: call the 75/25 CTE selector (closes gap 1)
        song_id, from_bank, bank_source = await select_today_song(
            db, user_id, tz_offset_minutes, force_reroll=False
        )
        if song_id is None:
            raise HTTPException(
                status_code=404,
                detail="No song available. Add songs from Settings.",
            )

        # Load the songs row (selector already scoped result to user — extra filter for D&S)
        row = (
            await db.execute(
                select(Song).where(Song.id == song_id, Song.user_id == user_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="No song found for today's pick.")

    # Read same-day rating row (Slice C: populate .rated if the user has already rated)
    rating_row = (
        await db.execute(
            text(
                "SELECT rating, rated_at FROM user_sessions "
                "WHERE user_id = :user_id AND song_id = :song_id "
                "  AND local_calendar_day = :day AND is_reroll_marker = false "
                "LIMIT 1"
            ),
            {"user_id": str(user_id), "song_id": row.id, "day": local_day},
        )
    ).mappings().one_or_none()

    rated_info = None
    if rating_row is not None:
        rated_info = TodayRatingInfo(
            rating=(
                rating_row["rating"].value
                if hasattr(rating_row["rating"], "value")
                else rating_row["rating"]
            ),
            rated_at=rating_row["rated_at"].isoformat(),
        )

    return TodaySongResponse(
        song=SongResponse.model_validate(row),
        breakdown_available=(row.breakdown_generated_at is not None),
        from_bank=from_bank,
        bank_source=bank_source,
        rerolled=rerolled,
        rated=rated_info,
        rerolls_left=rerolls_left,
    )


@router.post("/today-song/reroll", response_model=TodaySongResponse)
async def reroll_today_song(
    user_id: UUID = Depends(get_user_id),
    tz_offset_minutes: int = Depends(get_tz_offset_minutes),
    db: AsyncSession = Depends(get_db),
) -> TodaySongResponse:
    """Force a daily reroll — picks a new song from the bank and persists a reroll marker.

    Enforces one-per-day via:
    1. Application-layer pre-check (reduces DB round-trip for common repeat-tap case).
    2. DB partial-unique index uq_user_sessions_daily_reroll (authoritative per T-03-04-02).

    On second POST same day: returns HTTP 409 (either from pre-check or index).

    Threat T-03-04-01: user_id from Depends(get_user_id), NEVER from request body.
    Threat T-03-04-02: INSERT wrapped in IntegrityError → 409.
    Threat T-03-04-05: bank_source is server-computed by select_today_song, not client-supplied.
    """
    # Compute local calendar day server-side (D-10)
    local_day = await db.scalar(
        text(
            "SELECT DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute'))"
        ),
        {"tz": tz_offset_minutes},
    )

    # Application-layer legibility short-circuit (T-03-04-02 — DB index is the authority)
    existing = (
        await db.execute(
            text(
                "SELECT 1 FROM user_sessions "
                "WHERE user_id = :user_id AND local_calendar_day = :day AND is_reroll_marker = true "
                "LIMIT 1"
            ),
            {"user_id": str(user_id), "day": local_day},
        )
    ).one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Already used today's reroll.")

    # Compute new pick with force_reroll=True (different seed suffix per RESEARCH §8 landmine 3)
    song_id, _from_bank, bank_source = await select_today_song(
        db, user_id, tz_offset_minutes, force_reroll=True
    )
    if song_id is None:
        raise HTTPException(status_code=404, detail="No alternative song available.")

    # Insert reroll marker row (T-03-04-01: user_id from dep; T-03-04-05: bank_source from selector)
    try:
        await db.execute(
            text(
                "INSERT INTO user_sessions "
                "  (id, user_id, song_id, rating, local_calendar_day, tz_offset_minutes, "
                "   is_reroll_marker, bank_source) "
                "VALUES "
                "  (gen_random_uuid(), :user_id, :song_id, NULL, :day, :tz, TRUE, :bank_source)"
            ),
            {
                "user_id": str(user_id),
                "song_id": song_id,
                "day": local_day,
                "tz": tz_offset_minutes,
                "bank_source": bank_source,
            },
        )
        await db.commit()
    except sqlalchemy.exc.IntegrityError:
        # Race condition: concurrent second POST hit the partial-unique index first
        await db.rollback()
        raise HTTPException(status_code=409, detail="Already used today's reroll.")

    # Load the songs row for the reroll pick (T-03-04-04: user_id filter)
    row = (
        await db.execute(
            select(Song).where(Song.id == song_id, Song.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Reroll song not found.")

    return TodaySongResponse(
        song=SongResponse.model_validate(row),
        breakdown_available=(row.breakdown_generated_at is not None),
        from_bank=True,  # rerolls are always bank picks (D-05)
        bank_source=bank_source,
        rerolled=True,
        rated=None,  # reroll marker and rating row are distinct (db.py lines 244-251)
        rerolls_left=0,  # spent the one daily reroll
    )
