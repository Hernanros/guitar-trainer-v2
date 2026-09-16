"""Regression tests for difficulty_label() — quick 260916-01.

GET /api/v1/song-of-day returned HTTP 500 for every cold-start user: the seed-song
upsert in selectors/today_song.py bound song_catalog.difficulty (NUMERIC(4,3), decoded
by asyncpg as Decimal) straight into songs.difficulty (String(50)), and asyncpg refused
the implicit conversion:

    asyncpg.exceptions.DataError: invalid input for query argument $4:
      Decimal('0.550') (expected str, got Decimal)

songs.difficulty is a 3-tier label rendered verbatim into the SongOfDayCard badge, so
str(Decimal(...)) would have traded a 500 for "0.550" on the card. These tests pin the
bucketing instead.

Thresholds come from the catalog tagging contract in
0007_song_catalog_tuning_and_expansion.py — anchors 0.20 beginner / 0.50 intermediate /
0.80 advanced, so nearest-anchor midpoints are 0.35 and 0.65.

Pure unit tests — no Postgres required (unlike test_today_song_selector.py).
"""
from decimal import Decimal

import pytest

from app.selectors.today_song import difficulty_label


class TestProductionRegression:
    def test_exact_payload_that_500d_in_production(self):
        """The literal value from the 2026-09-16 Railway traceback."""
        assert difficulty_label(Decimal("0.550")) == "intermediate"

    def test_never_returns_a_stringified_number(self):
        """Guards the SongOfDayCard badge against str(Decimal) creeping back in."""
        for raw in ("0.000", "0.200", "0.550", "0.800", "1.000"):
            assert difficulty_label(Decimal(raw)) in {"beginner", "intermediate", "advanced"}


class TestTaggingContractAnchors:
    @pytest.mark.parametrize(
        "anchor,expected",
        [("0.20", "beginner"), ("0.50", "intermediate"), ("0.80", "advanced")],
    )
    def test_each_anchor_maps_to_its_own_tier(self, anchor, expected):
        assert difficulty_label(Decimal(anchor)) == expected


class TestThresholdBoundaries:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("0.349", "beginner"),
            ("0.350", "intermediate"),  # boundary is exclusive-below
            ("0.649", "intermediate"),
            ("0.650", "advanced"),
        ],
    )
    def test_boundaries_are_half_open(self, value, expected):
        assert difficulty_label(Decimal(value)) == expected

    @pytest.mark.parametrize("value,expected", [("0.000", "beginner"), ("1.000", "advanced")])
    def test_range_endpoints(self, value, expected):
        assert difficulty_label(Decimal(value)) == expected


class TestInputTolerance:
    def test_none_stays_none(self):
        """songs.difficulty is nullable and the card hides an empty badge — do not
        silently coerce a NULL catalog difficulty into 'beginner'."""
        assert difficulty_label(None) is None

    @pytest.mark.parametrize("value", [0.55, "0.55", Decimal("0.55")])
    def test_accepts_float_str_and_decimal(self, value):
        assert difficulty_label(value) == "intermediate"

    def test_int_endpoints(self):
        assert difficulty_label(0) == "beginner"
        assert difficulty_label(1) == "advanced"

    def test_float_goes_through_str_not_binary_expansion(self):
        """Decimal(0.35) would be 0.34999999... and slip into the wrong bucket;
        the implementation routes floats through str() to avoid that."""
        assert difficulty_label(0.35) == "intermediate"
