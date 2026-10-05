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
#
# FLE-115 (2026-10-05): tab schema v2 — articulation, rhythm, capo. Implements
#   deliverable 1 of .planning/design/261005-fle105-tab-schema-v2.md (accepted,
#   commit 1b20620). Section references below (§2.1, §7.1.4, ...) are to that
#   document. Nine additions, every one optional with a default, so a plain
#   picked note stays exactly {string, fret, duration} and every cached JSONB
#   breakdown reads back unchanged.
import logging
from fractions import Fraction
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FLE-115 repair policy — when a validator clamps and when it raises
# ---------------------------------------------------------------------------
# §8.2 states the governing constraint: unlike `drills`, which breakdown.py
# soft-fails to [] on ValidationError, a `tab` validation error has NO soft-fail
# path. Raising there does not drop a note, it drops the song — permanently, for
# a cached row. But "never raise" is not right either: some violations have no
# honest repair, and inventing one would be our musical judgement standing in
# for the model's.
#
# The split already exists in this file and v2 just generalises it —
# `Drill._clamp_repetitions` clamps a value, `Drill._target_bpm_above_start`
# raises on a relationship:
#
#   CLAMP/COERCE a VALUE that has a nearest-legal answer.
#     - an unknown duration string            -> "quarter"   (§8.2)
#     - a legacy `fret: -1`                   -> fret 0 + effects:["dead"] (§6.1)
#     - a fret outside 0..24-capo             -> the bound    (§7.1)
#     - a beat with neither notes nor rest    -> a quarter rest (§3)
#     - simultaneous notes that disagree on duration -> the longest (§2.4)
#
#   RAISE on a RELATIONSHIP that contradicts itself, where any repair would be
#   an invention:
#     - a beat that is BOTH a sounding column and a rest (§3)
#     - a legato link whose target fret contradicts it (§4)
#     - `sounding_name` that is not `name` transposed up `capo` (§7.2)
#     - a drill snippet whose capo/tuning differ from the parent tab's (§7.1.4)
#
# Every clamp logs a warning. None of the raising cases is reachable from a
# cached row: all four involve v2-only fields that no pre-FLE-115 breakdown
# carries, so the compatibility argument above does not apply to them.
# ---------------------------------------------------------------------------


# --- §2.1 Duration -- a closed vocabulary, canonical spelling <modifier>-<base>
Duration = Literal[
    "whole", "half", "quarter", "eighth", "sixteenth", "thirty-second",
    "dotted-half", "dotted-quarter", "dotted-eighth", "dotted-sixteenth",
    "triplet-half", "triplet-quarter", "triplet-eighth", "triplet-sixteenth",
]

# --- §2.2 The beat-value table. THE one table; `Fraction`, not float.
#
# Exported deliberately: the repo carried three private, mutually disagreeing
# duration tables (app/ai/tab_verification.py, scripts/fle102_tab_audit.py:47,
# scripts/fle103_verify_pilot_tabs.py:42). This is the one they import.
#
# `Fraction`, and the reason stated here is NOT the one in §2.2 of the spec.
# §2.2 (and the FLE-115 issue body) justify it with `sum([1/6] * 6) != 1.0`.
# That is false — it is exactly 1.0. Measured before relying on it: every bar of
# <= 7 columns for 4/4, 3/4, 7/8 and 3/8 exhaustively, plus 400k sampled bars up
# to 40 columns, produced ZERO float false verdicts, and the 1e-6 tolerance was
# never hiding a short bar either (the smallest expressible shortfall is a 32nd,
# 0.125). See test_float_residue_appears_on_a_partial_sum_not_a_complete_bar.
#
# `Fraction` is still the right call, for the weaker true reason: with these 14
# denominators float exactness is a COINCIDENCE of symmetric rounding, not a
# property, and it stops holding the moment a sum does not complete a bar —
# sum([1/3] * 5) != float(Fraction(5, 3)) is real, and a partial sum is what a
# consumer compares the moment it measures anything but a full measure. Exact
# arithmetic makes that structural and lets the meter check drop its tolerance
# rather than pick one. It costs nothing on 14 fixed denominators.
#
# One quarter note = 1 beat, independent of the time signature's denominator.
DURATION_BEATS: dict[str, Fraction] = {
    "whole": Fraction(4),
    "half": Fraction(2),
    "quarter": Fraction(1),
    "eighth": Fraction(1, 2),
    "sixteenth": Fraction(1, 4),
    "thirty-second": Fraction(1, 8),
    "dotted-half": Fraction(3),
    "dotted-quarter": Fraction(3, 2),
    "dotted-eighth": Fraction(3, 4),
    "dotted-sixteenth": Fraction(3, 8),
    "triplet-half": Fraction(4, 3),
    "triplet-quarter": Fraction(2, 3),
    "triplet-eighth": Fraction(1, 3),
    "triplet-sixteenth": Fraction(1, 6),
}

# §8.2 — the alternate spellings the two existing audit scripts already accept.
# Nothing that passes today's audit may fail tomorrow's parse.
_DURATION_ALIASES = {
    "dotted_half": "dotted-half",
    "dotted_quarter": "dotted-quarter",
    "dotted_eighth": "dotted-eighth",
    "dotted_sixteenth": "dotted-sixteenth",
    "eighth-triplet": "triplet-eighth",
    "quarter-triplet": "triplet-quarter",
    "sixteenth-triplet": "triplet-sixteenth",
    "half-triplet": "triplet-half",
    "thirty_second": "thirty-second",
    "32nd": "thirty-second",
}

