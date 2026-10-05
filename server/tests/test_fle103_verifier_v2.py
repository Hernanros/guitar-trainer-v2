"""FLE-117 — the pilot-set verifier under tab schema v2.

WHY THIS FILE EXISTS
The headline change in FLE-117 is that `fle103_verify_pilot_tabs.py` folds the
capo into its pitch derivation and skips bent notes in the chord-spelling
checks. Neither path is exercised by re-running the verifier over the committed
pilot set, because **that set contains no capo and no bend** — all 1612 notes
are plain `quarter` on an uncapoed neck. A green run there is real evidence for
the pitch/harmony half and *zero* evidence for the two changes that were the
point. These tests are the only thing standing behind them.

Each test below also asserts the PRE-CHANGE behaviour it replaced, because the
spec's claim is specifically that the old code failed *correct* tab. A test that
only shows the new code passing cannot tell "fixed" from "never broken".

Fixtures are Wonderwall's real situation (§7): standard tuning, capo 2, the Em7
shape, sounding F#m7.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.song import DURATION_BEATS
from scripts.fle103_verify_pilot_tabs import (
    check_chord,
    check_drawn_rhythm,
    check_renderer_parity,
    column_width,
    main,
    open_pcs,
    renderer_duration_table,
    sounding_pcs,
)

STANDARD = ["E", "A", "D", "G", "B", "e"]
OPENS = open_pcs(STANDARD)

# The Em7 shape, strings 6..1 = frets 0,2,0,0,0,0. Uncapoed it sounds
# {E, G, B, D} = Em7; at capo 2 every pitch moves up two and it sounds
# {F#, A, C#, E} = F#m7. One shape, two names — §7.2's whole point.
EM7_SHAPE = [(6, 0), (5, 2), (4, 0), (3, 0), (2, 0), (1, 0)]


def _beat(notes, duration="quarter", rest=None, bends=None):
    """One column. `bends` maps string number -> Bend dict."""
    if rest is not None:
        return {"notes": [], "rest": rest}
    bends = bends or {}
    out = []
    for s, f in notes:
        n = {"string": s, "fret": f, "duration": duration}
        if s in bends:
            n["bend"] = bends[s]
        out.append(n)
    return {"notes": out}


def _measure(beats, time_signature="4/4"):
    return {"beats": beats, "time_signature": time_signature}


def _run(tmp_path, songs, expect_fail=None):
    """Run the real `main()` end to end over a synthetic set; return exit code.

    `expect_fail` is a substring that must appear in the reported failures. It
    is not decoration: every negative test here would also pass on an exit code
    of 1 produced by some unrelated check, and three of them did have a second
    plausible reason to go red. Asserting the reason is what makes them
    evidence.
    """
    doc = {"schema_version": 1, "issue": "FLE-117-test", "songs": songs}
    p = tmp_path / "set.json"
    p.write_text(json.dumps(doc))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(str(p))
    out = buf.getvalue()
    if expect_fail is not None:
        assert rc == 1, f"expected a failure mentioning {expect_fail!r}; got:\n{out}"
        after = out.split("FAILURES:", 1)[1]
        assert expect_fail in after, (
            f"run went red, but not for {expect_fail!r}:\n{after}")
    return rc


def _song(measures, chords, *, title="T", capo=None, difficulty=0.5):
    tab = {"tuning": list(STANDARD), "measures": measures}
    if capo is not None:
        tab["capo"] = capo
    return {"title": title, "catalog_difficulty": difficulty,
            "chord_sequence": chords, "tab": tab}


# --- §7.1 capo ---------------------------------------------------------------

def test_capo_is_added_to_every_note_with_no_special_case_for_fret_zero():
    """§7.1 — `open + capo + fret`, uniform.

    The fret-0 case is the one a reader expects to be special ("the open
    string") and it specifically is not: under a capo at 2, fret 0 is the string
    AT the capo and sounds two semitones sharp. Getting this wrong only on open
    strings would corrupt exactly the open-position shapes the capo-relative
    convention exists to preserve.
    """
    open_e = _beat([(6, 0)])
    fretted = _beat([(6, 3)])
    assert sounding_pcs([open_e], OPENS, 0) == {OPENS[6]}
    assert sounding_pcs([open_e], OPENS, 2) == {(OPENS[6] + 2) % 12}
    assert sounding_pcs([fretted], OPENS, 2) == {(OPENS[6] + 5) % 12}


def test_wonderwall_em7_shape_at_capo_2_spells_fsharp_m7():
    beats = [_beat(EM7_SHAPE)]
    pcs = sounding_pcs(beats, OPENS, 2)
    # F# A C# E
    assert pcs == {6, 9, 1, 4}


def test_correct_capo_tab_passes_v1_v4_and_failed_before_the_capo_term(tmp_path):
    """The regression §7.1 consequence 2 predicted, both directions.

    Same notes, same declared chord. With the capo read, correct tab is green;
    with the capo ignored — which is literally what the pre-FLE-117 script did,
    since it had no `capo` term at all — the same correct tab goes red. This is
    the test that distinguishes "the fix works" from "there was nothing wrong".
    """
    measures = [_measure([_beat(EM7_SHAPE)] * 4) for _ in range(2)]
    chords = ["F#m7", "F#m7"]

    assert _run(tmp_path, [_song(measures, chords, capo=2)]) == 0

    # capo omitted -> the pre-v2 derivation, scored two semitones flat. The
    # foreign notes it names are the Em7 pitches: the shape, not the sound.
    _run(tmp_path, [_song(measures, chords, capo=None)],
         expect_fail="F#m7 sounds foreign ['B', 'D', 'G']")


def test_absent_capo_reads_exactly_as_it_did_before(tmp_path):
    """Migration safety — `capo` absent means 0 and 0 changes nothing.

    Every cached breakdown and the committed pilot set are in this shape, so the
    no-capo path has to be bit-for-bit the old behaviour, not merely close.
    """
    measures = [_measure([_beat(EM7_SHAPE)] * 4)]
    assert _run(tmp_path, [_song(measures, ["Em7"], capo=None)]) == 0
    assert _run(tmp_path, [_song(measures, ["Em7"], capo=0)]) == 0
    assert sounding_pcs(measures[0]["beats"], OPENS, 0) == {4, 7, 11, 2}


def test_fret_ceiling_shrinks_as_the_capo_climbs(tmp_path):
    """§7.1 consequence 1 — `0 <= fret <= 24 - capo`.

    Checked in the verifier even though `Tab._clamp_frets_to_capo` also clamps
    it, because the verifier reads raw JSON: if only the model enforced it, the
    ground truth would say fret 23 and the served tab would say 22, with nothing
    anywhere reporting the disagreement.
    """
    high = [_measure([_beat([(1, 23)])] * 4)]
    assert _run(tmp_path, [_song(high, [None], capo=0)]) == 0    # 23 <= 24
    _run(tmp_path, [_song(high, [None], capo=2)],                # 23 > 22
         expect_fail="fret 23 exceeds 22 (24 - capo 2)")


# --- §5 bends ----------------------------------------------------------------

BEND = {"semitones": 1.0, "kind": "bend"}


def test_a_bent_note_is_not_scored_at_its_fingered_pitch():
    """§5 — a bend sounds `fret + semitones`, which is not an integer fret."""
    beats = [_beat([(1, 2)], bends={1: BEND})]
    assert sounding_pcs(beats, OPENS, 0) == set()
    # and without the bend the same note is scored normally: e + 2 = F#
    assert sounding_pcs([_beat([(1, 2)])], OPENS, 0) == {6}


def test_correct_bend_is_not_flagged_foreign_and_was_before(tmp_path):
    """V1 gets more permissive — the half the spec leads with.

    Beat 2 is a bend fingered at F#, which is foreign to G7. Scoring the
    fingered pitch reports a foreign note on tab that is correct; §5 says skip
    it. Beat 1 carries the full G7 voicing so V2/V3/V4 are satisfied either way
    and the only thing moving is V1.
    """
    g7 = [(6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 1)]   # G B D G B F
    bent = _beat([(1, 2)], duration="half", bends={1: BEND})
    measures = [_measure([_beat(g7, duration="half"), bent])]

    assert _run(tmp_path, [_song(measures, ["G7"])]) == 0

    # same measure, bend removed: the fingered F# is now scored and is foreign
    unbent = _measure([_beat(g7, duration="half"),
                       _beat([(1, 2)], duration="half")])
    _run(tmp_path, [_song([unbent], ["G7"])],
         expect_fail="G7 sounds foreign ['F#']")


def test_a_bend_cannot_supply_the_third_which_is_the_trap_for_fle107(tmp_path):
    """The other direction, asserted on purpose: V2/V3/V4 get STRICTER.

    Skipping bent notes cuts both ways. Here the only B in the measure is a bent
    note, so G7 has no third as printed and V2 fires. That is correct — a third
    reached only by a bend is not sounding at the printed pitch — but it is a
    true-and-surprising failure, and the tempting "fix" when FLE-107 hits it is
    to loosen V2. The honest repair is to notate the landing pitch as its own
    note. This test exists so that loosening V2 breaks something.
    """
    no_third = [(6, 3), (4, 0), (1, 1)]                  # G D F — no B
    bent_b = _beat([(5, 2)], duration="half", bends={5: BEND})
    measures = [_measure([_beat(no_third, duration="half"), bent_b])]
    _run(tmp_path, [_song(measures, ["G7"])],
         expect_fail="G7 contains no third (B absent)")


def test_an_all_bent_measure_warns_once_instead_of_failing_three_times():
    """V1-V4 cannot judge a measure with no derivable pitch class at all.

    Reporting "no third", "no defining tone" and "no root" would blame the
    voicing for what is really the verifier abstaining.
    """
    fails, warns = [], []
    beats = [_beat([(1, 2)], bends={1: BEND})]
    check_chord("G7", beats, OPENS, 0, "W m1", fails, warns)
    assert fails == []
    assert len(warns) == 1 and "every note is bent" in warns[0]


# --- §2.2 exact meter --------------------------------------------------------

def test_a_sextuplet_bar_sums_exactly(tmp_path):
    """Six `triplet-sixteenth` = one beat, exactly (§2.3). Plus 3 quarters = 4."""
    beats = [_beat([(1, 0)], duration="triplet-sixteenth") for _ in range(6)]
    beats += [_beat([(1, 0)]) for _ in range(3)]
    assert _run(tmp_path, [_song([_measure(beats)], [None])]) == 0
    assert sum(DURATION_BEATS["triplet-sixteenth"] for _ in range(6)) == 1


def test_a_bar_short_by_a_thirty_second_fails(tmp_path):
    """No tolerance (§2.2). The smallest expressible shortfall must be caught."""
    beats = [_beat([(1, 0)]) for _ in range(3)]
    beats += [_beat([(1, 0)], duration="sixteenth") for _ in range(3)]
    beats += [_beat([(1, 0)], duration="thirty-second")]       # 3.875, not 4
    _run(tmp_path, [_song([_measure(beats)], [None])],
         expect_fail="durations sum to 3.875 beats, 4/4 wants 4")


def test_a_rest_column_contributes_its_length(tmp_path):
    """§3 — a beat is a sounding column or a silent one, and both have length."""
    beats = [_beat([(1, 0)]), _beat(None, rest="quarter"),
             _beat([(1, 0)], duration="half")]
    assert _run(tmp_path, [_song([_measure(beats)], [None])]) == 0


# --- V6 rewritten against §9.1 ----------------------------------------------

def test_more_than_four_beats_per_measure_is_no_longer_a_failure(tmp_path):
    """The old V6, deleted. §9.1 sizes a measure from its own content.

    Sixteen sixteenths in 4/4 is the bar that used to be rejected with
    "overflows TabNotation MEASURE_WIDTH". It is ordinary music; the limit was
    a renderer constant (§10 row 9).
    """
    beats = [_beat([(1, 0)], duration="sixteenth") for _ in range(16)]
    assert _run(tmp_path, [_song([_measure(beats)], [None])]) == 0


def test_v6a_the_renderers_duration_table_matches_section_2_2():
    """The fourth copy of the table — the one no other Python test touches.

    TabNotation.tsx must re-declare §2.2 in TypeScript because it cannot import
    a Python dict. If that copy drifts, the rhythm the player SEES stops
    matching the rhythm V5 validated, silently.
    """
    theirs = renderer_duration_table()
    assert theirs is not None, "V6a could not parse the renderer's table"
    assert theirs == dict(DURATION_BEATS)

    fails, warns = [], []
    check_renderer_parity(fails, warns)
    assert fails == [] and warns == []


def test_v6a_reports_drift_rather_than_ignoring_it(monkeypatch):
    """A parity check that cannot fail is not a check."""
    import scripts.fle103_verify_pilot_tabs as V

    monkeypatch.setattr(V, "renderer_duration_table",
                        lambda: {**DURATION_BEATS, "eighth": Fraction(1)})
    fails, warns = [], []
    V.check_renderer_parity(fails, warns)
    assert len(fails) == 1 and "'eighth'" in fails[0]

    monkeypatch.setattr(V, "renderer_duration_table", lambda: None)
    fails, warns = [], []
    V.check_renderer_parity(fails, warns)
    assert fails == [] and len(warns) == 1 and "V6a SKIPPED" in warns[0]


def test_v6b_names_the_durations_section_9_1_draws_identically():
    """§9.1 clamps a column to `max(MIN_COLUMN, BEAT_WIDTH * beats)`.

    Everything below 20/48 of a beat therefore draws the same 20px: a
    sixteenth, a dotted-sixteenth, a thirty-second, a triplet-eighth and a
    triplet-sixteenth are mutually indistinguishable on screen. Rhythm is
    visible down to the eighth note and no further.
    """
    assert column_width("eighth") == 24
    clamped = {"sixteenth", "dotted-sixteenth", "thirty-second",
               "triplet-eighth", "triplet-sixteenth"}
    assert {d for d in DURATION_BEATS if column_width(d) == 20} == clamped

    warns = []
    check_drawn_rhythm([_beat([(1, 0)], duration="sixteenth"),
                        _beat([(1, 0)], duration="thirty-second")],
                       "W m1", warns)
    assert len(warns) == 1 and "not on the staff" in warns[0]


def test_v6b_is_a_warning_and_never_turns_the_run_red(tmp_path):
    """§10 row 9 again: a drawing limit must not become a notation rule.

    This bar mixes a sixteenth and a thirty-second — identical on screen — and
    the run still has to pass. Making it fail would re-impose a renderer limit
    on ground truth two checks after deleting the last one.
    """
    beats = [_beat([(1, 0)]) for _ in range(3)]
    beats += [_beat([(1, 0)], duration="sixteenth") for _ in range(2)]
    beats += [_beat([(1, 0)], duration="thirty-second") for _ in range(4)]
    # 3 + 0.5 + 0.5 == 4
    assert _run(tmp_path, [_song([_measure(beats)], [None])]) == 0


# --- the committed set ------------------------------------------------------

def test_the_committed_pilot_set_is_still_green():
    """Deliverable 3's stated verification. See the module docstring for what
    this does NOT prove: the set has no capo, no bend and no rhythm."""
    assert main() == 0


def test_the_pilot_set_no_longer_carries_the_dead_renderer_constraint():
    """§10 row 9 — `max_beats_per_measure: 4` cited TabNotation MEASURE_WIDTH as
    its rationale. A renderer constant had become a rule about the music."""
    path = (Path(__file__).resolve().parents[1] / "data" / "verified_tabs"
            / "pilot_verified_set.json")
    conventions = json.loads(path.read_text())["conventions"]
    assert "max_beats_per_measure" not in conventions
    assert "MEASURE_WIDTH" not in json.dumps(conventions)


def test_the_pilot_set_is_still_all_quarter_notes():
    """Pins the caveat so it stays true or gets noticed.

    If FLE-107 lands a re-notation and this test still passes, the re-notation
    did not happen. If it fails, the set has rhythm in it for the first time and
    every "the pilot set proves no rhythm" caveat in this repo needs rereading.
    """
    path = (Path(__file__).resolve().parents[1] / "data" / "verified_tabs"
            / "pilot_verified_set.json")
    durations = {n["duration"]
                 for s in json.loads(path.read_text())["songs"]
                 for m in s["tab"]["measures"]
                 for b in m["beats"]
                 for n in b["notes"]}
    assert durations == {"quarter"}
