# server/app/api/v1/users.py
# User bootstrap + retrieval endpoints (Phase 2).
# Pattern: follows server/app/api/v1/song_of_day.py — AsyncSession dep injection,
# Pydantic response models, HTTPException error handling.
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.db import User
from app.models.user import UserBootstrapRequest, UserResponse, SkillGraphResponse

router = APIRouter()


@router.post("/users", response_model=SkillGraphResponse, status_code=201)
async def bootstrap_user(
    body: UserBootstrapRequest,
    db: AsyncSession = Depends(get_db),
) -> SkillGraphResponse:
    """Bootstrap stub (02-01): creates the user row + marks onboarded_at.

    Skill-graph population (Sonnet call + song_skills + skill_nodes writes) is added in 02-03.
    Returns SkillGraphResponse(nodes=[]) — empty until 02-03 lands.

    Idempotent: ON CONFLICT DO UPDATE means repeated calls with the same UUID
    succeed and update onboarded_at (no 500 on duplicates per D-04).
    """
    stmt = pg_insert(User).values(
        id=body.user_id,
        preferences=body.preferences.model_dump(),
        raw_onboarding_text=body.raw_input,
        onboarded_at=datetime.now(timezone.utc),
    ).on_conflict_do_update(
        index_elements=["id"],
        set_={
            "preferences": body.preferences.model_dump(),
            "raw_onboarding_text": body.raw_input,
            "onboarded_at": datetime.now(timezone.utc),
        },
    )
    await db.execute(stmt)
    await db.commit()
    return SkillGraphResponse(nodes=[])


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
