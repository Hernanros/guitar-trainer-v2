# server/tests/test_session_assemble.py
#
# Pins app/sessions/assemble.py to FLE-4 §2, §5.3, §5.4, §6, §7.3 and §12 rung 5.
# Offline: no DB, no network, no model spend.
#
# Two properties these tests exist to protect, both of them the issue's own
# definition of done:
#
#   1. SAME INPUTS -> BYTE-IDENTICAL PLAN (§13). Asserted by building the same
#      snapshot twice and comparing the row dicts, and by checking that nothing in
#      the plan depends on process-local state.
#   2. SECONDS ARE NEVER LOST. sum(planned_seconds) == T x 60 for every shape,
#      including every degraded one. A session that quietly loses four minutes is a
#      session the player experiences as broken and the telemetry reads as fine.

from datetime import date, timedelta

import pytest

from app.sessions.assemble import (
    GeneratorInputs,
    ConsolidationSong,
    ItemKind,
    NoMaterialError,
    SongMaterial,
    build_plan,
    plan_as_rows,
    session_seed,
    _consolidation_drill,
    _unrated_technique_ids,
)
from app.sessions.plan import (
    GENERATOR_VERSION,
    LAYOFF_REENTRY_DAYS,
    MAX_RATING_TAPS,
    MIN_PLANNED_REPS,
    SUPPORTED_TARGET_MINUTES,
    Block,
    SessionMode,
    block_budget,
    repertoire_split,
)
from app.sessions.select import Degradation, DrillCandidate, TechniquePick
from app.sessions.taxonomy import SkillRoot, Tier

TODAY = date(2026, 9, 23)
USER = "11111111-1111-1111-1111-111111111111"


def tab(beats):
    """One 4/4 measure from a list of beats, each a list of (string, fret, duration)."""
    return {
        "measures": [
            {
                "time_signature": "4/4",
                "beats": [
                    {"notes": [{"string": s, "fret": f, "duration": d} for s, f, d in beat]}
                    for beat in beats
                ],
            }
        ]
    }


# Same fixture tiers as test_session_select.py, for the same reason: the band
# filter is tier-gated and a drill that lands outside the band under test would
# pass a test for the wrong reason.
D2_TAB = tab([[(1, f, "eighth")] for f in (5, 7, 5, 9)])  # w/ 100->120


def drill(drill_id, **kw):
    """A D2 candidate, in band for the default context (player_level 0.5)."""
    defaults = dict(
        skill_node_id=f"node-{drill_id}",
        tab_snippet=D2_TAB,
        start_bpm=100,
        target_bpm=120,
        repetitions=30,
        node_mastery=0.5,
        rung_bpm=100,
        attempts=3,
        last_practiced_on=TODAY - timedelta(days=5),
        root=SkillRoot.LEAD,
    )
    defaults.update(kw)
    return DrillCandidate(drill_id=drill_id, **defaults)


SONG = SongMaterial(
    song_id=74,
    bpm=120,
    readiness=0.50,
    section_skill_node_id="node-section",
    skill_node_ids=frozenset({"node-a"}),
)


def inputs(**kw):
    """A snapshot that produces a full four-block session; tests override one thing.

    sessions_count is 9 and completion_3 is 0.9 so §4's PERFORM rules 1 and 3 do not
    fire — a test about block sizes must not silently be a test about cold start.
    """
    defaults = dict(
        user_id=USER,
        local_calendar_day=TODAY,
        tz_offset_minutes=-180,
        target_minutes=45,
        sessions_count=9,
        days_since_last=1,
        completion_3=0.90,
        player_level=0.50,
        root_mastery={SkillRoot.LEAD.value: 0.50},
        song=SONG,
        candidates=[drill(f"d{i}") for i in range(6)],
        can_play_songs=[ConsolidationSong(song_id=12, bpm=90)],
    )
    defaults.update(kw)
    return GeneratorInputs(**defaults)


def by_block(plan, block):
    return [i for i in plan.items if i.block is block]


# ---------------------------------------------------------------------------
# §2 — block order and the shape of a full session
# ---------------------------------------------------------------------------

