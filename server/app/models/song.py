# server/app/models/song.py
# Pydantic v2 models — D-03 compliant semantic music JSON.
# These are the single source of truth for the API contract (D-02).
# Mobile TypeScript types are generated from the FastAPI OpenAPI schema.
#
# Phase 2: SongResponse extended with optional category + user_id (D-14).
# category is Optional to accommodate system-user seed row (which has can_play backfilled).
# user_id is Optional per D-14 step 1; Phase 3 will tighten.
#
# Phase 3 (03-01): TodaySongResponse added — composes SongResponse + selector metadata.
# TodaySongResponse.rated is Optional[TodayRatingInfo] = None in this slice (Slice A).
# Slice C will patch it via qc.setQueryData.
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


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
    """Top-level API response for GET /api/v1/song-of-day."""
    id: int
    title: str
    artist: str
    genre: str
    difficulty: str   # "intermediate" | "advanced"
    bpm: int
    key: str
    breakdown: Breakdown
    # Phase 2 additions (Optional per D-14 — backfilled on existing row, nullable for new)
    user_id: Optional[UUID] = None
    category: Optional[Literal["can_play", "working_on", "aspirational"]] = None

    model_config = {"from_attributes": True}


# Phase 3 — bank source discriminator (used in TodaySongResponse + mobile FromTheBankTag chip)
BankSource = Literal["user_bench", "seed_catalog"]


class TodayRatingInfo(BaseModel):
    """Rating info for today's session — populated by Slice C (POST /api/v1/sessions).

    Slice A ships this field as Optional[TodayRatingInfo] = None.
    Slice C patches it via qc.setQueryData after rating submission.
    """
    rating: Literal["not_my_tempo", "getting_closer", "thats_what_im_looking_for"]
    rated_at: str  # ISO timestamp string


class TodaySongResponse(BaseModel):
    """GET /api/v1/song-of-day response — composes SongResponse + selector metadata.

    Selector metadata lets the mobile client:
    - Show the correct Fletcher line variant (UI-SPEC §1) based on from_bank + bank_source
    - Render FromTheBankTag chip when from_bank=True (UI-SPEC §2)
    - Disable/hide the re-roll button when rerolled=True
    - Show breakdown CTA immediately if breakdown_available=True (cache hit)

    rated: Optional[TodayRatingInfo] — Slice A leaves this None.
    Slice C's POST /api/v1/sessions writes the session, then the mobile client updates
    the cache via qc.setQueryData with the rated field populated.

    model_config from_attributes=False — constructed by hand from selector + song row,
    not ORM-coerced directly.
    """
    song: SongResponse
    breakdown_available: bool         # True if songs.breakdown_generated_at IS NOT NULL
    from_bank: bool                   # True if 25% override / empty working_on / reroll fired
    bank_source: Optional[BankSource] = None  # None when from_bank=False; chip variant when True
    rerolled: bool                    # True if the user has already used today's reroll
    rerolls_left: int = 1             # 0 or 1 — Slice A: 1 initially, 0 after reroll
    rated: Optional[TodayRatingInfo] = None  # Revision A: Slice C patches this via setQueryData

    model_config = {"from_attributes": False}
