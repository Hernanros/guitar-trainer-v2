# Phase 1: Foundation & Empty Loop — Research

**Researched:** 2026-07-16
**Domain:** Expo + EAS / FastAPI + Railway + Postgres / MMKV / OpenAPI codegen / react-native-svg
**Confidence:** HIGH (core stack) / MEDIUM (codegen workflow, Railway config)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Phase 1 ships the full Phase 3-ready payload shape with hardcoded dummy content. Every field the AI teacher will eventually populate is present with realistic example data. Phase 3 replaces content, not schema.
- **D-02:** Pydantic models in FastAPI are the single source of truth. Mobile client's TypeScript types are generated from the FastAPI-emitted OpenAPI schema (tool: `openapi-typescript` or equivalent) at client build time. Generated file is gitignored.
- **D-03:** Semantic music JSON. Tab: `{ measures: [{ beats: [{ string, fret, duration }] }] }`. Chords: `{ name, positions: [{ string, fret, finger }] }`. Client computes SVG layout via `react-native-svg`.
- **D-04:** `GET /api/v1/song-of-day` — DB-backed with trivial selector. Reads single hardcoded row from a `songs` table. No `user_id` or `date` query params yet.

### Claude's Discretion
- Monorepo layout: single repo with top-level `mobile/` and `server/` directories
- No `user_id` column in Phase 1
- Expo Router (not React Navigation) for the three-tab shell
- Placeholder screens for Library/Toolkit: plain "Coming soon" text
- MMKV write-through on every successful response, no TTL
- Railway Git integration (auto-deploy on push to `main`) for FastAPI
- Manual `eas build --profile preview` for Phase 1

### Deferred Ideas (OUT OF SCOPE)
- Repo layout deep discussion — revisit at Phase 2 if uncomfortable
- Multi-user seams depth — Phase 2 topic
- Navigation library choice — Expo Router by default per research
- CI/EAS deploy automation — manual builds for Phase 1
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PLAT-01 | Expo + React Native app runs on iOS via EAS build | EAS build setup, iOS ad hoc provisioning, Apple Developer credentials |
| PLAT-02 | Expo + React Native app runs on Android via EAS build | EAS build setup, Android APK via internal distribution |
| PLAT-03 | FastAPI backend deployed to Railway with a persistent Postgres database | Railway Nixpacks / Procfile deployment, preDeployCommand for Alembic, DATABASE_URL wiring |
| PLAT-04 | App reads cached "today" data from MMKV when offline | MMKV v4 + NitroModules, TanStack Query persist-client, PersistQueryClientProvider pattern |
</phase_requirements>

---

## Summary

Expo SDK 57 (released 2026-07-15) ships React Native 0.86 with New Architecture enabled by default and not disableable. The stack is confirmed current and mature: `react-native-mmkv` v4 now uses NitroModules (JSI) instead of the old C++ bridge, which has one major consequence — **MMKV cannot run in Expo Go**. The mobile app must be developed against a dev client or EAS preview build from day one. This is the single largest planning surprise: the project cannot use Expo Go for iteration. EAS dev builds become the dev loop.

On the backend, Railway's canonical FastAPI deployment pattern is Nixpacks + Procfile (no Dockerfile needed for a greenfield Python service). The critical Railway-specific config is `preDeployCommand` in `railway.toml` for Alembic migrations — this runs between build and deploy on Railway's private network with DATABASE_URL already injected. The Alembic-then-uvicorn `&&` chain in `startCommand` is an equally valid alternative. Railway injects `$PORT` at runtime; uvicorn must bind to `0.0.0.0:$PORT`.

The Pydantic-to-OpenAPI-to-TypeScript codegen loop (D-02) uses `openapi-typescript` v7 pointing at the live FastAPI `/openapi.json` endpoint. This is a manual `npm run codegen` script in `mobile/`; it is not automatic at build time because the FastAPI server must be running. The generated `.d.ts` file lives at `mobile/src/api/generated/schema.d.ts` and is gitignored.

**Primary recommendation:** Stand up dev-client EAS builds immediately (before writing any feature code) because MMKV's NitroModules requirement blocks Expo Go — the entire Phase 1 dev loop depends on getting at least one platform's dev-client build installed on a real device or simulator.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Song-of-day fetch | API / Backend | — | `GET /api/v1/song-of-day` returns full payload; mobile is a reader only |
| Offline cache (MMKV) | Mobile / Client | — | MMKV is client-local; no server involvement in offline reads |
| Tab/chord SVG rendering | Mobile / Client | — | D-03 explicitly moves layout computation to the client |
| Schema source of truth | API / Backend | Mobile / Client (generated types) | Pydantic models → OpenAPI JSON → generated `.d.ts` per D-02 |
| Database + migrations | Database / Storage | API / Backend | Alembic owns schema evolution; FastAPI owns query execution |
| Bottom-tab navigation | Mobile / Client | — | Expo Router file-based tabs in `app/(tabs)/` |
| Three-tab shell layout | Mobile / Client | — | Expo Router `(tabs)/_layout.tsx` |
| Server-state management | Mobile / Client | — | TanStack Query `useQuery` with `PersistQueryClientProvider` |
| UI state management | Mobile / Client | — | Zustand (no UI state in Phase 1 beyond navigation) |

---

## Standard Stack