def test_the_four_blocks_come_out_in_fixed_order_with_contiguous_indexes():
    """§2 — the generator never shuffles blocks, and item_index is the plan order."""
    plan = build_plan(inputs())
    assert [i.item_index for i in plan.items] == list(range(plan.item_count))
    order = [i.block for i in plan.items]
    assert order == sorted(
        order,
        key=lambda b: [Block.WARMUP, Block.TECHNIQUE, Block.REPERTOIRE, Block.CONSOLIDATION].index(b),
    )
    assert order[0] is Block.WARMUP
    assert order[-1] is Block.CONSOLIDATION


def test_a_full_session_has_one_warmup_one_consolidation_and_two_repertoire_items():
    plan = build_plan(inputs())
    assert len(by_block(plan, Block.WARMUP)) == 1
    assert len(by_block(plan, Block.CONSOLIDATION)) == 1
    rep = by_block(plan, Block.REPERTOIRE)
    assert [i.kind for i in rep] == [ItemKind.SONG_SECTION, ItemKind.SONG_PLAY]
    assert by_block(plan, Block.TECHNIQUE)


@pytest.mark.parametrize("minutes", SUPPORTED_TARGET_MINUTES)
def test_seconds_are_never_lost_at_any_supported_length(minutes):
    """The invariant. §3's budget sums to exactly T x 60 and the assembler moves
    seconds between items without ever dropping one."""
    plan = build_plan(inputs(target_minutes=minutes))
    assert plan.planned_seconds == minutes * 60


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param(dict(candidates=[]), id="no_drills"),
        pytest.param(dict(song=None), id="no_song"),
        pytest.param(dict(can_play_songs=[]), id="no_can_play_song"),
        pytest.param(dict(candidates=[drill("only")]), id="one_drill_bank"),
        pytest.param(
            dict(candidates=[], can_play_songs=[]), id="song_only"
        ),
        pytest.param(
            dict(song=None, can_play_songs=[]), id="drills_only"
        ),
    ],
)
def test_seconds_are_never_lost_in_any_degraded_shape(shape):
    """Every rung of §12 plus the two shapes the spec does not cover. A degraded
    session is still exactly T minutes long."""
    plan = build_plan(inputs(target_minutes=45, **shape))
    assert plan.planned_seconds == 45 * 60


def test_target_minutes_outside_the_locked_four_is_refused_not_rounded():
    """§0 locked 15|30|45|60. A fifth value needs a GENERATOR_VERSION bump, and a
    silent round would make the session shape a function of an unversioned guess."""
    with pytest.raises(ValueError, match="target_minutes"):
        build_plan(inputs(target_minutes=20))


# ---------------------------------------------------------------------------
# §13 — reproducibility
# ---------------------------------------------------------------------------

def test_the_same_snapshot_produces_the_identical_plan():
    """§13 — same inputs, byte-identical plan. This is the issue's done-when."""
    first = build_plan(inputs())
    second = build_plan(inputs())
    assert plan_as_rows(first) == plan_as_rows(second)
    assert first.seed == second.seed


def test_candidate_order_does_not_change_the_plan():
    """A plan that depends on the row order the loader happened to return is not
    reproducible in the sense §13 means — the SQL has no ORDER BY guarantee."""
    forward = [drill(f"d{i}") for i in range(6)]
    plan_a = build_plan(inputs(candidates=forward))
    plan_b = build_plan(inputs(candidates=list(reversed(forward))))
    assert plan_as_rows(plan_a) == plan_as_rows(plan_b)


def test_the_seed_is_the_user_and_the_day_and_nothing_else():
    plan = build_plan(inputs())
    assert plan.seed == session_seed(USER, TODAY) == f"{USER}|2026-09-23"
    assert plan.generator_version == GENERATOR_VERSION


def test_a_different_day_moves_the_seeded_choices():
    """The consolidation song and the stretch slot are seeded on the day, so two
    days running should not be the identical session."""
    songs = [ConsolidationSong(song_id=i, bpm=90) for i in (11, 12, 13, 14, 15)]
    picks = set()
    for offset in range(12):
        plan = build_plan(
            inputs(local_calendar_day=TODAY + timedelta(days=offset), can_play_songs=songs)
        )
        picks.add(by_block(plan, Block.CONSOLIDATION)[0].song_id)
    assert len(picks) > 1


# ---------------------------------------------------------------------------
# §5.3 — repertoire
# ---------------------------------------------------------------------------

