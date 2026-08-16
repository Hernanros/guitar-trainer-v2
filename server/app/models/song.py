# server/app/models/song.py
# Pydantic v2 models — D-03 compliant semantic music JSON.
# These are the single source of truth for the API contract (D-02).
# Mobile TypeScript types are generated from the FastAPI OpenAPI schema.
#
# Phase 2: SongResponse extended with optional category + user_id (D-14).
# category is Optional to accommodate system-user seed row (which has can_play backfilled).
# user_id is Optional per D-14 step 1; Phase 3 will tighten.
# Phase 3: TodayRatingInfo + TodaySongResponse (Slice A payload shape; rated field added in Slice C).
# Phase 3 gap-closure (03-04): TodaySongResponse gains rerolls_left: int (0 or 1 per D-05).
# Phase 4 hotfix (2026-08-16): SongResponse.genre/difficulty/bpm/key/breakdown loosened to
#   Optional per .planning/debug/song-of-day-nullable-metadata.md. Backstop for any
#   song-insert path that skips Sonnet (future admin tools, migrations, seed catalog).
#   Primary correctness lives at insert time: Sonnet now emits full metadata during
#   onboarding, so happy-path rows are always populated. The Optional shape only
#   activates when a legacy row (pre-hotfix) or non-Sonnet path leaves a column NULL —
#   in which case the endpoint no longer 500s; it just serves the row with missing
#   fields and breakdown_available=False.
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


class Note(BaseModel):
    """A single picked note on the guitar."""
    string: int     # 1 = high e ... 6 = low E
    fret: int       # 0 = open
    duration: str   # "quarter" | "eighth" | "half" | "whole" | "sixteenth"


class Beat(BaseModel):
    """A rhythmic beat holding one or more simultaneous notes (chord within a beat)."""
    notes: list[Note]


class Measure(BaseModel):
    """A musical measure with a list of beats."""
    beats: list[Beat]
    time_signature: str = "4/4"  # e.g. "4/4", "3/4", "6/8"


class Tab(BaseModel):
    """Full tab notation as a sequence of measures."""
    measures: list[Measure]
    tuning: list[str] = ["E", "A", "D", "G", "B", "e"]  # standard low-to-high


class ChordPosition(BaseModel):
    """Finger placement for a single string in a chord diagram."""
    string: int             # 1 = high e ... 6 = low E
    fret: int               # 0 = open, -1 = muted
    finger: Optional[int] = None  # 1=index, 2=middle, 3=ring, 4=pinky; None=open/muted


class Chord(BaseModel):
    """A chord diagram with all string positions."""
    name: str
    positions: list[ChordPosition]
    barre_fret: Optional[int] = None   # fret number for full/partial barre
    base_fret: int = 1                 # starting fret of diagram window (needed for SVG y-offset)


class TechniqueNote(BaseModel):
    """A technique tip or instructional callout."""
    heading: str
    body: str


class Breakdown(BaseModel):
    """Full Phase 3-ready song breakdown (D-01).

    Phase 3 will add: practice_loops, difficulty_tags — additive fields, no breaking change.
    """
    tab: Tab
    chords: list[Chord]
    technique_notes: list[TechniqueNote]


