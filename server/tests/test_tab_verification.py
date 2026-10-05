"""FLE-106 — generator verification pass.

Pure pydantic-model tests for app.ai.tab_verification. No DB, no network,
no LLM: every case here builds a Breakdown object directly and asserts on
verify()'s blocking/warnings split.

Each hard-block test is a minimal reproduction of one of FLE-102's named
defect classes (see server/scripts/fle102_tab_audit.py's C1-C6 and FLE-103's
V1/V5-V8); the uniform-duration and cross-song-reuse tests pin the two
checks that must NEVER block (C4 is a feel judgement, C7/T7 is not
implemented at all — see the module docstring for why).
"""
from app.ai.tab_verification import verify
from app.models.song import Beat, Breakdown, Chord, ChordPosition, Drill, Measure, Note, Tab


def _note(string: int, fret: int, duration: str = "quarter") -> Note:
    return Note(string=string, fret=fret, duration=duration)


def _chord(name: str) -> Chord:
    return Chord(name=name, positions=[ChordPosition(string=6, fret=0)])


def _breakdown(
    measures: list[Measure],
    chords: list[Chord],
    tuning: list[str] | None = None,
    drills: list[Drill] | None = None,
) -> Breakdown:
    tab = Tab(measures=measures, tuning=tuning or ["E", "A", "D", "G", "B", "e"])
    kwargs = {"tab": tab, "chords": chords, "technique_notes": []}
    # Breakdown.drills carries min_length=2 on EXPLICIT input only (see its
    # docstring) — omit the kwarg entirely rather than pass [] so every
    # existing drills-less test keeps hitting the unvalidated default_factory
    # path instead of a spurious min_length failure.
    if drills is not None:
        kwargs["drills"] = drills
    return Breakdown(**kwargs)


def _drill(name: str, snippet_measures: list[Measure], tuning: list[str] | None = None) -> Drill:
    """A minimally-valid Drill — every field other than name/tab_snippet is
    filler that satisfies the model's own constraints (FLE-72 ladder span,
    MIN/MAX_DRILL_REPETITIONS) without being load-bearing for these tests."""
    return Drill(
        name=name,
        target_skill_temp_id="skill-1",
        song_specific=False,
        what="Practice the passage.",
        tab_snippet=Tab(measures=snippet_measures, tuning=tuning or ["E", "A", "D", "G", "B", "e"]),
        start_bpm=60,
        target_bpm=70,
        repetitions=8,
        success_criterion="It rings clean.",
    )


# ---------------------------------------------------------------------------
# Baseline: a correct Em bar passes clean.
# ---------------------------------------------------------------------------

def test_clean_breakdown_passes_with_no_blocking_or_warnings():
    # Open Em shape struck as a chord (half note), then its G and B picked
    # out singly (quarter notes) — 2 + 1 + 1 = 4 beats, mixed durations so
    # C4's uniform-grid warning doesn't fire either.
    measures = [
        Measure(
            beats=[
                Beat(notes=[_note(6, 0, "half"), _note(5, 2, "half"), _note(4, 2, "half")]),
                Beat(notes=[_note(3, 0, "quarter")]),
                Beat(notes=[_note(2, 0, "quarter")]),
            ],
            time_signature="4/4",
        )
    ]
    bd = _breakdown(measures, [_chord("Em")])
    result = verify(bd)
    assert result.ok
    assert result.blocking == []
    assert result.warnings == []


# ---------------------------------------------------------------------------
# C1 / METER
# ---------------------------------------------------------------------------

def test_meter_mismatch_blocks():
    measures = [
        Measure(
            beats=[Beat(notes=[_note(6, 0, "quarter")]), Beat(notes=[_note(6, 0, "quarter")])],
            time_signature="4/4",
        )
    ]
    bd = _breakdown(measures, [_chord("E")])
    result = verify(bd)
    assert not result.ok
    assert any(d.startswith("METER:") for d in result.blocking)


# ---------------------------------------------------------------------------
# C2 / UNPLAYABLE
# ---------------------------------------------------------------------------

