"""FLE-107 — build the tab regression set with committed ground truth.

Zero spend: no LLM, no DB, no network. Emits
`server/data/regression_tabs/regression_set.json`.

    server/.venv/bin/python server/scripts/fle107_build_regression_set.py

Why this set is not the FLE-103 pilot set
-----------------------------------------
Opposite requirements, stated in FLE-103 §6 and still true. The pilot set
EXCLUDES every song whose material is articulation, because a stranger must be
able to practise from it. This set is CHOSEN for those songs, because its
function is to fail when the generator gets articulation wrong. Seven of the ten
here are songs FLE-102 audited and found unfit, on purpose: that is what makes
"0 of 10 fit to practise from" into a number that moves release over release.

Division of labour with fle107_score_tab.py
-------------------------------------------
This file KNOWS the fret numbers. The scorer does NOT derive anything from them
— it re-derives every sounding pitch from `tuning` + `capo` + `fret` and checks
the result against separately hand-declared SCALE DEGREES and CHORD NAMES. A
fret I typed wrong is caught there. That separation is why FLE-103 caught my own
D7#11 (I wrote the #11 at a fret that is F, not G#) before it was published. Do
not merge these two files, and do not let the scorer import this one.

Two bases, and the honesty rule that produced them
--------------------------------------------------
Every bar carries `basis`:

  note_exact   I commit the notes. The scorer diffs them.
  constraint   I do NOT commit notes, because I cannot certify them cold. I
               commit assertions the correct tab must satisfy — the tuning, the
               key's pitch content, that a bend exists at all, that the
               subdivision is a sextuplet. The scorer checks those.

48 bars are note_exact, 32 are constraint. The 32 are not padding and they are
not a guess dressed up as a number: for Comfortably Numb, "the passage contains
no structural C natural" is the ENTIRE T5 defect FLE-102 measured, and it is
certifiable without the record, while "bar 3 beat 2 is a whole-step bend from
the 14th fret" is not. Writing the second from memory is exactly the failure
mode I was hired to catch. Each constraint song names what I would need.

Frames (read this before editing a chord name or a degree)
----------------------------------------------------------
All chord names and all `root` values in this file are **shape names**. In Eb
tuning the "Em" shape sounds Ebm; with a capo at 2 the "Em7" shape sounds F#m7.
The scorer resolves them uniformly:

    sounding_pc = shape_pc + tuning_offset + capo

and a note's sounding pc the same way, via `open_midi(tuning)[string] + capo +
fret`. So a frame error shows up as a disagreement rather than as silence. Fret
numbers are capo-relative per FLE-115 §7 — the capoed string is fret 0.
"""
import json
import pathlib

OUT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "data" / "regression_tabs" / "regression_set.json"
)

STANDARD = ["E", "A", "D", "G", "B", "e"]
EFLAT = ["Eb", "Ab", "Db", "Gb", "Bb", "eb"]
DADGAD = ["D", "A", "D", "G", "A", "d"]


# --- small builders -------------------------------------------------------
def n(string, fret, duration="quarter", **kw):
    note = {"string": string, "fret": fret, "duration": duration}
    note.update({k: v for k, v in kw.items() if v is not None})
    return note


def beat(*notes):
    return {"notes": list(notes)}


def stab(shape, duration="quarter"):
    """One column sounding a whole voicing. `shape` is {string: fret}."""
    return beat(*[n(s, f, duration) for s, f in sorted(shape.items(), reverse=True)])


def req(type_, severity="fail", scope="every_bar", why="", **params):
    r = {"type": type_, "severity": severity, "scope": scope, "why": why}
    r.update(params)
    return r


# --- shapes ---------------------------------------------------------------
# Hand-typed. Every one is re-derived and checked against its chord NAME by
# fle107_score_tab.py, which has never seen this table.
SHAPES = {
    # House of the Rising Sun — open position, standard tuning
    "Am":  {5: 0, 4: 2, 3: 2, 2: 1, 1: 0},
    "C":   {5: 3, 4: 2, 3: 0, 2: 1, 1: 0},
    "D":   {4: 0, 3: 2, 2: 3, 1: 2},
    "F":   {6: 1, 4: 3, 3: 2, 2: 1, 1: 1},
    "E":   {6: 0, 5: 2, 4: 2, 3: 1, 2: 0, 1: 0},
    # Little Wing — standard shapes, Eb tuning
    "Em":  {6: 0, 5: 2, 4: 2, 3: 0, 2: 0, 1: 0},
    "G":   {6: 3, 5: 2, 4: 0, 3: 0, 2: 0, 1: 3},
    "Bm":  {5: 2, 4: 4, 3: 4, 2: 3, 1: 2},
    "Bb":  {5: 1, 4: 3, 3: 3, 2: 3, 1: 1},
    # Wonderwall — capo 2, Gallagher's four voicings
    "Em7":     {6: 0, 5: 2, 4: 2, 3: 0, 2: 3, 1: 3},
    "G(wond)": {6: 3, 5: 2, 4: 0, 3: 0, 2: 3, 1: 3},
    "Dsus4":   {4: 0, 3: 2, 2: 3, 1: 3},
    "A7sus4":  {5: 0, 4: 2, 3: 0, 2: 3, 1: 3},
    # Sultans of Swing — intro
    "Dm":  {4: 0, 3: 2, 2: 3, 1: 1},
    "A":   {5: 0, 4: 2, 3: 2, 2: 2, 1: 0},
}

# The chord NAME each shape asserts, kept apart from the shape itself so the
# scorer is told a name and never a spelling.
SHAPE_NAME = {
    "G(wond)": "G",
}


def shape_name(key):
    return SHAPE_NAME.get(key, key)


