"""GET /api/v1/songs/{song_id}/breakdown — lazy-fetch, cache-forever breakdown.

Cache semantics (Claude's Discretion D-11):
  - songs.breakdown_generated_at IS NULL  → call Sonnet, persist, return
  - songs.breakdown_generated_at IS NOT NULL → return cached JSONB, no Sonnet call

On AIBreakdownError (Sonnet failure after retry):
  - Return HTTP 503 with Fletcher-voiced detail
  - breakdown_generated_at stays NULL — next user tap retries

Access control (T-03-02-04): song MUST be owned by X-User-ID; 404 otherwise.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.breakdown import AIBreakdownError, run_technique_breakdown
from app.api.deps import get_user_id
from app.db.session import get_db
from app.models.db import SkillNode, Song, SongSkill
from app.models.song import Breakdown

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/songs/{song_id}/breakdown", response_model=Breakdown)
async def get_breakdown(
    song_id: int,
    user_id=Depends(get_user_id),
    db: AsyncSession = Depends(get_db),
) -> Breakdown:
    """Return the technique breakdown for song_id, generating it on first tap.

    Cache hit: songs.breakdown_generated_at IS NOT NULL  → return JSON directly.
    Cache miss: call run_technique_breakdown, persist to songs.breakdown + set
                breakdown_generated_at atomically, return Breakdown.

    Returns:
        200 Breakdown on success.
        404 if song_id not owned by X-User-ID.
        503 with Fletcher-voiced detail if Sonnet call fails after retry.
    """
    # 1. Load song + access control (T-03-02-04)
    result = await db.execute(
        select(Song).where(Song.id == song_id, Song.user_id == user_id)
    )
    song = result.scalar_one_or_none()
    if song is None:
        raise HTTPException(
            status_code=404, detail="Song not found or not owned by user."
        )

    # 2. Cache hit — short-circuit without Sonnet call (T-03-02-02, D-11)
    if song.breakdown_generated_at is not None:
        return Breakdown.model_validate(song.breakdown)

    # 3. Cache miss — resolve target skills for this song + user_level
    skill_rows = (
        await db.execute(
            select(SkillNode.name)
            .join(SongSkill, SongSkill.skill_node_id == SkillNode.id)
            .where(SongSkill.song_id == song_id, SkillNode.user_id == user_id)
        )
    ).scalars().all()
    target_skill_names = list(skill_rows)

    user_level_scalar = await db.scalar(
        select(func.coalesce(func.avg(SkillNode.mastery), 0.5)).where(
            SkillNode.user_id == user_id, SkillNode.level == "leaf"
        )
    )
    user_level = float(user_level_scalar) if user_level_scalar is not None else 0.5

    # 4. Sonnet call — NO SAVEPOINT (RESEARCH §5, plain transaction on cache write)
    # On AIBreakdownError: return 503; breakdown_generated_at stays NULL (retry ok)
    try:
        breakdown = await run_technique_breakdown(
            song.title, song.artist or "", target_skill_names, user_level
        )
    except AIBreakdownError as exc:
        logger.warning(
            "Breakdown call failed for song %s user %s: %s", song_id, user_id, exc
        )
        raise HTTPException(
            status_code=503,
            detail="Fletcher stepped away from the desk. Give me another second and try again.",
        )

    # 5. Persist atomically — write both fields + commit in one shot.
    # SQLAlchemy autobegin means the session already has a transaction open from
    # the SELECT above. We set values and commit directly (same as users.py pattern).
    # On DB failure here, breakdown_generated_at stays NULL so next tap retries.
    song.breakdown = breakdown.model_dump()
    song.breakdown_generated_at = datetime.now(timezone.utc)
    await db.commit()

    return breakdown
