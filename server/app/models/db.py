# server/app/models/db.py
# SQLAlchemy ORM models.
# Phase 1: Song table with JSONB breakdown column.
# Phase 2: User table, Song extended with user_id + category, enums for song category and skill level.
# SkillNode and SongSkill ORM classes are added in 02-03 (they need queries, not just tables).
import enum
import uuid
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# -------------------------------------------------------------------------
# Python enums (exported for use in Pydantic models and migrations)
# -------------------------------------------------------------------------

class SongCategory(str, enum.Enum):
    """Category of a song in the user's library (per D-10)."""
    CAN_PLAY = "can_play"
    WORKING_ON = "working_on"
    ASPIRATIONAL = "aspirational"


class SkillLevel(str, enum.Enum):
    """Depth level in the skill tree (per D-09). Exported for 02-03 ORM additions."""
    ROOT = "root"
    SUB = "sub"
    LEAF = "leaf"


# -------------------------------------------------------------------------
# User ORM model (Phase 2 — POC single-user with multi-user seams)
# -------------------------------------------------------------------------

class User(Base):
    """Users table — device-UUID identity per D-04.

    Phase 2: single user POC. No auth. X-User-ID header carries user_id.
    Phase 4+: cost governor will add per-UUID rate limits at the header-dep boundary.
    """
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    preferences: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="'{}'::jsonb"
    )
    raw_onboarding_text: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    onboarded_at: Mapped[Optional[DateTime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# -------------------------------------------------------------------------
# Song ORM model (Phase 1 + Phase 2 extensions)
# -------------------------------------------------------------------------

class Song(Base):
    __tablename__ = "songs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    artist: Mapped[str] = mapped_column(String(255), nullable=False)
    genre: Mapped[str] = mapped_column(String(100), nullable=True)
    difficulty: Mapped[str] = mapped_column(String(50), nullable=True)
    bpm: Mapped[int] = mapped_column(Integer, nullable=True)
    key: Mapped[str] = mapped_column(String(10), nullable=True)
    breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # Phase 2 additions (nullable per D-14 step 1 — seed row backfilled below)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    category: Mapped[Optional[str]] = mapped_column(
        SAEnum(SongCategory, name="song_category", values_callable=lambda x: [e.value for e in x]),
        nullable=True,
    )