# --- the 1-5-6-b7 boogie --------------------------------------------------
# The figure FLE-102 found missing from Pride and Joy ("loses the 5->6->b7
# boogie movement ... which is a rock pattern, not a shuffle"). Eight eighths,
# two strings, the low root pedalled against 5 / 6 / b7 / 6.
#
# Written as STRAIGHT EIGHTHS with `feel: "shuffle"` on the Tab, per FLE-115
# §2.5: swing and shuffle are MARKED, not notated. A tab that writes literal
# triplet-eighths AND sets feel: shuffle has applied the swing twice — scored as
# a failure under tolerance A-9, and the thing FLE-118 has to teach the prompt.
def boogie(low_string, high_string, root_fret=0):
    pattern = [2, 2, 4, 4, 5, 5, 4, 4]       # on the higher of the two strings
    return [
        beat(n(low_string, root_fret, "eighth"), n(high_string, f, "eighth"))
        for f in pattern
    ]


def arpeggio_6_8(order):
    """Six eighths = 3 quarter-beats = one 6/8 bar. `order` is [(string, fret)]."""
    return [beat(n(s, f, "eighth")) for s, f in order]


SONGS = []

# =========================================================================
# 1. Sweet Home Chicago — the shuffle, and feel-as-a-mark
# =========================================================================
SONGS.append({
    "title": "Sweet Home Chicago",
    "artist": "Robert Johnson",
    "arrangement": (
        "The standard band reading: 12-bar in E, shuffle feel, boogie figure on "
        "the two lowest strings. NOT Johnson's own 1936 solo performance, which "
        "is a fingerpicked country-blues arrangement and a different guitar "
        "part. The catalog credits Johnson; the arrangement every blues player "
        "means by this title is the electric one, and that is what I committed. "
        "Which of the two Fletcher teaches is a curation call for Junie, not a "
        "tab question — but the ground truth has to name one, so it names this."
    ),
    "passage": "Bars 1-8 of the 12-bar form: I I I I IV IV I I.",
    "setup": {"tuning": STANDARD, "capo": 0, "key": "E", "time_signature": "4/4",
              "feel": "shuffle", "bpm": 92, "key_sounding": "E major"},
    "modes_guarded": ["shuffle-feel-marked-not-notated", "T9-rhythm-grid",
                      "T10-bars-that-do-not-add-up", "T2-no-third (correctly absent here)"],
    "why_in_set": (
        "The cheapest possible test of the one thing FLE-115 §2.5 added: a tab "
        "that gets every fret right and leaves `feel` at `straight` is not this "
        "song. FLE-102 also found T10 here — two bars, neither of which added up "
        "to 4/4 — so the meter check has a real prior failure to regress against."
    ),
    "song_requirements": [
        req("feel_is", severity="fail", scope="passage", feel="shuffle",
            why="A Chicago shuffle played straight is a different rhythm. FLE-102: "
                "notated straight in the cached tab."),
        req("no_literal_swing_subdivision", severity="fail", scope="every_bar",
            why="A-9: triplet-eighth pairs PLUS feel:shuffle double-applies the "
                "swing. The groove is the mark, not the notation."),
    ],
    "bars": [
        {
            "index": i,
            "kind": "riff",
            "basis": "note_exact",
            "chord": None,
            "chord_checked": False,
            "match": "superset_ok",
            "rhythm_scored": True,
            "requirements": [
                req("scale_degrees_exact", severity="fail",
                    root=root, degrees=["1", "5", "6", "b7"],
                    why="The boogie's whole pitch content. A third anywhere here is "
                        "wrong (this figure has none) and so is a missing b7 — the "
                        "b7 is what makes it a blues rather than a rock fifth."),
            ],
            "beats": boogie(*strings),
        }
        for i, (root, strings) in enumerate(
            [("E", (6, 5))] * 4 + [("A", (5, 4))] * 2 + [("E", (6, 5))] * 2, start=1
        )
    ],
    "cannot_verify": [
        {"what": "Whether the form takes the quick change (bar 2 to IV).",
         "need": "A product decision on which reading we teach; both are standard. "
                 "Tolerance A-8 passes either, and only on bar 2."},
        {"what": "Johnson's own solo arrangement, note for note.",
         "need": "The 1936 record plus a reference transcription. Not committed here."},
    ],
})

# =========================================================================
# 2. Pride and Joy — the same boogie a semitone down, in Eb
# =========================================================================
SONGS.append({
    "title": "Pride and Joy",
    "artist": "Stevie Ray Vaughan",
    "arrangement": "Texas shuffle in E shapes, Eb tuning, so it sounds in Eb.",
    "passage": "Bars 1-8 of the 12-bar form: I I I I IV IV I I.",
    "setup": {"tuning": EFLAT, "capo": 0, "key": "E (shapes) / Eb (sounding)",
              "time_signature": "4/4", "feel": "shuffle", "bpm": 110, "key_sounding": "Eb major"},
    "modes_guarded": ["altered-tuning-Eb", "shuffle-feel", "T2-no-chord-quality",
                      "T6-invented-material", "T10"],
    "why_in_set": (
        "The one cached tab whose defect FLE-102 could name in musical terms: the "
        "5->6->b7 movement that defines the figure was replaced by root+5th / "
        "root+octave, 'a rock pattern, not a shuffle'. Paired with Sweet Home "
        "Chicago on purpose: the SAME figure, one in standard and one in Eb, so a "
        "generator that is right in one frame and wrong in the other is visible. "
        "Also the second Eb entry, because FLE-89 established tuning is NOT the "
        "bug (4/4, then 8/8 agreeing) and a regression set has to keep proving it."
    ),
    "song_requirements": [
        req("feel_is", severity="fail", scope="passage", feel="shuffle",
            why="A Texas shuffle is the song. FLE-102 T9."),
        req("tuning_is", severity="fail", scope="passage", tuning=EFLAT,
            why="SRV tuned down a semitone. Confirmed against the catalog in FLE-102."),
        req("no_literal_swing_subdivision", severity="fail", scope="every_bar",
            why="A-9."),
    ],
    "bars": [
        {
            "index": i,
            "kind": "riff",
            "basis": "note_exact",
            "chord": None,
            "chord_checked": False,
            # superset_ok, and this is the load-bearing tolerance on this song:
            # SRV's figure carries an open-string rake and double-stops over this
            # bass. I commit the movement, not his fills, and extra notes in the
            # bar do not fail it. See cannot_verify.
            "match": "superset_ok",
            "rhythm_scored": True,
            "requirements": [
                req("scale_degrees_present", severity="fail",
                    root=root, degrees=["1", "5", "6", "b7"],
                    why="The 6 and the b7 are the movement FLE-102 found missing. "
                        "`present` not `exact` here: the fills add notes."),
            ],
            "beats": boogie(*strings),
        }
        for i, (root, strings) in enumerate(
            [("E", (6, 5))] * 4 + [("A", (5, 4))] * 2 + [("E", (6, 5))] * 2, start=1
        )
    ],
    "cannot_verify": [
        {"what": "SRV's upper-voice fills, the rake, and the double-stop bends.",
         "need": "The record plus a reference transcription. The committed bars are "
                 "the bass movement only; tolerance A-6 (superset_ok) is what keeps "
                 "a fuller correct transcription from scoring as wrong."},
    ],
})

