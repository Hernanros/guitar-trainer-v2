---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: 04-04-PLAN.md complete — Slice D (nightly decay scheduler + APScheduler + decay_runs audit) shipped; Phase 4 COMPLETE
last_updated: "2026-09-10T00:00:00.000Z"
last_activity: 2026-09-10 -- Completed quick task 260910-01: mobile refresh affordances (pull-to-refresh + dev button + E♭/D standard KNOWN_TUNINGS). Also 2026-09-10 corrected Lenny attribution (ffdf2e7 — was Open E, actually E♭ standard). Prior: 2026-09-09 260909-01 tuning-aware Sonnet initial ship; 2026-09-08 9 fixes end-to-end.
progress:
  total_phases: 5
  completed_phases: 4
  total_plans: 16
  completed_plans: 15
  percent: 75
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-07-15)

**Core value:** One real song, decomposed by an AI teacher, every day — so practicing feels like playing.
**Current focus:** Phase 03 — AI Teacher & Song of the Day

## Current Position

Phase: 04 (Cost Governor & Node Verification) — COMPLETE
Plan: 4 of 4 complete (Slice D shipped — nightly decay scheduler + decay_runs audit)
Status: Phase 4 complete — all 6 req IDs satisfied (COST-01/02/03/04, SKILL-04, SKILL-05)
Last activity: 2026-09-09 -- Completed quick task 260909-01: tuning-aware Sonnet + mobile tuning label (commit: af3fdef); Task #16 investigation closed. Prior: 2026-09-08 shipped 9 fixes (route-leak, schema regen, null-guards, useBreakdown wire, re-run FK fix, Sonnet skill_graph hardening, reroll bank_source fix, Grace-E). 2026-08-12 Phase 4 Slice D shipped.

Progress: [██████████████░] Phase 1 iOS ✓, Phase 2 iOS ✓, Phase 3 ✓, Phase 4 ✓, Phase 5 next

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

Last session: 2026-09-09 (Sonnet tuning-awareness quick task shipped)
Stopped at: Shipped af3fdef (tuning-aware Sonnet + mobile TabNotation tuning label) closing Task #16. Server change needs Railway deploy; mobile change needs new EAS build. 2026-09-08 previously shipped 9 commits + verified core loop end-to-end on device. Open: Task #7 cross-day rating (needs midnight rotation), Task #18 sparse skill graph investigation, Task #9 orphaned app-tabs cleanup, Items 4/5/6 device-verify (need new EAS build).
Resume file: .planning/HANDOFF.json (needs update — next_action should shift to "trigger EAS build + verify tuning label + Item 4/5/6 device walk")
