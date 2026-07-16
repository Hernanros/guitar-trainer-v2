# server/app/models/db.py
# SQLAlchemy ORM model for the songs table.
# The breakdown column is stored as JSONB — see Alembic migration 0001.
from sqlalchemy import Integer, String, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


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