# =========================================================================
# 3. Comfortably Numb — bends, and the T5 semitone
# =========================================================================
SONGS.append({
    "title": "Comfortably Numb",
    "artist": "Pink Floyd",
    "arrangement": "Standard tuning. The first solo, over the verse changes in B minor.",
    "passage": "First solo, bars 1-8.",
    "setup": {"tuning": STANDARD, "capo": 0, "key": "B minor",
              "time_signature": "4/4", "feel": "straight", "bpm": 63, "key_sounding": "B minor"},
    "modes_guarded": ["bend-led-melody", "T5-systematic-off-by-one-degree",
                      "T2-no-chord-quality", "sustain"],
    "basis_note": (
        "CONSTRAINT ONLY. I will not publish a bar-by-bar transcription of this "
        "solo from memory — see cannot_verify. What I can certify cold is the "
        "whole of the defect FLE-102 measured: every C in the cached tab is C "
        "natural where B minor requires C#, five bars of it, which turns B "
        "Aeolian into B Phrygian. That is checkable with no recording, it "
        "contradicts the breakdown's own chord list, and it is the single most "
        "audible wrong note available over Bm."
    ),
    "why_in_set": (
        "The bend song. FLE-102: 'bends, sustain, vibrato' is what the song is "
        "made of and 'fret numbers' is what the schema could say. FLE-115 §5 "
        "fixed that, so a tab of this passage with no `bend` anywhere is now a "
        "defect the schema can name, and this is the bar that names it."
    ),
    "song_requirements": [
        req("pitch_class_absent", severity="fail", scope="every_bar",
            pitch_classes=["C"], min_duration="eighth",
            why="T5. B natural minor has C#, not C. Qualified by duration on "
                "purpose: FLE-102's defect was five bars of STRUCTURAL C "
                "naturals. A chromatic passing tone shorter than an eighth is "
                "not that defect, so it is reported as a flag instead of a "
                "failure (tolerance A-13)."),
        req("contains_bend", severity="fail", scope="passage", min_semitones=1.0,
            why="A bend-led melody with no bend in eight bars is not this solo. "
                "Threshold at a whole step: Gilmour's phrasing is full-tone and "
                "wider, and a 0.5 would pass on an ornament."),
        req("sustain_present", severity="fail", scope="passage",
            why="Half-or-longer notes, a tie, or let_ring. The cached tab was an "
                "even grid of short notes, which is not a sustained melody — and "
                "this is now expressible (FLE-115 §4, §6)."),
        req("contains_effect", severity="flag", scope="passage",
            effects=["vibrato", "wide_vibrato"],
            why="FLE-102 named vibrato as load-bearing here. Flag, not fail: a "
                "correct 8-bar excerpt could land without one."),
        req("key_pitch_content", severity="flag", scope="every_bar",
            root="B", degrees=["1", "2", "b3", "4", "5", "b6", "b7"],
            why="B Aeolian. Flag, not fail: Gilmour bends outside the scale and a "
                "bent note's sounding pitch is not an integer fret (FLE-115 §5), "
                "so a fail here would punish correct phrasing."),
    ],
    "bars": [
        {"index": i, "kind": "melody", "basis": "constraint", "chord": None,
         "chord_checked": False, "match": None, "rhythm_scored": False,
         "requirements": []}
        for i in range(1, 9)
    ],
    "cannot_verify": [
        {"what": "The solo note for note, and the exact bend targets.",
         "need": "The record plus a reference transcription. I know this solo as a "
                 "listener, not as a chart I could publish bar by bar, and writing "
                 "it from memory would put the same class of error into the ground "
                 "truth that the ground truth exists to catch."},
        {"what": "Whether the verse is Bm-A-G-Em or carries a D substitution.",
         "need": "The record. Not committed, which is why there are no chord bars "
                 "on this song even though its harmony is the part I am surest of."},
    ],
})

