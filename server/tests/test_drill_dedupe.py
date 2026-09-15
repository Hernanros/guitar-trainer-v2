"""Unit tests for app.ai.drill_dedupe (FLE-8 Task 3).

Pure functions — no DB, no network, no LLM. That the write path is LLM-free is a
FLE-8 constraint, and these tests run without any client configured, which is the
cheapest proof of it.

Covers:
  - the three D-09 bands (>= 85 reuse / 70-84 queue / < 70 insert)
  - thresholds are INHERITED from skill_dedupe, not re-declared
  - dedup is scoped to (user, skill) — cross-skill collisions are not dups
  - collapsed duplicates are never used as scoring candidates
  - normalization (punctuation, case, token order)
"""
import uuid

import pytest

from app.ai import drill_dedupe
from app.ai.drill_dedupe import (
    DedupeAction,
    DrillCandidate,
    build_candidates,
    classify,
)
from app.ai.skill_dedupe import SCORE_AUTO_DEDUPE, SCORE_CURATOR_QUEUE


SKILL_A = uuid.uuid4()
SKILL_B = uuid.uuid4()


class _FakeDrill:
    """Duck-typed stand-in for db.Drill — build_candidates takes `object`."""

    def __init__(self, name, skill_node_id, canonical_drill_id=None, drill_id=None):
        self.id = drill_id or uuid.uuid4()
        self.name = name
        self.skill_node_id = skill_node_id
        self.canonical_drill_id = canonical_drill_id


def _cand(name, skill_node_id=SKILL_A, drill_id=None):
    return DrillCandidate(
        drill_id=drill_id or uuid.uuid4(), name=name, skill_node_id=skill_node_id
    )


# ---------------------------------------------------------------------------
# 1. Thresholds are inherited, not redefined
# ---------------------------------------------------------------------------

def test_thresholds_are_imported_from_skill_dedupe():
    """FLE-8 says extend the existing machinery. If skill thresholds move, drills move."""
    from app.ai import skill_dedupe

    assert drill_dedupe.SCORE_AUTO_DEDUPE is skill_dedupe.SCORE_AUTO_DEDUPE
    assert drill_dedupe.SCORE_CURATOR_QUEUE is skill_dedupe.SCORE_CURATOR_QUEUE
    assert drill_dedupe.normalize is skill_dedupe.normalize


# ---------------------------------------------------------------------------
# 2. The three bands
# ---------------------------------------------------------------------------

def test_no_candidates_inserts_as_first_canonical():
    """First drill ever banked for a (user, skill) has nothing to collapse against."""
    d = classify("Isolate the b3 to 3 slide", [])
    assert d.action is DedupeAction.INSERT
    assert d.score is None
    assert d.matched_drill_id is None
    assert d.should_insert


def test_identical_name_reuses_canonical():
    """Exact repeat — the 60-songs-x-3-drills case FLE-8 describes."""
    existing = _cand("Isolate the b3 to 3 slide")
    d = classify("Isolate the b3 to 3 slide", [existing])
    assert d.action is DedupeAction.REUSE
    assert d.score == 100
    assert d.matched_drill_id == existing.drill_id


def test_token_reordering_still_reuses():
    """token_set_ratio is order-insensitive — 'Slide Isolation' vs 'Isolation Slide'."""
    existing = _cand("Barre Chord Grip")
    d = classify("Grip Chord Barre", [existing])
    assert d.score >= SCORE_AUTO_DEDUPE
    assert d.action is DedupeAction.REUSE


def test_unrelated_name_inserts_new_canonical():
    """Genuinely different drill — must not collapse."""
    existing = _cand("Isolate the b3 to 3 slide")
    d = classify("Thumb and fingers independence", [existing])
    assert d.score < SCORE_CURATOR_QUEUE
    assert d.action is DedupeAction.INSERT


def test_best_match_wins_over_other_candidates():
    """Scored against ALL candidates; the highest scorer is the one returned."""
    near = _cand("Isolate the b3 to 3 slide")
    far = _cand("Thumb and fingers independence")
    d = classify("Isolate the b3 to 3 slide", [far, near])
    assert d.matched_drill_id == near.drill_id
    assert d.action is DedupeAction.REUSE


@pytest.mark.parametrize(
    "score,expected",
    [
        (100, DedupeAction.REUSE),
        (SCORE_AUTO_DEDUPE, DedupeAction.REUSE),        # 85 — inclusive lower bound
        (SCORE_AUTO_DEDUPE - 1, DedupeAction.QUEUE),    # 84 — top of queue band
        (SCORE_CURATOR_QUEUE, DedupeAction.QUEUE),      # 70 — inclusive lower bound
        (SCORE_CURATOR_QUEUE - 1, DedupeAction.INSERT), # 69 — below queue band
        (0, DedupeAction.INSERT),
    ],
)
def test_band_boundaries_are_inclusive_at_the_bottom(monkeypatch, score, expected):
    """Pin the exact band edges: >= 85 reuse, >= 70 queue, else insert.

    Scores are forced rather than reverse-engineered from strings, so this test
    asserts the banding logic and not rapidfuzz's behaviour.
    """
    monkeypatch.setattr(
        drill_dedupe, "best_match", lambda proposed, candidates: (candidates[0], score)
    )
    d = classify("anything", [_cand("Existing Drill")])
    assert d.action is expected
    assert d.score == score


# ---------------------------------------------------------------------------
# 3. Scoping — the failure mode this design exists to avoid
# ---------------------------------------------------------------------------

def test_build_candidates_excludes_other_skill_nodes():
    """Same name on a DIFFERENT skill is a different exercise, not a duplicate."""
    rows = [
        _FakeDrill("Isolate the slide", SKILL_A),
        _FakeDrill("Isolate the slide", SKILL_B),
    ]
    cands = build_candidates(rows, SKILL_A)
    assert len(cands) == 1
    assert cands[0].skill_node_id == SKILL_A


def test_build_candidates_excludes_collapsed_duplicates():
    """Never score against a dup row — a dup chain would drift from its canonical."""
    canonical = _FakeDrill("Isolate the slide", SKILL_A)
    dup = _FakeDrill("Isolate the slide", SKILL_A, canonical_drill_id=canonical.id)
    cands = build_candidates([canonical, dup], SKILL_A)
    assert [c.drill_id for c in cands] == [canonical.id]


def test_scoped_candidates_make_cross_skill_name_collision_insert():
    """End-to-end of the scoping rule: identical name, other skill -> INSERT."""
    rows = [_FakeDrill("Isolate the slide", SKILL_B)]
    cands = build_candidates(rows, SKILL_A)
    d = classify("Isolate the slide", cands)
    assert d.action is DedupeAction.INSERT
    assert d.matched_drill_id is None


# ---------------------------------------------------------------------------
# 4. Normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Isolate the b3→3 slide", "isolate the b33 slide"),  # unicode arrow stripped
        ("Barre-Chord Grip", "barrechord grip"),
        ("  Thumb & Fingers  ", "thumb  fingers"),
    ],
)
def test_name_normalized_is_reported_for_the_db_backstop(raw, expected):
    """The returned name_normalized is what the write path stores in
    drills.name_normalized, which backs uq_drills_canonical_identity. If this
    drifts from skill_dedupe.normalize, the DB backstop stops matching the
    application-layer decision."""
    d = classify(raw, [])
    assert d.name_normalized == expected


def test_punctuation_variants_collapse():
    """'Barre-Chord Grip' and 'Barre Chord Grip' must not both become canonical."""
    existing = _cand("Barre Chord Grip")
    d = classify("Barre-Chord Grip", [existing])
    assert d.action is DedupeAction.REUSE
