# Phase 1: Foundation & Empty Loop - Context

**Gathered:** 2026-07-15
**Status:** Ready for planning

<domain>
## Phase Boundary

Stand up the empty song-of-the-day loop end-to-end so the user can install the app on iOS or Android via EAS, open it, and see a hardcoded Song of the Day rendered by the three-tab shell — with a FastAPI + Postgres backend on Railway serving the payload and MMKV caching it for offline reads.

**In scope:** iOS + Android EAS builds, FastAPI service on Railway, persistent Postgres instance, `songs` table with one hardcoded row, `GET /api/v1/song-of-day` endpoint, MMKV-cached client fetch, three-tab shell (Today · Library · Toolkit) with placeholder screens for Library + Toolkit.

**Out of scope for this phase:** onboarding (Phase 2), skill graph (Phase 2), AI teacher / real breakdown content (Phase 3), self-report rating writes (Phase 3), cost governor (Phase 4), Library search + Toolkit metronome/tuner functionality (Phase 5).

</domain>

<decisions>
## Implementation Decisions

### Song-of-day payload shape
- **D-01:** Phase 1 ships the **full Phase 3-ready payload shape with hardcoded dummy content**. Every field the AI teacher will eventually populate (metadata, tab notation, chord diagrams, technique notes) is present in Phase 1 with realistic hardcoded example data. Phase 3 replaces content, not schema.
  - *Rationale:* Forces the shell to render every visual element from day one — catches layout / rendering issues before AI is in the loop. Minimizes Phase 3 rework.
  - *Implication for planner:* The one hardcoded row in the `songs` table must contain a complete, realistic breakdown so the client can render tab + chord diagrams without null-branching.

### API contract source of truth
- **D-02:** **Pydantic models in FastAPI are the single source of truth.** The mobile client's TypeScript types are generated from the FastAPI-emitted OpenAPI schema (tool: `openapi-typescript` or equivalent) at client build time.
  - *Rationale:* Zero drift by construction. Standard 2026 FastAPI + RN stack. Data scientist-friendly (Pydantic is where the data lives).
  - *Implication for planner:* Add a codegen step to the mobile-side build. The generated types file should live at a stable client path and be gitignored.

### Notation data model (tab + chords)
- **D-03:** **Semantic music JSON** — model the music, not the presentation. Tab is `{ measures: [{ beats: [{ string, fret, duration }] }] }`; chords are `{ name, positions: [{ string, fret, finger }] }`. The client computes SVG layout from data via `react-native-svg`.
  - *Rationale:* Reusable across Phase 3, v1.1, v2. Server never owns visual layout, so restyling doesn't require a backend deploy.
  - *Implication for planner:* Define concrete Pydantic models for `Measure`, `Beat`, `Note`, `Chord`, `ChordPosition`. The client's rendering primitives (fretboard grid, chord diagram, tab staff) can be minimal skeletons in Phase 1 but must accept the full semantic shape.

### Selector surface
- **D-04:** `GET /api/v1/song-of-day` — **DB-backed with a trivial selector**. Reads the single hardcoded row from a `songs` table and returns it. No `user_id` or `date` query params yet.
  - *Rationale:* Phase 2 adds users, Phase 3 replaces the selector with the real deterministic picker — the endpoint URL and client contract stay identical.
  - *Implication for planner:* Include the `songs` table in Phase 1's initial migration. The trivial selector is one query (`SELECT * FROM songs LIMIT 1`) — do not overbuild it.

### Claude's Discretion
- **Repo layout** (monorepo vs split repos) — user did not select this area. Recommendation: single monorepo with top-level `mobile/` and `server/` directories, since the OpenAPI codegen step needs the server schema visible from the mobile build.
- **Multi-user seams depth** — user did not select this area. Default: single hardcoded `songs` row with no `user_id` column for Phase 1; add user scoping in Phase 2 when onboarding lands.
- **Navigation library** (Expo Router vs React Navigation) — user did not select this area. Recommendation: Expo Router (current Expo default, file-based, better deep-linking story), unless research surfaces a reason to prefer React Navigation.
- **Placeholder screen content** for Library / Toolkit — plain "Coming soon" screens are fine; must not crash on navigation.
- **MMKV cache invalidation strategy** — write-through on every successful response, no TTL. Phase 1's payload is hardcoded and the client just needs "last known good" for airplane mode.
- **Deploy automation** — Railway Git integration (auto-deploy on push to `main`) for the FastAPI service is fine; EAS builds can be triggered manually (`eas build --profile preview`) for Phase 1.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Roadmap & requirements
- `.planning/ROADMAP.md` — Phase 1 section (goal, mode, dependencies, requirements list, success criteria)
- `.planning/REQUIREMENTS.md` — PLAT-01, PLAT-02, PLAT-03, PLAT-04 (the four platform requirements this phase closes)

### Project constraints & locked stack
- `.planning/PROJECT.md` — What This Is, Constraints, Key Decisions table
- `RECOVERED-DESIGN.md` — Stack table (Expo + RN 18, Zustand, TanStack Query, MMKV, react-native-svg, FastAPI on Railway, Postgres, Haiku/Sonnet/Opus model tiers) and MVP scope ("Approach A — Prove the loop")

### Downstream phases (for forward-compat awareness — do not implement)
- `.planning/ROADMAP.md` — Phase 2 (onboarding + skill graph) and Phase 3 (AI teacher + real selector) so payload shape and endpoint signature don't paint us into a corner

</canonical_refs>

<code_context>
## Existing Code Insights

**No source code exists yet.** This is a greenfield phase — planner should scaffold from scratch.

### Reusable Assets
None (empty repo).

### Established Patterns
None (empty repo). Planner establishes patterns for the project during this phase.

### Integration Points
- `.planning/config.json` already sets `git.branching_strategy: none`, `mode: yolo`, `granularity: coarse` — planner should respect these.
- `CLAUDE.md` documents the tech stack and constraints — anything scaffolded should be consistent.

</code_context>

<specifics>
## Specific Ideas

- The one hardcoded song used for Phase 1 should be a real, recognizable song the user can practice against (not "Lorem Ipsum in G") so the shell feels honest during development. Suggestion: something in the user's likely wheelhouse (blues shuffle, standard rock riff) — Claude picks unless the user calls a specific one.
- Client generated types file: pick a stable location (e.g., `mobile/src/api/generated/schema.d.ts`) so imports don't move around later.
- Pydantic models should be structured so that Phase 3's `breakdown` field can add new sub-fields (e.g., practice loops, difficulty tags) without breaking the client — leave the door open for additive schema growth.

</specifics>

<deferred>
## Deferred Ideas

- **Repo layout deep discussion** (monorepo vs split) — user skipped this area. If the "single monorepo" default becomes uncomfortable later, revisit at the top of Phase 2.
- **Multi-user seams depth** — user skipped this area. Phase 2 will need to answer "how does user_id thread through the API and DB" when onboarding lands; carry forward as a Phase 2 discussion topic.
- **Navigation library choice** — user skipped this area. Planner should propose Expo Router by default; if the planner's research turns up a strong reason to prefer React Navigation (e.g., a specific gesture library dependency), it can flag for user confirmation.
- **CI / EAS deploy automation** — manual EAS builds for Phase 1; automated pipelines (GitHub Actions triggering EAS) belong in a later phase or the Ship track.

</deferred>

---

*Phase: 1-Foundation & Empty Loop*
*Context gathered: 2026-07-15*
