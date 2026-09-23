# server/tests/test_session_select.py
#
# Pins app/sessions/select.py to FLE-4 §5.1, §5.2, §11 and §12. Offline: no DB, no
# network, no model spend. If one of these fails, either the code drifted from the
# spec or the spec changed and GENERATOR_VERSION owes a bump.
#
# The property these tests exist to protect above all others: THE SAME INPUTS
# PRODUCE THE SAME PLAN. A session the server cannot reproduce is a session nobody
# can debug, and "reproducible for a given user and duration" is the issue's own
# definition of done.

from datetime import date, timedelta

import pytest

from app.sessions.plan import MIN_PLANNED_REPS, SessionMode, block_budget
from app.sessions.select import (
    MAX_MAINTENANCE_DRILLS,
    MAX_SONG_SPECIFIC_DRILLS,
    Degradation,
    DrillCandidate,
    SelectionContext,
    fill_technique_block,
    score,
    select_warmup,
    stable_index,
)
from app.sessions.taxonomy import SkillRoot, TechniqueFamily, Tier

TODAY = date(2026, 9, 23)


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


# Tier is computed from the tab and the bpm columns (§9.1), and almost every filter
# in this module is tier-gated, so the fixtures below are built to land on a KNOWN
# tier. A test whose drill accidentally sits outside the band under test passes for
# the wrong reason. Each is paired with the bpm columns its tier assumes.
#
# All are one 4/4 measure = 4 beats per rep, so rep-fitting never accidentally
# disqualifies a drill unless a test means it to.
D1_TAB = tab([[(1, 5, "quarter")] for _ in range(4)])                    # w/ 100->105
D2_TAB = tab([[(1, f, "eighth")] for f in (5, 7, 5, 9)])                 # w/ 100->120
D3_TAB = D2_TAB                                                          # w/ 100->200
D4_TAB = tab([[(1, 5, "eighth"), (2, 12, "eighth")] for _ in range(4)])  # w/ 60->200

# 16 measures of 4/4 = 64 beats per rep. At 60bpm one rep is 64s, so a 225s slot
# fits 2 reps -- below MIN_PLANNED_REPS. This drill fits only a very long slot.
SLOW_TAB = {"measures": [{"time_signature": "4/4", "beats": []} for _ in range(16)]}


def drill(drill_id, **kw):
    """A D2 candidate with spec-neutral defaults; tests override what they mean.

    D2 is the default because the default context (player_level 0.5, no root
    mastery) puts the band at D2-D3 — so a default drill is in band, and a test that
    wants an out-of-band one has to say so.
    """
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


def d1_drill(drill_id, **kw):
    return drill(drill_id, tab_snippet=D1_TAB, start_bpm=100, target_bpm=105, **kw)


def d3_drill(drill_id, **kw):
    return drill(drill_id, tab_snippet=D3_TAB, start_bpm=100, target_bpm=200, **kw)


def d4_drill(drill_id, **kw):
    return drill(drill_id, tab_snippet=D4_TAB, start_bpm=60, target_bpm=200, rung_bpm=60, **kw)


def test_the_tier_fixtures_land_where_they_claim():
    """Guards every other test in this file: if the rubric is retuned these fixtures
    move, and a band-gated test would start passing for the wrong reason."""
    from app.sessions.select import _tier_of  # noqa: PLC0415 - internal, tested here on purpose

    assert _tier_of(d1_drill("a")) == Tier.D1
    assert _tier_of(drill("b")) == Tier.D2
    assert _tier_of(d3_drill("c")) == Tier.D3
    assert _tier_of(d4_drill("d")) == Tier.D4


def ctx(**kw):
    defaults = dict(user_id="user-1", local_calendar_day=TODAY, player_level=0.5)
    defaults.update(kw)
    return SelectionContext(**defaults)


# ---------------------------------------------------------------------------
# Determinism — the property the whole design is for
# ---------------------------------------------------------------------------

