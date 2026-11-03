"""FLE-149 — OWN_CHORDS must not invent foreign notes out of a chord NAME it
cannot read.

`_chord_pcs` took `s[0]` as the root and scanned the rest for a quality
substring. Given a `chord.name` that is prose rather than a chord symbol it did
not decline: it returned that letter's major triad, `_check_own_chords` unioned
that into `allowed`, and because OWN_CHORDS blocks, the breakdown 503d on an
artefact of the parser.

Every number asserted below was measured against the live module, before and
after. The ground truth is FLE-141's eval of the shipped FLE-118 prompt
(`.planning/phases/04.1-ai-drills/04.1-07-EVAL-FLE118-RAW.txt`, on main at
3f4b840): SRV's Lenny declared

    ['B7#9', 'E-shape barre A', 'E-shape barre B', 'E-shape barre G']

and was refused for 5 OWN_CHORDS defects over m3/m5/m6/m7/m8 flagging the
pitch classes G, C# and C. Two of those three were the parser's invention.
"""
import pytest

from app.ai.tab_verification import _chord_pcs, verify
from app.models.song import Beat, Breakdown, Chord, ChordPosition, Measure, Note, Tab

# Lenny's declared chord list, verbatim from the eval artifact.
LENNY_CHORDS = ["B7#9", "E-shape barre A", "E-shape barre B", "E-shape barre G"]

# Standard tuning, so `_tuning_offset` is 0 and `_check_foreign_notes` scores in
# ONE frame. A test that leaned on the second (Eb) frame to rescue a note would
# pass for a reason other than the parse under test.
STD = ["E", "A", "D", "G", "B", "e"]

# string/fret for the three pitch classes the eval flagged, in standard tuning.
G3 = (3, 0)      # midi 55, pc 7
CS4 = (2, 2)     # midi 61, pc 1
C4 = (2, 1)      # midi 60, pc 0
B3 = (2, 0)      # midi 59, pc 11 — allowed by B7#9 alone, the control


def _chord(name: str) -> Chord:
    return Chord(name=name, positions=[ChordPosition(string=6, fret=0)])


def _bar(*pitches: tuple[int, int]) -> Measure:
    """One 4/4 bar, one note per beat. Durations are quarters, so the bar meters
    exactly and only the pitch content is under test."""
    return Measure(
        beats=[Beat(notes=[Note(string=s, fret=f, duration="quarter")]) for s, f in pitches],
        time_signature="4/4",
    )


def _breakdown(measures: list[Measure], names: list[str]) -> Breakdown:
    return Breakdown(
        tab=Tab(measures=measures, tuning=STD),
        chords=[_chord(n) for n in names],
        technique_notes=[],
    )


def _own_chords(result) -> list[str]:
    return [d for d in result.blocking if d.startswith("OWN_CHORDS:")]


def _unparseable(result) -> list[str]:
    return [w for w in result.warnings if w.startswith("CHORD_NAME_UNPARSEABLE:")]


# ---------------------------------------------------------------------------
# The parse itself.
# ---------------------------------------------------------------------------

def test_the_three_barre_shapes_no_longer_collapse_to_one_wrong_chord():
    """Before: all three returned E Ab B — E major, the shape letter's triad —
    so the three chords the breakdown actually declares contributed none of
    their own pitches."""
    assert _chord_pcs("E-shape barre A") == {9, 1, 4}    # A major
    assert _chord_pcs("E-shape barre B") == {11, 3, 6}   # B major
    assert _chord_pcs("E-shape barre G") == {7, 11, 2}   # G major
    assert len({frozenset(_chord_pcs(n)) for n in LENNY_CHORDS[1:]}) == 3


def test_the_shape_names_the_quality_and_the_target_names_the_root():
    assert _chord_pcs("Em-shape barre A") == {9, 0, 4}        # A minor
    assert _chord_pcs("A-shape barre D") == {2, 6, 9}         # D major
    # An explicit quality on the target is the more specific of two statements.
    assert _chord_pcs("E-shape barre A7") == _chord_pcs("A7")


