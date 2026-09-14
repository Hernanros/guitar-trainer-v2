---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: 04.1-04-PLAN.md complete — Wave 3 (mobile drill-detail screen) shipped via cherry-pick recovery after worktree base-drift incident. Wave 4 (04.1-05 manual eval) pending.
last_updated: "2026-09-14T00:00:00.000Z"
last_activity: 2026-09-14 -- Phase 4.1 Wave 3 (Plan 04) shipped via cherry-pick recovery. Executor spawned in worktree isolation forked off ancient base (ee44834 pre-Phase-4.1) — worktree base drift caused a would-delete-30-files merge blocker. Recovery: reset main to 9522dea (dropped stray executor commit on main), cherry-picked 5 worktree commits onto proper Wave 2 base, resolved 1 comment-only conflict in [songId].tsx Task 5. tsc clean. Prior: 2026-09-14 Phase 4.1 Waves 1+2 executed (8 commits shipped). 2026-09-14 inserted Phase 4.1 + captured CONTEXT + RESEARCH + 5 PLAN.md files.
progress:
  total_phases: 5
  completed_phases: 4
  total_plans: 16
  completed_plans: 16
  percent: 81
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-07-15)

**Core value:** One real song, decomposed by an AI teacher, every day — so practicing feels like playing.
**Current focus:** Phase 03 — AI Teacher & Song of the Day

## Current Position

Phase: 04.1 (AI Drills) — EXECUTING — Waves 1+2+3 shipped, Wave 4 pending
Plan: 4 of 5 complete (Plans 01, 02, 03, 04 shipped; Plan 05 pending)
Status: Wave 3 (04.1-04 mobile drill-detail screen) shipped 2026-09-14 via cherry-pick recovery — see "Worktree base-drift incident" below. Tree state: nested Expo Router /breakdown/[songId]/drill/[drillIndex] route live, useSubmitDrillRating hook + 4 mutation tests, drill.test.tsx pure-fn helpers 16 tests (B2), B1 server-derived drillRatedToday on parent breakdown screen (deletes pre-revision mutation-cache subscribe pattern). Only pre-existing app-tabs.web.tsx tsc error remains (Phase 1 orphan, deferred). Deployments PENDING: Railway (server changes) + EAS iOS (mobile drill UI + drill-detail screen) BEFORE Wave 4 can run. Wave 4 = Plan 04.1-05 (manual eval loop against LIVE Anthropic ~$0.10-0.30 + 2 blocking checkpoints).
Last activity: 2026-09-14 -- Phase 4.1 Wave 3 shipped (Plan 04 via cherry-pick recovery). Prior: 2026-09-14 Waves 1+2 executed (8 commits shipped). 2026-09-14 inserted Phase 4.1 + captured CONTEXT + RESEARCH + 5 PLAN.md files.

## Worktree base-drift incident (2026-09-14)

Executor spawned via `Agent(isolation="worktree")` for Plan 04.1-04. The isolated worktree branch forked off `ee44834` (README commit from **before** Phase 4.1 was inserted) instead of current `9522dea`. Result: worktree branch had Task 2-5 commits but no Wave 2 files at all. Executor's Task 5 "brought in Wave 2 files" by re-creating them (identical content), and one Task 2 attempt accidentally committed to the main worktree before switching. If the standard workflow cleanup had merged the worktree branch back, its deletion guard would have blocked (would-delete 30 files / ~7000 lines of Waves 1+2). Recovery via cherry-pick + `git reset --hard 9522dea`. Root cause: worktree fork base not aligned with orchestrator HEAD. Future-infra: verify worktree base = orchestrator HEAD before dispatching executor; guard-clause for base-diff exceeding N commits behind.

Progress: [██████████████░] Phase 1 iOS ✓, Phase 2 iOS ✓, Phase 3 ✓, Phase 4 ✓, Phase 4.1 Waves 1-3 ✓ (Wave 4 pending deploys), Phase 5 after

## Performance Metrics

**Velocity:**

- Total plans completed: 4
- Average duration: n/a
- Total execution time: 0h

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 02 | 4 | - | - |

**Recent Trend:**

