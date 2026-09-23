# server/app/models/db.py
# SQLAlchemy ORM models.
# Phase 1: Song table with JSONB breakdown column.
# Phase 2: User table, Song extended with user_id + category, enums for song category and skill level.
# Phase 2 (02-03): SkillNode + SongSkill ORM classes added (tables already exist per migration 0002).
# Phase 3 (03-01): SongCatalog + UserSession ORM classes, RatingLevel + PrimarySkillRoot enums,
#                  Song.breakdown_generated_at column.
# Phase 4 (04-01): GovernorCall, SkillNodeProposal, SkillNodeRejection, DecayRun ORM classes;
#                  SkillNode gains canonical_node_id + last_decayed_at (migration 0004).
# FLE-8 Task 3: Drill, DrillAttempt, DrillDedupeQueue ORM classes (migration 0006) — drills
#               become first-class so a drill survives the song that spawned it.
# FLE-9 Task 5: PracticeSession, PracticeSessionItem, DrillProgress ORM classes (migration
#               0009) — the session object; DrillAttempt gains FLE-4 §13's full record.
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Boolean, Date, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String, Text, func, text
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


class RatingLevel(str, enum.Enum):
    """Phase 3 session rating — D-06 Fletcher-voiced 3-tier."""
    NOT_MY_TEMPO = "not_my_tempo"
    GETTING_CLOSER = "getting_closer"
    THATS_WHAT_IM_LOOKING_FOR = "thats_what_im_looking_for"


class PrimarySkillRoot(str, enum.Enum):
    """Root taxonomy for song_catalog primary skill (D-08 fixed roots)."""
    RHYTHM = "rhythm"
    LEAD = "lead"
    CHORD_VOICINGS = "chord_voicings"
    FINGERSTYLE = "fingerstyle"
    MUSIC_THEORY = "music_theory"
    TIMING = "timing"


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
    # Phase 3 addition: cache signal for breakdown — if not None, serve from JSONB without Sonnet call.
    breakdown_generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    # Phase 4 additions (migration 0004)
    canonical_node_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("skill_nodes.id"),
        nullable=True,
    )
    last_decayed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# -------------------------------------------------------------------------
# SongSkill ORM model (Phase 2 — junction between songs and skill_nodes)
# -------------------------------------------------------------------------