@pytest.mark.parametrize(
    "prose",
    [
        # Measured before FLE-149, in this order: B5 (the "5" came from
        # "fret 5"), D major, G major, A major.
        "Barre chord at fret 5",
        "Dropped D power chord",
        "Gradual bend into the next bar",
        "Alternate between the two voicings",
        # SYSTEM_PROMPT's other spelling of the shape idea (breakdown.py:645),
        # which was read as E5 — again the "5" of "fret 5". Resolving a fret to
        # a root needs the shape's root string AND the tuning, so this declines
        # rather than guesses; `_SHAPE_FORM_RE` deliberately does not match it.
        "E-shape barre at fret 5",
        "walk-down",
        "N.C.",
        "",
    ],
)
def test_prose_is_declined_rather_than_mis_rooted(prose):
    assert _chord_pcs(prose) is None


@pytest.mark.parametrize(
    "name,expected",
    [
        # Every stored prod chord name is the parenthesised form; the gloss is
        # stripped and the symbol in front of it must still parse.
        ("B (barre, fret 2)", {11, 3, 6}),
        ("Bbm (implied tonic)", {10, 1, 5}),
        ("G5 (fret 5)", {7, 2}),
        ("A (open)", {9, 1, 4}),
        ("Em7", {4, 7, 11, 2}),
        ("A7sus4", {9, 2, 4, 7}),
        ("Dsus4", {2, 7, 9}),
        ("G", {7, 11, 2}),
        ("B7#9", {11, 3, 6, 9, 2}),
        ("Cmaj7", {0, 4, 7, 11}),
        ("Asus2", {9, 11, 4}),
        ("Caug", {0, 4, 8}),
        ("Gdim", {7, 10, 1}),
        # A space and a spelled-out quality are the same symbol.
        ("A minor", {9, 0, 4}),
        ("B 7#9", {11, 3, 6, 9, 2}),
        # A slash bass sounds, so it joins `allowed`; QUAL's own "6/9" is not a
        # slash bass and must not be split on.
        ("D/F#", {2, 6, 9}),
        ("C/B", {0, 4, 7, 11}),
    ],
)
def test_real_chord_symbols_still_parse(name, expected):
    assert _chord_pcs(name) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        # Found while confirming FLE-149, all the same bug: a scan that returns
        # a confident answer to a name it did not parse. The comment is what the
        # OLD code returned.
        ("C6/9", {0, 2, 4, 7, 9}),       # was C D E G Bb — "9" shadowed "6/9"
        ("F#m7b5", {6, 9, 0, 4}),        # was C# E F# A — the minor branch
                                         #   short-circuited to "m7"
        ("Gsus", {7, 0, 2}),             # was G major — a third it lacks, and
                                         #   its fourth left foreign
        ("Cm9", {0, 3, 7, 10, 2}),       # was Cm
        ("Gdim7", {7, 10, 1, 4}),        # was Gdim
        ("Cadd9", {0, 4, 7, 2}),         # was C9 — a b7 it lacks
    ],
)
def test_qualities_that_previously_resolved_to_a_different_chord(name, expected):
    assert _chord_pcs(name) == expected


def test_open_voiced_qualities_fail_open_instead_of_blocking_against_a_guess():
    """A 13th is voiced with or without its 11th and an 11th with or without its
    3rd, so no closed pitch set states them. They decline — see QUAL's comment
    on the rule for extending it."""
    assert _chord_pcs("F13") is None
    assert _chord_pcs("Bm11") is None


# ---------------------------------------------------------------------------
# The trap: returning None is not on its own a fix. `allowed` is a UNION, so a
# name that drops out NARROWS the permitted pitches and flags MORE notes.
# ---------------------------------------------------------------------------

def test_dropping_the_unreadable_names_would_have_flagged_more_not_fewer():
    parsed = set().union(*(_chord_pcs(n) for n in LENNY_CHORDS))
    symbol_only = _chord_pcs("B7#9")
    assert len(parsed) == 8          # reading the shapes: 7 -> 8 pitch classes
    assert len(symbol_only) == 5     # dropping them:      7 -> 5
    assert symbol_only < parsed
    # The two pitches the eval flagged as foreign are licensed ONLY by the
    # shapes, which is exactly why dropping them doubles down on the bug.
    assert {7, 1}.isdisjoint(symbol_only)
    assert {7, 1} <= parsed


# ---------------------------------------------------------------------------
# Lenny, end to end through verify().
# ---------------------------------------------------------------------------