### Core — Mobile

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `expo` | 57.0.6 | SDK runtime, build tooling | Current latest; includes RN 0.86 [VERIFIED: npm registry] |
| `react-native` | 0.86.0 | Core framework | Included via Expo SDK 57; New Architecture always-on [VERIFIED: npm registry] |
| `expo-router` | 57.0.6 | File-based navigation including bottom tabs | Current default for new Expo projects; same version as SDK [VERIFIED: npm registry] |
| `react-native-mmkv` | 4.3.2 | Offline key-value storage | Locked in RECOVERED-DESIGN.md; ~30x faster than AsyncStorage [VERIFIED: npm registry] |
| `react-native-nitro-modules` | 0.36.1 | Required peer for MMKV v4 (NitroModules JSI bridge) | Mandatory since MMKV v4 dropped old C++ bridge [VERIFIED: npm registry] |
| `react-native-svg` | 15.15.5 | Tab + chord diagram rendering via SVG primitives | Locked in RECOVERED-DESIGN.md; software-mansion, widely used [VERIFIED: npm registry] |
| `zustand` | 5.0.14 | UI state management | Locked in RECOVERED-DESIGN.md; minimal boilerplate [VERIFIED: npm registry] |
| `@tanstack/react-query` | 5.101.2 | Server state management + query caching | Locked in RECOVERED-DESIGN.md [VERIFIED: npm registry] |
| `@tanstack/react-query-persist-client` | 5.101.2 | `PersistQueryClientProvider` wrapping TanStack with MMKV | Official TanStack persistence plugin [VERIFIED: npm registry] |
| `@tanstack/query-async-storage-persister` | 5.101.2 | `createAsyncStoragePersister` adapter for custom storage | Official TanStack persister factory [VERIFIED: npm registry] |
| `openapi-typescript` | 7.13.0 | Generates TypeScript types from FastAPI OpenAPI schema | Types-only, zero runtime cost; D-02 tooling choice [VERIFIED: npm registry] |

### Core — Server

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `fastapi` | 0.128.8 (latest) | HTTP framework + OpenAPI generation | Locked in stack; pydantic-native [VERIFIED: PyPI] |
| `pydantic` | 2.13.4 (latest) | Request/response models and JSON schema | Pydantic v2; FastAPI uses it natively [VERIFIED: PyPI] |
| `uvicorn` | 0.39.0 (latest) | ASGI server | Standard FastAPI runtime server [VERIFIED: PyPI] |
| `sqlalchemy` | 2.0.51 (latest) | ORM / query layer | SQLAlchemy 2.0 async style [VERIFIED: PyPI] |
| `asyncpg` | 0.31.0 (latest) | Async Postgres driver | Required for SQLAlchemy async + Postgres [VERIFIED: PyPI] |
| `alembic` | 1.16.5 (latest) | DB migrations | Standard migration tool for SQLAlchemy [VERIFIED: PyPI] |

### Supporting — Mobile

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `expo-dev-client` | (via Expo SDK) | Run dev builds without Expo Go | Required from day 1 because MMKV needs native code |
| `react-native-screens` | ^4.25.2 | Native navigation screen containers | Peer dep of expo-router; auto-installed |
| `react-native-safe-area-context` | >=5.4.0 | Notch/home-bar safe area insets | Peer dep of expo-router; needed for tab bar |
| `react-native-gesture-handler` | ^2.32 | Gesture support in navigation | Peer dep of expo-router; auto-installed |
| `react-native-reanimated` | ^4.5 | Animation support | Peer dep of expo-router; auto-installed |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `openapi-typescript` (types only) | `@hey-api/openapi-ts` (full client) | hey-api generates a full fetcher; adds runtime dependency and coupling; types-only is lighter and lets us control fetch layer |
| Nixpacks + Procfile | Dockerfile | Dockerfile gives more control but adds maintenance burden; Nixpacks auto-detects Python and is zero-config for greenfield |
| `preDeployCommand` for Alembic | `alembic upgrade head && uvicorn ...` in startCommand | Both work; preDeployCommand is Railway's native migration slot — cleaner separation of concerns |
| `asyncpg` | `psycopg2-binary` | psycopg2-binary is sync; asyncpg is required for SQLAlchemy 2.0 async mode |

### Mobile Installation

```bash
# From mobile/ directory
npx expo install react-native-mmkv react-native-nitro-modules react-native-svg zustand
npx expo install @tanstack/react-query @tanstack/react-query-persist-client @tanstack/query-async-storage-persister
npm install --save-dev openapi-typescript
```

### Server Installation

```bash
# From server/ directory
pip install fastapi uvicorn sqlalchemy asyncpg alembic pydantic
# requirements.txt for Railway
```

---

## Package Legitimacy Audit

> slopcheck was not available in this environment. All packages below were manually verified against official repositories and npm/PyPI registries. No packages have postinstall scripts.

| Package | Registry | Age | Source Repo | Postinstall | Disposition |
|---------|----------|-----|-------------|-------------|-------------|
| `expo` | npm | ~10 yrs | github.com/expo/expo | none | Approved |
| `expo-router` | npm | ~3 yrs | github.com/expo/expo | none | Approved |
| `react-native` | npm | ~11 yrs | github.com/facebook/react-native | none | Approved |
| `react-native-mmkv` | npm | ~5 yrs (2021) | github.com/mrousavy/react-native-mmkv | none | Approved |
| `react-native-nitro-modules` | npm | ~1 yr (2025) | github.com/mrousavy/nitro | none | Approved — same author as MMKV (mrousavy), explicit peer dep |
| `react-native-svg` | npm | ~8 yrs | github.com/software-mansion/react-native-svg | none | Approved |
| `zustand` | npm | ~6 yrs | github.com/pmndrs/zustand | none | Approved |
| `@tanstack/react-query` | npm | ~7 yrs | github.com/TanStack/query | none | Approved |
| `@tanstack/react-query-persist-client` | npm | ~4 yrs | github.com/TanStack/query | none | Approved |
| `@tanstack/query-async-storage-persister` | npm | ~3 yrs | github.com/TanStack/query | none | Approved |
| `openapi-typescript` | npm | ~6 yrs (2020) | github.com/openapi-ts/openapi-typescript | none | Approved |
| `fastapi` | PyPI | ~7 yrs | github.com/fastapi/fastapi | — | Approved |
| `pydantic` | PyPI | ~8 yrs | github.com/pydantic/pydantic | — | Approved |
| `sqlalchemy` | PyPI | ~20 yrs | github.com/sqlalchemy/sqlalchemy | — | Approved |
| `alembic` | PyPI | ~13 yrs | github.com/sqlalchemy/alembic | — | Approved |
| `asyncpg` | PyPI | ~9 yrs | github.com/MagicStack/asyncpg | — | Approved |
| `uvicorn` | PyPI | ~7 yrs | github.com/encode/uvicorn | — | Approved |

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