class SongSkill(Base):
    """Junction table mapping songs to their required skill nodes (per D-10).

    Composite primary key: (song_id, skill_node_id).
    weight: relative importance of the skill for the song (default 1.0).
    Used in Phase 3's per-user song-of-day selector and mastery queries.
    D-07: weight column exists in schema but is NOT USED — all skills weighted equally.
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


# -------------------------------------------------------------------------
# SongCatalog ORM model (Phase 3 — seed songs global to all users)
# -------------------------------------------------------------------------

class SongCatalog(Base):
    """Global seed song catalog — 64 hand-curated songs shipped with Fletcher.

    Provides a bank for new users with empty working_on and for the 25% random override.
    difficulty: [0, 1] on the same scale as skill_nodes.mastery for direct comparison.

    Migration 0003 seeded the first 10; migration 0007 added `tuning` and expanded
    the catalog to 64 across genre, difficulty and tuning (FLE-6 Task 8). The tagging
    contract for difficulty / primary_skill_root / tuning is documented at the top of
    0007_song_catalog_tuning_and_expansion.py — read it before adding a row, because
    the session generator reads these fields and a sloppy tag becomes a bad session.
    The spread is tabulated in
    .planning/quick/260915-01-expand-song-catalog/260915-01-CATALOG.md.
    """
    __tablename__ = "song_catalog"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    artist: Mapped[str] = mapped_column(String(255), nullable=False)
    genre: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    primary_skill_root: Mapped[str] = mapped_column(
        SAEnum(
            PrimarySkillRoot,
            name="primary_skill_root",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,  # created by migration 0003 raw SQL
        ),
        nullable=False,
    )
    difficulty: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False
    )
    # Canonical recorded tuning (migration 0007). One of ALLOWED_TUNINGS in that
    # migration, enforced DB-side by song_catalog_tuning_check. Dual default
    # mirrors the SongCatalog.difficulty pattern: `default` covers ORM inserts,
    # `server_default` covers raw-SQL inserts, so the column is never None on read.
    tuning: Mapped[str] = mapped_column(
        String(32), nullable=False, default="standard", server_default=text("'standard'")
    )
    breakdown: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# -------------------------------------------------------------------------
# UserSession ORM model (Phase 3 — session rating log)
# -------------------------------------------------------------------------

class UserSession(Base):
    """User session table — one row per practice session rating.

    is_reroll_marker=True rows record daily re-roll events (rating=NULL).
    is_reroll_marker=False rows record real ratings (rating not NULL).

    Partial-unique indexes:
      - uq_user_sessions_daily_reroll (from migration 0003):
          (user_id, local_calendar_day) WHERE is_reroll_marker=true
          (one re-roll per day)
      - uq_user_sessions_daily_rating (RECREATED in migration 0005):
          (user_id, song_id, local_calendar_day, COALESCE(drill_index, -1))
          WHERE is_reroll_marker=false
          (one rating per (song, drill_slot) per day — COALESCE(-1) treats a
          whole-song rating as its own slot alongside drill_index=0..N; allows
          reroll marker + rating to coexist for same song+day)

    Phase 4.1 (migration 0005) added drill_index + target_skill_node_id nullable
    columns for per-drill rating write path. Both-or-neither is enforced by the
    ck_drill_index_pairs_skill_node CHECK constraint on the table AND by the
    @model_validator on SessionCreate at the API boundary (defense in depth).
    """
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    song_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("songs.id"), nullable=True  # nullable for reroll markers with no song_id
    )
    rating: Mapped[Optional[str]] = mapped_column(
        SAEnum(
            RatingLevel,
            name="rating_level",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,  # created by migration 0003 raw SQL
        ),
        nullable=True,  # NULL for reroll markers
    )
    local_calendar_day: Mapped[date] = mapped_column(Date, nullable=False)
    tz_offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Phase 4.1 (migration 0005): drill rating columns.
    # Both are nullable and the DB enforces both-or-neither via CHECK constraint
    # ck_drill_index_pairs_skill_node. NULL = whole-song rating (existing behavior).
    drill_index: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        doc="Drill rating: 0-based index into Breakdown.drills. NULL = whole-song rating.",
    )
    target_skill_node_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("skill_nodes.id"),
        nullable=True,
        doc=(
            "The single skill_node this drill rating writes to. NULL when drill_index is NULL. "
            "CHECK constraint ck_drill_index_pairs_skill_node enforces both-or-neither at the DB."
        ),
    )
    is_reroll_marker: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # migration 0003: added as raw SQL column; not in original ORM definition.
    bank_source: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True
    )
    # migration 0008 (FLE-54): persists the first non-reroll daily pick so the
    # seeded-random CTE is only called once per (user, day).
    is_daily_pick_marker: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


# -------------------------------------------------------------------------
# GovernorCall ORM model (Phase 4 — cost governor audit ledger)
# -------------------------------------------------------------------------

class GovernorCall(Base):
    """Governor calls table — one row per Sonnet call attempt, append-only ledger.

    prompt_tokens_estimated: populated pre-dispatch via count_tokens API (D-03).
    prompt_tokens_actual/output_tokens_actual: populated post-dispatch from resp.usage.
    dollars_estimated/dollars_actual: computed from token counts (optional, nullable).
    error_code: type(exc).__name__ on failure; NULL on success.
    created_at: row inserted BEFORE Sonnet dispatch (cap-check precedes insert).
    """
    __tablename__ = "governor_calls"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens_estimated: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prompt_tokens_actual: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens_actual: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    dollars_estimated: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)
    dollars_actual: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# -------------------------------------------------------------------------
# SkillNodeProposal ORM model (Phase 4 — node verification pipeline input)
# -------------------------------------------------------------------------

class SkillNodeProposal(Base):
    """Skill node proposal — one row per node proposed by onboarding-Sonnet.

    Flows through: dedupe_score → verifier → status ('pending'|'approved'|'rejected'|'merged').
    canonical_id: set when proposal is matched to an existing canonical node (D-12).
    verifier_verdict/verifier_reason: from run_skill_node_verify response (Slice C).
    """
    __tablename__ = "skill_node_proposals"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    proposed_name: Mapped[str] = mapped_column(String(255), nullable=False)
    fuzzy_score: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    canonical_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_nodes.id"), nullable=True
    )
    verifier_verdict: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    verifier_reason: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# -------------------------------------------------------------------------
# SkillNodeRejection ORM model (Phase 4 — node verification pipeline rejections)
# -------------------------------------------------------------------------

class SkillNodeRejection(Base):
    """Rejected skill node proposal — D-14 graceful drop + audit record.

    No FK to users — rejections are global audit records (proposed_name is the key).
    verifier_response: full JSON from run_skill_node_verify (nullable — may be absent
    if rejected at fuzzy-score stage before verifier ran).
    """
    __tablename__ = "skill_node_rejections"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    proposed_name: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String(1024), nullable=False)
    verifier_response: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# -------------------------------------------------------------------------
# DecayRun ORM model (Phase 4 — nightly mastery decay job audit)
# -------------------------------------------------------------------------

class DecayRun(Base):
    """Decay run — one row per nightly APScheduler decay job execution (D-Claude-decay).

    finished_at: NULL until job completes (or fails).
    nodes_affected: count of skill_nodes rows updated by the decay UPDATE.
    error: exception message if the job failed; NULL on success.
    """
    __tablename__ = "decay_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    nodes_affected: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)


# -------------------------------------------------------------------------
# Drill bank ORM models (FLE-8 Task 3 — migration 0006)
#
# Before 0006 a drill's identity was (song_id, drill_index) into one song's
# cached breakdown JSONB. These three tables make a drill first-class so it can
# be handed to the user tomorrow when the song is different.
#
# Naming note: `app.models.song.Drill` is the PYDANTIC wire/LLM shape emitted
# inside a breakdown. `db.Drill` below is the PERSISTED bank row. They are
# deliberately separate — import as `db.Drill` vs `song.Drill` at call sites.
# -------------------------------------------------------------------------

class Drill(Base):
    """A banked practice drill — persists independently of the song that spawned it.

    Dedup (FLE-8: "duplicates collapse on write") is scoped to
    (user_id, skill_node_id): two similarly-named drills targeting DIFFERENT
    skills are not duplicates. Classification runs rapidfuzz only, via
    app.ai.drill_dedupe — no LLM in the write path, per the FLE-8 constraint.

      >= 85  reuse the existing canonical, write no new row
      70-84  write a DrillDedupeQueue row, write no drill row
      < 70   insert a new canonical

    canonical_drill_id NULL means THIS row is canonical. The partial-unique index
    uq_drills_canonical_identity (user_id, skill_node_id, name_normalized)
    WHERE canonical_drill_id IS NULL is the DB-level backstop for
    exact-after-normalization collisions; rapidfuzz covers the fuzzy band above it.

    origin_song_id / origin_drill_index are ADVISORY PROVENANCE, not identity.
    origin_song_id is ON DELETE SET NULL so the drill outlives its song. There is
    intentionally no both-or-neither CHECK on the pair — SET NULL cannot null
    origin_drill_index, so such a constraint would make song deletion fail. Once
    origin_song_id is nulled, origin_drill_index is retained but meaningless.
    """
    __tablename__ = "drills"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_normalized: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="app.ai.skill_dedupe.normalize(name). Dedup key component and DB collision backstop.",
    )
    skill_node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("skill_nodes.id", ondelete="CASCADE"),
        nullable=False,
        doc="The single skill this drill exercises. Dedup scope.",
    )
    canonical_drill_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("drills.id", ondelete="CASCADE"),
        nullable=True,
        doc="NULL = this row IS canonical. Set = collapsed duplicate.",
    )
    dedupe_score: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, doc="rapidfuzz token_set_ratio vs canonical at write time."
    )
    # Drill content — mirrors app.models.song.Drill field-for-field.
    song_specific: Mapped[bool] = mapped_column(Boolean, nullable=False)
    what: Mapped[str] = mapped_column(Text, nullable=False)
    tab_snippet: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    start_bpm: Mapped[int] = mapped_column(Integer, nullable=False)
    target_bpm: Mapped[int] = mapped_column(Integer, nullable=False)
    repetitions: Mapped[int] = mapped_column(Integer, nullable=False)
    success_criterion: Mapped[str] = mapped_column(Text, nullable=False)
    common_trap: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Provenance — nullable by design.
    origin_song_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("songs.id", ondelete="SET NULL"), nullable=True
    )
    origin_drill_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DrillAttempt(Base):
    """One recorded attempt at a banked drill — the per-drill history FLE-8 asks for.

    Records what ACTUALLY happened: tempo reached (not the drill's target_bpm),
    reps completed, when. A user_sessions row records a 3-choice verdict and
    nothing about the attempt; this is the substrate v2 feedback needs.

    Append-only, and deliberately WITHOUT a one-per-day unique index. Contrast
    uq_user_sessions_daily_rating, which enforces one verdict per slot per day:
    three attempts at three tempos in one sitting is real data, not a conflict.

    user_session_id is ON DELETE SET NULL — attempt history outlives the rating
    row it came from, and attempts logged outside a rated session have it NULL.
    """
    __tablename__ = "drill_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    drill_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("drills.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_sessions.id", ondelete="SET NULL"), nullable=True
    )
    local_calendar_day: Mapped[date] = mapped_column(
        Date, nullable=False, doc="Client-local day, same convention as user_sessions."
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    tempo_reached_bpm: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, doc="Tempo ACTUALLY reached. CHECK: 20-400 when set."
    )
    reps_completed: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, doc="CHECK: >= 0 when set."
    )
    rating: Mapped[Optional[str]] = mapped_column(
        SAEnum(
            RatingLevel,
            name="rating_level",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,  # created by migration 0003 raw SQL
        ),
        nullable=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- FLE-9 (migration 0009): the rest of FLE-4 §13's attempt record. -------
    # 0006 shipped the stub — a verdict and the tempo reached. What was missing is
    # everything needed to AUDIT a ladder transition after the fact. All nullable:
    # rows written before the session engine existed have no honest value, and a NULL
    # outcome meaning "predates the classifier" is exactly right.
    #
    # Forward reference by string: PracticeSessionItem is defined below this class.
    session_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("practice_session_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    rung_bpm: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, doc="drill_progress.rung_bpm at attempt time — the tempo of record."
    )
    planned_bpm: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        doc="The tempo the ITEM asked for. Differs from rung_bpm on §7.3 re-entry, and "
            "the gap between asked-for and held is what v2 stall detection reads.",
    )
    planned_reps: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Annotated str, not the enum: DrillAttemptOutcome is declared below this class and
    # SQLAlchemy cannot resolve a forward reference inside Mapped[]. Same shape as
    # `rating` above, which is str-annotated for the same reason.
    outcome: Mapped[Optional[str]] = mapped_column(
        SAEnum(
            "CLEAR", "HOLD", "MISS", "SKIP",
            name="drill_attempt_outcome",
            create_type=False,
        ),
        nullable=True,
        doc="§7.1, classified server-side and STORED. Recomputing it later from "
            "(rating, reps) would rewrite history every time the rubric is retuned.",
    )
    re_entry: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    duration_s: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    target_skill_node_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("skill_nodes.id", ondelete="SET NULL"),
        nullable=True,
        doc="The node this rating moves mastery on. Nadia's review finding #1 — §13 had "
            "no mastery write path for bank drills, and `deficit` is 100 of the ~180 "
            "points in the §11.2 selection score.",
    )


class DrillDedupeQueue(Base):
    """The 70-84 uncertain dedup band — curator queue. Mirrors SkillNodeProposal.

    A row here means "rapidfuzz was not confident, so a human or an async job
    decides" — and NO drill row was written. This is the async escape hatch that
    lets the write path stay LLM-free (FLE-8 constraint) while still extending the
    Phase 4 machinery rather than building a parallel mechanism.

    payload holds the full proposed drill so approval can insert it without
    regenerating — no Sonnet call on the resolve path either.
    """
    __tablename__ = "drill_dedupe_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    proposed_name: Mapped[str] = mapped_column(String(255), nullable=False)
    skill_node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_nodes.id", ondelete="CASCADE"), nullable=False
    )
    candidate_drill_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("drills.id", ondelete="CASCADE"), nullable=False
    )
    fuzzy_score: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# -------------------------------------------------------------------------
# Session engine ORM models (FLE-9 Task 5 — migration 0009)
#
# The union of FLE-4 §13 (`sessions` / `session_items`) and the FLE-21 "Pilot
# Session Telemetry" contract revision 2, which renamed them. Per FLE-21 R0 these
# are ONE pair of tables carrying both column sets, not a pedagogy pair plus a
# telemetry pair.
#
# Naming note: `PracticeSession` is the session LIFECYCLE row. `UserSession` above
# is the per-RATING daily log and is a different object with a confusingly similar
# name — the prefix is not decoration. The Pydantic wire models are likewise
# `PracticeSessionResponse` / `PracticeSessionItemResponse`, never `Session*`:
# openapi-typescript keys components['schemas'] by Pydantic class name and the
# mobile client already binds the existing `SessionResponse` at
# mobile/src/api/sessions.ts:26, so a collision breaks useSubmitRating at the next
# `npm run codegen`, in a file nobody touched.
# -------------------------------------------------------------------------

class SessionMode(str, enum.Enum):
    """FLE-4 §4 — the technique-vs-repertoire balance for one session."""
    BUILD = "BUILD"
    BALANCED = "BALANCED"
    PERFORM = "PERFORM"


class SessionState(str, enum.Enum):
    """FLE-21 §1. `completed` (not FLE-4 §13's `complete`) for consistency with items."""
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class SessionTerminalReason(str, enum.Enum):
    """FLE-21 §3. No `superseded` — R7 deleted it; supersession can no longer occur."""
    USER_COMPLETED = "user_completed"
    DAY_ROLLED = "day_rolled"
    TIMEOUT = "timeout"


class SessionItemBlock(str, enum.Enum):
    """FLE-4 §2 — the four phases, in fixed order. The generator never shuffles them."""
    WARMUP = "warmup"
    TECHNIQUE = "technique"
    REPERTOIRE = "repertoire"
    CONSOLIDATION = "consolidation"


class SessionItemKind(str, enum.Enum):
    """FLE-4 §13."""
    DRILL = "drill"
    SONG_SECTION = "song_section"
    SONG_PLAY = "song_play"


class SessionItemState(str, enum.Enum):
    """FLE-21 §2 — a monotonic lattice: not_reached -> in_progress -> {completed, skipped}.

    `not_reached` rather than FLE-4 §13's `pending` because the distinction is the
    whole point: `skipped` means the user actively passed, `not_reached` means the
    session ended before here. `pending` blurs them.
    """
    NOT_REACHED = "not_reached"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class AdvanceMode(str, enum.Enum):
    """FLE-21 §5.1 — how the player left an item. Separates the two readings in §4.1."""
    USER_TAP = "user_tap"
    AUTO = "auto"


class DrillProgressState(str, enum.Enum):
    """FLE-4 §7.5."""
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    MASTERED = "mastered"
    RETIRED = "retired"


class DrillAttemptOutcome(str, enum.Enum):
    """FLE-4 §7.1 — classified server-side and stored so the classifier is auditable."""
    CLEAR = "CLEAR"
    HOLD = "HOLD"
    MISS = "MISS"
    SKIP = "SKIP"


def _pg_enum(py_enum: type[enum.Enum], name: str) -> SAEnum:
    """SAEnum bound to a type migration 0009 created. Never re-creates the type."""
    return SAEnum(
        py_enum,
        name=name,
        values_callable=lambda x: [e.value for e in x],
        create_type=False,
    )


class DrillProgress(Base):
    """FLE-4 §7 — the tempo ladder, one row per (user, drill).

    The rule in one sentence, which is deliberate because a tempo system a player
    doesn't understand feels arbitrary and arbitrary feels like homework: nail it
    twice and you move up, miss it twice and you drop back.

    §7.6 — the nightly 5% decay job in app/scheduler.py moves skill_nodes.mastery and
    must NOT touch rung_bpm. Mastery is the graph's ESTIMATE of a skill and estimates
    should get less confident with time; a rung is a RECORD of a tempo the player
    actually held, twice. Layoff re-entry (§7.3) is the honest mechanism for time
    passing — it plans you lower without rewriting the record.
    """
    __tablename__ = "drill_progress"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    drill_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("drills.id", ondelete="CASCADE"), nullable=False
    )
    rung_bpm: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="init = drill.start_bpm; always a multiple of 5."
    )
    consecutive_clears: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    consecutive_misses: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_practiced_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    state: Mapped[DrillProgressState] = mapped_column(
        _pg_enum(DrillProgressState, "drill_progress_state"),
        nullable=False,
        default=DrillProgressState.ACTIVE,
        server_default=text("'active'::drill_progress_state"),
    )
    mastered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="§7.5 — arms the 14-day maintenance timer."
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PracticeSession(Base):
    """One planned practice session — the object the app did not have.

    `generated_at` and `started_at` are deliberately separate (FLE-21 §1). A session
    generated and never opened is NOT an abandoned session; it is an ignored plan,
    which is a different and also interesting number. Only rows with
    `started_at IS NOT NULL` enter the completion-rate denominator.

    Abandonment is the DAY ROLL (FLE-4 §6, adopted verbatim by FLE-21 R3) — not a
    fixed window. A started session is abandoned once the user's local day has rolled
    past `local_calendar_day`. The `timeout` reason exists only as protection against
    a corrupt `tz_offset_minutes`; if it fires during the pilot that is a bug report,
    not a metric.
    """
    __tablename__ = "practice_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    local_calendar_day: Mapped[date] = mapped_column(
        Date, nullable=False, doc="Server-computed from tz_offset_minutes per D-10."
    )
    tz_offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    song_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("songs.id", ondelete="SET NULL"),
        nullable=True,
        doc="Inherited from the day's selector (FLE-4 §5.3). The session never re-rolls.",
    )
    target_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="From UserPreferences.session_length_min. 15|30|45|60."
    )
    mode: Mapped[SessionMode] = mapped_column(
        _pg_enum(SessionMode, "session_mode"), nullable=False
    )
    allow_push: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        doc="§7.4 — when false NO drill may push a rung, regardless of clears. Clears "
            "still accumulate, so the push lands next session.",
    )
    generator_version: Mapped[str] = mapped_column(Text, nullable=False)
    seed: Mapped[str] = mapped_column(Text, nullable=False)
    mode_rule: Mapped[str] = mapped_column(
        String(32), nullable=False, doc="§4 first-match-wins rule name — why this mode."
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="First item entered. WRITE-ONCE (FLE-21 R5)."
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[SessionState] = mapped_column(
        _pg_enum(SessionState, "session_state"),
        nullable=False,
        default=SessionState.PLANNED,
        server_default=text("'planned'::session_state"),
    )
    terminal_reason: Mapped[Optional[SessionTerminalReason]] = mapped_column(
        _pg_enum(SessionTerminalReason, "session_terminal_reason"), nullable=True
    )
    completion_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3), nullable=True)
    elapsed_active_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Sum of item active_seconds. CLIENT-REPORTED — corroborate with ended_at - started_at.",
    )
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_item_index_reached: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, doc="GREATEST(existing, index) — monotonic, never decreases."
    )


