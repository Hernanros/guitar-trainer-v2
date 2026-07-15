# Phase 1: Foundation & Empty Loop - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-15
**Phase:** 1-foundation-empty-loop
**Areas discussed:** Today's payload shape

---

## Gray Area Selection

Four gray areas were surfaced. User selected one; the other three were logged as Claude's discretion / deferred ideas.

| Gray Area | Description | Selected |
|-----------|-------------|----------|
| Repo layout — monorepo vs split | One repo with mobile/ + server/ dirs, or two separate GitHub repos | |
| Today's payload shape — minimal vs Phase 3-ready | Should Phase 1's hardcoded song return only shell-required fields, or the full contract Phase 3 will fill in? | ✓ |
| Multi-user seams — how stubbed | Zero user infra now, user_id everywhere with default "self", or full users table + X-User-ID header wired without auth | |
| Navigation library — Expo Router vs React Navigation | File-based vs imperative navigation | |

---

## Today's payload shape

### Q1: How much of the Phase 3 breakdown structure should Phase 1's hardcoded payload include?

| Option | Description | Selected |
|--------|-------------|----------|
| Full shape, dummy content | Every field Phase 3 needs is present in Phase 1, populated with hardcoded example data. Shell renders every visual element from day one. Phase 3 replaces content, not schema. | ✓ |
| Minimal metadata only | Just title, artist, tempo, style. `breakdown` null. Shell shows placeholder. Fastest Phase 1, but Phase 3 designs the breakdown schema + updates client + tests all at once. | |
| Metadata + breakdown envelope with empty sections | Metadata plus a `breakdown` object with empty `tab: []`, `chords: []`, `notes: ''`. Middle ground. | |
| Let Claude decide | I'll bias toward the full shape. | |

**User's choice:** Full shape, dummy content (Recommended)
**Notes:** Selected the recommendation. Rationale locked in CONTEXT.md D-01.

### Q2: Where does the payload's contract live — what's the source of truth for the schema?

| Option | Description | Selected |
|--------|-------------|----------|
| Pydantic → OpenAPI → generated TS types | Pydantic models in FastAPI are the single source. `openapi-typescript` generates client types at build time. Zero drift by construction. | ✓ |
| Hand-written Pydantic + hand-written TS types | Two parallel definitions kept manually in sync. Simplest tooling, drift is a real risk. | |
| Zod (client) as source, mirrored to Pydantic manually | Client-first schema in Zod, hand-mirror to Pydantic. Reverse-drift risk. | |
| Let Claude decide | Would pick Pydantic → generated TS. | |

**User's choice:** Pydantic → OpenAPI → generated TS types (Recommended)
**Notes:** Locked in CONTEXT.md D-02.

### Q3: How should the tab and chord data be modeled in the response schema?

| Option | Description | Selected |
|--------|-------------|----------|
| Semantic music JSON (notes / measures / strings / frets) | Model the music: `{ measures: [{ beats: [{ string, fret, duration }] }] }`, chords as `{ name, positions: [{ string, fret, finger }] }`. Client computes SVG layout. | ✓ |
| ASCII tab strings + chord name list | Classic guitar-tab text plus chord names. Tiny payload but client has to parse ASCII into SVG. | |
| Rendered SVG primitives from server | Server pre-computes SVG paths, client just draws. Simplest client but server owns visual layout. | |
| Let Claude decide | Would pick the semantic model. | |

**User's choice:** Semantic music JSON (Recommended)
**Notes:** Locked in CONTEXT.md D-03.

### Q4: How much of Phase 3's selection logic should the Phase 1 endpoint anticipate?

| Option | Description | Selected |
|--------|-------------|----------|
| DB-backed with trivial selector | `GET /api/v1/song-of-day` reads from a `songs` table with one hardcoded row. No `user_id` or `date` params yet. | ✓ |
| Hardcoded response — no DB read | Endpoint returns a Python literal. Postgres exists but stores nothing. | |
| Full signature (user_id + date) with stub selector | `GET /api/v1/song-of-day?user_id=self&date=YYYY-MM-DD`. Selector ignores params. | |
| Let Claude decide | Would go DB-backed with trivial selector. | |

**User's choice:** DB-backed with trivial selector (Recommended)
**Notes:** Locked in CONTEXT.md D-04.

---

## Claude's Discretion

Areas the user did not select for discussion — Claude has flexibility per CONTEXT.md:

- **Repo layout** — recommended single monorepo (mobile/ + server/) because the OpenAPI codegen crosses the boundary.
- **Multi-user seams depth** — Phase 1 has no user_id column; user scoping added in Phase 2.
- **Navigation library** — recommended Expo Router (Expo default, file-based, better deep-linking).
- **Placeholder screen content** for Library / Toolkit — plain "Coming soon" screens.
- **MMKV cache invalidation** — write-through on every response, no TTL.
- **Deploy automation** — Railway Git integration for FastAPI, manual `eas build` for Phase 1.

## Deferred Ideas

- **Repo layout deep discussion** — revisit at top of Phase 2 if the monorepo default becomes uncomfortable.
- **Multi-user seams depth** — carry forward as a Phase 2 discussion topic when onboarding lands.
- **Navigation library** — planner may flag React Navigation as an alternative if research surfaces a strong reason.
- **CI / EAS deploy automation** — deferred to a later phase or the Ship track.