def test_section_work_is_unskippable_and_rated_and_the_play_through_is_neither():
    """§6 — repertoire item 1 is THE ONLY unskippable item in the session."""
    plan = build_plan(inputs())
    section, play = by_block(plan, Block.REPERTOIRE)
    assert section.skippable is False
    assert section.rated is True
    assert section.click_enabled is True
    assert section.target_skill_node_id == "node-section"
    assert play.skippable is True
    assert play.rated is False
    assert play.click_enabled is False  # §5.3 — player toggle, defaults off.
    assert [i for i in plan.items if not i.skippable] == [section]


def test_the_section_split_follows_the_mode_share():
    plan = build_plan(inputs())
    budget = block_budget(plan.target_minutes, plan.mode)
    expected_section, expected_play = repertoire_split(budget.repertoire, plan.mode)
    section, play = by_block(plan, Block.REPERTOIRE)
    assert section.planned_seconds == expected_section
    assert play.planned_seconds == expected_play


def test_a_song_the_player_is_far_from_is_planned_below_its_own_tempo():
    """§5.3 — tempo_factor 0.70 under 0.40 readiness, 1.00 at or above 0.70."""
    far = build_plan(inputs(song=SongMaterial(song_id=74, bpm=120, readiness=0.10)))
    close = build_plan(inputs(song=SongMaterial(song_id=74, bpm=120, readiness=0.75)))
    assert by_block(far, Block.REPERTOIRE)[0].planned_bpm == 85    # round_to_5(120*0.70)
    assert by_block(close, Block.REPERTOIRE)[0].planned_bpm == 120
    assert by_block(far, Block.REPERTOIRE)[1].planned_bpm == 120   # play-through is always song bpm


def test_unknown_readiness_plans_slow_rather_than_at_full_tempo():
    """Header note (c). The spec leaves this undefined; planning a player at full
    song tempo on zero evidence is the worse of the two errors."""
    plan = build_plan(inputs(song=SongMaterial(song_id=74, bpm=120, readiness=None)))
    assert by_block(plan, Block.REPERTOIRE)[0].planned_bpm == 85
    assert "song_readiness_unknown_planned_slow" in plan.notes


def test_at_t15_the_play_through_carries_the_consolidation_flag_and_there_is_no_fourth_block():
    """§2 exception — a 1-minute consolidation block is theatre. Same end-on-a-win
    effect, no extra block boundary in a session with only 900 seconds."""
    plan = build_plan(inputs(target_minutes=15))
    assert by_block(plan, Block.CONSOLIDATION) == []
    play = by_block(plan, Block.REPERTOIRE)[1]
    assert play.is_consolidation is True
    assert play.rated is False
    assert plan.planned_seconds == 900


@pytest.mark.parametrize("minutes", (30, 45, 60))
def test_above_t15_consolidation_is_its_own_block(minutes):
    plan = build_plan(inputs(target_minutes=minutes))
    assert len(by_block(plan, Block.CONSOLIDATION)) == 1
    assert by_block(plan, Block.REPERTOIRE)[1].is_consolidation is False


# ---------------------------------------------------------------------------
# §5.4 — consolidation
# ---------------------------------------------------------------------------

def test_the_consolidation_item_is_a_can_play_song_unrated_and_unclicked():
    """§5.4 — a rating tap here converts the win back into an assessment."""
    plan = build_plan(inputs())
    item = by_block(plan, Block.CONSOLIDATION)[0]
    assert item.kind is ItemKind.SONG_PLAY
    assert item.song_id == 12
    assert item.planned_bpm == 90
    assert item.rated is False
    assert item.click_enabled is False
    assert item.skippable is True
    assert item.is_consolidation is True


def test_with_no_can_play_song_the_fallback_is_the_most_owned_drill_below_tempo():
    """§5.4 fallback — highest historical clear rate, planned at rung_bpm - 10."""
    owned = drill("owned", attempts=20, lifetime_clears=18, rung_bpm=110)
    shaky = drill("shaky", attempts=20, lifetime_clears=2)
    plan = build_plan(inputs(can_play_songs=[], candidates=[owned, shaky]))
    item = by_block(plan, Block.CONSOLIDATION)[0]
    assert item.kind is ItemKind.DRILL
    assert item.drill_id == "owned"
    assert item.planned_bpm == 100  # 110 - 10
    assert item.rated is False
    assert item.click_enabled is False
    assert "consolidation_fallback_drill" in plan.notes