_FALLBACK_DURATION = "quarter"

# §7.1 — there is no way to express a note behind the capo, which is correct:
# physically there isn't one. The usable range shrinks as the capo climbs.
MAX_FRET = 24


def canonical_duration(v: object) -> object:
    """§8.2 — normalise an inbound duration string. COERCE, DO NOT RAISE.

    See the repair-policy block above: `duration: str` -> `duration: Duration`
    is the one field v2 makes stricter, and a value outside the 14 taken as a
    ValidationError would cost the whole song, not the note.

    Non-string input falls through untouched so Pydantic's own type error is
    still the thing the caller sees.
    """
    if not isinstance(v, str):
        return v
    s = v.strip().lower().replace(" ", "-").replace("--", "-")
    s = _DURATION_ALIASES.get(s, s)
    if s not in DURATION_BEATS:
        logger.warning(
            "Unknown note duration %r — coerced to %r (FLE-115 §8.2).",
            v, _FALLBACK_DURATION,
        )
        return _FALLBACK_DURATION
    return s


def duration_beats(value: object) -> Optional[Fraction]:
    """§2.2 — exact beat-value of a duration string, or None if it is unknown.

    The read-only companion to `canonical_duration`, and the entry point the
    audit scripts use. The two behaviours have to stay separate: on the WRITE
    path an unknown duration is coerced so a whole song is not lost (§8.2), but
    on the AUDIT path coercing it would silently repair the very defect the
    audit exists to count. So this reports `None` and the caller decides.

    Accepts the §8.2 aliases, because the scripts that call it accepted them
    before this table existed and nothing that passes today's audit may fail
    tomorrow's.
    """
    if not isinstance(value, str):
        return None
    s = value.strip().lower().replace(" ", "-").replace("--", "-")
    return DURATION_BEATS.get(_DURATION_ALIASES.get(s, s))


# --- §4 Legato links ------------------------------------------------------
# All four say the same structural thing: the next note on this string is
# reached without a fresh pick. The target is implicit — the next note on the
# same string in beat order, crossing the barline if necessary.
Link = Literal["tie", "hammer_on", "pull_off", "slide"]

# §4 — each link carries a free, exact check that needs no recording.
# (predicate on (this_fret, next_fret), human description)
_LINK_RULES: dict[str, tuple] = {
    "tie": (lambda a, b: a == b, "the same fret"),
    "hammer_on": (lambda a, b: b > a, "a higher fret"),
    "pull_off": (lambda a, b: b < a, "a lower fret"),
    "slide": (lambda a, b: b != a, "a different fret"),
}

# --- §6 Technique and effects — two fields, deliberately ------------------
# They cannot be one field: a palm-muted, accented hammer-on into a bent note
# with vibrato is a SINGLE note in Comfortably Numb, and a
# one-articulation-per-note model is the same kind of lossy as the v1 schema
# this replaces.
Technique = Literal[
    "tap", "slap", "pop", "rake",
    "natural_harmonic", "pinch_harmonic", "artificial_harmonic",
]
Effect = Literal[
    "vibrato", "wide_vibrato", "palm_mute", "staccato", "accent", "let_ring", "dead",
]


# --- §7.2 chord-name transposition ----------------------------------------
# Flat-preferring spelling, matching app/ai/tab_verification.py's CHROM so the
# two modules name the same pitch the same way.
_CHROM = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
_NOTE_PC = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4,
    "F": 5, "E#": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9,
    "A#": 10, "BB": 10, "B": 11, "CB": 11,
}


def _split_root(name: str) -> Optional[tuple[int, str]]:
    """('Em7') -> (4, 'm7'); None when the leading token is not a note name."""
    s = (name or "").strip()
    if not s or s[0].upper() not in "ABCDEFG":
        return None
    root, i = s[0].upper(), 1
    if i < len(s) and s[i] in "#b":
        root, i = root + s[i], i + 1
    pc = _NOTE_PC.get(root.upper())
    if pc is None:
        return None
    return pc, s[i:]


def transpose_chord_name(name: str, semitones: int) -> Optional[str]:
    """§7.2 — 'Em7' up 2 -> 'F#m7'. Quality unchanged; slash bass moves too.

    Returns None when the name cannot be parsed as root + suffix, so callers can
    tell "could not transpose" from "transposes to itself".
    """
    out = []
    for part in (name or "").split("/"):
        split = _split_root(part)
        if split is None:
            return None
        pc, suffix = split
        out.append(_CHROM[(pc + semitones) % 12] + suffix)
    return "/".join(out)


def _is_transposition(name: str, sounding: str, semitones: int) -> Optional[bool]:
    """True/False when both names parse, None when either does not.

    Compares the root by PITCH CLASS, not spelling — F#m7 and Gbm7 are the same
    chord and either is an acceptable `sounding_name`.
    """
    want, got = (name or "").split("/"), (sounding or "").split("/")
    if len(want) != len(got):
        return False
    for w, g in zip(want, got):
        a, b = _split_root(w), _split_root(g)
        if a is None or b is None:
            return None
        if (a[0] + semitones) % 12 != b[0] % 12 or a[1].strip() != b[1].strip():
            return False
    return True


