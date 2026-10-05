"""FLE-103 — build the hand-verified pilot set from curated chord tables.

Zero spend: no LLM, no DB, no network. Emits
`server/data/verified_tabs/pilot_verified_set.json` in the real `Tab` schema
(server/app/models/song.py) so FLE-107 can score generator output as a DIFF
against it instead of against my opinion.

    server/.venv/bin/python server/scripts/fle103_build_pilot_set.py

Division of labour with fle103_verify_pilot_tabs.py
---------------------------------------------------
This script KNOWS the chord shapes. The verifier does NOT — it re-derives the
pitch of every emitted {string, fret} from the tuning alone and checks the
result against the declared chord NAME. So a shape I typed wrong here is caught
there, rather than being published because I was confident. That separation is
the whole point; do not merge these two files.

Representation conventions (deliberate, and different from the generator's)
---------------------------------------------------------------------------
1. A muted string emits NO note. The generator writes fret=-1, which `Note`
   does not document (-1 is a ChordPosition idiom) and which TabNotation.tsx
   renders as the glyph "x" sitting on a string line. A chord the player is
   told not to sound is an absence, not an event.
2. WITHDRAWN by FLE-117 (schema v2 §10 row 9). This used to read "every
   measure holds AT MOST 4 beats... a renderer constraint", because
   TabNotation.tsx pinned MEASURE_WIDTH = LEFT_MARGIN + 4 * BEAT_WIDTH + 8
   while laying beats out at beatIndex * BEAT_WIDTH with no clamp, so beat 5
   escaped its own measure (31 of 72 cached prod bars, 43%).

   The measurement was right and the conclusion was wrong: a renderer constant
   had become a rule about the MUSIC, and the notation was being written down
   to what the component could draw. §9.1 derives each measure's width from its
   own content, so the limit is gone and so is this convention. Measures here
   are bounded by the time signature and nothing else.
3. Every note in a chord-stab beat carries the same duration, and that is
   CORRECT here, not the T4 "uniform grid" defect FLE-102 named: these are
   simultaneous notes of one voicing, not a flattened melody.
"""
import json
import pathlib

# --- tuning / pitch model (builder's own copy; verifier re-derives independently)
STD = {6: 40, 5: 45, 4: 50, 3: 55, 2: 59, 1: 64}  # midi, standard EADGBe

# --- chord shapes -----------------------------------------------------------
# string -> fret. Muted strings are simply absent (convention 1 above).
# Every one of these was spelled out by hand and is re-checked by the verifier.
SHAPES = {
    # open-position triads
    "Em":      {6: 0, 5: 2, 4: 2, 3: 0, 2: 0, 1: 0},
    "E":       {6: 0, 5: 2, 4: 2, 3: 1, 2: 0, 1: 0},
    "G":       {6: 3, 5: 2, 4: 0, 3: 0, 2: 0, 1: 3},
    "C":       {5: 3, 4: 2, 3: 0, 2: 1, 1: 0},
    "A":       {5: 0, 4: 2, 3: 2, 2: 2, 1: 0},
    "Am":      {5: 0, 4: 2, 3: 2, 2: 1, 1: 0},
    "D":       {4: 0, 3: 2, 2: 3, 1: 2},
    "D/F#":    {6: 2, 4: 0, 3: 2, 2: 3, 1: 2},
    # the two-chord engine of "A Horse with No Name" (the 200200 shape)
    "D6/9":    {6: 2, 5: 0, 4: 0, 3: 2, 2: 0, 1: 0},
    # barre triads
    "Bm":      {5: 2, 4: 4, 3: 4, 2: 3, 1: 2},
    "F#m":     {6: 2, 5: 4, 4: 4, 3: 2, 2: 2, 1: 2},
    "Gm":      {6: 3, 5: 5, 4: 5, 3: 3, 2: 3, 1: 3},
    # sevenths — the third is load-bearing in every one of these
    "Em7":     {6: 0, 5: 2, 4: 0, 3: 0, 2: 0, 1: 0},
    "Am7":     {5: 0, 4: 2, 3: 0, 2: 1, 1: 0},
    "F#7":     {6: 2, 5: 4, 4: 2, 3: 3, 2: 2, 1: 2},
    "Gb7":     {6: 2, 5: 4, 4: 2, 3: 3, 2: 2, 1: 2},  # = F#7, spelled flat in jazz charts
    "G7":      {6: 3, 5: 5, 4: 3, 3: 4, 2: 3, 1: 3},
    "G7b9":    {6: 3, 4: 3, 3: 4, 2: 3, 1: 4},
    "Gm7":     {6: 3, 5: 5, 4: 3, 3: 3, 2: 3, 1: 3},
    "F7":      {6: 1, 5: 3, 4: 1, 3: 2, 2: 1, 1: 1},
    "Fm7":     {6: 1, 5: 3, 4: 1, 3: 1, 2: 1, 1: 1},
    "Cm7":     {5: 3, 4: 5, 3: 3, 2: 4},
    "Dm7":     {5: 5, 4: 3, 3: 5, 2: 3},
    "D7":      {5: 5, 4: 4, 3: 5, 2: 3},
    # root - #11 - b7 - 3 from the bottom. The 5th is dropped, which is normal
    # for an altered dominant. My first pass put the #11 at 2:6, which is F,
    # not G# — the verifier caught it; see the FLE-103 writeup, "what I corrected".
    "D7#11":   {5: 5, 4: 6, 3: 5, 2: 7},
    "Dm7b5":   {5: 5, 4: 6, 3: 5, 2: 6},
    "Am7b5":   {5: 0, 4: 1, 3: 0, 2: 1},
    "Bb7":     {5: 1, 4: 3, 3: 1, 2: 3},
    "Cmaj7":   {5: 3, 4: 5, 3: 4, 2: 5},
    "Bbmaj7":  {5: 1, 4: 3, 3: 2, 2: 3},
    "Ebmaj7":  {5: 6, 4: 8, 3: 7, 2: 8},
    "Bmaj7":   {5: 2, 4: 4, 3: 3, 2: 4},
    "Gmaj7":   {6: 3, 4: 4, 3: 4, 2: 3},
    "Fmaj7":   {4: 3, 3: 2, 2: 1, 1: 0},
}


