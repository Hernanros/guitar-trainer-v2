"""FLE-115 — tab schema v2: model, coercer, canonical duration table.

Pure Pydantic — no DB, no network, no LLM. Deliverable 1 of
`.planning/design/261005-fle105-tab-schema-v2.md` (accepted, commit 1b20620);
section markers below are to that document.

The suite is organised around the spec's repair policy rather than around the
fields, because that split is the thing most likely to be broken by a later
edit: a VALUE with a nearest-legal answer is coerced and warns, a RELATIONSHIP
that contradicts itself raises. Getting that backwards is not a style error —
coercing a contradiction hides a two-semitone pitch bug (§7.1.4), and raising on
a value drops a whole cached song (§8.2), since `tab` has no soft-fail path.

Run with `server/.venv/bin/python -m pytest` — bare python3 is 3.9.6 here and
fails on the 3.10+ syntax in this package with a misleading error.
"""
from __future__ import annotations

import logging
import uuid
from fractions import Fraction

import pytest
from pydantic import ValidationError

from app.models.song import (
    DURATION_BEATS,
    MAX_FRET,
    Beat,
    Bend,
    Breakdown,
    Chord,
    ChordPosition,
    Drill,
    Measure,
    Note,
    Tab,
    TechniqueNote,
    beat_beats,
    canonical_duration,
    duration_beats,
    measure_beats,
    time_signature_beats,
    transpose_chord_name,
)

# The three duration values FLE-102 measured across all 532 notes of the cached
# corpus. Nothing else has ever been emitted.
CACHED_DURATIONS = ["quarter", "eighth", "half"]


def _note(**kw) -> Note:
    return Note(**{"string": 3, "fret": 5, "duration": "quarter", **kw})


def _tab(beats=None, **kw) -> Tab:
    beats = beats if beats is not None else [Beat(notes=[_note()])]
    return Tab(measures=[Measure(beats=beats, time_signature="4/4")], **kw)


def _drill(**overrides) -> dict:
    """A valid Drill as a DICT — the inbound shape §7.1.4 inheritance acts on.

    Breakdown's `_inherit_tab_setup` is a mode="before" validator, so it only
    sees raw dicts; passing a constructed Drill would skip the behaviour under
    test, which is precisely the bug (a snippet silently taking capo=0).
    """
    d = dict(
        name="Isolate the b3 curl",
        target_skill_temp_id=str(uuid.uuid4()),
        song_specific=True,
        what="Two notes on the G string under the capo. Nothing else.",
        tab_snippet={
            "measures": [{"beats": [{"notes": [
                {"string": 3, "fret": 3, "duration": "quarter"}]}],
                "time_signature": "4/4"}],
        },
        start_bpm=60,
        target_bpm=70,
        repetitions=10,
        success_criterion="The curl arrives on the beat.",
    )
    d.update(overrides)
    return d


def _breakdown(tab: dict, *, chords=None, drills=None) -> dict:
    out = dict(
        tab=tab,
        chords=chords if chords is not None else [],
        technique_notes=[{"heading": "h", "body": "b"}],
    )
    if drills is not None:
        out["drills"] = drills
    return out


# ---------------------------------------------------------------------------
# §2.1 / §2.2 — the vocabulary and the one table
# ---------------------------------------------------------------------------


def test_duration_table_is_the_closed_vocabulary():
    """§2.1/§2.2 — 14 values, and the table covers exactly them."""
    assert len(DURATION_BEATS) == 14
    for name in DURATION_BEATS:
        assert _note(duration=name).duration == name


def test_duration_table_values_are_exact_fractions():
    """§2.2 — `Fraction`, not float. This is the whole point of the table."""
    assert all(isinstance(v, Fraction) for v in DURATION_BEATS.values())
    assert DURATION_BEATS["quarter"] == 1
    assert DURATION_BEATS["dotted-quarter"] == Fraction(3, 2)
    assert DURATION_BEATS["triplet-eighth"] == Fraction(1, 3)
    assert DURATION_BEATS["triplet-sixteenth"] == Fraction(1, 6)


