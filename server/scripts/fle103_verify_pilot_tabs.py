"""FLE-103 — verify the hand-authored pilot set. Read-only, zero spend, no LLM, no DB.

    server/.venv/bin/python server/scripts/fle103_verify_pilot_tabs.py [set.json]

The argument defaults to the committed pilot set. Pass a path to check a
candidate file before it becomes ground truth (FLE-107 wants that order).

This script is deliberately IGNORANT of the chord shapes in
fle103_build_pilot_set.py. It reads only {string, fret, duration} plus the
declared chord NAME, re-derives every pitch from the tuning array AND THE CAPO,
and asks whether the sounding notes actually spell the chord that is claimed. If
I typed a shape wrong, this is what catches it — the point is that my confidence
is not the evidence.

Checks (unit: the measure, so the number has a stated denominator)
-----------------------------------------------------------------
  V1 foreign note      a sounding pitch class outside the declared chord
  V2 third present     the third is in the voicing (FLE-102 T2: 6 of 10 cached
                       tabs contained no third ANYWHERE across 8 bars)
  V3 characteristic    the tone that defines the quality is present — b5 of a
                       m7b5, #11 of a 7#11, b9 of a 7b9, maj7 of a maj7
  V4 root present      the root is sounded somewhere in the voicing
  V5 meter             beat durations sum to the time signature, EXACTLY
  V6 renderer parity   the renderer's duration table matches §2.2, and the
                       rhythm §9.1 draws is distinguishable (see V6 below)
  V7 one note / string no string sounded twice in one beat (unplayable)
  V8 fret span         fretted span within a beat <= 5 (one hand position)
  V9 feel marking      `tab.feel` is present and legal, agrees with the prose
                       `feel`, and is not double-applied as triplet pairs (C8).
                       Unit: the song. Added after FLE-111/116 shipped the
                       renderer that draws the marking — see `check_feel` for the
                       defect in this file that nothing had been catching.

FLE-117 — what schema v2 changed in here (spec §10 row 4)
---------------------------------------------------------
Three of the four changes exist to stop this script failing CORRECT tab:

  * Capo (§7.1). Pitch is `open_pitch(tuning[string]) + capo + fret`, uniform,
    with no special case for fret 0. Deriving from `tuning` alone scored every
    capo song two semitones flat, so V1-V4 failed across the board on tab that
    was right. Wonderwall, Wagon Wheel and Street Fighting Man are the three in
    the 64 that this applies to.
  * Bent notes are SKIPPED by V1-V4 (§5). A bend sounds `fret + semitones`,
    which is not an integer fret, so scoring the FINGERED pitch would flag
    every correct bend as a foreign note.
  * Exact `Fraction` meter, no tolerance (§2.2, landed with FLE-115).
  * V6 no longer asks "does this measure fit" — §9.1 made measure width a
    function of the measure's own content, so it always fits. It now asks the
    two questions that replaced that one. See `V6_NOTE` below.

WHAT A GREEN RUN DOES NOT PROVE
-------------------------------
All 1612 notes in the pilot set are `quarter` and none carries a capo or a bend,
so a green run exercises the PITCH/HARMONY half and nothing else. It encodes
zero rhythm: as all-quarters ground truth it would score a correctly swung bar
as WRONG. Do not read green here as rhythm coverage — FLE-107 re-notates the
five swing/bossa songs (§2.5.1). The capo and bend paths above are likewise
unexercised by this data and are covered by tests/test_fle103_verifier_v2.py
instead, which is the only evidence that they work.

Plus two set-level reports:
  COVERAGE   for every player_level in [0.20, 1.00], how many of the set fall
             inside the selector's ABS(difficulty - player_level) <= 0.15
             window. A window of 0 is a dead app; a window of 1 is a user who
             sees the same song every day and whose reroll cannot move.
  SHAPE REUSE  measures identical across two songs. Reported as INFORMATIONAL,
             never as a failure — see the note at the bottom of main().
"""
import json
import pathlib
import re
import sys
from collections import defaultdict
from fractions import Fraction

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.models.song import (  # noqa: E402  (needs the path fix)
    DURATION_BEATS,
    duration_beats,
)
# V9 imports the ONE C8 predicate rather than reimplementing it. Same reason
# DURATION_BEATS is imported above and not copied: a duplicated rhythm heuristic
# is how two scripts start disagreeing about what a defect is.
from app.ai.tab_verification import is_double_applied_swing  # noqa: E402