def test_the_fallback_prefers_a_drill_the_session_has_not_already_used():
    """An assembler judgment, not a spec line: a session that hands you the same
    drill three times reads as a bug even when every rule is satisfied."""
    used = drill("used", attempts=30, lifetime_clears=30)
    fresh = drill("fresh", attempts=4, lifetime_clears=2)
    assert _consolidation_drill([used, fresh], already_planned=frozenset({"used"})).drill_id == "fresh"
    # ...but reuse beats having no consolidation item at all.
    assert _consolidation_drill([used], already_planned=frozenset({"used"})).drill_id == "used"


def test_a_short_clean_history_loses_to_a_long_one_at_the_same_rate():
    """Confidence should follow evidence. 3-of-3 is not 30-of-30."""
    lucky = drill("lucky", attempts=3, lifetime_clears=3)
    proven = drill("proven", attempts=30, lifetime_clears=30)
    assert _consolidation_drill([lucky, proven], already_planned=frozenset()).drill_id == "proven"


def test_a_drill_with_no_history_is_never_the_end_on_a_win_item():
    assert _consolidation_drill([drill("new", attempts=0)], already_planned=frozenset()) is None


def test_with_neither_a_can_play_song_nor_a_practised_drill_the_block_folds_forward():
    """The spec does not cover this. Folded into the play-through exactly as §2 does
    at T=15, rather than inventing a new behaviour or dropping the seconds."""
    plan = build_plan(inputs(can_play_songs=[], candidates=[drill("d", attempts=0)]))
    assert by_block(plan, Block.CONSOLIDATION) == []
    assert by_block(plan, Block.REPERTOIRE)[1].is_consolidation is True
    assert "consolidation_folded_into_play_through" in plan.notes
    assert plan.planned_seconds == 45 * 60


# ---------------------------------------------------------------------------
# §7.3 — layoff re-entry
# ---------------------------------------------------------------------------

def test_a_drill_untouched_for_ten_days_is_planned_one_step_below_its_rung():
    """§7.3 — layoff costs a rung of confidence, never a rung of record. The
    drill_progress row is NOT rewritten; only the plan moves."""
    stale = drill(
        "stale",
        rung_bpm=115,
        last_practiced_on=TODAY - timedelta(days=LAYOFF_REENTRY_DAYS),
    )
    plan = build_plan(inputs(candidates=[stale]))
    item = [i for i in by_block(plan, Block.TECHNIQUE)][0]
    assert item.re_entry is True
    assert item.planned_bpm == 110  # 115 - 5
    assert stale.rung_bpm == 115


def test_nine_days_off_is_not_a_layoff():
    """The boundary is worth a test of its own: 10 is the constant, not 'about a
    week and a half'."""
    recent = drill(
        "recent",
        rung_bpm=115,
        last_practiced_on=TODAY - timedelta(days=LAYOFF_REENTRY_DAYS - 1),
    )
    item = by_block(build_plan(inputs(candidates=[recent])), Block.TECHNIQUE)[0]
    assert item.re_entry is False
    assert item.planned_bpm == 115


def test_re_entry_never_plans_below_the_drills_own_floor():
    at_floor = drill(
        "floor", rung_bpm=100, start_bpm=100, last_practiced_on=TODAY - timedelta(days=40)
    )
    item = by_block(build_plan(inputs(candidates=[at_floor])), Block.TECHNIQUE)[0]
    assert item.re_entry is True
    assert item.planned_bpm == 100


def test_a_never_practised_drill_is_not_a_re_entry():
    """attempts = 0 means no history, which must not read as an infinite layoff."""
    item = by_block(
        build_plan(inputs(candidates=[drill("new", attempts=0, last_practiced_on=None)])),
        Block.TECHNIQUE,
    )[0]
    assert item.re_entry is False


def test_re_entry_reps_are_refitted_to_the_lower_tempo_and_floored_not_dropped():
    """§5.5 returns None to mean 'drop this drill'. After selection, dropping would
    leave a hole in the block, so the floor applies and §6 permits the overrun."""
    stale = drill("stale", rung_bpm=105, last_practiced_on=TODAY - timedelta(days=30))
    item = by_block(build_plan(inputs(candidates=[stale])), Block.TECHNIQUE)[0]
    assert item.planned_bpm == 100
    assert item.planned_reps >= MIN_PLANNED_REPS


