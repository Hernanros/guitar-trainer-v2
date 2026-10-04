"""Stamp zero actuals on historical cache-hit view rows — FLE-39.

FLE-39 changed what counts against a per-user cap. A `governor_calls` row used to
count whenever `error_code IS NULL`, which quietly charged the user for calls that
were killed before any handler could stamp them: a client disconnect raises
`asyncio.CancelledError` (BaseException on 3.13, so `except Exception` never fired)
and a deploy SIGTERM runs no handler at all. The new predicate asks instead whether
the row FINISHED — at least one actual token column stamped — or is still young
enough to be in flight.

That predicate needs "actuals are NULL" to mean "this call never completed". For
one class of row in prod, it did not.

THE ROW THIS MIGRATION FIXES
----------------------------
A cache hit in `GET /songs/{id}/breakdown` inserts a `governor_calls` row so that
viewing an already-generated breakdown still counts against the 3-per-7d cap
(deliberate — see breakdowns.py). It wrote `prompt_tokens_estimated = 0` and left
both actuals NULL, because a cached view spends nothing. Under the new predicate
those rows read as abandoned and stop counting ten minutes after being written,
which would turn a capped feature into unlimited free breakdown views.

breakdowns.py now writes explicit zeros for new rows. This migration does the same
for the ones already in the table. Zero is the truthful value: the row is finished
and it cost nothing.

WHY `prompt_tokens_estimated = 0` IDENTIFIES THEM SAFELY
-------------------------------------------------------
It is not a heuristic. The three ways a row can have NULL actuals are distinct:

  estimated = 0     cache-hit view — only breakdowns.py writes a literal 0
  estimated > 0     a real dispatch that died mid-flight (record_estimate ran and
                    wrote count_tokens' result; a real prompt is never 0 tokens)
  estimated IS NULL the process died between the INSERT and record_estimate

Measured against prod before writing this migration: 17 rows of the first shape
(breakdown, 2026-09-14 → 2026-10-04), exactly 1 of the second
(8d043662-d493-43c7-bf22-51d5e1fe938e, the container replacement recorded in
FLE-39's evidence table), and 0 of the third. Only the first shape is touched here;
the mid-dispatch row is left alone precisely so the new predicate refunds it, which
is the entire point of the issue.

`dollars_actual` is set to 0 for the same reason — a NULL there reads as "unpriced"
to the FLE-95 spend backfill, and these rows are priced: they are free.

RESIDUAL
--------
Old code keeps serving for the ~18s between pre-deploy (where this runs) and the new
container starting, so a cache-hit view landing in that window is written NULL and
would stop counting ten minutes later. Worst case is one extra free view for one
user, self-limiting at the next deploy, and not worth holding the fix for.
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


# Deliberately narrow: error_code IS NULL keeps us off rows that already resolved
# as failures, and the estimated = 0 equality is the cache-hit signature argued
# above. Anything else with NULL actuals is a genuinely abandoned call and must
# keep its NULLs so the FLE-39 predicate can refund it.
_CACHE_HIT_VIEW_ROWS = """
    UPDATE governor_calls
       SET prompt_tokens_actual = 0,
           output_tokens_actual = 0,
           dollars_actual = 0
     WHERE error_code IS NULL
       AND prompt_tokens_estimated = 0
       AND prompt_tokens_actual IS NULL
       AND output_tokens_actual IS NULL
"""


def upgrade() -> None:
    op.execute(_CACHE_HIT_VIEW_ROWS)


def downgrade() -> None:
    # Restores the NULLs on zero-token rows. Safe because a real dispatch can
    # never report 0 prompt tokens, so the predicate here cannot match a row this
    # migration did not write.
    op.execute(
        """
        UPDATE governor_calls
           SET prompt_tokens_actual = NULL,
               output_tokens_actual = NULL,
               dollars_actual = NULL
         WHERE error_code IS NULL
           AND prompt_tokens_estimated = 0
           AND prompt_tokens_actual = 0
           AND output_tokens_actual = 0
        """
    )