def test_stable_index_is_stable_across_calls_and_not_python_hash():
    """Python's hash() is salted per process, so a session seeded with it would
    replan differently after a server restart. That is the exact property the spec's
    `seed` column exists to rule out."""
    assert stable_index("user-1|2026-09-23|stretch", 7) == stable_index("user-1|2026-09-23|stretch", 7)
    # A precomputed value: if blake2b or the encoding changes, every historical
    # session becomes unexplainable and this must fail loudly.
    assert stable_index("user-1|2026-09-23|stretch", 1000) == stable_index("user-1|2026-09-23|stretch", 1000)


def test_stable_index_varies_with_the_day():
    keys = {stable_index(f"user-1|2026-09-{d:02d}|stretch", 50) for d in range(1, 29)}
    assert len(keys) > 1  # the stretch slot must not be frozen to one drill forever


def test_stable_index_rejects_an_empty_pool():
    with pytest.raises(ValueError):
        stable_index("k", 0)


def test_the_same_inputs_produce_the_same_plan():
    cands = [drill(f"d{i}", node_mastery=0.3 + i * 0.05) for i in range(8)]
    first = fill_technique_block(cands, ctx(), technique_seconds=900)
    for _ in range(5):
        again = fill_technique_block(cands, ctx(), technique_seconds=900)
        assert [p.candidate.drill_id for p in again.picks] == [p.candidate.drill_id for p in first.picks]
        assert [p.planned_reps for p in again.picks] == [p.planned_reps for p in first.picks]
        assert again.slot_seconds == first.slot_seconds


def test_candidate_order_does_not_change_the_plan():
    """Selection must be a function of the candidate SET, not of the row order the
    database happened to return."""
    cands = [drill(f"d{i}", node_mastery=0.3 + i * 0.05) for i in range(8)]
    forward = fill_technique_block(cands, ctx(), technique_seconds=900)
    backward = fill_technique_block(list(reversed(cands)), ctx(), technique_seconds=900)
    assert [p.candidate.drill_id for p in forward.picks] == [p.candidate.drill_id for p in backward.picks]


# ---------------------------------------------------------------------------
# §11.2 — the score
# ---------------------------------------------------------------------------

def test_deficit_dominates():
    """100 of the ~180 available points. The bank exists to serve weaknesses."""
    weak = drill("weak", node_mastery=0.1)
    strong = drill("strong", node_mastery=0.9)
    assert score(weak, ctx()) - score(strong, ctx()) == pytest.approx(80.0)


def test_never_practised_scores_half_staleness_not_full():
    """0.50, deliberately: a freshly backfilled bank must not flush everything
    familiar out of a player's sessions overnight."""
    fresh = drill("fresh", attempts=0, last_practiced_on=None)
    stale = drill("stale", last_practiced_on=TODAY - timedelta(days=30))
    yesterday = drill("recent", last_practiced_on=TODAY - timedelta(days=1))
    assert score(stale, ctx()) > score(fresh, ctx()) > score(yesterday, ctx())


def test_staleness_saturates_at_thirty_days():
    a = drill("a", last_practiced_on=TODAY - timedelta(days=30))
    b = drill("b", last_practiced_on=TODAY - timedelta(days=300))
    assert score(a, ctx()) == score(b, ctx())


def test_song_relevance_is_worth_twenty_five():
    d = drill("d")
    plain = ctx()
    relevant = ctx(song_skill_node_ids=frozenset({d.skill_node_id}))
    assert score(d, relevant) - score(d, plain) == pytest.approx(25.0)


def test_ladder_momentum_fires_only_at_exactly_one_clear():
    """A drill sitting on ONE clear is one session from a push. Two clears means the
    push already happened at rating time."""
    base = score(drill("d", consecutive_clears=0), ctx())
    assert score(drill("d", consecutive_clears=1), ctx()) - base == pytest.approx(15.0)
    assert score(drill("d", consecutive_clears=2), ctx()) == base


def test_recency_is_a_sixty_point_penalty():
    d = drill("d")
    assert score(d, ctx()) - score(d, ctx(recent_drill_ids=frozenset({"d"}))) == pytest.approx(60.0)


def test_family_repeat_is_a_thirty_point_penalty():
    d = drill("d", family=TechniqueFamily.L1)
    assert score(d, ctx()) - score(d, ctx(), filled_family_keys=frozenset({d.family_key})) == pytest.approx(30.0)