def test_same_string_twice_in_one_beat_blocks():
    measures = [
        Measure(
            beats=[Beat(notes=[_note(6, 0, "whole"), _note(6, 2, "whole")])],
            time_signature="4/4",
        )
    ]
    bd = _breakdown(measures, [_chord("E")])
    result = verify(bd)
    assert not result.ok
    assert any(d.startswith("UNPLAYABLE:") for d in result.blocking)


# ---------------------------------------------------------------------------
# C3 / FRET_SPAN
# ---------------------------------------------------------------------------

def test_fret_span_over_five_blocks():
    measures = [
        Measure(
            beats=[Beat(notes=[_note(6, 1, "whole"), _note(5, 9, "whole")])],
            time_signature="4/4",
        )
    ]
    bd = _breakdown(measures, [_chord("E")])
    result = verify(bd)
    assert not result.ok
    assert any(d.startswith("FRET_SPAN:") for d in result.blocking)


# ---------------------------------------------------------------------------
# C5 / SPLIT_CHORD
# ---------------------------------------------------------------------------

def test_chord_split_across_disjoint_adjacent_beats_blocks():
    measures = [
        Measure(
            beats=[
                Beat(notes=[_note(6, 0, "eighth"), _note(2, 0, "eighth"), _note(1, 0, "eighth")]),
                Beat(notes=[_note(5, 2, "eighth"), _note(4, 2, "eighth"), _note(3, 0, "eighth")]),
                Beat(notes=[_note(6, 0, "quarter")]),
                Beat(notes=[_note(6, 0, "half")]),
            ],
            time_signature="4/4",
        )
    ]
    bd = _breakdown(measures, [_chord("Em")])
    result = verify(bd)
    assert not result.ok
    assert any(d.startswith("SPLIT_CHORD:") for d in result.blocking)


# ---------------------------------------------------------------------------
# V6 / RENDERER_WIDTH — downgraded from blocking to a warning by FLE-118
# ---------------------------------------------------------------------------

def test_more_than_four_beats_in_a_measure_warns_but_does_not_block():
    """This test asserted `not result.ok` until FLE-118, and the inversion is
    the point rather than a concession to make a suite pass.

    V6's entire justification was TabNotation.tsx's fixed `MEASURE_WIDTH`, which
    FLE-116 (commit 0b8f2ee, spec §9.1) replaced with width-from-content; the
    component now computes measure offsets as a running sum and says in as many
    words that "a 4/4 bar of straight sixteenths no longer overflows its box."

    Blocking outlived its reason and became actively destructive. `verify` is
    the live write gate — breakdown.py:982 turns anything in `blocking` into
    TabVerificationError, which refuses to serve the breakdown at all — and the
    bar below is the most ordinary rhythm in guitar music: eight straight
    eighths in 4/4. FLE-103 §1b measured 43% of prod bars above 4 columns. So
    as a block this gate threw away correct music to protect a renderer
    constraint that no longer existed, and it would have made FLE-118's RHYTHM
    block (sixteenths, triplets, rests — all of which push past 4 columns)
    worse than useless.

    It stays as a WARNING because a client older than FLE-116's bundle does
    still draw these overlapped; that is a client-version problem with a
    self-healing fix (the next OTA), not grounds to discard the breakdown. The
    exact constraint on how many columns a bar may hold is METER (V1), which
    this bar satisfies: 8 eighths = 4 beats.
    """
    measures = [
        Measure(
            beats=[Beat(notes=[_note(6, 0, "eighth")]) for _ in range(8)],
            time_signature="4/4",
        )
    ]
    bd = _breakdown(measures, [_chord("E")])
    result = verify(bd)
    assert result.ok, result.blocking
    assert not any("RENDERER_WIDTH" in d for d in result.blocking)
    assert any(w.startswith("RENDERER_WIDTH:") for w in result.warnings), result.warnings


# ---------------------------------------------------------------------------
# C6 / OWN_CHORDS — standard tuning, single frame
# ---------------------------------------------------------------------------

def test_note_outside_declared_chord_blocks():
    # Em = {E, G, B}. Fret 1 on the low E string sounds F — foreign to Em.
    measures = [Measure(beats=[Beat(notes=[_note(6, 1, "whole")])], time_signature="4/4")]
    bd = _breakdown(measures, [_chord("Em")])
    result = verify(bd)
    assert not result.ok
    assert any(d.startswith("OWN_CHORDS:") for d in result.blocking)


