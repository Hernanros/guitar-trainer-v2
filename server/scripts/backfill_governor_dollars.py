"""backfill_governor_dollars.py — price the pre-FLE-19 governor_calls rows (FLE-95).

FLE-19 made the write path populate `dollars_estimated` / `dollars_actual`, but a
write-path fix cannot reach rows that were already written. Measured against prod on
2026-10-04 (178 rows): 143 rows carry real Anthropic token counts with
`dollars_actual IS NULL`, so `SELECT SUM(dollars_actual) FROM governor_calls`
under-reported lifetime spend by ~$2.105 — the exact complaint FLE-19 was filed
about, fixed only going forward. 144 rows have the same gap on the estimated side.

This script closes it by pricing the token columns the rows already carry. It does
NOT re-derive rates: it calls `app.ai.governor.price_dollars`, the same helper
`record_estimate` and `record_actuals` call, so a rate change can never leave history
priced at a rate the live path disagrees with. Pricing keys off each row's own
`model` column for the same reason the write path does — a row stays priced at the
model it actually ran on.

USAGE (from the server/ directory) — dry run first, it is the default:
    DATABASE_URL='postgresql://...@<proxy-host>:<port>/railway' \\
      .venv/bin/python -m scripts.backfill_governor_dollars

    # then, with an explicit go-ahead, the same command plus:
    ... .venv/bin/python -m scripts.backfill_governor_dollars --apply

EXIT CODES:
    0  ran (dry run reported a plan, or --apply committed it)
    1  nothing to do — every row is already priced
    2  DATABASE_URL unset, or rows were skipped because their model has no rate

WHAT IT DELIBERATELY DOES NOT TOUCH
-----------------------------------
Cache-hit view rows. `GET /songs/{id}/breakdown` inserts a governor_calls row for a
cached view so the view still counts against the cap, and that row spent no money.
Alembic 0013 stamps those rows `prompt_tokens_actual = 0, output_tokens_actual = 0,
dollars_actual = 0` — zero, not NULL, precisely so a $0.00 row stays distinguishable
from an unpriced one. Both predicates below exclude them from either side:

  * the actual pass requires `COALESCE(prompt_tokens_estimated, -1) <> 0`, which is
    0013's own cache-hit signature inverted. A real dispatch never reports 0 prompt
    tokens, and a row whose count_tokens call failed has NULL there, not 0 — hence
    COALESCE to -1 rather than a bare `<> 0`, which NULL would make unknown.
  * the estimated pass requires `prompt_tokens_estimated > 0`, same signature.

That holds whether or not 0013 has deployed yet: before it, a cache-hit row has NULL
actuals and no dollars to write; after it, it is already priced at 0.

HOW `dollars_estimated` IS RECONSTRUCTED (and why it needs a history table)
--------------------------------------------------------------------------
`dollars_actual` is exact — both its inputs live on the row. `dollars_estimated` is
not. The live path prices it as `price_dollars(model, prompt_tokens_estimated,
max_output_tokens)`, where `max_output_tokens` is the calling module's own
`max_tokens` ceiling, making the figure a pre-dispatch WORST CASE. There is no
`output_tokens_estimated` column — that ceiling is a property of the code at dispatch
time and is not persisted, so it has to be recovered from git.

Recovered below. The CURRENT value of each ceiling is imported, not copied, so this
table cannot drift from the modules it describes; only the superseded values are
frozen literals, which is safe because they are historical facts:

    skill_verify   1024   unchanged since the feature existed
    onboarding     8192   unchanged since the feature existed
    breakdown      8192   until a6adbaa raised it to 20000 (FLE-43/FLE-44), which
                          went live with the 54fdce9 deploy at 2026-09-16T14:46:50Z

Verified against the 9 affected breakdown rows: three sit at 2026-09-16 10:41-10:43Z
(four hours before the boundary) and five at 2026-09-17/09-24, so none lands near
enough to the switch for the boundary to be a judgement call.

If a feature has no entry, its rows are counted as skipped rather than priced at a
guessed ceiling, and `--skip-estimated` turns the whole pass off. That follows
price_dollars' own rule: in a cost audit trail a missing number beats a wrong one,
because a missing one is visibly missing.

NO EFFECT ON THE PILOT CEILING
------------------------------
`pilot_spend()` sums `COALESCE(dollars_actual, dollars_estimated)` over
COUNTS_AGAINST_CAP_SQL. Every row this script prices on the estimated side either
also gets a `dollars_actual` (which COALESCE prefers) or has NULL actuals and a
`created_at` far outside the 10-minute in-flight grace, so it does not count. The
worst-case estimated figures are therefore invisible to the ceiling — which matters,
because they are several times larger than real spend by construction and must never
be mistaken for it.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.breakdown import _MAX_OUTPUT_TOKENS as _BREAKDOWN_CEILING  # noqa: E402
from app.ai.governor import price_dollars  # noqa: E402
from app.ai.onboarding import _MAX_OUTPUT_TOKENS as _ONBOARDING_CEILING  # noqa: E402
from app.ai.skill_verifier import _MAX_OUTPUT_TOKENS as _SKILL_VERIFY_CEILING  # noqa: E402


# The 20000 ceiling went live with the deploy of 54fdce9 (which carried a6adbaa), not
# with a6adbaa's commit timestamp six minutes earlier. Deploy time is what a row's
# created_at can be compared against.
_BREAKDOWN_CEILING_RAISED_AT = datetime(2026, 9, 16, 14, 46, 50, tzinfo=timezone.utc)

# feature -> ((in_effect_from | None, max_output_tokens), ...), oldest era first.
# None means "since the feature existed". See the module docstring for provenance.
_CEILING_HISTORY: dict[str, tuple[tuple[datetime | None, int], ...]] = {
    "skill_verify": ((None, _SKILL_VERIFY_CEILING),),
    "onboarding": ((None, _ONBOARDING_CEILING),),
    "breakdown": ((None, 8192), (_BREAKDOWN_CEILING_RAISED_AT, _BREAKDOWN_CEILING)),
}


def output_ceiling(feature: str, created_at: datetime) -> int | None:
    """The `max_tokens` the given feature dispatched with at `created_at`.

    Returns None for a feature with no recorded history, so the caller can skip the
    row instead of inventing a ceiling for it.
    """
    eras = _CEILING_HISTORY.get(feature)
    if not eras:
        return None
    ceiling = None
    for start, value in eras:
        if start is None or created_at >= start:
            ceiling = value
    return ceiling


# Rows whose actuals are real but unpriced. The estimated-side guard is 0013's
# cache-hit signature; see the module docstring.
_ACTUAL_ROWS_SQL = """
    SELECT id, feature, model, created_at,
           prompt_tokens_actual, output_tokens_actual
      FROM governor_calls
     WHERE prompt_tokens_actual IS NOT NULL
       AND dollars_actual IS NULL
       AND COALESCE(prompt_tokens_estimated, -1) <> 0
     ORDER BY created_at