*slopcheck was unavailable — all packages tagged `[ASSUMED]` for legitimacy. All are well-known ecosystem standards with official GitHub organizations. Planner may add `checkpoint:human-verify` before install if desired, but risk is low.*

---

## Architecture Patterns

### System Architecture Diagram

```
[Mobile App — Expo + RN 0.86]
  |
  |-- Expo Router (tabs)
  |     |-- (tabs)/index.tsx         Today tab
  |     |-- (tabs)/library.tsx       Placeholder
  |     `-- (tabs)/toolkit.tsx       Placeholder
  |
  |-- TanStack Query (useQuery)
  |     |   staleTime: Infinity, gcTime: 24h
  |     |
  |     `-- PersistQueryClientProvider
  |           |
  |           `-- MMKV persister (write-through on success)
  |                 key: "REACT_QUERY_OFFLINE_CACHE"
  |
  |-- API fetch: GET $EXPO_PUBLIC_API_URL/api/v1/song-of-day
  |
  |-- Offline path: MMKV returns cached query data when network absent
  |
  `-- react-native-svg: renders tab/chord from JSON data

[FastAPI — Railway service]
  |
  |-- preDeployCommand: alembic upgrade head
  |
  |-- GET /api/v1/song-of-day
  |     `-- SELECT * FROM songs LIMIT 1
  |
  |-- GET /openapi.json            (codegen source for mobile)
  |
  `-- CORSMiddleware               (allow_origins=["*"] for POC)

[Postgres — Railway add-on]
  |
  `-- songs table (single row, full Phase 3-ready payload shape)

[OpenAPI Codegen — developer workflow]
  |
  |-- Developer runs: npm run codegen (in mobile/)
  |     `-- npx openapi-typescript $EXPO_PUBLIC_API_URL/openapi.json -o src/api/generated/schema.d.ts
  |
  `-- Generated schema.d.ts is gitignored; imported throughout mobile/
```

### Recommended Project Structure

```
guitar-trainer-v2/               # repo root
├── mobile/                      # Expo app
│   ├── app/
│   │   ├── _layout.tsx          # Root layout (Stack with headerShown:false)
│   │   └── (tabs)/
│   │       ├── _layout.tsx      # TabLayout: Today / Library / Toolkit
│   │       ├── index.tsx        # Today tab (song-of-day screen)
│   │       ├── library.tsx      # Placeholder "Coming soon"
│   │       └── toolkit.tsx      # Placeholder "Coming soon"
│   ├── src/
│   │   ├── api/
│   │   │   ├── generated/
│   │   │   │   └── schema.d.ts  # GITIGNORED — generated from FastAPI
│   │   │   ├── queryClient.ts   # TanStack QueryClient + MMKV persister
│   │   │   └── songOfDay.ts     # useQuery hook for /api/v1/song-of-day
│   │   ├── components/
│   │   │   ├── TabNotation.tsx  # react-native-svg tab renderer
│   │   │   └── ChordDiagram.tsx # react-native-svg chord renderer
│   │   └── store/
│   │       └── uiStore.ts       # Zustand (empty in Phase 1)
│   ├── assets/                  # Icons, splash, fonts
│   ├── app.json                 # Expo config (bundleId, package, SDK version)
│   ├── eas.json                 # EAS build profiles
│   ├── .env                     # EXPO_PUBLIC_API_URL=http://localhost:8000
│   ├── .env.production          # EXPO_PUBLIC_API_URL=https://your-railway.app
│   └── package.json
│
├── server/                      # FastAPI service
│   ├── app/
│   │   ├── main.py              # FastAPI app, CORS, router registration
│   │   ├── api/
│   │   │   └── v1/
│   │   │       └── song_of_day.py  # GET /api/v1/song-of-day router
│   │   ├── models/
│   │   │   ├── song.py          # Pydantic models (SongResponse, Tab, Chord…)
│   │   │   └── db.py            # SQLAlchemy models
│   │   └── db/
│   │       ├── session.py       # async engine + SessionLocal
│   │       └── seed.py          # Hardcoded song row insert (idempotent)
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   │       └── 0001_initial_songs_table.py
│   ├── alembic.ini
│   ├── requirements.txt
│   ├── Procfile                 # web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
│   └── railway.toml             # preDeployCommand, healthcheckPath
│
├── .planning/                   # GSD planning artifacts
├── CLAUDE.md
└── README.md
```

### Pattern 1: Expo Router Three-Tab Layout

**What:** File-based bottom tabs using `app/(tabs)/_layout.tsx`
**When to use:** All navigation in Phase 1 (3 tabs: Today, Library, Toolkit)

```tsx
// Source: https://docs.expo.dev/router/advanced/tabs/
// mobile/app/(tabs)/_layout.tsx
import { Tabs } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';

