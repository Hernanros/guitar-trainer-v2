# server/tests/test_session_taxonomy.py
#
# Pins app/sessions/taxonomy.py to FLE-4 §8, §9 and §11.1. Offline: no DB, no
# network, no model spend. If one of these fails, either the code drifted from the
# spec or the spec changed and GENERATOR_VERSION owes a bump.

import pytest

from app.models.db import PrimarySkillRoot
from app.sessions.taxonomy import (
    FAMILY_SPECS,
    PILOT_BAND_CELLS,
    VIABLE_CELLS,
    SkillRoot,
    TechniqueFamily,
    Tier,
    band,
    compute_tier,
    tier_from_raw,
    in_band,
    stretch_tier,
    tier_features,
    tier_from_mastery,
)


def tab(beats):
    """A tab_snippet from a list of beats, each a list of (string, fret, duration)."""
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


# ---------------------------------------------------------------------------
# The mirrored enum — duplication that must not drift
# ---------------------------------------------------------------------------

def test_skill_root_mirrors_the_orm_enum_exactly():
    """taxonomy.SkillRoot restates db.PrimarySkillRoot so the pure core imports no
    SQLAlchemy. That is only safe while the two agree member-for-member."""
    assert [e.value for e in SkillRoot] == [e.value for e in PrimarySkillRoot]
    assert [e.name for e in SkillRoot] == [e.name for e in PrimarySkillRoot]


# ---------------------------------------------------------------------------
# §8 — the family grid
# ---------------------------------------------------------------------------

def test_all_eighteen_families_are_specified():
    assert len(TechniqueFamily) == 18
    assert set(FAMILY_SPECS) == set(TechniqueFamily)


def test_every_family_hangs_off_a_real_root():
    for family, spec in FAMILY_SPECS.items():
        assert isinstance(spec.root, SkillRoot), family


def test_every_family_tier_range_is_non_empty_and_ordered():
    for family, spec in FAMILY_SPECS.items():
        assert spec.tier_low <= spec.tier_high, family


def test_viable_cell_count_is_sixty():
    """§8's headline number. A typo in the tier ranges silently grades the bank
    against the wrong denominator, which is exactly the kind of error a coverage
    report looks healthy while making."""
    assert VIABLE_CELLS == 60


def test_pilot_band_cell_count_is_forty_five():
    """§10.3 — the hard gate counts D2-D4 cells inside their family's range."""
    assert PILOT_BAND_CELLS == 45


def test_c4_does_not_exist_below_d4():
    """The rule the grid exists to encode: 'extended jazz voicings at D1' isn't a
    beginner drill, it's a mislabelled one."""
    low, high = TechniqueFamily.C4.tier_range
    assert low == Tier.D4 and high == Tier.D5


# ---------------------------------------------------------------------------
# §9 — tiers are ordered
# ---------------------------------------------------------------------------

def test_tiers_compare_by_difficulty_not_by_string():
    assert Tier.D1 < Tier.D2 < Tier.D3 < Tier.D4 < Tier.D5
    assert Tier.D5 > Tier.D1
    assert Tier.D3 <= Tier.D3


def test_shifted_clamps_at_both_ends():
    assert Tier.D1.shifted(-1) == Tier.D1
    assert Tier.D5.shifted(1) == Tier.D5
    assert Tier.D3.shifted(1) == Tier.D4
    assert Tier.D3.shifted(-2) == Tier.D1


@pytest.mark.parametrize(
    "mastery,expected",
    [
        (0.00, Tier.D1), (0.19, Tier.D1),
        (0.20, Tier.D2), (0.39, Tier.D2),
        (0.40, Tier.D3), (0.59, Tier.D3),
        (0.60, Tier.D4), (0.79, Tier.D4),
        (0.80, Tier.D5), (1.00, Tier.D5),
    ],
)
def test_tier_from_mastery_band_boundaries(mastery, expected):
    assert tier_from_mastery(mastery) == expected


# ---------------------------------------------------------------------------
# §9.1 — the rubric
# ---------------------------------------------------------------------------

def test_empty_snippet_floors_the_three_features_that_need_notes():
    """Density, span and simultaneity need notes to read. The bpm columns don't, so
    the ladder stretch still scores — and so does speed load, which assumes quarter
    notes when there is no subdivision to read. A drill whose tab failed to generate
    must not score as trivially easy and outrank real material on every tier
    filter."""
    f = tier_features({"measures": []}, start_bpm=60, target_bpm=120)
    assert (f.density, f.span, f.simultaneity) == (0, 0, 0)
    assert f.ladder_stretch == 4  # (120-60)/5 = 12 rungs, > 8
    assert f.speed_load == 1      # assumed quarters: 1.0 x 120/60 = 2.0 nps


def test_speed_load_reads_the_shortest_subdivision_present():
    """`shortest` is the FASTEST note in the snippet, not the average."""
    mixed = tab([[(1, 5, "whole")], [(1, 5, "sixteenth")]])
    slow = tab([[(1, 5, "whole")], [(1, 5, "whole")]])
    # sixteenths at 120bpm: 4 x 120/60 = 8 nps -> bucket 3. Whole notes: 0.5 -> 0.
    assert tier_features(mixed, start_bpm=60, target_bpm=120).speed_load == 3
    assert tier_features(slow, start_bpm=60, target_bpm=120).speed_load == 0
    # The same snippet one bucket faster, to prove the boundary is inclusive.
    assert tier_features(mixed, start_bpm=60, target_bpm=150).speed_load == 4


def test_span_ignores_open_strings():
    """Fret 0 is not a hand position. Counting it makes every open-chord drill read
    as a 12-fret stretch."""
    with_open = tab([[(6, 0, "quarter"), (1, 3, "quarter"), (2, 5, "quarter")]])
    assert tier_features(with_open, start_bpm=60, target_bpm=60).span == 0  # 5-3 = 2