class Bend(BaseModel):
    """§5 — where the pitch goes from the fingered fret.

    `Note.fret` stays the FINGERED fret. The four `kind` values are the four
    standard forms and are complete for single-target bends.

    `0.25` is in the schema on purpose (§5.1): the quarter-tone curl is a pitch,
    not an ornament — in a blues in E the b3 curled toward the major 3rd is
    deliberately neither, and rounding it either way is a PITCH ERROR. The
    scatter risk is handled in the FLE-118 generator prompt, which must not teach
    `0.25` in v1; the schema stays able to express ground truth. Render it `b¼`
    and call it a "curl", never a "quarter-tone bend".

    The sounding pitch of a bent note is not an integer fret: any consumer
    deriving pitch (the FLE-103 verifier's V1/V2/V3 chord checks) must SKIP a
    bent note rather than score the fingered pitch, or every correct bend reads
    as a foreign note.
    """
    semitones: Literal[0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    kind: Literal["bend", "pre_bend", "bend_release", "pre_bend_release"] = "bend"

    @field_validator("semitones", mode="before")
    @classmethod
    def _as_float(cls, v: object) -> object:
        """Accept `1` for `1.0` — JSON drops a trailing `.0` on whole numbers."""
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v.strip())
            except ValueError:
                return v
        return v


class Note(BaseModel):
    """A single note on the guitar.

    v1 was exactly {string, fret, duration} — FLE-102 measured all 532 notes of
    the cached corpus and found that key-set with no exceptions, which is why
    six of ten audited songs had no correct representation at any prompt quality.
    Everything v2 adds below is optional with a default, so the common case (a
    plain picked note) costs the model nothing extra to emit.
    """
    string: int     # 1 = high e ... 6 = low E
    # §6.1 — `fret` is >= 0, ALWAYS. This resolves a live contradiction:
    # TabNotation.tsx:94 renders `fret === -1` as a dead note while
    # fle103_verify_pilot_tabs.py:143 rejects `fret < 0`. A dead/ghost note is
    # now the fret it is fingered at plus effects:["dead"]; `fret = -1` keeps its
    # meaning only on ChordPosition, where it means "string not played" in a
    # diagram and is a different concept.
    fret: int = Field(..., ge=0)
    duration: Duration
    # §4 — the next note on this string is reached without a fresh pick.
    link_to_next: Optional[Link] = None
    bend: Optional[Bend] = None
    # §6 — how the note is STRUCK. Exclusive: a note cannot be both tapped and
    # slapped. None means an ordinary pick.
    technique: Optional[Technique] = None
    # §6 — what happens to it WHILE IT RINGS, and how hard. Stackable.
    effects: list[Effect] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_negative_fret(cls, data: object) -> object:
        """§6.1 — carry a cached `fret: -1` forward as effects:["dead"].

        A clamp, not a raise (see the repair-policy block): the renderer has
        emitted and drawn `-1` since v1, so a cached breakdown may hold one, and
        rejecting it would take the whole song down. `-1` never named a fret —
        it named "this string is muted" — so fret 0 plus the `dead` effect is
        the faithful reading, and the renderer draws the same `x` it drew before.
        """
        if not isinstance(data, dict):
            return data
        fret = data.get("fret")
        if not isinstance(fret, int) or isinstance(fret, bool) or fret >= 0:
            return data
        effects = list(data.get("effects") or [])
        if fret == -1 and "dead" not in effects:
            effects.append("dead")
        logger.warning(
            "Note.fret %d is negative — coerced to 0%s (FLE-115 §6.1).",
            fret, " with effects:['dead']" if fret == -1 else "",
        )
        return {**data, "fret": 0, "effects": effects}

    @field_validator("duration", mode="before")
    @classmethod
    def _canonicalise_duration(cls, v: object) -> object:
        return canonical_duration(v)

    @field_validator("effects")
    @classmethod
    def _dedupe_effects(cls, v: list) -> list:
        """§6 — duplicates are de-duplicated on parse, first occurrence wins."""
        seen: list = []
        for e in v:
            if e not in seen:
                seen.append(e)
        return seen


class Beat(BaseModel):
    """One rhythmic column: either a sounding voicing or a silence, never both.

    §3 — `rest` lives here and not on `Note` because a rest has no string and no
    fret; putting it on `Note` forces a sentinel on an object that has neither
    and every consumer then has to know the sentinel. At the beat level the
    meter arithmetic stays one branch (see `beat_beats`).
    """
    notes: list[Note] = Field(default_factory=list)
    rest: Optional[Duration] = None   # set iff notes == []

    @model_validator(mode="before")
    @classmethod
    def _repair_empty_beat(cls, data: object) -> object:
        """A beat with no notes and no rest is silence of unstated length.

        Clamped to a quarter rest rather than rejected: an empty `notes` list is
        reachable from a cached row (nothing ever forbade it), and the repair is
        the one the data already implies — a column with nothing sounding IS a
        rest. The length is a guess, which is why it warns; the meter check in
        app/ai/tab_verification.py is what reports the bar if the guess is wrong.
        """
        if not isinstance(data, dict):
            return data
        if data.get("notes") or data.get("rest") is not None:
            return data
        logger.warning(
            "Beat has neither notes nor a rest — coerced to a quarter rest "
            "(FLE-115 §3)."
        )
        return {**data, "rest": _FALLBACK_DURATION}

    @field_validator("rest", mode="before")
    @classmethod
    def _canonicalise_rest(cls, v: object) -> object:
        return v if v is None else canonical_duration(v)

    @model_validator(mode="after")
    def _one_column_one_duration(self) -> "Beat":
        """§3 exactly-one-of, and §2.4 one duration per beat.

        §2.4 is the rule that survives from v1 — fle103_verify_pilot_tabs.py:88
        already fails a beat whose notes disagree ("simultaneous notes of one
        voicing must agree"), and v2 promotes it here. It COERCES to the longest
        value rather than raising, which is the same fallback that verifier
        already takes, because a disagreeing beat is reachable from cached data.

        The consequence has to be stated rather than discovered: independent
        polyphonic voices remain inexpressible. Expressing them needs a
        `voices: list[Voice]` layer above Beat — confirmed out of scope by Fret
        (Q3). A note held under later motion is written `effects: ["let_ring"]`
        at the nominal duration of its own beat (§2.4.1).
        """
        if self.notes and self.rest is not None:
            raise ValueError(
                "a beat is either a sounding column or a rest, never both — "
                f"got {len(self.notes)} note(s) and rest={self.rest!r} (§3)"
            )
        if not self.notes and self.rest is None:
            raise ValueError("a beat must hold notes or a rest (§3)")
        durations = {n.duration for n in self.notes}
        if len(durations) > 1:
            longest = max(durations, key=lambda d: DURATION_BEATS[d])
            logger.warning(
                "Beat mixes durations %s — simultaneous notes of one voicing "
                "must agree; coerced to %r (FLE-115 §2.4).",
                sorted(durations), longest,
            )
            for n in self.notes:
                n.duration = longest
        return self


