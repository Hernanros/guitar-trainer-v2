"""Gate tests for scripts/grade_eval.py — the L5 rewrite and the A1 promotion (FLE-45).

WHY THESE TESTS EXIST
The 04.1-07 eval re-run failed gate L5 on all four gradeable songs, and two of
those four failures were the RULE's fault, not the model's:

  - Little Wing D3→D4 "failed" because D4 sounds ONE string at a time where D3
    sounded two. D4 is a six-note bass-chord-melody roll — the hardest drill in
    the set. The dominance rule called it a regression.
  - Beat It D3→D4 "failed" because D4 drops the position shift. It trades the
    shift for a cross-string root change, which is harder.

The other two are real and MUST still fail:

  - Kashmir D2→D3 drops shapes, change and shift all at once; D3→D4 adds nothing.
  - Lenny D1→D2 drops shape count; D3→D4 drops displacement.

So the bar for the rewrite is not "L5 passes more often" — it is "L5 stops
punishing trades and keeps punishing regressions". Both halves are asserted
here, using the four real drill sequences from
.planning/phases/04.1-ai-drills/04.1-07-EVAL-RERUN-RESULTS.md.

The tiers below are the ones the MECHANIC TIERS list assigns to those real
drills. They are the fixture's whole point: a test that invented its own easy
sequences would prove nothing about the run that motivated the change.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.grade_eval import Drill, Measure, Song, grade


def _drill(
    index: int,
    name: str,
    tier: int | None,
    beats: list[list[tuple[int, int]]],
    what: str = "",
) -> Drill:
    """A Drill with one snippet measure. `beats` is [[(string, fret), ...], ...]."""
    d = Drill(index=index, name=name, mechanic_tier=tier, what=what)
    d.measures = [Measure(index=1, time_sig="4/4", beats=beats)]
    return d


def _song(drills: list[Drill], main_beats=None) -> Song:
    """A Song carrying `drills`, plus a main tab that shares nothing with them.

    The main tab is deliberately in the same fret region as the fixtures (frets
    5-9) so gate L7 passes and does not confound an L5/A1 assertion.
    """
    s = Song(index=1, title="Test Song", artist="Test Artist")
    s.skill_ids = ["skill-0"]
    for d in drills:
        d.skill_id = "skill-0"
        d.song_specific = s.title.lower() in d.what.lower() or s.artist.lower() in d.what.lower()
    s.drills = drills
    s.main = [
        Measure(
            index=1,
            time_sig="4/4",
            beats=main_beats or [[(6, 5)], [(5, 7)], [(4, 6)], [(3, 9)]],
        )
    ]
    return s


# Single note, two-string alternation, held shape, shape change — the geometry
# each tier implies, reused across the ordering tests.
_ONE_NOTE = [[(6, 5)], [(6, 5)]]
_ALTERNATION = [[(6, 5)], [(5, 7)], [(6, 5)], [(5, 7)]]
_HELD_SHAPE = [[(6, 5), (5, 7), (4, 7)], [(6, 5), (5, 7), (4, 7)]]
_SHAPE_CHANGE = [[(6, 5), (5, 7), (4, 7)], [(6, 8), (5, 7), (4, 5)]]


class TestL5NoLongerPunishesTrades:
    """The two 04.1-07 failures that were the rule's fault, not the model's."""

    def test_little_wing_roll_after_a_two_string_drill_passes(self):
        """D4 sounds fewer strings than D3 and is still harder — tier 6 vs tier 3.

        This is the exact shape the dominance rule rejected: c_voices 2→1. Under
        tiers it is a rise, because a bass-chord-melody roll IS the hard thing.
        """
        song = _song([
            _drill(1, "Thumb bass, open position", 1, _ONE_NOTE),
            _drill(2, "Alternate across two strings", 2, _ALTERNATION),
            _drill(3, "Hold the D shape, strike it", 3, _HELD_SHAPE),
            # The six-note roll: one note at a time, three independent voices.
            _drill(4, "Bass-chord-melody roll", 6,
                   [[(6, 5)], [(4, 7)], [(3, 7)], [(2, 5)], [(3, 7)], [(4, 7)]]),
        ])
        status, evidence = grade(song)["L5"]
        assert status == "PASS", evidence

    def test_beat_it_equal_tier_trade_passes(self):
        """D4 drops the position shift and adds a cross-string root change.

        Both drills are shape changes, so both are tier 4. Equal tiers are
        explicitly allowed — that is what makes a trade expressible at all.
        """
        song = _song([
            _drill(1, "Palm-muted single note", 1, _ONE_NOTE),
            _drill(2, "Two-string power chord stab", 3, _HELD_SHAPE),
            _drill(3, "Shape change with a shift", 4,
                   [[(6, 5), (5, 7)], [(6, 9), (5, 11)]]),
            _drill(4, "Cross-string root change", 4,
                   [[(6, 5), (5, 7)], [(5, 5), (4, 7)]]),
        ])
        status, evidence = grade(song)["L5"]
        assert status == "PASS", evidence


class TestL5StillCatchesRealRegressions:
    """The two 04.1-07 failures that were genuine. A looser gate must not lose them."""

    def test_kashmir_drop_from_shape_change_to_single_note_fails(self):
        """D2→D3 dropped shapes, change AND shift at once. Tier 4→2 is a drop."""
        song = _song([
            _drill(1, "One sustained DADGAD drone", 1, _ONE_NOTE),
            _drill(2, "Sus4 shape change", 4, _SHAPE_CHANGE),
            _drill(3, "Back to a single fretted note", 2, _ALTERNATION),
            _drill(4, "Still just alternation", 2, _ALTERNATION),
        ])
        status, evidence = grade(song)["L5"]
        assert status == "FAIL"
        assert "2→3" in evidence and "4" in evidence, evidence

    def test_lenny_drop_at_the_first_step_fails(self):
        """D1→D2 dropped shape count — a regression on the very first step."""
        song = _song([
            _drill(1, "Held E-shape barre", 3, _HELD_SHAPE),
            _drill(2, "Single-note slide", 1, _ONE_NOTE),
            _drill(3, "Shape change", 4, _SHAPE_CHANGE),
        ])
        status, evidence = grade(song)["L5"]
        assert status == "FAIL"
        assert "1→2" in evidence, evidence

    def test_a_single_step_backwards_anywhere_fails(self):
        song = _song([
            _drill(1, "a", 1, _ONE_NOTE),
            _drill(2, "b", 4, _SHAPE_CHANGE),
            _drill(3, "c", 5, _SHAPE_CHANGE),
            _drill(4, "d", 4, _SHAPE_CHANGE),
        ])
        status, evidence = grade(song)["L5"]
        assert status == "FAIL"
        assert "3→4" in evidence, evidence


class TestL5RequiresTheDeclaration:
    """`mechanic_tier` is Optional on the model for cached-row compat only."""

    def test_missing_tier_fails_rather_than_passing_silently(self):
        """An undeclared tier makes the ordering ungradeable, which is a FAIL.

        The field is Optional[int] so pre-FLE-45 cached breakdowns still parse.
        That compatibility must not become a way for fresh output to skip L5.
        """
        song = _song([
            _drill(1, "a", 1, _ONE_NOTE),
            _drill(2, "b", None, _SHAPE_CHANGE),
        ])
        status, evidence = grade(song)["L5"]
        assert status == "FAIL"
        assert "mechanic_tier" in evidence, evidence

    def test_equal_tiers_throughout_pass(self):
        """Four drills at one tier is legal. The rule is non-decreasing, not rising."""
        song = _song([_drill(i, f"d{i}", 3, _HELD_SHAPE) for i in range(1, 5)])
        assert grade(song)["L5"][0] == "PASS"


class TestA1IsARealGate:
    """Prose/tab agreement — the Lenny D4 defect (FLE-45 part 3)."""

    def test_lenny_d4_barre_prose_over_a_monophonic_snippet_fails(self):
        """The verbatim 04.1-07 defect: barre promised, four single notes shipped."""
        song = _song([
            _drill(1, "Warm up", 1, _ONE_NOTE, what="Play one note and hold it."),
            _drill(
                2, "Barre Change V→VII", 4,
                [[(3, 6)], [(3, 7)], [(3, 8)], [(3, 9)]],
                what=(
                    "Move between an E-shape barre at fret 5 and fret 7, settling "
                    "before striking the full chord."
                ),
            ),
        ])
        status, evidence = grade(song)["A1"]
        assert status == "FAIL"
        assert "drill 2" in evidence and "monophonic" in evidence, evidence

    def test_a1_is_counted_in_the_gate_total(self, tmp_path, capsys, monkeypatch):
        """Promotion means A1 SCORES. Through 04.1-07 it printed but was skipped.

        End-to-end through main() rather than grade(), because the exclusion this
        asserts against lived in main()'s scoring loop (`if g != "A1"`), not in
        grade(). Going through main() also proves the renderer's
        `mechanic_tier : N` line round-trips into gate L5 — the two halves of
        FLE-45 that have to agree on a wire format.
        """
        from scripts import grade_eval

        # One song, two drills, one gate deliberately broken (A1): the snippet is
        # monophonic while `what` promises a barre chord.
        fixture = tmp_path / "eval.txt"
        fixture.write_text(
            "SONG 1/5: Test Song — Test Artist\n"
            "  target_skills provided to Sonnet:\n"
            "    id=00000000-0000-0000-0000-000000000001  name='thing'\n"
            "\n"
            "  MAIN SONG TAB — 1 measure(s), tuning=['E']\n"
            "    m1 (4/4): [6/5] [5/7] [4/6] [3/9]\n"
            "  DRILL 1: Warmup\n"
            "    song_specific : False\n"
            "    target_skill  : 'thing' (id=00000000-0000-0000-0000-000000000001)\n"
            "    tempo         : 60 → 80 BPM\n"
            "    mechanic_tier : 1 (single sustained note)\n"
            "    what          : Play one note and hold it.\n"
            "    tab_snippet   : 1 measure(s), tuning=['E']\n"
            "    m1 (4/4): [6/5] [6/5]\n"
            "  DRILL 2: Barre Change\n"
            "    song_specific : False\n"
            "    target_skill  : 'thing' (id=00000000-0000-0000-0000-000000000001)\n"
            "    tempo         : 60 → 80 BPM\n"
            "    mechanic_tier : 4 (shape change)\n"
            "    what          : Move between an E-shape barre and strike the full chord.\n"
            "    tab_snippet   : 1 measure(s), tuning=['E']\n"
            "    m1 (4/4): [3/6] [3/7] [3/8]\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(sys, "argv", ["grade_eval", str(fixture)])
        grade_eval.main()
        out = capsys.readouterr().out

        # 8 gates counted for the one song — A1 among them, so the denominator is
        # 8 and not the pre-FLE-45 7.
        assert "TOTAL: 7/8 gates passed" in out, out
        assert "A1: FAIL" in out, out
        # The declared tiers parsed off the renderer's line and reached L5.
        assert "tiers non-decreasing: [1, 4]" in out, out
        assert "D1 T1 " in out and "D2 T4 " in out, out

    def test_chord_prose_with_a_polyphonic_snippet_passes(self):
        song = _song([
            _drill(1, "a", 1, _ONE_NOTE, what="One note."),
            _drill(2, "Strike the barre", 3, _HELD_SHAPE,
                   what="Hold the E-shape barre and strum it on every beat."),
        ])
        assert grade(song)["A1"][0] == "PASS"

    def test_monophonic_snippet_with_honest_prose_passes(self):
        """A1 fires on a broken PROMISE, not on monophony itself."""
        song = _song([
            _drill(1, "Slide", 1, _ONE_NOTE,
                   what="Slide from the b3 to the 3 on the low E string, one note."),
        ])
        assert grade(song)["A1"][0] == "PASS"


class TestL1ComparesBeatsNotNotes:
    """Part 2: reusing one of the song's chord voicings is not a slice."""

    def test_reusing_one_song_chord_voicing_is_not_a_violation(self):
        """Three notes of one chord are ONE beat, not three consecutive pairs.

        This is the contradiction FLE-45 part 2 fixes in the prompt wording: the
        neck-region rule requires the drill to use the song's own shapes, and the
        old "3 consecutive (string, fret) pairs" reading made that automatically
        illegal. The grader already compared beats; this pins the behaviour.
        """
        voicing = [(6, 5), (5, 7), (4, 7)]
        song = _song(
            [_drill(1, "Strike the song's own barre", 3, [voicing, voicing])],
            main_beats=[voicing, [(3, 7)], [(2, 5)], [(3, 7)]],
        )
        assert grade(song)["L1"][0] == "PASS", grade(song)["L1"][1]

    def test_three_consecutive_song_beats_is_still_a_slice(self):
        main = [[(6, 5)], [(5, 7)], [(4, 7)], [(3, 9)]]
        song = _song(
            [_drill(1, "Quoted the song", 2, [[(6, 5)], [(5, 7)], [(4, 7)]])],
            main_beats=main,
        )
        status, evidence = grade(song)["L1"]
        assert status == "FAIL"
        assert "reproduces 3 beats" in evidence, evidence


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
