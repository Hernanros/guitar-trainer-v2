"""app/sessions/family_classify.py — §8 family classification.

Pure unit tests, no DB. The corpus at the bottom is the part that matters: it is real
Sonnet-generated drill text lifted from the Phase 4.1 eval runs, not invented
examples, so the pass rate here is a measurement of the classifier rather than of the
test author's imagination.

THE PROPERTY THESE TESTS DEFEND is not the pass rate. It is that a wrong answer is
never preferred to no answer: §10.1 exists to stop the bank reporting coverage it
does not have, and a misfiled drill fabricates a stocked cell while a NULL merely
undercounts one. So the strict tests are the ones asserting None.
"""
from __future__ import annotations

import pytest

from app.sessions.family_classify import (
    MIN_SCORE,
    classify_family,
    families_for_root,
)
from app.sessions.taxonomy import FAMILY_SPECS, SkillRoot, TechniqueFamily


# ---------------------------------------------------------------------------
# Root narrowing
# ---------------------------------------------------------------------------

def test_every_family_is_reachable_from_exactly_one_root():
    """The §8 grid is rooted, and root narrowing depends on that partition holding."""
    seen: list[TechniqueFamily] = []
    for root in SkillRoot:
        seen.extend(families_for_root(root))
    assert sorted(f.value for f in seen) == sorted(f.value for f in TechniqueFamily)


def test_no_root_widens_to_all_eighteen():
    """A drill whose node has no root ancestor must not be unclassifiable by default.

    snapshot.py already treats a missing root as normal (the root joins are LEFT), so
    this module has to as well.
    """
    assert families_for_root(None) == tuple(TechniqueFamily)


def test_root_narrowing_cannot_return_a_family_from_another_root():
    """The safety property of narrowing: a rhythm drill can never be filed as C2.

    Text deliberately screams "barre chords", which is the strongest possible C2
    signal, and the root is rhythm. C2 is not reachable, so the answer is an R
    family or nothing — never C2.
    """
    verdict = classify_family(
        root=SkillRoot.RHYTHM,
        name="Barre Chord Shape Hold",
        what="Hold a full barre chord and keep the barre clean.",
    )
    assert verdict.family is None or verdict.family in families_for_root(SkillRoot.RHYTHM)


# ---------------------------------------------------------------------------
# Declining to classify — the behaviour §10.1 depends on
# ---------------------------------------------------------------------------

def test_text_with_no_mechanic_declines():
    """The synthetic fixture shape: a name and body carrying no technique vocabulary."""
    verdict = classify_family(root=SkillRoot.RHYTHM, name="drill-a1b2c3d4", what="what")
    assert verdict.family is None
    assert verdict.reason == "no_marker"


def test_empty_text_declines_rather_than_defaulting():
    verdict = classify_family(root=SkillRoot.LEAD, name="", what="")
    assert verdict.family is None


def test_a_secondary_marker_alone_never_classifies():
    """Supporting vocabulary must not carry a classification on its own.

    "open" is C1 support, not a C1 mechanic. A drill that only says "open" is not
    evidence that the C1/D1 cell is stocked.
    """
    verdict = classify_family(root=SkillRoot.CHORD_VOICINGS, name="Open Hold", what="")
    assert verdict.family is None
    assert verdict.score < MIN_SCORE


def test_a_genuine_tie_in_body_text_is_ambiguous_not_arbitrary():
    """Two families, equal evidence, neither in the name -> None.

    The headline tie-break needs a headline. With the mechanics named only in the
    body there is no leading mechanic to read, so the honest answer is no answer.
    """
    verdict = classify_family(
        root=SkillRoot.FINGERSTYLE,
        name="Study",
        what="Use the thumb on the bass note, then an arpeggio across the top strings.",
    )
    assert verdict.family is None
    assert verdict.reason == "ambiguous"


# ---------------------------------------------------------------------------
# The headline-order tie-break
# ---------------------------------------------------------------------------

def test_headline_order_breaks_a_tie():
    """A drill name leads with its mechanic and trails with its vehicle.

    "Thumb-Bass then Finger Roll: Two-String Arpeggio" is a thumb-independence drill
    that USES an arpeggio, and it says so by putting the thumb first.
    """
    verdict = classify_family(
        root=SkillRoot.FINGERSTYLE,
        name="Thumb-Bass then Finger Roll: Two-String Arpeggio at Position III",
        skill_node_name="chord-melody arpeggios",
        what="With your thumb holding fret 3 on string 6, pluck strings 4 and 3 in "
        "alternation, creating a rolling arpeggio under the melody.",
    )
    assert verdict.family is TechniqueFamily.F1
    assert verdict.reason == "matched_by_position"


def test_position_matches_are_reported_separately():
    """matched_by_position is a weaker match and must stay auditable as one.

    It is the reason code a coverage review spot-checks first, so it must never be
    flattened into plain `matched`.
    """
    verdict = classify_family(
        root=SkillRoot.FINGERSTYLE,
        name="Arpeggio Roll over a Thumb Bass",
        skill_node_name="chord-melody arpeggios",
        what="Roll the fingers while the thumb holds the bass note.",
    )
    assert verdict.reason in {"matched", "matched_by_position"}
    if verdict.reason == "matched_by_position":
        assert verdict.family is TechniqueFamily.F2  # arpeggio leads this one