export default function TabLayout() {
  return (
    <Tabs screenOptions={{ tabBarActiveTintColor: '#E07B39' }}>
      <Tabs.Screen
        name="index"
        options={{
          title: 'Today',
          tabBarIcon: ({ color }) => (
            <Ionicons name="musical-notes" size={24} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="library"
        options={{
          title: 'Library',
          tabBarIcon: ({ color }) => (
            <Ionicons name="library" size={24} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="toolkit"
        options={{
          title: 'Toolkit',
          tabBarIcon: ({ color }) => (
            <Ionicons name="construct" size={24} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}
```

Root layout must suppress the header for the tabs group:

```tsx
// mobile/app/_layout.tsx
import { Stack } from 'expo-router';

export default function RootLayout() {
  return (
    <Stack>
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
    </Stack>
  );
}
```

### Pattern 2: TanStack Query + MMKV Offline Persistence

**What:** Wrap the app in `PersistQueryClientProvider` so that cached query results survive app kills. MMKV is the storage backend.
**When to use:** Root of the app, wrapping all screens that need offline access.

```tsx
// Source: https://github.com/mrousavy/react-native-mmkv/blob/main/docs/WRAPPER_REACT_QUERY.md
// mobile/src/api/queryClient.ts
import { QueryClient } from '@tanstack/react-query';
import { createAsyncStoragePersister } from '@tanstack/query-async-storage-persister';
import { MMKV } from 'react-native-mmkv';

const mmkv = new MMKV({ id: 'query-cache' });

const mmkvStorage = {
  setItem: (key: string, value: string) => { mmkv.set(key, value); },
  getItem: (key: string) => mmkv.getString(key) ?? null,
  removeItem: (key: string) => { mmkv.delete(key); },
};

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: Infinity,   // Phase 1: hardcoded data never goes stale
      gcTime: 1000 * 60 * 60 * 24, // 24h in cache
    },
  },
});

export const mmkvPersister = createAsyncStoragePersister({
  storage: mmkvStorage,
  throttleTime: 1000,
});
```

```tsx
// mobile/app/_layout.tsx (complete version)
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client';
import { queryClient, mmkvPersister } from '../src/api/queryClient';

export default function RootLayout() {
  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{ persister: mmkvPersister }}
    >
      <Stack>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      </Stack>
    </PersistQueryClientProvider>
  );
}
```

### Pattern 3: Song-of-Day Query Hook

```tsx
// mobile/src/api/songOfDay.ts
import { useQuery } from '@tanstack/react-query';
import type { components } from './generated/schema';

type SongResponse = components['schemas']['SongResponse'];

async function fetchSongOfDay(): Promise<SongResponse> {
  const url = `${process.env.EXPO_PUBLIC_API_URL}/api/v1/song-of-day`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<SongResponse>;
}

export function useSongOfDay() {
  return useQuery({
    queryKey: ['song-of-day'],
    queryFn: fetchSongOfDay,
    // staleTime: Infinity inherited from queryClient defaults
  });
}
```

### Pattern 4: FastAPI Pydantic Models (D-03 compliant)

**What:** Concrete Pydantic models for the full Phase 3-ready payload shape per D-01 and D-03.

```python
# server/app/models/song.py
# Source: Pydantic v2 official docs + D-03 decision
from pydantic import BaseModel
from typing import Optional

class Note(BaseModel):
    string: int        # 1=high e … 6=low E
    fret: int          # 0 = open
    duration: str      # "quarter" | "eighth" | "half" | "whole" | "sixteenth"

class Beat(BaseModel):
    notes: list[Note]  # supports chords within a beat

class Measure(BaseModel):
    beats: list[Beat]
    time_signature: str = "4/4"  # e.g. "4/4", "3/4", "6/8"

class Tab(BaseModel):
    measures: list[Measure]
    tuning: list[str] = ["E", "A", "D", "G", "B", "e"]  # standard

class ChordPosition(BaseModel):
    string: int   # 1=high e … 6=low E
    fret: int     # 0=open, -1=muted
    finger: Optional[int] = None  # 1=index … 4=pinky, None=open/muted

class Chord(BaseModel):
    name: str
    positions: list[ChordPosition]
    barre_fret: Optional[int] = None  # for full/partial barres
    base_fret: int = 1  # starting fret of diagram window

class TechniqueNote(BaseModel):
    heading: str
    body: str

class Breakdown(BaseModel):
    tab: Tab
    chords: list[Chord]
    technique_notes: list[TechniqueNote]
    # Phase 3 will add: practice_loops, difficulty_tags — additive, no breaking change

class SongResponse(BaseModel):
    id: int
    title: str
    artist: str
    genre: str
    difficulty: str   # "intermediate" | "advanced"
    bpm: int
    key: str
    breakdown: Breakdown

    model_config = {"from_attributes": True}
```

**Why `Beat` holds a list of `Note`:** A guitar beat can be a single note or a chord played simultaneously (3 notes at once). The `notes` list on `Beat` captures this without breaking the tab/measure hierarchy.

**Forward-compat note (D-01):** `Breakdown` is designed for additive Phase 3 fields (`practice_loops`, `difficulty_tags`). Because pydantic `model_config = {"populate_by_name": True}` is the default in v2, the client's generated types will accept additional fields automatically via TypeScript's open type system.

### Pattern 5: FastAPI App Setup

```python
# server/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.song_of_day import router as song_router

app = FastAPI(title="Guitar Trainer API", version="1.0.0")

# CORS: allow all origins for POC mobile client
# Mobile apps don't send an Origin header for same-origin requests,
# but allow * for Expo dev builds hitting the Railway service
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # Phase 1 POC — narrow in Phase 2
    allow_credentials=False,  # must be False when allow_origins=["*"]
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(song_router, prefix="/api/v1")

@app.get("/healthz")
async def health():
    return {"status": "ok"}
```

### Pattern 6: Railway Configuration

```toml
# server/railway.toml
[build]
builder = "nixpacks"

[deploy]
preDeployCommand = "alembic upgrade head"
startCommand = "uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1"
healthcheckPath = "/healthz"
healthcheckTimeout = 30
restartPolicyType = "on_failure"
```

```
# server/Procfile (Nixpacks fallback if railway.toml not used)
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### Pattern 7: EAS Build Profiles

```json
// mobile/eas.json
{
  "cli": {
    "version": ">= 20.0.0"
  },
  "build": {
    "development": {
      "developmentClient": true,
      "distribution": "internal",
      "ios": {
        "simulator": true
      }
    },
    "preview": {
      "distribution": "internal",
      "ios": {
        "resourceClass": "m-medium"
      },
      "android": {
        "buildType": "apk"
      }
    },
    "production": {}
  }
}
```

**Three profiles explained:**
- `development` — for day-to-day iteration; iOS targets the simulator (no Apple credentials needed); Android device via internal APK
- `preview` — the Phase 1 "ship" profile; iOS ad hoc on real device (requires UDID registration); Android APK
- `production` — placeholder for App Store / Play Store submission (Phase 5)

### Pattern 8: OpenAPI Codegen Dev Loop

```json
// mobile/package.json (relevant scripts section)
{
  "scripts": {
    "codegen": "npx openapi-typescript $EXPO_PUBLIC_API_URL/openapi.json -o src/api/generated/schema.d.ts",
    "codegen:local": "npx openapi-typescript http://localhost:8000/openapi.json -o src/api/generated/schema.d.ts"
  }
}
```

Run manually after any Pydantic model change: `cd mobile && npm run codegen:local` (FastAPI dev server must be running).

```
# mobile/.gitignore additions
src/api/generated/
```

### Pattern 9: Minimal react-native-svg Chord Diagram

```tsx
// mobile/src/components/ChordDiagram.tsx
// No third-party guitar library needed — hand-rolled is correct per D-03
// Source: react-native-svg docs (software-mansion/react-native-svg)
import React from 'react';
import { Svg, Line, Circle, Text, G } from 'react-native-svg';
import type { components } from '../api/generated/schema';

type Chord = components['schemas']['Chord'];

const STRING_SPACING = 24;
const FRET_SPACING = 28;
const STRINGS = 6;
const FRETS_SHOWN = 4;

export function ChordDiagram({ chord }: { chord: Chord }) {
  const width = (STRINGS - 1) * STRING_SPACING + 40;
  const height = FRETS_SHOWN * FRET_SPACING + 60;

  return (
    <Svg width={width} height={height}>
      <G translateX={20} translateY={30}>
        {/* Fret lines */}
        {Array.from({ length: FRETS_SHOWN + 1 }).map((_, i) => (
          <Line
            key={`fret-${i}`}
            x1={0} y1={i * FRET_SPACING}
            x2={(STRINGS - 1) * STRING_SPACING} y2={i * FRET_SPACING}
            stroke="black" strokeWidth={i === 0 ? 3 : 1}
          />
        ))}
        {/* String lines */}
        {Array.from({ length: STRINGS }).map((_, i) => (
          <Line
            key={`string-${i}`}
            x1={i * STRING_SPACING} y1={0}
            x2={i * STRING_SPACING} y2={FRETS_SHOWN * FRET_SPACING}
            stroke="black" strokeWidth={1}
          />
        ))}
        {/* Finger dots */}
        {chord.positions
          .filter(p => p.fret > 0)
          .map((pos, idx) => {
            const x = (pos.string - 1) * STRING_SPACING;
            const y = (pos.fret - chord.base_fret) * FRET_SPACING + FRET_SPACING / 2;
            return (
              <Circle key={idx} cx={x} cy={y} r={10} fill="black" />
            );
          })}
        {/* Chord name */}
        <Text x={(STRINGS - 1) * STRING_SPACING / 2} y={-12}
          textAnchor="middle" fontSize={14} fontWeight="bold">
          {chord.name}
        </Text>
      </G>
    </Svg>
  );
}
```

### Anti-Patterns to Avoid

- **Using Expo Go for development:** MMKV v4 (NitroModules) cannot run in Expo Go. Every iteration requires a dev-client build. Do not attempt to prototype with Expo Go — it will fail at runtime with `NitroModules native module could not be found`.
- **Binding uvicorn to `::` on Railway:** Railway's internal routing translates IPv4 to IPv6, but client health checks may fail when using `::`. Bind to `0.0.0.0` for maximum compatibility.
- **allow_credentials=True with allow_origins=["*"]:** FastAPI/Starlette will reject this combination. Either narrow the origin list or keep `allow_credentials=False` (correct for a mobile-to-server API with no cookies).
- **Committing the generated schema.d.ts:** This file is derived from the server and must be gitignored. Committing it creates a maintenance trap when Pydantic models change.
- **Storing DATABASE_URL in requirements.txt or committed .env:** Railway injects DATABASE_URL automatically when a Postgres add-on is attached. Never hardcode it.
- **Using an AAB for Android internal distribution:** EAS's `buildType: "apk"` is required for internal distribution; AAB can only be installed via the Play Store.
- **Forgetting `base_fret` in the Chord model:** Guitar chord diagrams often start at fret 5 or 7. Without `base_fret`, the SVG renderer can't calculate correct y-offsets. It's in the Pydantic model above; do not drop it from the hardcoded seed row.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| TypeScript API types | Manual interface definitions | `openapi-typescript` + FastAPI `/openapi.json` | Types drift; codegen is zero-cost after setup |
| Offline cache invalidation | Custom cache + TTL logic | `PersistQueryClientProvider` + MMKV persister | TanStack handles serialization, rehydration, GC |
| Database migrations | Manual SQL files | Alembic autogenerate | Alembic handles column diffs, down migrations, stamp |
| iOS code signing | Self-managed certs | EAS managed credentials | EAS rotates, stores, and shares credentials across team |
| CORS handling in FastAPI | Custom middleware | `CORSMiddleware` from `fastapi.middleware.cors` | Handles preflight, headers correctly |
| SVG guitar chord library | Third-party RN guitar lib | `react-native-svg` primitives (hand-rolled) | No maintained RN-native guitar chord lib exists; `react-guitar-chord` (npm) is web-only and last published 2018; hand-rolled SVG is 40 lines and fully D-03-compliant |

**Key insight:** The chord/tab rendering is the one place where hand-rolling IS correct because no maintained React Native library accepts the D-03 semantic JSON shape. `react-guitar-chord` (last updated 2018) is web-only; `react-chords` is not on npm at all as of 2026.

---

## Common Pitfalls

### Pitfall 1: MMKV NitroModules Error in Expo Go

**What goes wrong:** App crashes at startup with `Error: Failed to get NitroModules: The native "NitroModules" Turbo/Native-Module could not be found.`
**Why it happens:** MMKV v4 requires native code compiled into the app bundle. Expo Go is a prebuilt shell that does not include arbitrary native modules.
**How to avoid:** Never start the dev loop with `npx expo start` pointing at Expo Go. Always use `npx expo start --dev-client` after installing a dev-client build from EAS (`eas build --profile development`).
**Warning signs:** Any `expo start` that shows the Expo Go QR code will fail once MMKV is imported.

### Pitfall 2: iOS Device Build Without UDID Registration

**What goes wrong:** Ad hoc build (`preview` profile) installs but the device was not in the provisioning profile — the app crashes at launch with "Unable to Install App."
**Why it happens:** iOS ad hoc provisioning requires each device's UDID to be registered before the build is created. Adding UDIDs after the build requires a new build.
**How to avoid:** Run `eas device:create` before running `eas build --profile preview --platform ios`. Register Hernan's device UDID first.
**Warning signs:** IPA installs successfully but immediately crashes on first launch.

### Pitfall 3: Railway Postgres DATABASE_URL Protocol Mismatch

**What goes wrong:** SQLAlchemy async engine raises `TypeError: AsyncEngine cannot be used in sync context` or `sqlalchemy.exc.ArgumentError: Could not parse rfc1738 URL from string 'postgres://...'`
**Why it happens:** Railway injects `DATABASE_URL` with the scheme `postgres://` (PostgreSQL standard). SQLAlchemy 2.0 async with asyncpg requires `postgresql+asyncpg://`.
**How to avoid:** In `server/app/db/session.py`, rewrite the URL scheme:
```python
import os
raw_url = os.environ["DATABASE_URL"]
DATABASE_URL = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
```
**Warning signs:** Server starts locally but crashes immediately on Railway after connecting to the Postgres add-on.

### Pitfall 4: EAS Build Picks Wrong iOS Distribution Type

**What goes wrong:** `eas build --profile preview --platform ios` creates an App Store build instead of an ad hoc build, requires App Store Connect credentials, and cannot be installed on a device.
**Why it happens:** Not setting `"distribution": "internal"` in the preview profile in `eas.json`.
**How to avoid:** Ensure `eas.json` preview profile has `"distribution": "internal"`. EAS will then auto-select ad hoc provisioning.
**Warning signs:** EAS CLI prompts for App Store Connect API keys during a "preview" build.

### Pitfall 5: CORS allow_credentials + allow_origins Conflict

**What goes wrong:** FastAPI raises `RuntimeError: CORS: allow_credentials=True cannot be used with allow_origins='*'`
**Why it happens:** Browser security restriction; credentials (cookies) cannot be sent cross-origin to a wildcard origin.
**How to avoid:** Keep `allow_credentials=False` when using `allow_origins=["*"]`. Mobile apps don't use browser cookies anyway — they send tokens in headers if needed (Phase 2+).
**Warning signs:** FastAPI startup exception mentioning CORS middleware configuration.

### Pitfall 6: Alembic `env.py` Hardcoded Database URL

**What goes wrong:** Alembic migrations fail on Railway because `env.py` references a hardcoded local URL or reads from a `.env` file not present on Railway.
**Why it happens:** Default Alembic template uses `config.get_main_option("sqlalchemy.url")` which reads `alembic.ini` — fine locally, fails on Railway.
**How to avoid:** Override in `alembic/env.py`:
```python
import os
config.set_main_option("sqlalchemy.url",
    os.environ["DATABASE_URL"].replace("postgres://", "postgresql+asyncpg://", 1))
```
**Warning signs:** `preDeployCommand` exits with non-zero code; Railway deploy log shows "no DATABASE_URL" or connection refused.

### Pitfall 7: Generated Types File Checked In

**What goes wrong:** `src/api/generated/schema.d.ts` is committed; when a Pydantic model changes, the committed file drifts from reality; TypeScript compiler passes but runtime data is wrong.
**How to avoid:** Add `src/api/generated/` to `mobile/.gitignore` before first commit. Add a `TODO` comment in the codegen script reminding developers to regenerate.
**Warning signs:** TypeScript compiles cleanly but the app receives fields that don't match the type definition, causing silent `undefined` access.

---

## Code Examples

### Full Phase 3-Ready Hardcoded Seed Row (D-01 compliant)

The one hardcoded song should feel real. Suggested: "Sweet Home Chicago" (Robert Johnson/Blues Brothers) — recognizable, intermediate difficulty, blues style aligned with Hernan's profile (blues focus from RECOVERED-DESIGN.md).

```python
# server/app/db/seed.py
HARDCODED_SONG = {
    "title": "Sweet Home Chicago",
    "artist": "Robert Johnson",
    "genre": "blues",
    "difficulty": "intermediate",
    "bpm": 112,
    "key": "E",
    "breakdown": {
        "tab": {
            "tuning": ["E", "A", "D", "G", "B", "e"],
            "measures": [
                {
                    "time_signature": "4/4",
                    "beats": [
                        {"notes": [{"string": 6, "fret": 0, "duration": "quarter"}]},
                        {"notes": [{"string": 5, "fret": 2, "duration": "eighth"},
                                   {"string": 4, "fret": 2, "duration": "eighth"}]},
                        {"notes": [{"string": 6, "fret": 0, "duration": "quarter"}]},
                        {"notes": [{"string": 5, "fret": 2, "duration": "quarter"}]},
                    ]
                }
            ]
        },
        "chords": [
            {
                "name": "E7",
                "base_fret": 1,
                "positions": [
                    {"string": 6, "fret": 0, "finger": None},
                    {"string": 5, "fret": 2, "finger": 2},
                    {"string": 4, "fret": 0, "finger": None},
                    {"string": 3, "fret": 1, "finger": 1},
                    {"string": 2, "fret": 0, "finger": None},
                    {"string": 1, "fret": 0, "finger": None},
                ]
            },
            {
                "name": "A7",
                "base_fret": 1,
                "positions": [
                    {"string": 6, "fret": -1, "finger": None},
                    {"string": 5, "fret": 0, "finger": None},
                    {"string": 4, "fret": 2, "finger": 2},
                    {"string": 3, "fret": 0, "finger": None},
                    {"string": 2, "fret": 2, "finger": 3},
                    {"string": 1, "fret": 0, "finger": None},
                ]
            }
        ],
        "technique_notes": [
            {
                "heading": "Shuffle Feel",
                "body": "This is a 12-bar blues in E with a shuffle rhythm. Play the bass note on beats 1 and 3, and the chord on 2 and 4. Swing the eighth notes — long-short, long-short."
            },
            {
                "heading": "Fretting Hand",
                "body": "Keep your thumb behind the neck on E7. For A7, let strings 6 mute naturally — your thumb can wrap over if comfortable."
            }
        ]
    }
}
```

### Alembic Initial Migration (songs table)

```python
# server/alembic/versions/0001_initial_songs_table.py
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

def upgrade() -> None:
    op.create_table(
        'songs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('artist', sa.String(255), nullable=False),
        sa.Column('genre', sa.String(100), nullable=True),
        sa.Column('difficulty', sa.String(50), nullable=True),
        sa.Column('bpm', sa.Integer(), nullable=True),
        sa.Column('key', sa.String(10), nullable=True),
        sa.Column('breakdown', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )

def downgrade() -> None:
    op.drop_table('songs')
```

**Why JSONB:** The `breakdown` field is a rich nested object that will be replaced wholesale by Phase 3's AI output. JSONB is index-queryable and avoids a complex multi-table schema for what is fundamentally an opaque document in Phase 1.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| MMKV with C++ JSI bridge | MMKV v4 with NitroModules | MMKV v4 (2025) | Requires `react-native-nitro-modules` peer; cannot use Expo Go |
| React Native Old Architecture (Bridge) | New Architecture always-on | RN 0.82 / Expo SDK 55 | Cannot be disabled in SDK 57; `newArchEnabled: false` ignored |
| `createExpoApp` without `--template` | `npx create-expo-app@latest --template default@sdk-57` | SDK 57 (2026-07-15) | Explicit SDK pinning recommended during SDK transition windows |
| openapi-typescript v6 | openapi-typescript v7 | v7.0 (late 2024) | Output format changed; v7 is the current stable |
| Railway Nixpacks (deprecated term) | Railway Nixpacks (current) | Ongoing | Nixpacks is Railway's active builder; Dockerfile is optional override |
| `startCommand` includes migration | `preDeployCommand` for migration | Railway config-as-code | Cleaner separation; Railway runs preDeployCommand in private network |

**Deprecated/outdated:**
- `react-guitar-chord` (npm): Last published 2018, web-only, no RN support. Do not use.
- `react-chords`: Not published on npm as of 2026. The tombatossals GitHub repo exists but the npm package does not. Do not use.
- Expo Go for development with native modules: Dead end from MMKV v4 onward.
- SQLAlchemy 1.x patterns (`Session` as context manager, non-async): Use SQLAlchemy 2.0 async style.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `preDeployCommand` in `railway.toml` runs in the same environment as the deployed container and has access to `DATABASE_URL` | Railway Configuration Pattern | Migration fails silently; database never migrated; app crashes on first request |
| A2 | The codegen dev loop (npm run codegen:local) is manual and not triggered by EAS build automatically | OpenAPI Codegen Loop | Generated types diverge from API silently; TypeScript compiles but runtime is incorrect |
| A3 | Railway Nixpacks auto-detects Python from `requirements.txt` without a `runtime.txt` | FastAPI Standard Stack | Build fails if Nixpacks cannot determine Python version; fix: add `python-3.12` to `runtime.txt` |
| A4 | `EXPO_PUBLIC_API_URL` set via EAS environment dashboard is available at build time (not just runtime) | Environment Variables | App built with empty/wrong API URL; all fetches fail |
| A5 | Expo SDK 57 + expo-router 57 does not require any additional setup for the bottom-tab layout beyond what is documented | Expo Router Pattern | Build fails due to unlisted peer dependency or config plugin requirement |

---

## Open Questions

1. **Python version for Railway Nixpacks**
   - What we know: Nixpacks auto-detects Python from `requirements.txt`; recent versions default to Python 3.12
   - What's unclear: Whether a `runtime.txt` or `.python-version` file is required for Railway's Nixpacks builder to select the correct Python 3.12
   - Recommendation: Add a `runtime.txt` containing `python-3.12` to `server/` to make the version explicit — takes 30 seconds and eliminates ambiguity

2. **Apple Developer membership timing**
   - What we know: iOS `preview` (ad hoc) builds require an active Apple Developer Program membership ($99/year)
   - What's unclear: Whether Hernan already has an active Apple Developer account
   - Recommendation: Confirm before planning Wave 1 of iOS build tasks; if not enrolled, the iOS build tasks are blocked until enrollment completes (can take 24–48 hours)

3. **`EXPO_PUBLIC_API_URL` per environment sourcing**
   - What we know: EAS supports per-environment variables via `eas env:create`; local dev uses `.env`
   - What's unclear: Whether the Railway service URL is stable (Railway may reassign URLs on service recreation)
   - Recommendation: Use Railway's "custom domain" or note that the Railway-assigned `*.up.railway.app` URL is stable for a given service name

4. **songs table: Alembic autogenerate vs manual migration**
   - What we know: Alembic can autogenerate from SQLAlchemy models
   - What's unclear: Whether to use autogenerate or write the migration manually for the initial table
   - Recommendation: Write the initial migration manually (shown in code examples above) — autogenerate adds SQLAlchemy ORM model dependency that Phase 1 may not need for a single query

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Node.js | Mobile build, codegen, EAS | ✓ | 24.15.0 | — |
| npm | Package management | ✓ | 11.12.1 | — |
| EAS CLI | EAS build commands | ✓ | 20.2.0 | — |
| npx | create-expo-app, codegen | ✓ | 11.12.1 | — |
| Python 3 | FastAPI server | ✓ | 3.9.6 (local) | 3.12 on Railway via Nixpacks |
| pip | Python packages | ✓ | 21.2.4 | — |
| Docker | (optional local dev) | ✓ | 29.4.3 | Not required — Nixpacks on Railway |
| Railway CLI | Manual Railway ops | ✓ | 4.59.0 | Railway dashboard as fallback |
| uv | Modern Python packaging | ✗ | — | pip + requirements.txt (sufficient) |
| ctx7 | Context7 documentation | ✗ | — | Official docs via WebFetch (used) |
| Expo Go | Mobile dev loop | NOT USABLE | — | EAS dev-client build (required) |
| Apple Developer Account | iOS builds | UNKNOWN | — | Blocks PLAT-01 until confirmed |
| Android device / emulator | Android builds | ASSUMED | — | iOS-first if Android device unavailable |

**Missing dependencies with no fallback:**
- Apple Developer Program membership — required for PLAT-01 iOS builds; must confirm before planning the iOS EAS build wave. If not enrolled, plan around Android-first for Phase 1 and add iOS as a deferred task.

**Missing dependencies with fallback:**
- Expo Go dev loop: replaced by EAS `development` profile + dev-client build. Slower iteration loop but fully functional.
- Python 3.9.6 (local vs Railway's 3.12): local development works fine on 3.9; Railway deploys 3.12 via Nixpacks. No code incompatibilities expected for the Phase 1 server stack.

---

## Security Domain

> `security_enforcement: true`, `security_asvs_level: 1`, `security_block_on: high` per config.json

### Applicable ASVS Categories (ASVS L1)

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | No auth in Phase 1; single-user POC |
| V3 Session Management | No | No sessions; stateless API |
| V4 Access Control | Minimal | No user-based access; single-row DB read; no write endpoints |
| V5 Input Validation | Partial | Pydantic validates all response serialization; Phase 1 has no user inputs |
| V6 Cryptography | No | No secrets stored client-side in Phase 1 |
| V9 Communication | Yes | HTTPS required in production (Railway provides TLS automatically) |

### Known Threat Patterns for This Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Direct object access without auth | Elevation of Privilege | Mitigated by POC design: no auth = no users = no private data. Phase 2 adds user_id checks. |
| SQL injection via song-of-day query | Tampering | `SELECT * FROM songs LIMIT 1` has no user-controlled input. SQLAlchemy parameterization handles future queries. |
| CORS wildcard abuse | Spoofing | POC has `allow_origins=["*"]`; acceptable for Phase 1 with no sensitive data. Narrow in Phase 2 when user data lands. |
| Exposed Railway service URL | Information Disclosure | Railway URL is public; no sensitive data served in Phase 1. Acceptable. |
| MMKV data access on rooted device | Info Disclosure | Phase 1 stores only the public song payload. No credentials or PII. Risk: LOW. |
| Hardcoded API URL in app bundle | Info Disclosure | `EXPO_PUBLIC_API_URL` is a public endpoint. Expected to be public. No risk. |

### Phase 1 Security Posture Summary

Phase 1 has a minimal attack surface: one GET endpoint, no auth, no user input, no PII, no secrets stored client-side. The highest security risk is the `allow_origins=["*"]` CORS config — acceptable for Phase 1 POC but must be narrowed when user data or auth is introduced in Phase 2. No HIGH-severity ASVS findings apply to Phase 1's scope.

---

## Sources

### Primary (HIGH confidence)

- npm registry — `expo@57.0.6`, `react-native@0.86.0`, `expo-router@57.0.6`, `react-native-mmkv@4.3.2`, `react-native-nitro-modules@0.36.1`, `react-native-svg@15.15.5`, `zustand@5.0.14`, `@tanstack/react-query@5.101.2`, `openapi-typescript@7.13.0` — all versions, publish dates, repositories verified
- PyPI registry — `fastapi@0.128.8`, `pydantic@2.13.4`, `uvicorn@0.39.0`, `sqlalchemy@2.0.51`, `asyncpg@0.31.0`, `alembic@1.16.5` — versions verified
- [Expo SDK 57 Changelog](https://expo.dev/changelog/sdk-57) — confirmed React Native 0.86, New Architecture always-on, no breaking changes
- [Expo Router Tabs docs](https://docs.expo.dev/router/advanced/tabs/) — `(tabs)/_layout.tsx` canonical pattern
- [EAS Build Internal Distribution docs](https://docs.expo.dev/build/internal-distribution/) — iOS ad hoc (100 device limit, UDID required), Android APK
- [EAS Build Setup docs](https://docs.expo.dev/build/setup/) — managed credentials, platform requirements
- [EAS Environment Variables docs](https://docs.expo.dev/eas/environment-variables/) — `EXPO_PUBLIC_` prefix, per-environment config
- [react-native-mmkv TanStack Query wrapper](https://github.com/mrousavy/react-native-mmkv/blob/main/docs/WRAPPER_REACT_QUERY.md) — exact storage adapter code
- [openapi-typescript introduction](https://openapi-ts.dev/introduction) — types-only, live URL support, v7 behavior
- [Railway config-as-code](https://docs.railway.com/reference/config-as-code) — `preDeployCommand` in `railway.toml`
- [Railway pre-deploy command docs](https://docs.railway.com/deployments/pre-deploy-command) — lifecycle, constraints

### Secondary (MEDIUM confidence)

- [React-native-mmkv DeepWiki Expo Configuration](https://deepwiki.com/mrousavy/react-native-mmkv/6.3-expo-configuration) — NitroModules + prebuild requirement confirmed by community
- [Railway FastAPI Guide](https://docs.railway.com/guides/fastapi) — Nixpacks + Hypercorn pattern (we use uvicorn variant)
- Multiple Railway community articles confirming `$PORT` env var and `0.0.0.0` binding requirement
- FastAPI CORS docs confirming `allow_credentials=False` required with `allow_origins=["*"]`

### Tertiary (LOW confidence)

- Community article about Railway `preDeployCommand` multi-command limitation (use shell script if needed)

---

## Metadata

**Confidence breakdown:**
- Standard stack (versions): HIGH — verified against npm/PyPI registries directly
- Architecture patterns: HIGH — verified against official Expo, Railway, TanStack docs
- MMKV / NitroModules Expo Go constraint: HIGH — confirmed by official repo and community (multiple sources)
- Codegen workflow: MEDIUM — live-server-required constraint is inferred from openapi-typescript CLI behavior; no single "official" workflow doc for this monorepo pattern
- Railway preDeployCommand: MEDIUM — Railway docs confirm the mechanism; Alembic-specific usage is community-verified

**Research date:** 2026-07-16
**Valid until:** 2026-08-16 (stable stack; SDK 57 just released — unlikely to move for 30+ days)
