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
4. Two feel fields, on purpose, and only one of them is machine-readable.
   `feel` is prose for a human reading the set ("medium swing — comped on beats").
   `tab_feel` is the `Tab.feel` enum (straight|swing|shuffle, schema v2 §2.5)
   and is the ONLY one that reaches a player: it lands in `tab.feel`, survives
   `Tab.model_validate` in app/verified_tabs.py, is stored in
   song_catalog.verified_tab, and TabNotation.tsx §9.2 draws it as a
   "Swing 8ths" / "Shuffle" badge on the setup line.

   `tab_feel` is DECLARED per song, never inferred from the prose. Pattern-
   matching "swing" out of an English sentence is the same move as reading a
   chord quality off a song title, and this file exists because that move is
   not allowed here. The verifier's V9 checks the two against each other, so a
   disagreement is a failure rather than a silent downgrade to "straight".
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
    # --- FLE-103 widening (2026-10-25) --------------------------------------
    # A key may carry a "|variant" suffix: the chord NAME is everything before
    # the bar, and the suffix only distinguishes two voicings of one name.
    # Needed because Wonderwall's Em7 and G are not the open-position Em7 and G
    # above — Gallagher holds strings 2 and 1 at the 3rd fret through all four
    # chords, and that pedal is the sound of the song. Writing the ordinary
    # shapes would be a different arrangement wearing the same chord names.
    #
    # These frets are CAPO-RELATIVE (§7.1): the capoed string is fret 0. So
    # "6: 0" is the low E string at the capo, sounding F#, not E.
    "Em7|wonderwall":    {6: 0, 5: 2, 4: 2, 3: 0, 2: 3, 1: 3},
    "G|wonderwall":      {6: 3, 5: 2, 4: 0, 3: 0, 2: 3, 1: 3},
    "Dsus4|wonderwall":  {4: 0, 3: 2, 2: 3, 1: 3},
    "A7sus4|wonderwall": {5: 0, 4: 2, 3: 0, 2: 3, 1: 3},
    # 12-bar blues in E shapes (Folsom) and the Sultans of Swing intro bed.
    "B7":      {5: 2, 4: 1, 3: 2, 2: 0, 1: 2},
    "Dm":      {4: 0, 3: 2, 2: 3, 1: 1},
    "Bb":      {5: 1, 4: 3, 3: 3, 2: 3, 1: 1},
}

# The boogie figure: the root droning against 5 - 6 - b7 - 6 on the next string
# up, in straight eighths, swung by the FEEL MARKING rather than by notation
# (§2.5). Eight eighths to the bar.
#
# This is the same figure, note for note, that FLE-107's regression set commits
# for Sweet Home Chicago and Pride and Joy. Deliberately one transcription: two
# of my own files disagreeing about the same eight bars of the same song would
# be worse than either of them being wrong, because then neither is ground truth.
BOOGIE_FRETS = [2, 2, 4, 4, 5, 5, 4, 4]
BOOGIE_DEGREES = ["1", "5", "6", "b7"]


def chord_measure(name, beats=4, duration="quarter", time_sig="4/4"):
    """One measure: the named voicing struck on each of `beats` beats."""
    shape = SHAPES[name]
    notes = [{"string": s, "fret": f, "duration": duration}
             for s, f in sorted(shape.items(), reverse=True)]
    return {"beats": [{"notes": list(notes)} for _ in range(beats)],
            "time_signature": time_sig,
            # Everything before "|" — the NAME is a chord, the variant is only
            # this file's way of telling two voicings of it apart.
            "_chord": name.split("|", 1)[0]}