CHROM = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
PC = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
      "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10,
      "B": 11}
# FLE-115: the private float duration table that used to live here is gone — it
# was the narrowest of three disagreeing copies (10 values; it rejected
# `sixteenth-triplet` and `thirty-second` as unknown). The one table is
# app.models.song.DURATION_BEATS, reached through `duration_beats()`.

# interval sets from the root. `third` names which interval must be present for
# V2; `char` is the quality-defining tone for V3.
QUALITIES = {
    "":       {"ivals": {0, 4, 7},            "third": 4,  "char": None},
    "m":      {"ivals": {0, 3, 7},            "third": 3,  "char": None},
    "7":      {"ivals": {0, 4, 7, 10},        "third": 4,  "char": 10},
    "maj7":   {"ivals": {0, 4, 7, 11},        "third": 4,  "char": 11},
    "m7":     {"ivals": {0, 3, 7, 10},        "third": 3,  "char": 10},
    "m7b5":   {"ivals": {0, 3, 6, 10},        "third": 3,  "char": 6},
    "7b9":    {"ivals": {0, 1, 4, 7, 10},     "third": 4,  "char": 1},
    "7#11":   {"ivals": {0, 4, 6, 7, 10},     "third": 4,  "char": 6},
    "6/9":    {"ivals": {0, 2, 4, 7, 9},      "third": 4,  "char": 2},
}


def parse_chord(name):
    """'D/F#' -> (2, '', 6);  'D6/9' -> (2, '6/9', None).

    The root comes off FIRST, because '6/9' is a quality containing a slash and
    would otherwise be read as a bass note named '9'.
    """
    i = 2 if len(name) > 1 and name[1] in "#b" else 1
    root = PC[name[:i].upper()]
    rest = name[i:]
    bass = None
    if rest not in QUALITIES and "/" in rest:
        rest, bass_s = rest.split("/", 1)
        bass = PC[bass_s.upper()]
    if rest not in QUALITIES:
        raise SystemExit(f"verifier does not know quality {rest!r} in {name!r}")
    return root, rest, bass


def open_pcs(tuning):
    """string number (6 low .. 1 high) -> open-string pitch class."""
    if len(tuning) != 6:
        raise SystemExit(f"tuning must be 6 strings, got {tuning!r}")
    # tuning is low->high; string 6 is lowest.
    return {6 - i: PC[n.upper()] for i, n in enumerate(tuning)}


def sounding_pcs(beats, opens, capo):
    """§7.1 + §5 — the pitch classes a measure actually SOUNDS.

    Two departures from the pre-v2 one-liner, both of which were failing correct
    tab rather than catching bad tab:

    1. `+ capo`. Frets are capo-relative (§7, Fret's convention: the capoed
       string is fret 0) and `tuning` keeps meaning the pitch of the open
       strings BEFORE the capo. So the capo is a constant added to every note on
       every string, with NO special case for fret 0 — a capo-2 open E string
       sounds F#, and `fret == 0` is not "the open string" any more, it is "the
       string at the capo". Omit the term and Wonderwall's whole 8 bars read two
       semitones flat while being correctly notated.

    2. A note carrying a `bend` contributes NOTHING. Its sounding pitch is
       `fret + semitones`, which for the ¼/½/1½/2½ values is not an integer
       fret and has no pitch class in a 12-tone set at all. The old code would
       have scored the FINGERED pitch, i.e. flagged the note the bend starts
       from — so every correct bend reads as a foreign note (§5's closing
       paragraph).

       Skipping is the spec's instruction and it cuts both ways, which is worth
       stating because the second half is a trap for FLE-107: V1 gets more
       permissive (a bend can no longer be foreign) but V2/V3/V4 get STRICTER
       (a bend can no longer supply the third, the characteristic tone or the
       root). A bar whose only third is a bent note will fail V2 here. That is
       deliberate — if the third is only ever reached by a bend, it is not
       sounding at the printed pitch and a verifier that credits it is lying —
       but it is the kind of true-and-surprising failure that gets "fixed" by
       deleting the check, so: the honest repair is to notate the landing pitch
       as its own note, not to loosen V2. Crediting a bend's integer-semitone
       target was considered and deliberately NOT done: it is a music decision
       past what §5 says, and this is the file whose entire premise is that my
       confidence is not the evidence.

    READ THIS BEFORE ADDING A CAPO SONG TO THE PILOT SET. The capo term is
    correct here only because this file's ground truth declares chords by their
    SOUNDING name — `chord_sequence` is hand-authored and predates §7.2's
    `sounding_name`, so it is absolute. Both sides of the comparison must sit in
    the same frame, and the live write gate resolves that the other way on
    purpose: `app/ai/tab_verification.py` deliberately OMITS the capo because it
    scores against `Chord.name`, which in v2 is the shape as fingered (`Em7`),
    so there the capo cancels. See the comment at tab_verification.py:238.

    So a capo song added here must write the SOUNDING chord (`F#m7`), never the
    shape (`Em7`). Write the shape and V1-V4 fail across correct tab — the exact
    breakage this capo fix exists to remove, reintroduced from the other side
    and looking identical in the output. There is no way to detect which
    convention a bare name follows, which is why it is written down instead.
    """
    pcs = set()
    for beat in beats:
        for n in beat["notes"]:
            if n.get("bend"):
                continue
            pcs.add((opens[n["string"]] + capo + n["fret"]) % 12)
    return pcs


