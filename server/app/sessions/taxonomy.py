# server/app/sessions/taxonomy.py
#
# PART III of the FLE-4 spec — the drill taxonomy. Pure functions, no DB, no I/O,
# no LLM.
#
# Two axes. §8 families are a hard-coded 18-value grid hung off the six fixed
# PrimarySkillRoot values; §9 tiers are D1-D5 on the same [0,1] scale the skill
# graph and the song catalog already use. A (family, tier) pair is a CELL, and the
# bank is graded by how many cells are stocked.
#
# The load-bearing property of §9.1, and the reason tier is computed rather than
# judged: tier is a deterministic function of columns the drill already has. When
# the rubric gets retuned after the pilot -- and it will -- every tier is recomputed
# with one UPDATE over stored features. No re-generation, no Sonnet spend.
#
# Mirrored enums, deliberately: `SkillRoot` restates app.models.db.PrimarySkillRoot
# rather than importing it, the same way plan.py restates SessionMode and Block.
# The point is that the pure core of the session engine imports no SQLAlchemy and
# can be unit-tested with no database at all. test_session_taxonomy.py pins the two
# copies to each other so the duplication cannot drift silently.
#
# Spec section references are to the `Session Structure & Drill Taxonomy` document
# on FLE-4. Any change to a constant or a bucket boundary in this file is a
# GENERATOR_VERSION bump -- old sessions have to stay explicable.

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

__all__ = [
    "SkillRoot",
    "Tier",
    "TechniqueFamily",
    "FamilySpec",
    "FAMILY_SPECS",
    "VIABLE_CELLS",
    "PILOT_BAND_CELLS",
    "TierFeatures",
    "tier_features",
    "compute_tier",
    "tier_from_raw",
    "tier_from_mastery",
    "band",
    "stretch_tier",
    "in_band",
]


class SkillRoot(str, enum.Enum):
    """D-08's six fixed roots. Mirror of app.models.db.PrimarySkillRoot."""

    RHYTHM = "rhythm"
    LEAD = "lead"
    CHORD_VOICINGS = "chord_voicings"
    FINGERSTYLE = "fingerstyle"
    MUSIC_THEORY = "music_theory"
    TIMING = "timing"


class Tier(str, enum.Enum):
    """§9 — five difficulty tiers, aligned to the [0,1] mastery scale.

    D1 0.00-0.20 · D2 0.20-0.40 · D3 0.40-0.60 · D4 0.60-0.80 · D5 0.80-1.00.
    """

    D1 = "D1"
    D2 = "D2"
    D3 = "D3"
    D4 = "D4"
    D5 = "D5"

    @property
    def ordinal(self) -> int:
        """1-5. Tiers are ordered; the enum's string value is not comparable."""
        return _TIER_ORDINALS[self.value]

    def shifted(self, delta: int) -> "Tier":
        """Move `delta` tiers, clamped to [D1, D5]. §11.1 walks the band with this."""
        return _TIER_BY_ORDINAL[max(1, min(5, self.ordinal + delta))]

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Tier):
            return NotImplemented
        return self.ordinal < other.ordinal

    def __le__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Tier):
            return NotImplemented
        return self.ordinal <= other.ordinal

    def __gt__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Tier):
            return NotImplemented
        return self.ordinal > other.ordinal

    def __ge__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Tier):
            return NotImplemented
        return self.ordinal >= other.ordinal


_TIER_ORDINALS = {"D1": 1, "D2": 2, "D3": 3, "D4": 4, "D5": 5}
_TIER_BY_ORDINAL = {1: Tier.D1, 2: Tier.D2, 3: Tier.D3, 4: Tier.D4, 5: Tier.D5}

# §9 — tier from a [0,1] mastery. Upper bounds, walked in order.
_MASTERY_TIER_BANDS = ((0.20, Tier.D1), (0.40, Tier.D2), (0.60, Tier.D3), (0.80, Tier.D4))

# §10.3 — the pilot cohort is intermediate-to-advanced. D1 and D5 are out of band.
PILOT_BAND_TIERS = (Tier.D2, Tier.D3, Tier.D4)