def chord_measure(name, beats=4, duration="quarter", time_sig="4/4"):
    """One measure: the named voicing struck on each of `beats` beats."""
    shape = SHAPES[name]
    notes = [{"string": s, "fret": f, "duration": duration}
             for s, f in sorted(shape.items(), reverse=True)]
    return {"beats": [{"notes": list(notes)} for _ in range(beats)],
            "time_signature": time_sig,
            "_chord": name}


def riff_measure(events, time_sig="4/4"):
    """One measure of single notes. events = [(string, fret, duration), ...]"""
    return {"beats": [{"notes": [{"string": s, "fret": f, "duration": d}]}
                      for s, f, d in events],
            "time_signature": time_sig,
            "_chord": None}


def bars(*specs):
    """specs: chord name, or (name, n_bars)."""
    out = []
    for sp in specs:
        name, n = (sp, 1) if isinstance(sp, str) else sp
        out.extend(chord_measure(name) for _ in range(n))
    return out


# --- the ten ----------------------------------------------------------------
# `cannot_verify` is a first-class field. An empty list is a claim, so it is
# only empty where I mean it.
SONGS = [
    {
        "title": "A Horse with No Name",
        "artist": "America",
        "catalog_difficulty": 0.19,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "E minor",
        "time_signature": "4/4",
        "feel": "straight eighths",
        "bpm": 122,
        "passage": "Verse — the entire song alternates two chords; 8 bars = two full cycles.",
        "why_in_set": "Two chords, both open position, no articulation. The cleanest "
                      "possible test that the generator can spell a minor tonic.",
        "mode_flip_guard": "Em must contain G natural. A G# here is the T1 defect.",
        "measures": bars(("Em", 2), ("D6/9", 2), ("Em", 2), ("D6/9", 2)),
        "corrections": [],
        "cannot_verify": [],
    },
    {
        "title": "Zombie",
        "artist": "The Cranberries",
        "catalog_difficulty": 0.28,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "E minor",
        "time_signature": "4/4",
        "feel": "straight eighths",
        "bpm": 84,
        "passage": "Verse/chorus loop — Em C G D/F#, one bar each, twice.",
        "why_in_set": "The canonical i-VI-III-VII minor loop. Four chords, every "
                      "third present, nothing to articulate.",
        "mode_flip_guard": "Em needs G natural; D/F# needs F# in the bass AND as a third.",
        "measures": bars("Em", "C", "G", "D/F#", "Em", "C", "G", "D/F#"),
        "corrections": [],
        "cannot_verify": [
            "Whether the recorded voicing is D/F# or plain D. Both are correct "
            "harmony; the bass note is an arrangement detail. Scored as D major "
            "either way."
        ],
    },
    {
        "title": "Ain't No Sunshine",
        "artist": "Bill Withers",
        "catalog_difficulty": 0.36,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "A minor",
        "time_signature": "4/4",
        "feel": "straight, heavy backbeat",
        "bpm": 76,
        "passage": "Verse — Am Em7 G Am, one bar each, twice.",
        "why_in_set": "A minor tonic with a m7 on the v. Tests the third on two "
                      "different minor chords in one passage.",
        "mode_flip_guard": "Am must contain C natural, not C#. Em7 must contain "
                           "G natural and D.",
        "measures": bars("Am", "Em7", "G", "Am", "Am", "Em7", "G", "Am"),
        "corrections": [],
        "cannot_verify": [
            "This is not a guitar record — the hook is strings and Withers' voice, "
            "and the guitar is a supporting part. The harmony is certain; whether "
            "it should be TAUGHT as a guitar song is a curation call, not a tab "
            "question. Flagged for Junie/Nadia."
        ],
    },
    {
        "title": "Canon in D",
        "artist": "Johann Pachelbel",
        "catalog_difficulty": 0.43,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "D major",
        "time_signature": "4/4",
        "feel": "straight",
        "bpm": 64,
        "passage": "The ground bass cycle — D A Bm F#m G D G A, one bar each. "
                   "8 bars is exactly one full cycle.",
        "why_in_set": "Public domain (migration 0015 tags it so), and the only "
                      "song in the set whose 8-bar excerpt is a COMPLETE musical "
                      "unit rather than a slice. Two barre minors test position.",
        "mode_flip_guard": "Bm needs D natural; F#m needs A natural. Both are "
                           "barre shapes, so a one-fret position error (T3) "
                           "changes the chord.",
        "measures": bars("D", "A", "Bm", "F#m", "G", "D", "G", "A"),
        "corrections": [],
        "cannot_verify": [],
    },
    {
        "title": "Autumn Leaves",
        "artist": "Joseph Kosma",
        "catalog_difficulty": 0.47,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "G minor (relative Bb major)",
        "time_signature": "4/4",
        "feel": "medium swing — comped on beats, not a melody line",
        "bpm": 120,
        "passage": "A section, bars 1-8: ii-V-I in Bb then ii-V-i in Gm.",
        "why_in_set": "The clearest ii-V-I in the catalog. Four chord qualities "
                      "(m7, dom7, maj7, m7b5) in eight bars — nowhere to hide a "
                      "missing third.",
        "mode_flip_guard": "Am7b5 must contain Eb (the flat five). A natural E "
                           "makes it Am7 and destroys the cadence.",
        "measures": bars("Cm7", "F7", "Bbmaj7", "Ebmaj7",
                         "Am7b5", "D7", ("Gm", 2)),
        "corrections": [],
        "cannot_verify": [
            "The swing feel. Comped quarter-note stabs are correct as PITCH and "
            "wrong as FEEL, and the renderer shows no rhythm at all, so the feel "
            "cannot be delivered or scored today. Not counted as a defect "
            "against the generator; counted as a product gap."
        ],
    },
    {
        "title": "Take the 'A' Train",
        "artist": "Billy Strayhorn",
        "catalog_difficulty": 0.55,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "C major",
        "time_signature": "4/4",
        "feel": "medium swing",
        "bpm": 160,
        "passage": "A section, bars 1-8.",
        "why_in_set": "The D7#11 is the whole identity of this tune. An altered "
                      "dominant is the single hardest thing for the generator to "
                      "get right, and it is checkable without a recording.",
        "mode_flip_guard": "D7#11 must contain G# (the #11). Drop it and you have "
                           "an ordinary D7, which is the wrong tune.",
        "measures": bars(("Cmaj7", 2), ("D7#11", 2), "Dm7", "G7", ("Cmaj7", 2)),
        "corrections": [],
        "cannot_verify": [],
    },
    {
        "title": "Blue Bossa",
        "artist": "Kenny Dorham",
        "catalog_difficulty": 0.58,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "C minor",
        "time_signature": "4/4",
        "feel": "bossa nova (even eighths, not swung)",
        "bpm": 148,
        "passage": "Bars 1-8 of the 16-bar form.",
        "why_in_set": "Minor ii-V-i. Pairs with Autumn Leaves as the same cadence "
                      "a minor third away, so a generator that transposes a house "
                      "pattern instead of transcribing (T7) shows up as an exact "
                      "shape match between the two.",
        "mode_flip_guard": "Cm7 needs Eb; Dm7b5 needs Ab; G7b9 needs B natural "
                           "AND Ab. The b9 is the sound of the cadence.",
        "measures": bars(("Cm7", 2), ("Fm7", 2), "Dm7b5", "G7b9", ("Cm7", 2)),
        "corrections": [],
        "cannot_verify": [
            "Charts vary between G7alt / G7b9 / G7#5 in bar 6. I committed G7b9. "
            "Any altered G dominant should score as correct; a plain G7 should not."
        ],
    },
    {
        "title": "The Girl from Ipanema",
        "artist": "Antonio Carlos Jobim",
        "catalog_difficulty": 0.60,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "F major",
        "time_signature": "4/4",
        "feel": "bossa nova (even eighths)",
        "bpm": 136,
        "passage": "A section, bars 1-8.",
        "why_in_set": "The Gb7 in bar 6 is a tritone substitution — the one chord "
                      "in the set that is not diatonic to anything. A generator "
                      "guessing from the key signature cannot reach it.",
        "mode_flip_guard": "Gb7 must contain Bb (= A#, its third). Gm7 and Gb7 sit "
                           "one fret apart; a position slip (T3) swaps them.",
        "measures": bars(("Fmaj7", 2), ("G7", 2), "Gm7", "Gb7", ("Fmaj7", 2)),
        "corrections": [],
        "cannot_verify": [],
    },
    {
        "title": "Hotel California",
        "artist": "Eagles",
        "catalog_difficulty": 0.65,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "B minor",
        "time_signature": "4/4",
        "feel": "straight, reggae-inflected backbeat",
        "bpm": 75,
        "passage": "The 8-bar progression, one bar each: Bm F#7 A E G D Em F#7.",
        "why_in_set": "The hardest mode-flip trap available without articulation. "
                      "A minor key whose dominant is a MAJOR seventh chord — the "
                      "generator must put A# over a B-minor tonality, which is "
                      "exactly the move it gets backwards in the other direction "
                      "(T1: major third where the minor belongs).",
        "mode_flip_guard": "Bm needs D natural. F#7 needs A# — not A. Both in the "
                           "same 8 bars, in opposite directions. Em needs G natural; "
                           "E major needs G#, two bars apart.",
        "measures": bars("Bm", "F#7", "A", "E", "G", "D", "Em", "F#7"),
        "corrections": [],
        "cannot_verify": [
            "The recorded intro is a 12-string arrangement with the voicings spread "
            "across two guitars. I committed the single-guitar chord reading, which "
            "is the correct harmony and the standard way it is taught. A 6-string "
            "transcription cannot be note-for-note identical to the record here."
        ],
    },
    {
        "title": "Giant Steps",
        "artist": "John Coltrane",
        "catalog_difficulty": 0.95,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "modulating — B / G / Eb major thirds cycle",
        "time_signature": "4/4",
        "feel": "fast swing",
        "bpm": 240,
        "passage": "Bars 1-8: Bmaj7 D7 Gmaj7 Bb7 Ebmaj7 Ebmaj7 Am7 D7.",
        "why_in_set": "The only song I can certify at the top of the difficulty "
                      "range (see the writeup: above 0.65 this catalog is hard "
                      "BECAUSE of articulation, which the schema drops). Three key "
                      "centres in eight bars — no house pattern survives it.",
        "mode_flip_guard": "Bmaj7 needs D# and A#. Gmaj7 needs F#. Ebmaj7 needs D. "
                           "Each maj7 is a different key centre; a single borrowed "
                           "scale produces wrong notes in two of the three.",
        "measures": bars("Bmaj7", "D7", "Gmaj7", "Bb7", ("Ebmaj7", 2), "Am7", "D7"),
        "corrections": [],
        "cannot_verify": [
            "Bar 6 is Ebmaj7 held for two bars in the common chart; some fake books "
            "split it. Harmonically identical, so either should score as correct."
        ],
    },
]