# ---- V6: the renderer checks that replaced the MEASURE_WIDTH one -------------
#
# V6_NOTE. The old V6 was `len(beats) > 4 -> FAIL`, because TabNotation.tsx
# pinned MEASURE_WIDTH to four beats while laying beats out unbounded, so beat 5
# escaped its own measure. FLE-116 shipped §9.1 — measure width is now a running
# sum of each column's own width — and that check is therefore dead: no measure
# can overflow a box sized from its own content. Deleting it outright is also
# what §10 row 9 asks for, because the four-beat limit had leaked out of the
# renderer and into the ground truth's `conventions` block, bounding the MUSIC to
# what the component could draw.
#
# Two real renderer facts replaced it, one a failure and one a warning:
#
#   V6a (FAILURE) the renderer re-declares §2.2's table in TypeScript, because
#       it cannot import a Python dict. That is a fourth copy of the table whose
#       first three copies disagreed (§2.1), and it is the one copy no Python
#       test touches. If it drifts, the rhythm the player SEES stops matching the
#       rhythm V5 validated — silently, in the one direction nobody is looking.
#
#   V6b (WARNING, never a failure) §9.1 clamps a column to
#       `max(MIN_COLUMN, BEAT_WIDTH * beats)`, so every duration below 20/48 of
#       a beat draws the SAME width: a sixteenth, a thirty-second and a
#       triplet-sixteenth are indistinguishable on screen. When one measure
#       mixes two such durations, the rhythm is in the data and not on the
#       staff.
#
#       This is a warning on purpose. Making it fail would re-impose a renderer
#       limit on ground truth — the exact mistake §10 row 9 exists to undo, two
#       checks after undoing it. The music is right; the drawing is lossy. The
#       fix belongs in the renderer (smaller MIN_COLUMN, or beams), never in the
#       notation.
_RENDERER_TSX = (pathlib.Path(__file__).resolve().parents[2] / "mobile" / "src"
                 / "components" / "TabNotation.tsx")
BEAT_WIDTH = 48      # TabNotation.tsx:87
MIN_COLUMN = 20      # TabNotation.tsx:94


def column_width(duration):
    """§9.1's `columnWidth`, in Python. Exact — `Fraction` in, `Fraction` out."""
    beats = duration_beats(duration)
    if beats is None:
        beats = Fraction(1)   # beatsOf()'s `?? 1` degradation, mirrored
    return max(Fraction(MIN_COLUMN), BEAT_WIDTH * beats)


def renderer_duration_table():
    """Parse `DURATION_BEATS` out of TabNotation.tsx. None if it moved.

    Deliberately a regex over the source rather than a build step: this script
    is zero-dependency, read-only and runs without node. The cost is that a
    mobile-side refactor can hide the table from it, which is why a parse miss
    WARNS instead of failing — a server verifier that goes red because someone
    reformatted a .tsx file would be turned off, and then V6a would be gone for
    a worse reason than drift.
    """
    if not _RENDERER_TSX.exists():
        return None
    src = _RENDERER_TSX.read_text()
    m = re.search(r"DURATION_BEATS:\s*Record<string,\s*number>\s*=\s*\{(.*?)\}",
                  src, re.DOTALL)
    if not m:
        return None
    out = {}
    for key, val in re.findall(r"""['"]?([a-z-]+)['"]?\s*:\s*([^,}]+)""",
                               m.group(1)):
        expr = val.strip()
        frac = re.fullmatch(r"(\d+)\s*/\s*(\d+)", expr)
        if frac:                      # the renderer writes `4 / 3`, `1 / 6`
            out[key] = Fraction(int(frac.group(1)), int(frac.group(2)))
        else:
            try:
                out[key] = Fraction(expr)
            except (ValueError, ZeroDivisionError):
                return None
    return out or None