def test_cached_durations_round_trip():
    """Every duration the cached corpus actually holds parses unchanged."""
    for d in CACHED_DURATIONS:
        assert _note(duration=d).duration == d
        assert d in DURATION_BEATS


def test_v1_note_keyset_still_parses():
    """§7 backward compat — a plain picked note is still {string, fret, duration}."""
    n = Note(**{"string": 1, "fret": 0, "duration": "eighth"})
    assert (n.link_to_next, n.bend, n.technique, n.effects) == (None, None, None, [])


# ---------------------------------------------------------------------------
# §2.3 — sextuplets are expressible, and exact
# ---------------------------------------------------------------------------


def test_sextuplet_bar_sums_to_exactly_one_beat():
    """§2.2/§2.3 — six triplet-sixteenths are one beat EXACTLY."""
    bar = Measure(
        beats=[Beat(notes=[_note(duration="triplet-sixteenth")]) for _ in range(6)],
        time_signature="4/4",
    )
    assert measure_beats(bar) == 1
    assert measure_beats(bar) == Fraction(1)


def test_float_residue_appears_on_a_partial_sum_not_a_complete_bar():
    """CORRECTION to §2.2's stated rationale, measured rather than asserted.

    §2.2 and the FLE-115 issue body both justify `Fraction` with
    `sum([1/6] * 6) != 1.0`. That is FALSE: it is exactly 1.0, because IEEE754
    rounding on these particular denominators is symmetric and cancels. An
    exhaustive search over every bar of <= 7 columns for 4/4, 3/4, 7/8 and 3/8,
    plus 400k sampled bars up to 40 columns, found ZERO float false verdicts —
    no correct bar flagged and no wrong bar passed. The 1e-6 tolerance was never
    hiding anything either: the smallest expressible shortfall is a 32nd, 0.125,
    five orders of magnitude above it.

    The residue is real one step away, on a sum that does NOT complete a bar —
    and that is the case a consumer hits the moment it compares anything other
    than a full measure (a drill snippet's length, a click offset, a partial
    tie group).

    So `Fraction` is still right, for the weaker and true reason: it makes
    exactness a PROPERTY rather than a lucky coincidence of these 14
    denominators, and it lets the meter check drop a tolerance instead of
    picking one. It is not, as the spec claims, repairing a live defect.
    """
    assert sum([1 / 6] * 6) == 1.0            # the spec's claim, refuted
    assert sum([1 / 3] * 5) != float(Fraction(5, 3))   # where it is real

    five = Measure(beats=[Beat(notes=[_note(duration="triplet-eighth")])
                          for _ in range(5)], time_signature="4/4")
    assert measure_beats(five) == Fraction(5, 3)
    assert float(measure_beats(five)) != sum([1 / 3] * 5)


def test_mixed_tuplet_bar_sums_exactly():
    """A 4/4 bar of 3 + 6 + 2 subdivisions sums to 4 with no residue."""
    beats = (
        [Beat(notes=[_note(duration="triplet-quarter")]) for _ in range(3)]
        + [Beat(notes=[_note(duration="triplet-sixteenth")]) for _ in range(6)]
        + [Beat(notes=[_note(duration="eighth")]) for _ in range(2)]
    )
    bar = Measure(beats=beats, time_signature="4/4")
    assert measure_beats(bar) == 4
    assert measure_beats(bar) == time_signature_beats("4/4")


def test_time_signature_beats():
    assert time_signature_beats("4/4") == 4
    assert time_signature_beats("3/4") == 3
    assert time_signature_beats("6/8") == 3
    assert time_signature_beats("7/8") == Fraction(7, 2)
    assert time_signature_beats("garbage") == 4   # falls back, never raises
    assert time_signature_beats("0/0") == 4