class SongResponse(BaseModel):
    """Top-level API response for GET /api/v1/song-of-day.

    Invariants (always required): id, title, artist. Everything else is Optional.

    Metadata (genre/difficulty/bpm/key) and breakdown are Optional as a defense-in-depth
    backstop for any song-insert path that doesn't emit them. Phase 4 hotfix 2026-08-16:
    Sonnet now emits metadata at onboarding so user-onboarded songs land fully populated.
    breakdown is Optional because rows land with a placeholder JSONB until the Phase 3
    breakdown selector fills it in on first user request. Callers should consult
    TodaySongResponse.breakdown_available (server-authoritative signal per
    songs.breakdown_generated_at) rather than probing SongResponse.breakdown for None.

    Mobile clients should treat all Optional fields as nullable; the current EAS build
    has strict interpolation but the happy path (Sonnet-populated) will never surface
    None values for genre/difficulty/bpm/key. Deferred mobile graceful-degradation is
    tracked in memory/project_eas_batch_phase3_and_4.md.
    """
    id: int
    title: str
    artist: str
    # ---- Metadata (Phase 4 hotfix 2026-08-16 — loosened from required to Optional) ----
    genre: Optional[str] = None
    difficulty: Optional[str] = None   # "beginner" | "intermediate" | "advanced"
    bpm: Optional[int] = None
    key: Optional[str] = None
    breakdown: Optional[Breakdown] = None
    # Phase 2 additions (Optional per D-14 — backfilled on existing row, nullable for new)
    user_id: Optional[UUID] = None
    category: Optional[Literal["can_play", "working_on", "aspirational"]] = None

    @field_validator("category", mode="before")
    @classmethod
    def coerce_category_enum(cls, v):
        """Coerce SongCategory enum to its .value string (mirrors SkillNodeResponse pattern).

        SQLAlchemy ORM returns the Python enum instance from SAEnum columns on read-back.
        The Literal constraint requires a plain string. Extract .value to satisfy both.
        """
        if hasattr(v, "value"):
            return v.value
        return v

    @field_validator("breakdown", mode="before")
    @classmethod
    def coerce_placeholder_breakdown(cls, v):
        """Coerce placeholder JSONB dicts to None so Breakdown validation is skipped.

        Rows inserted during onboarding land with `breakdown = {"placeholder": "..."}`
        as a marker that Phase 3 will fill in the real breakdown on first request.
        The placeholder is a valid dict for the JSONB column but not a valid Breakdown
        (missing tab/chords/technique_notes). Coercing to None lets Optional[Breakdown]
        validation pass and defers the "is there a real breakdown?" question to the
        server-authoritative TodaySongResponse.breakdown_available flag.

        Real breakdowns are dicts with tab/chords/technique_notes keys — those pass
        through unchanged. None passes through unchanged (already-Optional path).
        """
        if isinstance(v, dict) and "placeholder" in v and "tab" not in v:
            return None
        return v

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Phase 3 — Today song payload with rating signal (Slice A + Slice C addition)
# ---------------------------------------------------------------------------

class TodayRatingInfo(BaseModel):
    """Rating info surfaced on TodaySongResponse.rated when the user has rated today's song.

    Populated in Slice C (POST /api/v1/sessions) and read back via GET /api/v1/song-of-day.
    The `rated` field is null when the user has not yet rated today's song.
    """
    rating: Literal["not_my_tempo", "getting_closer", "thats_what_im_looking_for"]
    rated_at: str  # ISO datetime string

    model_config = {"from_attributes": False}


class BreakdownQuota(BaseModel):
    """Phase 4 quota snapshot embedded in TodaySongResponse (D-06).

    remaining: calls left in the current 7-day rolling window (0..cap).
    cap: per-user cap (always 3 for the breakdown feature per D-01).
    resets_at: ISO datetime string — oldest_call_in_window + 7 days, server-authoritative.
    """
    remaining: int
    cap: int
    resets_at: str  # ISO datetime string

    model_config = {"from_attributes": False}


class TodaySongResponse(BaseModel):
    """Response shape for GET /api/v1/song-of-day (Phase 3 per-user selector).

    song: the selected song.
    breakdown_available: True if songs.breakdown_generated_at is not None (cache-forever per D-11).
    from_bank: True when the 25% random override / empty-working_on path fired.
    bank_source: "user_bench" if from user's own non-working_on songs; "seed_catalog" if from song_catalog.
    rerolled: True if the user used their one daily re-roll.
    rated: populated with TodayRatingInfo when the user has rated today's song; null otherwise.
    rerolls_left: 0 if the user has already rerolled today, 1 otherwise (D-05 one-per-day).
    breakdown_quota: Phase 4 — always populated; carries remaining/cap/resets_at for the quota chip (D-06).
    """
    song: SongResponse
    breakdown_available: bool
    from_bank: bool
    bank_source: Optional[Literal["user_bench", "seed_catalog"]] = None
    rerolled: bool
    rated: Optional[TodayRatingInfo] = None
    rerolls_left: int  # 0 or 1 — always populated (never null)
    breakdown_quota: BreakdownQuota  # Phase 4 addition — always populated

    model_config = {"from_attributes": False}
