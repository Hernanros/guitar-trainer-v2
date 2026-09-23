# server/tests/test_session_plan.py
#
# Pins app/sessions/plan.py to the FLE-4 spec. Offline: no DB, no network, no
# model spend. If one of these fails, either the code drifted from the spec or the
# spec changed and GENERATOR_VERSION owes a bump.

import math

import pytest

from app.sessions.plan import (
    GENERATOR_VERSION,
    MAX_RATING_TAPS,
    MAX_TECHNIQUE_SLOTS,
    MIN_PLANNED_REPS,
    SUPPORTED_TARGET_MINUTES,
    Block,
    SessionMode,
    beats_per_rep,
    block_budget,
    choose_mode,
    completion_ratio,
    has_stretch_slot,
    planned_reps,
    rating_tap_budget,
    rep_seconds,
    repertoire_split,
    round_to_5,
    section_bpm,
    section_tempo_factor,
    slot_seconds,
    technique_slots,
    warmup_bpm,
    warmup_reps,
)


def tab(measures, time_signature="4/4"):
    """Minimal tab_snippet: only the fields §5.5 actually reads."""
    return {"measures": [{"time_signature": time_signature, "beats": []} for _ in range(measures)]}


# ---------------------------------------------------------------------------
# §3.1 — the instantiated budget table. This is the spec's own worked example.
# ---------------------------------------------------------------------------

# T, warmup, technique, repertoire, consolidation, slots, slot_seconds
BALANCED_TABLE = [
    (15, 120, 351, 429, 0, 1, 351),
    (30, 216, 648, 792, 144, 3, 216),
    (45, 324, 972, 1188, 216, 4, 243),
    (60, 360, 1328, 1624, 288, 5, 265),
]


@pytest.mark.parametrize("t,w,tb,rb,c,n,slot", BALANCED_TABLE)
def test_balanced_budget_matches_spec_table(t, w, tb, rb, c, n, slot):
    b = block_budget(t, SessionMode.BALANCED)
    assert (b.warmup, b.technique, b.repertoire, b.consolidation) == (w, tb, rb, c)
    assert technique_slots(b.technique) == n
    assert slot_seconds(b.technique, n) == slot


@pytest.mark.parametrize("t", SUPPORTED_TARGET_MINUTES)
@pytest.mark.parametrize("mode", list(SessionMode))
def test_blocks_sum_to_exactly_the_target(t, mode):
    """§3 — 'Blocks sum to exactly B.' No seconds may be lost to rounding."""
    b = block_budget(t, mode)
    assert b.warmup + b.technique + b.repertoire + b.consolidation == t * 60 == b.total


@pytest.mark.parametrize("t", SUPPORTED_TARGET_MINUTES)
@pytest.mark.parametrize("mode", list(SessionMode))
def test_no_block_is_ever_empty_except_t15_consolidation(t, mode):
    """§12 — 'Never emit an empty block.' T=15 consolidation is the one fold (§2)."""
    b = block_budget(t, mode)
    assert b.warmup > 0 and b.technique > 0 and b.repertoire > 0
    if t == 15:
        assert b.consolidation == 0 and b.folds_consolidation
    else:
        assert b.consolidation >= 120 and not b.folds_consolidation


def test_technique_share_orders_build_above_balanced_above_perform():
    """§3 — mode is the technique-vs-repertoire dial; the ordering is the point."""
    budgets = {m: block_budget(45, m) for m in SessionMode}
    assert (budgets[SessionMode.BUILD].technique
            > budgets[SessionMode.BALANCED].technique
            > budgets[SessionMode.PERFORM].technique)
    assert (budgets[SessionMode.BUILD].repertoire
            < budgets[SessionMode.BALANCED].repertoire
            < budgets[SessionMode.PERFORM].repertoire)


def test_warmup_and_consolidation_clamps_bind_at_60():
    """§3 — a 60-minute session must not buy a 7-minute warm-up."""
    b = block_budget(60, SessionMode.BALANCED)
    assert b.warmup == 360  # clamped down from 0.12 x 3600 = 432
    assert b.consolidation == 288  # 0.08 x 3600, under the 300 ceiling


