"""FLE-118 — generator prompt teaches the tab schema v2 vocabulary.

Two halves, both pure (no DB, no network, no LLM):

  1. SYSTEM_PROMPT assertions. These are not style checks. Spec §10 row 5 is
     explicit that the v2 schema "buys nothing" until the prompt teaches it, so
     the thing these tests protect is the link between the two: a field added to
     app/models/song.py that the prompt never names is a field the model will
     never emit. The two NEGATIVE assertions matter most — the prompt vocabulary
     is deliberately SMALLER than the schema in exactly two places (§5.1 `0.25`
     bends, §2.5 `bossa`/`latin` feel), and both are the kind of narrowing a
     later reader "fixes" by widening it back to match the schema.

  2. The C8 double-applied-swing check (§2.5 rule 2), including the one case
     Fret named as legitimately NOT a defect.

What these tests cannot do is tell you whether the model obeys any of it. That
needs a live eval re-run; see the EVAL BASELINE note above SYSTEM_PROMPT for why
that re-run is blocked on grade_eval.py's L1/L4 rather than on tokens.
"""
from app.ai.breakdown import SYSTEM_PROMPT
from app.ai.tab_verification import is_double_applied_swing, verify
from app.models.song import (
    Beat,
    Breakdown,
    Chord,
    ChordPosition,
    DURATION_BEATS,
    Measure,
    Note,
    Tab,
)


# ---------------------------------------------------------------------------
# 1. The prompt teaches the §2.1-§7 vocabulary
# ---------------------------------------------------------------------------

def test_every_duration_in_the_canonical_table_is_named_in_the_prompt():
    """§2.1 — all 14, and the table is the source of truth for the list.

    Driving this off DURATION_BEATS rather than a literal list is the point: a
    15th duration added to the schema fails here until someone teaches it.
    """
    missing = [d for d in DURATION_BEATS if d not in SYSTEM_PROMPT]
    assert missing == [], f"durations in the schema the prompt never names: {missing}"


def test_prompt_states_the_canonical_hyphenated_spelling_rule():
    """§2.1 — one spelling. The repo carried three disagreeing tables before
    FLE-115; the aliases survive only in the inbound coercer."""
    assert "<modifier>-<base>" in SYSTEM_PROMPT
    # the two alias spellings the old audit scripts accepted, named as wrong
    assert "dotted_quarter" in SYSTEM_PROMPT
    assert "eighth-triplet" in SYSTEM_PROMPT


def test_prompt_names_the_measured_grid_defect_it_exists_to_fix():
    """FLE-102's finding is the whole reason for the RHYTHM block: 58 of 72
    bars had every note the same duration."""
    assert "58 of 72" in SYSTEM_PROMPT
    assert "RHYTHM" in SYSTEM_PROMPT


def test_prompt_teaches_rests_as_the_syncopation_tool():
    """§3 + §2.5.1 — bossa needs notated syncopation, which is rests plus the
    duration enum, not a style token."""
    assert "RESTS" in SYSTEM_PROMPT
    assert 'rest: "<duration>"' in SYSTEM_PROMPT
    assert "and of 2" in SYSTEM_PROMPT


def test_prompt_teaches_all_four_legato_links_with_their_fret_rules():
    """§4 — each link is validated against the fret it reaches, so a link
    taught without its rule is a link the model gets rejected for."""
    for link in ("tie", "hammer_on", "pull_off", "slide"):
        assert f'"{link}"' in SYSTEM_PROMPT, f"link_to_next value not taught: {link}"
    assert "link_to_next" in SYSTEM_PROMPT
    assert "SAME fret" in SYSTEM_PROMPT
    assert "HIGHER fret" in SYSTEM_PROMPT
    assert "LOWER fret" in SYSTEM_PROMPT
    assert "DIFFERENT fret" in SYSTEM_PROMPT


def test_prompt_teaches_every_bend_kind():
    """§5 — the four standard forms."""
    for kind in ("bend", "pre_bend", "bend_release", "pre_bend_release"):
        assert f'"{kind}"' in SYSTEM_PROMPT, f"bend kind not taught: {kind}"