def main():
    out = {
        "schema_version": 1,
        "issue": "FLE-103",
        "generated_by": "server/scripts/fle103_build_pilot_set.py",
        "verifier": "server/scripts/fle103_verify_pilot_tabs.py",
        "selector_contract": {
            "window": "ABS(song_catalog.difficulty - player_level) <= 0.15",
            "player_level_floor": 0.20,
            "source": "server/app/selectors/today_song.py, player_level.py",
        },
        "conventions": {
            "muted_strings": "omitted, never fret=-1",
            # FLE-117 (§10 row 9): `max_beats_per_measure: 4` and its rationale
            # "TabNotation.tsx MEASURE_WIDTH" are gone. That was a renderer
            # constant that had leaked into ground truth and bounded the MUSIC
            # to what the component could draw. §9.1 sizes every measure from
            # its own content, so the limit no longer exists and ground truth
            # must stop being shaped by it.
            "beats_per_measure": "unbounded — measure width is derived from "
                                 "content (schema v2 §9.1)",
        },
        "songs": [],
    }
    for s in SONGS:
        song = dict(s)
        measures = song.pop("measures")
        song["chord_sequence"] = [m.get("_chord") for m in measures]
        song["tab"] = {
            "tuning": song["tuning"],
            "measures": [{"beats": m["beats"], "time_signature": m["time_signature"]}
                         for m in measures],
        }
        out["songs"].append(song)

    dest = pathlib.Path(__file__).resolve().parents[1] / "data" / "verified_tabs"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "pilot_verified_set.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {path} — {len(out['songs'])} songs")


if __name__ == "__main__":
    main()