def check_renderer_parity(fails, warns):
    """V6a — the TS table must equal §2.2's, key for key and value for value."""
    theirs = renderer_duration_table()
    if theirs is None:
        warns.append(f"V6a SKIPPED: could not parse DURATION_BEATS out of "
                     f"{_RENDERER_TSX.name} — the renderer's copy of the §2.2 "
                     f"table is unchecked this run, so it can drift from the "
                     f"server's without anything going red. Re-point the regex "
                     f"in renderer_duration_table().")
        return
    ours = dict(DURATION_BEATS)
    for d in sorted(set(ours) | set(theirs)):
        if d not in theirs:
            fails.append(f"V6a: {_RENDERER_TSX.name} is missing duration "
                         f"{d!r} — it renders as 1 beat wide (beatsOf's `?? 1`) "
                         f"while V5 measures it as {ours[d]}")
        elif d not in ours:
            fails.append(f"V6a: {_RENDERER_TSX.name} declares duration {d!r}, "
                         f"which is not in §2.2 — the renderer can draw a "
                         f"rhythm the server will coerce to 'quarter' (§8.2)")
        elif theirs[d] != ours[d]:
            fails.append(f"V6a: duration {d!r} is {ours[d]} beats in §2.2 but "
                         f"{theirs[d]} in {_RENDERER_TSX.name} — the drawn "
                         f"rhythm disagrees with the validated one")


def check_drawn_rhythm(beats, where, warns):
    """V6b — durations that differ in the data but not on screen (§9.1 clamp)."""
    by_width = defaultdict(set)
    for b in beats:
        d = b.get("rest") or (b["notes"][0]["duration"] if b["notes"] else None)
        if d is not None:
            by_width[column_width(d)].add(d)
    for width, ds in sorted(by_width.items()):
        if len(ds) > 1:
            warns.append(f"{where}: {sorted(ds)} all draw {float(width):g}px "
                         f"wide (§9.1 MIN_COLUMN={MIN_COLUMN} clamp) — the "
                         f"rhythm is in the data but not on the staff")


# --- V9: the feel marking ---------------------------------------------------
# Prose words that COMMIT the song to a swung reading, and words that commit it
# to an even one. Matched against the human `feel` string to cross-examine the
# declared `tab_feel` enum — NOT to derive it. A song whose prose says neither
# (there are none today) is left alone rather than guessed at.
_PROSE_SWUNG = ("swing", "swung", "shuffle")
_PROSE_EVEN = ("straight", "even eighth", "even 8th", "not swung")
_FEEL_VALUES = ("straight", "swing", "shuffle")