def test_prompt_teaches_every_technique_and_effect_value():
    """§6 — two fields, deliberately: one exclusive, one stackable."""
    for technique in ("tap", "slap", "pop", "rake", "natural_harmonic",
                      "pinch_harmonic", "artificial_harmonic"):
        assert f'"{technique}"' in SYSTEM_PROMPT, f"technique not taught: {technique}"
    for effect in ("vibrato", "wide_vibrato", "palm_mute", "staccato", "accent",
                   "let_ring", "dead"):
        assert f'"{effect}"' in SYSTEM_PROMPT, f"effect not taught: {effect}"


def test_prompt_teaches_let_ring_at_nominal_duration():
    """§2.4.1 — the sustain convention, and the failure mode it forbids:
    expressing sustain by LENGTHENING a duration breaks the bar sum and
    one-duration-per-beat at once."""
    assert "NOMINAL DURATION OF ITS OWN BEAT" in SYSTEM_PROMPT
    assert "never by lengthening its duration" in SYSTEM_PROMPT


def test_prompt_teaches_capo_relative_frets_and_the_pitch_arithmetic():
    """§7/§7.1 — absolute-frets-plus-a-label would make every open-position
    shape unreadable, which defeats the field."""
    assert "FRET NUMBERS ARE RELATIVE TO THE CAPO" in SYSTEM_PROMPT
    assert "open_pitch(tuning[string]) + capo + fret" in SYSTEM_PROMPT
    assert "24 - capo" in SYSTEM_PROMPT
    assert "sounding_name" in SYSTEM_PROMPT


def test_prompt_teaches_the_three_feel_values():
    """§2.5 — marked, not notated; and the model keeps writing eighths."""
    for feel in ("straight", "swing", "shuffle"):
        assert f'"{feel}"' in SYSTEM_PROMPT, f"feel value not taught: {feel}"
    assert "KEEP WRITING EIGHTHS" in SYSTEM_PROMPT


def test_prompt_forbids_double_applied_swing():
    """§2.5 rule 2 — the prompt side of the C8 check below."""
    assert "NEVER DOUBLE-APPLY THE SWING" in SYSTEM_PROMPT
    assert "4/9 + 2/9" in SYSTEM_PROMPT


def test_prompt_requires_the_snippet_to_inherit_the_songs_setup():
    """§7.1.4 — the highest-value catch of Fret's review. A capo-2 drill that
    takes the capo:0 default is two semitones wrong with no error anywhere, and
    `Breakdown._assert_drill_setup_matches` raises on a snippet that states a
    DIFFERENT capo, which soft-fails every drill in the breakdown."""
    assert "A SNIPPET INHERITS THE SONG'S SETUP" in SYSTEM_PROMPT
    assert "two semitones" in SYSTEM_PROMPT


def test_prompt_keeps_frets_non_negative_and_sends_muted_notes_to_dead():
    """§6.1 — the renderer drew `fret: -1` while the verifier rejected it. v2
    resolves it on Note; -1 keeps its meaning only on ChordPosition."""
    assert "Note frets are never negative" in SYSTEM_PROMPT
    assert 'effects:["dead"]' in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 1b. The two DELIBERATE narrowings — the prompt vocabulary is smaller than
#     the schema, and these are the tests that stop someone widening it.
# ---------------------------------------------------------------------------

def test_the_schema_still_permits_the_curl_so_ground_truth_can_be_right():
    """§5.1 — the withholding is a PROMPT decision, not a schema one. If this
    test starts failing, the narrowing below lost its justification: the whole
    argument for withholding 0.25 from the prompt is that the schema keeps it
    for hand-committed ground truth."""
    from app.models.song import Bend
    assert Bend(semitones=0.25).semitones == 0.25


def test_prompt_withholds_the_quarter_tone_curl():
    """§5.1 — DO NOT widen this to match the schema. An under-taught model
    emitting half-step bends on the b3 is a defensible simplification; one
    scattering random quarter-tones is not. Revisit only once the generator
    places 0.5 bends correctly on blues repertoire."""
    assert "DO NOT EMIT 0.25" in SYSTEM_PROMPT
    # taught amounts start at a half step
    for amount in ("0.5", "1.0", "1.5", "2.0", "2.5", "3.0"):
        assert amount in SYSTEM_PROMPT, f"bend amount not taught: {amount}"