# =========================================================================
# 4. House of the Rising Sun — 6/8, arpeggios, and the reverse mode flip
# =========================================================================
_HOTRS = [
    ("Am", [(5, 0), (4, 2), (3, 2), (2, 1), (1, 0), (2, 1)]),
    ("C",  [(5, 3), (4, 2), (3, 0), (2, 1), (1, 0), (2, 1)]),
    ("D",  [(4, 0), (3, 2), (2, 3), (1, 2), (2, 3), (3, 2)]),
    ("F",  [(6, 1), (4, 3), (3, 2), (2, 1), (1, 1), (2, 1)]),
    ("Am", [(5, 0), (4, 2), (3, 2), (2, 1), (1, 0), (2, 1)]),
    ("C",  [(5, 3), (4, 2), (3, 0), (2, 1), (1, 0), (2, 1)]),
    ("E",  [(6, 0), (4, 2), (3, 1), (2, 0), (1, 0), (2, 0)]),
    ("E",  [(6, 0), (4, 2), (3, 1), (2, 0), (1, 0), (2, 0)]),
]
SONGS.append({
    "title": "The House of the Rising Sun",
    "artist": "The Animals",
    "arrangement": "Standard tuning, 6/8, the arpeggiated accompaniment.",
    "passage": "Verse, bars 1-8: Am C D F Am C E E.",
    "setup": {"tuning": STANDARD, "capo": 0, "key": "A minor",
              "time_signature": "6/8", "feel": "straight", "bpm": 76, "key_sounding": "A minor"},
    "modes_guarded": ["non-4/4-meter", "arpeggiated-pattern",
                      "minor-key-REVERSE-mode-flip", "T2", "T10"],
    "why_in_set": (
        "Two things nothing else in the set tests. First, a meter that is not "
        "4/4 — `time_signature_beats('6/8')` is 3, and a bar of six eighths is "
        "the cheapest way to catch a consumer that assumed four. Second, the "
        "mode flip in the direction nobody guards: this is in A minor and "
        "contains D MAJOR (F# natural) and E MAJOR (G# natural). A generator "
        "that reasons 'A minor, so use the natural minor scale' emits Dm and Em "
        "and is wrong on three of eight bars. FLE-102's T1 was always a major "
        "third where a minor belonged; Hotel California tests both directions in "
        "the pilot set, and this tests the reverse one here."
    ),
    "song_requirements": [
        req("pitch_class_required", severity="fail", scope="passage",
            pitch_classes=["F#", "G#"],
            why="The raised thirds of the D and E chords. Their absence IS the "
                "reverse mode flip, and it is arithmetic, not opinion."),
    ],
    "bars": [
        {
            "index": i,
            "kind": "arpeggio",
            "basis": "note_exact",
            "chord": shape_name(ch),
            "chord_checked": True,
            # Per BAR, not per beat: an arpeggio sounds one note per column, so a
            # per-beat third check fails every correct bar here. FLE-103's V2 is a
            # chord-stab check and must not be run on arpeggio bars — see the
            # warning in the writeup for FLE-117.
            "chord_spelled_over": "bar",
            "match": "exact",
            "rhythm_scored": True,
            "requirements": [],
            "beats": arpeggio_6_8(order),
        }
        for i, (ch, order) in enumerate(_HOTRS, start=1)
    ],
    "cannot_verify": [
        {"what": "The sixth eighth of each bar.",
         "need": "Nothing — it is a genuine arrangement variant. Every published "
                 "reading ascends through the shape; whether the sixth note steps "
                 "back down or repeats the top varies. Tolerance A-14 accepts any "
                 "note of the declared chord in column 6."},
    ],
})

# =========================================================================
# 5. Kashmir — DADGAD, and the invented-riff guard
# =========================================================================
SONGS.append({
    "title": "Kashmir",
    "artist": "Led Zeppelin",
    "arrangement": "DADGAD. The main riff: a sustained low-D drone under a "
                   "stepwise ascending line, the guitar figure cycling in 3 "
                   "against the drums' 4.",
    "passage": "Main riff, bars 1-8.",
    "setup": {"tuning": DADGAD, "capo": 0, "key": "D",
              "time_signature": "4/4", "feel": "straight", "bpm": 80, "key_sounding": "D"},
    "modes_guarded": ["DADGAD", "T6-invented-material", "drone/let_ring",
                      "polyrhythm (unexpressible — see schema_gap)"],
    "basis_note": (
        "CONSTRAINT ONLY. FLE-102 already measured why this song matters: the "
        "cached tab passes 8 of 8 mechanical checks and is a completely invented "
        "riff — a I-IV-V power-chord vamp at frets 0/5/7 where the song has a "
        "chromatic ascent. That is the gap between 62% mechanical and 0% musical, "
        "in one song. So the ground truth here is aimed squarely at it: the "
        "pitch-class SET of that vamp is {D, G, A}, and a bar whose entire "
        "content is {D, G, A} is the defect. I can assert that without a "
        "transcription."
    ),
    "why_in_set": (
        "The DADGAD entry the issue asked for, and the only song in either set "
        "whose tuning is not a uniform shift — so `_tuning_offset` returns None "
        "and the shape-name frame does not exist. Any consumer that assumes a "
        "second frame breaks here."
    ),
    "song_requirements": [
        req("tuning_is", severity="fail", scope="passage", tuning=DADGAD,
            why="Confirmed in FLE-102 — the one thing the cached tab got right."),
        req("pitch_set_not", severity="fail", scope="every_bar",
            pitch_classes=["D", "G", "A"],
            why="T6. A bar sounding exactly and only D/G/A is the invented "
                "I-IV-V vamp FLE-102 found substituted for the riff."),
        req("drone_present", severity="fail", scope="every_bar",
            strings=[6], pitch_classes=["D"],
            why="The low D pedal is the riff's floor. Expressible now via "
                "let_ring / tie / a long duration (FLE-115 §4, §6)."),
        req("stepwise_ascent", severity="fail", scope="passage", min_steps=3,
            why="The line climbs by semitones and tones from the tonic. Three "
                "consecutive adjacent steps is the weakest assertion that still "
                "excludes a fifths-only vamp, and it needs no transcription."),
        req("not_uniform_duration", severity="flag", scope="passage",
            why="T9. Eight bars of identical uniform quarters is the shape of a "
                "fabricated riff, not of this one."),
    ],
    "schema_gap": (
        "The 3-against-4 cannot be expressed. `time_signature` is per measure and "
        "there is one voice, so notating the guitar in 3/4 loses its relationship "
        "to the 4/4 bar, and notating it in 4/4 loses the polyrhythm. FLE-115 did "
        "not close this and I am not asking it to for v1 — a `voices` layer was "
        "already ruled out of scope (FLE-105 Q3). Recorded here so the regression "
        "set states its own blind spot rather than scoring around it."
    ),
    "bars": [
        {"index": i, "kind": "riff", "basis": "constraint", "chord": None,
         "chord_checked": False, "match": None, "rhythm_scored": False,
         "requirements": []}
        for i in range(1, 9)
    ],
    "cannot_verify": [
        {"what": "The riff note for note in DADGAD.",
         "need": "The record plus a reference transcription. I know the tuning, the "
                 "drone, the direction of the line and the polyrhythm; I do not "
                 "know the fret numbers cold, and FLE-102's own verdict on this "
                 "song was that a well-formed invention is the worst failure mode "
                 "we have. I will not add one."},
    ],
})