def check_feel(song, fails):
    """V9 — `tab.feel` is present, legal, and agrees with the prose `feel`.

    This check exists because of a defect it would have caught in this very
    file. `Tab.feel` defaults to "straight", and FLE-103's set carried feel only
    as a per-song PROSE string that nothing downstream reads. So three swing
    tunes — Autumn Leaves, Take the 'A' Train, Giant Steps — validated clean,
    loaded clean, and would have reached a pilot player's screen with no swing
    badge on the staff: told, in the only language the product speaks, to play
    Coltrane's changes dead straight. Nothing went red, because a default is not
    a disagreement.

    That is the `tuning: standard` failure mode exactly (schema v2 §7): a field
    that is TRUE as data and MISLEADING as instruction. The fix is not to infer
    the marking from the prose — pattern-matching "swing" out of an English
    sentence is the guess this whole file exists to refuse — but to declare it
    per song and make the two sources check each other. Then a future editor who
    changes one and not the other gets a failure instead of a silent downgrade.
    """
    where = song["title"]
    prose = (song.get("feel") or "").lower()
    tab = song["tab"]

    if "feel" not in tab:
        fails.append(f"V9 {where}: tab has no `feel` — it would default to "
                     f"'straight'. Ground truth states its feel; it does not "
                     f"inherit one (prose feel is {song.get('feel')!r})")
        return
    marking = tab["feel"]
    if marking not in _FEEL_VALUES:
        fails.append(f"V9 {where}: tab.feel {marking!r} is not one of "
                     f"{list(_FEEL_VALUES)} — `Tab.feel` is a closed Literal and "
                     f"this file would not survive Tab.model_validate (§2.5)")
        return

    swung_prose = any(w in prose for w in _PROSE_SWUNG)
    even_prose = any(w in prose for w in _PROSE_EVEN)
    # "bossa nova (even eighths, not swung)" trips BOTH lists on "not swung".
    # The even phrases win, because they are the ones that say what it IS.
    if swung_prose and not even_prose and marking == "straight":
        fails.append(f"V9 {where}: prose feel {song['feel']!r} is swung but "
                     f"tab.feel is 'straight' — the staff would carry no feel "
                     f"badge and the player is told to play it even")
    if even_prose and marking != "straight":
        fails.append(f"V9 {where}: prose feel {song['feel']!r} is even but "
                     f"tab.feel is {marking!r} — the staff would tell the player "
                     f"to swing even-eighth music")

    # C8, per bar: feel marked AND written out as triplet pairs is swing applied
    # twice (4/9 + 2/9). Vacuous on all-quarter data; it is here so that
    # FLE-107's re-notation cannot introduce it silently.
    #
    # BLOCKING here, warning in the write gate (`_check_double_applied_swing`),
    # and that is not an inconsistency. It warns there because no cached
    # breakdown carries `feel` at all, so its false-positive rate on generator
    # output is unmeasured and FLE-103 §6 is the precedent for not gating on an
    # unmeasured heuristic. Neither condition holds over THIS file: every
    # duration in it was typed by hand, so there is no unknown population to be
    # wrong about, and a bar of ground truth that cannot be performed as notated
    # is not a finding to report — it is a bug in the ruler.
    for mi, meas in enumerate(tab["measures"], 1):
        durations = [
            b.get("rest") if b.get("rest") is not None
            else (b["notes"][0]["duration"] if b.get("notes") else None)
            for b in meas["beats"]
        ]
        if is_double_applied_swing(marking, durations):
            fails.append(f"V9 {where} m{mi}: tab.feel is {marking!r} AND the bar "
                         f"is triplet-quarter/triplet-eighth pairs — swing "
                         f"applied twice gives 4/9 + 2/9. Mark the feel and "
                         f"write plain eighths (§2.5)")


def beat_duration(beat, where, fails):
    """Beat-value of one column as an exact `Fraction` (FLE-115 §2.2/§3)."""
    ds = {n["duration"] for n in beat["notes"]}
    if not ds:
        # §3 — a rest column has a length and contributes it to the bar. Under
        # the pre-v2 code this raised KeyError on `ds.pop()`.
        rest = duration_beats(beat.get("rest"))
        if rest is None:
            fails.append(f"{where}: beat has neither notes nor a rest")
            return Fraction(0)
        return rest
    if len(ds) > 1:
        fails.append(f"{where}: beat mixes durations {sorted(ds)} — "
                     f"simultaneous notes of one voicing must agree")
        return max(duration_beats(d) or Fraction(0) for d in ds)
    d = ds.pop()
    beats = duration_beats(d)
    if beats is None:
        fails.append(f"{where}: unknown duration {d!r}")
        return Fraction(0)
    return beats


