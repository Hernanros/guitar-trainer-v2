"""backfill_drill_taxonomy.py — fill drills.family / tier / tier_raw_score.

FLE-13, the half migration 0011 deliberately left out. `status` was backfilled in the
migration itself because it restates a column already present; these three need
Python — tier parses tab_snippet through app.sessions.taxonomy.compute_tier(), family
keyword-matches prose through app.sessions.family_classify — and neither belongs in a
deploy-time DDL path.

USAGE (from the server/ directory):
    cd server
    DATABASE_URL=postgresql+asyncpg://gt:devpass@localhost:5433/guitar_trainer \\
    python -m scripts.backfill_drill_taxonomy            # dry run, the default
    ... python -m scripts.backfill_drill_taxonomy --apply

DRY RUN IS THE DEFAULT, and a dry run makes exactly the decisions the write would,
so the plan is reviewable before it is committed — same contract as backfill_drills.py.

IDEMPOTENT, in the two senses that matter:
  - by default only NULL columns are filled, so a second run is a no-op
  - --recompute-tier re-derives tier for rows that already have one, which is the
    §9.1 retune path: change the rubric, re-run, every tier moves. It deliberately
    does NOT touch family, because family is a label, not a computation.

NO LLM. Same FLE-8 constraint the rest of the drill path runs under. Drills the
keyword rules decline are listed by --report-unclassified and left NULL for a
later Sonnet pass; they are never guessed at. A NULL family undercounts a §10 cell,
a wrong one fabricates coverage that is not there, and §10.1 exists to prevent
exactly the second thing.

TIER IS CLAMPED BY FAMILY, so order matters inside a row: family is classified
FIRST and passed into compute_tier(), because §9.1's family tier-range clamp is what
stops a vibrato drill scoring as D1 on a rubric that cannot see bends. A row whose
family cannot be classified still gets a tier — the unclamped one — which is the
more permissive of the two and is marked as such in the report.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.sessions.family_classify import CLASSIFIER_VERSION, classify_family
from app.sessions.taxonomy import SkillRoot, TechniqueFamily, compute_tier

# Every active drill plus the ancestry the classifier needs. The leaf -> sub -> root
# walk is the same two hops snapshot.py's _CANDIDATES_SQL makes, and for the same
# reason: the root lives two levels above the drill's own node.
#
# Duplicates and retired rows are excluded. A duplicate's coverage is carried by its
# canonical, and classifying it would put the same drill in a cell twice the moment
# anyone relaxed coverage.py's status filter.
_ROWS_SQL = text(
    """
    SELECT
        d.id::text            AS id,
        d.name                AS name,
        d.what                AS what,
        d.success_criterion   AS success_criterion,
        d.common_trap         AS common_trap,
        d.tab_snippet         AS tab_snippet,
        d.start_bpm           AS start_bpm,
        d.target_bpm          AS target_bpm,
        d.family::text        AS family,
        d.tier::text          AS tier,
        leaf.name             AS node_name,
        root.name             AS root_name
      FROM drills d
      JOIN skill_nodes leaf ON leaf.id = d.skill_node_id
      LEFT JOIN skill_nodes sub  ON sub.id  = leaf.parent_id
      LEFT JOIN skill_nodes root ON root.id = sub.parent_id AND root.level = 'root'
     WHERE d.status = 'active'
     ORDER BY d.id
    """
)

_UPDATE_SQL = text(
    """
    UPDATE drills
       SET family = COALESCE(CAST(:family AS technique_family), family),
           tier = CAST(:tier AS drill_tier),
           tier_raw_score = :tier_raw_score
     WHERE id = CAST(:id AS uuid)
    """
)


def _root_key(name: Optional[str]) -> Optional[SkillRoot]:
    """'Chord Voicings' -> SkillRoot.CHORD_VOICINGS. Unknown or absent -> None.

    None is not an error: a drill whose node has no root ancestor widens the family
    search to all 18 rather than failing, exactly as snapshot.py treats the same gap.
    """
    if not name:
        return None
    key = name.strip().lower().replace(" ", "_")
    try:
        return SkillRoot(key)
    except ValueError:
        return None


class Counters:
    """Per-run tally. Printed as the report and asserted on by tests."""

    def __init__(self) -> None:
        self.scanned = 0
        self.family_filled = 0
        self.family_declined = 0
        self.family_already_set = 0
        self.tier_filled = 0
        self.tier_recomputed = 0
        self.tier_unchanged = 0
        self.tier_unclamped = 0
        self.rows_written = 0
        self.reasons: Counter[str] = Counter()
        self.families: Counter[str] = Counter()
        self.cross_root_hints: Counter[str] = Counter()

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in vars(self).items() if not isinstance(v, Counter)}
        out["reasons"] = dict(self.reasons)
        out["families"] = dict(self.families)
        out["cross_root_hints"] = dict(self.cross_root_hints)
        return out


async def backfill(
    db: AsyncSession,
    *,
    apply: bool = False,
    recompute_tier: bool = False,
    verbose: bool = False,
) -> tuple[Counters, list[dict[str, Any]]]:
    """Classify and tier every active drill. Returns (counters, unclassified rows)."""
    counters = Counters()
    unclassified: list[dict[str, Any]] = []

    rows = (await db.execute(_ROWS_SQL)).all()
    for r in rows:
        counters.scanned += 1

        # --- family, first: compute_tier() clamps to its range. ---
        family: Optional[TechniqueFamily] = None
        if r.family:
            family = TechniqueFamily(r.family)
            counters.family_already_set += 1
        else:
            verdict = classify_family(
                root=_root_key(r.root_name),
                name=r.name or "",
                skill_node_name=r.node_name or "",
                what=r.what or "",
                success_criterion=r.success_criterion or "",
                common_trap=r.common_trap or "",
            )
            counters.reasons[verdict.reason] += 1
            if verdict.cross_root_hint is not None:
                counters.cross_root_hints[verdict.cross_root_hint.value] += 1
            if verdict.classified:
                family = verdict.family
                counters.family_filled += 1
            else:
                counters.family_declined += 1
                unclassified.append(
                    {
                        "id": r.id,
                        "name": r.name,
                        "node": r.node_name,
                        "root": r.root_name,
                        "reason": verdict.reason,
                        "best_score": verdict.score,
                        "runner_up": verdict.runner_up.value if verdict.runner_up else None,
                        "cross_root_hint": (
                            verdict.cross_root_hint.value
                            if verdict.cross_root_hint
                            else None
                        ),
                    }
                )
        if family is not None:
            counters.families[family.value] += 1
        else:
            counters.tier_unclamped += 1

        # --- tier. Skipped for rows that already have one unless asked. ---
        if r.tier and not recompute_tier:
            counters.tier_unchanged += 1
            continue

        tier, raw = compute_tier(
            r.tab_snippet or {},
            start_bpm=int(r.start_bpm),
            target_bpm=int(r.target_bpm),
            family=family,
        )
        if r.tier:
            counters.tier_recomputed += 1
        else:
            counters.tier_filled += 1

        if verbose:
            print(
                f"  {r.id[:8]} {(r.name or '')[:44]:46s} "
                f"family={family.value if family else '--':3s} tier={tier.value} raw={raw:2d}"
            )

        if apply:
            await db.execute(
                _UPDATE_SQL,
                {
                    "id": r.id,
                    # COALESCE in the statement means passing NULL leaves an existing
                    # family alone — the update cannot erase a classification.
                    "family": family.value if family else None,
                    "tier": tier.value,
                    "tier_raw_score": raw,
                },
            )
            counters.rows_written += 1

    if apply:
        await db.commit()
    return counters, unclassified


async def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes. Without it nothing is written and the same "
        "decisions are printed as a plan.",
    )
    parser.add_argument(
        "--recompute-tier",
        action="store_true",
        help="re-derive tier for rows that already have one — the §9.1 rubric "
        "retune path. Never touches family.",
    )
    parser.add_argument(
        "--report-unclassified",
        action="store_true",
        help="print the drills the rules declined, as JSON, for a later Sonnet pass.",
    )
    parser.add_argument("--verbose", action="store_true", help="one line per drill.")
    args = parser.parse_args(argv)

    async with AsyncSessionLocal() as db:
        counters, unclassified = await backfill(
            db,
            apply=args.apply,
            recompute_tier=args.recompute_tier,
            verbose=args.verbose,
        )

        print()
        print("=" * 72)
        print(f"DRILL TAXONOMY BACKFILL — {'APPLIED' if args.apply else 'DRY RUN'}")
        print(f"classifier version {CLASSIFIER_VERSION}")
        print("=" * 72)
        for key, value in counters.as_dict().items():
            print(f"  {key:22s} {value}")

        if args.report_unclassified and unclassified:
            print()
            print("UNCLASSIFIED (left NULL — candidates for a Sonnet pass):")
            print(json.dumps(unclassified, indent=2))

        # The gate this whole issue exists to make measurable. Reported on every run,
        # including dry runs, because the number people actually want is "would this
        # backfill let us grade the bank?"
        from app.sessions.coverage import grade, load_coverage

        report = grade(await load_coverage(db))
        print()
        print(report["summary"])
        if not args.apply:
            print("  (dry run — coverage reflects the DB as it stands, not the plan)")

    if not args.apply:
        print("\nNothing was written. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
