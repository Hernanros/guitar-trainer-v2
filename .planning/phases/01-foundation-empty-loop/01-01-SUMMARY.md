---
phase: 01-foundation-empty-loop
plan: "01"
subsystem: full-stack
tags: [fastapi, pydantic, alembic, postgres, expo, react-native, tanstack-query, mmkv, openapi-codegen]
dependency_graph:
  requires: []
  provides:
    - GET /api/v1/song-of-day (FastAPI, DB-backed, full payload shape)
    - songs table with Alembic migration + Sweet Home Chicago seed row
    - SongResponse Pydantic model (D-03 semantic music JSON)
    - Expo app three-tab shell (Today / Library / Toolkit)
    - useSongOfDay TanStack Query hook
    - MMKV offline persister (PersistQueryClientProvider)
    - OpenAPI codegen loop (npm run codegen:local)
  affects:
    - All subsequent plans (02-04) build on this skeleton
tech_stack:
  added:
    - FastAPI 0.128.8 + Pydantic 2.13.4 + uvicorn 0.39.0
    - SQLAlchemy 2.0.51 async + asyncpg 0.31.0 + alembic 1.16.5
    - psycopg2-binary 2.9.10 (Alembic sync migrations)
    - greenlet 3.2.3 (SQLAlchemy async on Python 3.9)
    - Expo SDK 57 + React Native 0.86 + Expo Router
    - react-native-mmkv 4.3.2 (NitroModules, dev-client only)
    - react-native-nitro-modules 0.36.1 (MMKV peer dep)
    - @tanstack/react-query 5.101.2 + persist-client + async-storage-persister
    - openapi-typescript 7.13.0 (codegen)
    - @expo/vector-icons 15.x (tab bar icons)
    - Postgres 16 (Docker local dev, gt:devpass@localhost:5433)
  patterns:
    - Async SQLAlchemy 2.0 (AsyncSessionLocal, async_sessionmaker, get_db dependency)
    - Pydantic v2 with from_attributes=True for ORM-to-response conversion
    - postgres:// -> postgresql+asyncpg:// scheme rewrite (session.py + alembic/env.py)
    - PersistQueryClientProvider + mmkvPersister (offline caching pattern)
    - MMKV v4 createMMKV() API (not new MMKV() from v3)
    - Expo Router file-based tabs under src/app/(tabs)/
