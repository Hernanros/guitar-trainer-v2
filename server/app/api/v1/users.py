# server/app/api/v1/users.py
# User bootstrap + skill-graph endpoints (Phase 2, upgraded in 02-03).
# Pattern: follows server/app/api/v1/song_of_day.py — AsyncSession dep injection,
# Pydantic response models, HTTPException error handling.
import logging
from datetime import datetime, timezone
from typing import List, Literal, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy import func as sqlfunc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.db import SkillLevel, SkillNode, Song, SongSkill, User
from app.models.skill_node import SkillNodeResponse, SonnetOnboardingOutput
from app.models.user import SkillGraphResponse, UserBootstrapRequest, UserResponse
from app.ai.onboarding import AIParseError, FIXED_ROOTS, run_onboarding_parse

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Soft cap on raw_input size — DoS mitigation (T-02-03-DOS).
_RAW_INPUT_MAX_CHARS_PER_CATEGORY = 10_000


def _check_raw_input_size(raw_input: dict) -> None:
    """Reject requests where any raw_input category exceeds the per-category char cap."""
    for key in ("can_play", "working_on", "aspirational"):
        v = raw_input.get(key, "") or ""
        if isinstance(v, str) and len(v) > _RAW_INPUT_MAX_CHARS_PER_CATEGORY:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"raw_input.{key} exceeds {_RAW_INPUT_MAX_CHARS_PER_CATEGORY}-char cap "
                    f"({len(v)} chars)."
                ),
            )


