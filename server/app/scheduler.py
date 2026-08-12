"""APScheduler in-process nightly mastery decay scheduler.

Single-worker safe: designed for `uvicorn --workers 1` (see server/railway.toml).
If workers ever increase beyond 1, replace with a postgres advisory lock or move
the decay job to a Railway cron plugin service so only one worker fires the job.

Exports:
  get_scheduler() -> AsyncIOScheduler  -- singleton factory
  decay_all_nodes() -> None            -- nightly 5% decay job (SKILL-05)

Registered in app.main::on_startup via:
    scheduler = get_scheduler()
    scheduler.add_job(
        decay_all_nodes,
        trigger="cron", hour=3, minute=0, timezone="UTC",
        id="decay_all_nodes", replace_existing=True,
    )
    scheduler.start()
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text

from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """Return the singleton AsyncIOScheduler, creating it on first call.

    The scheduler is initialized with timezone="UTC" so cron triggers
    default to UTC when no per-job timezone is specified.
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


async def decay_all_nodes() -> None:
    """Nightly 5% mastery decay job (SKILL-05, D-Claude-decay).

    For each skill_node row untouched > 7 days with mastery > 0:
      - Reduces mastery by 5%: mastery = GREATEST(mastery * 0.95, 0)
      - Sets last_decayed_at = now()

    Debounce guard: last_decayed_at < now() - interval '20 hours' prevents
    double-decay if APScheduler fires a catch-up run within the same calendar
    day (misfire_grace_time window can be up to 20-23 h after 03:00 UTC slot).

    An audit row is inserted into decay_runs for every invocation regardless
    of success or failure, so silent crashes are impossible (D-Claude-decay,
    T-04-04-04). Failure path uses a fresh session to ensure the audit row
    commits even when the primary session is in an error state.
    """
    async with AsyncSessionLocal() as db:
        try:
            # --- Insert audit row (started_at) BEFORE the UPDATE ---
            insert_result = await db.execute(
                text(
                    "INSERT INTO decay_runs (id, started_at) "
                    "VALUES (gen_random_uuid(), now()) "
                    "RETURNING id"
                )
            )
            run_id = insert_result.scalar_one()

            # --- Execute the decay UPDATE ---
            update_result = await db.execute(
                text(
                    "UPDATE skill_nodes "
                    "SET mastery = GREATEST(mastery * 0.95, 0), "
                    "    last_decayed_at = now() "
                    "WHERE updated_at < now() - interval '7 days' "
                    "  AND mastery > 0 "
                    "  AND (last_decayed_at IS NULL "
                    "       OR last_decayed_at < now() - interval '20 hours') "
                    "RETURNING id"
                )
            )
            count = len(update_result.fetchall())

            # --- Close the audit row ---
            await db.execute(
                text(
                    "UPDATE decay_runs "
                    "SET finished_at = now(), nodes_affected = :count "
                    "WHERE id = :run_id"
                ),
                {"count": count, "run_id": str(run_id)},
            )
            await db.commit()
            logger.info("Decay run %s complete: %d nodes decayed.", run_id, count)

        except Exception as exc:
            await db.rollback()
            # Open a FRESH session — the errored session may be in an invalid state.
            # This ensures the failure audit row commits even after a DB error.
            async with AsyncSessionLocal() as err_db:
                await err_db.execute(
                    text(
                        "INSERT INTO decay_runs "
                        "(id, started_at, finished_at, error) "
                        "VALUES (gen_random_uuid(), now(), now(), :error)"
                    ),
                    {"error": str(exc)[:1000]},
                )
                await err_db.commit()
            logger.error("Decay run failed: %s", exc)
            # Do NOT re-raise — the scheduler continues; audit row surfaces the failure.