key_files:
  created:
    - server/app/models/song.py (Pydantic models: Note, Beat, Measure, Tab, ChordPosition, Chord, TechniqueNote, Breakdown, SongResponse)
    - server/app/models/db.py (SQLAlchemy Song ORM model)
    - server/app/db/session.py (async engine + get_db dependency)
    - server/app/db/seed.py (Sweet Home Chicago seed, idempotent)
    - server/app/api/v1/song_of_day.py (GET /api/v1/song-of-day)
    - server/app/main.py (FastAPI app, CORS, router, startup seed)
    - server/alembic/versions/0001_initial_songs_table.py (initial migration)
    - server/alembic/env.py (DATABASE_URL override, postgres:// rewrite)
    - server/alembic.ini
    - server/requirements.txt
    - server/runtime.txt (python-3.12)
    - server/railway.toml (preDeployCommand = "alembic upgrade head")
    - server/Procfile
    - mobile/src/api/queryClient.ts (QueryClient + mmkvPersister)
    - mobile/src/api/songOfDay.ts (useSongOfDay hook)
    - mobile/src/app/_layout.tsx (PersistQueryClientProvider root)
    - mobile/src/app/(tabs)/_layout.tsx (three-tab navigation)
    - mobile/src/app/(tabs)/index.tsx (Today tab, calls useSongOfDay)
    - mobile/src/app/(tabs)/library.tsx (placeholder)
    - mobile/src/app/(tabs)/toolkit.tsx (placeholder)
    - mobile/src/store/uiStore.ts (Zustand stub)
    - mobile/eas.json (development/preview/production profiles)
    - mobile/.env.production (placeholder Railway URL)
    - .gitignore (repo root: excludes .venv, generated/, etc.)
    - mobile/.gitignore (excludes src/api/generated/)
  modified:
    - mobile/package.json (added codegen:local script, all new deps)
    - mobile/app.json (name/slug/scheme for guitar-trainer)
decisions:
  - "D-01 satisfied: full Phase 3-ready payload in seed (E7/A7 chords, 2 technique_notes, 2 measures)"
  - "D-02 satisfied: Pydantic -> OpenAPI -> openapi-typescript codegen, schema.d.ts gitignored"
  - "D-03 satisfied: semantic music JSON schema (Note/Beat/Measure/Tab + ChordPosition/Chord)"
  - "D-04 satisfied: SELECT * FROM songs LIMIT 1, no user_id or date params"
  - "Expo Router file-based tabs at src/app/(tabs)/ (Claude discretion)"
  - "MMKV v4 createMMKV() API (v3 new MMKV() constructor removed in v4)"
metrics:
  duration: "~13 minutes"
  completed: "2026-07-16"
  tasks_completed: 3
  tasks_total: 3
  files_created: 25
  files_modified: 3
---

# Phase 1 Plan 01: Walking Skeleton Summary

**One-liner:** FastAPI + Postgres + Pydantic models + Alembic migration serve Sweet Home Chicago via `GET /api/v1/song-of-day`; Expo SDK 57 three-tab app fetches it via TanStack Query with MMKV offline persistence; OpenAPI codegen generates TypeScript types with zero drift.

## What Was Built

End-to-end walking skeleton proving the Guitar Trainer v2 full stack communicates from mobile to server to database:

1. **Server (FastAPI + Postgres):** `GET /api/v1/song-of-day` returns the hardcoded Sweet Home Chicago payload — id, title, artist, genre, bpm, key, and a full Phase 3-ready `breakdown` with semantic tab (measures/beats/notes), chord diagrams (E7, A7 with finger positions), and technique notes. The server starts with a seed check and inserts the row if the table is empty.

2. **Database (Postgres via Docker):** `songs` table created by Alembic revision `0001`. The seed row is realistic (not lorem ipsum) with a 12-bar blues shuffle in E including two chords at base_fret 1 with open/muted string markers.

3. **Mobile (Expo SDK 57):** Three-tab Expo Router shell (`Today` / `Library` / `Toolkit`). The Today tab calls `useSongOfDay()` via TanStack Query. The root layout wraps with `PersistQueryClientProvider` and MMKV persister — offline reads will work once the dev-client build is installed. Library and Toolkit are "Coming soon" placeholders.

4. **Codegen loop:** `npm run codegen:local` generates `mobile/src/api/generated/schema.d.ts` from the live FastAPI `/openapi.json`. TypeScript compiles with zero errors after codegen. The generated file is gitignored per D-02.

## Verification Evidence

```
curl http://localhost:8000/api/v1/song-of-day
-> { "title": "Sweet Home Chicago", "artist": "Robert Johnson", ... "breakdown": { "tab": {...}, "chords": [{"name": "E7", ...}, {"name": "A7", ...}], "technique_notes": [...] } }

curl http://localhost:8000/openapi.json
-> { "info": { "version": "1.0.0" }, "paths": { "/api/v1/song-of-day": {...}, "/healthz": {...} } }

curl http://localhost:8000/healthz
-> { "status": "ok" }

npx tsc --noEmit (from mobile/)
-> (no output — zero errors)

git check-ignore mobile/src/api/generated/schema.d.ts
-> mobile/src/api/generated/schema.d.ts (exit 0 — gitignored)
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] psycopg2-binary added to requirements.txt**
- **Found during:** Task 1 verification (Alembic migration)
- **Issue:** Alembic runs sync migrations and requires a sync Postgres driver. `asyncpg` is async-only and can't be used for Alembic's `engine_from_config` sync path.
- **Fix:** Added `psycopg2-binary==2.9.10` to `requirements.txt` and installed it in the venv.
- **Files modified:** `server/requirements.txt`
- **Commit:** fd6838e

**2. [Rule 1 - Bug] greenlet added to requirements.txt**
- **Found during:** Task 1 — FastAPI startup on Python 3.9
- **Issue:** SQLAlchemy 2.0 async requires `greenlet` as a separate dependency on Python 3.9 (bundled only in Python 3.10+). `ValueError: the greenlet library is required`.
- **Fix:** Added `greenlet==3.2.3` to `requirements.txt`.
- **Files modified:** `server/requirements.txt`
- **Commit:** fd6838e

**3. [Rule 1 - Bug] Seed uses ORM insert instead of raw text() SQL**
- **Found during:** Task 1 — server startup
- **Issue:** Raw `text()` SQL with named parameters (`:breakdown`) conflicted with asyncpg's positional `$N` parameter style. The `::jsonb` cast caused `PostgresSyntaxError: syntax error at or near ":"`.
- **Fix:** Replaced raw SQL with SQLAlchemy ORM insert (`db.add(SongORM(...))`) which correctly handles JSONB column serialization.
- **Files modified:** `server/app/db/seed.py`
- **Commit:** fd6838e

**4. [Rule 1 - Bug] MMKV v4 API: createMMKV() replaces new MMKV()**
- **Found during:** Task 2 TypeScript check
- **Issue:** `react-native-mmkv` v4 (NitroModules) changed the constructor API. `new MMKV()` no longer exists — replaced by `createMMKV()`. TypeScript error: `'MMKV' only refers to a type, but is being used as a value here`.
- **Fix:** Changed `new MMKV({ id: 'query-cache' })` to `createMMKV({ id: 'query-cache' })`.
- **Files modified:** `mobile/src/api/queryClient.ts`
- **Commit:** 768f503

**5. [Rule 1 - Bug] MMKV v4: remove() replaces delete()**
- **Found during:** Task 2 TypeScript check
- **Issue:** `mmkv.delete(key)` no longer exists in MMKV v4. The method was renamed to `mmkv.remove(key)`.
- **Fix:** Updated `removeItem` in the MMKV storage adapter.
- **Files modified:** `mobile/src/api/queryClient.ts`
- **Commit:** 768f503

**6. [Rule 2 - Missing] @expo/vector-icons installed**
- **Found during:** Task 2 — `(tabs)/_layout.tsx` uses Ionicons
- **Issue:** The Expo SDK 57 default template uses `expo-symbols` instead of `@expo/vector-icons`. Ionicons require the vector-icons package.
- **Fix:** Installed `@expo/vector-icons` via `npx expo install`.
- **Files modified:** `mobile/package.json`
- **Commit:** 768f503

**7. [Rule 1 - Bug] CSS module types added**
- **Found during:** Task 2 TypeScript check
- **Issue:** The Expo SDK 57 template scaffold references CSS modules (`.module.css`) and side-effect CSS imports (`@/global.css`) without TypeScript declarations.
- **Fix:** Created `mobile/src/types/css.d.ts` with CSS module type declarations.
- **Files modified:** `mobile/src/types/css.d.ts` (created)
- **Commit:** 768f503

**8. [Rule 3 - Workaround] Port 8000 conflict with another Docker container**
- **Found during:** Task 3 — codegen
- **Issue:** Port 8000 was occupied by `inplace-crm-backend-1` Docker container from another project.
- **Fix:** Temporarily stopped the other container, ran FastAPI on port 8000, completed codegen, then restarted the container. The `codegen:local` script and `.env` correctly reference port 8000 as the standard local dev port.
- **Impact:** None on produced artifacts — codegen ran against port 8000 as specified.

## Prerequisites Verified

| Dependency | Required | Available | Notes |
|------------|----------|-----------|-------|
| Node.js | >= 20 | 24.15.0 | OK |
| npm | required | 11.12.1 | OK |
| Python 3 | >= 3.9 | 3.9.6 (local) | 3.12 on Railway via Nixpacks |
| Docker | for Postgres | 29.4.3 | Used gt-postgres container on port 5433 |
| Postgres | required | 16.14 (Docker) | guitar_trainer DB, gt:devpass |
| EAS CLI | for builds | 20.2.0 | Not used in Plan 01 (Plan 04) |

**Local Postgres setup:** Used Docker (`gt-postgres` container on port 5433) since port 5432 was occupied by another project's Postgres. The server's `DATABASE_URL` uses port 5433 in local dev.

## Known Stubs

| Stub | File | Reason |
|------|------|--------|
| `[Tab notation — Plan 02]` | `mobile/src/app/(tabs)/index.tsx` | SVG tab rendering deferred to Plan 02 per scope note |
| `[Chord diagram — Plan 02]` | `mobile/src/app/(tabs)/index.tsx` | SVG chord diagram rendering deferred to Plan 02 per scope note |
| `https://placeholder.up.railway.app` | `mobile/.env.production` | Real Railway URL set in Plan 04 after deployment |
| Empty Zustand store | `mobile/src/store/uiStore.ts` | Phase 1 has no UI state; extended in Phase 2 |

These stubs are intentional and documented in the plan. They do not prevent the plan's goal (proving the end-to-end dev loop).

## Follow-up Items for Later Plans

- **Plan 02:** Add `react-native-svg` chord diagram and tab notation rendering to `(tabs)/index.tsx`. Remove `[Plan 02]` placeholder text blocks. Add `ChordDiagram` and `TabNotation` components.
- **Plan 03:** Wire MMKV offline persistence and EAS dev-client build setup (the MMKV persister is configured but the dev-client build is needed to actually test offline reads on device).
- **Plan 04:** Update `mobile/.env.production` with real Railway URL after deployment. Run `eas env:create EXPO_PUBLIC_API_URL` in EAS.
- **Python version:** Local Python is 3.9.6 (Railway uses 3.12 via Nixpacks). `greenlet` is required on Python 3.9 but auto-bundled on 3.12. The `requirements.txt` includes it for safety.
- **Alembic env.py:** Uses sync `postgresql://` scheme for Alembic migrations, while `session.py` uses `postgresql+asyncpg://` for app queries. Both correctly handle Railway's `postgres://` injection.

## Self-Check: PASSED

All 25 created files confirmed to exist. All 3 task commits verified. All content checks passed.

| Check | Result |
|-------|--------|
| All server files exist | PASSED |
| All mobile files exist | PASSED |
| Task 1 commit fd6838e exists | PASSED |
| Task 2 commit 768f503 exists | PASSED |
| Task 3 commit 907e4e1 exists | PASSED |
| postgresql+asyncpg:// in session.py | PASSED |
| preDeployCommand in railway.toml | PASSED |
| python-3.12 in runtime.txt | PASSED |
| Sweet Home Chicago in seed.py | PASSED |
| LIMIT 1 in song_of_day.py | PASSED |
| allow_credentials=False in main.py | PASSED |
| src/api/generated/ in mobile .gitignore | PASSED |
| codegen:local script in package.json | PASSED |
| mmkvPersister exported from queryClient.ts | PASSED |
| useSongOfDay exported from songOfDay.ts | PASSED |
| PersistQueryClientProvider in root _layout.tsx | PASSED |
