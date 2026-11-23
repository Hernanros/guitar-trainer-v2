"""FLE-154 — OWN_CHORDS warns instead of blocking when the foreign note is a
passing tone. Fret's ruling on FLE-144 (2026-11-23).

Built from the REAL refusal payloads of the 2026-11-23 remeasure
(`.planning/fle89-remeasure-2026-11-23/raw/`), not from hand-written fixtures,
which is the whole point: FLE-144's own hypothesis about this check was refuted
by a comment inside the function, and the thing that settled it was going back
to what the generator actually emitted. No live Sonnet spend — the payloads are
on disk because FLE-148 made the refusal path log them.

The four bars the card names, and what each must now do:

  Sweet Home Chicago m4   D3+Ab3 / D3+Bb3 x4       still REFUSED (blocking)
  Sweet Home Chicago m7   G2 F#2 F2(+B2) E2(+Bb2)  WARNS, does not block
  Comfortably Numb m3     D5, B4 hammer-on, C5     WARNS, does not block
  Comfortably Numb m7     F5 D5 C5 B4 C5           WARNS, does not block

Note what "passes" means for Comfortably Numb: its OWN_CHORDS defects are
gone, but the breakdown is STILL refused, for a METER defect at m4 (durations
sum to 4.5 beats in 4/4) that has nothing to do with this card. The tests below
assert on the OWN_CHORDS lines specifically and say so, rather than on
`result.ok`, so a later METER fix does not silently change what they cover.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.tab_verification import verify
from app.models.song import Beat, Breakdown, Chord, Measure, Note, Tab

RAW = (
    Path(__file__).resolve().parents[2]
    / ".planning"
    / "fle89-remeasure-2026-11-23"
    / "raw"
)
SWEET_HOME_CHICAGO = "20261123T100455Z-sweet-home-chicago-robert-johnson.json"
COMFORTABLY_NUMB = "20261123T100443Z-comfortably-numb-pink-floyd.json"
_TUNING = ["E", "A", "D", "G", "B", "E"]


def _load(filename: str) -> Breakdown:
    payload = json.loads((RAW / filename).read_text())
    return Breakdown.model_validate(payload["raw_input"])


def _own_chords(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("OWN_CHORDS:")]


def _for_measure(lines: list[str], measure: int) -> list[str]:
    return [line for line in lines if f"m{measure}:" in line]


# ---------------------------------------------------------------------------
# The bar that must STILL be refused
# ---------------------------------------------------------------------------

def test_sweet_home_chicago_m4_repeated_foreign_note_still_blocks():
    """m4 is `D3+Ab3 / D3+Bb3` eight times. Bb is foreign to E7/A7/B7, struck
    four times, and its neighbour Ab is a WHOLE step away — it is heard as
    harmony and contradicts it. A genuinely wrong bar; the card is explicit
    that relaxing OWN_CHORDS must not let it through.
    """
    result = verify(_load(SWEET_HOME_CHICAGO))

    blocking = _for_measure(_own_chords(result.blocking), 4)
    assert blocking, _own_chords(result.blocking)
    assert "Bb3" in blocking[0]
    # All four strikes named, as before — the relaxation must not thin out the
    # evidence in a defect it still raises.
    assert blocking[0].count("Bb3") == 4, blocking[0]
    # And it must not ALSO appear as a warning: one verdict per note.
    assert not _for_measure(_own_chords(result.warnings), 4), result.warnings


# ---------------------------------------------------------------------------
# The three bars that must now warn instead
# ---------------------------------------------------------------------------

def test_sweet_home_chicago_m7_chromatic_walkdown_warns():
    """m7 is the walkdown G2-F#2-F2(+B2)-E2(+Bb2)-E2. Two foreign notes:

      F2  (b3, bass)  — a half step into E2, and inside the run itself
      Bb2 (b4, upper) — rides on top of the run's last column

    Bb2 is the note that decides which voice the run is measured on. It is
    approached by a half step from B2 and never resolves, so the half-step
    test cannot license it; the four-column chromatic bass line can, and does.
    See `_passing_tone_reason` for the argument.
    """
    result = verify(_load(SWEET_HOME_CHICAGO))

    assert not _for_measure(_own_chords(result.blocking), 7), result.blocking
    warnings = _for_measure(_own_chords(result.warnings), 7)
    assert len(warnings) == 1, result.warnings
    assert "F2" in warnings[0] and "Bb2" in warnings[0]
    assert "chromatic run" in warnings[0]
    # Both tests fire in this one bar — F2 steps AND rides the run — so the
    # warning names both reasons rather than stopping at the first.
    assert "half step into the next note" in warnings[0]
    # Per-bar verdicts: m7 warns while m4 of the same tab still blocks, so the
    # song as a whole is still refused and for the right bar.
    assert not result.ok
    assert _for_measure(_own_chords(result.blocking), 4)


@pytest.mark.parametrize("measure", [3, 7])
def test_comfortably_numb_hammer_on_b4_to_c5_warns(measure: int):
    """m3 and m7 both hammer B4 into C5. B is foreign to Dm/C/Bb; a half step
    into the next note is the plainest possible passing tone, and refusing it
    refuses the lick.
    """
    result = verify(_load(COMFORTABLY_NUMB))

    assert not _for_measure(_own_chords(result.blocking), measure), result.blocking
    warnings = _for_measure(_own_chords(result.warnings), measure)
    assert len(warnings) == 1, result.warnings
    assert "B4" in warnings[0]
    assert "half step into the next note" in warnings[0]


def test_comfortably_numb_has_no_own_chords_defects_left():
    """Both of its OWN_CHORDS refusals were the same hammer-on, so after this
    change the check has nothing left to say against that breakdown.

    It is still refused — METER m4 sums to 4.5 beats in 4/4 — and that is a
    real, separate defect this card does not touch. Asserted here so the test
    file records WHY the song still 503s and nobody reads this change as
    having fixed it.
    """
    result = verify(_load(COMFORTABLY_NUMB))

    assert not _own_chords(result.blocking), result.blocking
    assert len(_own_chords(result.warnings)) == 2, result.warnings
    assert not result.ok
    assert [d for d in result.blocking if d.startswith("METER:") and "m4:" in d]


# ---------------------------------------------------------------------------
# The boundary: what the relaxation must NOT swallow
# ---------------------------------------------------------------------------

def _bar(notes: list[tuple[int, int, str]]) -> Measure:
    """One measure, one note per column, from (string, fret, duration)."""
    return Measure(
        beats=[
            Beat(notes=[Note(string=s, fret=f, duration=d)]) for s, f, d in notes
        ],
        time_signature="4/4",
    )


def _breakdown(measure: Measure, chord: str) -> Breakdown:
    return Breakdown.model_validate(
        {
            "tab": Tab(tuning=_TUNING, measures=[measure]),
            "chords": [Chord(name=chord, positions=[])],
            "technique_notes": [],
        }
    )


def test_a_held_foreign_note_still_blocks():
    """A half note is not in transit however neatly it steps afterwards. Em is
    {E,G,B}; F on the low E string held for two beats, then its resolution to
    F# — a half step up, so the step test alone would pass it.
    """
    bar = _bar([(6, 1, "half"), (6, 2, "half")])
    result = verify(_breakdown(bar, "Em"))
    assert _own_chords(result.blocking), result.warnings


def test_a_trilled_foreign_note_still_blocks():
    """F-F#-F-F#-F-F#-F-F#: every F steps a half step into the next note, so
    the step test passes on each one individually. Four strikes of a foreign
    pitch in one bar is not a passing tone, it is the harmony of the bar, and
    `_MAX_PASSING_STRIKES` is what keeps a trill from buying a pass.
    """
    bar = _bar([(6, 1 + (i % 2), "eighth") for i in range(8)])
    result = verify(_breakdown(bar, "Em"))
    assert _own_chords(result.blocking), result.warnings


def test_an_oscillation_is_not_a_chromatic_run():
    """G-F#-G: two half steps, opposite directions, so no run of 3 — a run is
    monotonic. The middle F# is still licensed (it steps into the next note);
    what this pins is that `_chromatic_run_columns` does not count a wobble as
    three-in-a-row and license a bar wholesale.
    """
    from app.ai.tab_verification import _chromatic_run_columns

    assert _chromatic_run_columns([(1, [43]), (2, [42]), (3, [43])]) == set()
    assert _chromatic_run_columns([(1, [43]), (2, [42]), (3, [41])]) == {1, 2, 3}


def test_one_bar_can_block_and_warn_at_once():
    """A bar with both kinds of foreign note gets both verdicts, each naming
    only its own notes — F# walks into G (passing), C natural is held for two
    beats against Em (wrong). The warning says the bar still blocks so nobody
    reads it as a clean bar.
    """
    bar = _bar([(6, 2, "quarter"), (6, 3, "quarter"), (5, 3, "half")])
    result = verify(_breakdown(bar, "Em"))

    blocking = _own_chords(result.blocking)
    warnings = _own_chords(result.warnings)
    assert blocking and "C3" in blocking[0] and "F#2" not in blocking[0]
    assert warnings and "F#2" in warnings[0] and "C3" not in warnings[0]
    assert "still block" in warnings[0]


def test_drill_chord_identity_is_unaffected_by_the_relaxation():
    """DRILL_CHORD shares `_check_foreign_notes` but asks a different question
    — is the voicing this drill is NAMED after the one it plays — so a foreign
    note in it does not get the passing-tone escape. A snippet named for Em
    whose notes walk F-F#-G chromatically must still block.
    """
    from tests.test_tab_verification import _drill

    walk = _bar([(6, 1, "quarter"), (6, 2, "quarter"), (6, 3, "half")])
    pedal = _bar([(3, 0, "whole")])
    bd = Breakdown.model_validate(
        {
            "tab": Tab(tuning=_TUNING, measures=[_bar([(6, 0, "whole")])]),
            "chords": [Chord(name="Em", positions=[])],
            "technique_notes": [],
            "drills": [
                _drill("Em Voicing Hold", [walk], tuning=_TUNING),
                _drill("Isolate the pull-off", [pedal], tuning=_TUNING),
            ],
        }
    )
    result = verify(bd)
    assert [d for d in result.blocking if d.startswith("DRILL_CHORD:")], result.blocking