# ---------------------------------------------------------------------------
# Cross-root hints — advisory, never authoritative
# ---------------------------------------------------------------------------

def test_cross_root_hint_never_overrides_the_root():
    """A hint is evidence about the SKILL GRAPH, not a classification.

    This drill is pure §8 M2 (syncopation, a TIMING family) but its node roots to
    rhythm. The verdict must stay inside rhythm; M2 may only be reported as a hint.
    """
    verdict = classify_family(
        root=SkillRoot.RHYTHM,
        name="3-Against-4 Accent Drill: Foot vs. Strum",
        skill_node_name="cross-rhythm strumming",
        what="Strum in a repeating 12-beat loop with a 3-against-4 cross rhythm.",
    )
    assert verdict.family is not TechniqueFamily.M2
    if verdict.family is not None:
        assert verdict.family in families_for_root(SkillRoot.RHYTHM)
    assert verdict.cross_root_hint is TechniqueFamily.M2


def test_no_cross_root_hint_when_the_root_already_wins():
    verdict = classify_family(
        root=SkillRoot.RHYTHM,
        name="Single-String Palm Mute Lock",
        skill_node_name="palm-muted eighth notes",
        what="Pick the open low-E string with strict palm muting.",
    )
    assert verdict.family is TechniqueFamily.R2
    assert verdict.cross_root_hint is None


# ---------------------------------------------------------------------------
# Real drill text. Every case below is Sonnet output from a Phase 4.1 eval run.
# ---------------------------------------------------------------------------

REAL_DRILLS = [
    # (name, skill_node_name, what, root, expected_family)
    (
        "Lock the E-Shape Barre at VII",
        "E-shape barre voicings",
        "Hold an E-shape barre at fret 7, pluck strings 6, 3, 2, and 1 one at a time "
        "to confirm each rings clean, then strike the full chord.",
        SkillRoot.CHORD_VOICINGS,
        TechniqueFamily.C2,
    ),
    (
        "Isolate the b3→3 Slide at Position VII",
        "b3-3 slide E-shape",
        "On string 3, pick fret 8 and slide into fret 9 using only fretting-hand "
        "pressure — no second pick attack.",
        SkillRoot.LEAD,
        TechniqueFamily.L3,
    ),
    (
        "Single-String Palm Mute Lock",
        "palm-muted eighth notes",
        "Pick the open low-E string with strict palm muting, eight consecutive "
        "downstrokes per measure.",
        SkillRoot.RHYTHM,
        TechniqueFamily.R2,
    ),
    (
        "A5 → B5 Power Chord Shift",
        "power-chord riff",
        "Drill the tightest chord change in Beat It — A5 to B5 and back.",
        SkillRoot.RHYTHM,
        TechniqueFamily.R3,
    ),
    (
        "G → A7sus4 Cold-Switch",
        "G-Em-D-A7sus4 chord changes",
        "From a G chord shape, shift to an A7sus4 shape, keeping the top two "
        "strings planted.",
        SkillRoot.CHORD_VOICINGS,
        TechniqueFamily.C1,
    ),
    (
        "Eight Downstrokes: Wrist Engine, Not Arm",
        "downstroke pattern",
        "Hold a static four-string chord shape and drive eight consecutive "
        "downstrokes per measure using only wrist rotation.",
        SkillRoot.RHYTHM,
        TechniqueFamily.R1,
    ),
    (
        "Thumb-Over One-String Bass Anchor",
        "thumb-over bass notes",
        "Wrap your thumb over the low E string to fret the 3rd fret, hold it there, "
        "and pick that bass note cleanly 8 times in a row.",
        SkillRoot.FINGERSTYLE,
        TechniqueFamily.F1,
    ),
    (
        "Three-String Ascending Arpeggio Roll",
        "chord-melody arpeggios",
        "Hold an Am7 chord shape and roll upward through strings 4, 2, and 1 — "
        "one finger per string, letting each note sustain into the next.",
        SkillRoot.FINGERSTYLE,
        TechniqueFamily.F2,
    ),
]


@pytest.mark.parametrize(
    "name,node,what,root,expected",
    REAL_DRILLS,
    ids=[d[0][:32] for d in REAL_DRILLS],
)
def test_real_drill_text_classifies_correctly(name, node, what, root, expected):
    verdict = classify_family(root=root, name=name, skill_node_name=node, what=what)
    assert verdict.family is expected, (
        f"{name!r} -> {verdict.family} ({verdict.reason}, score {verdict.score})"
    )


def test_every_classified_family_belongs_to_the_asserted_root():
    """Cross-check the corpus against §8: the expected family must hang off the root.

    Catches a test written against a family from the wrong root, which would
    otherwise look like a classifier bug the first time narrowing did its job.
    """
    for name, _node, _what, root, expected in REAL_DRILLS:
        assert FAMILY_SPECS[expected].root is root, name