def test_note_inside_declared_chord_does_not_block():
    # Em = {E, G, B}. Open low E string sounds E — in the chord.
    measures = [Measure(beats=[Beat(notes=[_note(6, 0, "whole")])], time_signature="4/4")]
    bd = _breakdown(measures, [_chord("Em")])
    result = verify(bd)
    assert result.ok


def test_unparseable_chord_name_is_skipped_not_blocked():
    """A chord name the parser can't read contributes nothing to `allowed` —
    it must not make every note foreign by default."""
    measures = [Measure(beats=[Beat(notes=[_note(6, 0, "whole")])], time_signature="4/4")]
    bd = _breakdown(measures, [_chord("???")])
    result = verify(bd)
    assert result.ok


# ---------------------------------------------------------------------------
# C6 / OWN_CHORDS — Eb standard, two-frame check (shape name vs. sounding pitch)
# ---------------------------------------------------------------------------

def test_eb_tuning_shape_name_frame_is_checked_before_flagging_foreign():
    """Eb standard: a note that sounds a semitone below its chord's NAME is
    legitimate (the player fingers an 'E' shape; the guitar sounds Eb) — this
    must pass via the shape-name frame, not be flagged foreign."""
    eb_tuning = ["Eb", "Ab", "Db", "Gb", "Bb", "Eb"]
    measures = [Measure(beats=[Beat(notes=[_note(6, 0, "whole")])], time_signature="4/4")]
    bd = _breakdown(measures, [_chord("E")], tuning=eb_tuning)
    result = verify(bd)
    assert result.ok, result.blocking


def test_eb_tuning_still_catches_a_genuinely_wrong_note():
    """The two-frame check must not become a free pass — a note wrong in BOTH
    frames (the Comfortably Numb class of defect: wrong in the sounding frame
    AND the shape-name frame) still blocks."""
    eb_tuning = ["Eb", "Ab", "Db", "Gb", "Bb", "Eb"]
    # Fret 2 on the low string in Eb tuning sounds F natural — not in E major
    # ({E,G#,B}) in the sounding frame, nor in the shape-name frame either.
    measures = [Measure(beats=[Beat(notes=[_note(6, 2, "whole")])], time_signature="4/4")]
    bd = _breakdown(measures, [_chord("E")], tuning=eb_tuning)
    result = verify(bd)
    assert not result.ok
    assert any(d.startswith("OWN_CHORDS:") for d in result.blocking)


# ---------------------------------------------------------------------------
# C4 / UNIFORM_DURATION — warns, never blocks
# ---------------------------------------------------------------------------

def test_uniform_duration_grid_warns_but_does_not_block():
    # 4 quarter-note beats, all the same duration, all inside Em and within
    # the 4-beat renderer width — the ONLY thing wrong is the flattened
    # rhythm, so this must warn without tripping any blocking check.
    measures = [
        Measure(
            beats=[
                Beat(notes=[_note(6, 0, "quarter")]),
                Beat(notes=[_note(3, 0, "quarter")]),
                Beat(notes=[_note(2, 0, "quarter")]),
                Beat(notes=[_note(6, 0, "quarter")]),
            ],
            time_signature="4/4",
        )
    ]
    bd = _breakdown(measures, [_chord("Em")])
    result = verify(bd)
    assert result.ok, f"C4 must never be a blocking defect, got {result.blocking}"
    assert any(w.startswith("UNIFORM_DURATION:") for w in result.warnings)


# ---------------------------------------------------------------------------
# PITCH_DEGENERACY (FLE-127) — warns, never blocks
# ---------------------------------------------------------------------------

def test_repeated_single_pitch_bar_warns_but_does_not_block():
    """Song 107 (Eruption) m1: eight strikes of the open high E, nothing
    else. Not in the record, not practisable — but it is wrong because it
    has no pitch content, not because of notation or physics, so it warns
    rather than blocks."""
    measures = [
        Measure(
            beats=[Beat(notes=[_note(1, 0, "eighth")]) for _ in range(8)],
            time_signature="4/4",
        )
    ]
    bd = _breakdown(measures, [_chord("E")])
    result = verify(bd)
    assert result.ok, result.blocking
    assert any(w.startswith("PITCH_DEGENERACY:") for w in result.warnings), result.warnings


