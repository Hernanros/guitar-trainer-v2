# Roadmap: Guitar Trainer v2

## Overview

Five vertical-MVP phases that stand up the song-of-the-day loop end-to-end. Phase 1 lands a running iOS + Android app talking to a Railway-hosted FastAPI + Postgres with an offline MMKV cache and a hardcoded "today" song — the empty loop the user can already open every morning. Phase 2 replaces the hardcoded seed with real onboarding and a persistent 3-level DAG skill graph. Phase 3 wires the AI teacher (Sonnet 4.6) into song-of-the-day selection, tab/chord rendering via react-native-svg, and deterministic self-report writes. Phase 4 hardens the money side — single-module LLM governor, per-user caps, quota UI, Console cap — and the graph side — dedup + Sonnet verifier for new nodes with curator queue, nightly 5% decay. Phase 5 fills out Library + Toolkit (metronome + tuner) so the app is shareable day one.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation & Empty Loop** - Expo/RN + EAS on iOS + Android, FastAPI on Railway + Postgres, MMKV cache, three-tab shell wired to a hardcoded "today" song
- [x] **Phase 2: Onboarding & Initial Skill Graph** - 5–8 min onboarding, Settings re-run, 3-level DAG skill graph with 5-bpm bins seeded from onboarding answers (completed 2026-07-28)
- [ ] **Phase 3: AI Teacher & Song of the Day** - Sonnet 4.6 breakdown, react-native-svg tab + chord diagrams, deterministic song selection, self-report rating writes back to graph
- [x] **Phase 4: Cost Governor & Node Verification** - Single-module LLM governor, per-user rate caps, quota UI, $20 Console cap, embed-dedup + Sonnet verifier for new nodes + curator queue, nightly 5% decay (completed 2026-08-12)
- [ ] **Phase 4.1: AI Drills** (INSERTED 2026-09-14) - Sonnet emits 2-4 named drills per breakdown with target_skill_node + tempo ladder + rep count; mobile renders drill list + focused drill screen; per-drill rating writes to skill_nodes.mastery. Recovers the drills-in-service-of-songs vision from V1's failure diagnosis.
- [ ] **Phase 5: Library, Toolkit & Polish** - Library tab (search/add/remove), Toolkit (metronome + chromatic tuner), shareability polish

## Phase Details

### Phase 1: Foundation & Empty Loop

**Goal**: A user can install the app on iOS or Android via EAS, open it, and see today's (hardcoded) song rendered by the three-tab shell — with the FastAPI + Postgres backend on Railway serving the payload and MMKV caching it for offline reads.
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: PLAT-01, PLAT-02, PLAT-03, PLAT-04
**Success Criteria** (what must be TRUE):

  1. iOS EAS build installs on device and launches to the Today tab
  2. Android EAS build installs on device and launches to the Today tab
  3. Today tab shows a hardcoded song-of-the-day fetched from the Railway-hosted FastAPI backend backed by a persistent Postgres instance
  4. With the device in airplane mode, reopening the app still renders yesterday's cached "today" payload from MMKV
  5. Three-tab shell (Today · Library · Toolkit) navigates without crashing; Library and Toolkit render placeholder screens

**Plans**: 4 plans in 3 waves

Plans:

**Wave 1**

- [x] 01-01-PLAN.md — Walking Skeleton: monorepo scaffold + FastAPI server + Pydantic models + DB-backed endpoint + Expo app + codegen loop *(2026-07-16)*

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-02-PLAN.md — SVG rendering: ChordDiagram + TabNotation components + Today tab wired to full breakdown payload *(2026-07-16)*
- [x] 01-03-PLAN.md — MMKV offline persistence: PersistQueryClientProvider + MMKV persister at root layout *(2026-07-16)*

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 01-04-PLAN.md — Ship to devices: Railway deploy ✓ + iOS EAS preview build ✓ + Android EAS preview build ⏸ deferred *(2026-07-19; Android pending device/emulator setup — PLAT-02)*

**Cross-cutting constraints:**

- D-01 (full Phase 3-ready payload shape) + D-02 (Pydantic → generated TS types) — enforced across 01-01 and 01-02
- MMKV v4 requires a dev-client build (Expo Go cannot load it) — runtime verification of 01-03 lives in 01-04 after the dev-client installs

### Phase 2: Onboarding & Initial Skill Graph

