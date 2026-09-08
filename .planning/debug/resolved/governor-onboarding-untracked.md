---
status: resolved-false-positive
trigger: "Onboarding cost is silently untracked: user 28c6b9ea-... has 5 governor_calls rows with feature='skill_verify' but ZERO rows with feature='onboarding' despite Sonnet returning 200s in Anthropic Console for their onboarding batch parse. @governed decorator returns normally, endpoint returns 200, no exceptions logged — the only symptom is missing DB rows. Suspect surface: _run_onboarding_parse_bounded fresh-session wrapper introduced in commit def6ba8 (2026-08-17, 'unbreak onboarding: isolate @governed commit from SAVEPOINT'). Blocks any real POC where cost governance matters — governor cap enforcement is incomplete."
created: 2026-08-17
updated: 2026-08-17
phase: 04
milestone: v1.0
---

# Debug Session: governor-onboarding-untracked

## Observed facts

**User report (2026-08-17, from HANDOFF.json):**
Prod DB query during 5-P0 hotfix session:
```sql
SELECT feature, COUNT(*) FROM governor_calls
WHERE user_id = '28c6b9ea-...' GROUP BY feature;
```
Result claimed:
- `feature='skill_verify'` → 5 rows
- `feature='onboarding'` → 0 rows

**Meanwhile in Anthropic Console:** Sonnet /v1/messages calls for the same user's onboarding batch parse succeeded (200s visible in Console dashboard).

**Governor invariant broken (as reported):** every Sonnet call inside `@governed`-decorated function must write a `governor_calls` row (used for $20/mo cap check + per-user rate limits). The onboarding call site was reported to bypass `_insert_governor_call`.

**Suspected introduction:** commit `def6ba8` (2026-08-17):
```
fix(04-03): unbreak onboarding — isolate @governed commit from SAVEPOINT
```
This commit introduced `_run_onboarding_parse_bounded` as a fresh-session wrapper to protect `@governed`'s `db.commit()` from tearing down the caller's outer `db.begin_nested()` SAVEPOINT. That fix worked for the crash symptom but was suspected to have broken the cost-tracking write path in the process.

## Symptoms (as originally reported)

**Expected:**
Every Sonnet-backed call in the onboarding batch parse writes a row to `governor_calls` with `feature='onboarding'`, `user_id`, `input_tokens`, `output_tokens`, and cost fields — enforcing the $20/mo Anthropic Console cap and per-user rate/quota limits (COST-01/02/03/04).

**Actual (as reported):**
`@governed` decorator returns normally, endpoint returns 200 OK, no exceptions logged, but zero `governor_calls` rows appear for `feature='onboarding'`. Same user has correctly-recorded rows for `feature='skill_verify'` — so the decorator itself is functional; something specific to the onboarding call site is bypassing `_insert_governor_call`.

**Actual (verified 2026-08-17):**
NOT REPRODUCIBLE. User 28c6b9ea has 2 governor_calls rows with feature='onboarding' (not 0) and 10 with feature='skill_verify' (not 5). Governor tracking works correctly for onboarding across all recent users.

## Evidence

- timestamp: 2026-08-17T18:30Z
  step: reviewed def6ba8 diff (server/app/api/v1/users.py)
  found: `_run_onboarding_parse_bounded` opens a fresh `AsyncSessionLocal()` and passes it to `run_onboarding_parse(db=onboarding_gov_db, user_id=user_id)`. The @governed wrapper extracts `db` from kwargs, calls `_insert_governor_call(db, call_id, user_id, feature)` which does INSERT + `await db.commit()` on the fresh session. Order-of-operations is correct: INSERT+COMMIT happens BEFORE the wrapped fn body runs, so a subsequent AIParseError cannot cause row loss.
  eliminates: hypothesis #1 (fresh-session pattern loses ContextVar) — ContextVar is set AFTER the INSERT, and it only affects record_estimate/record_actuals path, not the INSERT itself.

- timestamp: 2026-08-17T18:32Z
  step: reviewed @governed wrapper (server/app/ai/governor.py:253-340) — INSERT happens on line 296, try/except starts on line 301. Any exception in _insert_governor_call would propagate to the endpoint as an HTTPException/500, NOT a silent bypass. Endpoint returned 200 per the report, so the INSERT must have succeeded (or the code path was not taken).
  eliminates: hypothesis #3 (try/except swallow — no such except wraps the INSERT).

- timestamp: 2026-08-17T18:35Z
  step: ran `tests/test_onboarding_governor_savepoint.py` locally against gt-postgres:5433 — all 3 tests PASS including `test_bootstrap_records_onboarding_governor_row` which asserts `SELECT COUNT(*) FROM governor_calls WHERE user_id = :uid AND feature = 'onboarding'` returns exactly 1 after a full bootstrap, with `error_code IS NULL` and `prompt_tokens_actual` populated. This test mocks at the get_client (network) boundary so the REAL @governed decorator runs.
  eliminates: hypothesis #5 (test-mocked-at-wrong-boundary) — the existing regression test IS mocking at the correct seam and IS passing.

