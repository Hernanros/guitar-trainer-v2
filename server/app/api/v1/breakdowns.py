"""GET /api/v1/songs/{song_id}/breakdown — lazy-fetch, cache-forever breakdown.

Cache semantics (Claude's Discretion D-11):
  - songs.breakdown_generated_at IS NULL  → call Sonnet, persist, return
  - songs.breakdown_generated_at IS NOT NULL → return cached JSONB, no Sonnet call

Phase 4.1 (Plan 04.1-02 B1 FIX) — response shape changed to BreakdownEnvelope:
  Every GET /api/v1/songs/{id}/breakdown response is now wrapped in a
  BreakdownEnvelope { breakdown: Breakdown, drill_rated_today_indices: list[int] }.

  The `drill_rated_today_indices` field is COMPUTED PER-REQUEST from user_sessions
  rows (not cached). The Breakdown itself (cached JSONB) is unchanged — D-11
  cache-forever contract preserved.

  This is the durable server-derived source of truth for the mobile drill-primary
  UI logic (replaces the fragile QueryClient mutation-cache subscription pattern).

  BREAKING RESPONSE SHAPE: mobile consumers must access `response.breakdown.drills`
  (etc.) instead of `response.drills`. Plan 03 (mobile) regenerates schema.d.ts to
  pick up the new shape.

On AIBreakdownError (Sonnet failure after retry):
  - Return HTTP 503 with Fletcher-voiced detail
  - breakdown_generated_at stays NULL — next user tap retries

On BudgetExceededError (per-user 5-per-local-day cap hit — FLE-92):
  - Return HTTP 429 with BREAKDOWN_CAPPED body (D-02 Fletcher voice)
  - No Sonnet dispatch occurred

On PilotBudgetExhaustedError (global $25 pilot ceiling reached — FLE-92):
  - Return HTTP 429 with PILOT_BUDGET_SPENT body
  - Deliberately a DIFFERENT code from BREAKDOWN_CAPPED: the cap clears at
    midnight and the ceiling does not clear at all without Hernan raising it, so
    one message cannot serve both without lying to somebody.

On AnthropicQuotaExceededError (Phase 4 — org-level Anthropic 429):
  - Return HTTP 503 with FLETCHER_OUT body (D-08)

Access control (T-03-02-04): song MUST be owned by X-User-ID; 404 otherwise.
"""
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.breakdown import AIBreakdownError, run_technique_breakdown
from app.ai.governor import (
    AnthropicQuotaExceededError,
    BudgetExceededError,
    PilotBudgetExhaustedError,
)
from app.api.deps import get_tz_offset_minutes, get_user_id
from app.db.session import get_db
from app.models.db import SkillNode, Song, SongSkill, UserSession
from app.models.song import Breakdown, BreakdownEnvelope, is_renderable_breakdown
from app.selectors.player_level import floor_player_level

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Limit copy (FLE-92 item 3) — review with Nadia before editing
# ---------------------------------------------------------------------------
# What the user used to hit was "Not my tempo. You've had 3 breakdowns this week.
# Come back in 7 days." Three things were wrong with it:
#
#   - "Not my tempo" is the label on a RATING PILL in the player. Reusing it as an
#     error heading made the refusal read as the app insulting the player's playing.
#   - It named a wall a week wide with no stated reason, which a motivated user
#     reads as "this feature is broken" rather than "this feature is rationed".
#   - The day count was computed from a rolling window, so it drifted while the
#     screen was open.
#
# Both strings below are deliberately plain about the fact that there IS a limit and
# why. A rationed feature that says so keeps its credibility; one that just refuses
# loses it.
#
# Both are paired with copy in mobile/src/components/BreakdownErrorCard.tsx, which is
# what a current client renders; these are what ships when the client is older than
# the server, so the two have to agree in SUBSTANCE. Only one of them is byte-for-byte
# identical (FLE-97, Nadia):
#
#   - PILOT_BUDGET_SPENT_MESSAGE is verbatim the client's body string.
#   - CAPPED_MESSAGE_TEMPLATE is NOT. The client splits it into a heading ("That's your
#     breakdowns for today.") plus a body that opens with the count as its own beat
#     ("All {cap} of them. The counter resets at midnight — …"). Concatenated the two
#     say the same thing; the client's rhythm is the better one, which is why it stays.
#
# So: do not assume editing one file syncs the other, and do not "repair" the cap
# strings into byte equality — the split is deliberate.
CAPPED_MESSAGE_TEMPLATE = (
    "That's your {cap} breakdowns for today. The counter resets at midnight — "
    "go put the ones you've got into your hands."
)