# =========================================================================
# 6. Wonderwall — the capo
# =========================================================================
_WONDER = ["Em7", "G(wond)", "Dsus4", "A7sus4"] * 2
SONGS.append({
    "title": "Wonderwall",
    "artist": "Oasis",
    "arrangement": "Standard tuning, capo at 2. Gallagher's four voicings, with "
                   "the top two strings held at 3 throughout.",
    "passage": "Verse, bars 1-8: Em7 G Dsus4 A7sus4, twice.",
    "setup": {"tuning": STANDARD, "capo": 2, "key": "Em shapes / F#m sounding",
              "time_signature": "4/4", "feel": "straight", "bpm": 87, "key_sounding": "F# minor"},
    "modes_guarded": ["capo-origin", "sounding_name-transposition",
                      "drill-snippet-capo-inheritance", "T2"],
    "why_in_set": (
        "FLE-103 §4 found the capo gap and excluded this song rather than work "
        "around it; FLE-115 §7 added the field, and this is the bar that holds it "
        "to the convention. Three distinct things fail here if the capo is wrong: "
        "every sounding pitch is two semitones flat, `Chord.sounding_name` is "
        "absent or untransposed, and a drill snippet cut from this tab that takes "
        "capo 0 is silently in a different key (§7.1.4 — the defect that section "
        "was written for). The committed shapes are capo-RELATIVE: the capoed "
        "string is fret 0."
    ),
    "song_requirements": [
        req("capo_is", severity="fail", scope="passage", capo=2,
            why="Not a label. sounding_pitch = open(tuning) + capo + fret."),
        req("sounding_name_present", severity="fail", scope="passage",
            why="FLE-115 §7.2 — with capo > 0 the shape name alone tells the "
                "player the wrong key. Em7 here sounds F#m7."),
        req("absolute_fret_transposition", severity="flag", scope="passage",
            why="A-11. A tab that emits the right SOUNDING pitches as absolute "
                "frets with capo 0 is pitch-correct and hands the player "
                "unidiomatic barres instead of these open shapes. A pedagogy "
                "defect, not a pitch defect, so it is flagged and reported in its "
                "own column rather than failed."),
    ],
    "bars": [
        {
            "index": i,
            "kind": "chord",
            "basis": "note_exact",
            "chord": shape_name(ch),
            "chord_checked": True,
            "chord_spelled_over": "beat",
            "match": "exact",
            # The strum is 8ths with accents; our schema has no strum direction,
            # so committing quarters and scoring the rhythm would fail a correct
            # transcription for a reason I cannot express. Pitch is scored, rhythm
            # is not (A-7) — the bar must still sum to the meter.
            "rhythm_scored": False,
            "requirements": [],
            "beats": [stab(SHAPES[ch]) for _ in range(4)],
        }
        for i, ch in enumerate(_WONDER, start=1)
    ],
    "cannot_verify": [
        {"what": "The strum pattern.",
         "need": "Nothing recording-side — the schema has no strum direction and no "
                 "way to say 'down-down-up-up-down-up'. Out of scope for v1; the "
                 "bars are scored on pitch and meter only (A-7)."},
    ],
})

# =========================================================================
# 7. Little Wing — T1, T7, T11, in Eb
# =========================================================================
_LW = ["Em", "G", "Am", "Em", "Bm", "Bb", "Am", "C"]
SONGS.append({
    "title": "Little Wing",
    "artist": "Jimi Hendrix",
    "arrangement": "Eb tuning, standard shapes, so it sounds a semitone below the "
                   "names. The chord bed of the intro/verse, without Hendrix's fills.",
    "passage": "Intro/verse, bars 1-8: Em G Am Em Bm Bb Am C.",
    "setup": {"tuning": EFLAT, "capo": 0, "key": "Em shapes / Ebm sounding",
              "time_signature": "4/4", "feel": "straight", "bpm": 69, "key_sounding": "Eb minor"},
    "modes_guarded": ["T1-mode-flip", "T7-chromatic-clusters", "T11-split-voicing",
                      "T8-borrowed-from-the-wrong-song", "altered-tuning-Eb"],
    "why_in_set": (
        "FLE-102's worst tab of ten, and among the newest — 1 of 8 chords right, "
        "opening on E major plus a b6 where Em belongs, an 8-bar excerpt using all "
        "12 pitch classes, and a chord list carrying E7#9, which is Purple Haze "
        "and not this song. The chord bed is the part of this arrangement I can "
        "certify cold and it is also where every one of those defects lives, so "
        "the fills being out of scope costs the regression set nothing."
    ),
    "song_requirements": [
        req("pitch_class_absent", severity="fail", scope="passage",
            pitch_classes=["G#"],
            why="T1. In the shape frame G# is the major third of the E tonic; the "
                "song is in Em and needs G natural. FLE-102 found it in bars 4 "
                "and 7 of the cached tab. Note the frame: in Eb tuning the shape "
                "G# sounds G, and both are wrong for the same reason."),
        req("max_distinct_pitch_classes", severity="fail", scope="passage", n=10,
            why="T7. The cached 8 bars used all 12. Ten is generous — this "
                "progression needs eight — and still catches a chromatic cluster."),
        req("max_distinct_pitch_classes", severity="flag", scope="every_bar", n=5,
            why="T7 per bar. The cached m3 was a five-note stack holding both a "
                "minor third and a major seventh. Flag: a legitimate 6-note "
                "voicing with a doubled octave can reach 5 distinct classes."),
        req("tuning_is", severity="fail", scope="passage", tuning=EFLAT,
            why="Confirmed against the catalog in FLE-102."),
    ],
    "bars": [
        {
            "index": i,
            "kind": "chord",
            "basis": "note_exact",
            "chord": shape_name(ch),
            "chord_checked": True,
            # Per BEAT. T11: the cached tab split one six-string voicing across
            # two adjacent beats, so the chord never sounded whole. Checking per
            # bar would have passed it.
            "chord_spelled_over": "beat",
            "match": "exact",
            "rhythm_scored": False,
            "requirements": [],
            "beats": [stab(SHAPES[ch]) for _ in range(4)],
        }
        for i, ch in enumerate(_LW, start=1)
    ],
    "cannot_verify": [
        {"what": "Hendrix's fills, the thumb-over voicings and the slides.",
         "need": "The record plus a reference transcription. The committed bars are "
                 "the chord bed; `match: exact` is correct for them because a chord "
                 "stab with an added note is a different voicing, but it means a "
                 "full transcription with fills will not score against this song. "
                 "Stated rather than hidden: that is a known limit of this entry."},
        {"what": "Whether bar 6 is Bb or Bbmaj7 in the published charts.",
         "need": "Nothing — both spell Bb-D-F plus an optional A. Tolerance A-3 "
                 "accepts either; the third is what matters."},
    ],
})

