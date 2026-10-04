"""pilot_spend.py — read the global pilot dollar ceiling (FLE-92 item 2/3).

This script IS the 80% alert channel. Hernan asked for an alert at $20 of $25 and
explicitly ruled out building notification infrastructure for it, so the alert is two
things and no more:

  1. A WARNING log line from the governor itself the moment any governed call is
     dispatched above 80% (grep the Railway logs for FLETCHER_PILOT_CEILING_ALERT).
  2. This script, which reports the same numbers on demand and exits non-zero once
     the threshold is crossed, so a cron or a heartbeat can treat it as a check
     without parsing anything.

USAGE (from the server/ directory):
    # dev DB
    DATABASE_URL=postgresql://gt:devpass@localhost:5433/guitar_trainer \\
      .venv/bin/python -m scripts.pilot_spend

    # prod, via the Railway public proxy
    DATABASE_URL='postgresql://...@<proxy-host>:<port>/railway' \\
      .venv/bin/python -m scripts.pilot_spend

EXIT CODES — chosen so `||` chains read correctly in a shell:
    0  under 80% of the ceiling. Nothing to do.
    1  at or over 80%. Raise FLETCHER_PILOT_CEILING_USD before it hits.
    2  at or over 100%. Breakdowns are ALREADY being refused with PILOT_BUDGET_SPENT.
    3  the ceiling is switched off entirely (FLETCHER_PILOT_CEILING_USD=off). Reported
       as a failure rather than as "fine", because an uncapped pilot is the condition
       this whole slice exists to prevent and it should never be the quiet default.

Reads the same effective_pilot_ceiling() / pilot_spend() the server enforces with, so
this cannot report a number the governor disagrees with. Per-user and per-feature
breakdowns are reported alongside the total because the first question on seeing $20
is always "who / what spent it".
"""
from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.governor import (  # noqa: E402
    COUNTS_AGAINST_CAP_SQL,
    PILOT_CEILING_ALERT_FRACTION,
    RESERVATION_USD,
    effective_pilot_ceiling,
    pilot_spend,
    pilot_window_start,
)


def _async_url(raw: str) -> str:
    """Normalise a DATABASE_URL to the asyncpg driver."""
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    return raw


async def _attribution(db: AsyncSession) -> tuple[list, list]:
    """Per-feature and per-user spend, same predicate and window as the total.

    Hand-written rather than reusing pilot_spend() because the GROUP BY is the whole
    point; the WHERE is interpolated from the same constants so the parts still sum
    to the total the governor enforces on.
    """
    start = pilot_window_start()
    window_clause = "created_at >= :start AND " if start is not None else ""
    params: dict[str, object] = {"start": start} if start is not None else {}

    per_feature = (
        await db.execute(
            text(
                "SELECT feature, COUNT(*) AS calls, "
                "       COALESCE(SUM(COALESCE(dollars_actual, dollars_estimated)), 0) AS usd "
                "FROM governor_calls "
                f"WHERE {window_clause}{COUNTS_AGAINST_CAP_SQL} "
                "GROUP BY feature ORDER BY usd DESC"
            ),
            params,
        )
    ).all()

    per_user = (
        await db.execute(
            text(
                "SELECT user_id, COUNT(*) AS calls, "
                "       COALESCE(SUM(COALESCE(dollars_actual, dollars_estimated)), 0) AS usd "
                "FROM governor_calls "
                f"WHERE {window_clause}{COUNTS_AGAINST_CAP_SQL} "
                "GROUP BY user_id ORDER BY usd DESC LIMIT 20"
            ),
            params,
        )
    ).all()

    return per_feature, per_user


async def main() -> int:
    raw_url = os.environ.get("DATABASE_URL")
    if not raw_url:
        print("DATABASE_URL is not set. See the usage block at the top of this file.")
        return 3

    ceiling = effective_pilot_ceiling()
    start = pilot_window_start()

    engine = create_async_engine(_async_url(raw_url), echo=False, pool_size=1, max_overflow=0)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            spent = await pilot_spend(db)
            per_feature, per_user = await _attribution(db)

            # Rows the ceiling deliberately does NOT bill, reported so the gap between
            # this number and the Anthropic console is explainable rather than
            # mysterious. See pilot_spend's docstring: a call that failed on our side
            # after Anthropic charged us is real money this total cannot see.
            window_clause = "created_at >= :start AND " if start is not None else ""
            params: dict[str, object] = {"start": start} if start is not None else {}
            excluded = await db.scalar(
                text(
                    "SELECT COUNT(*) FROM governor_calls "
                    f"WHERE {window_clause}NOT ({COUNTS_AGAINST_CAP_SQL})"
                ),
                params,
            )
    finally:
        await engine.dispose()

    print("Fletcher pilot spend")
    print(f"  window start : {start.isoformat() if start else 'all rows (FLETCHER_PILOT_START unset)'}")

    if ceiling is None:
        print("  ceiling      : OFF (FLETCHER_PILOT_CEILING_USD)")
        print(f"  spent        : ${spent}")
        print()
        print("  The global ceiling is switched off. Governed calls will not refuse at")
        print("  any dollar figure. Unset FLETCHER_PILOT_CEILING_USD to restore $25.")
        return 3

    pct = (spent / ceiling * 100) if ceiling else Decimal(0)
    remaining = ceiling - spent
    print(f"  ceiling      : ${ceiling}")
    print(f"  spent        : ${spent}  ({pct:.1f}%)")
    print(f"  remaining    : ${remaining}  (~{int(remaining / RESERVATION_USD)} breakdowns)")
    print(f"  not billed   : {excluded or 0} rows (errored or abandoned mid-dispatch)")

    if per_feature:
        print("\n  by feature:")
        for feature, calls, usd in per_feature:
            print(f"    {feature:<14} {calls:>5} calls  ${usd}")
    if per_user:
        print("\n  by user (top 20):")
        for user_id, calls, usd in per_user:
            print(f"    {user_id}  {calls:>5} calls  ${usd}")

    if spent >= ceiling:
        print(
            f"\n  CEILING REACHED. Breakdowns are refusing with PILOT_BUDGET_SPENT.\n"
            f"  Raise FLETCHER_PILOT_CEILING_USD on the Railway fletcher service to resume."
        )
        return 2
    if spent >= ceiling * PILOT_CEILING_ALERT_FRACTION:
        print(
            f"\n  {PILOT_CEILING_ALERT_FRACTION * 100:.0f}% THRESHOLD CROSSED. "
            f"${remaining} left before breakdowns start refusing.\n"
            f"  Raise FLETCHER_PILOT_CEILING_USD now if the pilot should keep running."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