def check_chord(name, beats, opens, capo, where, fails, warns):
    """V1-V4 for one measure against its declared chord NAME.

    Extracted from main() by FLE-117 so that the capo and bend paths can be
    tested directly (tests/test_fle103_verifier_v2.py). That is not tidiness:
    the pilot set contains no capo and no bend, so a green run over it does not
    execute either branch, and inlined here they would have shipped with
    literally no evidence behind them.
    """
    rt, qual, bass = parse_chord(name)
    spec = QUALITIES[qual]
    allowed = {(rt + i) % 12 for i in spec["ivals"]}
    if bass is not None:
        allowed.add(bass)
    # §7.1 capo + §5 bend skip, both inside sounding_pcs().
    sounding = sounding_pcs(beats, opens, capo)
    if not sounding and any(b["notes"] for b in beats):
        # Every note in the measure is bent, so §5 left nothing with a
        # derivable pitch class. Saying "no third, no defining tone, no root"
        # three times over would blame the voicing for what is really the
        # verifier declining to judge it.
        warns.append(f"{where}: every note is bent — V1-V4 cannot judge "
                     f"{name} from fingered pitches (§5)")
        return

    # V1 foreign notes
    foreign = sounding - allowed
    if foreign:
        fails.append(f"{where}: {name} sounds foreign "
                     f"{sorted(CHROM[p] for p in foreign)}")
    # V2 third
    if (rt + spec["third"]) % 12 not in sounding:
        fails.append(f"{where}: {name} contains no third "
                     f"({CHROM[(rt + spec['third']) % 12]} absent)")
    # V3 characteristic tone
    if spec["char"] is not None and (rt + spec["char"]) % 12 not in sounding:
        fails.append(f"{where}: {name} missing its defining tone "
                     f"{CHROM[(rt + spec['char']) % 12]}")
    # V4 root
    if rt not in sounding:
        fails.append(f"{where}: {name} has no root {CHROM[rt]}")