**Goal**: A new user completes the 5–8 minute onboarding once, the server persists a 3-level DAG skill graph with 5-bpm tempo bins seeded from those answers, and the user can re-run onboarding from Settings.
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: ONB-01, ONB-02, ONB-03, ONB-04, SKILL-01, SKILL-02
**Success Criteria** (what must be TRUE):

  1. First launch (no prior onboarding) routes the user into an onboarding flow that captures songs they can play, are working on, and aspire to
  2. Onboarding also captures style/genre tags and session preferences (session length + retention format: streak / weekly digest / monthly milestone)
  3. On onboarding completion, an initial 3-level DAG skill graph (root domains → sub-domains → leaf skills with 5-bpm tempo bins) is persisted server-side for the user
  4. Settings screen exposes a "Re-run onboarding" action that resets and re-seeds the skill graph
  5. Skill graph state survives app restart and is fetchable via the FastAPI backend

**Plans**: 4 plans in 3 waves

Plans:

**Wave 1**

- [x] 02-01-PLAN.md — User identity foundation: Alembic 0002 (users + song user-scoping + song_skills + skill_nodes) + stub bootstrap endpoint + X-User-ID plumbing + mobile MMKV UUID + root-layout onboarded-check redirect

**Wave 2** *(parallel — blocked on Wave 1)*

- [x] 02-02-PLAN.md — Fletcher-voiced wizard: 5-section onboarding shell + intro card + song input area + session-length chips + retention radio + MMKV wizard-state resume-mid-flow + Complete-tap wired to bootstrap
- [x] 02-03-PLAN.md — AI + skill graph bootstrap: server/app/ai/ module + Sonnet 4.6 structured tool-use + atomic songs+song_skills+skill_nodes persist + fail-open with 6-root bootstrap graph + GET /skill-graph + POST /re-run endpoints

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 02-04-PLAN.md — Settings + re-run + polish: Settings as 4th tab + Fletcher confirm dialog + useSkillGraph + useUserReonboard hooks + Fletcher loader rotation (3 copy variants) + fail-open card + end-to-end device walkthrough

### Phase 3: AI Teacher & Song of the Day

