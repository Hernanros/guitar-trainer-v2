"""regenerate_drill_breakdowns.py — give pre-4.1 breakdowns their drills (FLE-54 Fix 2).

D-11 is cache-forever: once `songs.breakdown_generated_at IS NOT NULL` the breakdown
endpoint returns the stored JSONB and never calls Sonnet again. Drills arrived in
Phase 4.1, so every breakdown cached before that has no `drills` key — forever. The
mobile screen hides an empty drills section, so it degrades quietly instead of
erroring, which is why nobody noticed. Drills are the core of the v1 session; in the
pilot this is a silently broken experience for the earliest and most-used songs.

This is a **versioned-schema backfill, not a cache-invalidation policy**. It targets
exactly the rows whose stored breakdown predates the drills schema, regenerates them
once, and stops. The cache-forever contract is unchanged for everything else.

USAGE (from the server/ directory, with the venv python — bare python3 is 3.9.6):
    cd server

    # 1. Plan. Dry run is the DEFAULT: counts rows, prints the estimate, spends nothing.
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/guitar_trainer \\
    .venv/bin/python -m scripts.regenerate_drill_breakdowns

    # 2. Spend. --apply calls Sonnet once per candidate, through @governed.
    DATABASE_URL=... .venv/bin/python -m scripts.regenerate_drill_breakdowns --apply

    # 3. One song, whatever its drills state — the force path.
    DATABASE_URL=... .venv/bin/python -m scripts.regenerate_drill_breakdowns \\
        --song-id 73 --apply

COST. Every call goes through `run_technique_breakdown`, which carries `@governed`:
it lands in `governor_calls` with real token counts and honours the same per-user
weekly cap a user gets. The backfill has no exemption — a capped user's rows are
reported as skipped, not silently waved through. The dry-run dollar figure is an
estimate from prod averages and is there to be approved before spending, not to be
trusted afterwards; `governor_calls` holds the actuals.

ORDER OF OPERATIONS the issue asks for: dev first, then report the prod row count and
estimated spend for approval, and only then run --apply against prod.

SAFE TO RE-RUN. A row is only rewritten when regeneration produced at least one
surviving drill, so a run that fails or gets interrupted leaves every untouched row
still a candidate, and every fixed row no longer one.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.db.session import AsyncSessionLocal
from app.services.breakdown_backfill import (
    Candidate,
    estimate_cost_usd,
    find_candidates,
    regenerate_one,
)

_REASON_LABEL = {
    "pre_drills_schema": "pre-4.1 schema gap",
    "soft_fail_empty": "post-4.1 soft-fail",
    "forced": "forced by --song-id",
}


def _print_plan(candidates: list[Candidate]) -> None:
    print(f"\n=== candidates: {len(candidates)} ===")
    for c in candidates:
        stamp = c.generated_at.isoformat() if c.generated_at else "never"
        print(
            f"  song={c.song_id:<6} {c.title[:38]:<38} {c.artist[:22]:<22} "
            f"generated={stamp}  [{_REASON_LABEL.get(c.reason, c.reason)}]"
        )
    if candidates:
        print(f"\n  estimated spend: ${estimate_cost_usd(len(candidates)):.2f}")


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate breakdowns that predate drills (FLE-54 Fix 2).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually call Sonnet and write. Without this it is a dry run that spends nothing.",
    )
    parser.add_argument(
        "--song-id",
        type=int,
        metavar="N",
        help=(
            "Regenerate exactly this song regardless of its drills state — the "
            "single-song escape hatch. Still requires a cached breakdown to exist."
        ),
    )
    parser.add_argument(
        "--include-soft-fail",
        action="store_true",
        help=(
            "Also re-roll post-4.1 rows whose drills came back empty. Those are "
            "generation outcomes, not schema gaps, so re-rolling may just spend again "
            "for the same empty result. Off by default."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Stop after N rows. Use it to buy a small batch before committing to the rest.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async with AsyncSessionLocal() as db:
        candidates = await find_candidates(
            db,
            include_soft_fail=args.include_soft_fail,
            force_song_id=args.song_id,
        )
        if args.limit is not None:
            candidates = candidates[: args.limit]

        _print_plan(candidates)

        if not candidates:
            print("\nNothing to do.")
            return 0

        if not args.apply:
            print("\nDRY RUN — nothing written, nothing spent. Pass --apply to commit.")
            return 0

        print(f"\n=== applying to {len(candidates)} row(s) ===")
        ok = 0
        failed: list[str] = []
        for c in candidates:
            result = await regenerate_one(db, c)
            if result.ok:
                ok += 1
                print(f"  OK   song={result.song_id} drills={result.drills_written}")
            else:
                failed.append(f"song={result.song_id}: {result.detail}")
                print(f"  SKIP song={result.song_id} — {result.detail}")

    print(f"\n=== done: {ok} regenerated, {len(failed)} skipped ===")
    for line in failed:
        print(f"  {line}")
    print("\nActual spend is in `governor_calls`, not the estimate above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
