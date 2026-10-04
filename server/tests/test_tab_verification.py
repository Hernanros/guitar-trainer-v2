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
from app.models.song import Beat, Breakdown, Chord, ChordPosition, Measure, Note, Tab


def _note(string: int, fret: int, duration: str = "quarter") -> Note:
    return Note(string=string, fret=fret, duration=duration)


def _chord(name: str) -> Chord:
    return Chord(name=name, positions=[ChordPosition(string=6, fret=0)])


def _breakdown(measures: list[Measure], chords: list[Chord], tuning: list[str] | None = None) -> Breakdown:
    tab = Tab(measures=measures, tuning=tuning or ["E", "A", "D", "G", "B", "e"])
    return Breakdown(tab=tab, chords=chords, technique_notes=[])


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
# V6 / RENDERER_WIDTH
# ---------------------------------------------------------------------------

def test_more_than_four_beats_in_a_measure_blocks():
    measures = [
        Measure(
            beats=[Beat(notes=[_note(6, 0, "eighth")]) for _ in range(8)],
            time_signature="4/4",
        )
    ]
    bd = _breakdown(measures, [_chord("E")])
    result = verify(bd)
    assert not result.ok
    assert any(d.startswith("RENDERER_WIDTH:") for d in result.blocking)


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
