# Requirements: Guitar Trainer v2

**Defined:** 2026-07-15
**Core Value:** One real song, decomposed by an AI teacher, every day — so practicing feels like playing.

## v1 Requirements

Requirements for the "Prove the loop" MVP. Each maps to exactly one roadmap phase.

### Platform

- [ ] **PLAT-01**: Expo + React Native app runs on iOS via EAS build
- [ ] **PLAT-02**: Expo + React Native app runs on Android via EAS build
- [ ] **PLAT-03**: FastAPI backend deployed to Railway with a persistent Postgres database
- [ ] **PLAT-04**: App reads cached "today" data (song of the day, skill graph snapshot) from MMKV when offline

### Onboarding

- [ ] **ONB-01**: New user completes one-time onboarding that captures songs they can play, songs they're working on, and aspirational songs
- [ ] **ONB-02**: Onboarding captures style/genre tags and session preferences (session length, retention format — streak vs weekly digest vs monthly milestone)
- [ ] **ONB-03**: Initial skill graph state is seeded from onboarding answers
- [ ] **ONB-04**: User can re-run onboarding from Settings at any time

### Skill Graph

- [ ] **SKILL-01**: Skill graph is a 3-level DAG (root domains → sub-domains → leaf skills) stored server-side
- [ ] **SKILL-02**: Leaf skills carry tempo-band data split into 5-bpm bins
- [ ] **SKILL-03**: Session ratings update the graph via deterministic rules (no LLM in the write path)
- [ ] **SKILL-04**: New node proposals (from user or AI) pass an embedded-dedup + Sonnet-verifier gate before joining the canonical graph; uncertain proposals queue for curator review
- [ ] **SKILL-05**: Nightly job decays nodes untouched >7 days by 5%

### Song of the Day

- [ ] **SOTD-01**: Today tab shows exactly one Song of the Day, chosen from current skill graph state and user preferences
- [ ] **SOTD-02**: AI teacher (Sonnet 4.6) produces a technique-breakdown for the day's song
- [ ] **SOTD-03**: Breakdown renders playable material as tab notation via react-native-svg
- [ ] **SOTD-04**: Breakdown renders chord diagrams via react-native-svg
- [ ] **SOTD-05**: After practicing, user submits a self-report session rating that feeds back into the skill graph

### Library & Toolkit

- [ ] **LIB-01**: Library tab lists all tracked songs; user can search, add, and remove
- [ ] **TOOL-01**: Toolkit tab includes a Web Audio–equivalent metronome with tempo and subdivisions
- [ ] **TOOL-02**: Toolkit tab includes a chromatic tuner

### AI Cost Guardrails

- [ ] **COST-01**: All Anthropic API calls route through a single server-side governor module that logs token estimates before dispatch
- [ ] **COST-02**: Per-user feature caps enforced ("Break down this song" limited to 3/week on free tier)
- [ ] **COST-03**: Client shows remaining quota to the user for capped features
- [ ] **COST-04**: Anthropic Console monthly hard cap set to $20 with billing alerts wired up

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Coach

- **COACH-01**: Conversational AI coach chat over the current skill graph and song
- **COACH-02**: Coach can suggest and add exercises directly to the user's practice queue

### Discovery

- **DISC-01**: "What skills do I need for [song]?" — bidirectional discovery UI over the skill graph
- **DISC-02**: "Show me songs that hit [skill]" — song discovery from a chosen skill node

### Streaming

- **STREAM-01**: Spotify PKCE OAuth integration surfaces user's top tracks/artists
- **STREAM-02**: Song of the Day can pull from user's streaming history (paid tier)

### Mic Listening (v1.2+)

- **MIC-01**: World-class real-time pitch + timing analysis (deferred until it can be truly good)

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Client-side mic DSP / "78% in time" scoring | v1's version was a gimmick; only ship when the analysis is world-class |
| Conversational coach chat | Deferred to v1.1 — MVP proves the loop without it |
| Bidirectional discovery UI | Data model supports it; UI is v1.1 |
| Video / vision input | Not needed for the daily loop |
| Streaming integration in MVP | Optional feature that moves to paid tier v1.1 |
| Paywall / monetization | Nothing pre-PMF |
| Full tab-library import/rendering | Beyond MVP SVG output — deferred to v1.2+ |
| Multi-user auth | POC is single-user; multi-user seams are stubbed but not wired |

## User Stories

- **As a serious guitarist**, I open the app in the morning and immediately see one song to work on today — I don't have to build a session or decide what to do.
- **As a self-reporting user**, I rate how the session went in one tap so tomorrow's song reflects today's progress.
- **As a mobile user**, I can view today's song and breakdown even without connectivity, because the day's data is cached.
- **As a POC user**, I trust the AI won't rack up a giant bill because usage is capped and I can see my remaining quota.
- **As an onboarding user**, I answer a few questions about the songs I can play, am working on, and aspire to — and the app builds an initial skill graph from that.

## Acceptance Criteria

- **Loop closes daily**: user launches app → sees today's song → practices → submits rating → next day's song reflects the rating.
- **Skill graph is auditable**: every graph change traces to a session rating or a nightly-decay run — no silent LLM writes.
- **Cost stays under $20/mo** during POC, verified by weekly Anthropic Console check.
- **Both platforms**: iOS and Android EAS builds installable on device.
- **Offline read** of the day's song works in airplane mode.

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| PLAT-01 | TBD | Pending |
| PLAT-02 | TBD | Pending |
| PLAT-03 | TBD | Pending |
| PLAT-04 | TBD | Pending |
| ONB-01 | TBD | Pending |
| ONB-02 | TBD | Pending |
| ONB-03 | TBD | Pending |
| ONB-04 | TBD | Pending |
| SKILL-01 | TBD | Pending |
| SKILL-02 | TBD | Pending |
| SKILL-03 | TBD | Pending |
| SKILL-04 | TBD | Pending |
| SKILL-05 | TBD | Pending |
| SOTD-01 | TBD | Pending |
| SOTD-02 | TBD | Pending |
| SOTD-03 | TBD | Pending |
| SOTD-04 | TBD | Pending |
| SOTD-05 | TBD | Pending |
| LIB-01 | TBD | Pending |
| TOOL-01 | TBD | Pending |
| TOOL-02 | TBD | Pending |
| COST-01 | TBD | Pending |
| COST-02 | TBD | Pending |
| COST-03 | TBD | Pending |
| COST-04 | TBD | Pending |

**Coverage:**
- v1 requirements: 25 total
- Mapped to phases: 0 (will be filled by roadmapper)
- Unmapped: 25 ⚠️

---
*Requirements defined: 2026-07-15*
*Last updated: 2026-07-15 after initialization*