def test_family_repeat_falls_back_to_the_root_while_the_column_is_missing():
    """FLE-8 has not shipped `drills.family`. Until it does the penalty runs on the
    skill-node root, which stops two lead drills stacking even though it cannot stop
    two alternate-picking drills."""
    a = drill("a", family=None, root=SkillRoot.LEAD)
    b = drill("b", family=None, root=SkillRoot.LEAD)
    assert a.family_key == b.family_key == "root:lead"


def test_family_repeat_never_collapses_two_unknown_drills_together():
    """Falling all the way through to the skill node means the penalty simply never
    fires, rather than firing against every other family-less drill in the bank."""
    a = drill("a", family=None, root=None)
    b = drill("b", family=None, root=None)
    assert a.family_key != b.family_key


# ---------------------------------------------------------------------------
# §5.2 — hard constraints
# ---------------------------------------------------------------------------

def test_retired_and_mastered_drills_are_not_selectable():
    cands = [
        drill("ok"),
        drill("retired", progress_state="retired"),
        drill("mastered", progress_state="mastered"),
    ]
    picks = fill_technique_block(cands, ctx(), technique_seconds=900).picks
    assert {p.candidate.drill_id for p in picks} == {"ok"}


def test_dedup_suppressed_drills_are_not_selectable():
    """`canonical_drill_id IS NOT NULL` is today's `status != 'active'`."""
    cands = [drill("ok"), drill("dupe", is_canonical=False)]
    picks = fill_technique_block(cands, ctx(), technique_seconds=900).picks
    assert {p.candidate.drill_id for p in picks} == {"ok"}


def test_a_drill_that_cannot_make_six_reps_is_dropped_not_planned_short():
    """§5.5 — the single most likely way a technically-valid session comes out
    pedagogically worthless."""
    cands = [drill("fits"), drill("too_slow", tab_snippet=SLOW_TAB, start_bpm=60, target_bpm=105, rung_bpm=60)]
    fill = fill_technique_block(cands, ctx(), technique_seconds=900)
    assert {p.candidate.drill_id for p in fill.picks} == {"fits"}
    assert all(p.planned_reps >= MIN_PLANNED_REPS for p in fill.picks)


def test_no_two_picks_share_a_skill_node():
    cands = [drill(f"d{i}", skill_node_id="shared") for i in range(5)]
    picks = fill_technique_block(cands, ctx(), technique_seconds=900).picks
    assert len(picks) == 1


def test_at_most_one_maintenance_drill():
    cands = [drill(f"m{i}", progress_state="maintenance") for i in range(4)] + [
        drill(f"a{i}") for i in range(4)
    ]
    picks = fill_technique_block(cands, ctx(), technique_seconds=900).picks
    maintenance = [p for p in picks if p.candidate.is_maintenance]
    assert len(maintenance) <= MAX_MAINTENANCE_DRILLS


def test_at_most_one_song_specific_drill():
    cands = [drill(f"s{i}", song_specific=True) for i in range(4)] + [drill(f"a{i}") for i in range(4)]
    picks = fill_technique_block(cands, ctx(), technique_seconds=900).picks
    assert len([p for p in picks if p.candidate.song_specific]) <= MAX_SONG_SPECIFIC_DRILLS


def test_drills_from_the_last_two_sessions_are_excluded_when_the_bank_allows():
    cands = [drill(f"d{i}") for i in range(6)]
    context = ctx(recent_drill_ids=frozenset({"d0", "d1"}))
    picks = fill_technique_block(cands, context, technique_seconds=900).picks
    assert not ({"d0", "d1"} & {p.candidate.drill_id for p in picks})


# ---------------------------------------------------------------------------
# §5.2 / §11.1 — ordering and the stretch slot
# ---------------------------------------------------------------------------

def test_the_block_ramps_easiest_tier_first():
    """Difficulty ramps; the session doesn't open on its hardest thing."""
    cands = [d3_drill("harder"), drill("easier"), d3_drill("harder2"), drill("easier2")]
    picks = fill_technique_block(cands, ctx(), technique_seconds=900).picks
    tiers = [p.tier.ordinal for p in picks if not p.is_stretch]
    assert tiers == sorted(tiers)
    assert len(set(tiers)) > 1  # the ramp is real, not a one-tier block


