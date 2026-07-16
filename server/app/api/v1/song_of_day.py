# server/app/api/v1/song_of_day.py
# GET /api/v1/song-of-day — trivial selector per D-04.
# Reads the single hardcoded row from the songs table.
# No user_id or date params in Phase 1; Phase 3 replaces the selector.
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.seed import seed_songs
from app.db.session import get_db
from app.models.db import Song
from app.models.song import SongResponse

router = APIRouter()


@router.get("/song-of-day", response_model=SongResponse)
async def get_song_of_day(db: AsyncSession = Depends(get_db)) -> SongResponse:
    """Return today's song of the day.

    D-04: trivial selector — SELECT * FROM songs LIMIT 1.
    If the table is empty (e.g., after a fresh migration), seeds the hardcoded row first.
    """
    result = await db.execute(select(Song).limit(1))
    row = result.scalar_one_or_none()

    if row is None:
        # Table is empty — seed and retry
        await seed_songs(db)
        result = await db.execute(select(Song).limit(1))
        row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="No song found. Seed data missing.")

    return SongResponse.model_validate(row)
