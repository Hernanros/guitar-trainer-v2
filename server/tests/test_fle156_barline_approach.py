"""FLE-156 — the half-step passing-tone test looks across the barline.

FLE-154 gave a foreign note a warning instead of a refusal when it steps a half
step into THE NEXT NOTE IN ITS OWN BAR. Fret's listen-review of that change
(2026-11-23 12:31) found the hole it left, which was never a regression: a
foreign note on the last 8th of a bar has no next note in that bar, so the
step test could not fire for it and it fell through to blocking unless a
chromatic run of 3+ inside the same bar happened to cover it.

What that refused is the most ordinary chromatic move in blues — an approach
note on the last beat leading into the next bar's downbeat. The fixture below
is exactly that move, spelled as the card spells it: a C on the last 8th of an
E7 bar resolving down into the B7 bar's B.

Hand-written fixtures here rather than the FLE-154 payloads on purpose. Those
payloads are the record of what the generator really emitted and they pin the
four bars Fret ruled on (see `test_fle154_passing_tone.py`, which must stay
green and does); none of them happens to contain a last-beat approach, which is
why the hole survived that card. This file states the rule directly.
"""
from __future__ import annotations

from app.models.song import Beat, Breakdown, Chord, Measure, Note, Tab

from app.ai.tab_verification import verify

_TUNING = ["E", "A", "D", "G", "B", "E"]

# E7 {E,G#,B,D} + B7 {B,D#,F#,A} — the I and the V of a blues turnaround.
# C natural is in neither, and B is the root of the second one.
_CHORDS = ["E7", "B7"]

_E2 = (6, 0)    # pc E — owned by E7
_C3 = (5, 3)    # pc C — owned by neither chord. MIDI 48
_B2 = (5, 2)    # pc B — owned by B7.                MIDI 47, a half step below
_D3 = (4, 0)    # pc D — owned by E7. MIDI 50, a WHOLE step above C3


def _bar(notes: list[tuple[int, int, str]]) -> Measure:
    """One measure, one note per column, from (string, fret, duration)."""
    return Measure(
        beats=[
            Beat(notes=[Note(string=s, fret=f, duration=d)]) for s, f, d in notes
        ],
        time_signature="4/4",
    )


def _breakdown(measures: list[Measure]) -> Breakdown:
    return Breakdown.model_validate(
        {
            "tab": Tab(tuning=_TUNING, measures=measures),
            "chords": [Chord(name=n, positions=[]) for n in _CHORDS],
            "technique_notes": [],
        }
    )


def _approach_bar(last: tuple[int, int]) -> Measure:
    """Eight 8ths: seven E2 pedal strokes, then `last` on the final 8th."""
    return _bar([(*_E2, "eighth")] * 7 + [(*last, "eighth")])


def _own_chords(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("OWN_CHORDS:")]


def _blocking_own_chords(lines: list[str]) -> list[str]:
    # Blocking OWN_CHORDS defects are not prefixed (only the warnings are), so
    # match them the way `_check_own_chords` writes them.
    return [line for line in lines if "declared chords" in line]


# ---------------------------------------------------------------------------
# The acceptance case
# ---------------------------------------------------------------------------

def test_approach_note_on_the_last_eighth_warns_not_blocks():
    """m1 is an E7 bar: E2 pedalled in 8ths, then C3 on the last 8th. m2 is the
    B7 bar and opens on B2 — a half step below that C3, and a pitch B7 owns.

    Before FLE-156 the C3 was the last sounding column of its bar, so there was
    nothing in m1 for the step test to resolve it into and the song was refused
    for it. It now warns: the line resolves, the barline is notation.
    """
    result = verify(_breakdown([_approach_bar(_C3), _bar([(*_B2, "whole")])]))

    assert not _blocking_own_chords(result.blocking), result.blocking
    warnings = _own_chords(result.warnings)
    assert len(warnings) == 1, result.warnings
    assert "m1:" in warnings[0] and "C3" in warnings[0]
    assert "half step into the next bar's first note" in warnings[0]
    # Nothing else in the fixture is wrong, so the breakdown is accepted —
    # which is the point of the card: it stops refusing the song.
    assert result.ok, result.blocking


# ---------------------------------------------------------------------------
# What the look across the barline must NOT swallow
# ---------------------------------------------------------------------------

def test_a_whole_step_across_the_barline_still_blocks():
    """Same bar, same position, but m2 opens on D3 — a whole step above the
    C3, so the C resolves nowhere. Looking across the barline must widen WHICH
    notes the half-step test can see, not what counts as a half step.
    """
    result = verify(_breakdown([_approach_bar(_C3), _bar([(*_D3, "whole")])]))

    blocking = _blocking_own_chords(result.blocking)
    assert blocking and "C3" in blocking[0], result.warnings
    assert not _own_chords(result.warnings), result.warnings


def test_a_held_approach_note_still_blocks_across_the_barline():
    """A half note is harmony however neatly it steps into the next bar. The
    held/repeated half of Fret's FLE-154 ruling is checked before either step
    test and FLE-156 does not move it: C3 held for two beats against E7,
    resolving into B2, is still a refusal.
    """
    bar1 = _bar([(*_E2, "half"), (*_C3, "half")])
    result = verify(_breakdown([bar1, _bar([(*_B2, "whole")])]))

    assert _blocking_own_chords(result.blocking), result.warnings
    assert not _own_chords(result.warnings), result.warnings


def test_the_last_bar_of_the_tab_has_no_next_downbeat():
    """There is no bar after the last one, so a foreign note on its last 8th
    has nothing to resolve into and behaves exactly as it did under FLE-154.
    `next_measure=None` is a real state, not a defensive branch — this is the
    case that reaches it.
    """
    result = verify(_breakdown([_approach_bar(_C3)]))

    blocking = _blocking_own_chords(result.blocking)
    assert blocking and "C3" in blocking[0], result.warnings
    assert not _own_chords(result.warnings), result.warnings


def test_rests_at_the_top_of_the_next_bar_are_skipped_not_fatal():
    """`_sounding_columns` drops rest columns inside a bar, so "the next bar's
    first note" means its first SOUNDING column too. A bar that opens with an
    8th rest before the B2 is the same musical resolution and gets the same
    verdict — the alternative reads a rest as a wrong pitch.
    """
    bar2 = Measure(
        beats=[
            Beat(rest="eighth"),
            Beat(notes=[Note(string=_B2[0], fret=_B2[1], duration="eighth")]),
            Beat(notes=[Note(string=_B2[0], fret=_B2[1], duration="half")]),
            Beat(notes=[Note(string=_B2[0], fret=_B2[1], duration="quarter")]),
        ],
        time_signature="4/4",
    )
    result = verify(_breakdown([_approach_bar(_C3), bar2]))

    assert not _blocking_own_chords(result.blocking), result.blocking
    warnings = _own_chords(result.warnings)
    assert len(warnings) == 1, result.warnings
    assert "half step into the next bar's first note" in warnings[0]