# ---------------------------------------------------------------------------
# §8.2 — the coercer. COERCE, DO NOT RAISE.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,want", [
    ("dotted_half", "dotted-half"),
    ("dotted_quarter", "dotted-quarter"),
    ("dotted_eighth", "dotted-eighth"),
    ("eighth-triplet", "triplet-eighth"),
    ("quarter-triplet", "triplet-quarter"),
    ("sixteenth-triplet", "triplet-sixteenth"),
    ("thirty_second", "thirty-second"),
    ("32nd", "thirty-second"),
    ("  QUARTER  ", "quarter"),
    ("dotted quarter", "dotted-quarter"),
])
def test_aliases_canonicalise(raw, want):
    """§8.2 — nothing that passed the pre-v2 audits may fail the v2 parse."""
    assert canonical_duration(raw) == want
    assert _note(duration=raw).duration == want


def test_unknown_duration_coerces_and_does_not_raise(caplog):
    """§8.2 — the governing constraint: `tab` has NO soft-fail path.

    Raising here would not drop a note, it would drop the song, permanently, for
    a cached row. So an unrecognised value becomes a quarter and WARNS.
    """
    with caplog.at_level(logging.WARNING):
        n = _note(duration="semihemidemisemiquaver")
    assert n.duration == "quarter"
    assert any("coerced" in r.getMessage() for r in caplog.records)


def test_unknown_duration_coerces_inside_a_whole_breakdown():
    """The coercion has to survive the real nesting, not just a bare Note."""
    bd = Breakdown(**_breakdown(_tab(
        beats=[Beat(notes=[_note(duration="hemiola")])]).model_dump()))
    assert bd.tab.measures[0].beats[0].notes[0].duration == "quarter"


def test_non_string_duration_still_raises_pydantics_type_error():
    """The coercer passes non-strings through so the caller sees a real error."""
    assert canonical_duration(7) == 7
    with pytest.raises(ValidationError):
        _note(duration=7)


def test_duration_beats_reports_unknown_instead_of_coercing():
    """The audit path must COUNT an unknown duration, not silently repair it.

    `duration_beats` is what scripts/fle102_tab_audit.py and
    scripts/fle103_verify_pilot_tabs.py import in place of their own private
    tables. If it coerced like the write path, the audits would stop being able
    to report the defect they exist to measure.
    """
    assert duration_beats("quarter") == 1
    assert duration_beats("eighth-triplet") == Fraction(1, 3)   # alias accepted
    assert duration_beats("nonsense") is None
    assert duration_beats(None) is None