class TechniqueFamily(str, enum.Enum):
    """§8 — the 18 technique families. Hard-coded, global, never per-user.

    Coverage needs a stable grid: a per-user taxonomy cannot be graded. Per-user
    skill NODES stay Sonnet-generated as today. A drill carries both a family and a
    target skill node and they are different things -- the family says which cell of
    the bank the drill stocks, the node says whose weakness it serves.
    """

    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"
    F1 = "F1"
    F2 = "F2"
    F3 = "F3"
    T1 = "T1"
    T2 = "T2"
    M1 = "M1"
    M2 = "M2"

    @property
    def spec(self) -> "FamilySpec":
        return FAMILY_SPECS[self]

    @property
    def root(self) -> SkillRoot:
        return FAMILY_SPECS[self].root

    @property
    def tier_range(self) -> tuple[Tier, Tier]:
        s = FAMILY_SPECS[self]
        return s.tier_low, s.tier_high


@dataclass(frozen=True)
class FamilySpec:
    """One row of the §8 table.

    `tier_low`/`tier_high` encode which cells are MUSICALLY REAL. "Extended jazz
    voicings at D1" isn't a beginner drill, it's a mislabelled one -- so C4 simply
    does not exist below D4. That is what turns a 90-cell rectangle into a 60-cell
    grid worth grading against.
    """

    label: str
    root: SkillRoot
    tier_low: Tier
    tier_high: Tier

    @property
    def cells(self) -> int:
        return self.tier_high.ordinal - self.tier_low.ordinal + 1


FAMILY_SPECS: dict[TechniqueFamily, FamilySpec] = {
    TechniqueFamily.R1: FamilySpec("Strumming patterns", SkillRoot.RHYTHM, Tier.D1, Tier.D3),
    TechniqueFamily.R2: FamilySpec("Palm muting & dynamics", SkillRoot.RHYTHM, Tier.D1, Tier.D4),
    TechniqueFamily.R3: FamilySpec("Riff rhythm lock-up", SkillRoot.RHYTHM, Tier.D2, Tier.D5),
    TechniqueFamily.L1: FamilySpec("Alternate picking & speed", SkillRoot.LEAD, Tier.D2, Tier.D5),
    TechniqueFamily.L2: FamilySpec("Bending & vibrato", SkillRoot.LEAD, Tier.D2, Tier.D5),
    TechniqueFamily.L3: FamilySpec("Legato (slides, hammers, pulls)", SkillRoot.LEAD, Tier.D1, Tier.D4),
    TechniqueFamily.L4: FamilySpec("Scale shapes & position shifts", SkillRoot.LEAD, Tier.D2, Tier.D5),
    TechniqueFamily.C1: FamilySpec("Open chords & transitions", SkillRoot.CHORD_VOICINGS, Tier.D1, Tier.D2),
    TechniqueFamily.C2: FamilySpec("Barre chords", SkillRoot.CHORD_VOICINGS, Tier.D2, Tier.D4),
    TechniqueFamily.C3: FamilySpec("Triads & inversions", SkillRoot.CHORD_VOICINGS, Tier.D3, Tier.D5),
    TechniqueFamily.C4: FamilySpec("Extended & jazz voicings", SkillRoot.CHORD_VOICINGS, Tier.D4, Tier.D5),
    TechniqueFamily.F1: FamilySpec("Thumb independence & bass lines", SkillRoot.FINGERSTYLE, Tier.D2, Tier.D4),
    TechniqueFamily.F2: FamilySpec("Arpeggio patterns", SkillRoot.FINGERSTYLE, Tier.D1, Tier.D4),
    TechniqueFamily.F3: FamilySpec("Hybrid & Travis picking", SkillRoot.FINGERSTYLE, Tier.D3, Tier.D5),
    TechniqueFamily.T1: FamilySpec("Fretboard mapping", SkillRoot.MUSIC_THEORY, Tier.D1, Tier.D3),
    TechniqueFamily.T2: FamilySpec("Intervals & chord construction", SkillRoot.MUSIC_THEORY, Tier.D2, Tier.D4),
    TechniqueFamily.M1: FamilySpec("Subdivision & click discipline", SkillRoot.TIMING, Tier.D1, Tier.D3),
    TechniqueFamily.M2: FamilySpec("Syncopation & feel", SkillRoot.TIMING, Tier.D2, Tier.D5),
}