def test_prompt_does_not_offer_a_bossa_or_latin_feel():
    """§2.5 rule 3 — the enum is three values on purpose. A style token would
    let the model emit a label in place of the syncopation, which is the exact
    substitution that produced this schema change. The prompt may NAME bossa
    while routing it to "straight"; what it must not do is offer it as a value.

    So this asserts on the ENUM, not on the absence of the word. The first
    draft of this test banned the substring `"bossa"` outright and failed
    against a prompt that was RIGHT: the sentence 'There is no "bossa" or
    "latin" value' is the clearest possible statement of the narrowing, and a
    test forbidding the prompt from saying it would push the prompt toward
    saying nothing — which is how the model ends up inventing the value.
    """
    assert 'is exactly three values: "straight" (the default), "swing",\n"shuffle"' in SYSTEM_PROMPT
    assert 'There is no "bossa" or "latin" value and you must not ask for one.' in SYSTEM_PROMPT
    # bossa is named only to route it to straight, never offered as a value
    assert "including all bossa and Latin" in SYSTEM_PROMPT
    # the schema agrees, so widening the enum there fails here too
    from typing import get_args

    from app.models.song import Tab as _Tab

    assert set(get_args(_Tab.model_fields["feel"].annotation)) == {
        "straight",
        "swing",
        "shuffle",
    }


# ---------------------------------------------------------------------------
# 2. C8 — the double-applied-swing check
# ---------------------------------------------------------------------------

def _note(string: int, fret: int, duration: str = "quarter") -> Note:
    return Note(string=string, fret=fret, duration=duration)


def _breakdown(measures: list[Measure], feel: str = "straight") -> Breakdown:
    """Em7, not E, and the choice is load-bearing for every `result.ok` below.

    The C8 bars are all built from string 3 at frets 7 and 9 — G+7 = D and
    G+9 = E. Declaring plain E = {E, G#, B} made OWN_CHORDS block on the D,
    so the first draft's `assert result.ok` was failing on an unrelated check
    and would have gone on passing for the wrong reason if C8 ever started
    blocking. Em7 = {E, G, B, D} covers both notes, which leaves C8 the only
    check with anything to say about these bars — the whole point of the
    fixture.
    """
    return Breakdown(
        tab=Tab(measures=measures, feel=feel),
        chords=[Chord(name="Em7", positions=[ChordPosition(string=6, fret=0)])],
        technique_notes=[],
    )


def _swung_pairs_bar(feel: str) -> Breakdown:
    """A 4/4 bar written as four triplet-quarter/triplet-eighth pairs.

    Sums to exactly 4 beats (4 x (2/3 + 1/3)), so METER passes and C8 is the
    only thing with anything to say about it.
    """
    beats = []
    for _ in range(4):
        beats.append(Beat(notes=[_note(3, 7, "triplet-quarter")]))
        beats.append(Beat(notes=[_note(3, 9, "triplet-eighth")]))
    return _breakdown([Measure(beats=beats, time_signature="4/4")], feel=feel)


def test_swung_pairs_under_a_swing_feel_warns():
    result = verify(_swung_pairs_bar("swing"))
    assert any(w.startswith("DOUBLE_SWING:") for w in result.warnings), result.warnings


def test_swung_pairs_under_a_shuffle_feel_warns():
    result = verify(_swung_pairs_bar("shuffle"))
    assert any(w.startswith("DOUBLE_SWING:") for w in result.warnings), result.warnings


def test_double_swing_never_blocks_a_write():
    """Informational, like C4. The arithmetic argument would justify blocking,
    but no cached breakdown carries a `feel` field at all, so the
    false-positive rate is unmeasured — and FLE-103 §6's C7 (100% FP rate on
    correct data) is the precedent for not gating writes on that."""
    result = verify(_swung_pairs_bar("swing"))
    assert not any("DOUBLE_SWING" in d for d in result.blocking)
    assert result.ok


