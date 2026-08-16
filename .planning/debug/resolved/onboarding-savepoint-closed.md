---
status: resolved
trigger: "Onboarding POST /api/v1/users returns HTTP 500 on prod, blocking all new users. User completing wizard on fresh EAS iOS build 690bc876 hit Complete, timed out (~30-60s waiting on Sonnet + retry), then saw 'Fletcher lost the thread' error."
created: 2026-08-16
updated: 2026-08-16
phase: 04
milestone: v1.0
---

# Debug Session: onboarding-savepoint-closed

## Symptoms (prefilled)

- **Expected**: `POST /api/v1/users` (onboarding complete) should return 200 with `SkillGraphResponse` after Sonnet parses the wizard payload and the skill graph is persisted.
- **Actual**: Server returns HTTP 500. Mobile client maps to generic error card at `mobile/src/app/onboarding/preferences.tsx:185` showing "Fletcher lost the thread. Try that again." to the user after ~30–60s wait.
- **Error message**:
  ```
  sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.
  Please complete the context manager before emitting further commands.
  ```
  Full traceback origin:
  - `server/app/api/v1/users.py:109` (in `_run_verifier_pipeline`, first `db.execute(text("SELECT id, name, level FROM skill_nodes ..."))`)
  - ← called from `server/app/api/v1/users.py:388` (`_persist_bootstrap`)
  - ← called from `server/app/api/v1/users.py:564` (`bootstrap_user`)
- **Timeline**: First observed 2026-08-16 on prod (Railway) after Phase 4 Slice C landed (commit `cf75eef` on 2026-08-12, deployed with `ee73c2e` on 2026-08-13). Never worked with Slice C code path on a real Sonnet-backed onboarding — previous onboarding tests all mock the AI / governor.
- **Repro**:
  1. Fresh EAS iOS build 690bc876-2b56-49b3-905f-7d19a6c1c6a4 installed on device
  2. Complete onboarding wizard through all 5 sections
  3. Hit "Complete" on preferences screen
  4. `POST /api/v1/users` returns 500 after Sonnet call succeeds
  5. Server log confirms: Sonnet succeeded (HTTP 200 from Anthropic), then verifier pipeline query blew up on closed transaction

## Root cause (already diagnosed by main orchestrator)

Slice C added `@governed(feature="onboarding", cap=None)` on `run_onboarding_parse` in `server/app/ai/onboarding.py:66`. The `@governed` decorator's `_insert_governor_call(db, ...)` runs `db.commit()` on the passed AsyncSession.

`bootstrap_user` at `server/app/api/v1/users.py:558` wraps `run_onboarding_parse` + `_persist_bootstrap` in a SAVEPOINT via `async with db.begin_nested():` — so the "outer" session `db` is inside a nested transaction.

Passing that same `db` to `run_onboarding_parse(db=db)` at line 559 means the governor's `db.commit()` fires against the SAVEPOINT session and prematurely closes it. The subsequent `db.execute(...)` in `_run_verifier_pipeline` at line 109 hits "closed transaction".

## Fix template (already exists in same file)

Slice C fixed the identical bug pattern for the verifier fan-out. See `server/app/api/v1/users.py:210`:

```python
async def _bounded_verify(prop: SonnetSkillNodeProposal) -> None:
    # IMPORTANT: run_skill_node_verify is @governed(cap=None) — the decorator
    # calls _insert_governor_call(db, ...) which commits on the passed session.
    # Using the main 'db' (which is inside a SAVEPOINT) would commit the SAVEPOINT
    # prematurely. Instead, we use a FRESH AsyncSession so the governor_calls
    # INSERT+COMMIT runs on its own independent transaction.
    async with semaphore:
        async with AsyncSessionLocal() as verifier_db:
            verdict = await run_skill_node_verify(
                prop.name, candidate_names, db=verifier_db, user_id=user_id,
            )
```

Same fix needed at `server/app/api/v1/users.py:559`: wrap the `run_onboarding_parse` call in an `async with AsyncSessionLocal() as onboarding_gov_db:` block and pass that session as the `db=` argument. The Sonnet call result (`sonnet_output`) can still be used by the outer `_persist_bootstrap` — only the governor bookkeeping needs the fresh session.

## Regression test gap

The current onboarding integration test suite (`test_users_bootstrap_mocked.py` per STATE.md decision log 2026-07-20) **mocks the governor**, so the `_insert_governor_call` → `db.commit()` never runs against the SAVEPOINT session in test. That's why this shipped clean through CI. A regression test must exercise the real `@governed` path with a real SAVEPOINT — either by not mocking the governor, or by adding a targeted test that asserts the outer session is still open after `run_onboarding_parse` returns.

## Impact

**P0 — blocks every new user on prod.** No user can complete onboarding since Phase 4 Slice C deployed (2026-08-13). Existing users are unaffected (they already have onboarded_at set).

## Current Focus

- **hypothesis**: Governor `db.commit()` inside SAVEPOINT closes it; fix is to use a fresh AsyncSession for the governor bookkeeping at the `run_onboarding_parse` call site, mirroring the `_bounded_verify` precedent.
- **next_action**: Confirm hypothesis by reading `server/app/ai/governor.py` decorator body + `_insert_governor_call` implementation → verify the fix template applies cleanly at users.py:559 → write regression test that exercises real `@governed` path → apply fix → run test suite → deploy verification.

## Evidence