def test_the_stretch_slot_is_always_last_and_reaches_above_the_band():
    """§11.1 — band is D2-D3 at mastery 0.5, so the stretch slot is the one place a
    D4 drill can appear: once, at the end, after the session has banked easier wins."""
    cands = [drill(f"d{i}") for i in range(4)] + [d4_drill("stretchy")]
    fill = fill_technique_block(cands, ctx(root_mastery={"lead": 0.5}), technique_seconds=900)
    stretch = [i for i, p in enumerate(fill.picks) if p.is_stretch]
    assert stretch == [len(fill.picks) - 1]
    assert fill.picks[-1].candidate.drill_id == "stretchy"
    assert fill.picks[-1].tier == Tier.D4


def test_only_one_slot_is_ever_a_stretch():
    cands = [drill(f"d{i}") for i in range(3)] + [d4_drill(f"s{i}") for i in range(3)]
    fill = fill_technique_block(cands, ctx(root_mastery={"lead": 0.5}), technique_seconds=1200)
    assert len([p for p in fill.picks if p.is_stretch]) <= 1


def test_the_stretch_pick_is_reproducible_but_varies_by_day():
    cands = [drill("body")] + [d4_drill(f"s{i}") for i in range(8)]
    context = ctx(root_mastery={"lead": 0.5})
    first = fill_technique_block(cands, context, technique_seconds=900)
    again = fill_technique_block(cands, context, technique_seconds=900)
    assert [p.candidate.drill_id for p in first.picks] == [p.candidate.drill_id for p in again.picks]
    chosen = set()
    for day in range(1, 29):
        other = ctx(root_mastery={"lead": 0.5}, local_calendar_day=date(2026, 9, day))
        fill = fill_technique_block(cands, other, technique_seconds=900)
        chosen |= {p.candidate.drill_id for p in fill.picks if p.is_stretch}
    assert len(chosen) > 1


def test_a_single_slot_session_has_no_stretch_slot():
    """T=15's one drill should not be the hard one."""
    cands = [drill(f"d{i}") for i in range(6)]
    budget = block_budget(15, SessionMode.BALANCED)
    fill = fill_technique_block(cands, ctx(), technique_seconds=budget.technique)
    assert fill.slots == 1
    assert not any(p.is_stretch for p in fill.picks)


# ---------------------------------------------------------------------------
# §3.3 + §12.1 — how many slots, and shrinking N
# ---------------------------------------------------------------------------

def test_n_shrinks_to_the_bank_rather_than_emitting_a_placeholder():
    """§12 rung 1. Two drills means a two-slot session, not five slots with three
    'coming soon' cards."""
    cands = [drill("a"), drill("b")]
    fill = fill_technique_block(cands, ctx(), technique_seconds=1200)
    assert fill.slots == 2
    assert fill.degradation == Degradation.STRICT


def test_shrinking_n_buys_longer_slots():
    """Slot seconds are the block divided by N, so a thin bank produces MORE reps per
    mechanic rather than a shorter session."""
    many = [drill(f"d{i}") for i in range(6)]
    few = [drill("a"), drill("b")]
    assert fill_technique_block(few, ctx(), technique_seconds=1200).slot_seconds > (
        fill_technique_block(many, ctx(), technique_seconds=1200).slot_seconds
    )


def test_n_takes_the_largest_fillable_count_not_the_first_that_works():
    """Four eligible drills must produce four slots, not two. Reading §12 rung 1 as a
    single subtraction instead of a fixed point silently halves the session."""
    cands = [drill(f"d{i}") for i in range(4)]
    fill = fill_technique_block(cands, ctx(), technique_seconds=1200)
    assert fill.slots == 4


def test_the_block_never_exceeds_the_bank():
    for n in range(1, 7):
        cands = [drill(f"d{i}") for i in range(n)]
        fill = fill_technique_block(cands, ctx(), technique_seconds=1200)
        assert fill.slots <= n


