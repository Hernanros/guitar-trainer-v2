"""APScheduler in-process background jobs.

Single-worker safe: designed for `uvicorn --workers 1` (see server/railway.toml).
If workers ever increase beyond 1, replace with a postgres advisory lock or move
the jobs to a Railway cron plugin service so only one worker fires them.

Exports:
  get_scheduler() -> AsyncIOScheduler    -- singleton factory
  decay_all_nodes() -> None              -- nightly 5% decay job (SKILL-05)
  sweep_abandoned_sessions() -> None     -- hourly abandonment backstop (FLE-21 §3)

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


async def sweep_abandoned_sessions() -> None:
    """Hourly backstop that closes open practice sessions (FLE-21 §3).

    Load-bearing, not hygiene. `store.resolve_or_generate` already closes a user's
    stale sessions inline and transactionally — but only when that user comes BACK.
    This is for the user who does not: without it their last session stays
    `in_progress` forever, counted as neither completed nor abandoned, and FLE-21 §4's
    completion rate is `completed / (completed + abandoned)`. A session stuck open is
    silently removed from the denominator, so the pilot's headline metric drifts UP as
    participants drop out. A number that improves because data went missing is the
    worst failure mode available here, because it reads as success.

    Two conditions, and they are not peers:

    1. **The day roll** (FLE-4 §6, adopted verbatim by FLE-21 R3) — the real rule.
       Evaluated per row against that row's own `tz_offset_minutes`, because "the day
       rolled" is a claim about the user's local clock and the pilot roster is not in
       one timezone.
    2. **36 hours since `last_activity_at`** — protection against a corrupt
       `tz_offset_minutes`, nothing more. It should fire approximately never; when it
       does, `lifecycle.sweep_open_sessions` logs a WARNING per row, and that is a bug
       report rather than a metric.

    Runs hourly (see main.py) so the day roll is caught within an hour of the user's
    local midnight regardless of which timezone they are in.

    Failures are logged and swallowed, matching `decay_all_nodes`: a scheduler job that
    raises kills nothing useful and the next hour's run retries anyway. There is no
    audit table for this job — the UPDATE is idempotent (a closed session no longer
    matches `state = ANY(open_states)`), so a missed run costs at most an hour of
    staleness rather than a lost write.
    """
    from app.sessions.lifecycle import sweep_open_sessions

    async with AsyncSessionLocal() as db:
        try:
            result = await sweep_open_sessions(db)
            await db.commit()
            if result.total:
                logger.info(
                    "Session sweep: closed %d (day_rolled=%d, timeout=%d).",
                    result.total,
                    result.day_rolled,
                    result.timed_out,
                )
        except Exception as exc:
            await db.rollback()
            logger.error("Session sweep failed: %s", exc)
            # Do NOT re-raise — next hourly run retries; the UPDATE is idempotent.
