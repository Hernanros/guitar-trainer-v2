"""FLE-106 — generator verification pass: recording-free mechanical checks run
on every breakdown before it is persisted.

FLE-126 — every check below used to read `breakdown.tab` only; nothing read
`drills[].tab_snippet`, even though a snippet is the same Measure/Beat/Note
structure as the tab and is the thing the player actually practises. That let
Little Wing drill1 ship an E9 voicing labelled E7#9 (no G, the #9) with its
coaching copy naming the wrong degrees — eight checks below, zero of them
looking at the one place the player's fingers go.

METER, UNPLAYABLE and FRET_SPAN are notation/physical checks with no bar
classifier dependency, so they now run over every drill's `tab_snippet` too,
unconditionally, same as the main tab (see `verify()`). A fourth check,
DRILL_CHORD, is new: a drill's `name` sometimes IS the chord claim ("E ->
E7#9 Colour Swap"), and when it manifestly is, the snippet must not sound a
pitch foreign to the chord(s) the name names — the same OWN_CHORDS machinery,
just sourced from the name instead of `breakdown.chords`. It is deliberately
NOT the V2-third/chord-stab completeness check FLE-122 twice warned against
promoting unconditionally (a boogie has no third; an arpeggio sounds one note
per column) — DRILL_CHORD never requires a chord tone to sound, only that no
sounding note is foreign to it, which is exactly OWN_CHORDS' already-blocking
shape, not V2/T11's never-promoted one. The gate that keeps it off riffs and
arpeggios is `_drill_name_chord_pcs`'s refusal to trust a bare root letter —
see its docstring.

Built on FLE-102's audit (`server/scripts/fle102_tab_audit.py`) and narrowed by
FLE-103's finding against hand-verified ground truth
(`.planning/investigations/261005-fle103-pilot-verified-set.md` §6): T7/C7
cross-song template reuse has a 100% false-positive rate on correct data (two
songs sharing a Cm7 is not plagiarism) and must stay a human prompt, never an
automated gate. It is not implemented here at all — and structurally could not
be a per-write check anyway: it needs every OTHER song's tab to compare
against, which is a corpus-wide batch question (fle102_tab_audit.py's job),
not something one write can answer cheaply.

FLE-107 §6c (`.planning/investigations/261005-fle107-tab-regression-set.md`)
is this file's SECOND warning of that same shape, and for the same reason it
is recorded here rather than only in the issue:

  V2-THIRD (the "chord stab" check: root + third + characteristic tone sound
            SOMEWHERE in a bar assigned a chord) and T11 (the stricter form:
            those three sound TOGETHER in one column) are both absent from
            this module ON PURPOSE. Both need a bar classifier this schema
            does not carry. Fret's regression ground truth
            (`regression_set.json`, scored by `fle107_score_tab.py`) tags
            every bar `kind` (chord / riff / arpeggio / melody),
            `chord_checked` (false on riff bars) and `chord_spelled_over`
            (beat vs bar), and gates `check_chord` on `chord_checked` before
            either rule is allowed to fire. Without that gate: a boogie has
            no third at all — the 1-5-6-b7 figure is the entire content of
            eight bars each of Sweet Home Chicago and Pride and Joy, and a
            third in it would be the defect — and an arpeggio sounds one
            note per column, so a per-beat third check fails every correct
            bar of House of the Rising Sun, 8 for 8. `Measure`/`Beat` carry
            none of `kind`, `chord_checked` or `chord_spelled_over` today, so
            there is nothing in this module's input to gate on; only the
            offline scorer has the classifier, and only because Fret
            hand-authored it. DO NOT port either check into `verify()` as an
            unconditional block until the generator itself emits an
            equivalent per-bar classifier — doing so first fails correct riff
            and arpeggio tab the day it ships, the same false-positive shape
            T7 already demonstrated above. And even where `chord_checked` is
            true, T11 stays partial BY DESIGN: it fires on "never sounded
            whole in one column" and must NOT fire on a split that leaves a
            complete triad in one half, because a multi-beat voicing is
            legitimate. `fle107_score_tab.py --mutation-test` row 10 is the
            form T11 covers; FLE-107 §6c names the form it does not, and
            correctly does not.

Five checks are hard blocks (`verify()` raises when any fire) — each is wrong
against notation or against the guitar itself, no recording needed:

  METER          a measure's beat durations don't sum to its time signature (C1)
  UNPLAYABLE     the same string fretted twice in one beat (C2)
  FRET_SPAN      a fretted span > 5 frets inside one beat — not one hand
                 position (C3)
  SPLIT_CHORD    a 2+-string shape sounded across two disjoint adjacent beats,
                 so the voicing it names never actually sounds whole (C5)
  OWN_CHORDS     a sounding pitch class outside every chord this breakdown
                 itself declares, checked in BOTH tuning frames via
                 tuning_offset() so an Eb/DADGAD song isn't flagged for
                 sounding a semitone below its chord shapes' names. This is
                 C6 from the audit — it alone caught the whole Comfortably
                 Numb error (every C natural where B minor needs C#) purely
                 from the tab contradicting the chords shipped beside it.

Three checks are informational only and never block:

  RENDERER_WIDTH (> 4 beats in one measure) WAS the sixth hard block and was
                 DOWNGRADED by FLE-118 — its whole justification was
                 TabNotation.tsx's fixed MEASURE_WIDTH, which FLE-116 replaced
                 with width-from-content (spec §9.1). Left blocking it would
                 have refused most of the rhythmic variety FLE-118's prompt
                 asks for; FLE-103 §1b measured 43% of prod bars above 4
                 columns, so that is the normal shape of this model's output.
                 See `_check_renderer_width` for the full argument.
  UNIFORM_DURATION (C4, every note in a measure the same length): FLE-102 calls
                 this "a feel judgement, not a notation error on its face." It
                 is real evidence of a flattened rhythm, not grounds to refuse
                 an otherwise-sound breakdown.
  DOUBLE_SWING   (C8, FLE-118 — spec §2.5 rule 2) a non-straight `feel` on a
                 bar ALSO written out as triplet-quarter/triplet-eighth pairs.
                 Swing applied to an already-swung pair gives 4/9 + 2/9. The
                 arithmetic would justify blocking; the unmeasured
                 false-positive rate does not yet — see
                 `_check_double_applied_swing` for why, and
                 `is_double_applied_swing` for the predicate, which
                 fle102_tab_audit.py imports rather than re-deriving.
  PITCH_DEGENERACY (FLE-127) every sounding note in a measure is the same
                 pitch — a bar with no pitch content, orthogonal to C4
                 (C4 is a RHYTHM property, one duration; this is a PITCH
                 property, one pitch class — a bar can trip either, both or
                 neither). Warns rather than blocks because it cannot tell
                 real filler apart from a deliberate single-note pedal tone
                 or tremolo bar; see `_check_pitch_degeneracy` and
                 `.planning/investigations/261005-fle127-pitch-degeneracy-check.md`
                 for the disposition call.

Caveat on the record (FLE-102 "the accuracy number, and why the computable one
is the wrong one"): Kashmir passed 8 of 8 of these mechanical checks and was a
completely invented riff. This module is a floor that catches cheap defects
before a write — it is NOT a measure of whether the tab is the song.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from app.models.song import Breakdown, Drill, Tab, measure_beats, time_signature_beats

# --- pitch model (mirrors fle102_tab_audit.py) -----------------------------
CHROM = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
STD_MIDI = {6: 40, 5: 45, 4: 50, 3: 55, 2: 59, 1: 64}
NOTE_PC = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
    "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10,
    "BB": 10, "B": 11,
}

# FLE-115: the private float `DUR` table that used to live here is gone — it was
# one of three disagreeing copies. app.models.song.DURATION_BEATS is now the one
# table, in exact `Fraction`, and `beat_beats` carries the rest branch (§2.2,
# §3) so this module cannot silently skip a rest column.

# Was a renderer constant mirrored from mobile/src/components/TabNotation.tsx,
# whose MEASURE_WIDTH assumed 4 beats per measure. FLE-116 (§9.1) removed that
# assumption, so this no longer mirrors anything live — it is now only the
# threshold above which a PRE-FLE-116 client draws bars overlapping, which is
# why the check that reads it warns instead of blocking.
_MAX_RENDERABLE_BEATS = 4

# intervals above the root, by chord-name suffix. Superset of fle102_tab_audit's
# QUAL (which matched the cached prod corpus's naming) and fle103's QUALITIES
# (which matched the hand-authored pilot set) — a live gate sees both styles.
QUAL = {
    "maj7": (0, 4, 7, 11), "m7b5": (0, 3, 6, 10), "m7": (0, 3, 7, 10),
    "7#9": (0, 4, 7, 10, 3), "7b9": (0, 4, 7, 10, 1), "7#11": (0, 4, 6, 7, 10),
    "9": (0, 4, 7, 10, 2), "6/9": (0, 2, 4, 7, 9),
    "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9), "sus4": (0, 5, 7),
    "sus2": (0, 2, 7), "5": (0, 7), "dim": (0, 3, 6), "aug": (0, 4, 8),
    "7": (0, 4, 7, 10), "m": (0, 3, 7), "": (0, 4, 7),
}


def _open_midi(tuning: list[str]) -> dict[int, int]:
    """tuning is a 6-list low->high; map to string number 6..1 midi."""
    if not tuning or len(tuning) != 6:
        return dict(STD_MIDI)
    out = {}
    for i, name in enumerate(tuning):
        strnum = 6 - i
        std = STD_MIDI[strnum]
        pc = NOTE_PC.get(name.strip().upper())
        if pc is None:
            out[strnum] = std
            continue
        cands = [pc + 12 * o for o in range(1, 9)]
        out[strnum] = min(cands, key=lambda m: abs(m - std))
    return out


def _pitch(strnum: int, fret: int, om: dict[int, int]) -> tuple[str, int]:
    m = om.get(strnum, STD_MIDI.get(strnum, 40)) + int(fret)
    return f"{CHROM[m % 12]}{m // 12 - 1}", m


def _tuning_offset(tuning: list[str]) -> int | None:
    """semitones the whole guitar is shifted, or None if not a uniform shift.

    Chord NAMES describe shapes ("E (open)"), which in Eb tuning sound a
    semitone lower than the name. A note must be checked in both frames
    before it can be called wrong; only a note outside BOTH is real. DADGAD
    is not a uniform shift, so no second frame exists.
    """
    om = _open_midi(tuning)
    deltas = {om[s] - STD_MIDI[s] for s in range(1, 7)}
    return deltas.pop() if len(deltas) == 1 else None


def _chord_pcs(name: str) -> set[int] | None:
    """pitch classes a chord name asserts; None if unparseable."""
    s = (name or "").split("(")[0].strip().replace("minor", "m").replace("major", "maj")
    if not s:
        return None
    root = s[0].upper()
    i = 1
    if i < len(s) and s[i] in "#b":
        root += s[i]
        i += 1
    pc = NOTE_PC.get(root.upper())
    if pc is None:
        return None
    suf = s[i:].strip()
    if suf.lower().startswith("m") and not suf.lower().startswith("maj"):
        key = "m7" if "7" in suf else ("m6" if "6" in suf else "m")
    else:
        key = next(
            (k for k in ("maj7", "m7b5", "7#9", "7b9", "7#11", "sus4", "sus2",
                         "dim", "aug", "9", "6/9", "7", "6", "5") if k in suf.lower()),
            "",
        )
    return {(pc + iv) % 12 for iv in QUAL[key]}


@dataclass
class VerificationResult:
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocking


def _check_meter(tab: Tab) -> list[str]:
    """C1 — exact, under FLE-115 §2.2. No tolerance, because there is no error.

    The dropped `abs(total - want) > 1e-6` was not fixing a live defect — see
    the note on DURATION_BEATS in app/models/song.py; the float sums it existed
    to absorb were measured to be exact, and 1e-6 was far too small to hide any
    expressible shortfall. It goes because with `Fraction` there is nothing left
    for it to do, and a tolerance nobody can state the purpose of is a liability.

    The behaviour change that DOES matter here is the rest branch: a rest column
    counts toward the bar (`beat_beats`), where the old code saw a beat with no
    notes, set `unknown`, and skipped the measure's check entirely — so a bar of
    rests was never metered at all.
    """
    bad = []
    for mi, m in enumerate(tab.measures, 1):
        ts = m.time_signature or "4/4"
        want = time_signature_beats(ts)
        if any(not bt.notes and bt.rest is None for bt in m.beats):
            continue  # nothing sounding and nothing silent — no length to sum
        total = measure_beats(m)
        if total != want:
            bad.append(
                f"m{mi}: durations sum to {float(total):g} beats, bar is {ts} "
                f"({float(want):g})"
            )
    return bad


def _check_unplayable(tab: Tab) -> list[str]:
    bad = []
    for mi, m in enumerate(tab.measures, 1):
        for bi, bt in enumerate(m.beats, 1):
            seen = Counter(n.string for n in bt.notes)
            dup = [s for s, c in seen.items() if c > 1]
            if dup:
                bad.append(f"m{mi} b{bi}: string(s) {dup} fretted twice in one beat")
    return bad


def _check_fret_span(tab: Tab) -> list[str]:
    bad = []
    for mi, m in enumerate(tab.measures, 1):
        for bi, bt in enumerate(m.beats, 1):
            fr = [n.fret for n in bt.notes if n.fret > 0]
            if fr and max(fr) - min(fr) > 5:
                bad.append(f"m{mi} b{bi}: fretted span {min(fr)}-{max(fr)} = {max(fr) - min(fr)} frets")
    return bad


def _check_split_chord(tab: Tab) -> list[str]:
    bad = []
    for mi, m in enumerate(tab.measures, 1):
        beats = m.beats
        for bi in range(len(beats) - 1):
            a = {n.string: n.fret for n in beats[bi].notes}
            b = {n.string: n.fret for n in beats[bi + 1].notes}
            if len(a) >= 2 and len(b) >= 2 and not (set(a) & set(b)) and len(set(a) | set(b)) >= 5:
                bad.append(f"m{mi} b{bi + 1}/b{bi + 2}: disjoint halves of one {len(a) + len(b)}-string voicing")
    return bad


def _check_renderer_width(tab: Tab) -> list[str]:
    """RENDERER_WIDTH — DOWNGRADED TO A WARNING BY FLE-118. See below.

    This was a hard block, and its entire justification was a renderer constant
    that no longer exists: FLE-116 (commit `0b8f2ee`, spec §9.1) replaced
    TabNotation's fixed `MEASURE_WIDTH` with width-from-content, and the
    component now says in as many words that "a 4/4 bar of straight sixteenths
    no longer overflows its box."

    Left blocking, it would have made FLE-118 actively harmful rather than
    merely incomplete. The RHYTHM, RESTS and ARTICULATION blocks this issue
    added to SYSTEM_PROMPT teach sixteenths, triplets and rests — a 4/4 bar of
    plain eighths is already 8 columns — so the gate would have refused, at
    write time, most of the rhythmic variety the prompt now asks for. FLE-103
    §1b measured 43% of prod bars at more than 4 columns, so this is the
    historically NORMAL shape of the model's output, not an edge case.

    Why a warning rather than deletion: a client older than FLE-116's bundle
    still draws these bars overlapping. But that is a client-version concern
    with a self-healing fix (the next OTA), whereas refusing the write loses the
    breakdown outright and hands the user nothing. A server must not destroy
    correct music to protect an un-updated client. The real constraint on how
    many columns a bar may hold is METER (C1), which is exact.
    """
    return [
        f"m{mi}: {len(m.beats)} beats > {_MAX_RENDERABLE_BEATS} — renders "
        f"overlapped on a client older than FLE-116's width-from-content bundle"
        for mi, m in enumerate(tab.measures, 1)
        if len(m.beats) > _MAX_RENDERABLE_BEATS
    ]


def _check_foreign_notes(tab: Tab, allowed: set[int]) -> dict[int, list[str]]:
    """Notes in `tab` whose pitch class is outside `allowed` in every tuning
    frame. Shared machinery: OWN_CHORDS sources `allowed` from
    `breakdown.chords`, DRILL_CHORD (FLE-126) from a drill's own name — same
    check, different source for what counts as "this breakdown's own".

    # FLE-115 §7.1/§7.2 — the capo term is DELIBERATELY ABSENT here, and that is
    # not the defect §7.1.2 warns about. The absolute sounding pitch is
    # open + capo + fret, but this check scores notes against `Chord.name`,
    # which in v2 is THE SHAPE AS FINGERED (Wonderwall's first chord is `Em7`,
    # sounding F#m7). Both sides of the comparison are therefore in the
    # capo-relative frame and the capo cancels; adding it would score the notes
    # absolute against shape names and fail every capo song. A verifier that
    # scores against an ABSOLUTE chord name — fle103_verify_pilot_tabs.py, whose
    # hand-authored ground truth predates `sounding_name` — does need the term.
    """
    om = _open_midi(tab.tuning)
    off = _tuning_offset(tab.tuning)
    frames = [0] if not off else [0, -off]
    bad: dict[int, list[str]] = {}
    for mi, m in enumerate(tab.measures, 1):
        for bi, bt in enumerate(m.beats, 1):
            for n in bt.notes:
                # FLE-115 §5: a bent note does not sound at an integer fret.
                # Scoring the fingered pitch flags every correct bend as a
                # foreign note, so skip it rather than score it wrong.
                if n.bend is not None:
                    continue
                nm, midi = _pitch(n.string, n.fret, om)
                if all((midi + f) % 12 not in allowed for f in frames):
                    bad.setdefault(mi, []).append(f"b{bi} {nm}")
    return bad


def _check_own_chords(breakdown: Breakdown) -> list[str]:
    allowed: set[int] = set()
    for c in breakdown.chords:
        pcs = _chord_pcs(c.name)
        if pcs is not None:
            allowed |= pcs
    if not allowed:
        # No parseable declared chords at all — nothing to check against.
        return []
    bad = _check_foreign_notes(breakdown.tab, allowed)
    chord_names = sorted({c.name for c in breakdown.chords})
    return [
        f"m{mi}: notes {', '.join(notes)} outside this breakdown's own declared "
        f"chords {chord_names}"
        for mi, notes in sorted(bad.items())
    ]


# --- DRILL_CHORD (FLE-126): does a drill's own NAME match its snippet? -----
# Longest-suffix-first so alternation prefers "7#9" over "7" at the same
# position — mirrors _chord_pcs's own `next(k for k in (...) if k in suf)`
# longest-meaningful-match intent, just as a regex instead of a scan.
_CHORD_QUALITIES = sorted((k for k in QUAL if k), key=len, reverse=True)
_CHORD_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-G](?:#|b)?(?:"
    + "|".join(re.escape(q) for q in _CHORD_QUALITIES)
    + r")?(?![A-Za-z0-9])"
)


def _drill_name_chord_pcs(name: str) -> set[int]:
    """Pitch classes every chord symbol in a drill's NAME asserts, unioned —
    or the empty set if the name doesn't unambiguously name one.

    A bare root letter ('Held E Voicing') is cheap to mistake for a stray
    capital — a string name, an article, a key-of-the-song mention — so by
    itself it earns no trust. FLE-122 is two warnings about exactly this
    false-positive shape for a chord check (V2-third's boogie, T11's
    arpeggio). The bar here: require at least one token with an accidental
    or quality suffix — unambiguously a chord, not a letter — before
    trusting ANY token in the name. Once that bar clears, a bare root in the
    SAME name is trusted too: "E -> E7#9" should check against {E} union
    {E7#9}, not only the complex one.
    """
    tokens = _CHORD_TOKEN_RE.findall(name or "")
    if not any(len(t) > 1 for t in tokens):
        return set()
    allowed: set[int] = set()
    for t in tokens:
        pcs = _chord_pcs(t)
        if pcs is not None:
            allowed |= pcs
    return allowed


def _check_drill_chord_identity(drill: Drill) -> list[str]:
    """DRILL_CHORD — a drill named after a chord must actually voice it.

    The shipped defect (Little Wing drill1, "E -> E7#9 Colour Swap"): the
    second voicing sounds {E, G#, B, D, F#} — E9, not E7#9, because F# (the
    plain 9th) stands where G (the #9 the name promises) belongs. F# is
    foreign to E7#9's own pitch classes {E, G#, B, D, G}, so the same
    foreign-note machinery that already blocks OWN_CHORDS catches it here —
    this is NOT the V2-third/T11 "did every chord tone sound" completeness
    check; it never requires a tone to sound, only that none sounding is
    foreign. See `_drill_name_chord_pcs` for the gate that keeps this off
    drills whose name doesn't actually name a chord.
    """
    allowed = _drill_name_chord_pcs(drill.name)
    if not allowed:
        return []
    bad = _check_foreign_notes(drill.tab_snippet, allowed)
    return [
        f"m{mi}: notes {', '.join(notes)} outside the chord {drill.name!r} names"
        for mi, notes in sorted(bad.items())
    ]


def _check_uniform_duration(tab: Tab) -> list[str]:
    """C4 — informational, never blocking (see module docstring)."""
    warn = []
    for mi, m in enumerate(tab.measures, 1):
        alld = {n.duration for bt in m.beats for n in bt.notes}
        if len(alld) == 1 and len(m.beats) > 2:
            warn.append(f"m{mi}: every note is {alld.pop()!r} — a grid, not a rhythm")
    return warn


def _check_pitch_degeneracy(tab: Tab) -> list[str]:
    """PITCH_DEGENERACY (FLE-127) — informational, never blocking.

    A measure whose every sounding note is the same pitch has no pitch
    content: nothing in it is worth reading as music rather than a strike
    count. The shipped defect (song 107, *Eruption*, m1): eight strikes of
    the open high E, not in the record and not a practisable bar.

    C4 (`_check_uniform_duration`) cannot catch this — on that same tab it
    fires on 7 of the other 8 bars too, all legitimate tapping and
    pentatonic runs in straight eighths, because C4 measures a RHYTHM
    property (one note LENGTH across the bar), not a pitch one. A warning
    that fires on 7/8 bars cannot discriminate the filler bar from the real
    ones. Distinct-PITCH count is the orthogonal signal: on that tab it
    fires on m1 alone.

    Warns rather than blocks for the same reason C4 does, and by the same
    owner call as FLE-122's V2/T11 decision: it cannot tell real filler
    apart from a deliberate single-note pedal tone or tremolo bar, a rare
    but legitimate choice, and that false-positive rate is unmeasured
    against real generator output — see
    `.planning/investigations/261005-fle127-pitch-degeneracy-check.md`.

    Bent notes are excluded from the pitch set for the same reason
    `_check_foreign_notes` excludes them: a bend doesn't sound at its
    fingered, integer fret, and this module has no target-pitch field to
    score it at instead.

    Scoped to the main tab only, same as C4/C8/RENDERER_WIDTH — not run
    over drill snippets, where a single sustained or pedalled pitch is
    often the entire teaching point.
    """
    om = _open_midi(tab.tuning)
    warn = []
    for mi, m in enumerate(tab.measures, 1):
        midis = [
            _pitch(n.string, n.fret, om)[1]
            for bt in m.beats
            for n in bt.notes
            if n.bend is None
        ]
        if len(midis) > 1 and len(set(midis)) == 1:
            warn.append(
                f"m{mi}: all {len(midis)} notes are {CHROM[midis[0] % 12]} — "
                f"no pitch content unless this is a deliberate pedal/tremolo bar"
            )
    return warn


# --- C8: double-applied swing (FLE-118, spec §2.5 rule 2) ------------------
# THE ONE IMPLEMENTATION. fle102_tab_audit.py imports this predicate as its C8
# rather than carrying a second copy — the spec asked for the check to exist
# once, and a duplicated rhythm heuristic is how two scripts start disagreeing
# about what a defect is (the three duration tables FLE-115 collapsed).
#
# It takes plain duration STRINGS rather than a Tab so both callers can reach
# it: the write gate holds parsed `Tab` objects, the corpus audit holds raw
# JSONB dicts and deliberately does not parse them (parsing would repair the
# very defects the audit counts).
_SWUNG_PAIR = ("triplet-quarter", "triplet-eighth")


def is_double_applied_swing(feel: str, durations: list) -> bool:
    """§2.5 — is this bar a swing feel written out AS a triplet pair as well?

    `feel` applies to what is written, so a reader who swings an already-swung
    triplet pair gets 4/9 + 2/9 of a beat. That is not a stylistic preference,
    it is a bar that cannot be performed as notated by anyone reading it
    correctly.

    Returns True only when BOTH hold:
      - `feel` is not "straight" — the bar already carries the instruction, and
      - every column is `triplet-quarter` or `triplet-eighth`, alternating, in
        whole long-short (or short-long) pairs.

    THE `feel != "straight"` CONDITION IS LOAD-BEARING — do not drop it to make
    the check "catch more". It is what exempts Fret's one legitimate case: a
    drill whose teaching object IS the subdivision ("feel the triplet underneath
    the shuffle") is written in literal triplets with `feel: "straight"` for
    that snippet, and is correct. Triplets also stay legal whenever
    `feel != "straight"` on their own — they then mean a REAL triplet against
    the prevailing feel, such as a fill. It is specifically the all-pairs bar
    that has no such reading.
    """
    if (feel or "straight") == "straight":
        return False
    ds = list(durations)
    if len(ds) < 2 or len(ds) % 2:
        return False
    if any(d not in _SWUNG_PAIR for d in ds):
        return False
    return all(a != b for a, b in zip(ds, ds[1:]))


def _check_double_applied_swing(tab: Tab) -> list[str]:
    """C8 — informational, never blocking. See `is_double_applied_swing`.

    Why this warns rather than blocks, which is a weaker disposition than the
    arithmetic argument alone would justify: no cached breakdown carries a
    `feel` field at all (they all predate v2), so this check has never been run
    against real generator output and its false-positive rate is unmeasured.
    FLE-103 §6 is the precedent that settles it — C7 cross-song template reuse
    read as obviously sound and turned out to have a 100% false-positive rate on
    hand-verified correct data. Promote this to `blocking` once there is output
    to measure it against, not before.
    """
    warn = []
    for mi, m in enumerate(tab.measures, 1):
        durations = [
            b.rest if b.rest is not None else (b.notes[0].duration if b.notes else None)
            for b in m.beats
        ]
        if is_double_applied_swing(tab.feel, durations):
            warn.append(
                f"m{mi}: feel is {tab.feel!r} and the bar is written as "
                f"triplet-quarter/triplet-eighth pairs — swing applied twice "
                f"gives 4/9 + 2/9. Mark the feel and write plain eighths."
            )
    return warn


def verify(breakdown: Breakdown) -> VerificationResult:
    """Run every recording-free check against one freshly-generated breakdown."""
    tab = breakdown.tab
    blocking: list[str] = []
    blocking += [f"METER: {d}" for d in _check_meter(tab)]
    blocking += [f"UNPLAYABLE: {d}" for d in _check_unplayable(tab)]
    blocking += [f"FRET_SPAN: {d}" for d in _check_fret_span(tab)]
    blocking += [f"SPLIT_CHORD: {d}" for d in _check_split_chord(tab)]
    blocking += [f"OWN_CHORDS: {d}" for d in _check_own_chords(breakdown)]

    # FLE-126 — METER/UNPLAYABLE/FRET_SPAN carry no bar-classifier dependency
    # (unlike V2-third/T11, see module docstring), so they run over every
    # drill's tab_snippet unconditionally, same as the main tab. DRILL_CHORD
    # is gated per-drill by `_drill_name_chord_pcs`.
    for i, d in enumerate(breakdown.drills):
        snippet = d.tab_snippet
        label = f"drill {i} ({d.name!r})"
        blocking += [f"METER: {label} {m}" for m in _check_meter(snippet)]
        blocking += [f"UNPLAYABLE: {label} {m}" for m in _check_unplayable(snippet)]
        blocking += [f"FRET_SPAN: {label} {m}" for m in _check_fret_span(snippet)]
        blocking += [f"DRILL_CHORD: {label} {m}" for m in _check_drill_chord_identity(d)]

    warnings = [f"UNIFORM_DURATION: {d}" for d in _check_uniform_duration(tab)]
    warnings += [f"DOUBLE_SWING: {d}" for d in _check_double_applied_swing(tab)]
    warnings += [f"RENDERER_WIDTH: {d}" for d in _check_renderer_width(tab)]
    warnings += [f"PITCH_DEGENERACY: {d}" for d in _check_pitch_degeneracy(tab)]

    return VerificationResult(blocking=blocking, warnings=warnings)