def test_block_budget_rejects_nonpositive_target():
    with pytest.raises(ValueError):
        block_budget(0, SessionMode.BALANCED)


# ---------------------------------------------------------------------------
# §3.3 — slot count
# ---------------------------------------------------------------------------

def test_slot_cap_holds_at_every_length():
    """§3.3 — MAX_TECHNIQUE_SLOTS is hard regardless of T. Even a 3-hour session."""
    assert technique_slots(block_budget(180, SessionMode.BUILD).technique) == MAX_TECHNIQUE_SLOTS


def test_longer_sessions_buy_longer_slots_not_only_more_of_them():
    """§3.3 — 'A 60-minute session buys LONGER slots, not more of them.'"""
    b45 = block_budget(45, SessionMode.BALANCED)
    b60 = block_budget(60, SessionMode.BALANCED)
    assert slot_seconds(b60.technique, technique_slots(b60.technique)) > slot_seconds(
        b45.technique, technique_slots(b45.technique)
    )


def test_slots_never_go_under_the_minimum_slot_length():
    """§3.3 — 'never slots under 2:30'."""
    for t in SUPPORTED_TARGET_MINUTES:
        for mode in SessionMode:
            b = block_budget(t, mode)
            n = technique_slots(b.technique)
            assert n >= 1
            assert slot_seconds(b.technique, n) >= 150 or n == 1


def test_thin_bank_shrinks_n_rather_than_padding():
    """§12 step 1 — 'shrink N to the number of eligible candidates (down to 1)'."""
    tb = block_budget(60, SessionMode.BALANCED).technique
    assert technique_slots(tb, eligible_candidates=2) == 2
    assert technique_slots(tb, eligible_candidates=99) == MAX_TECHNIQUE_SLOTS
    assert technique_slots(tb, eligible_candidates=0) == 0


def test_t15_has_no_stretch_slot():
    """§11.1 — 'Skipped entirely when N == 1.' The one drill isn't the hard one."""
    assert has_stretch_slot(technique_slots(block_budget(15, SessionMode.BALANCED).technique)) is False
    assert has_stretch_slot(technique_slots(block_budget(45, SessionMode.BALANCED).technique)) is True


# ---------------------------------------------------------------------------
# §6 — rating taps
# ---------------------------------------------------------------------------

def test_rating_taps_match_the_spec_worked_examples():
    """§3.2 — T=15 is 2 taps (1 technique + 1 section); T=45 is 5 (4 + 1)."""
    assert rating_tap_budget(1) + 1 == 2
    assert rating_tap_budget(4) + 1 == 5


def test_rating_taps_are_capped_at_six_total():
    """§6 — 'six taps is a session, twelve is a form.'"""
    for n in range(0, 20):
        assert rating_tap_budget(n) + 1 <= MAX_RATING_TAPS


# ---------------------------------------------------------------------------
# §5.3 — repertoire
# ---------------------------------------------------------------------------

def test_repertoire_split_is_lossless_and_matches_t45():
    """§3.2 — T=45 BALANCED is 713s section + 475s play-through."""
    rb = block_budget(45, SessionMode.BALANCED).repertoire
    section, play = repertoire_split(rb, SessionMode.BALANCED)
    assert (section, play) == (713, 475)
    assert section + play == rb


@pytest.mark.parametrize("mode", list(SessionMode))
@pytest.mark.parametrize("t", SUPPORTED_TARGET_MINUTES)
def test_repertoire_split_never_loses_a_second(t, mode):
    rb = block_budget(t, mode).repertoire
    assert sum(repertoire_split(rb, mode)) == rb


def test_perform_mode_favours_the_play_through():
    """§5.3 — PERFORM drops section share to 0.40; the point is to play, not drill."""
    rb = block_budget(45, SessionMode.PERFORM).repertoire
    section, play = repertoire_split(rb, SessionMode.PERFORM)
    assert play > section


@pytest.mark.parametrize("readiness,factor", [
    (0.0, 0.70), (0.39, 0.70), (0.40, 0.85), (0.69, 0.85), (0.70, 1.00), (1.0, 1.00),
])
def test_section_tempo_factor_bands(readiness, factor):
    """§5.3 — boundaries are inclusive-low, so 0.40 is already the 0.85 band."""
    assert section_tempo_factor(readiness) == factor