# =========================================================================
# 8. Big Love — the fingerpicked alternating pattern
# =========================================================================
SONGS.append({
    "title": "Big Love",
    "artist": "Lindsey Buckingham",
    "arrangement": "UNDECIDED, and that is the point — see cannot_verify. "
                   "Standard tuning, E minor, a fast fingerpicked alternating "
                   "pattern in both readings.",
    "passage": "Main figure, bars 1-8.",
    "setup": {"tuning": STANDARD, "capo": 0, "key": "E minor",
              "time_signature": "4/4", "feel": "straight", "bpm": 112, "key_sounding": "E minor"},
    "modes_guarded": ["fingerpicked-alternating-pattern", "T1-mode-flip",
                      "T9-undifferentiated-notes"],
    "basis_note": (
        "CONSTRAINT ONLY, and for a different reason from the other three. It is "
        "not that I cannot hear it — it is that 'correct' is UNDEFINED. FLE-102 "
        "flagged this already: the 1987 Tango in the Night studio part and "
        "Buckingham's solo live arrangement are different guitar parts, the "
        "breakdown names neither, and the song is off-catalog under 'Fleetwood "
        "Mac' (FLE-108). Committing one arrangement as ground truth would be "
        "answering a product question with a transcription. What survives both "
        "readings is what I committed."
    ),
    "why_in_set": (
        "The fingerpicked entry the issue asked for, and the one place a real "
        "pattern-shaped assertion can be made: an alternating-thumb figure has a "
        "moving bass with upper-voice notes BETWEEN the bass notes. 'Undifferentiated "
        "notes' (FLE-102's phrase for the cached tab) fails that structurally, with "
        "no recording and no arrangement decision needed."
    ),
    "song_requirements": [
        req("pitch_class_absent", severity="fail", scope="passage",
            pitch_classes=["G#"],
            why="T1. FLE-102 found bars 7-8 alternating an E bass with G# — the "
                "song is in Em and that G should be natural. True in either "
                "arrangement, which is why it is a fail despite the rest being open."),
        req("min_distinct_bass_pitches", severity="fail", scope="every_bar",
            n=2, strings=[5, 6],
            why="An alternating thumb alternates. One repeated bass note is a "
                "pedal, not this pattern."),
        req("interleaved_upper_voice", severity="fail", scope="every_bar",
            bass_strings=[5, 6],
            why="The defining structure: at least one upper-voice column BETWEEN "
                "two bass columns. A tab whose bass notes are all adjacent has "
                "the notes and not the pattern."),
        req("contains_duration", severity="flag", scope="passage",
            durations=["sixteenth", "triplet-sixteenth", "thirty-second"],
            why="The figure is a fast picking pattern. Flag not fail: uniform "
                "sixteenths are legitimate here, so `not_uniform_duration` would "
                "be a false positive — the wrong guard for this song."),
    ],
    "bars": [
        {"index": i, "kind": "riff", "basis": "constraint", "chord": None,
         "chord_checked": False, "match": None, "rhythm_scored": False,
         "requirements": []}
        for i in range(1, 9)
    ],
    "cannot_verify": [
        {"what": "WHICH ARRANGEMENT we teach — studio 1987 or Buckingham solo live.",
         "need": "A product decision, from Hernan through Miagi, and then the "
                 "record. This is not a tab question and I should not settle it by "
                 "picking one. Until it is settled, no note-exact ground truth for "
                 "this song can exist, and the four assertions above are the whole "
                 "of what is scorable."},
    ],
})