def test_span_needs_two_fretted_notes():
    single = tab([[(1, 12, "quarter")]])
    assert tier_features(single, start_bpm=60, target_bpm=60).span == 0


def test_simultaneity_is_the_widest_beat():
    chord = tab([[(1, 1, "quarter")], [(s, 5, "quarter") for s in range(1, 7)]])
    assert tier_features(chord, start_bpm=60, target_bpm=60).simultaneity == 4


def test_raw_score_is_the_sum_and_bounded_by_twenty():
    f = tier_features(
        tab([[(s, 12, "sixteenth") for s in range(1, 7)], [(1, 1, "sixteenth")]]),
        start_bpm=60,
        target_bpm=200,
    )
    assert f.raw == f.speed_load + f.density + f.span + f.simultaneity + f.ladder_stretch
    assert 0 <= f.raw <= 20


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0, Tier.D1), (3, Tier.D1),
        (4, Tier.D2), (7, Tier.D2),
        (8, Tier.D3), (11, Tier.D3),
        (12, Tier.D4), (15, Tier.D4),
        (16, Tier.D5), (20, Tier.D5),
    ],
)
def test_raw_to_tier_cutoffs_are_inclusive_upper_bounds(raw, expected):
    """'D1 if raw <= 3 · D2 if <= 7 · D3 if <= 11 · D4 if <= 15 · else D5'. These are
    the numbers most likely to be retuned after the pilot, so they are pinned
    directly rather than inferred through a crafted snippet."""
    assert tier_from_raw(raw) == expected


def test_every_raw_score_maps_to_a_tier():
    assert all(isinstance(tier_from_raw(r), Tier) for r in range(0, 21))


def test_trivial_drill_is_d1_and_a_hard_one_is_d5():
    easy, easy_raw = compute_tier(
        tab([[(1, 5, "quarter")], [(1, 5, "quarter")]]), start_bpm=60, target_bpm=65
    )
    hard, hard_raw = compute_tier(
        tab([[(s, 12, "sixteenth") for s in range(1, 7)]] * 4 + [[(1, 1, "sixteenth")]]),
        start_bpm=60,
        target_bpm=220,
    )
    assert easy == Tier.D1 and easy_raw <= 3
    assert hard == Tier.D5 and hard_raw > 15


def test_family_range_wins_over_the_rubric():
    """§9.1 — 'tier = clamp(tier, family.tier_range); family range wins'. This is the
    v1 mitigation for the rubric being blind to bends and vibrato: an L2 drill that
    scores as trivial is still D2, because L2 does not exist below D2."""
    trivial = tab([[(1, 5, "whole")]])
    unclamped, _ = compute_tier(trivial, start_bpm=60, target_bpm=65)
    clamped, _ = compute_tier(trivial, start_bpm=60, target_bpm=65, family=TechniqueFamily.L2)
    assert unclamped == Tier.D1
    assert clamped == Tier.D2


def test_family_range_clamps_from_above_too():
    brutal = tab([[(s, 12, "sixteenth") for s in range(1, 7)]] * 4)
    clamped, _ = compute_tier(brutal, start_bpm=60, target_bpm=220, family=TechniqueFamily.C1)
    assert clamped == Tier.D2  # C1 tops out at D2


def test_raw_score_is_unaffected_by_the_clamp():
    """tier_raw_score is stored so the rubric can be retuned with one UPDATE. If the
    clamp leaked into it, the stored features would no longer be the raw signal."""
    trivial = tab([[(1, 5, "whole")]])
    _, raw_unclamped = compute_tier(trivial, start_bpm=60, target_bpm=65)
    _, raw_clamped = compute_tier(trivial, start_bpm=60, target_bpm=65, family=TechniqueFamily.L2)
    assert raw_unclamped == raw_clamped


def test_tier_is_deterministic():
    snippet = tab([[(1, 5, "eighth"), (2, 7, "eighth")], [(3, 9, "sixteenth")]])
    first = compute_tier(snippet, start_bpm=70, target_bpm=110)
    for _ in range(5):
        assert compute_tier(snippet, start_bpm=70, target_bpm=110) == first


# ---------------------------------------------------------------------------
# §11.1 — the band
# ---------------------------------------------------------------------------

def test_band_is_one_tier_of_owned_material_under_the_mastery_tier():
    assert band(root_mastery=0.45) == (Tier.D2, Tier.D3)


def test_band_floors_at_d1():
    assert band(root_mastery=0.05) == (Tier.D1, Tier.D1)


def test_band_falls_back_to_player_level_then_to_a_half():
    assert band(player_level=0.85) == (Tier.D4, Tier.D5)
    assert band() == (Tier.D2, Tier.D3)  # 0.5 -> D3


def test_root_mastery_wins_over_player_level():
    assert band(root_mastery=0.10, player_level=0.90) == (Tier.D1, Tier.D1)


def test_stretch_tier_is_one_above_the_band_and_caps_at_d5():
    assert stretch_tier(root_mastery=0.45) == Tier.D4
    assert stretch_tier(root_mastery=0.95) == Tier.D5


def test_the_stretch_tier_is_never_inside_the_band():
    """The whole point of the stretch slot is that it reaches OUTSIDE the band, once,
    at the end. If it were in-band the greedy fill could take it for any slot."""
    for mastery in (0.0, 0.25, 0.5, 0.75, 1.0):
        bounds = band(root_mastery=mastery)
        stretch = stretch_tier(root_mastery=mastery)
        if bounds[1] != Tier.D5:
            assert not in_band(stretch, bounds)
