---
status: resolved
trigger: "Re-run onboarding on prod (eacfcb9) 500s at commit-time with FK violation on skill_nodes.parent_id. Verifier pipeline drops a sub-level node (verdict='no' or deferred_overflow), but the sub's leaf children still get inserted with parent_id pointing to the dropped sub — orphan leaves violate fk_skill_nodes_parent at commit."
created: 2026-08-17
updated: 2026-08-17
phase: 04
milestone: v1.0
---

# Debug Session: onboarding-fk-orphan-leaves

## Observed facts (from Railway logs)

**Stack trace signature** (from `/tmp/railway-rerun.txt`):
```
asyncpg.exceptions.ForeignKeyViolationError: insert or update on table "skill_nodes"
  violates foreign key constraint "fk_skill_nodes_parent"
DETAIL:  Key (parent_id)=(b564b549-47d8-4fb1-8c34-c5674dc1b4c4)
       is not present in table "skill_nodes".
```

Fires at commit time (SQLAlchemy `_commit_impl`), so the whole SAVEPOINT rolls back.

**Preceding log context** confirms the verifier ran and dropped nodes:
```
WARNING:app.api.v1.users:Skipping song_skill for dropped proposal
  temp_id 'leaf-open-chord-voicings-100' in song 'Big Love'.
```

The `_DROPPED_ATTR` skip is being honored for `song_skills` junction but NOT for the skill_nodes with orphan `parent_id` references.

## Root cause (code-verified)

Reading `server/app/api/v1/users.py:425-457` (the skill_nodes insert loop in `_persist_bootstrap`):

```python
for prop in ordered:
    if getattr(prop, _DROPPED_ATTR, False):
        continue                                    # ← correctly skips dropped node

    parent_uuid = temp_to_uuid.get(prop.parent_temp_id) if prop.parent_temp_id else None
    # ...
    db.add(SkillNode(
        id=temp_to_uuid[prop.temp_id],
        parent_id=parent_uuid,                       # ← BUG: parent may be dropped
        ...
    ))
```

`temp_to_uuid` was populated for ALL proposals at line 394 (`{p.temp_id: uuid4() for p in output.skill_graph}`) — including proposals that would later be dropped by the verifier. So when a leaf's `parent_temp_id` points to a dropped sub, `temp_to_uuid.get(...)` still returns a valid UUID — but that UUID never gets a row inserted (parent was dropped and `continue`'d). PostgreSQL only checks the FK at commit (per migration 0002, `fk_skill_nodes_parent` is `DEFERRABLE INITIALLY DEFERRED`) → the whole transaction blows up at commit.

**Scope:** only affects sub-level proposals with children. Roots aren't verified (D-08). Leaves have no children so cascade doesn't apply to them. The bug fires only when Sonnet emits a sub that the verifier drops (`verdict='no'` OR `deferred_overflow`) while that sub has leaf children.

**Not caused by any of today's fixes.** Present since Phase 4 Slice C introduced the verifier pipeline. Latent until now — most Sonnet outputs happen to not have dropped subs with kids.

## Fix scope (user chose: Cascade drop)

**Primary fix in `_persist_bootstrap` (server/app/api/v1/users.py):**

Between the verifier pipeline (line 418) and the skill_nodes insert loop (line 425), add a **cascade-drop pass**:

1. Build a set `dropped_temp_ids` = temp_ids of all proposals with `_DROPPED_ATTR=True`.
2. Iterate the graph: any leaf whose `parent_temp_id in dropped_temp_ids` gets marked with `_DROPPED_ATTR=True`.
3. Log each cascade drop as WARNING with parent + leaf names.
4. (Belt-and-suspenders: run the check recursively so if sub→sub trees ever exist, cascades propagate. Not needed for current 3-level DAG but future-proofs.)

The existing insert loop and song_skills junction loop already honor `_DROPPED_ATTR` — the cascade pass just extends the drop set. No changes needed at insert sites.

**Regression tests** — new `server/tests/test_persist_bootstrap_cascade_drop.py`:
- Sub dropped by verifier `verdict='no'` → its leaves are cascade-dropped → no FK violation, transaction commits, leaves + song_skills correctly absent.
- Sub dropped as `deferred_overflow` → same behavior.
- Non-orphan leaves (parent kept) → still inserted normally.
- Song_skills for cascade-dropped leaves are skipped (existing behavior extended).

## Also relevant — 3 unpushed fixes will ship together

Local `main` is ahead of origin by 1 commit (`33d78ac` — song-of-day nullable metadata fix). When we push the cascade fix, both go out. That means:
- `33d78ac` fix (SongResponse Optional + Sonnet metadata population)
- THIS session's fix (cascade drop for orphan leaves)

Both are P0 hotfixes. Bundling saves one Railway redeploy cycle.

## User recovery path (post-fix deploy)

User `28c6b9ea-...` is in a weird partial state right now:
- `onboarded_at` still set from earlier successful eacfcb9 onboarding
- 2 songs (from that onboarding) with NULL metadata → song-of-day still 500s until `33d78ac` deploys
- Skill_nodes from the successful eacfcb9 onboarding are still present (the FAILED re-run rolled back cleanly)

