# server/app/models/song.py
# Pydantic v2 models — D-03 compliant semantic music JSON.
# These are the single source of truth for the API contract (D-02).
# Mobile TypeScript types are generated from the FastAPI OpenAPI schema.
from typing import Optional
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

    model_config = {"from_attributes": True}
