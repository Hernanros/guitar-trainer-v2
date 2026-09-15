"""Dedup-on-write for banked drills (FLE-8 Task 3).

Extends the Phase 4 skill machinery rather than building a parallel mechanism:
`normalize`, `dedupe_score`, `best_match` and the D-09 thresholds are IMPORTED
from `skill_dedupe`, not re-declared. If those thresholds move, drills move with
them.

Two deliberate differences from the skill pipeline, both required by FLE-8:

1. **No LLM in the write path.** The skill pipeline runs a Sonnet verifier for
   the sub-70 band. Drills do not. `classify` is a pure function — no I/O, no
   network, no DB — and the 70-84 band writes a `drill_dedupe_queue` row instead
   of calling a model. The curator queue IS the async escape hatch; that is what
   it already exists to be.

2. **Dedup is scoped to (user_id, skill_node_id).** Two drills with similar
   names targeting different skills are not duplicates: "Isolate the slide" on
   *legato* and "Isolate the slide" on *vibrato* are different exercises. Callers
   MUST pass candidates already filtered to one skill node — `build_candidates`
   exists so that filtering is not hand-rolled at each call site.

Banding (D-09, unchanged):
    >= 85  REUSE   reuse the existing canonical drill, write no new row
    70-84  QUEUE   write a drill_dedupe_queue row, write no drill row
    < 70   INSERT  insert a new canonical drill
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from typing import Iterable, Optional

from app.ai.skill_dedupe import (
    SCORE_AUTO_DEDUPE,
    SCORE_CURATOR_QUEUE,
    best_match,
    dedupe_score,
    normalize,
)

__all__ = [
    "DedupeAction",
    "DedupeDecision",
    "DrillCandidate",
    "build_candidates",
    "classify",
    "normalize",
]


class DedupeAction(str, enum.Enum):
    """What the write path should do with a proposed drill."""

    REUSE = "reuse"    # >= 85 — hand back the existing canonical
    QUEUE = "queue"    # 70-84 — curator queue, no drill row
    INSERT = "insert"  # < 70  — new canonical


@dataclass(frozen=True)
class DrillCandidate:
    """An existing canonical drill a proposal is scored against.

    Only the fields dedup needs. Build these from `db.Drill` rows via
    `build_candidates` so the (user, skill) scoping is applied in one place.
    """

    drill_id: uuid.UUID
    name: str
    skill_node_id: uuid.UUID


@dataclass(frozen=True)
class DedupeDecision:
    """Result of classifying one proposed drill.

    `matched_drill_id` and `score` are None only when there were no candidates
    at all — i.e. the first drill ever banked for this (user, skill).
    """

    action: DedupeAction
    score: Optional[int]
    matched_drill_id: Optional[uuid.UUID]
    name_normalized: str

    @property
    def should_insert(self) -> bool:
        return self.action is DedupeAction.INSERT


def build_candidates(
    drills: Iterable[object],
    skill_node_id: uuid.UUID,
) -> list[DrillCandidate]:
    """Filter `db.Drill` rows down to the canonical ones for a single skill node.

    Applies both scoping rules that dedup correctness depends on:
      - `skill_node_id` must match — cross-skill name collisions are not dups
      - `canonical_drill_id is None` — never score against a collapsed duplicate,
        which would let a dup chain drift away from its canonical

    Takes `object` rather than `db.Drill` to keep this module free of an ORM
    import (it stays a pure, unit-testable function); duck-typing on the four
    attributes is sufficient and lets tests pass simple stand-ins.
    """
    out: list[DrillCandidate] = []
    for d in drills:
        if getattr(d, "canonical_drill_id", None) is not None:
            continue
        if getattr(d, "skill_node_id", None) != skill_node_id:
            continue
        out.append(
            DrillCandidate(
                drill_id=d.id,
                name=d.name,
                skill_node_id=d.skill_node_id,
            )
        )
    return out


def classify(
    proposed_name: str,
    candidates: list[DrillCandidate],
) -> DedupeDecision:
    """Classify a proposed drill name against existing canonical drills.

    Pure function: no DB, no network, no LLM. The caller owns the transaction and
    performs the write the returned action calls for.

    `candidates` MUST already be scoped to one (user, skill) — use
    `build_candidates`. Passing an unscoped list silently produces cross-skill
    collapses, which is the one failure mode this design is built to avoid.
    """
    name_normalized = normalize(proposed_name)

    if not candidates:
        # First drill for this (user, skill). Nothing to collapse against.
        return DedupeDecision(
            action=DedupeAction.INSERT,
            score=None,
            matched_drill_id=None,
            name_normalized=name_normalized,
        )

    by_name = {c.name: c for c in candidates}
    match = best_match(proposed_name, list(by_name.keys()))

    if match is None:
        # best_match returns None for an empty candidate list or an all-zero
        # best score — treat both as "nothing to collapse against".
        return DedupeDecision(
            action=DedupeAction.INSERT,
            score=None,
            matched_drill_id=None,
            name_normalized=name_normalized,
        )

    best_name, score = match
    matched = by_name[best_name]

    if score >= SCORE_AUTO_DEDUPE:
        action = DedupeAction.REUSE
    elif score >= SCORE_CURATOR_QUEUE:
        action = DedupeAction.QUEUE
    else:
        action = DedupeAction.INSERT

    return DedupeDecision(
        action=action,
        score=score,
        matched_drill_id=matched.drill_id,
        name_normalized=name_normalized,
    )


def score_against(proposed_name: str, existing_name: str) -> int:
    """Thin re-export of `skill_dedupe.dedupe_score` for drill call sites.

    Present so drill code never reaches past this module into the skill pipeline
    for a bare score, which would make the shared-threshold coupling invisible.
    """
    return dedupe_score(proposed_name, existing_name)
