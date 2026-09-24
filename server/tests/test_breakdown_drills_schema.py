"""Pydantic schema tests for the Drill model + Breakdown.drills field (Plan 04.1-01, Task 1).

Pure Pydantic — no DB, no mocks. Covers:
  - Drill required-field / range / type constraints
  - Drill target_bpm > start_bpm @model_validator (Wave 2 fix)
  - Breakdown.drills default-empty (backward compat with pre-4.1 cached rows)
  - Breakdown.drills min_length=2 / max_length=4 (Landmine #3 strict validation
    when drills is EXPLICITLY provided by caller — Sonnet output path)
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.models.song import (
    MAX_DRILL_REPETITIONS,
    MAX_LADDER_SPAN_BPM,
    MIN_DRILL_REPETITIONS,
    Beat,
    Breakdown,
    Drill,
    Measure,
    Note,
    Tab,
)


def _tab_snippet() -> Tab:
    """Minimal valid Tab for use as a Drill.tab_snippet."""
    return Tab(
        measures=[
            Measure(
                beats=[
                    Beat(notes=[Note(string=3, fret=3, duration="quarter")]),
                    Beat(notes=[Note(string=3, fret=4, duration="quarter")]),
                ],
                time_signature="4/4",
            )
        ],
        tuning=["E", "A", "D", "G", "B", "e"],
    )


def _valid_drill(**overrides) -> Drill:
    """Construct a valid Drill, allowing per-test overrides via kwargs."""
    defaults = dict(
        name="Isolate the b3→3 slide",
        target_skill_temp_id=str(uuid.uuid4()),
        song_specific=True,
        what="Two-note slide on the G string. Nothing else.",
        tab_snippet=_tab_snippet(),
        start_bpm=60,
        target_bpm=70,
        repetitions=10,
        success_criterion="Slide arrives on the beat without a bump.",
        common_trap="Beginners over-anchor the ring finger.",
    )
    defaults.update(overrides)
    return Drill(**defaults)


# ---------------------------------------------------------------------------
# Drill schema tests
# ---------------------------------------------------------------------------


def test_drill_schema_valid():
    """A Drill with all required fields at valid ranges passes validation."""
    drill = _valid_drill()
    assert drill.name == "Isolate the b3→3 slide"
    assert drill.song_specific is True
    assert drill.start_bpm == 60
    assert drill.target_bpm == 70
    assert drill.repetitions == 10
    assert drill.common_trap == "Beginners over-anchor the ring finger."


def test_drill_schema_missing_required():
    """Omitting any required field raises ValidationError; common_trap is optional."""
    required_fields = [
        "name",
        "target_skill_temp_id",
        "song_specific",
        "what",
        "tab_snippet",
        "start_bpm",
        "target_bpm",
        "repetitions",
        "success_criterion",
    ]
    base = dict(
        name="X",
        target_skill_temp_id=str(uuid.uuid4()),
        song_specific=True,
        what="do stuff",
        tab_snippet=_tab_snippet(),
        start_bpm=60,
        target_bpm=70,
        repetitions=10,
        success_criterion="unlocked",
    )
    for field in required_fields:
        payload = {k: v for k, v in base.items() if k != field}
        with pytest.raises(ValidationError):
            Drill(**payload)

    # common_trap is optional — omitting it must NOT raise
    drill = Drill(**base)
    assert drill.common_trap is None


def test_drill_schema_bpm_ranges():
    """start_bpm/target_bpm must satisfy ge/le bounds."""
    with pytest.raises(ValidationError):
        _valid_drill(start_bpm=39)          # below 40
    with pytest.raises(ValidationError):
        _valid_drill(start_bpm=181, target_bpm=200)  # above 180
    with pytest.raises(ValidationError):
        _valid_drill(target_bpm=221)        # above 220


def test_drill_schema_bpm_multiple_of_5_not_enforced():
    """Multiple-of-5 is SYSTEM_PROMPT guidance only — Pydantic accepts 63."""
    drill = _valid_drill(start_bpm=63, target_bpm=73)
    assert drill.start_bpm == 63


def test_drill_target_bpm_gt_start_bpm():
    """W2 fix: target_bpm must be strictly greater than start_bpm.

    Any positive delta validates — the 10-15 BPM range is SYSTEM_PROMPT guidance,
    not Pydantic-enforced.
    """
    # equal → invalid
    with pytest.raises(ValidationError):
        _valid_drill(start_bpm=70, target_bpm=70)
    # target below start → invalid
    with pytest.raises(ValidationError):
        _valid_drill(start_bpm=70, target_bpm=65)
    # any positive delta → valid
    drill = _valid_drill(start_bpm=60, target_bpm=61)
    assert drill.target_bpm > drill.start_bpm


# ---------------------------------------------------------------------------
# FLE-72 — tap budget. Both bounds CLAMP rather than raise, because a raise
# anywhere under `drills` costs the song every drill (breakdown.py Landmine #3
# soft-fail strips the whole key and re-parses).
# ---------------------------------------------------------------------------


def test_drill_repetitions_clamped_into_tap_budget():
    """Out-of-range rep counts are pulled to the nearest bound, not rejected."""
    assert _valid_drill(repetitions=30).repetitions == MAX_DRILL_REPETITIONS
    assert _valid_drill(repetitions=13).repetitions == MAX_DRILL_REPETITIONS
    assert _valid_drill(repetitions=1).repetitions == MIN_DRILL_REPETITIONS
    # In-range values are untouched, including both bounds.
    assert _valid_drill(repetitions=MIN_DRILL_REPETITIONS).repetitions == MIN_DRILL_REPETITIONS
    assert _valid_drill(repetitions=8).repetitions == 8
    assert _valid_drill(repetitions=MAX_DRILL_REPETITIONS).repetitions == MAX_DRILL_REPETITIONS


def test_drill_repetitions_non_int_still_type_errors():
    """The clamp passes non-ints through so Pydantic's own type error still fires."""
    with pytest.raises(ValidationError):
        _valid_drill(repetitions="lots")