- Last 5 plans: n/a
- Trend: n/a

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Design phase (2026-07-14, recovered): Mobile-first Expo + RN, music-centric (not exercise-centric), MVP is "Prove the loop" — self-report only, no mic
- Design phase (2026-07-14, recovered): 4-layer AI cost guardrails, $20/month POC cap, single-module server-side governor
- Design phase (2026-07-14, recovered): Skill graph is a 3-level DAG with 5-bpm tempo bins, deterministic writes only, nightly 5% decay >7 days
- Roadmap (2026-07-15): 5 vertical-MVP phases, each delivering an end-to-end user capability; every phase carries `Mode: mvp`
- Phase 1 context (2026-07-15): Full Phase 3-ready payload shape from day one (dummy content), Pydantic → OpenAPI → generated TS types, semantic music JSON for tab/chords, DB-backed trivial selector at `GET /api/v1/song-of-day`
- Fletcher product identity (2026-07-19): Product named Fletcher (JK Simmons / Whiplash reference); "the teacher Fletcher should have been" positioning — reinforcing not critical, celebrates hard-won achievements by grinders (not savants). Fletcher-vocabulary ("Rushing", "Dragging", "Not my tempo") used as diagnosis + coaching (sharp word + next step). Design brief at `.planning/design/fletcher-identity.md`.
- Phase 2 context (2026-07-20): 5-section wizard (Welcome/Play/Working/Aspire/Preferences), MMKV wizard state + device UUID identity (resolves multi-user seams — users table + X-User-ID header, no auth), free-text + Sonnet batch parse on Complete (~$0.02–$0.05/user), fixed root taxonomy, normalized per-user tree schema, all mastery starts at 0 (deterministic writes principle preserved)
- Phase 4 Slice A (2026-07-30): Cap counts ALL views (cache-hit and cache-miss) against 3/7d limit — cap-check runs before cache read in breakdowns endpoint; record governor_calls row even for cache hits so the rolling window is accurate
- Phase 4 Slice A (2026-07-30): ContextVar approach for call_id passthrough — single-worker (uvicorn --workers 1) means each async task inherits its own ContextVar copy at creation time, no cross-task leakage; simpler than explicit kwarg threading
- Phase 4 Slice A (2026-07-30): record_estimate / record_actuals open a fresh AsyncSessionLocal() session — avoids cross-session state with the endpoint's session; isolated UPDATE commits are safe under single-worker posture
- Phase 4 Slice B (2026-07-30): COUNT + MIN queries (not now()+7d shortcut) for breakdown_quota — test_breakdown_quota_resets_at_matches_oldest_plus_7d enforces this
- Phase 4 Slice B (2026-07-30): schema.d.ts manually updated (server not running locally for codegen); BreakdownQuota typed correctly from Pydantic model
- Phase 4 Slice B (2026-07-30): today-song invalidation in breakdown screen uses useEffect on data-availability change to avoid side effects in render body
- Phase 4 Slice C (2026-08-12): Form-encoded POST body for /admin/curator/action (not JSON) — matches D-11 browser-tool posture; CuratorActionBody Pydantic model kept as JSON contract documentation
- Phase 4 Slice C (2026-08-12): Fresh AsyncSessionLocal() per verifier call in _bounded_verify — avoids @governed db.commit() closing SAVEPOINT prematurely (critical architectural fix)
- Phase 4 Slice C (2026-08-12): @governed proven at 3 call sites (breakdown, onboarding, skill_verify) — Slice A abstraction generalizes correctly
- Phase 4 Slice D (2026-08-12): 20h debounce guard chosen over 6h for APScheduler catch-up window (T-04-04-02 mitigation)
- Phase 4 Slice D (2026-08-12): decay_all_nodes does NOT re-raise on exception — APScheduler continues; fresh-session error path commits audit row
- Phase 4 Slice D (2026-08-12): scheduler.add_job uses id='decay_all_nodes' + replace_existing=True — idempotent startup (safe for test contexts)
- Phase 4 Slice D (2026-08-12): Phase 4 COMPLETE — all 6 req IDs (COST-01/02/03/04, SKILL-04, SKILL-05) satisfied

### Pending Todos

None yet.

### Blockers/Concerns

None yet.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260908-01 | Wire useBreakdown hook + call from breakdown screen (closes Slice B deviation §3) | 2026-09-08 | 3c67c77 | [260908-01-wire-usebreakdown-hook](./quick/260908-01-wire-usebreakdown-hook/) |
| 260909-01 | Tuning-aware Sonnet + mobile tuning label (closes Task #16 tuning-quality investigation) | 2026-09-09 | af3fdef | [260909-01-sonnet-tuning-awareness](./quick/260909-01-sonnet-tuning-awareness/) |
| 260910-01 | Mobile refresh affordances — pull-to-refresh + dev Settings button + E♭/D standard KNOWN_TUNINGS entries | 2026-09-10 | 1e3a12f | [260910-01-mobile-refresh-batch](./quick/260910-01-mobile-refresh-batch/) |

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-09-14 (Phase 4.1 Wave 3 shipped via cherry-pick recovery)
Stopped at: Wave 3 (Plan 04.1-04 mobile drill-detail screen) shipped. 5 commits on main (a34d1d1 Task 2, f0058a4 Task 3, 0bb56c2 Task 4, 30950b6 Task 5, e4d7899 SUMMARY). Nested Expo Router drill route + useSubmitDrillRating hook + drill.test.tsx 16 pure-fn tests + B1 server-derived drillRatedToday on parent breakdown screen. Recovery from worktree base-drift incident (see incident block in Current Position). tsc clean modulo pre-existing Phase 1 app-tabs.web.tsx orphan. jest not runnable locally (jest-expo not installed in mobile/node_modules — pre-existing env issue, declared in package.json but absent).
Resume file: .planning/phases/04.1-ai-drills/04.1-05-PLAN.md (Wave 4 = manual eval loop against live Anthropic + 2 blocking checkpoints; BLOCKED on Railway deploy of server changes + fresh EAS iOS build)