# ---------------------------------------------------------------------------
# §6 — rating taps
# ---------------------------------------------------------------------------

def test_a_session_never_asks_for_more_than_six_taps():
    """§6 — rating fatigue is a real failure mode: six taps is a session, twelve is
    a form. Checked at every length, since N grows with T."""
    for minutes in SUPPORTED_TARGET_MINUTES:
        plan = build_plan(inputs(target_minutes=minutes))
        assert plan.rated_item_count <= MAX_RATING_TAPS


def test_the_warmup_and_the_consolidation_item_are_never_rated():
    """§5.1 and §5.4. The warm-up never touches the ladder; the win is not an exam."""
    plan = build_plan(inputs())
    assert by_block(plan, Block.WARMUP)[0].rated is False
    assert by_block(plan, Block.CONSOLIDATION)[0].rated is False


def test_when_the_cap_binds_the_lowest_scored_technique_slots_go_unrated():
    """§6's tie-break. Cannot fire at MAX_TECHNIQUE_SLOTS = 5 (N + 1 <= 6 always),
    so it is exercised directly — the rule is written against the spec, not against
    today's constants."""
    picks = tuple(
        TechniquePick(
            candidate=drill(f"d{i}"),
            tier=Tier.D2,
            score=float(i),
            planned_bpm=100,
            planned_reps=10,
        )
        for i in range(6)
    )
    unrated = _unrated_technique_ids(picks)
    assert unrated == frozenset({"d0"})  # lowest score of the six
    assert _unrated_technique_ids(picks[:5]) == frozenset()


# ---------------------------------------------------------------------------
# §12 rung 5 + the shapes the spec does not cover
# ---------------------------------------------------------------------------

def test_an_empty_bank_converts_the_technique_block_into_section_work():
    """§12 rung 5 — unfilled technique seconds become SECTION work specifically.
    The block that lost time was the working block, so the song section absorbs it
    rather than the play-through."""
    plan = build_plan(inputs(candidates=[]))
    assert by_block(plan, Block.TECHNIQUE) == []
    assert by_block(plan, Block.WARMUP) == []
    budget = block_budget(45, plan.mode)
    section = by_block(plan, Block.REPERTOIRE)[0]
    expected_section, expected_play = repertoire_split(budget.repertoire, plan.mode)
    assert section.planned_seconds == expected_section + budget.technique + budget.warmup
    assert by_block(plan, Block.REPERTOIRE)[1].planned_seconds == expected_play
    assert "rung5_technique_to_repertoire" in plan.notes
    assert "warmup_unfilled" in plan.notes


def test_a_one_drill_bank_still_produces_a_real_session():
    """§12 — 'a 15-minute session with one drill and 10 minutes of song work is a
    good session. A session with three coming soon cards is not a session.'"""
    plan = build_plan(inputs(target_minutes=15, candidates=[drill("only")]))
    technique = by_block(plan, Block.TECHNIQUE)
    assert len(technique) == 1
    assert technique[0].drill_id == "only"
    assert by_block(plan, Block.WARMUP)  # the fallback warm-up, never an empty block
    assert plan.planned_seconds == 900


def test_a_user_with_no_song_spends_the_repertoire_budget_on_technique():
    """Header note (b): the other direction from §12, and the one that happens to a
    genuinely new user. The pool is widened BEFORE the fill so slots are sized for it."""
    plan = build_plan(inputs(song=None))
    assert by_block(plan, Block.REPERTOIRE) == []
    assert "no_song_repertoire_to_technique" in plan.notes
    budget = block_budget(45, plan.mode)
    technique_seconds = sum(i.planned_seconds for i in by_block(plan, Block.TECHNIQUE))
    consolidation = sum(i.planned_seconds for i in by_block(plan, Block.CONSOLIDATION))
    warmup = sum(i.planned_seconds for i in by_block(plan, Block.WARMUP))
    assert technique_seconds + consolidation + warmup == 45 * 60
    assert technique_seconds >= budget.technique + budget.repertoire


def test_nothing_to_practise_raises_rather_than_emitting_a_placeholder():
    """§12 — never emit a placeholder item. The API turns this into 'finish
    onboarding', not a 500 and not a session of three empty cards."""
    with pytest.raises(NoMaterialError):
        build_plan(inputs(candidates=[], song=None, can_play_songs=[]))