class Measure(BaseModel):
    """A musical measure with a list of beats."""
    beats: list[Beat]
    time_signature: str = "4/4"  # e.g. "4/4", "3/4", "6/8"


def beat_beats(beat: Beat) -> Fraction:
    """§2.2/§3 — the beat-value of one column. One branch, exact.

    A rest column contributes its own value; a sounding column contributes its
    notes' (single, per §2.4) duration. An exported helper so no consumer has to
    re-derive the rest branch — the one that is silently skipped by default.
    """
    if beat.rest is not None:
        return DURATION_BEATS[beat.rest]
    if not beat.notes:
        return Fraction(0)
    return max(DURATION_BEATS[n.duration] for n in beat.notes)


def measure_beats(measure: Measure) -> Fraction:
    """Exact sum of a measure's columns, in quarter-note beats."""
    return sum((beat_beats(b) for b in measure.beats), Fraction(0))


def time_signature_beats(time_signature: str) -> Fraction:
    """'6/8' -> Fraction(3) quarter-note beats. Falls back to 4/4."""
    try:
        num, den = (int(x) for x in (time_signature or "4/4").split("/"))
        if num <= 0 or den <= 0:
            raise ValueError
        return Fraction(num * 4, den)
    except Exception:
        return Fraction(4)


class Tab(BaseModel):
    """Full tab notation as a sequence of measures.

    §7 capo. Wonderwall (catalog difficulty 0.20, tagged `standard`) is played in
    standard tuning with a capo at 2. With `tuning` as the only setup field,
    emitting the shapes makes every sounding pitch two semitones wrong and
    emitting the pitches hands the player unidiomatic barres instead of the open
    shapes the song is actually played with — `tuning: standard` is true and
    actively misleading. Wagon Wheel and Street Fighting Man have it too.

    FRET NUMBERS ARE RELATIVE TO THE CAPO; the capoed string is fret 0, and
    `tuning` continues to mean the pitch of the open strings BEFORE the capo:

        sounding_pitch(note) = open_pitch(tuning[note.string]) + capo + note.fret

    Uniform — no special case for fret == 0. Absolute-frets-plus-a-label makes
    every open-position shape unreadable, which defeats the point of the field.
    Chord diagrams share this origin (ChordPosition.fret, Chord.base_fret) or the
    diagrams contradict the tab.

    `capo` means a FULL six-string capo. Partial and cut capos are not
    expressible and MUST NOT be faked with a doctored `tuning` array — that puts
    a lie in the one field pitch derivation trusts.
    """
    measures: list[Measure]
    tuning: list[str] = ["E", "A", "D", "G", "B", "e"]  # standard low-to-high
    capo: int = Field(0, ge=0, le=12)
    # §2.5 — swing and shuffle are MARKED, not notated. Writing swing as literal
    # triplet pairs hard-codes a 2:1 ratio that real swing does not have: Autumn
    # Leaves at 120 is near 2:1, Giant Steps at 240 is played almost dead even,
    # and notating both identically teaches a player to bounce Giant Steps like a
    # shuffle. `swing` and `shuffle` stay distinct — shuffle is a hard triplet
    # subdivision whose omitted middle triplet IS the groove. No `bossa`/`latin`
    # value: it would let the model emit a label in place of the pattern, which
    # is the substitution that produced this schema change in the first place.
    feel: Literal["straight", "swing", "shuffle"] = "straight"

    @model_validator(mode="after")
    def _clamp_frets_to_capo(self) -> "Tab":
        """§7.1 — 0 <= fret <= 24 - capo.

        Clamped, not raised (repair policy): a fret number has a nearest-legal
        answer, and the write gate in app/ai/tab_verification.py is where a tab
        with impossible frets should be refused. A note behind the capo cannot
        be expressed because physically there isn't one.
        """
        ceiling = MAX_FRET - self.capo
        for mi, m in enumerate(self.measures, 1):
            for bi, b in enumerate(m.beats, 1):
                for n in b.notes:
                    if n.fret > ceiling:
                        logger.warning(
                            "m%d b%d: fret %d exceeds %d (24 - capo %d) — clamped "
                            "(FLE-115 §7.1).", mi, bi, n.fret, ceiling, self.capo,
                        )
                        n.fret = ceiling
        return self

    @model_validator(mode="after")
    def _validate_legato_links(self) -> "Tab":
        """§4 — each link's target is checked against the fret it reaches.

        The target is implicit: the next note on the SAME STRING in beat order,
        crossing the barline if necessary (a tie over a barline is how sustain
        past the bar is written; Comfortably Numb needs it). Slide direction is
        derived from the target fret, never declared — a declared direction is
        one more thing that can contradict the notes.

        Raises, because a link that contradicts its target has no honest repair:
        dropping it silently discards an articulation the model meant, and
        rewriting the target would be inventing a note. `link_to_next` is a
        v2-only field, so no cached breakdown can trip this.

        Durations are unaffected by a tie: each note keeps its own value and the
        bar sums the same. A tie means "do not re-pick", not "merge".
        """
        flat: list[tuple[int, int, int, Note]] = []
        column = 0
        for mi, m in enumerate(self.measures, 1):
            for bi, b in enumerate(m.beats, 1):
                column += 1
                for n in b.notes:
                    flat.append((column, mi, bi, n))

        problems: list[str] = []
        for idx, (col, mi, bi, n) in enumerate(flat):
            if n.link_to_next is None:
                continue
            nxt = next(
                (o for (c, _, _, o) in flat[idx + 1:] if c > col and o.string == n.string),
                None,
            )
            if nxt is None:
                problems.append(
                    f"m{mi} b{bi}: {n.link_to_next} on string {n.string} has no "
                    f"next note on that string to reach"
                )
                continue
            predicate, want = _LINK_RULES[n.link_to_next]
            if not predicate(n.fret, nxt.fret):
                problems.append(
                    f"m{mi} b{bi}: {n.link_to_next} from fret {n.fret} on string "
                    f"{n.string} needs {want} next, got {nxt.fret}"
                )
        if problems:
            raise ValueError("; ".join(problems) + " (§4)")
        return self