**Goal**: The user opens the Today tab, sees exactly one Song of the Day chosen deterministically from their current skill graph state, gets a Sonnet 4.6 technique breakdown rendered as tab + chord diagrams via react-native-svg, and submits a self-report rating that writes back into the graph via deterministic rules.
**Mode:** mvp
**Depends on**: Phase 2
**Requirements**: SOTD-01, SOTD-02, SOTD-03, SOTD-04, SOTD-05, SKILL-03
**Success Criteria** (what must be TRUE):

  1. Today tab shows exactly one Song of the Day, chosen by a deterministic selector reading current skill graph state and user preferences
  2. Tapping the song loads a Sonnet 4.6–produced breakdown of the technique required
  3. Breakdown renders playable material as tab notation via react-native-svg
  4. Breakdown renders chord diagrams via react-native-svg
  5. After practicing, the user submits a self-report rating in one tap and the skill graph is updated via deterministic rules (no LLM in the write path)
  6. Tomorrow's Song of the Day reflects yesterday's rating (verified by rating a session and checking the next day's selection)

**Plans**: 4 plans in 3 waves + gap-closure (vertical slices under MVP mode)

Plans:

**Wave 1**

- [x] 03-01-PLAN.md — Slice A · Deterministic Song of the Day: Alembic 0003 (song_catalog seeded with 10 hand-curated songs + user_sessions + breakdown_generated_at) + 75/25 CTE selector with setseed hashtext + reroll endpoint (one per day, DB-enforced) + X-Timezone-Offset header + SongOfDayCard + FromTheBankTag + FletcherLoader + Today tab wired to real selector *(executed with 4 known gaps)*

**Wave 2** *(blocked on Wave 1 — shares Song.breakdown_generated_at, extends TodaySongResponse)*

- [x] 03-02-PLAN.md — Slice B · Sonnet Breakdown + Rendering: schema spike test (Q1) + run_technique_breakdown (mirrors run_onboarding_parse) + GET /songs/{id}/breakdown with cache-forever short-circuit + TabNotation multi-measure horizontal-scroll refactor with React.memo + BreakdownErrorCard + /breakdown/[songId] expo-router route + Today CTA navigation *(executed)*

**Wave 3** *(blocked on Wave 1 for user_sessions schema; independent of Wave 2 rendering but sequential in user flow)*

- [x] 03-03-PLAN.md — Slice C · Rating write + mastery + already-rated states: POST /api/v1/sessions atomic write (single AsyncSession.begin, LEAST/GREATEST SQL clamp, explicit updated_at bump, IntegrityError→409, no LLM) + useSubmitRating mutation with skill-graph invalidation only + RatingPills + AlreadyRatedCard + SongOfDayCard already-rated variant + tomorrow's-pick reflects yesterday's rating *(executed)*

**Gap Closure** *(closes Wave 1 gaps: selector CTE wiring, /reroll endpoint, useReroll hook, bank_source propagation)*

- [x] 03-04-PLAN.md — Gap-closure: wire select_today_song CTE into GET handler, add POST /today-song/reroll with DB-enforced one-per-day, restore useReroll hook on mobile, propagate bank_source from selector (Revision B) *(2026-07-29)*

**Cross-cutting constraints:**

- SKILL-03 (no LLM in write path) — grep-verified in Slice C (`server/app/api/v1/sessions.py` contains no import from `server/app/ai/`)
- D-07 equal-weight — grep-verified in Slice A CTE + Slice C UPDATE (no `SongSkill.weight` reference)
- Phase 4 governor interception boundary — grep-verified across all 3 slices (`grep -rn "messages.create" server/app | grep -v "server/app/ai/"` returns empty)
- Fletcher voice contract — every user-facing string in Slice A/B/C is grep-verified against UI-SPEC.md verbatim
- Zero new packages across all 3 slices

**UI hint**: yes

### Phase 4: Cost Governor & Node Verification

**Goal**: Every Anthropic API call routes through a single server-side governor with per-user caps and quota visibility, the Console-level $20/month cap is wired with billing alerts, new skill-graph nodes pass embed-dedup + Sonnet verifier (with a curator queue for uncertain proposals), and a nightly job decays untouched nodes by 5%.
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: COST-01, COST-02, COST-03, COST-04, SKILL-04, SKILL-05
**Success Criteria** (what must be TRUE):

  1. All Anthropic API calls flow through one server-side governor module that logs a pre-emptive token estimate for every call
  2. "Break down this song" is enforced at 3/week per user; a fourth attempt in the same week is blocked with a clear message
  3. Client surfaces remaining quota for capped features so the user always knows what's left
  4. Anthropic Console shows a $20/month hard cap with billing alerts wired to the developer's email
  5. New skill-graph node proposals (user or AI) run through embedded-dedup + a Sonnet verifier; confident proposals join the canonical graph, uncertain ones land in a curator queue
  6. A nightly job decays every node untouched for more than 7 days by 5%, and the decay run is auditable in the backend

**Plans**: 4 plans in 4 waves (vertical slices under MVP mode)

Plans:

**Wave 1**

- [x] 04-01-PLAN.md — Slice A · Governor + breakdown cap: Alembic 0004 (governor_calls + skill_node_proposals + skill_node_rejections + decay_runs + skill_nodes.canonical_node_id + skill_nodes.last_decayed_at) + governor.py (BudgetExceededError + AnthropicQuotaExceededError + @governed decorator) + @governed applied to run_technique_breakdown + BudgetExceededError→429 BREAKDOWN_CAPPED + AnthropicQuotaExceededError→503 FLETCHER_OUT + BreakdownQuota Pydantic + TodaySongResponse.breakdown_quota field + rapidfuzz + apscheduler pinned + test_governor + test_alembic_0004 [COST-01, COST-02] *(2026-07-30)*

**Wave 2** *(blocked on Wave 1 — populates BreakdownQuota field declared in Slice A; requires governor_calls table)*

- [x] 04-02-PLAN.md — Slice B · Quota UI + Console cap: TodaySongResponse.breakdown_quota populated from governor_calls COUNT + inline chip on SongOfDayCard + disabled CTA at 0 + BreakdownErrorCard variants for BREAKDOWN_CAPPED and FLETCHER_OUT + daysUntilReset utility + RUNBOOK.md ($20/mo Console cap + FLETCHER_ADMIN_TOKEN generation) + startup log reminder + human checkpoint confirming Console cap set [COST-03, COST-04] *(2026-07-30; human-verified Console cap 2026-07-30)*

**Wave 3** *(blocked on Wave 1 for skill_node_proposals/skill_node_rejections + @governed; blocked on Wave 2 for RUNBOOK token generation)*

- [x] 04-03-PLAN.md — Slice C · Skill-node verification: skill_dedupe.py (rapidfuzz.token_set_ratio + thresholds 85/70) + skill_verifier.py (SkillNodeVerifyOutput + run_skill_node_verify with @governed cap=None) + verifier pipeline hooked into run_onboarding_parse inside SAVEPOINT + @governed applied to run_onboarding_parse + get_admin_token dep (hmac.compare_digest) + admin.py router (GET curator HTML + POST action) + skill_node_rejections drop path + test_skill_dedupe + test_skill_verifier + test_onboarding_verifier_pipeline + test_admin_curator [SKILL-04] *(2026-08-12; 37 tests pass)*

**Wave 4** *(blocked on Wave 1 for decay_runs + skill_nodes.last_decayed_at; sequential w/ Slice B and C for main.py on_startup edits)*

- [x] 04-04-PLAN.md — Slice D · Decay scheduler: scheduler.py (AsyncIOScheduler singleton + decay_all_nodes UPDATE with GREATEST clamp + last_decayed_at 20h debounce + decay_runs audit row on success AND failure via fresh session) + main.py startup registers cron hour=3 minute=0 timezone=UTC + test_scheduler_decay [SKILL-05] *(2026-08-12; 12 tests pass)*

**Cross-cutting constraints:**

- Every AI call site wrapped by @governed (grep-verified: `grep -c "@governed" server/app/ai/*.py` == 3 lines — breakdown, onboarding, skill_verifier)
- Single AsyncAnthropic invariant (grep-verified: `grep -rn "AsyncAnthropic(" server/app | grep -v "server/app/ai/client.py"` empty)
- Fletcher voice for cap errors — BREAKDOWN_CAPPED "Not my tempo." + FLETCHER_OUT "Fletcher's on a break." locked verbatim from D-02 + D-08
- rapidfuzz-only dedup (no external embeddings vendor) per D-09
- APScheduler in-process safe under `uvicorn --workers 1`; multi-worker migration documented in scheduler.py docstring for future
- Alembic 0004 consolidated in Slice A (all 4 new tables + 2 new columns) so Slices B/C/D touch no migration
- EAS device-verify batched with Phase 3 pending EAS build per user memory `eas-budget`

**UI hint**: yes

### Phase 4.1: AI Drills (INSERTED 2026-09-14)

**Goal**: Turn the Song of the Day breakdown from a static reference (tab + chords + technique-notes-as-paragraphs) into an actionable practice unit. The AI teacher emits 2–4 named drills per song, each targeting a specific skill_node with a tempo ladder, repetition count, and success criterion. Drills are rated individually so mastery writes back to one skill_node per drill — tightening the personalization signal the whole product hinges on.
**Mode:** mvp
**Depends on**: Phase 4 (needs the Sonnet governor pipeline + skill_nodes writeback path from Slice C)
**Requirements**: DRILL-01, DRILL-02, DRILL-03
**Success Criteria** (what must be TRUE):

  1. `GET /api/v1/songs/{id}/breakdown` response includes 2–4 drills with `name`, `target_skill_temp_id`, `what`, `tab_snippet` (1–2 measures), `start_bpm`, `target_bpm`, `repetitions`, `success_criterion`, optional `common_trap`
  2. Mobile `breakdown/[songId]` screen renders a **DRILLS** section above the full-song tab; each drill = a card with name + target skill + tempo ladder + Start button
  3. Tapping Start opens a focused drill screen showing the 1–2 measure `tab_snippet`, current tempo in the ladder, rep counter, and 3-choice rating (Done unlocked it / Getting closer / Not my tempo skip) — metronome integration DEFERRED to Phase 5 so drill screen renders static tab + spec for now
  4. Rating a drill writes to `skill_nodes.mastery` for the drill's `target_skill_temp_id` via the existing per-song rating path (extended for per-drill granularity)
  5. Existing Breakdown fields (`tab`, `chords`, `technique_notes`) unchanged and still render — drills are additive

**Motivation**: 2026-09-14 birdseye product review concluded V2 was repeating V1's failure mode with sides flipped. V1 had drills without songs → user stopped opening it (PROJECT.md line 47's killer diagnosis). V2 as shipped is songs without drills — same silo, other side. The word "drill" appeared 0 times in the pre-insertion ROADMAP. This phase restores the layer that got dropped between vision and roadmap.