def test_a_degradation_rung_is_recorded_on_the_plan():
    """A session that came out odd should be explicable from its own record."""
    plan = build_plan(inputs())
    assert plan.degradation == Degradation.STRICT
    assert plan.degradation_name == "strict"
    # Every drill already practised in the last two sessions -> §12.3 must fire.
    recent = [drill(f"d{i}") for i in range(3)]
    degraded = build_plan(
        inputs(candidates=recent, recent_drill_ids=frozenset(c.drill_id for c in recent))
    )
    assert degraded.degradation >= Degradation.ALLOW_RECENCY
    assert f"degradation:{degraded.degradation_name}" in degraded.notes
    assert any(i.repeat_ok for i in by_block(degraded, Block.TECHNIQUE))


# ---------------------------------------------------------------------------
# §4 — the mode reaches the plan, and the preference reaches the shape
# ---------------------------------------------------------------------------

def test_the_mode_and_the_rule_that_chose_it_are_both_on_the_plan():
    """§4 — 'so a session is explicable later'. The rule name is the explanation."""
    cold = build_plan(inputs(sessions_count=0))
    assert cold.mode is SessionMode.PERFORM
    assert cold.mode_rule == "cold_start"
    assert cold.allow_push is True

    layoff = build_plan(inputs(days_since_last=8))
    assert layoff.mode_rule == "layoff"
    assert layoff.allow_push is False  # §7.4 — the whole session consolidates.

    bailing = build_plan(inputs(completion_3=0.40))
    assert bailing.mode_rule == "bailing"
    assert bailing.allow_push is False


def test_bailing_produces_a_session_that_is_mostly_song():
    """§4 rule 3 is the loop the brief actually asked for: a player who bails gets
    LESS homework, not more."""
    bailing = build_plan(inputs(completion_3=0.40))
    healthy = build_plan(inputs(completion_3=0.95))
    def song_share(plan):
        song = sum(i.planned_seconds for i in plan.items if i.song_id is not None)
        return song / plan.planned_seconds
    assert song_share(bailing) > song_share(healthy)


@pytest.mark.parametrize("minutes", SUPPORTED_TARGET_MINUTES)
def test_the_onboarding_preference_is_what_sets_the_length(minutes):
    """The issue's own note: session_length_min is captured in onboarding and
    NOTHING reads it. This is the line where that stops being true."""
    plan = build_plan(inputs(target_minutes=minutes))
    assert plan.target_minutes == minutes
    assert plan.planned_seconds == minutes * 60


def test_a_longer_session_buys_longer_slots_not_more_of_them():
    """§3.3 — past five technique items a session reads as a checklist."""
    bank = [drill(f"d{i}") for i in range(12)]
    counts = {
        m: len(by_block(build_plan(inputs(target_minutes=m, candidates=bank)), Block.TECHNIQUE))
        for m in SUPPORTED_TARGET_MINUTES
    }
    assert max(counts.values()) <= 5
    assert counts[60] == counts[45] or counts[60] == 5


# ---------------------------------------------------------------------------
# The row form the writer persists
# ---------------------------------------------------------------------------

def test_plan_as_rows_emits_plain_enum_values_for_every_item():
    """The writer binds these straight to the PG enums; a dataclass instance in a
    row dict would fail at the driver, not here."""
    rows = plan_as_rows(build_plan(inputs()))
    assert len(rows) == build_plan(inputs()).item_count
    for row in rows:
        assert isinstance(row["block"], str)
        assert isinstance(row["kind"], str)
        assert row["kind"] in {"drill", "song_section", "song_play"}
        assert row["block"] in {"warmup", "technique", "repertoire", "consolidation"}
        assert (row["drill_id"] is None) != (row["kind"] == "drill")


def test_every_drill_item_carries_the_skill_node_the_rating_writes_to():
    """The rating write path needs target_skill_node_id per item (Phase 4.1's
    drill_index + target_skill_node_id pair). An item without it cannot be rated."""
    plan = build_plan(inputs())
    for item in plan.items:
        if item.kind is ItemKind.DRILL:
            assert item.target_skill_node_id is not None
            assert item.planned_bpm is not None
            assert item.planned_reps is not None and item.planned_reps >= 1