async def _persist_bootstrap(
    db: AsyncSession,
    user_id: UUID,
    output: Optional[SonnetOnboardingOutput],
    *,
    mode: Literal["full", "bootstrap"],
) -> List[SkillNode]:
    """Persist Sonnet's structured output (mode='full') or the 6-root fallback (mode='bootstrap').

    parent_id FK is DEFERRABLE INITIALLY DEFERRED per migration 0002 — insertion order
    doesn't matter for FK correctness; PostgreSQL only checks the FK at COMMIT time.
    We still sort by level for readability, but this is not load-bearing.

    Returns the inserted SkillNode rows so the caller can build SkillGraphResponse.
    """
    if mode == "bootstrap":
        # 6-root fallback graph — D-07 fail-open path. No Sonnet output required.
        rows = [
            SkillNode(
                id=uuid4(),
                user_id=user_id,
                name=root,
                level="root",
                parent_id=None,
                tempo_bin_low=None,
                tempo_bin_high=None,
            )
            for root in FIXED_ROOTS
        ]
        for row in rows:
            db.add(row)
        await db.flush()
        return rows

    # mode="full" — persist Sonnet's structured DAG + songs + song_skills
    assert output is not None, "output required for mode='full'"

    # 1) Map Sonnet temp_ids to real UUIDs
    temp_to_uuid: dict[str, UUID] = {p.temp_id: uuid4() for p in output.skill_graph}

    # 2) Coerce invalid root names to closest known root (D-08 defense-in-depth).
    #    Recommendation from plan: coerce with warning (safer than rejection for POC).
    root_lookup = {r.lower(): r for r in FIXED_ROOTS}
    for p in output.skill_graph:
        if p.level == "root" and p.name not in FIXED_ROOTS:
            fallback = root_lookup.get(p.name.lower())
            if fallback:
                logger.warning(
                    "Sonnet returned non-canonical root '%s' — coerced to '%s'", p.name, fallback
                )
                p.name = fallback
            else:
                logger.warning(
                    "Sonnet returned unknown root '%s' — coerced to 'Music Theory'", p.name
                )
                p.name = "Music Theory"

    # 3) Insert skill_nodes. Sorted by level for readability; FK is deferrable so order
    #    doesn't matter for correctness (PostgreSQL checks FK only at COMMIT time per migration 0002).
    level_order = {"root": 0, "sub": 1, "leaf": 2}
    ordered = sorted(output.skill_graph, key=lambda p: level_order[p.level])

    for prop in ordered:
        parent_uuid = temp_to_uuid.get(prop.parent_temp_id) if prop.parent_temp_id else None
        db.add(SkillNode(
            id=temp_to_uuid[prop.temp_id],
            user_id=user_id,
            name=prop.name,
            level=prop.level,
            parent_id=parent_uuid,
            tempo_bin_low=prop.tempo_bin_low,
            tempo_bin_high=prop.tempo_bin_high,
            # mastery defaults to 0.0 via server_default (D-11 deterministic-writes principle)
        ))

    await db.flush()   # make SkillNode IDs available for song_skills FK

    # 4) Insert songs
    song_id_by_key: dict[tuple, int] = {}
    for song_prop in output.songs:
        song = Song(
            title=song_prop.title,
            artist=song_prop.artist,
            genre=None,
            difficulty=None,
            bpm=None,
            key=None,
            # non-null JSONB required by schema; Phase 3 will populate with technique breakdown
            breakdown={"placeholder": "Phase 3 will populate breakdown"},
            user_id=user_id,
            category=song_prop.category,
        )
        db.add(song)
        await db.flush()
        song_id_by_key[(song_prop.title.lower(), song_prop.artist.lower())] = song.id

    # 5) Insert song_skills junctions
    for song_prop in output.songs:
        song_id = song_id_by_key.get(
            (song_prop.title.lower(), song_prop.artist.lower())
        )
        if song_id is None:
            continue
        for temp_id in song_prop.skill_temp_ids:
            skill_uuid = temp_to_uuid.get(temp_id)
            if skill_uuid is None:
                logger.warning(
                    "Sonnet referenced unknown temp_id '%s' in song '%s' — skipping.",
                    temp_id,
                    song_prop.title,
                )
                continue
            db.add(SongSkill(song_id=song_id, skill_node_id=skill_uuid))

    await db.flush()

    # Return the freshly-created skill_nodes for the response
    result = await db.execute(
        select(SkillNode).where(SkillNode.user_id == user_id)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/users", response_model=SkillGraphResponse, status_code=201)
async def bootstrap_user(
    body: UserBootstrapRequest,
    db: AsyncSession = Depends(get_db),
) -> SkillGraphResponse:
    """Bootstrap the user (D-04 + D-05 + D-06 + D-07):

    1. Idempotency guard (Revision D): if skill_nodes already exist for this user_id,
       short-circuit with mode='existing' — no Sonnet call, no writes.
    2. Upsert users row + preferences + raw_onboarding_text (parent transaction).
    3. Open SAVEPOINT (async with db.begin_nested()) around: Sonnet call +
       _persist_bootstrap(mode='full').
    4. On AIParseError: SAVEPOINT rolls back automatically; call
       _persist_bootstrap(mode='bootstrap') OUTSIDE the nested block to write 6-root fallback.
    5. Set onboarded_at on the user row.
    6. Single await db.commit() at end.
    """
    _check_raw_input_size(body.raw_input)

    # ---- Idempotency guard (Revision D) ----
    existing_count: int = (await db.execute(
        select(sqlfunc.count()).select_from(SkillNode).where(
            SkillNode.user_id == body.user_id
        )
    )).scalar_one()

    if existing_count > 0:
        # User already bootstrapped. Do NOT call Sonnet. Return existing graph.
        user_row = (await db.execute(
            select(User).where(User.id == body.user_id)
        )).scalar_one_or_none()
        if user_row is None:
            # Extremely unusual: skill_nodes exist but user row is gone. Refuse.
            raise HTTPException(
                status_code=500,
                detail="Data inconsistency: skill_nodes exist without user row.",
            )
        rows = (await db.execute(
            select(SkillNode).where(SkillNode.user_id == body.user_id)
        )).scalars().all()
        return SkillGraphResponse(
            nodes=[SkillNodeResponse.model_validate(r) for r in rows],
            mode="existing",
        )

    # ---- Step 2: upsert user row (parent transaction) ----
    now = datetime.now(timezone.utc)
    upsert = pg_insert(User).values(
        id=body.user_id,
        preferences=body.preferences.model_dump(),
        raw_onboarding_text=body.raw_input,
        # onboarded_at set at Step 5 after graph writes succeed
    ).on_conflict_do_update(
        index_elements=["id"],
        set_={
            "preferences": body.preferences.model_dump(),
            "raw_onboarding_text": body.raw_input,
        },
    )
    await db.execute(upsert)
    # NOTE: no flush/commit yet — the SAVEPOINT below is nested within this parent tx.

    # ---- Step 3 + 4: SAVEPOINT-guarded Sonnet call + full persist ----
    mode: Literal["full", "bootstrap"] = "full"
    skill_rows: List[SkillNode]
    try:
        async with db.begin_nested():
            sonnet_output = await run_onboarding_parse(body.raw_input)
            skill_rows = await _persist_bootstrap(
                db, body.user_id, sonnet_output, mode="full"
            )
    except AIParseError as e:
        # SAVEPOINT auto-rolled-back. Parent tx (user row + preferences) still alive.
        logger.warning(
            "Sonnet-backed bootstrap failed for user %s (%s) — falling back to 6-root graph.",
            body.user_id,
            e,
        )
        skill_rows = await _persist_bootstrap(db, body.user_id, None, mode="bootstrap")
        mode = "bootstrap"

    # ---- Step 5: mark onboarded_at ----
    await db.execute(
        pg_insert(User)
        .values(
            id=body.user_id,
            onboarded_at=now,
            preferences=body.preferences.model_dump(),
            raw_onboarding_text=body.raw_input,
        )
        .on_conflict_do_update(index_elements=["id"], set_={"onboarded_at": now})
    )

    # ---- Step 6: single commit ----
    await db.commit()

    return SkillGraphResponse(
        nodes=[SkillNodeResponse.model_validate(r) for r in skill_rows],
        mode=mode,
    )


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Return the user's preferences and onboarded_at timestamp.

    Returns 404 if the user has not been bootstrapped yet.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found.")
    return UserResponse(
        id=row.id,
        preferences=row.preferences,
        onboarded_at=row.onboarded_at.isoformat() if row.onboarded_at else None,
    )


@router.get("/users/{user_id}/skill-graph", response_model=SkillGraphResponse)
async def get_skill_graph(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> SkillGraphResponse:
    """Return the user's full skill graph (tree via parent_id, no separate edges list per D-09)."""
    user_row = (await db.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()
    if user_row is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

    rows = (await db.execute(
        select(SkillNode).where(SkillNode.user_id == user_id)
    )).scalars().all()
    return SkillGraphResponse(
        nodes=[SkillNodeResponse.model_validate(r) for r in rows],
        # get-endpoint always returns mode="full" — fail-open marker is set at bootstrap time only
        mode="full",
    )


@router.post("/users/{user_id}/re-run", response_model=SkillGraphResponse, status_code=200)
async def re_run_onboarding(
    user_id: UUID,
    body: UserBootstrapRequest,
    db: AsyncSession = Depends(get_db),
) -> SkillGraphResponse:
    """Settings re-run (per D-14 Claude's Discretion):

    Wipes user's songs + song_skills + skill_nodes; keeps users row + preferences
    (overwritten with new body); then re-runs the same SAVEPOINT fail-open bootstrap.

    Protection: refuses 403 if user_id is the system UUID (T-02-03-06 — seed data guard).
    """
    if body.user_id != user_id:
        raise HTTPException(
            status_code=400, detail="Body user_id must match path user_id."
        )

    if str(user_id) == "00000000-0000-0000-0000-000000000000":
        raise HTTPException(
            status_code=403,
            detail="Re-run against system user is forbidden (protects seed data).",
        )

    _check_raw_input_size(body.raw_input)

    # Verify user exists before wiping anything
    user_row = (await db.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()
    if user_row is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

    # Wipe order: song_skills first (FK to both songs + skill_nodes), then songs, then skill_nodes
    await db.execute(
        delete(SongSkill).where(
            SongSkill.song_id.in_(
                select(Song.id).where(Song.user_id == user_id)
            )
        )
    )
    await db.execute(delete(Song).where(Song.user_id == user_id))
    await db.execute(delete(SkillNode).where(SkillNode.user_id == user_id))

    # Update preferences + raw_input on the user row (parent tx)
    user_row.preferences = body.preferences.model_dump()
    user_row.raw_onboarding_text = body.raw_input

    # SAVEPOINT-guarded Sonnet + full persist (same pattern as bootstrap_user)
    mode: Literal["full", "bootstrap"] = "full"
    skill_rows: List[SkillNode]
    try:
        async with db.begin_nested():
            sonnet_output = await run_onboarding_parse(body.raw_input)
            skill_rows = await _persist_bootstrap(
                db, user_id, sonnet_output, mode="full"
            )
    except AIParseError as e:
        logger.warning(
            "Sonnet-backed re-run failed for user %s (%s) — falling back to 6-root graph.",
            user_id,
            e,
        )
        skill_rows = await _persist_bootstrap(db, user_id, None, mode="bootstrap")
        mode = "bootstrap"

    user_row.onboarded_at = datetime.now(timezone.utc)
    await db.commit()

    return SkillGraphResponse(
        nodes=[SkillNodeResponse.model_validate(r) for r in skill_rows],
        mode=mode,
    )
