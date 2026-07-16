# Roadmap: Guitar Trainer v2

## Overview

Five vertical-MVP phases that stand up the song-of-the-day loop end-to-end. Phase 1 lands a running iOS + Android app talking to a Railway-hosted FastAPI + Postgres with an offline MMKV cache and a hardcoded "today" song — the empty loop the user can already open every morning. Phase 2 replaces the hardcoded seed with real onboarding and a persistent 3-level DAG skill graph. Phase 3 wires the AI teacher (Sonnet 4.6) into song-of-the-day selection, tab/chord rendering via react-native-svg, and deterministic self-report writes. Phase 4 hardens the money side — single-module LLM governor, per-user caps, quota UI, Console cap — and the graph side — dedup + Sonnet verifier for new nodes with curator queue, nightly 5% decay. Phase 5 fills out Library + Toolkit (metronome + tuner) so the app is shareable day one.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation & Empty Loop** - Expo/RN + EAS on iOS + Android, FastAPI on Railway + Postgres, MMKV cache, three-tab shell wired to a hardcoded "today" song
- [ ] **Phase 2: Onboarding & Initial Skill Graph** - 5–8 min onboarding, Settings re-run, 3-level DAG skill graph with 5-bpm bins seeded from onboarding answers
- [ ] **Phase 3: AI Teacher & Song of the Day** - Sonnet 4.6 breakdown, react-native-svg tab + chord diagrams, deterministic song selection, self-report rating writes back to graph
- [ ] **Phase 4: Cost Governor & Node Verification** - Single-module LLM governor, per-user rate caps, quota UI, $20 Console cap, embed-dedup + Sonnet verifier for new nodes + curator queue, nightly 5% decay
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
- [ ] 01-01-PLAN.md — Walking Skeleton: monorepo scaffold + FastAPI server + Pydantic models + DB-backed endpoint + Expo app + codegen loop

**Wave 2** *(blocked on Wave 1 completion)*
- [ ] 01-02-PLAN.md — SVG rendering: ChordDiagram + TabNotation components + Today tab wired to full breakdown payload
- [ ] 01-03-PLAN.md — MMKV offline persistence: PersistQueryClientProvider + MMKV persister at root layout

**Wave 3** *(blocked on Wave 2 completion)*
- [ ] 01-04-PLAN.md — Ship to devices: Railway deploy + iOS EAS preview build + Android EAS preview build *(autonomous: false — Apple Developer + device UDID gate)*

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
**Plans**: TBD

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
**Plans**: TBD
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
**Plans**: TBD
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
| 1. Foundation & Empty Loop | 0/4 | Planned | - |
| 2. Onboarding & Initial Skill Graph | 0/TBD | Not started | - |
| 3. AI Teacher & Song of the Day | 0/TBD | Not started | - |
| 4. Cost Governor & Node Verification | 0/TBD | Not started | - |
| 5. Library, Toolkit & Polish | 0/TBD | Not started | - |