def test_lenny_g_and_c_sharp_are_no_longer_flagged_but_c_still_is():
    bd = _breakdown([_bar(G3, CS4, C4, B3)], LENNY_CHORDS)
    defects = _own_chords(verify(bd))
    assert len(defects) == 1
    # C4 is string 2 fret 1, beat 3 — the one genuinely foreign note, and
    # Fret's to adjudicate (it is not this issue's to silence).
    assert "b3 C4" in defects[0]
    for invented in ("G3", "C#4"):
        assert invented not in defects[0]


def test_lenny_with_only_its_previously_false_positives_passes_clean():
    bd = _breakdown([_bar(G3, CS4, B3, B3)], LENNY_CHORDS)
    result = verify(bd)
    assert _own_chords(result) == []
    assert _unparseable(result) == []


# ---------------------------------------------------------------------------
# The fail-open safety net, for a name no grammar here reads.
# ---------------------------------------------------------------------------

def test_one_unreadable_name_skips_own_chords_and_warns_without_blocking():
    bd = _breakdown([_bar(G3, CS4, C4, B3)], ["B7#9", "slide down from the 12th"])
    result = verify(bd)
    assert _own_chords(result) == []
    warnings = _unparseable(result)
    assert len(warnings) == 1
    assert "'slide down from the 12th'" in warnings[0]
    assert "1 of 2" in warnings[0]
    # The whole point: a check that could not run must never refuse a breakdown.
    assert not any(w.startswith("CHORD_NAME_UNPARSEABLE") for w in result.blocking)


def test_the_coverage_loss_is_real_and_pinned_deliberately():
    """A note that OWN_CHORDS would block on its own stops being blocked once a
    SIBLING name is unreadable. That is the accepted cost of never inventing a
    false positive — if this test starts failing, read `_check_own_chords`'s
    docstring before "fixing" it."""
    foreign = [_bar(C4, B3, B3, B3)]
    assert len(_own_chords(verify(_breakdown(foreign, ["B7#9"])))) == 1
    assert _own_chords(verify(_breakdown(foreign, ["B7#9", "noodle around E"]))) == []


def test_an_all_unreadable_chord_list_also_fails_open():
    bd = _breakdown([_bar(G3, CS4, C4, B3)], ["noodle around E", "walk it down"])
    result = verify(bd)
    assert _own_chords(result) == []
    assert len(_unparseable(result)) == 1


def test_a_scale_name_in_the_chord_field_declines_instead_of_becoming_a_triad():
    """Prod's `'A minor pentatonic (XII pos)'` (Eruption) — the one stored chord
    name of 30 that is not a chord symbol at all, but a SCALE.

    The old parser read it as the A minor triad, which is a strict subset of the
    scale it names, so the scale's own D and G became foreign. Scored against
    the stored Eruption breakdown that produced **6 OWN_CHORDS blocking defects
    over m3-m8**; the new parser produces 0 and one warning. (Measured read-only
    against prod 2026-11-03. Nothing stored needs repair — `verify()` runs
    before persist, so this never refused anything live. It would have refused a
    REGENERATION of that song, for the same reason Lenny was refused.)

    Not parsed into the pentatonic set on purpose: a scale in `chord.name` is
    the generator filling the field with the wrong kind of thing, and teaching
    the verifier to accept it would hide that. The warning is how FLE-118's
    owner finds out.
    """
    scale = "A minor pentatonic (XII pos)"
    assert _chord_pcs(scale) is None
    # The old reading, for the record: a subset of the scale, so the scale's own
    # notes were flagged as foreign to it.
    assert {9, 0, 4} < {9, 0, 2, 4, 7}
    result = verify(_breakdown([_bar(G3, CS4, C4, B3)], ["A5 (open)", "A minor (open)", scale]))
    assert _own_chords(result) == []
    assert "'A minor pentatonic (XII pos)'" in _unparseable(result)[0]


def test_the_warning_truncates_a_long_offender_list_but_states_the_count():
    names = [f"{letter} is not a chord symbol" for letter in "ABCDEFG"]
    result = verify(_breakdown([_bar(B3, B3, B3, B3)], names))
    (warning,) = _unparseable(result)
    assert "7 of 7" in warning
    assert "+3 more" in warning
