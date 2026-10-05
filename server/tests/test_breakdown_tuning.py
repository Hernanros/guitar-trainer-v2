"""FLE-128 — tuning plumbing: default-to-standard on catalog miss,
tuning_source field, and coherence-check wiring.
"""
from __future__ import annotations

import pytest

from app.ai.breakdown import TUNING_NOTE_MAP, TuningMismatchError, _format_user_message
from app.ai.tab_verification import _check_tuning_coherence, verify
from app.models.song import Breakdown, Chord, Drill, Measure, Tab, TechniqueNote


# ---------------------------------------------------------------------------
# 1. _format_user_message always emits a tuning block
# ---------------------------------------------------------------------------

def test_format_user_message_catalog_hit_emits_catalog_wording():
    msg = _format_user_message("Lenny", "SRV", [], 0.5, "eb_standard")
    assert "KNOWN TUNING" in msg
    assert "Eb" in msg


def test_format_user_message_catalog_miss_emits_default_standard():
    """Off-catalog songs must get a standard-tuning directive, never a blank."""
    msg = _format_user_message("Unknown Song", "Unknown Artist", [], 0.5, None)
    assert "TUNING DEFAULT" in msg
    assert "E" in msg
    # Must suppress inference ("do not infer") not invite it ("infer a tuning")
    assert "do not infer" in msg.lower() or "infer" not in msg.lower()


def test_format_user_message_catalog_miss_does_not_say_known_tuning():
    msg = _format_user_message("Unknown Song", "Unknown Artist", [], 0.5, None)
    assert "KNOWN TUNING" not in msg


# ---------------------------------------------------------------------------
# 2. tuning_source on the Breakdown model
# ---------------------------------------------------------------------------

def _minimal_tab(tuning=None) -> Tab:
    return Tab(
        tuning=tuning or ["E", "A", "D", "G", "B", "E"],
        measures=[],
        time_signature="4/4",
    )


def _minimal_breakdown(tuning=None) -> Breakdown:
    return Breakdown(
        tab=_minimal_tab(tuning),
        chords=[],
        technique_notes=[],
        # omit drills — default_factory=list bypasses min_length
    )


def test_breakdown_tuning_source_defaults_to_none():
    bd = _minimal_breakdown()
    assert bd.tuning_source is None


def test_breakdown_tuning_source_catalog():
    bd = _minimal_breakdown()
    bd.tuning_source = "catalog"
    assert bd.tuning_source == "catalog"


def test_breakdown_tuning_source_default():
    bd = _minimal_breakdown()
    bd.tuning_source = "default"
    assert bd.tuning_source == "default"


def test_breakdown_tuning_source_roundtrips():
    bd = _minimal_breakdown()
    bd.tuning_source = "catalog"
    dumped = bd.model_dump()
    # Omit drills to match the cached-row path (default_factory bypasses min_length)
    dumped.pop("drills", None)
    bd2 = Breakdown.model_validate(dumped)
    assert bd2.tuning_source == "catalog"


def test_breakdown_tuning_source_missing_parses_as_none():
    """Cached rows without tuning_source must still parse."""
    dumped = _minimal_breakdown().model_dump()
    dumped.pop("tuning_source", None)
    dumped.pop("drills", None)  # cached rows omit drills; default_factory bypasses min_length
    bd = Breakdown.model_validate(dumped)
    assert bd.tuning_source is None


# ---------------------------------------------------------------------------
# 3. Coherence check wired as warning, not block
# ---------------------------------------------------------------------------

def _breakdown_with_chords_and_tab(tuning, chord_name, notes_frets) -> Breakdown:
    """Build a minimal Breakdown whose coherence is determined by caller."""
    chord = Chord(
        name=chord_name,
        base_fret=0,
        positions=[
            {"string": 6, "fret": 0},
            {"string": 5, "fret": 2},
            {"string": 4, "fret": 2},
            {"string": 3, "fret": 1},
            {"string": 2, "fret": 0},
            {"string": 1, "fret": 0},
        ],
    )
    beats = [
        {"notes": [{"string": s, "fret": f, "duration": "quarter"}]}
        for s, f in notes_frets
    ]
    tab = Tab(
        tuning=tuning,
        time_signature="4/4",
        measures=[Measure(time_signature="4/4", beats=beats)],
    )
    return Breakdown(tab=tab, chords=[chord], technique_notes=[])