# =========================================================================
# 9. Eruption — tapping and sextuplets
# =========================================================================
SONGS.append({
    "title": "Eruption",
    "artist": "Van Halen",
    "arrangement": "Eb tuning. The tapped section, plus the A-minor arpeggio "
                   "figure that precedes it.",
    "passage": "Tapped section, bars 1-8.",
    "setup": {"tuning": EFLAT, "capo": 0, "key": "A minor shapes / Ab minor sounding",
              "time_signature": "4/4", "feel": "straight", "bpm": 136, "key_sounding": "Ab minor"},
    "modes_guarded": ["technique-tap", "triplet-sixteenth-sextuplets",
                      "T1-mode-flip", "pull_off-links", "altered-tuning-Eb"],
    "basis_note": (
        "CONSTRAINT ONLY. Four assertions, every one of which the cached tab "
        "failed and none of which needs the record: the section is tapped, it is "
        "in sextuplets, it is in A minor, and it is built on pull-offs. FLE-102 "
        "found it written as 'eight repeated open high-E eighth notes' for bar 1 "
        "and an A MAJOR arpeggio labelled A minor for bars 2-3."
    ),
    "why_in_set": (
        "The only entry in either set that exercises `technique: tap` and "
        "`triplet-sixteenth`. Both are new in FLE-115 and both were previously "
        "inexpressible, which is exactly how 'tapping written as plain eighths' "
        "survived to production. A schema field with no regression test is a field "
        "the generator will quietly never use."
    ),
    "song_requirements": [
        req("contains_technique", severity="fail", scope="every_bar",
            techniques=["tap"],
            why="It is the tapped section. Per bar, not per passage: the cached "
                "tab would have passed a passage-level check by tapping once."),
        req("contains_duration", severity="fail", scope="passage",
            durations=["triplet-sixteenth"],
            why="Sextuplets. FLE-102: written as even eighths."),
        req("contains_link", severity="fail", scope="passage",
            links=["pull_off"],
            why="The tap-pull-pull cell is the mechanic. Now expressible "
                "(FLE-115 §4), and the link's direction is checked against the "
                "target fret for free."),
        req("pitch_class_absent", severity="fail", scope="passage",
            pitch_classes=["C#"],
            why="T1. In the shape frame C# is the major third of A; the figure is "
                "A minor and needs C natural. FLE-102 found the arpeggio "
                "spelling A-E-A-C#-E-C#-A-E and calling it 'A minor (open)'."),
        req("not_uniform_duration", severity="flag", scope="passage",
            why="T9. Flag not fail: a sextuplet run IS uniform, legitimately."),
    ],
    "bars": [
        {"index": i, "kind": "melody", "basis": "constraint", "chord": None,
         "chord_checked": False, "match": None, "rhythm_scored": False,
         "requirements": []}
        for i in range(1, 9)
    ],
    "cannot_verify": [
        {"what": "The tapped pitches bar by bar.",
         "need": "The record plus a reference transcription."},
        {"what": "The tuning, exactly.",
         "need": "Nothing actionable. Van Halen I was recorded roughly a "
                 "quarter-tone flat; Eb is a defensible approximation and FLE-102 "
                 "did not count it as a defect. The schema can express a 0.25 curl "
                 "on a note (FLE-115 §5.1) but not a guitar tuned between frets, "
                 "and it should not try."},
    ],
})

# =========================================================================
# 10. Sultans of Swing — the major V in a minor key
# =========================================================================
_SULTANS = ["Dm", "C", "Bb", "A"] * 2
SONGS.append({
    "title": "Sultans of Swing",
    "artist": "Dire Straits",
    "arrangement": "Standard tuning, fingerpicked. The intro's chord bed, "
                   "without Knopfler's fills.",
    "passage": "Intro, bars 1-8: Dm C Bb A, twice.",
    "setup": {"tuning": STANDARD, "capo": 0, "key": "D minor",
              "time_signature": "4/4", "feel": "straight", "bpm": 148, "key_sounding": "D minor"},
    "modes_guarded": ["minor-key-mode-flip-on-the-DOMINANT", "fingerpicked",
                      "bend-led-fills", "T2"],
    "why_in_set": (
        "The trap is bar 4. The progression is Dm-C-Bb-A and that last chord is A "
        "MAJOR — the harmonic-minor dominant, which needs C#. A generator that "
        "derives the chords from the D natural minor scale emits Am and is wrong "
        "on two of eight bars, in the one place a listener notices immediately. "
        "This is the Hotel California test (Bm needs D, F#7 needs A#, one bar "
        "apart) moved into a song whose changes are not in dispute, and FLE-102's "
        "single most common defect was a third in the wrong direction."
    ),
    "song_requirements": [
        req("pitch_class_required", severity="fail", scope="passage",
            pitch_classes=["C#"],
            why="The raised third of the V. Its absence is the mode flip, and it "
                "is arithmetic — no recording needed."),
        req("contains_link", severity="flag", scope="passage",
            links=["hammer_on", "pull_off"],
            why="Knopfler's part is fingerpicked with legato fills. Flag: the "
                "chord bed alone is a correct, if plain, reading of these 8 bars."),
        req("contains_bend", severity="flag", scope="passage", min_semitones=0.5,
            why="Flag, not fail. The intro's first eight bars can legitimately "
                "land without one; this is the second bend-led entry and the "
                "failing threshold lives on Comfortably Numb."),
    ],
    "bars": [
        {
            "index": i,
            "kind": "chord",
            "basis": "note_exact",
            "chord": shape_name(ch),
            "chord_checked": True,
            "chord_spelled_over": "beat",
            # Knopfler arpeggiates rather than strums, so a correct transcription
            # has these pitches distributed across more columns and carries fills.
            # superset_ok + rhythm unscored is the honest tolerance: the harmony is
            # certain, the figure is not.
            "match": "superset_ok",
            "rhythm_scored": False,
            "requirements": [],
            "beats": [stab(SHAPES[ch]) for _ in range(4)],
        }
        for i, ch in enumerate(_SULTANS, start=1)
    ],
    "cannot_verify": [
        {"what": "Knopfler's arpeggiation figure and fills, note for note.",
         "need": "The record plus a reference transcription. The harmony "
                 "Dm-C-Bb-A is not in dispute and is what is committed."},
    ],
})


