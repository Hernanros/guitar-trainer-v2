# server/app/api/v1/sessions.py
# POST /api/v1/sessions — atomic session rating write + mastery update.
#
# Slice C (03-03): closes the daily loop. No LLM in this path (SKILL-03).
# Plan 04.1-02: extended with per-drill rating write path + drill-primary
#               aggregation policy (RESEARCH.md §Q3 Option A).
#
# Transaction pattern: `async with db.begin():` wraps ALL database operations.
# This works because db.begin() is called BEFORE any autobegin trigger fires
# (no db.execute/scalar before the begin block). Per PATTERNS.md lines 1097-1101
# and RESEARCH §5: plain begin(), NO SAVEPOINT (no LLM call in this path).
#
# Idempotency: two layers per T-03-03-02 (Phase 3) + drill-scope extension (Phase 4.1):
#   (a) App-level SELECT COUNT before INSERT → immediate 409 on match (legibility).
#       Scoped to the drill slot: whole-song rating checks drill_index IS NULL;
#       drill rating checks drill_index = body.drill_index.
#   (b) DB partial-unique index uq_user_sessions_daily_rating (recreated in migration
#       0005 with COALESCE(drill_index, -1) in the key) catches concurrent races.
#
# Drill-primary aggregation policy (Plan 04.1-02, RESEARCH.md §Q3 Option A):
#   If ANY drill rating exists for (user, song, today), a subsequent whole-song
#   rating (drill_index=NULL) returns 409 with code SONG_RATING_BLOCKED_BY_DRILL.
#   The reverse order (song rating first, then drill ratings) IS allowed — song
#   rating cannot follow drills, but drills after a song rating are fine.
#
# Access control (T-03-03-01):
#   - SELECT song WHERE id=body.song_id AND user_id=x_user_id → 404 if not found
#   - SkillNode.user_id == user_id in UPDATE WHERE clause (defense in depth)
#   - Drill path (Plan 04.1-02, T-04.1-05): SkillNode.id == body.target_skill_node_id
#     AND SkillNode.user_id == user_id → if rowcount==0 → 404 (crafted UUID
#     targeting another user's node caught here, cannot mutate mastery)
#
# SKILL-03 compliance: no LLM imports in this write path.
import logging
import uuid
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Numeric, cast, func, literal, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tz_offset_minutes, get_user_id
from app.db.session import get_db
from app.models.db import SkillNode, Song, SongSkill, UserSession
from app.models.session import SessionCreate, SessionResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# D-08: Fixed additive mastery shifts per rating tier.
# D-07: Equal-weight — all attached skill_nodes shift by the same amount
#       (applies to whole-song rating path; drill path writes to exactly one node).
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
    """Record a session rating and atomically update mastery.

    Two write paths (Plan 04.1-02):
      1. Whole-song rating (body.drill_index is None) — UPDATE mastery on every
         leaf skill_node in song_skills for this song (existing D-07 equal-weight path).
      2. Drill rating (body.drill_index is not None) — UPDATE mastery on exactly ONE
         skill_node (body.target_skill_node_id), guarded by SkillNode.user_id == user_id
         (T-04.1-05 defense in depth).

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

            # 3. Plan 04.1-02: drill-primary aggregation policy (RESEARCH.md §Q3 Option A).
            #    Only fires when this is a whole-song rating (drill_index is None).
            #    If any drill rating already exists for (user, song, today), block the
            #    song-level write with 409 SONG_RATING_BLOCKED_BY_DRILL.
            #    Note: this fires BEFORE the app-level idempotency check so the drill-primary
            #    error takes precedence over "Already rated this song today" for users who
            #    somehow have both patterns.
            if body.drill_index is None:
                existing_drill_ratings = await db.scalar(
                    select(func.count(UserSession.id)).where(
                        UserSession.user_id == user_id,
                        UserSession.song_id == body.song_id,
                        UserSession.local_calendar_day == local_day,
                        UserSession.drill_index.isnot(None),
                        UserSession.is_reroll_marker == False,  # noqa: E712
                    )
                )
                if existing_drill_ratings and existing_drill_ratings > 0:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "SONG_RATING_BLOCKED_BY_DRILL: "
                            "Rate the drills you did — the whole-song rating is off after drills."
                        ),
                    )

            # 4. Application-level idempotency guard — T-03-03-02 layer (a), extended
            #    for drill scope (Plan 04.1-02). Whole-song and per-drill ratings are
            #    checked against their own slots (drill_index=NULL vs drill_index=N).
            #    The DB constraint (COALESCE(drill_index, -1) partial-unique index from
            #    migration 0005) below catches concurrent double-taps that slip through.
            # FLE-54: exclude the daily-pick marker. It shares this slot's shape
            # (is_reroll_marker=false, drill_index=NULL, same user/song/day) but is a
            # provenance record, not a rating. Without this filter, persisting today's
            # pick would make the user's first whole-song rating 409 "Already rated".
            already_q = select(func.count(UserSession.id)).where(
                UserSession.user_id == user_id,
                UserSession.song_id == body.song_id,
                UserSession.local_calendar_day == local_day,
                UserSession.is_reroll_marker == False,  # noqa: E712
                UserSession.is_daily_pick_marker == False,  # noqa: E712
            )
            if body.drill_index is None:
                already_q = already_q.where(UserSession.drill_index.is_(None))
            else:
                already_q = already_q.where(UserSession.drill_index == body.drill_index)
            already = await db.scalar(already_q)
            if already:
                label = "drill" if body.drill_index is not None else "song"
                raise HTTPException(
                    status_code=409,
                    detail=f"Already rated this {label} today.",
                )

            # 5. INSERT session row (with drill columns populated when present)
            session_row = UserSession(
                id=uuid.uuid4(),
                user_id=user_id,
                song_id=body.song_id,
                rating=body.rating,
                local_calendar_day=local_day,
                tz_offset_minutes=tz_offset_minutes,
                is_reroll_marker=False,
                drill_index=body.drill_index,
                target_skill_node_id=body.target_skill_node_id,
            )
            db.add(session_row)

            # 6. UPDATE mastery — two branches per Plan 04.1-02.
            # T-03-03-03: SQL-side clamp with typed numeric literals (RESEARCH §8 landmine 11).
            # asyncpg does not support Postgres `::numeric` cast syntax in parameterized
            # queries — use func.least / func.greatest / cast() instead.
            # RESEARCH §9 Q5: explicit updated_at=func.now() (bulk UPDATE via execute()
            # does NOT fire SQLAlchemy onupdate hook).
            if body.drill_index is not None:
                # Drill write path (Plan 04.1-02, T-04.1-08):
                # UPDATE ONLY the specific target_skill_node_id row. The
                # SkillNode.user_id == user_id filter (T-04.1-05) ensures a crafted
                # target_skill_node_id UUID cannot mutate another user's mastery.
                # rowcount==0 → 404 (target not owned by this user).
                result = await db.execute(
                    update(SkillNode)
                    .where(
                        SkillNode.id == body.target_skill_node_id,
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
                if result.rowcount == 0:
                    raise HTTPException(
                        status_code=404,
                        detail="target_skill_node_id not found or not owned by user.",
                    )
            else:
                # Whole-song rating path (existing D-07 equal-weight behavior, unchanged
                # from Phase 3 Slice C). UPDATE mastery on every leaf skill_node in
                # song_skills for this song. Defense in depth: SkillNode.user_id filter
                # ensures cross-user tampering via a crafted body.song_id is neutered.
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
        # Plan 04.1-02: disambiguate drill vs song in the 409 message so the client
        # can tell whether it double-tapped the drill or the song rating.
        if "uq_user_sessions_daily_rating" in str(exc):
            label = "drill" if body.drill_index is not None else "song"
            raise HTTPException(
                status_code=409,
                detail=f"Already rated this {label} today.",
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
        drill_index=session_row.drill_index,
        target_skill_node_id=session_row.target_skill_node_id,
    )