- timestamp: 2026-08-17T18:40Z
  step: queried prod DB directly (railway/tokaido proxy):
    ```sql
    SELECT feature, COUNT(*) FROM governor_calls
    WHERE user_id = '28c6b9ea-09e2-4e40-a362-910e10f0a0e5' GROUP BY feature;
    ```
  found: `onboarding=2, skill_verify=10` — NOT the reported `onboarding=0, skill_verify=5`.
  Also queried per-user counts across all users: recent users (95b9cf5e, 3f025973) each have exactly 1 onboarding + 10 skill_verify + full 6-root/7-sub/5-6-leaf skill graph. Older users from before def6ba8 (28c6b9ea, cbd803c8) have multiple onboarding rows and 0 skill_nodes — consistent with the pre-def6ba8 SAVEPOINT-closed bug (governor row committed on outer session BEFORE savepoint failure rolled back the skill_nodes writes).
  eliminates: the entire premise of the debug session.

- timestamp: 2026-08-17T18:42Z
  step: queried global governor_calls state: 8 total rows with feature='onboarding' across 5 distinct users, ALL with error_code=None, spanning 2026-08-16 10:45 → 2026-08-17 17:59. No cleanup jobs delete governor_calls in production code (only in test fixtures).
  concludes: onboarding cost tracking IS working correctly. Governor invariant holds.

## Resolution

**Root cause:** No bug. The original report was based on an incorrect SQL query result — either the `feature='onboarding'` filter had a typo/wrong operator, the query was executed against a different database/environment, or the numbers were mis-transcribed from the actual result (reported `sv=5` when reality was 10 → off by 2×; reported `onboarding=0` when reality was 2 → off by exactly the count of successful onboarding rows).

The @governed decorator on `run_onboarding_parse` is functioning correctly when invoked via `_run_onboarding_parse_bounded`:
1. Fresh `AsyncSessionLocal()` is opened by the wrapper.
2. `@governed` wrapper INSERTs a governor_calls row and commits ON THAT FRESH SESSION before the wrapped fn body runs.
3. Any error in the wrapped fn triggers `_update_error` on the same fresh session (also succeeds independently of the outer SAVEPOINT).
4. Wrapped fn's `record_estimate` and `record_actuals` each open THEIR OWN fresh sessions internally, so they also commit independently.

**Fix:** Not applicable — nothing is broken.

**Verification:** Prod DB inspection shows all 5 users who invoked onboarding after Phase 4 Slice C (2026-08-12) have governor_calls rows recorded. Test suite (`tests/test_onboarding_governor_savepoint.py`) already proves this end-to-end with the REAL decorator against a real DB (all 3 tests pass locally). Governor cap enforcement is intact.

**Recommendation for the orchestrator:** Update HANDOFF.json to remove `governor-onboarding-tracking` from `remaining_tasks` and `human_actions_pending`. Consider adding a small operational script (e.g., `.planning/tools/verify_governor_coverage.py`) that runs the by-user query as a periodic sanity check so future false positives can be caught by re-running the same script rather than a fresh debug session.

## Related context

**Session memories:**
- `feedback_governed_savepoint_pattern` — accurate; def6ba8 correctly implemented the fresh-session pattern for @governed on onboarding.
- `reference_railway_cli` — used for prod DB inspection.

**Related resolved postmortems (all 2026-08-17):**
- `.planning/debug/resolved/onboarding-savepoint-closed.md` — the bug def6ba8 was fixing; fix is confirmed to also preserve governor tracking.
- `.planning/debug/resolved/onboarding-dup-song.md`
- `.planning/debug/resolved/song-of-day-nullable-metadata.md`

## Working hypotheses (all eliminated)

1. ~~Fresh-session pattern loses the ContextVar~~ — INSERT happens BEFORE ContextVar is set. Not relevant.
2. ~~Fresh session rollback path~~ — no rollback happens; commit is atomic per _insert_governor_call.
3. ~~Try/except swallow~~ — no try/except wraps the INSERT; any error would propagate as 500.
4. ~~@governed re-entry / double-wrap~~ — decorator has no re-entry check; each call INSERTs unconditionally.
5. ~~Test-mocked-at-wrong-boundary~~ — the regression test test_onboarding_governor_savepoint.py mocks at the correct (network) boundary and passes.

## Current Focus

- **hypothesis:** RESOLVED — no bug. Original report was based on inaccurate SQL result.
- **test:** Verified via (a) code inspection of def6ba8 + @governed decorator, (b) running the existing regression test suite locally, (c) direct prod DB inspection showing 8 onboarding governor_calls rows across 5 users, all successful.
- **expecting:** N/A
- **next_action:** Archive this debug session; no code change required.