def main(path=None):
    """`path` defaults to the committed pilot set; pass one to check another.

    FLE-117 added the argument for two users. The tests need a set with a capo
    and a bend in it, because the committed one has neither. And FLE-107, which
    re-notates five songs, can run this over a candidate file BEFORE replacing
    ground truth with it, which is the order those two steps want to be in.
    """
    root_dir = pathlib.Path(__file__).resolve().parents[1]
    path = (pathlib.Path(path) if path else
            root_dir / "data" / "verified_tabs" / "pilot_verified_set.json")
    if not path.exists():
        raise SystemExit(f"missing {path} — run fle103_build_pilot_set.py first")
    doc = json.loads(path.read_text())
    songs = doc["songs"]

    fails = []
    warns = []
    measures_checked = 0
    measures_with_chord = 0
    shape_index = defaultdict(list)

    # V6a is a property of the two source files, not of any one measure, so it
    # runs once rather than 80 times.
    check_renderer_parity(fails, warns)

    for song in songs:
        tab = song["tab"]
        # V9 is a property of the song, not of a measure, so it runs once per
        # song rather than per bar (its own C8 loop walks the bars).
        check_feel(song, fails)
        opens = open_pcs(tab["tuning"])
        # §7.1 — absent means 0, and 0 makes every term below a no-op, so a
        # pre-v2 tab reads exactly as it did. `.get` is the whole migration.
        capo = tab.get("capo", 0) or 0
        seq = song["chord_sequence"]
        for mi, meas in enumerate(tab["measures"]):
            measures_checked += 1
            where = f"{song['title']} m{mi + 1}"
            beats = meas["beats"]

            # V6b drawn rhythm (warning only — see V6_NOTE)
            check_drawn_rhythm(beats, where, warns)

            # V5 meter
            # FLE-115 §2.2 — exact, so the 1e-6 tolerance is gone. It was not
            # hiding anything (see the DURATION_BEATS note in app/models/song.py
            # for the measurement); under `Fraction` there is simply nothing for
            # it to absorb.
            num, den = (int(x) for x in meas["time_signature"].split("/"))
            want = Fraction(num * 4, den)
            got = sum((beat_duration(b, where, fails) for b in beats), Fraction(0))
            if got != want:
                fails.append(f"{where}: durations sum to {float(got):g} beats, "
                             f"{meas['time_signature']} wants {float(want):g}")

            for bi, beat in enumerate(beats):
                strings = [n["string"] for n in beat["notes"]]
                # V7 one note per string
                if len(strings) != len(set(strings)):
                    fails.append(f"{where} b{bi + 1}: string sounded twice")
                for n in beat["notes"]:
                    if not 1 <= n["string"] <= 6:
                        fails.append(f"{where} b{bi + 1}: string {n['string']}")
                    if n["fret"] < 0:
                        # FLE-115 §6.1 — this check and TabNotation.tsx used to
                        # contradict each other: the renderer DREW `fret: -1` as
                        # a dead note while this rejected it. v2 settles it in
                        # this direction — fret is >= 0 always — and now names
                        # the representation that replaces -1.
                        fails.append(f"{where} b{bi + 1}: fret {n['fret']} — a "
                                     f"dead note is its fingered fret plus "
                                     f"effects:['dead'], never -1 (§6.1)")
                    elif n["fret"] > 24 - capo:
                        # §7.1 consequence 1 — the usable range shrinks as the
                        # capo climbs, and there is no note behind the capo.
                        # Checked here because this script reads the raw JSON:
                        # `Tab._clamp_frets_to_capo` would silently clamp the
                        # same note on the way out of the API, so without this
                        # the ground truth and the served tab disagree and
                        # neither side says so.
                        fails.append(f"{where} b{bi + 1}: fret {n['fret']} "
                                     f"exceeds {24 - capo} (24 - capo {capo}) "
                                     f"— §7.1")
                # V8 fret span
                fr = [n["fret"] for n in beat["notes"] if n["fret"] > 0]
                if fr and max(fr) - min(fr) > 5:
                    fails.append(f"{where} b{bi + 1}: fret span "
                                 f"{min(fr)}-{max(fr)} exceeds one position")

            # chord checks
            name = seq[mi] if mi < len(seq) else None
            if not name:
                continue
            measures_with_chord += 1
            check_chord(name, beats, opens, capo, where, fails, warns)

            key = json.dumps([[sorted((n["string"], n["fret"])
                                      for n in b["notes"])] for b in beats])
            shape_index[key].append((song["title"], mi + 1, name))

    # ---- coverage ----------------------------------------------------------
    diffs = [(s["catalog_difficulty"], s["title"]) for s in songs]
    print("=== SELECTOR COVERAGE "
          "(ABS(difficulty - player_level) <= 0.15) ===")
    holes, thin = [], []
    pl = 0.20
    while pl <= 1.0001:
        inw = [t for d, t in diffs if abs(d - pl) <= 0.15]
        if not inw:
            holes.append(pl)
        elif len(inw) == 1:
            thin.append((pl, inw[0]))
        pl = round(pl + 0.01, 2)

    def spans(xs):
        out = []
        for x in xs:
            if out and abs(x - out[-1][1] - 0.01) < 1e-9:
                out[-1][1] = x
            else:
                out.append([x, x])
        return out

    if holes:
        for a, b in spans(holes):
            print(f"  DEAD   player_level {a:.2f}-{b:.2f}: 0 songs in window")
    else:
        print("  no dead windows in [0.20, 1.00]")
    if thin:
        bytitle = defaultdict(list)
        for p, t in thin:
            bytitle[t].append(p)
        for t, ps in bytitle.items():
            print(f"  THIN   player_level {min(ps):.2f}-{max(ps):.2f}: "
                  f"only '{t}' — no reroll variety")
    print(f"  difficulties: {sorted(d for d, _ in diffs)}")

    # ---- shape reuse (informational) ---------------------------------------
    dupes = {k: v for k, v in shape_index.items()
             if len({t for t, _, _ in v}) > 1}
    print("\n=== CROSS-SONG IDENTICAL MEASURES (informational, not a failure) ===")
    if not dupes:
        print("  none")
    for v in dupes.values():
        names = {n for _, _, n in v}
        locs = ", ".join(f"{t} m{m}" for t, m, _ in v)
        print(f"  {sorted(names)} at {locs}")
    print("  NOTE for FLE-106: identical measures across songs are LEGITIMATE "
          "when two tunes genuinely share a chord. FLE-102's T7 template-reuse\n"
          "  heuristic would flag these as defects, so T7 must stay a human "
          "prompt and must never become an automated gate.")

    # ---- warnings ----------------------------------------------------------
    # A second channel, added by FLE-117. V6b and the all-bent-measure note are
    # true observations that must NOT turn the run red: one is a renderer
    # limitation and the other is the verifier declining to judge. Folding
    # either into `fails` would push a drawing limit back into ground truth,
    # which is the §10 row 9 mistake this very commit is undoing.
    print("\n=== WARNINGS (never a failure — see V6_NOTE) ===")
    if not warns:
        print("  none")
    for w in warns:
        print(f"  - {w}")

    # ---- verdict -----------------------------------------------------------
    print(f"\n=== VERDICT ===")
    print(f"  measures checked: {measures_checked} "
          f"({measures_with_chord} carry a declared chord)")
    print(f"  songs: {len(songs)}")
    print(f"  warnings: {len(warns)}")
    if fails:
        print(f"  FAILURES: {len(fails)}")
        for f in fails:
            print(f"    - {f}")
        return 1
    print("  all checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