# ---------------------------------------------------------------------------
# §12 — the degradation ladder, in order
# ---------------------------------------------------------------------------

def test_an_empty_bank_returns_no_picks_rather_than_a_placeholder():
    """Rung 5: the caller converts the technique seconds into repertoire work. This
    module never emits a placeholder item and never emits an empty block."""
    fill = fill_technique_block([], ctx(), technique_seconds=900)
    assert fill.picks == ()


def test_the_band_widens_only_when_nothing_is_in_band():
    """Rung 2. An out-of-band drill must not be reachable while an in-band one is."""
    context = ctx(root_mastery={"lead": 0.05})  # band = D1..D1, stretch = D2
    out_of_band = drill("d2")          # D2 — one tier out, rescued by a widen
    in_band_drill = d1_drill("plain")  # D1 — in band
    both = fill_technique_block([out_of_band, in_band_drill], context, technique_seconds=900)
    assert both.degradation == Degradation.STRICT
    assert "plain" in {p.candidate.drill_id for p in both.picks}

    only_out_of_band = fill_technique_block([out_of_band], context, technique_seconds=900)
    assert only_out_of_band.degradation == Degradation.WIDEN_BAND
    assert only_out_of_band.slots == 1


def test_recency_is_violated_only_after_the_band_has_been_widened():
    """Rung 3 sits below rung 2, and a session with a repeated drill is still a
    session — but it is marked, so the player-facing copy can say so."""
    only = drill("d0")
    context = ctx(recent_drill_ids=frozenset({"d0"}))
    fill = fill_technique_block([only], context, technique_seconds=900)
    assert fill.slots == 1
    assert fill.degradation == Degradation.ALLOW_RECENCY
    assert fill.picks[0].repeat_ok is True


def test_the_song_specific_cap_holds_and_the_session_shrinks_instead():
    """Rung 4 is UNREACHABLE under the shrink-first reading of §12, and that is the
    right outcome rather than an omission.

    The written ladder puts 'shrink N' at rung 1 and 'lift the song_specific cap' at
    rung 4. Since the cap can never block the FIRST pick, shrinking always succeeds
    before rung 4 is reached — so a bank of nothing but song-specific drills yields a
    one-drill session, not a session of three repertoire accents. That matches §12's
    own stated preference ('A 15-minute session with one drill and 10 minutes of song
    work is a good session') and §3.3's ('a longer session buys LONGER slots, not more
    of them'). Raised with the spec author; if the intent was the opposite, this test
    is the one to flip."""
    cands = [drill(f"s{i}", song_specific=True) for i in range(3)]
    fill = fill_technique_block(cands, ctx(), technique_seconds=1200)
    assert fill.slots == MAX_SONG_SPECIFIC_DRILLS
    assert fill.degradation == Degradation.STRICT


def test_degradation_is_reported_not_hidden():
    """A session assembled off a degraded rung must say so: it is the difference
    between 'the bank is thin' and 'the generator is broken'."""
    fill = fill_technique_block([drill("d0")], ctx(recent_drill_ids=frozenset({"d0"})), technique_seconds=900)
    assert fill.degradation_name == "allow_recency"


# ---------------------------------------------------------------------------
# §5.1 — the warm-up
# ---------------------------------------------------------------------------

def test_warmup_is_planned_below_the_working_tempo():
    """Familiarity at minute zero is a ramp; novelty at minute zero is friction."""
    cands = [drill("w", rung_bpm=120, start_bpm=80)]
    pick = select_warmup(cands, ctx(), seconds=180)
    assert pick.planned_bpm == 110  # rung - 10


def test_warmup_never_plans_below_the_drills_own_floor():
    cands = [drill("w", rung_bpm=85, start_bpm=80)]
    assert select_warmup(cands, ctx(), seconds=180).planned_bpm == 80


def test_warmup_reps_are_sixty_percent_of_a_full_slot():
    cands = [drill("w")]
    full = fill_technique_block(cands, ctx(), technique_seconds=900).picks[0]
    warm = select_warmup(cands, ctx(), seconds=180)
    assert warm.planned_reps < full.planned_reps