def test_varied_pitch_run_does_not_trip_pitch_degeneracy():
    """The selectivity claim from the issue: a bar of straight eighths at
    varying pitches (legitimate tapping/pentatonic material) trips
    UNIFORM_DURATION (C4, a rhythm property) but must NOT trip
    PITCH_DEGENERACY (a pitch property) — the two checks are orthogonal."""
    measures = [
        Measure(
            beats=[
                Beat(notes=[_note(1, fret, "eighth")])
                for fret in [0, 3, 5, 3, 0, 3, 5, 7]
            ],
            time_signature="4/4",
        )
    ]
    # Chord name left unparseable (same device as
    # test_unparseable_chord_name_is_skipped_not_blocked) so OWN_CHORDS
    # contributes nothing — this test isolates C4 vs PITCH_DEGENERACY, not
    # chord-tone membership.
    bd = _breakdown(measures, [_chord("???")])
    result = verify(bd)
    assert result.ok, result.blocking
    assert any(w.startswith("UNIFORM_DURATION:") for w in result.warnings), result.warnings
    assert not any(w.startswith("PITCH_DEGENERACY:") for w in result.warnings), result.warnings


def test_single_note_bar_does_not_trip_pitch_degeneracy():
    """A single whole note is not degenerate — there is nothing repeated.
    The check needs more than one sounding note before "every note is the
    same pitch" says anything at all."""
    measures = [Measure(beats=[Beat(notes=[_note(6, 0, "whole")])], time_signature="4/4")]
    bd = _breakdown(measures, [_chord("E")])
    result = verify(bd)
    assert result.ok
    assert not any(w.startswith("PITCH_DEGENERACY:") for w in result.warnings), result.warnings


# ---------------------------------------------------------------------------
# FLE-126 — drills[].tab_snippet must run through the same checks as the
# main tab, not bypass every one of them.
# ---------------------------------------------------------------------------

def _filler_drill() -> Drill:
    """A second, uncontroversial drill — Breakdown.drills carries
    min_length=2 on explicit input, and this filler must trip NOTHING so
    each test below isolates the one check it is naming."""
    measures = [Measure(beats=[Beat(notes=[_note(3, 0, "whole")])], time_signature="4/4")]
    return _drill("Isolate the pull-off", measures)


def test_clean_drill_snippets_pass_alongside_a_clean_main_tab():
    main_measures = [Measure(beats=[Beat(notes=[_note(6, 0, "whole")])], time_signature="4/4")]
    drills = [_filler_drill(), _filler_drill()]
    bd = _breakdown(main_measures, [_chord("Em")], drills=drills)
    result = verify(bd)
    assert result.ok, result.blocking


def test_drill_snippet_meter_mismatch_blocks():
    """Pre-FLE-126 this bar was invisible to verify() — nothing read
    drills[].tab_snippet at all, so a two-beat bar claiming 4/4 shipped."""
    main_measures = [Measure(beats=[Beat(notes=[_note(6, 0, "whole")])], time_signature="4/4")]
    bad_snippet = [
        Measure(
            beats=[Beat(notes=[_note(6, 0, "quarter")]), Beat(notes=[_note(6, 0, "quarter")])],
            time_signature="4/4",
        )
    ]
    drills = [_drill("Half-step crawl", bad_snippet), _filler_drill()]
    bd = _breakdown(main_measures, [_chord("Em")], drills=drills)
    result = verify(bd)
    assert not result.ok
    assert any(d.startswith("METER:") and "drill 0" in d for d in result.blocking), result.blocking


def test_drill_snippet_same_string_twice_in_one_beat_blocks():
    main_measures = [Measure(beats=[Beat(notes=[_note(6, 0, "whole")])], time_signature="4/4")]
    bad_snippet = [
        Measure(beats=[Beat(notes=[_note(6, 0, "whole"), _note(6, 2, "whole")])], time_signature="4/4")
    ]
    drills = [_drill("Alternate pluck", bad_snippet), _filler_drill()]
    bd = _breakdown(main_measures, [_chord("Em")], drills=drills)
    result = verify(bd)
    assert not result.ok
    assert any(d.startswith("UNPLAYABLE:") and "drill 0" in d for d in result.blocking), result.blocking