# §8 / §10.3 — the two totals the spec asserts. Pinned here so a typo in the table
# above is caught by a test rather than by a coverage report that quietly grades
# against 59 cells.
VIABLE_CELLS = sum(s.cells for s in FAMILY_SPECS.values())
PILOT_BAND_CELLS = sum(
    1
    for s in FAMILY_SPECS.values()
    for t in PILOT_BAND_TIERS
    if s.tier_low <= t <= s.tier_high
)


# ---------------------------------------------------------------------------
# §9.1 — Tier is computed, not judged
# ---------------------------------------------------------------------------

# Notes-per-beat for each subdivision the Tab schema can express. Same table as
# plan.py's; kept local so neither module has to import the other.
_SUBDIVISION_NOTES_PER_BEAT = {
    "whole": 0.25,
    "half": 0.5,
    "quarter": 1.0,
    "eighth": 2.0,
    "sixteenth": 4.0,
}

# The five feature rubrics: (upper bound, bucket), walked in order, else 4.
_SPEED_LOAD_BUCKETS = ((1.5, 0), (3.0, 1), (5.0, 2), (8.0, 3))
_DENSITY_BUCKETS = ((1.0, 0), (1.5, 1), (2.5, 2), (4.0, 3))
_SPAN_BUCKETS = ((3, 0), (5, 1), (7, 2), (10, 3))
_SIMULTANEITY_BUCKETS = ((1, 0), (2, 1), (3, 2), (5, 3))
_LADDER_STRETCH_BUCKETS = ((2, 0), (4, 1), (6, 2), (8, 3))

# raw score -> tier. Upper bounds, walked in order, else D5.
_RAW_TIER_BANDS = ((3, Tier.D1), (7, Tier.D2), (11, Tier.D3), (15, Tier.D4))


def _bucket(value: float, buckets: Sequence[tuple[float, int]]) -> int:
    for upper, score in buckets:
        if value <= upper:
            return score
    return 4


@dataclass(frozen=True)
class TierFeatures:
    """The five §9.1 features, bucketed, plus the raw 0-20 sum.

    Stored alongside the tier (as `tier_raw_score`) precisely so the rubric is
    retunable without re-reading tab_snippet for every drill in the bank.
    """

    speed_load: int
    density: int
    span: int
    simultaneity: int
    ladder_stretch: int

    @property
    def raw(self) -> int:
        return (
            self.speed_load
            + self.density
            + self.span
            + self.simultaneity
            + self.ladder_stretch
        )