def test_section_bpm_rounds_to_a_multiple_of_five():
    """§1 — 'Always a multiple of 5.'"""
    assert section_bpm(120, 0.2) == 85   # 84 -> 85
    assert section_bpm(120, 0.5) == 100  # 102 -> 100
    assert section_bpm(120, 0.9) == 120
    for bpm in range(40, 241):
        assert section_bpm(bpm, 0.5) % 5 == 0


def test_round_to_5_rounds_halves_away_from_zero():
    """Guards against banker's rounding making 2.5 -> 2 and desyncing reproducibility."""
    assert round_to_5(82.5) == 85
    assert round_to_5(87.5) == 90


# ---------------------------------------------------------------------------
# §4 — mode selection. First match wins; the ORDER is the rule.
# ---------------------------------------------------------------------------

def base_inputs(**overrides):
    inputs = dict(
        sessions_count=10,
        days_since_last=1,
        completion_3=0.9,
        player_level=0.5,
        weakest_root_mastery=0.45,
        song_readiness=0.3,
    )
    inputs.update(overrides)
    return inputs


def test_rule_1_cold_start_is_perform():
    for count in (0, 1):
        d = choose_mode(**base_inputs(sessions_count=count))
        assert (d.mode, d.rule) == (SessionMode.PERFORM, "cold_start")
        assert d.allow_push is True


def test_rule_2_layoff_is_perform_and_blocks_push():
    """§4 — 'the app never greets a returning player with now do it faster'."""
    d = choose_mode(**base_inputs(days_since_last=7))
    assert (d.mode, d.rule, d.allow_push) == (SessionMode.PERFORM, "layoff", False)
    assert choose_mode(**base_inputs(days_since_last=6)).rule != "layoff"


def test_rule_3_bailing_gives_less_homework_not_more():
    """§4 — the feedback loop the brief specifically asked for."""
    d = choose_mode(**base_inputs(completion_3=0.5))
    assert (d.mode, d.rule, d.allow_push) == (SessionMode.PERFORM, "bailing", False)
    assert choose_mode(**base_inputs(completion_3=0.60)).rule != "bailing"


def test_rule_4_nearly_ready_song_is_perform():
    d = choose_mode(**base_inputs(song_readiness=0.70))
    assert (d.mode, d.rule) == (SessionMode.PERFORM, "song_nearly_ready")


def test_rule_5_root_deficit_is_build():
    """§4 — distance from the player's OWN level, so it fires at any absolute level."""
    d = choose_mode(**base_inputs(player_level=0.5, weakest_root_mastery=0.25))
    assert (d.mode, d.rule) == (SessionMode.BUILD, "root_deficit")
    advanced = choose_mode(**base_inputs(player_level=0.9, weakest_root_mastery=0.6))
    assert advanced.mode == SessionMode.BUILD
    assert choose_mode(**base_inputs(player_level=0.5, weakest_root_mastery=0.26)).mode == SessionMode.BALANCED


def test_rule_order_layoff_beats_root_deficit():
    """First match wins: a returning player gets music even with a gaping root hole."""
    d = choose_mode(**base_inputs(days_since_last=30, weakest_root_mastery=0.0))
    assert (d.mode, d.rule) == (SessionMode.PERFORM, "layoff")


def test_missing_history_never_fires_a_rule():
    """None means 'not computable', not 'zero'. A new user must not read as a layoff."""
    d = choose_mode(sessions_count=5, days_since_last=None, completion_3=None,
                    player_level=0.5, weakest_root_mastery=None, song_readiness=None)
    assert (d.mode, d.rule, d.allow_push) == (SessionMode.BALANCED, "default", True)


def test_mode_selection_is_pure_and_repeatable():
    """§13 — 'Same inputs -> byte-identical plan.'"""
    assert choose_mode(**base_inputs()) == choose_mode(**base_inputs())


# ---------------------------------------------------------------------------
# §5.5 — rep sizing
# ---------------------------------------------------------------------------

