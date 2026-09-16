---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: 04.1-05 Task 2 eval RAN — disposition REVISE (25/30 gates). Wave 4 paused at Task 3 HUMAN CHECKPOINT (on-device walk), which is blocked on FLE-18 + the REVISE remediation.
last_updated: "2026-09-15T08:45:00.000Z"
last_activity: 2026-09-14 -- Phase 4.1 Wave 4 Task 1 shipped (513a1c4): eval_drills.py + skip-by-default pytest wrapper. Executor cleanly hit Task 2 checkpoint — no invented eval results. Worktree base drift did NOT recur this run (explicit EXPECTED_BASE anchor + guard in executor prompt worked). Prior: 2026-09-14 Wave 3 shipped via cherry-pick recovery after worktree base drift. 2026-09-14 Waves 1+2 executed.
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

Phase: 04.1 (AI Drills) — EXECUTING — Waves 1+2+3 + Wave 4 Task 1 shipped, Wave 4 Tasks 2+3 pending human checkpoints
Plan: 4 of 5 complete + Plan 05 Task 1 shipped (Plan 05 pending human eval + on-device verify)
### 2026-09-15 — FLE-3 release ops (push / deploy / build)

- **`main` pushed to `origin`.** 21 commits, `ee44834..0db7955`. Phase 4.1 no longer exists only on
  one disk. Two follow-up commits added: eval-harness fixes + raw eval output captured, and
  `RECOVERED-DESIGN.md` — which CLAUDE.md cites as the locked-stack authority and which was untracked
  and not gitignored despite being written to survive session crashes.
- **Railway deploy live.** The push auto-triggered it (GitHub-connected). `/healthz` 200. Verified it
  carries Phase 4.1 rather than a healthy old image: prod `/openapi.json` exposes `Drill` (10 fields),
  `BreakdownEnvelope`, and `SessionCreate.drill_index`.
- **EAS builds on commit `0db7955`,** both preview/internal:
  iOS `1c973cf7-835e-4a73-b907-90171bf74b6e` · Android `7d99fdbe-ded2-474a-a89d-d63726c850fb`.
  **PLAT-02 unblocked** — no Android build had ever run; the gap was a missing keystore, generated in
  the cloud by EAS (no local `keytool`). No Apple purchase needed: existing Individual team
  `7X5579VC7F`, cert/profile valid to 2027-06-23, one provisioned iPhone.
- **Device walk NOT performed** — needs a human on provisioned hardware. See Blockers.

Status: Wave 4 Task 1 (513a1c4) shipped 2026-09-14 — server/scripts/eval_drills.py (5 tuning-diverse songs: Lenny/Kashmir/Little Wing/Beat It/Wonderwall) + server/tests/test_drill_eval_live.py (skip-by-default via ANTHROPIC_EVAL_RUN=1). W5 fix respected: Postgres-only, exits with clear instructions if DATABASE_URL absent/non-postgres. Task 2 CHECKPOINT: awaiting user to run live eval (~$0.35 budget), fill EVAL-RESULTS.md with 5 songs × 6 landmines = 30-gate table, disposition SHIP/REVISE PROMPT/REVISE SCHEMA/DEFER. Task 3 CHECKPOINT: awaiting Railway deploy + fresh EAS iOS build (batched with 6 Phase 3+4 device-verify items per memory).
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

**FLE-18 (critical) — breakdown timeout default is below real call latency.**
`app/ai/breakdown.py:209` defaults `timeout_seconds=30.0`, doubled to 60s on the single retry;
`app/api/v1/breakdowns.py:226` does not override it. Real breakdowns measured **72.1s average**
(360.4s / 5 songs, `04.1-05-EVAL-RAW-OUTPUT.txt:2072`). Both attempts fall short, so every
**cache-miss** breakdown returns 503. Cached breakdowns still serve, which is why earlier phases
never hit it. Blocks device items 2 and 7. `app/ai/onboarding.py:84` has the same 30s default and
may share the defect.

