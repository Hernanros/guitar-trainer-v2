"""Unit tests for server/app/ai/skill_dedupe.py.

Pure unit tests — no DB required. Tests validate:
  - normalize() lowercases and strips punctuation
  - dedupe_score() uses token_set_ratio (reorder invariant, near-match, partial, unrelated)
  - best_match() returns highest-scoring candidate or None for empty list

All assertions against threshold constants imported directly from skill_dedupe.
"""
import pytest

from app.ai.skill_dedupe import (
    SCORE_AUTO_DEDUPE,
    SCORE_CURATOR_QUEUE,
    best_match,
    dedupe_score,
    normalize,
)


# ---------------------------------------------------------------------------
# normalize()
# ---------------------------------------------------------------------------

def test_normalize_lowercases_and_strips_punctuation():
    assert normalize("Chord Voicings!") == "chord voicings"


def test_normalize_strips_unicode_punctuation():
    # Hyphens and semicolons are stripped; space between tokens is preserved.
    result = normalize("Slap-Pop; Techniques")
    # After stripping non-alphanum-non-space: 'slappop techniques' (no space after strip of -)
    # The hyphen is adjacent to 'Pop' so 'Slap-Pop' → 'slappop' (no space inserted)
    assert result == "slappop techniques"


# ---------------------------------------------------------------------------
# dedupe_score()
# ---------------------------------------------------------------------------

def test_dedupe_score_exact_match_is_100():
    assert dedupe_score("Blues Shuffle", "Blues Shuffle") == 100


def test_dedupe_score_reorder_is_100_via_token_set():
    # token_set_ratio is reorder-invariant
    assert dedupe_score("Shuffle Blues", "Blues Shuffle") == 100


def test_dedupe_score_near_match_above_85():
    score = dedupe_score("Blues Shuffle Rhythm", "Blues Shuffle")
    assert score >= SCORE_AUTO_DEDUPE, (
        f"Expected dedupe_score >= {SCORE_AUTO_DEDUPE} for near match, got {score}"
    )


def test_dedupe_score_partial_between_70_and_85():
    score = dedupe_score("Fingerstyle Fingerpicking", "Classical Fingerpicking")
    assert SCORE_CURATOR_QUEUE <= score < SCORE_AUTO_DEDUPE, (
        f"Expected score in [{SCORE_CURATOR_QUEUE}, {SCORE_AUTO_DEDUPE}), got {score}"
    )


def test_dedupe_score_unrelated_below_70():
    score = dedupe_score("Sweep Picking", "Tap Harmonics")
    assert score < SCORE_CURATOR_QUEUE, (
        f"Expected dedupe_score < {SCORE_CURATOR_QUEUE} for unrelated names, got {score}"
    )


# ---------------------------------------------------------------------------
# best_match()
# ---------------------------------------------------------------------------

def test_best_match_returns_highest_scoring_candidate():
    candidates = ["Rock", "Blues Shuffle", "Jazz"]
    result = best_match("Blues", candidates)
    assert result is not None
    best_candidate, best_score = result
    # "Blues Shuffle" should score highest against "Blues"
    assert best_candidate == "Blues Shuffle", (
        f"Expected 'Blues Shuffle' as best candidate, got '{best_candidate}'"
    )
    # Score should be > 0
    assert best_score > 0


def test_best_match_returns_none_when_candidates_empty():
    assert best_match("X", []) is None


def test_best_match_returns_none_or_candidate_for_single():
    # Single candidate — should return (candidate, score)
    result = best_match("Blues Shuffle", ["Blues Shuffle"])
    assert result is not None
    assert result[0] == "Blues Shuffle"
    assert result[1] == 100


# ---------------------------------------------------------------------------
# Threshold constants sanity
# ---------------------------------------------------------------------------

def test_threshold_constants_values():
    assert SCORE_AUTO_DEDUPE == 85
    assert SCORE_CURATOR_QUEUE == 70
    assert SCORE_CURATOR_QUEUE < SCORE_AUTO_DEDUPE