def test_the_audit_scripts_carry_no_private_duration_table():
    """§2.2 — three disagreeing copies collapse to one. Guards the regression."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("scripts/fle102_tab_audit.py",
                "scripts/fle103_verify_pilot_tabs.py",
                "app/ai/tab_verification.py"):
        src = (root / rel).read_text()
        assert not re.search(r"^DUR\s*=", src, re.M), f"{rel} redefines a table"
        assert "from app.models.song import" in src, f"{rel} imports the one table"


# ---------------------------------------------------------------------------
# §3 — rests
# ---------------------------------------------------------------------------


def test_rest_beat_parses_and_counts_toward_the_bar():
    bar = Measure(beats=[
        Beat(rest="half"),
        Beat(notes=[_note(duration="quarter")]),
        Beat(rest="quarter"),
    ], time_signature="4/4")
    assert beat_beats(bar.beats[0]) == 2
    assert measure_beats(bar) == 4


def test_beat_cannot_be_both_sounding_and_silent():
    """§3 exactly-one-of. RAISES: a column that is both has no honest repair."""
    with pytest.raises(ValidationError, match="never both"):
        Beat(notes=[_note()], rest="quarter")


def test_empty_beat_coerces_to_a_quarter_rest(caplog):
    """A column with nothing sounding IS a rest; the length is the guess."""
    with caplog.at_level(logging.WARNING):
        b = Beat(notes=[])
    assert b.rest == "quarter" and b.notes == []
    assert any("quarter rest" in r.getMessage() for r in caplog.records)


def test_rest_duration_is_canonicalised_too():
    assert Beat(rest="dotted_half").rest == "dotted-half"


# ---------------------------------------------------------------------------
# §2.4 — one duration per beat, promoted to a Beat validator
# ---------------------------------------------------------------------------


def test_beat_mixing_durations_coerces_to_the_longest(caplog):
    """§2.4 — the v1 rule, kept, and coerced rather than raised.

    Coerced because a disagreeing beat is reachable from a cached row, and the
    longest value is the fallback the pre-v2 verifier already took.
    """
    with caplog.at_level(logging.WARNING):
        b = Beat(notes=[_note(string=3, duration="eighth"),
                        _note(string=4, duration="dotted-half")])
    assert {n.duration for n in b.notes} == {"dotted-half"}
    assert beat_beats(b) == 3
    assert any("coerced" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# §4 — legato links. Each check must FIRE.
# ---------------------------------------------------------------------------


def _two_note_tab(link, fret_a, fret_b, *, string_b=3):
    return lambda: _tab(beats=[
        Beat(notes=[_note(string=3, fret=fret_a, link_to_next=link)]),
        Beat(notes=[_note(string=string_b, fret=fret_b)]),
    ])


@pytest.mark.parametrize("link,a,b", [
    ("tie", 7, 7),
    ("hammer_on", 5, 7),
    ("pull_off", 7, 5),
    ("slide", 5, 9),
])
def test_legato_link_accepts_its_legal_target(link, a, b):
    tab = _two_note_tab(link, a, b)()
    assert tab.measures[0].beats[0].notes[0].link_to_next == link


@pytest.mark.parametrize("link,a,b,msg", [
    ("tie", 7, 9, "the same fret"),
    ("hammer_on", 7, 5, "a higher fret"),
    ("hammer_on", 7, 7, "a higher fret"),
    ("pull_off", 5, 7, "a lower fret"),
    ("pull_off", 5, 5, "a lower fret"),
    ("slide", 5, 5, "a different fret"),
])
def test_legato_link_rejects_a_contradicting_target(link, a, b, msg):
    """RAISES: rewriting the target would be inventing a note (§4).

    Safe to raise because `link_to_next` is a v2-only field — no cached
    breakdown can carry one, so this is unreachable from the cache.
    """
    with pytest.raises(ValidationError, match=msg):
        _two_note_tab(link, a, b)()


@pytest.mark.parametrize("link", ["tie", "hammer_on", "pull_off", "slide"])
def test_legato_link_needs_a_next_note_on_the_same_string(link):
    with pytest.raises(ValidationError, match="no next note"):
        _two_note_tab(link, 5, 7, string_b=2)()


def test_legato_link_crosses_the_barline():
    """§4 — a tie over a barline is how sustain past the bar is written."""
    tab = Tab(measures=[
        Measure(beats=[Beat(notes=[_note(fret=7, link_to_next="tie")])]),
        Measure(beats=[Beat(notes=[_note(fret=7)])]),
    ])
    assert tab.measures[0].beats[0].notes[0].link_to_next == "tie"


def test_legato_target_is_the_next_column_not_the_same_one():
    """A second note on the same string in the SAME beat is not the target.

    If the search did not require a later column, a hammer-on would resolve
    against its own beat-mate and the check would pass on nothing.
    """
    with pytest.raises(ValidationError, match="no next note"):
        _tab(beats=[Beat(notes=[
            _note(string=3, fret=5, link_to_next="hammer_on"),
            _note(string=3, fret=9),
        ])])


# ---------------------------------------------------------------------------
# §5 — bends
# ---------------------------------------------------------------------------


def test_bend_keeps_the_quarter_tone_curl():
    """§5.1 — `0.25` stays in the SCHEMA; it is the v1 PROMPT that withholds it.

    The curl is a pitch, not an ornament: in a blues in E the b3 curled toward
    the major 3rd is deliberately neither, and rounding it either way is a pitch
    error.
    """
    assert Bend(semitones=0.25).semitones == 0.25
    assert _note(bend=Bend(semitones=0.25)).bend.kind == "bend"


def test_bend_accepts_an_integer_semitone_count():
    """JSON drops the trailing `.0`, so `1` must mean `1.0`."""
    assert Bend(semitones=1).semitones == 1.0
    assert Bend(**{"semitones": "2.0"}).semitones == 2.0


def test_bend_rejects_an_off_grid_amount():
    with pytest.raises(ValidationError):
        Bend(semitones=0.3)


@pytest.mark.parametrize("kind", ["bend", "pre_bend", "bend_release", "pre_bend_release"])
def test_bend_kinds(kind):
    assert Bend(semitones=1.0, kind=kind).kind == kind


# ---------------------------------------------------------------------------
# §6 / §6.1 — technique, effects, and the dead note
# ---------------------------------------------------------------------------


def test_technique_and_effects_stack_on_one_note():
    """§6 — they cannot be one field.

    A palm-muted, accented hammer-on into a bent note with vibrato is a SINGLE
    note in Comfortably Numb; a one-articulation-per-note model is the same kind
    of lossy as the v1 schema this replaces.
    """
    n = _note(
        link_to_next="hammer_on",
        technique="tap",
        effects=["palm_mute", "accent", "vibrato"],
        bend=Bend(semitones=1.0),
    )
    assert n.technique == "tap"
    assert n.effects == ["palm_mute", "accent", "vibrato"]


def test_effects_are_deduplicated_first_occurrence_wins():
    assert _note(effects=["vibrato", "accent", "vibrato"]).effects == ["vibrato", "accent"]


def test_technique_is_exclusive_and_closed():
    with pytest.raises(ValidationError):
        _note(technique=["tap", "slap"])
    with pytest.raises(ValidationError):
        _note(technique="strummed")


def test_unknown_effect_is_rejected():
    with pytest.raises(ValidationError):
        _note(effects=["reverb"])


def test_legacy_negative_fret_becomes_a_dead_note(caplog):
    """§6.1 — resolves the live contradiction, in the renderer's direction.

    TabNotation.tsx:94 DREW `fret === -1` as a dead note while
    fle103_verify_pilot_tabs.py:143 REJECTED it. v2 settles it: fret is >= 0
    always and a dead note is its fingered fret plus effects:["dead"]. The
    carry-forward is a clamp, not a raise, because the renderer has been
    emitting -1 since v1 so a cached row may hold one.
    """
    with caplog.at_level(logging.WARNING):
        n = _note(fret=-1)
    assert n.fret == 0 and n.effects == ["dead"]
    assert any("negative" in r.getMessage() for r in caplog.records)


def test_dead_effect_is_not_duplicated_on_a_note_that_already_has_it():
    assert _note(fret=-1, effects=["dead", "palm_mute"]).effects == ["dead", "palm_mute"]


def test_fret_is_never_negative_in_the_parsed_model():
    assert _note(fret=-5).fret == 0


def test_chord_position_keeps_minus_one_as_not_played():
    """§7.1.3 — `-1` keeps its v1 meaning on a DIAGRAM, and only there."""
    assert ChordPosition(string=6, fret=-1).fret == -1


# ---------------------------------------------------------------------------
# §2.5 — feel
# ---------------------------------------------------------------------------


def test_feel_defaults_to_straight_and_has_exactly_three_values():
    assert _tab().feel == "straight"
    for f in ("straight", "swing", "shuffle"):
        assert _tab(feel=f).feel == f
    for rejected in ("bossa", "latin", "triplet"):
        with pytest.raises(ValidationError):
            _tab(feel=rejected)


# ---------------------------------------------------------------------------
# §7 / §7.1 — capo and the fret ceiling
# ---------------------------------------------------------------------------


def test_capo_defaults_to_zero_so_cached_rows_read_back_unchanged():
    assert _tab().capo == 0
    assert _tab().tuning == ["E", "A", "D", "G", "B", "e"]


@pytest.mark.parametrize("capo", [0, 2, 7, 12])
def test_fret_ceiling_is_24_minus_capo(capo, caplog):
    """§7.1 — `0 <= fret <= 24 - capo`. The usable range shrinks as capo climbs."""
    ceiling = MAX_FRET - capo
    ok = _tab(beats=[Beat(notes=[_note(fret=ceiling)])], capo=capo)
    assert ok.measures[0].beats[0].notes[0].fret == ceiling

    with caplog.at_level(logging.WARNING):
        over = _tab(beats=[Beat(notes=[_note(fret=ceiling + 1)])], capo=capo)
    assert over.measures[0].beats[0].notes[0].fret == ceiling
    assert any("clamped" in r.getMessage() for r in caplog.records)


def test_capo_itself_is_bounded():
    with pytest.raises(ValidationError):
        _tab(capo=-1)
    with pytest.raises(ValidationError):
        _tab(capo=13)


# ---------------------------------------------------------------------------
# §7.1.4 — the silent one: a snippet INHERITS capo/tuning, never defaults them
# ---------------------------------------------------------------------------

DROP_D = ["D", "A", "D", "G", "B", "e"]


def test_drill_snippet_inherits_a_non_zero_capo():
    """§7.1.4 — the highest-value catch of Fret's review.

    Without inheritance a drill lifted from a capo-2 song takes `capo: 0` and is
    two semitones wrong WITH NO ERROR ANYWHERE: the notes are well-formed, the
    meter sums, and the verifier derives a pitch that is simply the wrong one.
    The DEFAULT is what makes it invisible.
    """
    tab = _tab(capo=2, tuning=DROP_D).model_dump()
    bd = Breakdown(**_breakdown(tab, drills=[_drill(), _drill()]))
    assert [d.tab_snippet.capo for d in bd.drills] == [2, 2]
    assert all(d.tab_snippet.tuning == DROP_D for d in bd.drills)


def test_inheritance_happens_for_a_raw_jsonb_row_too():
    """Both inbound paths matter: a Sonnet tool-call payload and a cached row."""
    row = _breakdown(_tab(capo=4).model_dump(), drills=[_drill(), _drill()])
    assert Breakdown.model_validate(row).drills[0].tab_snippet.capo == 4


def test_drill_snippet_disagreeing_with_its_parent_raises():
    """§7.1.4 — RAISES: overwriting it would hide the exact error it surfaces."""
    tab = _tab(capo=2).model_dump()
    bad = _drill(tab_snippet={**_drill()["tab_snippet"], "capo": 0})
    with pytest.raises(ValidationError, match=r"capo 0 != song tab capo 2"):
        Breakdown(**_breakdown(tab, drills=[bad, _drill()]))


def test_drill_snippet_disagreeing_tuning_raises():
    tab = _tab(tuning=DROP_D).model_dump()
    bad = _drill(tab_snippet={**_drill()["tab_snippet"],
                              "tuning": ["E", "A", "D", "G", "B", "e"]})
    with pytest.raises(ValidationError, match="tuning"):
        Breakdown(**_breakdown(tab, drills=[bad, _drill()]))


def test_pre_v2_cached_row_with_no_capo_keys_anywhere_still_parses():
    """§8.1 — why there is no Alembic migration. Both sides default and agree."""
    bd = Breakdown(**_breakdown(_tab().model_dump(), drills=[_drill(), _drill()]))
    assert bd.tab.capo == 0
    assert all(d.tab_snippet.capo == 0 for d in bd.drills)


# ---------------------------------------------------------------------------
# §7.2 — `name` is the shape, `sounding_name` carries the sound
# ---------------------------------------------------------------------------


def _chord(name, **kw) -> dict:
    return dict(name=name, positions=[{"string": 1, "fret": 0}], **kw)


def test_sounding_name_is_unset_at_capo_zero():
    bd = Breakdown(**_breakdown(_tab().model_dump(), chords=[_chord("Em7")]))
    assert bd.chords[0].sounding_name is None


def test_sounding_name_at_capo_zero_raises():
    """§7.2 — at capo 0 a second name is a duplicate or a contradiction, and
    there is no way to tell which."""
    with pytest.raises(ValidationError, match="set at capo 0"):
        Breakdown(**_breakdown(_tab().model_dump(),
                               chords=[_chord("Em7", sounding_name="Em7")]))


def test_sounding_name_is_derived_above_capo_zero():
    """Wonderwall, capo 2: Em7/G/D/A7sus4 shapes sound F#m7/A/E/B7sus4."""
    shapes = ["Em7", "G", "D", "A7sus4"]
    bd = Breakdown(**_breakdown(_tab(capo=2).model_dump(),
                                chords=[_chord(n) for n in shapes]))
    assert [c.sounding_name for c in bd.chords] == ["F#m7", "A", "E", "B7sus4"]
    assert [c.name for c in bd.chords] == shapes   # the SHAPE is untouched