**Out of scope** (deferred to Phase 5 or later):

- Metronome click during drill practice — Phase 5 Toolkit ships the metronome; a follow-on wires it into drill Start
- Drill progress tracking across sessions (rep counter is per-session only for now)
- Drill history / analytics
- Redefining Phase 5 — Phase 5 stays as-is after this insertion

**Plans**: 5 plans in 4 waves (revised 2026-09-14 per checker feedback — B1/B2/B4 fixes added tasks; Plan 04 grew from 3 → 5 tasks; Plan 02 grew from 3 → 4 tasks to add BreakdownEnvelope for server-derived drill state)

Plans:

**Wave 1**

- [x] 04.1-01-PLAN.md — Slice 1 · Server-side drill emission: Drill Pydantic model (bpm/rep constraints + target_bpm>start_bpm validator per W2) + Breakdown.drills field (min_length=2/max_length=4 per Landmine #3) + SYSTEM_PROMPT DRILLS block + _format_user_message pair-passing + breakdowns endpoint hallucinated-id drop-filter + Landmine #3 soft-fail (ValidationError → drills=[], never 500) + schema + endpoint tests [DRILL-01]

**Wave 2** *(blocked on Wave 1 — Drill Pydantic contract required for downstream)*

- [x] 04.1-02-PLAN.md — Slice 2 · Rating write path + BreakdownEnvelope: Alembic 0005 (drill_index + target_skill_node_id columns + COALESCE partial-unique index + CHECK constraint) + UserSession ORM extension + SessionCreate/SessionResponse Pydantic extension + submit_rating drill branch (surgical single-node UPDATE) + drill-primary 409 policy (SONG_RATING_BLOCKED_BY_DRILL) + **B1 FIX: BreakdownEnvelope wrapper adds server-derived drill_rated_today_indices to GET /breakdown response** + alembic 0005 + sessions + envelope tests [DRILL-03]

- [x] 04.1-03-PLAN.md — Slice 3 · Mobile drill list + envelope adapt: schema.d.ts regen (Drill + BreakdownEnvelope + SessionCreate.drill_index) + DrillCard component (compile-time contract tests per existing convention) + useBreakdown envelope-shape adaptation + DRILLS section inserted immediately below song header, above How-to-play-it (N2 clarification) + W1 shell semantics fix ( instead of fragile ) [DRILL-02]

**Wave 3** *(blocked on Wave 2 for BreakdownEnvelope + Wave 3 for DrillCard component)*

- [x] 04.1-04-PLAN.md — Slice 4 · Drill-detail screen + rating mutation + drill-primary UI: **B4 FIX: schema-regen sanity check task** + useSubmitDrillRating hook + nested Expo Router route + focused drill screen (N3 fix: guard-clause route params, no  non-null assertions) + pure-fn helpers exported for testability + **B2 FIX: mobile/__tests__/app/breakdown/drill.test.tsx behavioral tests** + **B1 FIX: parent breakdown screen derives drillRatedToday from envelope.drill_rated_today_indices — DELETES the pre-revision QueryClient mutation-cache subscription pattern** [DRILL-02, DRILL-03]

**Wave 4** *(blocked on all prior — manual eval + device verification)*

- [ ] 04.1-05-PLAN.md — Slice 5 · Manual eval + human checkpoints: eval_drills.py (5 tuning-diverse songs, live Anthropic, W5 fix: real Postgres dev-DB required, no SQLite fallback) + skip-by-default pytest wrapper + eval-results doc + human checkpoint for landmine gate table + human checkpoint for on-device end-to-end verification (N1 fix: step o queries user_sessions to validate target_skill_node_id resolves to a real skill_nodes row — real-user L2 coverage that fake-uuid eval script cannot provide) [DRILL-01, DRILL-02, DRILL-03]

**UI hint**: yes

### Phase 5: Library, Toolkit & Polish

**Goal**: The Library tab lets the user browse, search, add, and remove tracked songs; the Toolkit tab ships a working metronome (tempo + subdivisions) and a chromatic tuner — so the app feels shareable day one.
**Mode:** mvp
**Depends on**: Phase 4
**Requirements**: LIB-01, TOOL-01, TOOL-02
**Success Criteria** (what must be TRUE):

  1. Library tab lists every song the user is tracking, with search that filters the list live
  2. User can add a new song to the library and remove an existing one; changes persist across app restarts
  3. Toolkit tab includes a metronome with configurable tempo and subdivisions that keeps audible time
  4. Toolkit tab includes a chromatic tuner that identifies a played pitch from the device mic
  5. The app is installable and demoable to a second guitarist without dev intervention (build artifacts + shareable install path exist)

**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation & Empty Loop | 4/4 executed (Android build deferred) | Substantially complete (PLAT-02 pending) | 2026-07-19 |
| 2. Onboarding & Initial Skill Graph | 4/4 | Complete    | 2026-07-28 |
| 3. AI Teacher & Song of the Day | 4/4 | Complete | 2026-07-29 |
| 4. Cost Governor & Node Verification | 4/4 | Complete — all 6 req IDs satisfied (COST-01/02/03/04, SKILL-04, SKILL-05) | 2026-08-12 |
| 4.1. AI Drills (INSERTED) | 4/5 | In Progress|  |
| 5. Library, Toolkit & Polish | 0/TBD | Not started | - |