def test_literal_triplets_marked_straight_are_not_flagged():
    """Fret's one legitimate exception: a drill whose teaching object IS the
    subdivision ("feel the triplet underneath the shuffle") is written in
    literal triplets with feel: "straight" for that snippet.

    This is why the `feel != "straight"` condition in the predicate is
    load-bearing and must not be dropped to "catch more"."""
    result = verify(_swung_pairs_bar("straight"))
    assert not any("DOUBLE_SWING" in w for w in result.warnings), result.warnings


def test_a_real_triplet_against_a_swing_feel_is_not_flagged():
    """§2.5 — triplets stay LEGAL when feel != straight; they then mean a real
    triplet against the prevailing feel, such as a fill. Three even
    triplet-eighths are not a long-short pair, so nothing fires."""
    beats = [Beat(notes=[_note(3, 7, "triplet-eighth")]) for _ in range(3)]
    beats += [Beat(notes=[_note(3, 7, "quarter")]) for _ in range(3)]
    bd = _breakdown([Measure(beats=beats, time_signature="4/4")], feel="swing")
    result = verify(bd)
    assert not any("DOUBLE_SWING" in w for w in result.warnings), result.warnings
    assert result.ok


def test_plain_eighths_under_a_swing_feel_are_the_correct_answer():
    """The whole point of marking rather than notating: a swing tune is written
    in plain eighths and carries feel: "swing"."""
    beats = [Beat(notes=[_note(3, 7, "eighth")]) for _ in range(8)]
    bd = _breakdown([Measure(beats=beats, time_signature="4/4")], feel="swing")
    result = verify(bd)
    assert not any("DOUBLE_SWING" in w for w in result.warnings), result.warnings


def test_predicate_rejects_an_odd_number_of_columns():
    """"made entirely of pairs" means pairs — an unpaired trailing triplet is a
    different rhythm and not this defect."""
    assert not is_double_applied_swing(
        "swing", ["triplet-quarter", "triplet-eighth", "triplet-quarter"]
    )


def test_predicate_accepts_short_long_as_well_as_long_short():
    """An anticipated/reverse swing pair is the same 2/3 + 1/3 arithmetic and
    the same defect under an already-swung feel."""
    assert is_double_applied_swing(
        "swing", ["triplet-eighth", "triplet-quarter", "triplet-eighth", "triplet-quarter"]
    )


def test_predicate_rejects_a_bar_with_any_non_triplet_column():
    assert not is_double_applied_swing(
        "swing", ["triplet-quarter", "triplet-eighth", "quarter", "quarter"]
    )


def test_predicate_treats_a_missing_feel_as_straight():
    """Tab.feel defaults to "straight"; a caller reading raw JSONB may hand
    this None, and the audit script does exactly that on a pre-v2 row."""
    pairs = ["triplet-quarter", "triplet-eighth"]
    assert not is_double_applied_swing(None, pairs)
    assert not is_double_applied_swing("", pairs)


def test_audit_script_shares_the_one_predicate():
    """The spec asked for this check to exist once, not twice — a duplicated
    rhythm heuristic is how two scripts start disagreeing about what a defect
    is (the three duration tables FLE-115 collapsed)."""
    import importlib

    audit = importlib.import_module("scripts.fle102_tab_audit")
    assert audit.is_double_applied_swing is is_double_applied_swing


def test_audit_c8_fires_on_a_raw_jsonb_measure():
    """The audit deliberately does NOT parse through the Pydantic model —
    parsing would repair the defects it exists to count — so C8 has to work on
    a plain dict too."""
    import importlib

    audit = importlib.import_module("scripts.fle102_tab_audit")
    measure = {
        "time_signature": "4/4",
        "beats": [
            {"notes": [{"string": 3, "fret": 7, "duration": d}]}
            for d in ["triplet-quarter", "triplet-eighth"] * 4
        ],
    }
    codes = [c for c, _ in audit.check_measure(measure, "shuffle")]
    assert "C8" in codes
    assert "C8" not in [c for c, _ in audit.check_measure(measure, "straight")]