After both fixes deploy, recovery:
- Force-quit + relaunch the app → routes to Today tab (onboarded_at set)
- Today tab now succeeds (33d78ac makes SongResponse tolerate NULL metadata + placeholder breakdown)
- User can then re-run onboarding from Settings to get properly-populated songs (cascade fix ensures re-run doesn't crash even if verifier drops a sub)

## Deferred to next EAS batch (mobile side)

Wizard preferences screen (`mobile/src/app/onboarding/preferences.tsx`) has no error-recovery affordance: when the mutation fails, "Fletcher lost the thread" shows but there's no button to retry, navigate back, or exit the wizard. Requires an EAS rebuild to fix on device. Capture as follow-up.

## Current Focus

- **hypothesis (evidence-backed):** Verifier drops sub-nodes but their leaf children still get inserted with parent_id pointing to the dropped sub's temp_to_uuid entry; FK deferred to commit; whole tx blows up. Fix: cascade-drop leaves whose parent was dropped, before the skill_nodes insert loop.
- **next_action:** Implement cascade-drop pass in `_persist_bootstrap` → add regression tests → verify existing verifier pipeline tests still pass → commit → push (bundled with 33d78ac).

## Evidence

- Railway `/tmp/railway-rerun.txt` — ForeignKeyViolationError on `fk_skill_nodes_parent` at commit; specific orphan parent_id = `b564b549-47d8-4fb1-8c34-c5674dc1b4c4`.
- WARNING log immediately before crash confirms verifier dropped `leaf-open-chord-voicings-100` — proving verifier drops are happening in this transaction.
- Code read (`users.py:394`, `users.py:425-457`) confirms `temp_to_uuid` includes dropped proposals AND the insert loop doesn't check if `parent_uuid` corresponds to a dropped proposal.
- FK deferred nature (Alembic 0002 `fk_skill_nodes_parent`) explains why crash happens at commit, not at individual `db.add()` calls.

## Eliminated

- ~~"eacfcb9 dedup fix regressed"~~ — DISPROVEN. Sonnet call succeeded, verifier ran, coalesce logic never reached this failure path. This is deeper in `_persist_bootstrap`.
- ~~"def6ba8 SAVEPOINT fix regressed"~~ — DISPROVEN. The FK fires at commit AFTER the pipeline completed cleanly.
- ~~"33d78ac song-of-day fix related"~~ — DISPROVEN. 33d78ac is unpushed and unrelated to skill_nodes FK.

## Resolution

**Status:** RESOLVED — cascade-drop pass shipped + 5 regression tests green.

**Root cause (confirmed):** `_persist_bootstrap` populated `temp_to_uuid` for every Sonnet proposal (including ones the verifier would later drop). The skill_nodes insert loop correctly skipped `_DROPPED_ATTR=True` rows via `continue`, but children of dropped subs still inserted with `parent_id = temp_to_uuid[dropped_parent_temp_id]` — a valid UUID pointing at a row that never existed. Because `fk_skill_nodes_parent` is `DEFERRABLE INITIALLY DEFERRED` (alembic 0002), Postgres detected the orphan only at COMMIT → whole SAVEPOINT rolled back → endpoint 500.

**Fix (applied to `server/app/api/v1/users.py` between `_run_verifier_pipeline` and the skill_nodes insert loop):** cascade-drop pass. Builds `dropped_temp_ids` from the initial verifier drops, then loops to a fixed point marking any proposal whose `parent_temp_id` is in the dropped set. Each cascade drop emits a WARNING with the child + parent names. Existing insert loop and song_skills junction loop already honor `_DROPPED_ATTR`, so no changes were needed at insert sites.

**Regression tests (`server/tests/test_persist_bootstrap_cascade_drop.py`, 5 new tests):**
1. `test_cascade_drop_sub_verdict_no_drops_leaf_children` — Sub dropped by `verdict='no'` → both leaves cascade-drop; only the 6 roots remain; response 201.
2. `test_cascade_drop_deferred_overflow_drops_leaf_children` — Fan-out cap forces a sub into `deferred_overflow` while its leaf survived verification with `verdict='yes'`; cascade must drop the orphaned leaf. Fixture is order-sensitive (9 filler subs → target leaf in slot 10 → target sub in overflow slot 11).
3. `test_no_cascade_when_sub_kept` — Guardrail: `verdict='yes'` sub keeps its leaves; cascade must not over-drop.
4. `test_multi_drop_cascade_catches_all_orphans_across_subtrees` — Two independent dropped subtrees; all 4 orphaned leaves cascade.
5. `test_song_skills_skipped_for_cascade_dropped_leaves` — Song_skills junction loop skips cascade-dropped leaves via existing `_DROPPED_ATTR` check (song row itself still inserts).

**Regression-safety proof:** with the cascade block reverted, 4 of 5 tests fail with the exact prod signature — `asyncpg.exceptions.ForeignKeyViolationError: ... violates foreign key constraint "fk_skill_nodes_parent"`. Restoring the fix takes all 5 back to green.

**Neighboring test suite:** `test_onboarding_verifier_pipeline.py` (10), `test_onboarding_dup_song.py` (3), `test_onboarding_governor_savepoint.py` (3) — 17/17 green after fix. Full server suite: 18 failed + 10 errors, but all in unrelated files (`test_alembic_0003.py`, `test_breakdowns_mocked.py`, `test_song_of_day_quota.py`, `test_today_song_selector.py`, `test_users_bootstrap_mocked.py`) — matches the pre-existing 17-18 failure baseline (fixture drift, schema drift, test-pollution). Zero new regressions.

**Files touched:**
- `server/app/api/v1/users.py` — cascade-drop pass in `_persist_bootstrap` between lines 528 and 567 (36 new lines including comment block; see git diff for exact insertion).
- `server/tests/test_persist_bootstrap_cascade_drop.py` — new file, 5 tests, ~540 lines.

**Not touched (per orchestrator notes):**
- No DB cleanup for the affected user `28c6b9ea-...` — natural recovery after this fix + `33d78ac` deploy together.
- No mobile wizard error-recovery UX — captured as EAS-batch follow-up per `project_eas_budget.md`.

**Not pushed** — orchestrator will bundle push of local `33d78ac` + this cascade-drop commit together after review.
