"""FLE-80 — the §8 classifier measured against the drills production actually holds.

WHY THIS FILE EXISTS SEPARATELY from test_family_classify.py. That file's corpus is
hand-picked Sonnet output chosen to exercise one family each, so it measures whether
the rules work on text written to trip them. It passed 8/8 while the classifier was
silently declining half of production.

This corpus is the opposite: it is EVERY drill in every production breakdown at the
time FLE-80 was filed — songs 60, 71, 73, 74, 76, sixteen drills, no selection — so
the pass rate here is a coverage measurement rather than a rules check. The payloads
are lifted verbatim from `songs.breakdown` and the roots are the real two-hop
`skill_nodes` walk, which is how a corpus like this catches the thing the curated one
cannot: that the vocabulary gap was concentrated in one root.

The numbers this file pins are the before/after of FLE-80:

    classifier v1:  8/16 classified, 2 distinct cells, 1 pilot-band cell at stock >= 2
    classifier v2: 16/16 classified, 4 distinct cells, 2 pilot-band cells at stock >= 2

WHEN A MARKER CHANGES AND A CASE HERE FAILS, the question to ask is not "how do I make
it pass" — it is §10.1's question: is the expected family in this file still the honest
one? A wrong label fabricates a stocked cell. Loosening an assertion here to keep the
count up is the exact failure mode the whole taxonomy exists to prevent.

NOT A PROD DEPENDENCY. The payloads are frozen literals; this file touches no network
and no database. Refresh it deliberately, by re-running the projection in FLE-80, not
because a test went red.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.sessions.coverage import MIN_CELL_STOCK, pilot_band_cells
from app.sessions.family_classify import CLASSIFIER_VERSION, classify_family
from app.sessions.taxonomy import SkillRoot, TechniqueFamily, Tier, tier_from_raw


@dataclass(frozen=True)
class ProdDrill:
    """One drill out of a production breakdown, plus the family it honestly is.

    `song_id`/`index` are the drill's address in `songs.breakdown->'drills'`, kept so
    a failure here can be traced back to the row it came from.

    `raw` is the drill's real UNCLAMPED §9.1 tier score, carried instead of the
    `tab_snippet` it was computed from. The snippets are multi-kilobyte note arrays and
    embedding sixteen of them would bury the text this file is actually about; the tab
    rubric that turns one into a raw score is test_session_taxonomy.py's job. Keeping
    the real raw here is what lets the cell assertions below land on the same cells the
    production projection landed on, family clamp included.
    """

    song_id: int
    index: int
    name: str
    node: str
    root: SkillRoot
    what: str
    criterion: str
    trap: str
    expected: TechniqueFamily
    raw: int

    @property
    def label(self) -> str:
        return f"{self.song_id}#{self.index}-{self.name[:30]}"


# ---------------------------------------------------------------------------
# The corpus. Verbatim; see the module docstring before editing any of it.
# ---------------------------------------------------------------------------

PROD_DRILLS: tuple[ProdDrill, ...] = (
    ProdDrill(
        song_id=60, index=0,
        name="Thumb-Down / Fingers-Up Attack on E Shape",
        node="Chord-Melody Integration (~65 BPM)",
        root=SkillRoot.CHORD_VOICINGS,
        what=(
            "Hold an open E shape. Strike the bass note (string 6) with your thumb, "
            "then brush strings 3-2-1 upward with your index finger. Rest. Repeat. "
            "The goal is clean separation between the thumb note and the finger "
            "sweep — two distinct sonic events, not one smear."
        ),
        criterion=(
            "The bass thumb note lands squarely on the beat and is audibly separate "
            "from the finger sweep every single rep — no smearing at target tempo."
        ),
        trap=(
            "Beginners clench the pick hand and strum everything at once — relax "
            "and let the thumb move independently from the fingers."
        ),
        expected=TechniqueFamily.C1,
        raw=6,
    ),
    ProdDrill(
        song_id=60, index=1,
        name="Two-Note Blues Tag After Chord Stab",
        node="Blues Rhythm Embellishments (~65 BPM)",
        root=SkillRoot.RHYTHM,
        what=(
            "Strike a partial open E voicing (strings 4-3-2), then immediately add "
            "a hammer-on at fret 2 on string 4 followed by a pull-off back to open "
            "— a classic two-note blues embellishment. The chord stab and the tag "
            "must feel like one continuous gesture, not two separate events."
        ),
        criterion=(
            "The chord and the hammer-on tag flow as one phrase — no gap or "
            "hesitation between the stab and the blues note at target tempo."
        ),
        trap=(
            "Players pause after the chord to reposition for the hammer — the tag "
            "must start before the chord fully rings out."
        ),
        expected=TechniqueFamily.R3,
        raw=5,
    ),
    ProdDrill(
        song_id=60, index=2,
        name="Minor-Pentatonic Descending Line on String 2",
        node="Major/Minor Pentatonic Interplay (~65 BPM)",
        root=SkillRoot.LEAD,
        what=(
            "From fret 5 on string 2, descend stepwise through frets 4, 3, 2, 1, "
            "and resolve to the open string — all on string 2, one note per eighth "
            "beat. Then shift to fret 4 on string 3 and play the same descending "
            "idea. This drills the position shift between the two string layers "
            "that carries the pentatonic melody in a chord-melody context."
        ),
        criterion=(
            "Each note of the descending line rings cleanly with even volume — no "
            "note is louder or deader than its neighbor, and the string-to-string "
            "shift at the midpoint is seamless."
        ),
        trap=(
            "Players speed up through the descending line and clip the last note "
            "before the open string — keep the rhythm locked to the beat, "
            "especially near the open."
        ),
        expected=TechniqueFamily.L4,
        raw=3,
    ),
    ProdDrill(
        song_id=71, index=0,
        name="Lock In the E-Chord Shuffle Pulse",
        node="Blues Shuffle (E)",
        root=SkillRoot.RHYTHM,
        what=(
            "Hold the two-finger shape (root + 5th) on the low E and A strings at "
            "the 2nd fret — like an E-chord shuffle grip — and alternate picking "
            "the open bass string against the fretted pair, keeping a swung-eighth "
            "feel throughout. The goal is consistency of attack and groove, not "
            "speed."
        ),
        criterion=(
            "The groove feels like a rocking train — even, swung, no hesitation "
            "between the open bass and the fretted strings."
        ),
        trap=(
            "Lifting both fretting fingers off the strings between each beat "
            "instead of keeping the shape planted."
        ),
        expected=TechniqueFamily.R3,
        raw=6,
    ),
    ProdDrill(
        song_id=71, index=1,
        name="Shift the Shuffle from E to A: One-Bar Chord Change",
        node="Blues Shuffle (E)",
        root=SkillRoot.RHYTHM,
        what=(
            "Play two beats of the E-chord shuffle grip (open 6th string + fretted "
            "5th/4th), then move the same two-finger shape up to the A string "
            "without stopping the pulse. The fretting hand relocates — the rhythm "
            "doesn't."
        ),
        criterion=(
            "The two-beat transition between string sets is seamless — no audible "
            "gap, hiccup, or rush at the moment the hand moves."
        ),
        trap=(
            "Stopping the rhythm hand while the fretting hand relocates — treat the "
            "move as a hand-sync problem, not a fretting problem."
        ),
        expected=TechniqueFamily.R3,
        raw=6,
    ),
    ProdDrill(
        song_id=71, index=2,
        name="Double-Stop Mini-Barre: Plant and Strike",
        node="Double-Stop Rhythm Licks",
        root=SkillRoot.RHYTHM,
        what=(
            "Lay your index finger flat across strings 1 and 2 at fret 3, pick them "
            "together, then slide the same flat finger up to fret 5 and pick again. "
            "No individual finger changes — the whole barre moves as one unit."
        ),
        criterion=(
            "Both strings of every double-stop ring at equal volume with no fret "
            "buzz — your index finger applies balanced pressure across both strings "
            "at each position."
        ),
        trap=(
            "Rolling the index finger onto its side at one of the frets, which "
            "mutes the thinner string."
        ),
        expected=TechniqueFamily.R3,
        raw=5,
    ),
    ProdDrill(
        song_id=71, index=3,
        name="Pentatonic Descent: String-Cross Into the Turnaround",
        node="Pentatonic Scale Runs",
        root=SkillRoot.LEAD,
        what=(
            "Pick a four-note descending run crossing from string 2 to string 3 in "
            "the Eb minor pentatonic box near fret 3–5. Alternate pick strictly — "
            "down on the first note of each string — and make the last note land "
            "precisely on beat 4 of the bar."
        ),
        criterion=(
            "The run sounds fluid and even, with the final note hitting beat 4 like "
            "a door closing — rhythmically locked, not stumbled into."
        ),
        trap=(
            "Rushing the string change from string 2 to string 3 — your pick "
            "crosses the string before your fretting finger is set."
        ),
        expected=TechniqueFamily.L4,
        raw=3,
    ),
    ProdDrill(
        song_id=73, index=0,
        name="Hammer & Pull in the Bb Minor Box",
        node="Legato Hammer-ons & Pull-offs",
        root=SkillRoot.LEAD,
        what=(
            "On strings 3 and 2 at frets 7–9 and 8–10 respectively, alternate "
            "hammer-ons ascending and pull-offs descending — one string at a time. "
            "Pick only the first note of each pair; the second note must ring "
            "purely from finger pressure alone. Keep both notes equal in volume."
        ),
        criterion=(
            "Every legato note rings as clearly and as loudly as the picked note — "
            "no muffled ghosts, no cheating re-picks."
        ),
        trap=(
            "Beginners let the pull-off slip off the side of the string rather than "
            "plucking downward into the next string — the note dies instead of "
            "singing."
        ),
        expected=TechniqueFamily.L3,
        raw=3,
    ),
    ProdDrill(
        song_id=73, index=1,
        name="Cross-String Pentatonic Descent at Position VII",
        node="Pentatonic Scale Runs",
        root=SkillRoot.LEAD,
        what=(
            "Descend across strings 2 and 3 using only the Bb minor pentatonic "
            "shape at fret 7–10: start on fret 10 (string 2), pull off to fret 8, "
            "cross to string 3 fret 9, pull off to fret 7, then reverse and ascend. "
            "Use alternate picking for the string crosses only; use legato within "
            "each string."
        ),
        criterion=(
            "The phrase flows in a smooth, even arc — no rhythmic hiccup at the "
            "string crossing, no louder or softer note breaking the melodic line."
        ),
        trap=(
            "Players rush the string crossing because the pick is already in motion "
            "— plant the pick on the new string before the beat, not on it."
        ),
        expected=TechniqueFamily.L4,
        raw=3,
    ),
    ProdDrill(
        song_id=73, index=2,
        name="Three-String Pentatonic Box Run",
        node="Pentatonic Scale Runs",
        root=SkillRoot.LEAD,
        what=(
            "Ascend strings 4, 3, and 2 using the Bb minor pentatonic positions at "
            "frets 7–10: string 4 frets 7 and 9, string 3 frets 7 and 9, string 2 "
            "frets 8 and 10. Use one pick stroke per note. This drills the full box "
            "shape used in the melody's widest range phrases."
        ),
        criterion=(
            "You can run the box ascending and descending without pausing to find "
            "the next string — the shape is in your hand, not just your eyes."
        ),
        trap=(
            "Beginners collapse the wrist when crossing from string 3 to string 2, "
            "causing the fretting finger to mute the new string — keep the wrist "
            "slightly arched throughout."
        ),
        expected=TechniqueFamily.L4,
        raw=2,
    ),
    ProdDrill(
        song_id=74, index=0,
        name="Lock the Index: Anchor and Roll",
        node="Pentatonic Scale Runs",
        root=SkillRoot.LEAD,
        what=(
            "Keep your index finger planted at fret 12 and alternate-pick across "
            "strings 2 and 1, hitting fret 12 on each. The goal is zero anchor- "
            "finger lift — your index never leaves contact with both strings as you "
            "cross between them."
        ),
        criterion=(
            "Both strings ring clean and equal in volume, index never visibly lifts "
            "off the fretboard."
        ),
        trap=(
            "Letting the index finger pop fully off the neck when reaching for the "
            "pinky at fret 15."
        ),
        expected=TechniqueFamily.L4,
        raw=3,
    ),
    ProdDrill(
        song_id=74, index=1,
        name="Pinky Reach: Fret 12 to Fret 15",
        node="Pentatonic Scale Runs",
        root=SkillRoot.LEAD,
        what=(
            "On strings 1 and 2 individually, alternate between index at fret 12 "
            "and pinky at fret 15. This isolates the stretch that defines the top "
            "of the pentatonic box at the 12th position."
        ),
        criterion=(
            "Fret 15 rings as clearly and at the same volume as fret 12 — no pinky "
            "buzz or muted note."
        ),
        trap=(
            "Collapsing the pinky joint so the fingertip can't press cleanly, "
            "causing a dead or buzzing note at fret 15."
        ),
        expected=TechniqueFamily.L4,
        raw=3,
    ),
    ProdDrill(
        song_id=74, index=2,
        name="Box 1 Descending: String Skip with Shape Change",
        node="Pentatonic Scale Runs",
        root=SkillRoot.LEAD,
        what=(
            "Descend through the B minor pentatonic box 1 shape, changing between "
            "the two-fret patterns on each string pair (frets 12 and 14 on strings "
            "3–4, frets 12 and 15 on strings 1–2). This is the core shape-change "
            "challenge of the 12th-position pentatonic box."
        ),
        criterion=(
            "The transition from the 12/15 pattern (strings 1–2) to the 12/14 "
            "pattern (strings 3–4) is seamless — no hesitation, no buzzing on "
            "string 3."
        ),
        trap=(
            "Reaching for fret 15 on string 3 instead of fret 14 — the pattern "
            "changes between string 2 and string 3, and beginners miss this every "
            "time."
        ),
        expected=TechniqueFamily.L4,
        raw=3,
    ),
    ProdDrill(
        song_id=76, index=0,
        name="Lock the Thumb: Bass-Only Alternation on E",
        node="Alternating Bass Fingerpick Pattern",
        root=SkillRoot.RHYTHM,
        what=(
            "Hold an open E chord shape and pick only the two bass strings. Strike "
            "string 6 (root), then string 5 (fifth), then string 6, then string 5 — "
            "four times across one measure. No treble strings at all. Build a "
            "metronome-locked thumb habit before adding fingers."
        ),
        criterion=(
            "The two bass notes are perfectly even in volume and timing — no "
            "rushing the fifth, no hesitation returning to the root."
        ),
        trap=(
            "Letting the thumb slow down or stiffen between strings — keep the arc "
            "smooth and continuous, like a pendulum."
        ),
        expected=TechniqueFamily.R3,
        raw=2,
    ),
    ProdDrill(
        song_id=76, index=1,
        name="Add a Single Treble Note to the Bass Pulse",
        node="Alternating Bass Fingerpick Pattern",
        root=SkillRoot.RHYTHM,
        what=(
            "Hold an open A chord shape. Play bass on string 5 (open, beat 1), then "
            "pluck string 3 with your middle finger (beat 2), bass again on string "
            "5 (beat 3), then string 3 again (beat 4). This is the simplest bass- "
            "treble alternation skeleton — thumb and one finger only."
        ),
        criterion=(
            "Bass and treble alternate with zero audible gap — it sounds like one "
            "continuous pulse, not two separate hands fighting each other."
        ),
        trap=(
            "Plucking the treble string too hard to 'hear it over' the bass — aim "
            "for equal volume on every note."
        ),
        expected=TechniqueFamily.R3,
        raw=2,
    ),
    ProdDrill(
        song_id=76, index=2,
        name="Full Root-Fifth-Treble Pattern on a Held Shape",
        node="Alternating Bass Fingerpick Pattern",
        root=SkillRoot.RHYTHM,
        what=(
            "Hold a G chord shape (string 6 fret 3, string 5 fret 2, strings 4 and "
            "3 open). Pick the pattern: bass (string 6), treble (string 4), inner- "
            "bass (string 5), treble (string 4) — all eighth notes. This trains the "
            "full alternating bass fingerpick cycle at the neck position where the "
            "G section lives."
        ),
        criterion=(
            "The full eight-note loop runs without any pause or accent bump on beat "
            "5 — it should feel like one unbroken cycle, not two groups of four."
        ),
        trap=(
            "Tensing the fretting hand on the fret-3 root note — plant it once and "
            "relax; don't re-grip for every cycle."
        ),
        expected=TechniqueFamily.R3,
        raw=3,
    ),
)


def _classify(d: ProdDrill):
    """Exactly the call scripts/backfill_drill_taxonomy.py makes per row."""
    return classify_family(
        root=d.root,
        name=d.name,
        skill_node_name=d.node,
        what=d.what,
        success_criterion=d.criterion,
        common_trap=d.trap,
    )


# ---------------------------------------------------------------------------
# Per-drill: the label, not just the count
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("drill", PROD_DRILLS, ids=[d.label for d in PROD_DRILLS])
def test_every_production_drill_classifies_to_its_family(drill: ProdDrill):
    verdict = _classify(drill)
    assert verdict.family is drill.expected, (
        f"{drill.label}: got {verdict.family} ({verdict.reason}, score {verdict.score}, "
        f"runner-up {verdict.runner_up} at {verdict.runner_up_score})"
    )


def test_no_expected_family_contradicts_its_root():
    """A corpus entry naming a family from another root would read as a classifier bug.

    Root narrowing means such a case could never pass, so catch it as the test-data
    error it is. Same guard test_family_classify.py puts on its own corpus.
    """
    for d in PROD_DRILLS:
        assert d.expected.root is d.root, d.label


# ---------------------------------------------------------------------------
# The §10 numbers FLE-80 moved
# ---------------------------------------------------------------------------

def test_the_whole_corpus_classifies():
    """8/16 under v1. The regression this file was written to catch is a drop below 16.

    A drop here does not necessarily mean the classifier got worse — a marker change
    can legitimately trade one label for a decline. It means someone has to look.
    """
    declined = [(d.label, _classify(d).reason) for d in PROD_DRILLS
                if not _classify(d).classified]
    assert declined == [], f"{len(declined)} of {len(PROD_DRILLS)} declined: {declined}"


def test_no_drill_is_classified_only_by_headline_position():
    """`matched_by_position` is the weakest verdict the classifier can return.

    None of these sixteen needs it, and a marker change that makes one depend on it
    has moved a label onto a tie-break rather than onto evidence. Spot-check before
    relaxing this.
    """
    weak = [d.label for d in PROD_DRILLS if _classify(d).reason == "matched_by_position"]
    assert weak == [], weak


def _cell(family: TechniqueFamily, raw: int) -> tuple[TechniqueFamily, Tier]:
    """The (family, tier) cell a drill stocks — the family clamp applied to a raw score.

    This is compute_tier()'s second half, lifted out because the corpus carries `raw`
    rather than the tab snippet it came from. Order is the load-bearing part and it is
    the same order the backfill uses: family FIRST, because §9.1's clamp is what stops
    a rubric that cannot see bends from filing an L2 drill at D1.
    """
    tier = tier_from_raw(raw)
    low, high = family.tier_range
    if tier < low:
        tier = low
    elif tier > high:
        tier = high
    return family, tier


def _cells() -> dict[tuple[TechniqueFamily, Tier], int]:
    """(family, tier) -> stock over the whole corpus. A decline stocks nothing."""
    stock: dict[tuple[TechniqueFamily, Tier], int] = {}
    for d in PROD_DRILLS:
        verdict = _classify(d)
        if not verdict.classified:
            continue
        key = _cell(verdict.family, d.raw)
        stock[key] = stock.get(key, 0) + 1
    return stock


def test_the_corpus_spreads_over_more_than_two_cells():
    """v1 put all 8 of its classifications in 2 cells (L4/D2 and L3/D1).

    The §10.3 gate counts CELLS, so concentration is the failure mode that matters —
    16 drills in one cell would clear the count test above and still stock one cell.
    """
    assert len(_cells()) >= 4, _cells()


def test_the_cells_are_the_ones_the_production_projection_measured():
    """Pin the exact §10 stock, not just its shape.

    These four cells are what FLE-80's read-only projection against production
    produced. A marker or rubric change that shuffles drills between cells while
    keeping the counts is the case the two tests above cannot see.
    """
    assert _cells() == {
        (TechniqueFamily.C1, Tier.D2): 1,
        (TechniqueFamily.R3, Tier.D2): 7,
        (TechniqueFamily.L3, Tier.D1): 1,
        (TechniqueFamily.L4, Tier.D2): 7,
    }


def test_at_least_two_pilot_band_cells_reach_min_stock():
    """The gate's own unit. v1 reached 1 of 45; v2 reaches 2.

    Low in absolute terms and deliberately asserted as a floor, not a target: five
    songs cannot stock 45 cells and this test is not pretending otherwise. It exists
    so a marker regression that halves the bank's gradeable coverage goes red.
    """
    band = set(pilot_band_cells())
    reached = [c for c, n in _cells().items() if n >= MIN_CELL_STOCK and c in band]
    assert len(reached) >= 2, (reached, _cells())


def test_classifier_version_records_the_marker_change():
    """§8's rules are versioned so a coverage number can name what produced it.

    v1 is the vocabulary that declined 8 of these 16. Any later marker edit has to
    bump past 2 as well, or a stored family becomes untraceable to its rules.
    """
    assert CLASSIFIER_VERSION >= 2


# ---------------------------------------------------------------------------
# The specific calibration decisions FLE-80 made, pinned so they are not undone
# by accident
# ---------------------------------------------------------------------------

def test_lowering_min_score_would_misfile_a_fingerpicking_drill_as_strumming():
    """Why MIN_SCORE stayed at 3 — the concrete case, not the principle.

    FLE-80's declines all scored 1 or 2, which invites lowering the bar to 2. This
    drill is what a bar of 2 would buy: R1 scores 2 on it from the single secondary
    word "pattern", and it is an alternating-bass fingerpicking drill. The real fix
    was primary vocabulary, and with that in place R1 loses on margin instead of
    winning on a threshold.
    """
    drill = next(d for d in PROD_DRILLS if (d.song_id, d.index) == (76, 2))
    verdict = _classify(drill)
    assert verdict.family is TechniqueFamily.R3
    assert verdict.runner_up is TechniqueFamily.R1
    assert verdict.runner_up_score == 2, (
        "R1 still scores exactly the secondary-only 2 that a MIN_SCORE of 2 would "
        "have promoted over the correct answer"
    )


def test_bare_shuffle_does_not_outrank_strumming():
    """Why "shuffle" is an R3 secondary and "shuffle pulse" is the primary.

    As a bare primary, "shuffle" ties 6-6 with R1 on a node like "Blues Shuffle
    Strumming" and then wins the headline-ORDER tie-break because it comes first —
    filing a strumming drill as a figure drill. Specificity has to break this, not
    position.
    """
    verdict = classify_family(
        root=SkillRoot.RHYTHM,
        name="Blues Shuffle Strumming at 90",
        skill_node_name="Blues Shuffle Strumming",
        what="Strum the shuffle pattern with a relaxed wrist, eight to the bar.",
    )
    assert verdict.family is TechniqueFamily.R1, (
        f"got {verdict.family} ({verdict.reason}, score {verdict.score})"
    )


def test_a_rhythm_figure_named_only_by_its_own_vocabulary_classifies():
    """The FLE-80 gap in one line: shuffle/pulse/boogie/alternating-bass are mechanics.

    Under v1 every one of these scored 0-2 and the drill stayed NULL, stocking no cell.
    """
    for name in ("Lock the Shuffle Pulse", "Boogie Bass Figure on E",
                 "Alternating Bass Thumb Lock", "Chord Stab and Release"):
        verdict = classify_family(root=SkillRoot.RHYTHM, name=name, what="")
        assert verdict.family is TechniqueFamily.R3, f"{name} -> {verdict.family}"


def test_hyphenated_stroke_spellings_match_too():
    """_normalize folds hyphens to spaces, so "down-stroke" is NOT "downstroke".

    Both spellings have to be listed. This is the latent half of the same bug — it
    did not show up in FLE-80's sixteen, and it would have eventually.
    """
    for name in ("Eight Down-Strokes per Bar", "Eight Downstrokes per Bar",
                 "Up-Stroke Accent Drill"):
        verdict = classify_family(root=SkillRoot.RHYTHM, name=name, what="")
        assert verdict.family is TechniqueFamily.R1, f"{name} -> {verdict.family}"


def test_cross_root_hints_still_surface_the_skill_graph_question():
    """Classifying these did not silence the evidence that they are rooted oddly.

    Song 76's node "Alternating Bass Fingerpick Pattern" sits under the RHYTHM root in
    production — its sub-skill is literally called "Fingerpicking Patterns". The drills
    now classify R3, which is what the rooted grid says a bass figure under rhythm IS,
    and the F1 hint is still recorded so the rooting can be argued about with numbers.
    That hint is exactly what a fix to the skill-graph generator would be measured by.
    """
    hinted = [d.label for d in PROD_DRILLS
              if _classify(d).cross_root_hint is TechniqueFamily.F1]
    assert hinted, "the F1 cross-root signal on the song-76 drills was lost"
