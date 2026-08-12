"""Rapidfuzz-based dedup for proposed skill_node names (D-09).

No external embeddings — pure fuzzy match.
Thresholds per D-09:
  >= 85: auto-dedupe (reuse existing canonical)
  70-84: curator queue (uncertain similarity)
  < 70:  new proposal (run Sonnet verifier)
"""
import re

from rapidfuzz import fuzz


# D-09 thresholds (token_set_ratio scale 0–100)
SCORE_AUTO_DEDUPE = 85    # >= 85: reuse existing canonical
SCORE_CURATOR_QUEUE = 70  # 70–84: uncertain → curator queue
# < 70: new proposal → run Sonnet verifier


def normalize(name: str) -> str:
    """Lowercase + strip non-alphanumeric non-space chars for token_set_ratio input.

    Removes punctuation (including unicode hyphens/semicolons) so 'Slap-Pop' and
    'Slap Pop' score identically. ASCII space is preserved as word boundary.
    """
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def dedupe_score(proposed: str, existing: str) -> int:
    """Return token_set_ratio score (0–100) between two skill names.

    Handles token reordering ('Blues Shuffle' vs 'Shuffle Blues' = 100) and partial
    containment ('Blues Shuffle Rhythm' vs 'Blues Shuffle' >= 85). Both names are
    normalized before scoring.
    """
    return int(fuzz.token_set_ratio(normalize(proposed), normalize(existing)))


def best_match(proposed: str, candidates: list[str]) -> tuple[str, int] | None:
    """Return (best_candidate, score) or None if candidates is empty.

    Iterates all candidates and returns the highest-scoring (candidate, score) pair.
    If the best score is 0 or candidates is empty, returns None.

    POC cap note: this is O(n) over canonical names; at POC scale (< 1000 nodes)
    this is sub-millisecond. If taxonomy grows to 10k+ nodes, swap for indexed
    embedding search behind the same signature.
    """
    if not candidates:
        return None
    best: tuple[str, int] | None = None
    for candidate in candidates:
        score = dedupe_score(proposed, candidate)
        if best is None or score > best[1]:
            best = (candidate, score)
    return best
