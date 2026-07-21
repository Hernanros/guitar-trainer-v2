# server/app/models/db.py
# SQLAlchemy ORM models.
# Phase 1: Song table with JSONB breakdown column.
# Phase 2: User table, Song extended with user_id + category, enums for song category and skill level.
# Phase 2 (02-03): SkillNode + SongSkill ORM classes added (tables already exist per migration 0002).
import enum
import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String, func, text
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


# -------------------------------------------------------------------------
# SkillNode ORM model (Phase 2 — 3-level per-user skill tree per D-09)
# -------------------------------------------------------------------------

class SkillNode(Base):
    """Skill nodes table — per-user 3-level tree (root → sub → leaf).

    parent_id FK is DEFERRABLE INITIALLY DEFERRED per migration 0002:
    PostgreSQL only checks the FK constraint at COMMIT time, so any-order
    insertion within a single transaction is safe. Level-sorted insertion
    is used for readability only — not load-bearing.

    mastery: Numeric(4,3) default 0.0 per deterministic-writes principle (D-11).
    Sonnet writes STRUCTURE (nodes + hierarchy), never mastery values.
    """
    __tablename__ = "skill_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[str] = mapped_column(
        SAEnum(
            SkillLevel,
            name="skill_level",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,  # already created by migration 0002 raw SQL
        ),
        nullable=False,
    )
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("skill_nodes.id", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    tempo_bin_low: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tempo_bin_high: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mastery: Mapped[Decimal] = mapped_column(
        Numeric(4, 3),
        nullable=False,
        default=Decimal("0.0"),          # Python-side default so new rows have mastery=0.0
                                          # BEFORE flush/refresh — response serialization
                                          # needs it non-None on the in-memory object.
        server_default=text("0.0"),      # DB-side default backs it for direct-SQL inserts
                                          # (migrations, seeding, external tools).
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# -------------------------------------------------------------------------
# SongSkill ORM model (Phase 2 — junction between songs and skill_nodes)
# -------------------------------------------------------------------------

class SongSkill(Base):
    """Junction table mapping songs to their required skill nodes (per D-10).

    Composite primary key: (song_id, skill_node_id).
    weight: relative importance of the skill for the song (default 1.0).
    Used in Phase 3's per-user song-of-day selector and mastery queries.
    """
    __tablename__ = "song_skills"

    song_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("songs.id"), primary_key=True
    )
    skill_node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_nodes.id"), primary_key=True
    )
    weight: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, server_default=text("1.0")
    )