class ChordPosition(BaseModel):
    """Finger placement for a single string in a chord diagram.

    §7.1.3 — `fret` shares the tab's origin: capo-relative, capoed string = 0.
    `fret: -1` keeps its v1 meaning HERE and only here — "this string is not
    played" in a diagram, which is a different concept from a dead note (§6.1).
    """
    string: int             # 1 = high e ... 6 = low E
    fret: int               # 0 = open, -1 = muted
    finger: Optional[int] = None  # 1=index, 2=middle, 3=ring, 4=pinky; None=open/muted


class Chord(BaseModel):
    """A chord diagram with all string positions.

    §7.2 — under a capo the shape and the sound have different names, and both
    are needed. `name` is THE SHAPE AS FINGERED: under a capo at 2 Wonderwall's
    first chord is fingered Em7 and sounds F#m7. The shape is what the hand does,
    it is what the diagram beside it depicts, and it is the transferable unit —
    "Em7 shape" works on fifty songs, "F#m7 at capo 2" is knowledge about one.

    `sounding_name` is set iff `capo > 0` — anyone playing along with the record
    needs it, but at capo 0 it is a redundant duplicate, and a redundant
    duplicate is where a contradiction lives. Both are validated against the
    parent Tab's capo by `Breakdown._resolve_sounding_names`, because that is the
    first object that holds the chords and the capo at once.

    Song-level `key` stays ABSOLUTE: Wonderwall is in F# minor even though every
    shape on screen comes from E minor.
    """
    name: str
    sounding_name: Optional[str] = None  # absolute — "F#m7"; set iff capo > 0
    positions: list[ChordPosition]
    barre_fret: Optional[int] = None   # fret number for full/partial barre
    base_fret: int = 1                 # starting fret of diagram window (needed for SVG y-offset)


class TechniqueNote(BaseModel):
    """A technique tip or instructional callout."""
    heading: str
    body: str


