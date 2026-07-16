# Walking Skeleton — Guitar Trainer v2

**Phase:** 1
**Generated:** 2026-07-16

## Capability Proven End-to-End

A user opens the Expo app on iOS or Android, the app fetches Sweet Home Chicago from `GET /api/v1/song-of-day` on a Railway-hosted FastAPI service backed by a real Postgres row, and renders the song title, artist, and chord diagram on the Today tab — with MMKV caching the payload so reopening in airplane mode still shows the song.

## Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Mobile framework | Expo SDK 57 + React Native 0.86, New Architecture always-on | Locked in RECOVERED-DESIGN.md; current SDK; no config required for New Arch |
| Navigation | Expo Router (file-based, `app/(tabs)/`) | Expo's current default; best deep-linking story; bottom tabs via `(tabs)/_layout.tsx` |
| Server state | TanStack Query 5 (`useQuery`) with `PersistQueryClientProvider` | Locked in stack; handles loading/error/cache lifecycle; pairs natively with MMKV persister |
| Offline cache | `react-native-mmkv` v4 (NitroModules) as TanStack persister | Locked in stack; ~30x faster than AsyncStorage; requires dev-client build (no Expo Go) |
| UI state | Zustand 5 | Locked in stack; minimal boilerplate; Phase 1 has no UI state — store is a stub |
| SVG rendering | `react-native-svg` 15 (hand-rolled chord/tab primitives) | Locked in stack + D-03; no maintained RN guitar chord library accepts the semantic JSON shape |
| Backend framework | FastAPI 0.128 + Pydantic v2 + SQLAlchemy 2.0 async | Locked in stack; Pydantic is the schema source of truth (D-02) |
| API contract | Pydantic → FastAPI `/openapi.json` → `openapi-typescript` v7 → `schema.d.ts` | D-02; zero drift by construction; generated file gitignored |
| Database | Postgres (Railway add-on) with JSONB `breakdown` column | Locked in stack; JSONB stores the rich nested breakdown object without multi-table schema |
| Migrations | Alembic, run via Railway `preDeployCommand` in `railway.toml` | Railway's canonical migration slot; runs in private network before app start |
| Deployment | Railway (FastAPI service + Postgres add-on), auto-deploy from `main` branch | Locked in stack; zero-config Nixpacks Python detection; Git integration |
| Mobile builds | EAS (Expo Application Services) with `development` + `preview` profiles | No Expo Go due to MMKV NitroModules; EAS managed credentials; internal APK/ad-hoc IPA |
| Repo layout | Monorepo: `mobile/` + `server/` at repo root | Claude's discretion; codegen script in `mobile/` points at `server/`'s running OpenAPI endpoint |
| Auth | None — single-user POC, no auth in Phase 1 | Locked in PROJECT.md; multi-user seams stubbed (no `user_id` column in Phase 1) |
| Music data model | Semantic JSON: `Tab { measures[] }`, `Chord { positions[] }`, no ASCII tab | D-03; server never owns visual layout; client computes SVG from data |

## Stack Touched in Phase 1

- [x] Project scaffold — `npx create-expo-app` under `mobile/`, Python venv + pip under `server/`
- [x] Routing — Expo Router three-tab shell (`Today`, `Library`, `Toolkit`)
- [x] Database — Postgres `songs` table via Alembic migration + one real hardcoded row seeded at deploy
- [x] API — `GET /api/v1/song-of-day` reads from DB and returns full Pydantic `SongResponse`
- [x] UI — Today tab fetches song via TanStack Query and renders title, artist, chord diagram via `react-native-svg`
- [x] Offline — MMKV persister caches TanStack Query state; reopening in airplane mode serves cached payload
- [x] Deployment — FastAPI on Railway (auto-deploy); iOS + Android EAS `preview` builds distributed internally

## Out of Scope (Deferred to Later Slices)

- Onboarding flow and skill graph (Phase 2)
- Real AI teacher content / Sonnet 4.6 breakdown (Phase 3)
- Song-of-day deterministic selector using skill graph (Phase 3)
- Self-report session ratings (Phase 3)
- Library tab search / add / remove (Phase 5)
- Toolkit metronome + tuner (Phase 5)
- AI cost governor and per-user caps (Phase 4)
- Multi-user auth — `user_id` column added in Phase 2
- CI/EAS deploy automation (deferred to a later phase)
- `user_id` and `date` query params on `GET /api/v1/song-of-day` (Phase 3)
- Practice loops, difficulty tags in Breakdown model (Phase 3 additive fields)

## Subsequent Slice Plan

- Phase 2: New user completes onboarding (5–8 min), skill graph seeded server-side, Settings re-run
- Phase 3: AI teacher (Sonnet 4.6) produces technique breakdown; deterministic song selector; self-report rating writes to skill graph
- Phase 4: Single-module LLM cost governor, per-user caps, quota UI, $20 Console cap, embed-dedup + Sonnet node verifier, nightly 5% decay
- Phase 5: Library tab (search/add/remove), Toolkit (metronome + chromatic tuner), shareability polish
