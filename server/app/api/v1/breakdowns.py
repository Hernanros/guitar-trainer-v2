"""GET /api/v1/songs/{song_id}/breakdown — lazy-fetch, cache-forever breakdown.

Cache semantics (Claude's Discretion D-11):
  - songs.breakdown_generated_at IS NULL  → call Sonnet, persist, return
  - songs.breakdown_generated_at IS NOT NULL → return cached JSONB, no Sonnet call

On AIBreakdownError (Sonnet failure after retry):
  - Return HTTP 503 with Fletcher-voiced detail
  - breakdown_generated_at stays NULL — next user tap retries

On BudgetExceededError (Phase 4 — per-user 3/7d cap hit):
  - Return HTTP 429 with BREAKDOWN_CAPPED body (D-02 Fletcher voice)
  - No Sonnet dispatch occurred

On AnthropicQuotaExceededError (Phase 4 — org-level Anthropic 429):
  - Return HTTP 503 with FLETCHER_OUT body (D-08)

Access control (T-03-02-04): song MUST be owned by X-User-ID; 404 otherwise.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.breakdown import AIBreakdownError, run_technique_breakdown
from app.ai.governor import BudgetExceededError, AnthropicQuotaExceededError
from app.api.deps import get_user_id
from app.db.session import get_db
from app.models.db import SkillNode, Song, SongSkill
from app.models.song import Breakdown

logger = logging.getLogger(__name__)
router = APIRouter()


def _load_cached_breakdown(cached: dict) -> Breakdown:
    """Validate a cached breakdown dict from JSONB into a Breakdown model.

    Landmine (Plan 04.1-01): Breakdown.drills has min_length=2 which fires on
    explicit input. Cached rows serialized via model_dump() include `drills: []`
    when no drills were generated (pre-4.1 rows AND post-4.1 Landmine #3 soft-fail
    rows) — that empty-list key would trip min_length on re-read. Strip it before
    validation so `default_factory=list` kicks in and drills becomes [] cleanly.

    Real drills (2-4 items) pass through unchanged and validate normally.
    """
    if isinstance(cached, dict) and isinstance(cached.get("drills"), list) and not cached["drills"]:
        cached = {k: v for k, v in cached.items() if k != "drills"}
    return Breakdown.model_validate(cached)


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

    # 2. Phase 4: cap-check BEFORE cache hit (D-02 / COST-02).
    # The cap limits "how many times you view/request a breakdown" not just
    # "how many times Sonnet is called". Checking before cache ensures the
    # governor blocks access on call 4+ regardless of cache state.
    # BudgetExceededError → 429 BREAKDOWN_CAPPED
    from app.ai.governor import _check_cap
    from sqlalchemy import text as _text

    try:
        await _check_cap(db, user_id, "breakdown", 3)
    except BudgetExceededError as exc:
        resets_at_dt = datetime.fromisoformat(exc.resets_at)
        if resets_at_dt.tzinfo is None:
            resets_at_dt = resets_at_dt.replace(tzinfo=timezone.utc)
        days_remaining = max(0, (resets_at_dt - datetime.now(timezone.utc)).days + 1)
        message = (
            f"Not my tempo. You've had 3 breakdowns this week. "
            f"Come back in {days_remaining} days."
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "BREAKDOWN_CAPPED",
                "message": message,
                "resets_at": exc.resets_at,
            },
        )

    # 3. Cache hit — short-circuit without Sonnet call (T-03-02-02, D-11)
    # Insert a governor_calls row to record the cached view (cap tracking for all views).
    if song.breakdown_generated_at is not None:
        # Record cached breakdown access in governor_calls (so the cap correctly
        # counts cache-hit views against the 3/7d limit)
        import uuid as _uuid
        from app.ai.client import SONNET_MODEL as _SONNET_MODEL
        cached_call_id = str(_uuid.uuid4())
        await db.execute(
            _text(
                "INSERT INTO governor_calls "
                "  (id, user_id, feature, model, prompt_tokens_estimated, created_at) "
                "VALUES (:id, :uid, 'breakdown', :model, 0, now())"
            ),
            {"id": cached_call_id, "uid": str(user_id), "model": _SONNET_MODEL},
        )
        await db.commit()
        return _load_cached_breakdown(song.breakdown)

    # 4. Cache miss — resolve target skills for this song + user_level
    # Phase 4.1 (Plan 04.1-01 Task 2 rename → Task 3 wiring): fetch both id + name
    # so Sonnet can echo the exact skill_node UUID back in each drill's
    # `target_skill_temp_id` field (Landmine #2 hallucinated-id defense).
    skill_rows = (
        await db.execute(
            select(SkillNode.id, SkillNode.name)
            .join(SongSkill, SongSkill.skill_node_id == SkillNode.id)
            .where(SongSkill.song_id == song_id, SkillNode.user_id == user_id)
        )
    ).all()
    target_skills = [{"id": str(sid), "name": sname} for sid, sname in skill_rows]
    valid_skill_ids: set[str] = {s["id"] for s in target_skills}

    user_level_scalar = await db.scalar(
        select(func.coalesce(func.avg(SkillNode.mastery), 0.5)).where(
            SkillNode.user_id == user_id, SkillNode.level == "leaf"
        )
    )
    user_level = float(user_level_scalar) if user_level_scalar is not None else 0.5

    # 5. Sonnet call — NO SAVEPOINT (RESEARCH §5, plain transaction on cache write)
    # Phase 4: pass db + user_id to run_technique_breakdown for @governed decorator.
    # The @governed decorator handles: INSERT governor_calls row → count_tokens → dispatch.
    # Exception priority: AnthropicQuotaExceededError → 503 FLETCHER_OUT (D-08)
    #                     AIBreakdownError → 503 generic Fletcher message
    # (BudgetExceededError is caught upstream in step 2 — cap-check fires before this)
    try:
        breakdown = await run_technique_breakdown(
            song.title, song.artist or "", target_skills, user_level,
            db=db, user_id=user_id,
        )
    except BudgetExceededError as exc:
        # Defensive catch — should not reach here since step 2 already checked;
        # but handle it gracefully if concurrent requests race past step 2.
        resets_at_dt = datetime.fromisoformat(exc.resets_at)
        if resets_at_dt.tzinfo is None:
            resets_at_dt = resets_at_dt.replace(tzinfo=timezone.utc)
        days_remaining = max(0, (resets_at_dt - datetime.now(timezone.utc)).days + 1)
        message = (
            f"Not my tempo. You've had 3 breakdowns this week. "
            f"Come back in {days_remaining} days."
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "BREAKDOWN_CAPPED",
                "message": message,
                "resets_at": exc.resets_at,
            },
        )
    except AnthropicQuotaExceededError:
        # Phase 4 D-08: org-level Anthropic quota hit — FLETCHER_OUT
        raise HTTPException(
            status_code=503,
            detail={
                "code": "FLETCHER_OUT",
                "message": "Fletcher's on a break. Try again in an hour.",
                "retry_after_hint": "1h",
            },
        )
    except AIBreakdownError as exc:
        logger.warning(
            "Breakdown call failed for song %s user %s: %s", song_id, user_id, exc
        )
        raise HTTPException(
            status_code=503,
            detail="Fletcher stepped away from the desk. Give me another second and try again.",
        )

    # 5a. Landmine #2 defense: drop drills with target_skill_temp_id NOT in the
    # user's resolved skill_node ids (Sonnet hallucination). Log a warning per
    # dropped drill; keep the rest. Endpoint NEVER 500s from a hallucinated id.
    # The filtered list is what gets persisted, so subsequent cache-hit reads
    # never see the bad drill either.
    if breakdown.drills:
        validated_drills = []
        for d in breakdown.drills:
            if d.target_skill_temp_id in valid_skill_ids:
                validated_drills.append(d)
            else:
                logger.warning(
                    "Dropping drill with hallucinated target_skill_temp_id=%s "
                    "(not in user's %d target_skills) for song_id=%s user_id=%s drill_name=%r",
                    d.target_skill_temp_id, len(valid_skill_ids), song_id, user_id, d.name,
                )
        breakdown.drills = validated_drills

    # 6. Persist atomically — write both fields + commit in one shot.
    # SQLAlchemy autobegin means the session already has a transaction open from
    # the SELECT above. We set values and commit directly (same as users.py pattern).
    # On DB failure here, breakdown_generated_at stays NULL so next tap retries.
    song.breakdown = breakdown.model_dump()
    song.breakdown_generated_at = datetime.now(timezone.utc)
    await db.commit()

    return breakdown