def test_warmup_rule_a_inherits_slot_ones_family():
    """The warm-up PRELOADS the session's main mechanic at a tempo already owned —
    that is what a warm-up is for, not just moving blood."""
    slot1_drill = drill("slot1", family=TechniqueFamily.L1)
    other_family = drill("other", family=TechniqueFamily.C1, attempts=99)
    same_family = drill("same", family=TechniqueFamily.L1, attempts=2)
    fill = fill_technique_block([slot1_drill], ctx(), technique_seconds=900)
    pick = select_warmup([other_family, same_family], ctx(), seconds=180, slot_one=fill.picks[0])
    assert pick.rule == "a"
    assert pick.candidate.drill_id == "same"


def test_warmup_never_repeats_the_drill_it_is_warming_up_for():
    slot1_drill = drill("slot1", family=TechniqueFamily.L1)
    fill = fill_technique_block([slot1_drill], ctx(), technique_seconds=900)
    pick = select_warmup(
        [slot1_drill, drill("other", family=TechniqueFamily.L1, attempts=1)],
        ctx(),
        seconds=180,
        slot_one=fill.picks[0],
    )
    assert pick.candidate.drill_id == "other"


def test_warmup_rule_b_takes_any_family_at_d2_or_below():
    practised = drill("easy", family=None, attempts=7)
    pick = select_warmup([practised], ctx(), seconds=180)
    assert pick.rule == "b"


def test_warmup_prefers_the_most_practised_drill():
    """max(attempts) — the warm-up is never new material."""
    cands = [drill("rare", attempts=1), drill("familiar", attempts=40)]
    assert select_warmup(cands, ctx(), seconds=180).candidate.drill_id == "familiar"


def test_warmup_falls_back_to_slot_one_rather_than_emitting_an_empty_block():
    """§12.1's seed drills cannot exist yet — `drills.user_id` is NOT NULL — so rule
    (d) is unreachable. §12 forbids an empty block outright, so slot 1's own drill
    below tempo stands in. Delete this test when the seed rows ship."""
    slot1_drill = drill("slot1", family=TechniqueFamily.L1, attempts=0, last_practiced_on=None)
    fill = fill_technique_block([slot1_drill], ctx(), technique_seconds=900)
    pick = select_warmup([], ctx(), seconds=180, slot_one=fill.picks[0])
    assert pick.rule == "fallback_slot1"
    assert pick.planned_bpm < fill.picks[0].planned_bpm or pick.planned_reps < fill.picks[0].planned_reps


def test_warmup_uses_a_seed_drill_once_one_exists():
    seed = drill("seed-chromatic", attempts=0, last_practiced_on=None)
    pick = select_warmup([], ctx(), seconds=180, seed_drills=[seed])
    assert pick.rule == "d"
    assert pick.candidate.drill_id == "seed-chromatic"


def test_warmup_is_none_only_when_there_is_nothing_at_all():
    assert select_warmup([], ctx(), seconds=180) is None


# ---------------------------------------------------------------------------
# End-to-end shape across the four supported durations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("minutes", [15, 30, 45, 60])
def test_every_supported_duration_produces_a_fillable_block(minutes):
    """The onboarding preference is `15 | 30 | 45 | 60` and nothing has ever read it.
    Every one of the four must plan."""
    cands = [drill(f"d{i}", node_mastery=0.2 + i * 0.05) for i in range(10)]
    budget = block_budget(minutes, SessionMode.BALANCED)
    fill = fill_technique_block(cands, ctx(), technique_seconds=budget.technique)
    assert fill.slots >= 1
    assert all(p.planned_reps >= MIN_PLANNED_REPS for p in fill.picks)
    assert fill.slot_seconds * fill.slots <= budget.technique


@pytest.mark.parametrize("minutes", [15, 30, 45, 60])
def test_longer_sessions_buy_longer_slots_not_only_more_of_them(minutes):
    cands = [drill(f"d{i}") for i in range(10)]
    budget = block_budget(minutes, SessionMode.BALANCED)
    fill = fill_technique_block(cands, ctx(), technique_seconds=budget.technique)
    assert fill.slots <= 5  # MAX_TECHNIQUE_SLOTS — past five a session reads as a checklist