def test_sounding_name_is_checked_by_pitch_class_not_spelling():
    """F#m7 and Gbm7 are the same chord; either is an acceptable value."""
    for spelling in ("F#m7", "Gbm7"):
        bd = Breakdown(**_breakdown(_tab(capo=2).model_dump(),
                                    chords=[_chord("Em7", sounding_name=spelling)]))
        assert bd.chords[0].sounding_name == spelling


def test_wrong_sounding_name_raises():
    with pytest.raises(ValidationError, match="transposed up 2"):
        Breakdown(**_breakdown(_tab(capo=2).model_dump(),
                               chords=[_chord("Em7", sounding_name="Gm7")]))


def test_sounding_name_with_a_changed_quality_raises():
    """The quality must be unchanged — F#maj7 is not Em7 up two."""
    with pytest.raises(ValidationError, match="quality"):
        Breakdown(**_breakdown(_tab(capo=2).model_dump(),
                               chords=[_chord("Em7", sounding_name="F#maj7")]))


def test_unparseable_chord_name_is_left_alone_not_rejected(caplog):
    """§7.2/§8.2 — chord naming is open vocabulary; refusing to serve a song
    over a nickname would be the §8.2 mistake."""
    with caplog.at_level(logging.WARNING):
        bd = Breakdown(**_breakdown(_tab(capo=2).model_dump(),
                                    chords=[_chord("that shape from the intro")]))
    assert bd.chords[0].sounding_name is None
    assert any("transposed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("name,semis,want", [
    ("Em7", 2, "F#m7"),
    ("G", 2, "A"),
    ("A7sus4", 2, "B7sus4"),
    ("C", 1, "C#"),
    ("B", 1, "C"),
    ("Bb", 2, "C"),
    ("D/F#", 2, "E/Ab"),      # a slash bass moves with the root
    ("Cmaj7#11", 5, "Fmaj7#11"),
])
def test_transpose_chord_name(name, semis, want):
    assert transpose_chord_name(name, semis) == want


def test_transpose_chord_name_reports_what_it_cannot_parse():
    """None distinguishes "could not transpose" from "transposes to itself"."""
    assert transpose_chord_name("H7", 2) is None
    assert transpose_chord_name("", 2) is None


def test_chord_shape_and_sound_survive_a_round_trip():
    """A capoed Breakdown re-reads with the derived name intact, not re-derived
    off the already-transposed one."""
    bd = Breakdown(**_breakdown(_tab(capo=2).model_dump(), chords=[_chord("Em7")]))
    # `drills` is dropped because an empty list round-trips into min_length=2 —
    # pre-existing behaviour (the field only validates when EXPLICITLY given),
    # and not what this test is about.
    again = Breakdown.model_validate(bd.model_dump(exclude={"drills"}))
    assert again.chords[0].name == "Em7"
    assert again.chords[0].sounding_name == "F#m7"