def _iter_beats(tab_snippet: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for measure in tab_snippet.get("measures") or []:
        for beat in measure.get("beats") or []:
            yield beat


def _iter_notes(tab_snippet: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for beat in _iter_beats(tab_snippet):
        for note in beat.get("notes") or []:
            yield note


def tier_features(
    tab_snippet: Mapping[str, Any], *, start_bpm: int, target_bpm: int
) -> TierFeatures:
    """§9.1 — the five features, each bucketed 0-4, from the drill's own columns.

    What this rubric CANNOT see, stated because it bounds how much to trust it:
    `Note` carries string/fret/duration and nothing else, so there are no bend,
    slide or vibrato markers in the schema. A vibrato drill and a single sustained
    note score identically. The family tier-range clamp in compute_tier() is the
    v1 mitigation -- L2 can never fall below D2 -- and the real fix is a `technique`
    enum on Note, which is out of scope here (§14).

    A measure-less snippet scores the floor on density, span and simultaneity --
    there is nothing there to be hard. Its speed load is NOT zero: with no notes to
    read a subdivision from, the rubric assumes quarter notes, so a drill with no tab
    and a 200bpm target still reads as fast. That is the right default; the
    alternative is a drill whose tab failed to generate scoring as trivially easy and
    outranking real material on every tier filter in the selector.
    """
    beats = list(_iter_beats(tab_snippet))
    notes = list(_iter_notes(tab_snippet))

    # 1 — speed load. `shortest` is the shortest subdivision PRESENT, expressed as
    # notes-per-beat, so sixteenths (4.0) score higher than quarters (1.0).
    subdivisions = [
        _SUBDIVISION_NOTES_PER_BEAT[d]
        for d in (str(n.get("duration") or "quarter") for n in notes)
        if d in _SUBDIVISION_NOTES_PER_BEAT
    ]
    shortest = max(subdivisions) if subdivisions else 1.0
    nps = shortest * max(target_bpm, 0) / 60

    # 2 — density.
    density = len(notes) / max(len(beats), 1)

    # 3 — span. Open strings are not a hand position, so fret 0 is excluded.
    frets = [int(n["fret"]) for n in notes if n.get("fret") is not None and int(n["fret"]) > 0]
    span = max(frets) - min(frets) if len(frets) >= 2 else 0

    # 4 — simultaneity.
    simultaneity = max((len(b.get("notes") or []) for b in beats), default=1)

    # 5 — ladder stretch, in rungs.
    ladder_stretch = (target_bpm - start_bpm) / 5

    return TierFeatures(
        speed_load=_bucket(nps, _SPEED_LOAD_BUCKETS),
        density=_bucket(density, _DENSITY_BUCKETS),
        span=_bucket(span, _SPAN_BUCKETS),
        simultaneity=_bucket(simultaneity, _SIMULTANEITY_BUCKETS),
        ladder_stretch=_bucket(ladder_stretch, _LADDER_STRETCH_BUCKETS),
    )


def tier_from_raw(raw: int) -> Tier:
    """§9.1 — the raw 0-20 score to a tier. Upper bounds are inclusive.

    D1 <= 3 · D2 <= 7 · D3 <= 11 · D4 <= 15 · D5 otherwise. Split out from
    compute_tier() so the cutoffs can be pinned directly: they are the numbers most
    likely to be retuned after the pilot, and they are retuned by rewriting exactly
    this table.
    """
    for upper, tier in _RAW_TIER_BANDS:
        if raw <= upper:
            return tier
    return Tier.D5


def compute_tier(
    tab_snippet: Mapping[str, Any],
    *,
    start_bpm: int,
    target_bpm: int,
    family: Optional[TechniqueFamily] = None,
) -> tuple[Tier, int]:
    """§9.1 — (tier, tier_raw_score). The family's tier range WINS over the rubric.

    `family=None` is the pre-FLE-8-backfill case: the `drills` table has no family
    column yet, so there is no range to clamp to and the raw rubric stands. Passing
    a family once the column ships can only move the tier INTO its family's range,
    never out of it, so the unclamped value is the more permissive of the two.
    """
    features = tier_features(tab_snippet, start_bpm=start_bpm, target_bpm=target_bpm)
    raw = features.raw
    tier = tier_from_raw(raw)
    if family is not None:
        low, high = family.tier_range
        if tier < low:
            tier = low
        elif tier > high:
            tier = high
    return tier, raw


# ---------------------------------------------------------------------------
# §11.1 — Tier band per family
# ---------------------------------------------------------------------------

def tier_from_mastery(mastery: float) -> Tier:
    """§11.1 — which tier a [0,1] mastery reads as. D5 at the top."""
    for upper, tier in _MASTERY_TIER_BANDS:
        if mastery < upper:
            return tier
    return Tier.D5


def band(
    *,
    root_mastery: Optional[float] = None,
    player_level: Optional[float] = None,
) -> tuple[Tier, Tier]:
    """§11.1 — the tier range this user is served in this family.

    `m_f = root_mastery(root_of(f))`, falling back to player_level, falling back to
    0.5. The band is [max(D1, t_f - 1), t_f]: one tier of already-owned material
    under the tier the mastery actually reads as. The tier ABOVE is not in the band;
    it is reachable only through the stretch slot, once, at the end of the session.
    """
    m_f = root_mastery if root_mastery is not None else player_level
    if m_f is None:
        m_f = 0.5
    t_f = tier_from_mastery(m_f)
    return t_f.shifted(-1), t_f


def stretch_tier(
    *,
    root_mastery: Optional[float] = None,
    player_level: Optional[float] = None,
) -> Tier:
    """§11.1 — min(D5, t_f + 1). The one tier a session is allowed to reach above."""
    _, t_f = band(root_mastery=root_mastery, player_level=player_level)
    return t_f.shifted(1)


def in_band(tier: Tier, bounds: tuple[Tier, Tier]) -> bool:
    low, high = bounds
    return low <= tier <= high
