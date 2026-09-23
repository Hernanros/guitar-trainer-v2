"""Regenerate breakdowns that predate drills (FLE-54 Fix 2).

WHY THIS EXISTS
---------------
D-11 is cache-forever: once `songs.breakdown_generated_at IS NOT NULL`, the
breakdown endpoint returns the stored JSONB and never calls Sonnet again. Drills
arrived in Phase 4.1. Every breakdown generated before that has no `drills` key
at all — `_load_cached_breakdown` strips the empty list so it still validates, and
the mobile screen hides an empty drills section, so it degrades quietly instead of
erroring. The consequence is permanent: the earliest and most-used songs show a
drill-less breakdown forever, and drills are the core of the v1 session.

WHAT THIS IS NOT
----------------
This is a **versioned-schema backfill**, not a cache-invalidation policy. It does
not widen the cache-forever contract. It targets exactly the rows whose stored
breakdown predates the drills schema, regenerates them once, and stops. Rows that
already carry drills are never touched unless a caller names one explicitly via
`force_song_id` (the single-song escape hatch the issue asked for).

COST CONTROL
------------
Regeneration goes through `run_technique_breakdown`, which carries the `@governed`
decorator. Every call lands in `governor_calls` with token counts and is subject
to the same per-user weekly cap as a user-initiated breakdown. The backfill gets
no special exemption — if a user is at their cap, their rows are reported as
skipped rather than silently bypassing the cost model.

SOFT-FAIL ROWS
--------------
A post-4.1 row can also have `drills: []` because Sonnet returned none and
Landmine #3 soft-failed, or because every drill was dropped as a hallucinated
target_skill_temp_id. Those are indistinguishable from pre-4.1 rows by content
alone, so `find_candidates` separates them by `breakdown_generated_at` against
`DRILLS_SHIPPED_AT` and only counts pre-4.1 rows as backfill candidates. A
soft-fail row is a generation outcome, not a schema gap; re-rolling it is a
judgement call for whoever runs this, which is what `include_soft_fail` is for.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.breakdown import AIBreakdownError, run_technique_breakdown
from app.ai.governor import AnthropicQuotaExceededError, BudgetExceededError
from app.models.db import SkillNode, Song, SongSkill
from app.selectors.player_level import floor_player_level

logger = logging.getLogger(__name__)

# The 4.1 release that introduced drills. Rows stamped before this cannot have a
# drills key; rows stamped after it that lack one are soft-fails, not schema gaps.
DRILLS_SHIPPED_AT = datetime(2026, 9, 10, tzinfo=timezone.utc)

# Observed prod averages over real breakdown calls (governor_calls, Sonnet 4.6).
# Used only to print an estimate before spending — actuals are recorded by @governed.
AVG_PROMPT_TOKENS = 5356
AVG_OUTPUT_TOKENS = 4649
SONNET_INPUT_USD_PER_MTOK = 3.00
SONNET_OUTPUT_USD_PER_MTOK = 15.00


def estimate_cost_usd(n_songs: int) -> float:
    """Rough dollar cost of regenerating `n_songs` breakdowns, from prod averages."""
    per_call = (
        AVG_PROMPT_TOKENS / 1_000_000 * SONNET_INPUT_USD_PER_MTOK
        + AVG_OUTPUT_TOKENS / 1_000_000 * SONNET_OUTPUT_USD_PER_MTOK
    )
    return per_call * n_songs


def _empty_drills_predicate():
    """SQL for "this breakdown has no usable drills".

    Three states collapse to the same answer: the key is absent (pre-4.1 rows), the
    key holds `[]` (soft-fails), or the key holds something that is not an array at
    all (a malformed write). `jsonb_array_length` raises on anything but an array —
    including the SQL NULL a missing key yields — so the type check cannot be an OR
    arm beside it; Postgres does not promise to short-circuit OR and will happily
    evaluate the length call on a scalar and error out ("cannot get array length of
    a scalar"). CASE *does* guarantee arm ordering, so the length call is only ever
    reached for a value already known to be an array.
    """
    drills = Song.breakdown["drills"]
    return (
        case(
            (
                func.jsonb_typeof(drills) == "array",
                func.jsonb_array_length(drills),
            ),
            else_=0,
        )
        == 0
    )


@dataclass
class Candidate:
    song_id: int
    user_id: UUID
    title: str
    artist: str
    generated_at: datetime | None
    reason: str  # "pre_drills_schema" | "soft_fail_empty" | "forced"


@dataclass
class BackfillResult:
    song_id: int
    ok: bool
    detail: str
    drills_written: int = 0


async def find_candidates(
    db: AsyncSession,
    *,
    include_soft_fail: bool = False,
    force_song_id: int | None = None,
) -> list[Candidate]:
    """Rows whose cached breakdown has no usable drills.

    `force_song_id` returns exactly that song regardless of its drills state — the
    single-song regeneration path. It still requires a cached breakdown to exist,
    because a song with no breakdown is already handled by the normal cache-miss
    path the next time anyone opens it.
    """
    if force_song_id is not None:
        row = (
            await db.execute(select(Song).where(Song.id == force_song_id))
        ).scalar_one_or_none()
        if row is None:
            return []
        if row.breakdown_generated_at is None:
            logger.warning(
                "Song %s has no cached breakdown; the normal cache-miss path covers it.",
                force_song_id,
            )
            return []
        return [
            Candidate(
                song_id=row.id,
                user_id=row.user_id,
                title=row.title,
                artist=row.artist or "",
                generated_at=row.breakdown_generated_at,
                reason="forced",
            )
        ]

    rows = (
        await db.execute(
            select(Song)
            .where(
                Song.breakdown_generated_at.isnot(None),
                _empty_drills_predicate(),
            )
            .order_by(Song.breakdown_generated_at.asc())
        )
    ).scalars().all()

    out: list[Candidate] = []
    for row in rows:
        generated_at = row.breakdown_generated_at
        if generated_at is not None and generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        pre_drills = generated_at is not None and generated_at < DRILLS_SHIPPED_AT
        if not pre_drills and not include_soft_fail:
            continue
        out.append(
            Candidate(
                song_id=row.id,
                user_id=row.user_id,
                title=row.title,
                artist=row.artist or "",
                generated_at=generated_at,
                reason="pre_drills_schema" if pre_drills else "soft_fail_empty",
            )
        )
    return out


async def regenerate_one(db: AsyncSession, candidate: Candidate) -> BackfillResult:
    """Regenerate and persist one song's breakdown.

    Mirrors the cache-miss branch of GET /songs/{id}/breakdown: resolve the user's
    target skills, floor the player level, call Sonnet through @governed, drop
    drills whose target_skill_temp_id was hallucinated, then persist. Kept as a
    separate function rather than reaching into the endpoint so the live request
    path is not disturbed by a maintenance job.
    """
    song = (
        await db.execute(
            select(Song).where(
                Song.id == candidate.song_id, Song.user_id == candidate.user_id
            )
        )
    ).scalar_one_or_none()
    if song is None:
        return BackfillResult(candidate.song_id, False, "song disappeared mid-run")

    skill_rows = (
        await db.execute(
            select(SkillNode.id, SkillNode.name)
            .join(SongSkill, SongSkill.skill_node_id == SkillNode.id)
            .where(SongSkill.song_id == song.id, SkillNode.user_id == candidate.user_id)
        )
    ).all()
    target_skills = [{"id": str(sid), "name": sname} for sid, sname in skill_rows]
    if not target_skills:
        # Without target skills Sonnet has nothing to anchor a drill to, and every
        # drill it invents would be dropped by the hallucination filter below —
        # we would pay for the call and still write drills: []. Skip instead.
        return BackfillResult(
            candidate.song_id, False, "no target skills mapped for this song/user"
        )
    valid_skill_ids = {s["id"] for s in target_skills}

    avg_mastery = await db.scalar(
        select(func.avg(SkillNode.mastery)).where(
            SkillNode.user_id == candidate.user_id, SkillNode.level == "leaf"
        )
    )
    user_level = float(floor_player_level(avg_mastery))

    try:
        breakdown = await run_technique_breakdown(
            song.title,
            song.artist or "",
            target_skills,
            user_level,
            db=db,
            user_id=candidate.user_id,
        )
    except BudgetExceededError as exc:
        # The cap is deliberately honoured: the backfill does not get to spend
        # what a user would be refused.
        return BackfillResult(
            candidate.song_id, False, f"per-user cap hit, resets {exc.resets_at}"
        )
    except AnthropicQuotaExceededError:
        return BackfillResult(candidate.song_id, False, "anthropic org quota exceeded")
    except AIBreakdownError as exc:
        return BackfillResult(candidate.song_id, False, f"sonnet failed: {exc}")

    if breakdown.drills:
        kept = []
        for d in breakdown.drills:
            if d.target_skill_temp_id in valid_skill_ids:
                kept.append(d)
            else:
                logger.warning(
                    "Dropping hallucinated drill target_skill_temp_id=%s song_id=%s name=%r",
                    d.target_skill_temp_id,
                    song.id,
                    d.name,
                )
        breakdown.drills = kept

    if not breakdown.drills:
        # Do not overwrite a working cached breakdown with another drill-less one.
        # The old row is no worse than the new one and the user keeps their tab.
        return BackfillResult(
            candidate.song_id, False, "regenerated breakdown still had no drills; left as-is"
        )

    song.breakdown = breakdown.model_dump()
    song.breakdown_generated_at = datetime.now(timezone.utc)
    await db.commit()

    return BackfillResult(
        candidate.song_id, True, "regenerated", drills_written=len(breakdown.drills)
    )