def boogie_measure(root_string, root_fret=0, duration="eighth", time_sig="4/4"):
    """One bar of the shuffle boogie: a drone plus the 5-6-b7-6 movement.

    Each column is a DOUBLE STOP — the root and the moving note together, which
    is what the hand actually does and what FLE-102 found the generator
    flattening into a single line. The moving voice sits on the next string up
    from the drone, so an E-rooted bar is strings 6+5 and the IV is strings 5+4.

    There is no chord name for this bar, by design — see V10 in the verifier.
    """
    moving_string = root_string - 1
    beats = [
        {"notes": [
            {"string": root_string, "fret": root_fret, "duration": duration},
            {"string": moving_string, "fret": f, "duration": duration},
        ]}
        for f in BOOGIE_FRETS
    ]
    return {"beats": beats, "time_signature": time_sig, "_chord": None,
            "_degrees": {"root": None, "degrees": list(BOOGIE_DEGREES)}}


def boogie_bars(root_note, root_string, n):
    """`n` identical boogie bars rooted on `root_string`, degrees named."""
    out = []
    for _ in range(n):
        m = boogie_measure(root_string)
        m["_degrees"]["root"] = root_note
        out.append(m)
    return out


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
        "tab_feel": "straight",
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
        "tab_feel": "straight",
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
        # Not `shuffle`. The groove is a straight-16th backbeat; the enum's
        # `shuffle` means a hard triplet subdivision whose omitted middle
        # triplet IS the groove, and marking this song that way would have a
        # player bouncing a tune that does not bounce.
        "tab_feel": "straight",
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
        "tab_feel": "straight",
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
        # 120 is the near-2:1 end of the swing range (the ratio argument on
        # FLE-105 Q1 that chose MARKING over notation; Giant Steps at 240 is the
        # near-even end). The enum carries no ratio, which is the point — the
        # player supplies it from the tempo, the way a chart works.
        "tab_feel": "swing",
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
            # REVISED after FLE-111/FLE-116. The original text read "the renderer
            # shows no rhythm at all, so the feel cannot be delivered or scored
            # today" — that was true when written and is now FALSE. §9.1 sizes
            # columns from `note.duration` and §9.2 draws the feel badge, so the
            # product gap closed and what is left is this file's own debt.
            "The swing RATIO is not notated, and that is deliberate: `tab_feel` "
            "is `swing`, so the player is told to swing the eighths and supplies "
            "the ratio from the 120 bpm marking (FLE-105 Q1). What is NOT here is "
            "a comping RHYTHM — these are quarter-note stabs on all four beats, "
            "which is correct as pitch and inert as time. A real medium-swing "
            "comp anticipates and leaves holes. FLE-107 re-notates this song; "
            "until it does, a player practising these eight bars from the screen "
            "gets the changes and not the groove."
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
        "tab_feel": "swing",
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
        # `straight`, and that is the CORRECT marking, not a fallback: bossa is
        # even-eighth music. Its groove is syncopation — where the attacks land
        # — not a subdivision ratio, so there is deliberately no `bossa` value in
        # the enum (FLE-105 Q1): a label would let a writer emit the word in
        # place of the pattern. The pattern is still missing from these eight
        # bars; see `cannot_verify` and FLE-107.
        "tab_feel": "straight",
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
        "tab_feel": "straight",   # even eighths — see Blue Bossa's note
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
        "tab_feel": "straight",
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
        # Still `swing`, at 240 where it is played nearly even. There is no
        # `fast_swing` value and there should not be: the marking says "this is
        # swing music", the tempo says how much. Notating it as triplet pairs —
        # the option rejected on FLE-105 Q1 — would print this bar and Autumn
        # Leaves' identically and teach a player to bounce Coltrane.
        "tab_feel": "swing",
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

    # =======================================================================
    # THE WIDENING — five rock/blues songs, 2026-10-25.
    #
    # Hernan chose `widen-first` over shipping the original ten (answered
    # 2026-10-23): six of ten were jazz or classical standards while the roster
    # was recruited as serious intermediate/advanced ROCK players.
    #
    # What made the first ten skew was NOT, in the end, the renderer — that was
    # my F1 filter and it expired when FLE-116 landed. It was that the ruler
    # could only measure CHORDS. A blues shuffle is a figure, not a chord
    # progression, so there was no way to state its ground truth and no way to
    # check it; the set could therefore only contain chord-changes music. V10
    # (declared scale degrees, checked as equality) is what removes that, and
    # these five are what it buys.
    # =======================================================================
    {
        "title": "Wonderwall",
        "artist": "Oasis",
        "catalog_difficulty": 0.20,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        # §7.1 — frets are capo-relative; the capoed string is fret 0.
        "capo": 2,
        # §7.3 — the key NAMES THE SHAPES. The sounding key is not stored here;
        # `Breakdown._resolve_sounding_names` derives F#m7 etc. from the capo.
        # The parenthetical is for the player reading the setup line, who needs
        # to know what the thing sounds like if they play along with the record.
        "key": "E minor shapes — sounds F# minor with the capo at 2",
        "time_signature": "4/4",
        "feel": "straight eighths",
        "tab_feel": "straight",
        "bpm": 87,
        "passage": "Verse — Em7 G Dsus4 A7sus4, one bar each, twice.",
        "why_in_set": "The song the original ten could not hold: it was excluded "
                      "ONLY because there was no capo field, and §7 added one. "
                      "It is also the lowest-barrier rock song in the catalog, so "
                      "it anchors the bottom of the window with something the "
                      "roster actually asked for.",
        "mode_flip_guard": "Em7 needs G natural and D. The two sus chords must "
                           "contain NO third at all — a Dsus4 with an F# in it is "
                           "a D major chord and the suspension is the sound of "
                           "the verse.",
        "measures": (
            bars("Em7|wonderwall", "G|wonderwall",
                 "Dsus4|wonderwall", "A7sus4|wonderwall")
            + bars("Em7|wonderwall", "G|wonderwall",
                   "Dsus4|wonderwall", "A7sus4|wonderwall")
        ),
        "corrections": [],
        "cannot_verify": [
            "The strum pattern. Nothing recording-side would help: the schema has "
            "no strum direction, so there is no way to write down "
            "down-down-up-up-down-up. The pitches and the capo position are what "
            "is committed; the right hand is not.",
        ],
    },
    {
        "title": "Sweet Home Chicago",
        "artist": "Robert Johnson",
        "catalog_difficulty": 0.30,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "E",
        "time_signature": "4/4",
        "feel": "shuffle — the eighths are swung, hard",
        "tab_feel": "shuffle",
        "bpm": 92,
        "passage": "Bars 1-8 of the 12-bar form: I I I I IV IV I I, boogie figure "
                   "on the two lowest strings.",
        "why_in_set": "The most-played form in the music this roster plays, and "
                      "the first song in the set whose material is a FIGURE "
                      "rather than a chord sequence. It is also the pair to "
                      "Folsom Prison Blues on purpose: identical harmony, "
                      "opposite feel, so a player meets shuffle and straight over "
                      "the same changes.",
        "mode_flip_guard": "No third anywhere in this figure, in either "
                           "direction. The b7 (D) must be present — it is what "
                           "makes this a blues rather than a rock fifth — and a "
                           "G# or G natural appearing here is wrong.",
        "measures": (
            boogie_bars("E", 6, 4)      # I
            + boogie_bars("A", 5, 2)    # IV, same figure one string up
            + boogie_bars("E", 6, 2)    # I
        ),
        "corrections": [],
        "cannot_verify": [
            "Whether the form takes the quick change (bar 2 to the IV). Both "
            "readings are standard; this commits the straight four bars of I. "
            "It is a product decision about which reading we teach, not a "
            "question about the record.",
            "Johnson's own 1936 solo performance, note for note. What is "
            "committed is the standard BAND reading — the electric arrangement "
            "every blues player means by this title. Johnson's is a fingerpicked "
            "country-blues part and a different guitar part entirely; the catalog "
            "credits him, and which of the two Fletcher teaches is Junie's call.",
        ],
    },
    {
        "title": "Folsom Prison Blues",
        "artist": "Johnny Cash",
        "catalog_difficulty": 0.32,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "E",
        "time_signature": "4/4",
        "feel": "straight — boom-chick, bass note then strum",
        "tab_feel": "straight",
        "bpm": 105,
        "passage": "The complete 12-bar form: I I I I IV IV I I V V I I.",
        "why_in_set": "The only song in the set other than Canon in D whose "
                      "excerpt is a COMPLETE musical unit rather than a slice — "
                      "all twelve bars, so the V actually arrives. Bars 1-8 of a "
                      "12-bar never reach the dominant, which means the eight-bar "
                      "convention had been quietly hiding the chord that resolves "
                      "the form.",
        "mode_flip_guard": "B7 must contain D# (its third) and A (its b7). Flatten "
                           "the D# to D and the V stops being a dominant, which is "
                           "the one chord in a 12-bar that cannot be fudged.",
        "measures": bars(("E", 4), ("A", 2), ("E", 2), ("B7", 2), ("E", 2)),
        "corrections": [],
        "cannot_verify": [
            "WHICH RECORDING. This is the 1955 Sun reading, in E. The 1968 Folsom "
            "Prison live version is a tone higher and is the one most people have "
            "heard. The 12-bar harmony is identical either way, so a player "
            "practising this learns the right thing; the absolute key is the only "
            "thing at stake, and I am not going to assert one from memory.",
            "The boom-chick itself. These are quarter-note chord stabs, so what "
            "is committed is the harmony and the form, not Luther Perkins' "
            "alternating bass-and-strum — which is most of what the song SOUNDS "
            "like. Same class of gap as the swing comping on Autumn Leaves.",
        ],
    },
    {
        "title": "Pride and Joy",
        "artist": "Stevie Ray Vaughan",
        "catalog_difficulty": 0.64,
        # Down a semitone, every string by the same interval — so the shape frame
        # and the sounding frame differ by exactly -1 and `frame_shift` derives it.
        "tuning": ["Eb", "Ab", "Db", "Gb", "Bb", "eb"],
        "tuning_catalog_tag": "eb_standard",
        "key": "E shapes — sounds Eb, tuned down a semitone",
        "time_signature": "4/4",
        "feel": "shuffle — a Texas shuffle, swung hard",
        "tab_feel": "shuffle",
        "bpm": 110,
        "passage": "Bars 1-8 of the 12-bar form: I I I I IV IV I I, the shuffle "
                   "bass figure.",
        "why_in_set": "It puts a blues at 0.64, in the band where the set was "
                      "all jazz standards, and it is the one song here whose "
                      "tuning makes the shape and sounding frames differ — so it "
                      "is the only end-to-end evidence that a down-tuned song is "
                      "checked in the right frame rather than scored a semitone "
                      "flat across the board.",
        "mode_flip_guard": "Same as Sweet Home Chicago, one semitone down: no "
                           "third, b7 present. In the SOUNDING frame that is "
                           "Eb-Bb-C-Db, so a checker that forgets the tuning will "
                           "read every note of it as foreign.",
        "measures": (
            boogie_bars("E", 6, 4)
            + boogie_bars("A", 5, 2)
            + boogie_bars("E", 6, 2)
        ),
        "corrections": [],
        "cannot_verify": [
            "SRV's upper-voice fills, the rake, and the double-stop bends. What "
            "is committed is the bass figure only. That is the honest half: the "
            "shuffle under the song is this, and everything above it is the part "
            "I would need the record and a reference transcription to write.",
            "NOTE, not a caveat about the record: these eight bars are IDENTICAL "
            "to Sweet Home Chicago's, note for note, because the figure genuinely "
            "is the same one — the differences are the tuning, the tempo and the "
            "fills I did not commit. That is a real fact about the music and a "
            "real curation problem: two pilot days could serve what looks like "
            "the same screen. Flagged for Nadia to sequence, not to be fixed by "
            "inventing a difference.",
        ],
    },
    {
        "title": "Sultans of Swing",
        "artist": "Dire Straits",
        "catalog_difficulty": 0.74,
        "tuning": ["E", "A", "D", "G", "B", "e"],
        "tuning_catalog_tag": "standard",
        "key": "D minor",
        "time_signature": "4/4",
        "feel": "straight eighths",
        "tab_feel": "straight",
        "bpm": 148,
        "passage": "Intro, bars 1-8: Dm C Bb A, one bar each, twice.",
        "why_in_set": "The top of the window a pilot user can actually reach. "
                      "With player_level capped at 0.70 the highest window is "
                      "0.55-0.85, and before this the only rock in it was Hotel "
                      "California. It also carries the mode flip pointing the "
                      "OTHER way — the fourth chord of a D minor tune is A MAJOR.",
        "mode_flip_guard": "A must contain C# — a natural C makes it Am and the "
                           "turnaround loses its pull. This is the reverse of "
                           "FLE-102's T1 (a major third where a minor belonged), "
                           "so a generator patched to 'use the natural minor in a "
                           "minor key' fails exactly here.",
        "measures": bars("Dm", "C", "Bb", "A", "Dm", "C", "Bb", "A"),
        "corrections": [],
        "cannot_verify": [
            "Knopfler's arpeggiation figure and his fills, note for note. The "
            "harmony Dm-C-Bb-A is not in dispute and is what is committed; the "
            "fingerpicked pattern that makes it sound like this record is not, "
            "and would need the record plus a reference transcription.",
        ],
    },
]