# --- the scoring contract, committed alongside the data -------------------
SCORING = {
    "units": {
        "bar": (
            "PRIMARY. A bar passes only if every `fail`-severity check that "
            "applies to it passes. `flag` checks never change a pass/fail. "
            "Denominators are always printed."
        ),
        "tab": (
            "SECONDARY, and CONJUNCTIVE: a tab passes only if its setup matches "
            "AND every one of its bars passes. Not an average. A tab with one "
            "wrong bar is not fit for a stranger to practise from, which is the "
            "question FLE-89's bar actually asks."
        ),
        "setup": (
            "Its own table, scored per (song x field) over tuning, capo, "
            "time_signature, feel, key. Reported separately because these are the "
            "cheapest defects to fix and the most expensive to miss — and because "
            "FLE-89 already spent a revision on a tuning claim."
        ),
    },
    "three_scores_never_blended": [
        "setup: correct (song x field) pairs / 50",
        "note_exact: bars passing / 48, over the 6 songs with committed notes",
        "constraint: bars passing / 32, over the 4 songs without them",
    ],
    "why_not_one_number": (
        "FLE-102's finding was that 62% mechanical and 0% musical were both true "
        "of the same corpus, and that a gate built on the first would have passed "
        "an invented riff. Blending a note-level diff with a constraint check "
        "would rebuild that confusion inside a single percentage. The constraint "
        "score is also weaker evidence by construction — it says 'nothing "
        "disqualifying was found', not 'this is the song' — and a reader has to be "
        "able to see which is which."
    ),
    "severities": {
        "fail": "Counts against the bar. Recording-free and arithmetic.",
        "flag": (
            "Reported, never counted. Used where a correct tab could plausibly "
            "trip the check — a chromatic passing tone, a bend-free 8 bars, a "
            "legitimately uniform sextuplet run. A flag is a prompt for a human "
            "ear, which is the only thing that can settle it."
        ),
    },
    "tolerances": [
        {"id": "A-1", "rule": "Same pitch classes and same lowest sounding pitch, "
                              "different string/fret.", "verdict": "PASS, noted"},
        {"id": "A-2", "rule": "An upper voice doubled or dropped by an octave.",
         "verdict": "PASS"},
        {"id": "A-3", "rule": "The fifth omitted from a 4+-note chord.",
         "verdict": "PASS. The THIRD omitted is always FAIL — that is T2, the "
                    "defect in 6 of 10 cached tabs."},
        {"id": "A-4", "rule": "Slash-bass variants (D vs D/F#).", "verdict": "PASS"},
        {"id": "A-5", "rule": "Altered-dominant family (7b9 / 7#9 / 7#5 / alt) "
                              "substituted for each other.",
         "verdict": "PASS. A plain unaltered dominant where an altered one is "
                    "specified is FAIL."},
        {"id": "A-6", "rule": "Extra notes in a bar.",
         "verdict": "PASS where the bar sets match=superset_ok; FAIL where it sets "
                    "match=exact. Stated per bar, never inferred. The two also "
                    "differ in GRANULARITY: match=exact is scored column by column "
                    "and the column count must agree, while superset_ok is scored "
                    "over the whole bar (every reference pitch class present, the "
                    "reference bass still the bass) because a correct arpeggiation "
                    "of the same harmony redistributes it across more columns."},
        {"id": "A-7", "rule": "Durations on a bar with rhythm_scored=false.",
         "verdict": "UNSCORED. The bar must still sum to its time signature — the "
                    "meter check is never relaxed."},
        {"id": "A-8", "rule": "The 12-bar quick change (bar 2 to IV).",
         "verdict": "PASS either way, and only on bar 2."},
        {"id": "A-9", "rule": "feel. straight where shuffle is specified.",
         "verdict": "FAIL. swing and shuffle are NOT interchangeable (FLE-115 "
                    "§2.5). Literal triplet-eighth pairs PLUS feel=shuffle is also "
                    "FAIL — the swing applied twice."},
        {"id": "A-10", "rule": "Enharmonic spelling (F#7 vs Gb7).",
         "verdict": "PASS. Compared by pitch class."},
        {"id": "A-11", "rule": "Capo shapes transposed to absolute frets with "
                               "capo=0 and the right sounding pitches.",
         "verdict": "FLAG, not FAIL, in its own column. The pitches are right and "
                    "the fingering is unidiomatic — a pedagogy defect."},
        {"id": "A-12", "rule": "A different passage of the same song.",
         "verdict": "NOT SCORED. Reported as passage_mismatch. A diff against the "
                    "wrong eight bars is noise, not a measurement."},
        {"id": "A-13", "rule": "A forbidden pitch class appearing only below the "
                               "stated min_duration.",
         "verdict": "FLAG. A chromatic passing tone is not FLE-102's T5, which was "
                    "five bars of structural wrong notes."},
        {"id": "A-14", "rule": "Column 6 of a House of the Rising Sun bar.",
         "verdict": "PASS for any note of the declared chord. A genuine arrangement "
                    "variant."},
    ],
    "what_this_cannot_measure": (
        "A well-formed invention. FLE-102's Kashmir passed 8 of 8 mechanical "
        "checks and had nothing to do with the song. Against this set that "
        "specific invention now fails (pitch_set_not), but the general case does "
        "not: a generator that emits a plausible, meter-correct, in-key riff for a "
        "song whose notes I did not commit scores well and is still wrong. The 48 "
        "note_exact bars are the only part of this set immune to that, and they "
        "cover 6 of 10 songs. Do not report the constraint score as accuracy."
    ),
}


def main():
    doc = {
        "schema_version": 1,
        "issue": "FLE-107",
        "generated_by": "server/scripts/fle107_build_regression_set.py",
        "scorer": "server/scripts/fle107_score_tab.py",
        "tab_schema": "FLE-115 v2 — server/app/models/song.py",
        "author": "Fret (Guitar Curriculum Lead), 2026-10-05",
        "not_the_pilot_set": (
            "server/data/verified_tabs/pilot_verified_set.json (FLE-103) excludes "
            "every song whose material is articulation. This set is chosen for "
            "them. Opposite requirements by design — see FLE-103 §6."
        ),
        "frames": {
            "chord_names_and_roots": "SHAPE names, always.",
            "resolution": "sounding_pc = shape_pc + tuning_offset + capo",
            "fret_origin": "capo-relative; the capoed string is fret 0 (FLE-115 §7)",
        },
        "scoring": SCORING,
        "songs": SONGS,
    }

    counts = {"note_exact": 0, "constraint": 0}
    for s in SONGS:
        for b in s["bars"]:
            counts[b["basis"]] += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(OUT.parents[3])}")
    print(f"  {len(SONGS)} songs, {sum(counts.values())} bars "
          f"({counts['note_exact']} note_exact, {counts['constraint']} constraint)")
    for s in SONGS:
        basis = {b["basis"] for b in s["bars"]}
        print(f"  {s['title'][:34]:<34} {s['setup']['time_signature']:<4} "
              f"{s['setup']['feel']:<8} capo {s['setup']['capo']}  "
              f"{'/'.join(sorted(basis))}")


if __name__ == "__main__":
    main()