"""

_ESTIMATED_ROWS_SQL = """
    SELECT id, feature, model, created_at, prompt_tokens_estimated
      FROM governor_calls
     WHERE prompt_tokens_estimated > 0
       AND dollars_estimated IS NULL
     ORDER BY created_at
"""

_WRITE_ACTUAL_SQL = "UPDATE governor_calls SET dollars_actual = :usd WHERE id = :id"
_WRITE_ESTIMATED_SQL = (
    "UPDATE governor_calls SET dollars_estimated = :usd WHERE id = :id"
)


@dataclass
class Counters:
    """What the run did, per side. Printed by main(); asserted by the tests."""

    actual_matched: int = 0
    actual_priced: int = 0
    actual_usd: Decimal = Decimal("0")
    estimated_matched: int = 0
    estimated_priced: int = 0
    estimated_usd: Decimal = Decimal("0")
    # (id, feature, model, why) for every row left NULL on purpose.
    skipped: list[tuple[str, str, str, str]] = field(default_factory=list)

    @property
    def rows_written(self) -> int:
        return self.actual_priced + self.estimated_priced


async def backfill(
    db: AsyncSession,
    *,
    apply: bool = False,
    skip_estimated: bool = False,
) -> Counters:
    """Price every unpriced governor_calls row through `price_dollars`.

    THE CALLER OWNS THE TRANSACTION. `apply=True` executes the UPDATEs and does not
    commit, matching `backfill_drills.backfill` / `backfill_drill_taxonomy.backfill`
    — chaining two of these in one session must not let the second one's apply commit
    the first one's writes (see tests/test_backfill_drill_taxonomy.py).

    `apply=False` still reads and prices everything, so a dry run reports the exact
    figures the apply would write rather than an estimate of them.
    """
    counters = Counters()

    for row in (await db.execute(text(_ACTUAL_ROWS_SQL))).all():
        counters.actual_matched += 1
        usd = (
            price_dollars(row.model, row.prompt_tokens_actual, row.output_tokens_actual)
            if row.model
            else None
        )
        if usd is None:
            counters.skipped.append(
                (str(row.id), row.feature, row.model or "<null>", "unpriced model")
            )
            continue
        counters.actual_priced += 1
        counters.actual_usd += usd
        if apply:
            await db.execute(text(_WRITE_ACTUAL_SQL), {"usd": usd, "id": str(row.id)})

    if skip_estimated:
        return counters

    for row in (await db.execute(text(_ESTIMATED_ROWS_SQL))).all():
        counters.estimated_matched += 1
        ceiling = output_ceiling(row.feature, row.created_at)
        if ceiling is None:
            counters.skipped.append(
                (
                    str(row.id),
                    row.feature,
                    row.model or "<null>",
                    "no output-ceiling history for this feature",
                )
            )
            continue
        usd = (
            price_dollars(row.model, row.prompt_tokens_estimated, ceiling)
            if row.model
            else None
        )
        if usd is None:
            counters.skipped.append(
                (str(row.id), row.feature, row.model or "<null>", "unpriced model")
            )
            continue
        counters.estimated_priced += 1
        counters.estimated_usd += usd
        if apply:
            await db.execute(
                text(_WRITE_ESTIMATED_SQL), {"usd": usd, "id": str(row.id)}
            )

    return counters


def _async_url(raw: str) -> str:
    """Normalise a DATABASE_URL to the asyncpg driver (same shape as pilot_spend)."""
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    return raw


async def _lifetime_actual(db: AsyncSession) -> Decimal:
    """`SUM(dollars_actual)` — the number FLE-19/FLE-95 exist to make correct."""
    return Decimal(
        str(
            await db.scalar(
                text("SELECT COALESCE(SUM(dollars_actual), 0) FROM governor_calls")
            )
        )
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="commit the UPDATEs. Without it the script only reports the plan.",
    )
    parser.add_argument(
        "--skip-estimated",
        action="store_true",
        help=(
            "price dollars_actual only. dollars_estimated needs the output ceiling "
            "recovered from git (see the module docstring); this skips that half."
        ),
    )
    args = parser.parse_args()

    raw_url = os.environ.get("DATABASE_URL")
    if not raw_url:
        print("DATABASE_URL is not set. See the usage block at the top of this file.")
        return 2

    engine = create_async_engine(
        _async_url(raw_url), echo=False, pool_size=1, max_overflow=0
    )
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as db:
            before = await _lifetime_actual(db)
            counters = await backfill(
                db, apply=args.apply, skip_estimated=args.skip_estimated
            )
            if args.apply:
                await db.commit()
            else:
                await db.rollback()
            after = await _lifetime_actual(db)
    finally:
        await engine.dispose()

    mode = "APPLIED" if args.apply else "DRY RUN (nothing written)"
    print(f"governor_calls dollar backfill — {mode}")
    print(
        f"  dollars_actual    : {counters.actual_priced}/{counters.actual_matched} rows"
        f"  +${counters.actual_usd}"
    )
    if args.skip_estimated:
        print("  dollars_estimated : skipped (--skip-estimated)")
    else:
        print(
            f"  dollars_estimated : "
            f"{counters.estimated_priced}/{counters.estimated_matched} rows"
            f"  +${counters.estimated_usd}  (worst case, not spend)"
        )
    print(f"\n  SUM(dollars_actual) before : ${before}")
    print(f"  SUM(dollars_actual) after  : ${after}")
    if not args.apply:
        print(f"  (would become              : ${before + counters.actual_usd})")

    if counters.skipped:
        print(f"\n  left NULL on purpose ({len(counters.skipped)}):")
        for row_id, feature, model, why in counters.skipped:
            print(f"    {row_id}  {feature:<14} {model:<22} {why}")

    if counters.actual_matched == 0 and counters.estimated_matched == 0:
        print("\n  Nothing to do — every row is already priced.")
        return 1
    if counters.skipped:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
