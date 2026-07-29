# server/app/api/v1/sessions.py
# POST /api/v1/sessions — atomic session rating write + mastery update.
#
# Slice C (03-03): closes the daily loop. No LLM in this path (SKILL-03).
#
# Transaction pattern: `async with db.begin():` wraps ALL database operations.
# This works because db.begin() is called BEFORE any autobegin trigger fires
# (no db.execute/scalar before the begin block). Per PATTERNS.md lines 1097-1101
# and RESEARCH §5: plain begin(), NO SAVEPOINT (no LLM call in this path).
#
# Idempotency: two layers per T-03-03-02:
#   (a) App-level SELECT COUNT before INSERT → immediate 409 on match (legibility)
#   (b) DB partial-unique index uq_user_sessions_daily_rating catches concurrent races
#
# Access control (T-03-03-01):
#   - SELECT song WHERE id=body.song_id AND user_id=x_user_id → 404 if not found
#   - SkillNode.user_id == user_id in UPDATE WHERE clause (defense in depth)
#
# SKILL-03 compliance: no LLM imports in this write path.
import logging
import uuid
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Numeric, bindparam, cast, func, literal, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tz_offset_minutes, get_user_id
from app.db.session import get_db
from app.models.db import SkillNode, Song, SongSkill, UserSession
from app.models.session import SessionCreate, SessionResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# D-08: Fixed additive mastery shifts per rating tier.
# D-07: Equal-weight — all attached skill_nodes shift by the same amount.
# NOTE: D-07 equal-weight — no junction-table weight column is read in this path.
RATING_SHIFTS: dict[str, Decimal] = {
    "not_my_tempo": Decimal("-0.05"),
    "getting_closer": Decimal("0.05"),
    "thats_what_im_looking_for": Decimal("0.15"),
}


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def submit_rating(
    body: SessionCreate,
    user_id: UUID = Depends(get_user_id),
    tz_offset_minutes: int = Depends(get_tz_offset_minutes),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Record a session rating and atomically update mastery on all attached skill_nodes.

    Single `async with db.begin():` transaction wraps ALL operations (reads + writes).
    No LLM call — deterministic writes principle (SKILL-03, D-08).
    """
    # Compute shift here (pure Python, no DB) so it's available inside the transaction.
    shift = RATING_SHIFTS[body.rating]

    session_row: UserSession | None = None
    local_day = None

    try:
        async with db.begin():
            # 1. Compute local calendar day server-side (D-10 — never trust client for date math)
            local_day = await db.scalar(
                text(
                    "SELECT DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute'))"
                ),
                {"tz": tz_offset_minutes},
            )

            # 2. Access control: verify song exists and belongs to this user (T-03-03-01)
            song = (
                await db.execute(
                    select(Song).where(
                        Song.id == body.song_id,
                        Song.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if song is None:
                raise HTTPException(
                    status_code=404,
                    detail="Song not found or not owned by user.",
                )

            # 3. Application-level idempotency guard — T-03-03-02 layer (a)
            #    Check before INSERT for legibility; the DB constraint below catches
            #    concurrent double-taps that slip through this SELECT.
            already = await db.scalar(
                select(func.count(UserSession.id)).where(
                    UserSession.user_id == user_id,
                    UserSession.song_id == body.song_id,
                    UserSession.local_calendar_day == local_day,
                    UserSession.is_reroll_marker == False,  # noqa: E712 — SQLAlchemy col comparison
                )
            )
            if already:
                raise HTTPException(
                    status_code=409,
                    detail="Already rated this song today.",
                )

            # 4. INSERT session row
            session_row = UserSession(
                id=uuid.uuid4(),
                user_id=user_id,
                song_id=body.song_id,
                rating=body.rating,
                local_calendar_day=local_day,
                tz_offset_minutes=tz_offset_minutes,
                is_reroll_marker=False,
            )
            db.add(session_row)

            # 5. UPDATE mastery on every leaf skill_node in song_skills for this song.
            # T-03-03-03: SQL-side clamp with typed numeric literals (RESEARCH §8 landmine 11).
            # RESEARCH §9 Q5: explicit updated_at=func.now() — SQLAlchemy onupdate does NOT
            #   fire on bulk UPDATE via execute(); must be stated explicitly.
            # T-03-03-07 D-07: equal-weight — no song_skill weight column read.
            # T-03-03-01 defense-in-depth: SkillNode.user_id == user_id filter ensures
            #   even a crafted body.song_id cannot shift another user's mastery.
            # T-03-03-03: SQL clamp with typed numeric literals (RESEARCH §8 landmine 11).
            # asyncpg does not support Postgres `::numeric` cast syntax in parameterized queries.
            # Use SQLAlchemy's func.least / func.greatest / cast() instead — produces valid
            # parameterized SQL without `::` syntax and satisfies the landmine 11 requirement.
            # RESEARCH §9 Q5: explicit updated_at=func.now() bump.
            # T-03-03-07 D-07: equal-weight — no song_skill weight column read.
            await db.execute(
                update(SkillNode)
                .where(
                    SkillNode.id.in_(
                        select(SongSkill.skill_node_id).where(
                            SongSkill.song_id == body.song_id
                        )
                    ),
                    SkillNode.user_id == user_id,
                )
                .values(
                    mastery=func.least(
                        cast(literal(Decimal("1.0")), Numeric(4, 3)),
                        func.greatest(
                            cast(literal(Decimal("0.0")), Numeric(4, 3)),
                            SkillNode.mastery + cast(literal(shift), Numeric(4, 3)),
                        ),
                    ),
                    updated_at=func.now(),
                )
            )
            # db.begin() context manager commits on clean exit, rolls back on exception.
            # No explicit db.commit() call needed here — it's implicit on __aexit__.

    except HTTPException:
        # HTTPExceptions raised inside the begin() block must propagate as-is.
        # The begin() context manager rolls back the transaction automatically.
        raise
    except IntegrityError as exc:
        # T-03-03-02 layer (b): DB partial-unique index caught a concurrent race.
        # Map to 409 regardless of which constraint fires (both mean "already rated").
        if "uq_user_sessions_daily_rating" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="Already rated this song today.",
            )
        raise

    # session_row.rated_at was server_default=func.now(); refresh to read DB value
    # (the transaction committed; safe to refresh now).
    assert session_row is not None
    await db.refresh(session_row)

    return SessionResponse(
        id=session_row.id,
        user_id=session_row.user_id,
        song_id=session_row.song_id,
        rating=(
            session_row.rating.value
            if hasattr(session_row.rating, "value")
            else session_row.rating
        ),
        local_calendar_day=local_day.isoformat(),
        rated_at=session_row.rated_at.isoformat(),
    )