class PracticeSessionItem(Base):
    """One item in the plan. EVERY item row is written at generation time.

    Rows are never created lazily on first view, and that is the one decision in this
    feature that cannot be retrofitted. FLE-13's readout asks both "where did they
    bail" and "which block do they skip"; those are separable only if an untouched
    item is on the record. If rows appeared only when touched, a skipped block and a
    never-reached block would both simply be absent.

    The boolean flags are server-told and the client derives NONE of them (FLE-10 R5).
    That is what keeps FLE-4's versioning discipline alive after the trip to the
    device, and it is also what lets FLE-21 §4.1's skip readout exclude unskippable
    items from its denominator — `repertoire.item1` is unskippable BY CONSTRUCTION, so
    its zero skip count is a property of the generator, not a player preference.
    """
    __tablename__ = "practice_session_items"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("practice_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_index: Mapped[int] = mapped_column(Integer, nullable=False, doc="0-based plan order.")
    block: Mapped[SessionItemBlock] = mapped_column(
        _pg_enum(SessionItemBlock, "session_item_block"), nullable=False
    )
    kind: Mapped[SessionItemKind] = mapped_column(
        _pg_enum(SessionItemKind, "session_item_kind"), nullable=False
    )
    drill_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("drills.id", ondelete="SET NULL"), nullable=True
    )
    song_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("songs.id", ondelete="SET NULL"), nullable=True
    )
    target_skill_node_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_nodes.id", ondelete="SET NULL"), nullable=True
    )
    planned_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="ADVISORY (§6). Never a hard cut."
    )
    planned_bpm: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    planned_reps: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, doc="The denominator in §7.1's CLEAR test."
    )
    completed_reps: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        doc="NEVER inferred from active_seconds. NULL on a rated drill classifies HOLD.",
    )
    rated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    skippable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    click_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    re_entry: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    repeat_ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_consolidation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    state: Mapped[SessionItemState] = mapped_column(
        _pg_enum(SessionItemState, "session_item_state"),
        nullable=False,
        default=SessionItemState.NOT_REACHED,
        server_default=text("'not_reached'::session_item_state"),
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    active_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="NOT paused on background or screen lock (FLE-21 §6). Clamped to 4x planned.",
    )
    rating: Mapped[Optional[str]] = mapped_column(
        SAEnum(
            RatingLevel,
            name="rating_level",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,
        ),
        nullable=True,
        doc="NULL is a first-class value (FLE-21 R1) — four item classes are unrated by design.",
    )
    advance_mode: Mapped[Optional[AdvanceMode]] = mapped_column(
        _pg_enum(AdvanceMode, "advance_mode"), nullable=True
    )