# FLE-72 — tap-count budget for one drill run.
#
# The mobile drill screen (mobile/src/app/breakdown/[songId]/drill/[drillIndex].tsx,
# advanceRepOrTempo) walks the ladder in ONE sitting: `repetitions` taps at every
# 5-BPM rung from start_bpm to target_bpm, and the rating pills only appear once the
# top rung is finished. So the taps a user owes before they can rate is
#
#     rungs x repetitions   where rungs = (target_bpm - start_bpm) / 5 + 1
#
# Under the pre-FLE-72 bounds (repetitions 8-30, span guidance 10-40 BPM) that was up
# to 9 x 30 = 270 taps, and an ordinary breakdown measured 72-96 (FLE-56, song 71
# "Pride and Joy"). These two constants are the whole fix: 4 rungs x 12 reps caps the
# worst case at 48 taps, and the typical 10-BPM/8-rep drill lands at 24.
MAX_LADDER_SPAN_BPM = 15        # 4 rungs at RUNG_STEP_BPM=5, counting start_bpm
MIN_DRILL_REPETITIONS = 6       # == app.sessions.plan.MIN_PLANNED_REPS, deliberately
MAX_DRILL_REPETITIONS = 12


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

    FLE-72 tap budget: `repetitions` and the start→target span are CLAMPED, not
    rejected (see MAX_LADDER_SPAN_BPM above and the two validators below), because
    that soft-fail is all-or-nothing per song — one off-guidance number would cost
    the user every drill in the breakdown.

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
            "True if the drill only makes sense anchored to THIS song's actual "
            "passage — it would mean nothing detached from this recording. False if "
            "it teaches a foundational mechanic any guitarist could practice "
            "regardless of song; the passage named in `what` is just the example "
            "that motivated it. Do not base this on whether `what` happens to "
            "contain the song's title — `what` always names a passage (see below), "
            "so that text is not a reliable signal either way."
        ),
    )
    what: str = Field(
        ...,
        description=(
            "1-2 sentences explaining what the user does. MUST name the passage "
            "this drill prepares — which moment of the song earns it, e.g. 'the "
            "bridge's barre run' — on every drill, not only song_specific ones. "
            "MUST describe the same exercise the tab_snippet actually produces: do "
            "not promise a chord, barre, voicing or strum unless the snippet sounds "
            "more than one string at once."
        ),
    )
    # FLE-45: the ONE ordinal the drill order is graded on.
    #
    # Optional[int] rather than required, for the same backward-compat reason
    # `Breakdown.drills` is default_factory=list: rows cached before this field
    # existed have no `mechanic_tier` key, and a required field would make every
    # pre-FLE-45 breakdown fail to read back. Fresh Sonnet output omitting it is
    # therefore not a parse error — scripts/grade_eval.py fails gate L5 on a
    # missing tier instead, which is the right split. Ordering is a quality
    # signal the eval reports; it is not a correctness guarantee code can
    # provide, because reordering drills here would silently mask the model's
    # error and the tier is self-declared in the first place.
    mechanic_tier: Optional[int] = Field(
        None,
        ge=1,
        le=6,
        description=(
            "Which tier of the MECHANIC TIERS list in the system prompt this drill's "
            "hardest moment sits in: 1 single sustained note, 2 two-string alternation, "
            "3 held shape struck, 4 shape change, 5 shape change with position shift, "
            "6 polyphonic independence. Drills MUST be emitted in non-decreasing tier "
            "order. Equal tiers are allowed and are correct whenever a later drill "
            "trades one difficulty for another rather than adding one."
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
        description="Stretch tempo. Multiple of 5. Between 10 and 15 BPM higher than start_bpm.",
    )
    repetitions: int = Field(
        ...,
        ge=MIN_DRILL_REPETITIONS,
        le=MAX_DRILL_REPETITIONS,
        description=(
            f"Reps per tempo step. Between {MIN_DRILL_REPETITIONS} and "
            f"{MAX_DRILL_REPETITIONS}."
        ),
    )
    success_criterion: str = Field(
        ...,
        description="One sentence — what 'unlocked' sounds like. Fletcher voice.",
    )
    common_trap: Optional[str] = Field(
        None,
        description="Optional. One sentence about the mistake beginners make on this mechanic.",
    )

    @field_validator("repetitions", mode="before")
    @classmethod
    def _clamp_repetitions(cls, v: object) -> object:
        """FLE-72: pull an off-guidance rep count into range instead of rejecting it.

        CLAMP, not raise, because the cost of raising here is not "this drill is
        dropped" — it is "every drill for this song is dropped". breakdown.py's
        Landmine #3 soft-fail catches a ValidationError anywhere under `drills` by
        stripping the whole `drills` key and re-parsing, so one rep count of 20
        would cost the user all 2-4 drills. Narrowing the bound from 8-30 to 6-12
        makes that outcome LIKELY, not hypothetical: Sonnet reaches for round
        numbers, and 16 and 20 were both inside the old window.

        The ge/le on the field stays as the number Sonnet is ASKED for — it is what
        model_json_schema() ships in the tool definition — and this clamp is the
        backstop for when it answers with something else. mode="before" means it
        runs ahead of the int + ge/le core validation, so the constraint is never
        the thing that fires. Non-int input still falls through to Pydantic's own
        type error.
        """
        if isinstance(v, bool) or not isinstance(v, int):
            return v
        clamped = min(max(v, MIN_DRILL_REPETITIONS), MAX_DRILL_REPETITIONS)
        if clamped != v:
            logger.warning(
                "Drill.repetitions %d is outside the FLE-72 tap budget — clamped to %d.",
                v, clamped,
            )
        return clamped

    @model_validator(mode="after")
    def _target_bpm_above_start(self) -> "Drill":
        """W2 fix: target_bpm must be strictly greater than start_bpm.

        Still a raise, not a clamp: equal or inverted bpms mean Sonnet emitted a
        zero-progress ladder, and there is no honest repair for that — any target we
        invented would be our tempo judgement, not the model's. The FLE-72 span cap
        below only ever moves target_bpm DOWN toward start_bpm, so it can never
        create the violation this check rejects.
        """
        if self.target_bpm <= self.start_bpm:
            raise ValueError(
                f"target_bpm ({self.target_bpm}) must be strictly greater than "
                f"start_bpm ({self.start_bpm})"
            )
        return self

    @model_validator(mode="after")
    def _cap_ladder_span(self) -> "Drill":
        """FLE-72: cap start->target at MAX_LADDER_SPAN_BPM so a ladder is <=4 rungs.

        Clamped for the same reason as _clamp_repetitions — rejecting an 80-BPM
        stretch would cost the song all of its drills, and a drill whose stretch is
        trimmed to +15 is still a usable drill. target_bpm comes down rather than
        start_bpm going up: start_bpm is the warmup tempo Sonnet chose for this
        specific mechanic and is load-bearing for the drill being playable at all,
        while target_bpm is the stretch, which is exactly what we are budgeting.

        Runs AFTER _target_bpm_above_start (definition order), so `self.target_bpm >
        self.start_bpm` already holds and the clamped value stays above start_bpm:
        the ceiling is start_bpm + 15, and any value strictly above start_bpm that
        exceeds the ceiling lands ON the ceiling.
        """
        ceiling = self.start_bpm + MAX_LADDER_SPAN_BPM
        if self.target_bpm > ceiling:
            logger.warning(
                "Drill ladder span %d->%d BPM exceeds the FLE-72 budget of %d BPM — "
                "target_bpm clamped to %d.",
                self.start_bpm, self.target_bpm, MAX_LADDER_SPAN_BPM, ceiling,
            )
            self.target_bpm = ceiling
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
    # FLE-128: provenance of the tuning used when generating this breakdown.
    # "catalog" = matched a song_catalog row; "default" = no row, used standard.
    # None for cached rows generated before this field was added.
    tuning_source: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _inherit_tab_setup(cls, data: object) -> object:
        """§7.1.4 — a drill snippet INHERITS capo/tuning; it never defaults them.

        The highest-value catch of Fret's review, and neither of us named it in
        the first draft. `Tab` is embedded in `Drill.tab_snippet`, and `capo`
        defaults to 0. A drill lifted from a capo-2 song whose snippet takes that
        default is two semitones wrong WITH NO ERROR ANYWHERE — the notes are
        well-formed, the meter sums, and the verifier derives a pitch that is
        simply the wrong one. FLE-102's T8 with a new cause, and the DEFAULT is
        what makes it invisible. `tuning` has the same shape and the same
        exposure today.

        Inheritance happens here, before field validation, so it covers both
        inbound paths that matter: a Sonnet tool-call payload and a cached JSONB
        row. A snippet that states its own capo/tuning is left alone and then has
        to match — see `_assert_drill_setup_matches`. Cached rows carry neither
        key on either tab, so both sides default to 0/standard and agree.
        """
        if not isinstance(data, dict):
            return data
        tab = data.get("tab")
        drills = data.get("drills")
        if not isinstance(tab, dict) or not isinstance(drills, list):
            return data
        inheritable = {k: tab[k] for k in ("capo", "tuning") if k in tab}
        if not inheritable:
            return data
        patched, changed = [], False
        for d in drills:
            snippet = d.get("tab_snippet") if isinstance(d, dict) else None
            if isinstance(snippet, dict):
                missing = {k: v for k, v in inheritable.items() if k not in snippet}
                if missing:
                    d = {**d, "tab_snippet": {**snippet, **missing}}
                    changed = True
            patched.append(d)
        return {**data, "drills": patched} if changed else data

    @model_validator(mode="after")
    def _assert_drill_setup_matches(self) -> "Breakdown":
        """§7.1.4 — every drill snippet's capo/tuning equals the parent tab's.

        Raises: a snippet that disagrees with its own song's setup is a
        contradiction with no honest repair — silently overwriting it would hide
        exactly the two-semitone error this check exists to surface. Under
        `drills` the cost is bounded: breakdown.py's Landmine #3 soft-fail strips
        the drills key and re-parses, so the user still gets tab/chords/notes.
        """
        for i, d in enumerate(self.drills):
            if d.tab_snippet.capo != self.tab.capo:
                raise ValueError(
                    f"drill {i} ({d.name!r}): tab_snippet.capo {d.tab_snippet.capo} "
                    f"!= song tab capo {self.tab.capo} — a snippet inherits its "
                    f"parent's capo, it never defaults one (§7.1.4)"
                )
            if d.tab_snippet.tuning != self.tab.tuning:
                raise ValueError(
                    f"drill {i} ({d.name!r}): tab_snippet.tuning "
                    f"{d.tab_snippet.tuning} != song tab tuning {self.tab.tuning} "
                    f"(§7.1.4)"
                )
        return self

    @model_validator(mode="after")
    def _resolve_sounding_names(self) -> "Breakdown":
        """§7.2 — `sounding_name` is set iff capo > 0, and it must be the truth.

        Three behaviours, in the order they are hit:

        - capo == 0 and a sounding_name is set -> RAISE. At capo 0 the shape and
          the sound are the same chord, so a second name is either a duplicate or
          a contradiction, and there is no way to tell which.
        - capo > 0 and no sounding_name -> DERIVE it. The value is mechanically
          determined (`name` transposed up `capo`, quality unchanged), so filling
          it is not a guess, and leaving it empty is what strands the player who
          is trying to play along with the record.
        - capo > 0 and set -> check it. Compared by PITCH CLASS, so F#m7 and Gbm7
          both pass. Wonderwall checks out: Em7/G/D/A7sus4 -> F#m7/A/E/B7sus4.

        A name this module cannot parse as root+suffix (a slash-less oddity, a
        nickname) is left alone with a warning rather than rejected — chord
        naming is open vocabulary and refusing to serve a song over it would be
        the §8.2 mistake.
        """
        capo = self.tab.capo
        for ch in self.chords:
            if capo == 0:
                if ch.sounding_name:
                    raise ValueError(
                        f"chord {ch.name!r}: sounding_name {ch.sounding_name!r} is "
                        f"set at capo 0, where the shape and the sound are the "
                        f"same chord (§7.2)"
                    )
                continue
            if ch.sounding_name is None:
                derived = transpose_chord_name(ch.name, capo)
                if derived is None:
                    logger.warning(
                        "Chord %r cannot be transposed — leaving sounding_name "
                        "unset at capo %d (FLE-115 §7.2).", ch.name, capo,
                    )
                else:
                    ch.sounding_name = derived
                continue
            verdict = _is_transposition(ch.name, ch.sounding_name, capo)
            if verdict is None:
                logger.warning(
                    "Chord %r / sounding_name %r cannot both be parsed — "
                    "transposition unchecked at capo %d (FLE-115 §7.2).",
                    ch.name, ch.sounding_name, capo,
                )
            elif not verdict:
                raise ValueError(
                    f"chord {ch.name!r}: sounding_name {ch.sounding_name!r} is not "
                    f"{ch.name!r} transposed up {capo} semitones with the quality "
                    f"unchanged (expected {transpose_chord_name(ch.name, capo)!r}) "
                    f"(§7.2)"
                )
        return self


# The keys a persisted JSONB snapshot must carry to be a renderable Breakdown.
# `drills` is deliberately absent — it has a default_factory, so pre-4.1 cached
# breakdowns are still complete. Derived from the model so adding a required
# field to Breakdown cannot silently desync this set. See FLE-67.
_REQUIRED_BREAKDOWN_KEYS = frozenset(
    name for name, f in Breakdown.model_fields.items() if f.is_required()
)


def is_renderable_breakdown(v: object) -> bool:
    """True when a persisted `songs.breakdown` value is a full breakdown snapshot.

    FLE-67: the inverse of what SongResponse.coerce_placeholder_breakdown nulls
    out. Callers that report breakdown availability to the client should consult
    this alongside `breakdown_generated_at` so they never advertise a breakdown
    that the response body had to drop.
    """
    return isinstance(v, dict) and _REQUIRED_BREAKDOWN_KEYS.issubset(v)


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
# FLE-99 reworded the live prompt's SONG_SPECIFIC paragraph away from the
# "if `what` contains the title" test quoted above — every drill now names its
# passage, so that substring would otherwise fire on nearly everything. This
# function's test below is unaffected: naming a passage ("the bridge's barre
# run") does not require restating the song title or artist, so the false→true
# backstop still only fires on drills that actually spell one of those out.
#
# This is NOT a validator on Drill: Drill does not carry the song title/artist,
# and Breakdown does not either. Threading them into the model purely to satisfy
# this rule would make Drill un-reusable for every other caller. A free function
# applied by whoever HAS the song context keeps Drill a plain data shape.

def _names_song(text: str, song_title: str, song_artist: str) -> bool:
    """True if `text` mentions the song title or the artist, case-insensitively.

    Plain substring test in both fields. This USED to be the same test gate L4
    applied, so that the enforced value and the graded value could not drift;
    FLE-123 retired L4's substring IFF because the prompt forbids it in as many
    words ("Do not scan `what` for that text"). The test survives HERE on purpose:
    it is a one-way correctness backstop (see `enforce_song_specific`), not a
    judgement about what the flag means, and a drill naming the song in `what` is
    song-specific whatever else is true. It is no longer mirrored by any gate.

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
    left alone. Clearing the flag would mean deciding that a drill saying "the
    opening riff" or "the turnaround in the outro" is not about the song, which is
    exactly the kind of semantic judgement this function exists to avoid making.
    The false→true direction is a correctness guarantee, and only it belongs in code.

    FLE-123: this docstring used to add "Gate L4 grades the flag as an IFF, so that
    direction can still fail the eval — deliberately." That is no longer true, and
    it was never a good deal. Because this function forces the flag true whenever
    `what` names the song, L4's false→true half could never fail; its only live half
    punished the true→false case, which NAME THE PASSAGE now produces routinely (a
    drill correctly flagged true for "the intro's hammer-on lick" contains no
    title). L4 is now a notes-based derivation check and does not grade the
    true→false direction at all — so nothing downstream reports it. That direction
    is unenforced by design, here and in the eval.

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
        """Coerce not-yet-generated JSONB dicts to None (Grace-E, restored 2026-09-08).

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

        FLE-67 (2026-09-24): the `"placeholder" in v` guard was too narrow. Prod
        song 73 carried a bare `{}` — no `placeholder` key, so it fell through to
        Breakdown and 500'd GET /api/v1/song-of-day with three missing-field errors.
        Before FLE-54 that self-healed (the next GET recomputed a different pick);
        once the day's pick is persisted, the 500 is permanent for that user-day.
        The test is now the positive one — a dict is a breakdown only if it carries
        every required key — so ANY partial or empty snapshot degrades to "not
        generated yet" instead of 500ing the app's main screen. That matches what
        the client already renders for a song whose breakdown is pending, and it
        stays consistent with TodaySongResponse.breakdown_available, which now
        requires a renderable snapshot as well as breakdown_generated_at.
        """
        if isinstance(v, dict) and not _REQUIRED_BREAKDOWN_KEYS.issubset(v):
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

    remaining: calls left today (0..cap).
    cap: per-user cap — 5 for the breakdown feature per FLE-92, unless
         FLETCHER_CAP_BREAKDOWN overrides it for this deploy.
    resets_at: ISO datetime string — the user's NEXT LOCAL MIDNIGHT in UTC,
         server-authoritative.

    FLE-92 changed what resets_at means without changing its type, so a client
    older than the server still parses it. It used to be oldest_call_in_window + 7
    days, which moved as calls aged out; it is now a fixed daily boundary. An old
    client rendering it as "come back in N days" reads a few hours and rounds to 1 —
    wrong in wording, not in substance — which is why the mobile copy moved to
    naming midnight in the same change.
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
    breakdown_quota: Phase 4 — carries remaining/cap/resets_at for the quota chip (D-06).
                     Null only when the cap is switched off via FLETCHER_CAP_BREAKDOWN,
                     which the client reads as "no chip, CTA always enabled".
    """
    song: SongResponse
    breakdown_available: bool
    from_bank: bool
    bank_source: Optional[Literal["user_bench", "seed_catalog"]] = None
    rerolled: bool
    rated: Optional[TodayRatingInfo] = None
    rerolls_left: int  # 0 or 1 — always populated (never null)
    breakdown_quota: Optional[BreakdownQuota] = None  # null only when the cap is switched off

    model_config = {"from_attributes": False}