PILOT_BUDGET_SPENT_MESSAGE = (
    "Not you, and nothing's broken — Fletcher's beta runs on a fixed budget for "
    "new breakdowns, and it's spent. Everything you've already pulled apart "
    "still opens."
)


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


async def _compute_drill_rated_today_indices(
    db: AsyncSession,
    user_id: UUID,
    song_id: int,
    tz_offset_minutes: int,
) -> list[int]:
    """B1 FIX (Plan 04.1-02): return sorted list of drill_index values the user has
    rated for (song, today).

    Excludes reroll markers and NULL drill_index (whole-song) rows. Computed on
    every request from user_sessions — NOT cached, so it always reflects the
    current DB state and survives app restart / cache invalidation / cross-
    component navigation on the mobile side.

    Scoped strictly to (user_id, song_id) so USER A's drill ratings do not leak
    into USER B's response (T-04.1-B1 info-disclosure mitigation).
    """
    from sqlalchemy import text as _text
    local_day = await db.scalar(
        _text(
            "SELECT DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute'))"
        ),
        {"tz": tz_offset_minutes},
    )
    rows = await db.execute(
        select(UserSession.drill_index)
        .where(
            UserSession.user_id == user_id,
            UserSession.song_id == song_id,
            UserSession.local_calendar_day == local_day,
            UserSession.drill_index.isnot(None),
            UserSession.is_reroll_marker == False,  # noqa: E712
        )
        .order_by(UserSession.drill_index.asc())
    )
    return [r[0] for r in rows.all()]


