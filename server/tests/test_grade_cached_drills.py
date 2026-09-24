"""Tests for scripts/grade_cached_drills.py — the FLE-32 offline quality filter.

WHY THESE TESTS EXIST
FLE-32 offers two ways to answer "are these drills worth handing a user": pay
for another eval run, or apply a filter that is mechanically checkable offline.
This script is the second option, so the thing under test is not "does it grade"
— `grade_eval` already does that and has its own tests — it is **does the filter
hold back the right drills**. A filter that is too loose banks the song back to
the user as a drill; one that is too strict leaves the bank empty and the
session generator still degenerate.

The fixtures are the REAL production rows, because that is where the two
false-positive classes were found and a test on invented data would not have
caught either:

  L1 over-fires at a fixed 3-beat run. Song 71 D1's snippet is literally one bar
  of Pride and Joy's shuffle (cover 100%); song 73 D1 shares three notes with
  the song because both walk the same Bb minor box (cover 38%). Absolute-3 calls
  those the same defect. The ratio separates them, and both directions are
  asserted below.

  A1 cannot tell "promises a chord" from "mentions a chord". Song 76 D1's prose
  says "Hold an open E chord shape and pick only the two bass strings... No
  treble strings at all" — it explicitly disclaims polyphony and A1 still fires.
  That is why A1 is reported but not in the default exclusion set, asserted in
  test_a1_is_not_a_default_exclusion.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.grade_cached_drills import (
    _DEFAULT_EXCLUDE,
    _DEFAULT_L1_COVER_MIN,
    attribute,
    excluded_drill_indices,
    l1_cover,
    song_from_row,
)


def _beat(*pairs: tuple[int, int]) -> dict:
    """One beat as the cached breakdown JSON spells it."""
    return {"notes": [{"string": s, "fret": f, "duration": "eighth"} for s, f in pairs]}


def _measure(*beats: dict) -> dict:
    return {"beats": list(beats), "time_signature": "4/4"}


# Pride and Joy m1 — the E shuffle, eight beats of two alternating double-stops.
_PJ_MAIN_M1 = _measure(
    _beat((6, 0), (5, 2)),
    _beat((6, 0), (4, 2)),
    _beat((6, 0), (5, 2)),
    _beat((6, 0), (4, 2)),
    _beat((6, 0), (5, 2)),
    _beat((6, 0), (4, 2)),
    _beat((6, 0), (5, 2)),
    _beat((6, 0), (4, 2)),
)

# Song 73 m2 — a Bb-minor-box phrase on string 3 into string 2.
_LOVERS_MAIN_M2 = _measure(
    _beat((3, 7)), _beat((3, 7)), _beat((3, 9)), _beat((3, 7)), _beat((2, 8))
)


def _row(song_id: int, title: str, artist: str, main: list[dict], drills: list[dict]):
    """A stand-in for the `songs` row; `song_from_row` only reads attributes."""
    return SimpleNamespace(
        id=song_id,
        title=title,
        artist=artist,
        breakdown={"tab": {"tuning": [], "measures": main}, "drills": drills},
    )


def _drill_json(name: str, tier: int, what: str, measures: list[dict], skill_id: str):
    return {
        "name": name,
        "what": what,
        "mechanic_tier": tier,
        "song_specific": False,
        "target_skill_temp_id": skill_id,
        "tab_snippet": {"tuning": [], "measures": measures},
    }


_SKILL = "ae9df818-b5b9-4e7e-b36a-000000000000"


def test_verbatim_bar_copy_covers_the_whole_drill() -> None:
    """Song 71 D1: the snippet IS one bar of the shuffle, so cover is 1.0.

    This is the defect L1 was written to catch — the "drill" hands the user the
    song's own riff. It must be held back.
    """
    row = _row(
        71,
        "Pride and Joy",
        "Stevie Ray Vaughan",
        [_PJ_MAIN_M1],
        [
            _drill_json(
                "Lock In the E-Chord Shuffle Pulse",
                3,
                "Hold the two-finger shape and alternate picking the open bass string.",
                [
                    _measure(
                        _beat((6, 0), (5, 2)),
                        _beat((6, 0), (4, 2)),
                        _beat((6, 0), (5, 2)),
                        _beat((6, 0), (4, 2)),
                    )
                ],
                _SKILL,
            )
        ],
    )
    song = song_from_row(row, {_SKILL})
    assert l1_cover(song, song.drills[0]) == pytest.approx(1.0)
    assert "L1" in attribute(song)[1]
    # And the default threshold is low enough to actually catch it.
    assert l1_cover(song, song.drills[0]) >= _DEFAULT_L1_COVER_MIN


def test_shared_scale_box_is_not_a_reproduction() -> None:
    """Song 73 D1: three notes coincide inside the same box; cover stays low.

    L1 still FAILS here (three consecutive beats really are shared), but the
    drill is a hammer/pull exercise, not the song. The ratio is what keeps it
    bankable, so assert the gate fires AND the ratio spares it.
    """
    row = _row(
        73,
        "Cause We've Ended as Lovers",
        "Jeff Beck",
        [_LOVERS_MAIN_M2],
        [
            _drill_json(
                "Hammer & Pull in the Bb Minor Box",
                2,
                "Alternate hammer-ons ascending and pull-offs descending, one string at a time.",
                [
                    _measure(
                        _beat((3, 7)),
                        _beat((3, 9)),
                        _beat((3, 9)),
                        _beat((3, 7)),
                        _beat((2, 8)),
                        _beat((2, 10)),
                        _beat((2, 10)),
                        _beat((2, 8)),
                    )
                ],
                _SKILL,
            )
        ],
    )
    song = song_from_row(row, {_SKILL})
    cover = l1_cover(song, song.drills[0])
    assert "L1" in attribute(song)[1], "the raw gate must still report the shared run"
    assert cover < _DEFAULT_L1_COVER_MIN, f"cover {cover} would wrongly exclude the drill"


def test_a1_is_not_a_default_exclusion() -> None:
    """Prose that DISCLAIMS polyphony still trips A1 — so A1 cannot be a filter.

    Song 76 D1 says "No treble strings at all" and A1 flags it for the word
    "chord". The gate result is kept (it is real information about the prompt),
    but the default exclusion set must not contain A1, or correct fingerstyle
    drills get held back.
    """
    row = _row(
        76,
        "Big Love",
        "Fleetwood Mac",
        [_measure(_beat((6, 0)), _beat((4, 2)), _beat((5, 2)), _beat((4, 2)))],
        [
            _drill_json(
                "Lock the Thumb: Bass-Only Alternation on E",
                2,
                "Hold an open E chord shape and pick only the two bass strings. "
                "No treble strings at all.",
                [_measure(_beat((6, 0)), _beat((5, 2)), _beat((6, 0)), _beat((5, 2)))],
                _SKILL,
            )
        ],
    )
    song = song_from_row(row, {_SKILL})
    assert "A1" in attribute(song)[1], "A1 fires on the substring, which is the point"
    assert "A1" not in _DEFAULT_EXCLUDE


def test_l5_drop_is_blamed_on_the_later_drill() -> None:
    """Pride and Joy's real tiers are [3, 4, 4, 2]; D4 is the one out of place.

    Blaming D3 would leave the same ordering gap, so the attribution has to land
    on the drill that drops.
    """
    main = [_PJ_MAIN_M1]
    drills = [
        _drill_json(f"D{i}", tier, "single notes", [_measure(_beat((1, 12)))], _SKILL)
        for i, tier in enumerate([3, 4, 4, 2], start=1)
    ]
    song = song_from_row(_row(71, "Pride and Joy", "SRV", main, drills), {_SKILL})
    blame = attribute(song)
    assert "L5" in blame[4]
    assert "L5" not in blame[3]


def test_unresolvable_skill_id_is_blamed_per_drill() -> None:
    """L2 here means "the id does not resolve to a node this user owns"."""
    row = _row(
        71,
        "Pride and Joy",
        "SRV",
        [_PJ_MAIN_M1],
        [
            _drill_json(
                "Ghost skill", 3, "single notes", [_measure(_beat((1, 12)))], _SKILL
            )
        ],
    )
    song = song_from_row(row, set())  # user owns no nodes at all
    assert "L2" in attribute(song)[1]


def test_missing_tier_fails_l5_rather_than_passing_silently() -> None:
    """A cached row with no mechanic_tier is ungradeable, which L5 treats as FAIL."""
    drill = _drill_json("No tier", 3, "single notes", [_measure(_beat((1, 12)))], _SKILL)
    del drill["mechanic_tier"]
    song = song_from_row(_row(71, "P", "S", [_PJ_MAIN_M1], [drill]), {_SKILL})
    assert song.drills[0].mechanic_tier is None
    assert "L5" in attribute(song)[1]


def test_excluded_indices_are_zero_based_for_the_backfill() -> None:
    """The seam between the grader and `backfill_drills.py --quality-filter`.

    The grader numbers drills from 1 (matching the eval's `DRILL 1:` render);
    the backfill enumerates `breakdown['drills']` from 0. An off-by-one here
    would silently hold back the WRONG drill and still look plausible in the
    report, so it is asserted directly.

    Pride and Joy is the fixture because its offending drills are D1 and D3
    (1-based) — asymmetric, so an off-by-one cannot accidentally agree.
    """
    verbatim_bar = [
        _measure(
            _beat((6, 0), (5, 2)),
            _beat((6, 0), (4, 2)),
            _beat((6, 0), (5, 2)),
            _beat((6, 0), (4, 2)),
        )
    ]
    clean = [_measure(_beat((1, 12)), _beat((1, 15)))]
    row = _row(
        71,
        "Pride and Joy",
        "SRV",
        [_PJ_MAIN_M1],
        [
            _drill_json("copies the bar", 3, "shuffle", verbatim_bar, _SKILL),
            _drill_json("clean", 4, "single notes", clean, _SKILL),
            _drill_json("copies the bar too", 4, "shuffle", verbatim_bar, _SKILL),
        ],
    )
    held = excluded_drill_indices(row, {_SKILL})
    assert sorted(held) == [0, 2], "0-based indices of the first and third drills"
    assert all(gates == ["L1"] for gates in held.values())


def test_quality_filter_holds_nothing_when_every_drill_is_clean() -> None:
    """The filter must be a no-op on good rows, not a blanket tax on the bank."""
    row = _row(
        74,
        "Comfortably Numb",
        "Pink Floyd",
        [_measure(_beat((1, 14)), _beat((1, 12)), _beat((2, 15)), _beat((2, 13)))],
        [
            _drill_json(
                "Lock the Index: Anchor and Roll",
                2,
                "single notes, one string at a time",
                [_measure(_beat((3, 14)), _beat((4, 12)))],
                _SKILL,
            )
        ],
    )
    assert excluded_drill_indices(row, {_SKILL}) == {}


def test_empty_snippet_covers_nothing() -> None:
    """A drill with no snippet must not divide by zero or look like a full copy."""
    drill = _drill_json("Empty", 3, "nothing", [], _SKILL)
    song = song_from_row(_row(71, "P", "S", [_PJ_MAIN_M1], [drill]), {_SKILL})
    assert l1_cover(song, song.drills[0]) == 0.0
