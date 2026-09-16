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
# Phase 4 hotfix (2026-08-17): coerce_placeholder_breakdown now returns an empty-but-
#   valid Breakdown shape instead of None. The current EAS iOS build (690bc876, built
#   2026-08-16 09:21 UTC — BEFORE the 33d78ac coerce-to-None change shipped) still
#   accesses song.breakdown.tab / .chords / .technique_notes without null guards, and
#   returning None crashes the app to the iOS home screen. Empty-Breakdown makes those
#   .map() iterations render nothing without crashing. Once mobile ships graceful
#   degradation keyed on TodaySongResponse.breakdown_available (deferred to the next
#   EAS batch; see .planning/debug/mobile-crash-null-breakdown.md), the coerce target
#   can move back to None — until then empty-Breakdown is the mobile-safe contract.
import logging
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


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


class Drill(BaseModel):
    """A single Fletcher-voiced practice drill (Phase 4.1, Plan 04.1-01).

    Sonnet emits 2-4 of these per breakdown as part of the SAME `emit_breakdown`
    tool call (no new endpoint, no new governor category). Each drill isolates
    ONE skill from the target_skills list. `tab_snippet` is a CANONICAL EXERCISE
    SHAPE composed by Sonnet — NOT a slice of the main song tab (see SYSTEM_PROMPT
    DRILLS block BAD/GOOD example).

    Landmine #3 protection: target_bpm > start_bpm is enforced by @model_validator
    so a lazy Sonnet emit (equal bpms → zero-progress drill) fails at parse time.
    The breakdowns endpoint wraps run_technique_breakdown parsing in a try/except
    that soft-fails to drills=[] on ValidationError — the endpoint NEVER 500s
    from a drills-shape violation.

    target_skill_temp_id validity (is-this-a-real-skill-node-owned-by-the-user)
    is NOT enforced at the Pydantic layer — validation lives at the endpoint
    where the per-request resolved skill list is in scope (Plan 04.1-01 Task 3).
    """
    name: str = Field(
        ...,
        description="Short imperative name, e.g., 'Isolate the b3→3 slide'. Fletcher voice: sharp, diagnostic.",
    )
    target_skill_temp_id: str = Field(
        ...,
        description=(
            "The single skill_temp_id from target_skills this drill exercises. "
            "Exactly one. MUST be one of the ids in the user message."
        ),
    )
    song_specific: bool = Field(
        ...,
        description=(
            "True if `what` copy names this song. False if the drill is a foundational "
            "technique any guitarist could use regardless of song context."
        ),
    )
    what: str = Field(
        ...,
        description=(
            "1-2 sentences explaining what the user does. If song_specific=true, name "
            "the song and the moment."
        ),
    )
    tab_snippet: Tab = Field(
        ...,
        description=(
            "Canonical exercise shape. 1-2 measures max. MUST NOT be a slice of the main song tab."
        ),
    )
    start_bpm: int = Field(
        ...,
        ge=40,
        le=180,
        description="Warmup tempo. Multiple of 5. Between 40 and 180.",
    )
    target_bpm: int = Field(
        ...,
        ge=40,
        le=220,
        description="Stretch tempo. Multiple of 5. Between 10 and 40 BPM higher than start_bpm.",
    )
    repetitions: int = Field(
        ...,
        ge=8,
        le=30,
        description="Reps per tempo step. Between 8 and 30.",
    )
    success_criterion: str = Field(
        ...,
        description="One sentence — what 'unlocked' sounds like. Fletcher voice.",
    )
    common_trap: Optional[str] = Field(
        None,
        description="Optional. One sentence about the mistake beginners make on this mechanic.",
    )

    @model_validator(mode="after")
    def _target_bpm_above_start(self) -> "Drill":
        """W2 fix: target_bpm must be strictly greater than start_bpm.

        Any positive delta validates — the 10-40 BPM range is SYSTEM_PROMPT guidance,
        not Pydantic-enforced (Sonnet-side quality gate, not a hard schema constraint).
        """
        if self.target_bpm <= self.start_bpm:
            raise ValueError(
                f"target_bpm ({self.target_bpm}) must be strictly greater than "
                f"start_bpm ({self.start_bpm})"
            )
        return self


class Breakdown(BaseModel):
    """Full Phase 3-ready song breakdown (D-01).

    Phase 3 will add: practice_loops, difficulty_tags — additive fields, no breaking change.

    Phase 4.1 (Plan 04.1-01): `drills` field added — Sonnet emits 2-4 practice drills
    alongside tab/chords/technique_notes in the SAME tool call. `default_factory=list`
    means pre-4.1 cached breakdowns (where `drills` is absent from the JSONB) read back
    as `drills=[]` without triggering min_length=2 — that check only fires when a caller
    (i.e., Sonnet output parse) EXPLICITLY provides a drills list.
    """
    tab: Tab
    chords: list[Chord]
    technique_notes: list[TechniqueNote]
    # Phase 4.1: drills. min_length/max_length only apply on explicit input (Sonnet
    # output path). Default-factory-produced [] bypasses these checks — that's the
    # intended backward-compat behavior for pre-4.1 cached rows.
    drills: list[Drill] = Field(default_factory=list, min_length=2, max_length=4)