@router.get("/songs/{song_id}/breakdown", response_model=BreakdownEnvelope)
async def get_breakdown(
    song_id: int,
    user_id: UUID = Depends(get_user_id),
    tz_offset_minutes: int = Depends(get_tz_offset_minutes),
    db: AsyncSession = Depends(get_db),
) -> BreakdownEnvelope:
    """Return the technique breakdown envelope for song_id, generating it on first tap.

    Response envelope (Plan 04.1-02 B1 FIX):
        {
          "breakdown": { tab, chords, technique_notes, drills },   # cached-forever
          "drill_rated_today_indices": [0, 2, ...]                 # per-request
        }

    Cache hit: songs.breakdown_generated_at IS NOT NULL  → return JSON directly.
    Cache miss: call run_technique_breakdown, persist to songs.breakdown + set
                breakdown_generated_at atomically, return envelope.

    Returns:
        200 BreakdownEnvelope on success.
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
    # FLETCHER_CAP_BREAKDOWN can raise/remove this cap at runtime (see
    # app.ai.governor.effective_cap) — this check must honour the same
    # override the @governed decorator and song-of-day quota use, or the
    # env switch silently fails to uncap the actual breakdown endpoint.
    # BudgetExceededError → 429 BREAKDOWN_CAPPED
    from app.ai.governor import (
        BREAKDOWN_CAP,
        _check_cap,
        _check_pilot_ceiling,
        effective_cap,
        effective_pilot_ceiling,
    )
    from sqlalchemy import text as _text

    call_cap = effective_cap("breakdown", BREAKDOWN_CAP)

    try:
        if call_cap is not None:
            await _check_cap(db, user_id, "breakdown", call_cap)
    except BudgetExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "BREAKDOWN_CAPPED",
                "message": CAPPED_MESSAGE_TEMPLATE.format(cap=call_cap),
                "resets_at": exc.resets_at,
            },
        )

    # 3. Cache hit — short-circuit without Sonnet call (T-03-02-02, D-11)
    # Insert a governor_calls row to record the cached view (cap tracking for all views).
    #
    # FLE-67: the cache is only a hit if the snapshot is actually renderable. A row
    # marked generated whose JSONB is empty or partial would 500 in
    # _load_cached_breakdown below; treating it as a miss regenerates it instead,
    # which is the same self-heal the D-11 cache-forever contract already grants a
    # row that never generated. Costs one Sonnet call against a state no prod row
    # is in today — strictly better than a hard failure on the app's main CTA.
    if song.breakdown_generated_at is not None and is_renderable_breakdown(song.breakdown):
        # Record cached breakdown access in governor_calls (so the cap correctly
        # counts cache-hit views against the 3/7d limit)
        import uuid as _uuid
        from app.ai.client import SONNET_MODEL as _SONNET_MODEL
        cached_call_id = str(_uuid.uuid4())
        # FLE-39: the actuals are written as explicit 0, not left NULL. A cached
        # view spends nothing, but the cap predicate now reads NULL actuals on an
        # old row as "abandoned mid-dispatch, don't charge the user" — so leaving
        # them NULL here would quietly stop cached views counting ten minutes
        # after the fact, i.e. unlimited free breakdown views. Zero is also the
        # truthful number: this row is finished and it cost nothing.
        await db.execute(
            _text(
                "INSERT INTO governor_calls "
                "  (id, user_id, feature, model, prompt_tokens_estimated, "
                "   prompt_tokens_actual, output_tokens_actual, dollars_actual, created_at) "
                "VALUES (:id, :uid, 'breakdown', :model, 0, 0, 0, 0, now())"
            ),
            {"id": cached_call_id, "uid": str(user_id), "model": _SONNET_MODEL},
        )
        await db.commit()

        # B1 FIX: compute drill_rated_today_indices on every read (not cached)
        cached_breakdown = _load_cached_breakdown(song.breakdown)
        drill_indices = await _compute_drill_rated_today_indices(
            db, user_id, song_id, tz_offset_minutes,
        )
        return BreakdownEnvelope(
            breakdown=cached_breakdown,
            drill_rated_today_indices=drill_indices,
        )

    # 3b. FLE-92 — the global pilot ceiling, checked only on the path that spends.
    #
    # Placed AFTER the cache-hit return on purpose. A cached breakdown costs nothing
    # to serve, so refusing one because the pilot's budget is spent would withhold
    # work the user already paid for out of their daily five while billing the pilot
    # nothing for it. The ceiling gates spend, so it gates dispatch — not reads.
    #
    # This is a courtesy check, not the enforcement: _reserve_call_slot checks again
    # under the global advisory lock and that is the one that actually holds. Doing
    # it here too means a refusal costs one SELECT rather than building the whole
    # prompt and resolving skills first, and it keeps the refused response identical
    # whichever of the two fires.
    ceiling = effective_pilot_ceiling()
    if ceiling is not None:
        try:
            await _check_pilot_ceiling(db, ceiling)
        except PilotBudgetExhaustedError as exc:
            logger.warning(
                "Refusing breakdown for song %s user %s: pilot ceiling reached "
                "($%s of $%s).", song_id, user_id, exc.spent, exc.ceiling,
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "PILOT_BUDGET_SPENT",
                    "message": PILOT_BUDGET_SPENT_MESSAGE,
                    "resets_at": None,
                },
            )

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

    # player_level for the teaching prompt. Floored via floor_player_level for the
    # same reason the selector floors it (FLE-49): AVG returns 0.0 — not NULL — for a
    # user whose leaves exist but have never been rated, and the breakdown prompt
    # documents 0.0 as "beginner". Unfloored, every production user was being taught
    # as an absolute beginner. See selectors/player_level.py.
    avg_mastery = await db.scalar(
        select(func.avg(SkillNode.mastery)).where(
            SkillNode.user_id == user_id, SkillNode.level == "leaf"
        )
    )
    user_level = float(floor_player_level(avg_mastery))

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
        # Not merely defensive: this is the arm that fires when two taps race past
        # step 2's unlocked pre-check. _reserve_call_slot's locked check is the
        # authority, and it reports here. Same body as step 2 so the user cannot
        # tell which one refused them.
        raise HTTPException(
            status_code=429,
            detail={
                "code": "BREAKDOWN_CAPPED",
                "message": CAPPED_MESSAGE_TEMPLATE.format(cap=call_cap),
                "resets_at": exc.resets_at,
            },
        )
    except PilotBudgetExhaustedError as exc:
        # FLE-92 — the authoritative ceiling refusal, from under the global advisory
        # lock in _reserve_call_slot. Step 3b's pre-check is unlocked and can be
        # raced; this one cannot, which is what makes "no number of users can exceed
        # the ceiling" hold rather than nearly hold.
        logger.warning(
            "Refusing breakdown for song %s user %s at reservation: pilot ceiling "
            "reached ($%s of $%s).", song_id, user_id, exc.spent, exc.ceiling,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "PILOT_BUDGET_SPENT",
                "message": PILOT_BUDGET_SPENT_MESSAGE,
                "resets_at": None,
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

    # 7. B1 FIX: compute drill_rated_today_indices on every read.
    # On cache-miss (this branch), the user has almost certainly not rated any
    # drills for this song yet (they're seeing the breakdown for the first time),
    # so the list is typically []. But we compute it anyway to keep the response
    # shape consistent and correct for edge cases (e.g., an admin regen after
    # the user already rated).
    drill_indices = await _compute_drill_rated_today_indices(
        db, user_id, song_id, tz_offset_minutes,
    )
    return BreakdownEnvelope(
        breakdown=breakdown,
        drill_rated_today_indices=drill_indices,
    )