def test_beats_per_rep_sums_time_signature_numerators():
    assert beats_per_rep(tab(4)) == 16
    assert beats_per_rep(tab(4, "3/4")) == 12
    assert beats_per_rep(tab(2, "7/8")) == 14
    assert beats_per_rep({"measures": []}) == 0
    assert beats_per_rep({}) == 0


def test_beats_per_rep_defaults_a_junk_time_signature_to_four():
    """A malformed snippet must not crash generation; 4/4 is the safe assumption."""
    assert beats_per_rep({"measures": [{"time_signature": "nonsense"}]}) == 4
    assert beats_per_rep({"measures": [{}]}) == 4


def test_rep_seconds_has_a_four_second_floor():
    """§5.5 — max(4.0, ...). Nothing is really a 1-second rep."""
    assert rep_seconds(tab(1), 240) == 4.0
    assert rep_seconds(tab(4), 120) == pytest.approx(8.0)


def test_rep_seconds_rejects_nonpositive_bpm():
    with pytest.raises(ValueError):
        rep_seconds(tab(4), 0)


def test_planned_reps_honours_the_duty_cycle_and_the_drill_ceiling():
    """§5.5 — reps fit the clock; drill.repetitions is a CEILING, not a plan."""
    # 240s slot, 4 measures at 120bpm = 8s/rep. 240 * 0.75 / 8 = 22 reps.
    assert planned_reps(tab(4), 120, 240, repetitions_ceiling=30) == 22
    assert planned_reps(tab(4), 120, 240, repetitions_ceiling=12) == 12


def test_planned_reps_returns_none_when_the_drill_does_not_fit():
    """§5.5 — the rule that stops a valid session being pedagogically worthless.

    An 8-measure drill at 60bpm is 32s per rep. A 240s slot buys 5 reps, under
    MIN_PLANNED_REPS, so it is DROPPED from candidates rather than planned for 5.
    """
    assert planned_reps(tab(8), 60, 240, repetitions_ceiling=30) is None
    assert planned_reps(tab(8), 60, 256, repetitions_ceiling=30) == MIN_PLANNED_REPS


def test_planned_reps_never_returns_below_the_minimum():
    for measures in range(1, 12):
        for bpm in (50, 80, 120, 200):
            for seconds in (150, 216, 243, 265, 351):
                got = planned_reps(tab(measures), bpm, seconds, repetitions_ceiling=30)
                assert got is None or got >= MIN_PLANNED_REPS


# ---------------------------------------------------------------------------
# §5.1 — warm-up
# ---------------------------------------------------------------------------

def test_warmup_is_planned_below_the_working_rung():
    """§5.1 — 'familiarity at minute zero is a ramp'."""
    assert warmup_bpm(rung_bpm=100, start_bpm=60) == 90


def test_warmup_never_drops_below_the_drills_own_start_bpm():
    assert warmup_bpm(rung_bpm=55, start_bpm=50) == 50
    assert warmup_bpm(rung_bpm=50, start_bpm=50) == 50


def test_warmup_reps_are_sixty_percent_rounded_up():
    assert warmup_reps(20) == 12
    assert warmup_reps(21) == math.ceil(21 * 0.6) == 13
    assert warmup_reps(1) == 1
    assert warmup_reps(0) == 1


# ---------------------------------------------------------------------------
# §6 — completion
# ---------------------------------------------------------------------------

def test_skips_count_against_completion():
    """§6 — this is what feeds mode rule 3. A skip is not a completion."""
    assert completion_ratio(3, 6) == 0.5
    assert completion_ratio(6, 6) == 1.0
    assert completion_ratio(0, 0) == 0.0


def test_bailing_threshold_round_trips_through_mode_selection():
    """End-to-end on the loop: three half-finished sessions -> a mostly-song session."""
    ratio = completion_ratio(3, 7)
    d = choose_mode(**base_inputs(completion_3=ratio))
    assert d.rule == "bailing"
    section, play = repertoire_split(block_budget(30, d.mode).repertoire, d.mode)
    assert play > section


def test_generator_version_is_pinned():
    """§13 — a constant change in the spec is a version bump, not a silent edit."""
    assert GENERATOR_VERSION == "session-gen/1.0.0"
    assert [b.value for b in Block] == ["warmup", "technique", "repertoire", "consolidation"]