# ---------------------------------------------------------------------------
# song_specific enforcement (FLE-44)
# ---------------------------------------------------------------------------
# The FLE-17 §3 eval re-run (2026-09-16) showed the prompt cannot be trusted to
# set this flag. The L4 rule was restated as mechanically as English allows
# ("if `what` contains the song title or the artist name anywhere, then
# song_specific MUST be true. There is no exception") and Sonnet still emitted
# Kashmir D3 "the engine of the Kashmir groove" and D4 "the core of the Kashmir
# riff" with song_specific=false. It is not misreading an ambiguous instruction;
# it is overriding a clear one with its own semantics, because those drills
# genuinely ARE generic mechanics that merely name the song. Restating the rule
# a third time would not fix it — see
# .planning/phases/04.1-ai-drills/04.1-07-EVAL-RERUN-RESULTS.md §5.
#
# So the guarantee lives here instead: a deterministic pass over the parsed
# drills. The SYSTEM_PROMPT wording stays as a hint (it costs nothing and it
# steers the `what` copy), but nothing downstream depends on Sonnet obeying it.
#
# This is NOT a validator on Drill: Drill does not carry the song title/artist,
# and Breakdown does not either. Threading them into the model purely to satisfy
# this rule would make Drill un-reusable for every other caller. A free function
# applied by whoever HAS the song context keeps Drill a plain data shape.

def _names_song(text: str, song_title: str, song_artist: str) -> bool:
    """True if `text` mentions the song title or the artist, case-insensitively.

    Plain substring test in both fields — the same test `scripts/grade_eval.py`
    applies for gate L4, so the enforced value and the graded value cannot drift.

    Empty/whitespace-only title or artist is ignored rather than matched: "" is a
    substring of every string, and a song row with a blank artist would otherwise
    flip every drill in every breakdown to song_specific=True.
    """
    haystack = text.lower()
    for needle in (song_title, song_artist):
        if needle and needle.strip() and needle.strip().lower() in haystack:
            return True
    return False


def enforce_song_specific(
    breakdown: Breakdown, song_title: str, song_artist: str
) -> Breakdown:
    """Force `song_specific=True` on any drill whose `what` names the song (FLE-44).

    Mutates `breakdown.drills` in place and returns the same instance for call-site
    chaining.

    ONE-WAY ONLY. A drill that claims song_specific=True without naming the song is
    left alone. Gate L4 grades the flag as an IFF, so that direction can still fail
    the eval — deliberately. Clearing the flag would mean deciding that a drill
    saying "the opening riff" or "the turnaround in the outro" is not about the song,
    which is exactly the kind of semantic judgement this function exists to avoid
    making. The true→false direction is a copy-quality signal for the eval to report;
    the false→true direction is a correctness guarantee, and only it belongs in code.

    Args:
        breakdown: A parsed Breakdown. Pre-4.1 cached rows (drills=[]) are a no-op.
        song_title: The song's title, as stored on the songs row.
        song_artist: The song's artist, as stored on the songs row.

    Returns:
        The same Breakdown instance, with any mis-flagged drill corrected.
    """
    for drill in breakdown.drills:
        if not drill.song_specific and _names_song(drill.what, song_title, song_artist):
            logger.info(
                "FLE-44: forcing song_specific=True on drill %r for %r — `what` names "
                "the song or artist but Sonnet emitted false.",
                drill.name, song_title,
            )
            drill.song_specific = True
    return breakdown


class BreakdownEnvelope(BaseModel):
    """Envelope wrapper for GET /api/v1/songs/{id}/breakdown response.

    Phase 4.1 (Plan 04.1-02 B1 FIX): wraps the cached-forever Breakdown with
    EPHEMERAL per-request state derived from user_sessions.

    `drill_rated_today_indices` is the durable server-derived source of truth for
    the mobile drill-primary UI logic — replaces the fragile client-side QueryClient
    mutation-cache subscription pattern flagged by the plan checker.

    Contract preservation:
      - The `breakdown` field is the SAME shape as the raw Breakdown that was
        previously returned directly by get_breakdown (mobile consumers must now
        access response.breakdown.drills / .tab / .chords / .technique_notes rather
        than response.drills etc. — see Plan 03 schema.d.ts regen).
      - The `drill_rated_today_indices` field is computed on EVERY request from
        user_sessions and is NOT cached. This preserves the D-11 cache-forever
        contract for the Breakdown itself — the envelope is a pure request-scoped
        wrapper.
    """
    breakdown: Breakdown
    drill_rated_today_indices: list[int] = Field(
        default_factory=list,
        description=(
            "0-based drill_index values the current user has rated for this song today. "
            "Sorted ascending. Empty when no drill ratings exist yet. Server-derived — "
            "cannot be set by clients."
        ),
    )


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
        """Coerce placeholder JSONB dicts to None (Grace-E, restored 2026-09-08).

        Rows inserted during onboarding land with `breakdown = {"placeholder": "..."}`
        as a marker that a real breakdown has not been generated yet. Without this
        coerce, Pydantic would fail to validate the placeholder dict against the
        Breakdown schema and 500 the endpoint.

        History: the 2026-08-17 hotfix (6ae712a) coerced placeholder → empty-Breakdown
        as a bandaid for EAS build 690bc876 which accessed song.breakdown.tab / .chords
        without null guards and crashed on None. Grace-B (d42ae4c, 2026-09-08) added
        the null-guard mobile-side, and 3c67c77 (2026-09-08) wired useBreakdown so the
        pending state now has a real hook to display a FletcherLoader against. With
        both mobile-side fixes shipped in EAS build a08e7f3b+, the coerce target moves
        back to None so the Optional[Breakdown] contract matches server truth again.

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