def test_drill_snippet_fret_span_over_five_blocks():
    main_measures = [Measure(beats=[Beat(notes=[_note(6, 0, "whole")])], time_signature="4/4")]
    bad_snippet = [
        Measure(beats=[Beat(notes=[_note(6, 1, "whole"), _note(5, 9, "whole")])], time_signature="4/4")
    ]
    drills = [_drill("Stretch reach", bad_snippet), _filler_drill()]
    bd = _breakdown(main_measures, [_chord("Em")], drills=drills)
    result = verify(bd)
    assert not result.ok
    assert any(d.startswith("FRET_SPAN:") and "drill 0" in d for d in result.blocking), result.blocking


# ---------------------------------------------------------------------------
# DRILL_CHORD — the Little Wing drill1 defect itself: a voicing scored
# against the chord its OWN name claims, not against breakdown.chords.
# ---------------------------------------------------------------------------

def test_drill_chord_identity_catches_the_shipped_little_wing_defect():
    """Song 106 drill1, "E -> E7#9 Colour Swap": voicing `6:0 5:2 4:2 3:1 2:3
    1:2` sounds {E, G#, B, D, F#} — E9, not E7#9 (E7#9 needs G, the #9, not
    F#, the plain 9th). F# is foreign to E7#9's own pitch classes and must
    block, exactly like OWN_CHORDS already blocks a foreign note against
    breakdown.chords."""
    main_measures = [Measure(beats=[Beat(notes=[_note(6, 0, "whole")])], time_signature="4/4")]
    snippet = [
        Measure(
            beats=[
                Beat(
                    notes=[
                        _note(6, 0, "whole"),
                        _note(5, 2, "whole"),
                        _note(4, 2, "whole"),
                        _note(3, 1, "whole"),
                        _note(2, 3, "whole"),
                        _note(1, 2, "whole"),
                    ]
                )
            ],
            time_signature="4/4",
        )
    ]
    drills = [_drill("E -> E7#9 Colour Swap", snippet), _filler_drill()]
    bd = _breakdown(main_measures, [_chord("E")], drills=drills)
    result = verify(bd)
    assert not result.ok
    assert any(
        d.startswith("DRILL_CHORD:") and "drill 0" in d and "F#" in d for d in result.blocking
    ), result.blocking


def test_drill_chord_identity_passes_when_the_voicing_matches_the_name():
    """Same drill name, same shape, but string 1 frets the #9 (G, fret 3)
    instead of the plain 9th (F#, fret 2) — now the voicing actually IS
    E7#9, and must not block."""
    main_measures = [Measure(beats=[Beat(notes=[_note(6, 0, "whole")])], time_signature="4/4")]
    snippet = [
        Measure(
            beats=[
                Beat(
                    notes=[
                        _note(6, 0, "whole"),
                        _note(5, 2, "whole"),
                        _note(4, 2, "whole"),
                        _note(3, 1, "whole"),
                        _note(2, 3, "whole"),
                        _note(1, 3, "whole"),
                    ]
                )
            ],
            time_signature="4/4",
        )
    ]
    drills = [_drill("E -> E7#9 Colour Swap", snippet), _filler_drill()]
    bd = _breakdown(main_measures, [_chord("E")], drills=drills)
    result = verify(bd)
    assert result.ok, result.blocking


def test_drill_name_with_only_a_bare_root_letter_is_not_chord_checked():
    """FLE-122's warning applies here too: a lone capital letter ('Held E
    Voicing') is too easily a stray reference (a string name, an article) to
    trust as a chord claim. This snippet plays an F natural — foreign to ANY
    reading of 'E' — and must NOT block, because the name never clears the
    bar that makes DRILL_CHORD trust it."""
    main_measures = [Measure(beats=[Beat(notes=[_note(6, 0, "whole")])], time_signature="4/4")]
    snippet = [Measure(beats=[Beat(notes=[_note(6, 1, "whole")])], time_signature="4/4")]
    drills = [_drill("Embellishment Hammer Inside a Held E Voicing", snippet), _filler_drill()]
    bd = _breakdown(main_measures, [_chord("E")], drills=drills)
    result = verify(bd)
    assert result.ok, result.blocking