def test_coherence_coherent_breakdown_no_warning():
    """Standard tuning, sounding-pitch names, melody inside the chords -> clean."""
    STD = ["E", "A", "D", "G", "B", "E"]
    bd = _breakdown_with_chords_and_tab(
        STD, "E (open)", [(6, 0), (5, 2), (4, 2), (3, 1)]
    )
    warnings = _check_tuning_coherence(bd)
    tuning_warns = [w for w in warnings if "TUNING_COHERENCE" in w]
    assert not any("REVIEW" in w or "review" in w.lower() for w in tuning_warns), (
        f"Expected no review flag, got: {tuning_warns}"
    )


def test_coherence_eb_shape_names_no_warning():
    """E-flat tuning with shape names must NOT be flagged (the four-FP case)."""
    EB = ["Eb", "Ab", "Db", "Gb", "Bb", "Eb"]
    bd = _breakdown_with_chords_and_tab(
        EB, "E (open)", [(6, 0), (5, 2), (4, 2), (3, 1)]
    )
    warnings = _check_tuning_coherence(bd)
    assert not any("REVIEW" in w for w in warnings), (
        f"False positive on eb/shape-names: {warnings}"
    )


def test_coherence_incoherent_breakdown_warns():
    """Melody in one key, chords in another -> review flag in warnings (never blocking)."""
    STD = ["E", "A", "D", "G", "B", "E"]
    # Chords assert E major pitch classes; notes are a chromatic run avoiding them.
    # NOTE: OWN_CHORDS (blocking) may also fire on this data — that is correct
    # behaviour. The assertion here is only that TUNING_COHERENCE appears as a
    # WARNING, not that OWN_CHORDS is absent.
    bd = _breakdown_with_chords_and_tab(
        STD, "E (open)", [(1, 1), (1, 6), (1, 9), (1, 2)]
    )
    result = verify(bd)
    tuning_warns = [w for w in result.warnings if "TUNING_COHERENCE" in w]
    assert tuning_warns, "Expected TUNING_COHERENCE warning for incoherent breakdown"
    # The TUNING_COHERENCE check itself must never appear in blocking
    tuning_blocking = [b for b in result.blocking if "TUNING_COHERENCE" in b]
    assert not tuning_blocking, f"TUNING_COHERENCE must not block, got: {tuning_blocking}"


def test_coherence_no_chords_inconclusive_not_flagged_as_pass():
    """No chords -> inconclusive, reported as such, not as a clean pass."""
    STD = ["E", "A", "D", "G", "B", "E"]
    bd = Breakdown(
        tab=Tab(
            tuning=STD, time_signature="4/4",
            measures=[Measure(time_signature="4/4",
                              beats=[{"notes": [{"string": 6, "fret": 0, "duration": "quarter"}]}])],
        ),
        chords=[],
        technique_notes=[],
    )
    warnings = _check_tuning_coherence(bd)
    tuning_warns = [w for w in warnings if "TUNING_COHERENCE" in w]
    assert tuning_warns, "Inconclusive check must produce a warning, not silence"
    assert "inconclusive" in tuning_warns[0].lower()


def test_coherence_is_warning_not_blocking():
    """The coherence check itself must never appear in blocking, even on a flag."""
    STD = ["E", "A", "D", "G", "B", "E"]
    bd = _breakdown_with_chords_and_tab(
        STD, "E (open)", [(1, 1), (1, 6), (1, 9), (1, 2)]
    )
    result = verify(bd)
    tuning_blocking = [b for b in result.blocking if "TUNING_COHERENCE" in b]
    assert not tuning_blocking, f"TUNING_COHERENCE must not block: {tuning_blocking}"