**FLE-19 (medium) — governor cost audit trail is empty.**
`governor_calls.dollars_estimated` / `dollars_actual` are declared at `app/models/db.py:347-348`
but never written anywhere in `app/`. The eval spent ~$0.49 of real tokens and the governor still
summed `dollars_actual` to $0.00. Enforcement is unaffected (the per-user cap is count-based and
the $20/mo ceiling is the Anthropic Console hard cap), but the app has no internal spend visibility.

**Plan 05 Task 2 disposition = REVISE (not SHIP).** 25/30 landmine gates pass. L5 (ordering) fails
on 3/5 songs and L1 (snippet isolation) fails on Lenny. See `04.1-05-EVAL-RESULTS.md`. Task 3's
on-device walk should not start until the REVISE remediation and FLE-18 land — otherwise it burns
a device session and 3-per-7d breakdown quota grading drills that are already known-bad.

**Seven device-verify items remain UNVERIFIED.** Builds exist and are installable; nobody has walked
them. Checklist: `.planning/phases/04.1-ai-drills/04.1-05-DEVICE-VERIFICATION.md`.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260908-01 | Wire useBreakdown hook + call from breakdown screen (closes Slice B deviation §3) | 2026-09-08 | 3c67c77 | [260908-01-wire-usebreakdown-hook](./quick/260908-01-wire-usebreakdown-hook/) |
| 260909-01 | Tuning-aware Sonnet + mobile tuning label (closes Task #16 tuning-quality investigation) | 2026-09-09 | af3fdef | [260909-01-sonnet-tuning-awareness](./quick/260909-01-sonnet-tuning-awareness/) |
| 260910-01 | Mobile refresh affordances — pull-to-refresh + dev Settings button + E♭/D standard KNOWN_TUNINGS entries | 2026-09-10 | 1e3a12f | [260910-01-mobile-refresh-batch](./quick/260910-01-mobile-refresh-batch/) |
| 260915-01 | FLE-8 drill bank — migration 0006 (drills + drill_attempts + drill_dedupe_queue), rapidfuzz dedup-on-write, per-drill tempo/reps history. **Backfill deliberately excluded — blocked on drill quality (04.1-05 REVISE)** | 2026-09-15 | _pending_ | [260915-01-drill-bank-schema](./quick/260915-01-drill-bank-schema/) |
| 260915-ksk | FLE-5 metronome — absolute-deadline (drift-free) engine, expo-audio click with voice pooling, wired to the drill tempo ladder. 76 tests green. **Hold-tempo-on-hardware unverified — needs the FLE-42 build + a human device walk** | 2026-09-16 | f84db65 | [260915-ksk-metronome-drill-tempo-ladders](./quick/260915-ksk-metronome-drill-tempo-ladders/) |

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-09-14 (Phase 4.1 Wave 3 + Wave 4 Task 1 shipped)
Stopped at: Wave 4 paused at Task 2 human checkpoint. Task 1 shipped as 513a1c4 (eval script + pytest wrapper on main). Prior Wave 3 shipped as a34d1d1→e4d7899 (5 commits + tracking). To resume Wave 4: (1) run live eval via `cd server && ANTHROPIC_EVAL_RUN=1 ANTHROPIC_API_KEY=... DATABASE_URL=postgresql+asyncpg://... python -m scripts.eval_drills` (~$0.35 budget); (2) fill .planning/phases/04.1-ai-drills/04.1-05-EVAL-RESULTS.md with 30-gate landmine table + disposition; (3) commit results doc; (4) if SHIP disposition, deploy Railway server + build EAS iOS + do on-device verify (batch with 6 pending Phase 3+4 items); (5) commit SUMMARY.md; (6) re-enter via `/gsd:execute-phase 04.1 --wave 4` or `/gsd:progress` to finalize phase verification.
Resume file: .planning/phases/04.1-ai-drills/04.1-05-PLAN.md (Wave 4 = manual eval + 2 human checkpoints; Task 1 shipped; Tasks 2+3 pending)
