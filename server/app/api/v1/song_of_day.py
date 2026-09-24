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
# Phase 4 (Slice B): TodaySongResponse extended with .breakdown_quota (D-06).
#
# Design decisions: D-01 through D-05, D-10, Revision B (bank_source persistence + read-back).
# Threat mitigations: T-03-04-01 (user_id from dep, never body), T-03-04-02 (DB index + app
# pre-check), T-03-04-04 (cross-user marker read filter), T-03-04-05 (bank_source server-computed).
from datetime import datetime, timedelta, timezone
from uuid import UUID

import sqlalchemy.exc
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.governor import BREAKDOWN_CAP, effective_cap
from app.api.deps import get_tz_offset_minutes, get_user_id
from app.db.session import get_db
from app.models.db import Song
from app.models.song import (
    BreakdownQuota,
    SongResponse,
    TodayRatingInfo,
    TodaySongResponse,
    is_renderable_breakdown,
)
from app.selectors.today_song import select_today_song

router = APIRouter()


async def _breakdown_quota(db: AsyncSession, user_id: UUID) -> BreakdownQuota | None:
    """Phase 4 (D-06): quota snapshot for the chip — COUNT + MIN(created_at) from governor_calls.

    Uses the indexed query (ix_governor_calls_user_feature_created) — O(log n) per user.
    Returns None when FLETCHER_CAP_BREAKDOWN has removed the cap: there is no quota to
    report, the client hides the chip and leaves the CTA enabled.
    """
    cap = effective_cap("breakdown", BREAKDOWN_CAP)
    if cap is None:
        return None

    quota_count = await db.scalar(
        text(
            "SELECT COUNT(*) FROM governor_calls "
            "WHERE user_id = :user_id AND feature = 'breakdown' "
            "AND created_at > now() - interval '7 days' "
            "AND error_code IS NULL"
        ),
        {"user_id": str(user_id)},
    )
    oldest_call = await db.scalar(
        text(
            "SELECT MIN(created_at) FROM governor_calls "
            "WHERE user_id = :user_id AND feature = 'breakdown' "
            "AND created_at > now() - interval '7 days' "
            "AND error_code IS NULL"
        ),
        {"user_id": str(user_id)},
    )
    if oldest_call is None:
        resets_at_dt = datetime.now(timezone.utc) + timedelta(days=7)
    else:
        resets_at_dt = oldest_call + timedelta(days=7)
    return BreakdownQuota(
        remaining=max(0, cap - (quota_count or 0)),
        cap=cap,
        resets_at=resets_at_dt.isoformat(),
    )


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
        # Reroll path: use the song_id + bank_source from the persisted reroll marker (Revision B).
        # from_bank derived from bank_source presence — the reroll CTE actually runs the same
        # 75/25 branch logic as fresh picks (reroll_suffix only reseeds), so a reroll CAN land
        # on the working_on_pick branch (bank_source=null). Hard-coding from_bank=True lied
        # about that and left the FromTheBankTag chip in an inconsistent state (from_bank=true
        # but bank_source=null → mobile client-side conditional Boolean(from_bank && bank_source)
        # hid the chip anyway, so the user just never saw a bank chip on rerolled bank songs).
        song_id = reroll_marker["song_id"]
        bank_source = reroll_marker["bank_source"]
        from_bank = bank_source is not None

        # Load the songs row, filtered by user_id for defense-in-depth (T-03-04-04)
        row = (
            await db.execute(
                select(Song).where(Song.id == song_id, Song.user_id == user_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Rerolled song not found.")
    else:
        # FLE-54 Fix 1: check for an already-persisted daily pick marker before
        # running the CTE. The CTE assigns seeded random() values positionally (by
        # index scan order), so any non-HOT write that changes index key set shifts
        # which song wins the identical seed. Persisting on first call makes the day's
        # song a stable fact rather than a recomputation.
        daily_pick_marker = (
            await db.execute(
                text(
                    "SELECT song_id, bank_source FROM user_sessions "
                    "WHERE user_id = :user_id AND local_calendar_day = :day "
                    "  AND is_daily_pick_marker = true "
                    "LIMIT 1"
                ),
                {"user_id": str(user_id), "day": local_day},
            )
        ).mappings().one_or_none()

        if daily_pick_marker is not None:
            # Persisted path: return the stable daily pick.
            song_id = daily_pick_marker["song_id"]
            bank_source = daily_pick_marker["bank_source"]
            from_bank = bank_source is not None

            row = (
                await db.execute(
                    select(Song).where(Song.id == song_id, Song.user_id == user_id)
                )
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="No song found for today's pick.")
        else:
            # Fresh pick path: call the 75/25 CTE selector once, then persist the result
            # so subsequent GETs skip the CTE entirely (uq_user_sessions_daily_pick
            # partial unique index ensures at-most-one marker per user per day).
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

            # Persist the daily pick marker. On a concurrent second GET that races here,
            # the IntegrityError from uq_user_sessions_daily_pick is swallowed — both
            # requests computed the same seed and will return the same song anyway.
            try:
                await db.execute(
                    text(
                        "INSERT INTO user_sessions "
                        "  (id, user_id, song_id, rating, local_calendar_day, "
                        "   tz_offset_minutes, is_reroll_marker, is_daily_pick_marker, "
                        "   bank_source) "
                        "VALUES "
                        "  (gen_random_uuid(), :user_id, :song_id, NULL, :day, :tz, "
                        "   FALSE, TRUE, :bank_source)"
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
                # Race: another concurrent request persisted the marker first. Safe to
                # ignore — both requests computed from the same seed and produced the
                # same song_id.
                await db.rollback()

    # Read same-day rating row (Slice C: populate .rated if the user has already rated)
    rating_row = (
        await db.execute(
            text(
                # FLE-54: is_daily_pick_marker rows are NOT ratings — they carry
                # rating=NULL and is_reroll_marker=false, so without this filter the
                # marker is read back as a rating and TodayRatingInfo fails validation.
                "SELECT rating, rated_at FROM user_sessions "
                "WHERE user_id = :user_id AND song_id = :song_id "
                "  AND local_calendar_day = :day AND is_reroll_marker = false "
                "  AND is_daily_pick_marker = false "
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

    breakdown_quota = await _breakdown_quota(db, user_id)

    return TodaySongResponse(
        song=SongResponse.model_validate(row),
        # FLE-67: both halves — the cache signal AND a renderable snapshot. A row
        # marked generated whose JSONB is empty/partial is dropped to None by
        # SongResponse, and GET /songs/{id}/breakdown now treats it as a miss, so
        # reporting it available would promise a cached breakdown neither returns.
        breakdown_available=(
            row.breakdown_generated_at is not None
            and is_renderable_breakdown(row.breakdown)
        ),
        from_bank=from_bank,
        bank_source=bank_source,
        rerolled=rerolled,
        rated=rated_info,
        rerolls_left=rerolls_left,
        breakdown_quota=breakdown_quota,
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

    reroll_breakdown_quota = await _breakdown_quota(db, user_id)

    return TodaySongResponse(
        song=SongResponse.model_validate(row),
        # FLE-67: both halves — the cache signal AND a renderable snapshot. A row
        # marked generated whose JSONB is empty/partial is dropped to None by
        # SongResponse, and GET /songs/{id}/breakdown now treats it as a miss, so
        # reporting it available would promise a cached breakdown neither returns.
        breakdown_available=(
            row.breakdown_generated_at is not None
            and is_renderable_breakdown(row.breakdown)
        ),
        from_bank=True,  # rerolls are always bank picks (D-05)
        bank_source=bank_source,
        rerolled=True,
        rated=None,  # reroll marker and rating row are distinct (db.py lines 244-251)
        rerolls_left=0,  # spent the one daily reroll
        breakdown_quota=reroll_breakdown_quota,
    )