def main():
    out = {
        # 2 since FLE-103's feel pass: every tab now carries the §2.5 `feel`
        # enum. Bumped because app/verified_tabs.py stamps this value into each
        # row's provenance (`set_schema_version`), so a prod row can be traced to
        # the shape of the file that produced it rather than to "FLE-103" at large.
        "schema_version": 2,
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
            "feel": "swing and shuffle are MARKED in tab.feel, never written out "
                    "as triplet pairs (§2.5). Bossa is `straight` — even-eighth "
                    "music whose groove is syncopation, not a subdivision ratio. "
                    "A tab carrying feel=swing AND literal triplet durations is "
                    "double-applied swing and fails V9.",
        },
        "songs": [],
    }
    for s in SONGS:
        song = dict(s)
        measures = song.pop("measures")
        song["chord_sequence"] = [m.get("_chord") for m in measures]
        # One entry per measure, parallel to chord_sequence: exactly one of the
        # two is set for any bar. A bar with neither is a bar nothing checks,
        # which the verifier now fails rather than skipping (V10).
        degree_sequence = [m.get("_degrees") for m in measures]
        if any(degree_sequence):
            song["degree_sequence"] = degree_sequence
        song["tab"] = {
            "tuning": song["tuning"],
            # §2.5 — the enum, inside `tab`, because that is the object
            # app/verified_tabs.py runs through `Tab.model_validate`. Declared,
            # never derived from the prose `feel` (convention 4). Absent would
            # default to "straight" silently, which is how three swing tunes were
            # about to be served unmarked, so it is always written out.
            "feel": song["tab_feel"],
            "measures": [{"beats": m["beats"], "time_signature": m["time_signature"]}
                         for m in measures],
        }
        # §7.1 — written only when there is one. `Tab.capo` defaults to 0, and
        # unlike `feel` that default is SAFE here: a 0 means "no capo", which is
        # true of every song that omits it. The feel default was unsafe because
        # "straight" is a positive instruction about how to play; "no capo" is
        # not an instruction at all.
        if song.get("capo"):
            song["tab"]["capo"] = song["capo"]
        out["songs"].append(song)

    dest = pathlib.Path(__file__).resolve().parents[1] / "data" / "verified_tabs"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "pilot_verified_set.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {path} — {len(out['songs'])} songs")


if __name__ == "__main__":
    main()