- 2026-08-16 (Railway deployment logs, `/tmp/railway-post-onboarding.txt`):
  - `POST /api/v1/users HTTP/1.1 500 Internal Server Error`
  - Anthropic `count_tokens` → 200 (first call timed out, retried, succeeded)
  - Anthropic `messages` → 200 (Sonnet call succeeded)
  - Then: `sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.`
- STATE.md decision log 2026-08-12: "Fresh AsyncSessionLocal() per verifier call in _bounded_verify — avoids @governed db.commit() closing SAVEPOINT prematurely (critical architectural fix)" — confirms the pattern was known during Slice C.
- STATE.md decision log 2026-08-12: "@governed proven at 3 call sites (breakdown, onboarding, skill_verify) — Slice A abstraction generalizes correctly" — this claim is FALSE for onboarding; it was never exercised end-to-end with the SAVEPOINT-wrapped call site.
- 2026-08-16 (source read of `server/app/ai/governor.py:127-152`): confirmed `_insert_governor_call` ends with `await db.commit()` on the passed session. Also `_update_success` (line 174) and `_update_error` (line 189) commit on the passed session. Hypothesis matches source exactly.
- 2026-08-16 (source read of `server/app/api/v1/users.py`): `run_onboarding_parse` is called at TWO sites, not one: `bootstrap_user` line 559 AND `re_run_onboarding` line 693. Both pass the SAVEPOINT-nested `db`. Both required the fix.
- 2026-08-16 (first fix attempt exposed a secondary FK issue): swapping to a fresh session for the governor caused `governor_calls_user_id_fkey` violations because the fresh session's connection cannot see the outer transaction's uncommitted users row. Resolution required committing the users upsert BEFORE opening the SAVEPOINT block — this satisfies the FK and preserves the fail-open contract (users row survives if Sonnet fails downstream).

## Eliminated

*(none yet — root cause is the leading hypothesis and matches evidence exactly)*

## Resolution

**Root cause:** Slice C added `@governed` to `run_onboarding_parse` but did not update the two call sites (`bootstrap_user` and `re_run_onboarding` in `server/app/api/v1/users.py`) to isolate the decorator's `db.commit()` from the caller's SAVEPOINT. When the governor committed on the caller-supplied session, the SAVEPOINT was destroyed and the next `db.execute(...)` in the verifier pipeline raised "Can't operate on closed transaction".

**Fix:** `server/app/api/v1/users.py`
1. New helper `_run_onboarding_parse_bounded(raw_input, user_id)` opens a FRESH `AsyncSessionLocal()` for the `@governed` decorator's bookkeeping (mirrors the existing `_bounded_verify` pattern at users.py:210).
2. `bootstrap_user` now commits the users-row upsert BEFORE entering the SAVEPOINT block, so the fresh governor session can see the users row via FK (`governor_calls.user_id → users.id`). Fail-open contract is unchanged: if Sonnet fails, the SAVEPOINT rolls back the graph work but the users row + preferences remain durably committed.
3. `re_run_onboarding` gets the same commit-before-SAVEPOINT + `_run_onboarding_parse_bounded` treatment for symmetry.

**Regression test:** New file `server/tests/test_onboarding_governor_savepoint.py` — three tests that patch only `app.ai.onboarding.get_client` and `app.ai.skill_verifier.get_client` (Anthropic HTTP layer) so the REAL `@governed` decorator runs against the SAVEPOINT session:
- `test_bootstrap_survives_real_governor_commit_inside_savepoint` — asserts 201 (not 500) on POST /api/v1/users with a live governor round-trip.
- `test_bootstrap_records_onboarding_governor_row` — asserts exactly one `governor_calls` row with `feature='onboarding'` and populated actuals — proves the decorator DID run and record cleanly.
- `test_rerun_survives_real_governor_commit_inside_savepoint` — guards the second call site (`re_run_onboarding`) against the same regression.

**Postmortem — why the mocked test missed this:** `test_users_bootstrap_mocked.py` monkey-patches `app.api.v1.users.run_onboarding_parse` directly, replacing the entire `@governed`-wrapped callable with a plain async fn. That bypasses the decorator entirely — `_insert_governor_call()` never runs, so the SAVEPOINT is never closed in test. This is why the bug reached prod despite green CI. The lesson: mocking at the decorated-function boundary hides decorator-vs-caller interaction bugs. Prefer mocking at the network boundary (`get_client`) so the real decorator + DB write path executes under test.

**Verification:**
- `pytest tests/test_onboarding_governor_savepoint.py` — 3/3 pass.
- `pytest tests/test_onboarding_verifier_pipeline.py tests/test_governor.py` — 22/22 pass (related-path regression check).
- Full suite delta with vs without fix: 17 pre-existing failures in both runs; +3 new passes (the regression tests). Zero new regressions introduced.

**Files changed:**
- `server/app/api/v1/users.py` (fix + comments)
- `server/tests/test_onboarding_governor_savepoint.py` (new regression test)

**Follow-up:**
- Audit remaining `@governed` call sites for the same pattern: `run_technique_breakdown` (breakdowns endpoint) uses `feature='breakdown', cap=3`; needs the same "does the caller wrap this in a SAVEPOINT?" check. Ticket TBD.
- Consider adding a lint / test that fails if ANY `@governed`-decorated function is called with a session that's inside `begin_nested()` — mechanical guard against this class regressing again.
- The existing `test_users_bootstrap_mocked.py` should be updated (separate cleanup task) to stop monkeypatching `run_onboarding_parse` directly — it currently gives false confidence.