def test_drill_ladder_span_capped():
    """A stretch wider than MAX_LADDER_SPAN_BPM clamps target_bpm down."""
    drill = _valid_drill(start_bpm=60, target_bpm=140)
    assert drill.target_bpm == 60 + MAX_LADDER_SPAN_BPM
    assert drill.start_bpm == 60, "start_bpm is the warmup — it must not move"
    # Exactly at the cap is untouched.
    assert _valid_drill(start_bpm=60, target_bpm=75).target_bpm == 75
    # Inside the cap is untouched.
    assert _valid_drill(start_bpm=60, target_bpm=70).target_bpm == 70


def test_drill_ladder_span_cap_holds_off_grid_start_bpm():
    """The cap never clamps target_bpm to or below an off-grid start_bpm."""
    drill = _valid_drill(start_bpm=63, target_bpm=200)
    assert drill.target_bpm == 78
    assert drill.target_bpm > drill.start_bpm


def test_drill_worst_case_tap_count_is_48():
    """The point of FLE-72: the taps owed before the rating pills unlock.

    Mirrors mobile advanceRepOrTempo — `repetitions` taps at every 5-BPM rung from
    start_bpm to target_bpm inclusive, in one sitting. The pre-FLE-72 worst case was
    9 rungs x 30 reps = 270.
    """
    def taps(drill: Drill) -> int:
        rungs = (drill.target_bpm - drill.start_bpm) // 5 + 1
        return rungs * drill.repetitions

    # Worst case a Drill can now represent: both bounds asked for far past the cap,
    # both clamped. target_bpm=220 / repetitions=30 is the pre-FLE-72 ceiling.
    worst = _valid_drill(start_bpm=60, target_bpm=220, repetitions=30)
    assert taps(worst) == 48

    # The shape the prompt actually asks for: +10 BPM at 8 reps.
    typical = _valid_drill(start_bpm=60, target_bpm=70, repetitions=8)
    assert taps(typical) == 24


# ---------------------------------------------------------------------------
# Breakdown.drills field tests
# ---------------------------------------------------------------------------


def _valid_breakdown_kwargs() -> dict:
    return dict(
        tab=Tab(
            measures=[
                Measure(
                    beats=[Beat(notes=[Note(string=1, fret=0, duration="quarter")])]
                )
            ]
        ),
        chords=[],
        technique_notes=[],
    )


def test_breakdown_drills_default_empty():
    """Backward compat: constructing Breakdown WITHOUT drills yields drills=[].

    default_factory=list bypasses min_length=2 because Pydantic v2's list-length
    validation runs on validate/parse of PROVIDED input, not on defaults.
    """
    b = Breakdown(**_valid_breakdown_kwargs())
    assert b.drills == []


def test_breakdown_drills_min_length_enforced_on_provided_input():
    """LANDMINE #3: one drill (explicitly provided) raises ValidationError."""
    kwargs = _valid_breakdown_kwargs()
    kwargs["drills"] = [_valid_drill()]  # only 1 drill
    with pytest.raises(ValidationError):
        Breakdown(**kwargs)


def test_breakdown_drills_max_length_enforced_on_provided_input():
    """LANDMINE #3: 5 drills raises ValidationError (max_length=4)."""
    kwargs = _valid_breakdown_kwargs()
    kwargs["drills"] = [_valid_drill() for _ in range(5)]
    with pytest.raises(ValidationError):
        Breakdown(**kwargs)


def test_breakdown_drills_two_to_four_accepted():
    """2, 3, and 4 drills all validate cleanly."""
    for n in (2, 3, 4):
        kwargs = _valid_breakdown_kwargs()
        kwargs["drills"] = [_valid_drill() for _ in range(n)]
        b = Breakdown(**kwargs)
        assert len(b.drills) == n


def test_breakdown_with_drills_roundtrip():
    """Breakdown with drills round-trips through model_dump_json → model_validate_json."""
    kwargs = _valid_breakdown_kwargs()
    kwargs["drills"] = [_valid_drill(), _valid_drill(name="Another drill")]
    original = Breakdown(**kwargs)

    serialized = original.model_dump_json()
    reparsed = Breakdown.model_validate_json(serialized)

    assert len(reparsed.drills) == 2
    assert reparsed.drills[0].name == "Isolate the b3→3 slide"
    assert reparsed.drills[1].name == "Another drill"
    # Full-object round-trip equality
    assert reparsed.model_dump() == original.model_dump()


def test_breakdown_model_json_schema_includes_drills():
    """Breakdown.model_json_schema() has a `drills` property and Drill in $defs."""
    schema = Breakdown.model_json_schema()
    assert "drills" in schema["properties"], (
        "Breakdown schema must expose a `drills` property so Sonnet tool_use picks it up"
    )
    assert "Drill" in schema.get("$defs", {}), (
        "Breakdown schema must include Drill under $defs (nested Pydantic model)"
    )
