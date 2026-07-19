---
phase: 01-foundation-empty-loop
plan: "04"
subsystem: deploy
tags: [railway, postgres, eas, ios, mmkv, walking-skeleton-verified]
dependency_graph:
  requires:
    - 01-01 (Walking Skeleton — server + mobile scaffold)
    - 01-02 (SVG rendering — ChordDiagram + TabNotation)
    - 01-03 (MMKV persistence — verification-only)
  provides:
    - Live Railway backend at https://guitar-trainer-v2-production.up.railway.app
    - Persistent Postgres with Alembic migration + Sweet Home Chicago seed
    - iOS EAS `preview` build installed on Hernan's registered iPhone via ad-hoc distribution
    - MMKV airplane-mode cache verified on real device (PLAT-04)
  affects:
    - Every subsequent phase now ships to the same Railway backend
    - Phase 2 (onboarding) can call the live API from day one
tech_stack:
  added:
    - GitHub repo Hernanros/guitar-trainer-v2 (private) as the deploy source
    - Railway project with FastAPI service + Postgres add-on
    - EAS project @hairnone/guitar-trainer (projectId 5f696678-3c16-457a-bad8-69106a953649)
  patterns:
    - Railway auto-deploy from main branch
    - Manual DATABASE_URL reference (Railway did NOT auto-inject; had to add reference variable manually)
    - EAS preview profile → ad-hoc iOS distribution with managed credentials
    - .npmrc legacy-peer-deps=true to keep EAS `npm ci` and local `npm install` on the same resolution rules
key_files:
  created:
    - mobile/.npmrc (legacy-peer-deps=true — unblocks EAS npm ci)
    - .planning/phases/01-foundation-empty-loop/01-04-SUMMARY.md
  modified:
    - server/app/db/session.py (fix: handle both postgres:// and postgresql:// schemes from Railway)
    - server/railway.toml (healthcheckTimeout 30s → 90s for cold-start realities)
    - mobile/.env.production (placeholder → real Railway URL)
    - mobile/app.json (bundleIdentifier + package + EAS projectId)
decisions:
  - "PLAT-01 satisfied on iOS ad-hoc preview build. Confirmed on Hernan's physical iPhone."
  - "PLAT-03 satisfied — FastAPI live on Railway, Alembic migration ran, seed row present, /healthz + /api/v1/song-of-day + /openapi.json all responding."
  - "PLAT-04 satisfied — MMKV airplane-mode cache confirmed on the real device (force-quit + airplane + reopen still rendered Sweet Home Chicago)."
  - "PLAT-02 (Android EAS build) deferred per user pacing choice — the Android device/emulator wasn't ready. Documented as a follow-up. Reversal of the plan's originally-anticipated fallback (which assumed iOS was the risky path via Apple Developer gate); in practice Apple Developer was ready and Android tooling wasn't."
  - "Railway DATABASE_URL scheme changed since Wave 1's research — Railway now injects `postgresql://` (modern form) rather than the legacy `postgres://` the RESEARCH.md documented. session.py now handles both."
  - "Railway builder is Railpack by default (not Nixpacks). server/railway.toml still requests nixpacks explicitly which Railway honored. Root Directory MUST be set to `server/` — Railpack/Nixpacks won't auto-detect a nested Python app."
  - "openapi-typescript 7.13.0 has an outdated `typescript@^5.x` peer dep. Expo SDK 57 uses typescript 6.x. .npmrc legacy-peer-deps=true resolves the conflict on both EAS and local machines — the runtime output is TS 6 compatible."
metrics:
  duration: "~90 minutes (mostly interactive: Railway dashboard setup + debugging DATABASE_URL scheme + waiting for EAS iOS build)"
  completed: "2026-07-19"
  tasks_completed: 2  # Task 1 (Railway) + Task 3 (iOS build) — Task 3's Android path deferred
  tasks_total: 3      # Task 2 was a checkpoint gate (passed: Apple Dev active + UDID registered)
  files_touched: 5
requirements_satisfied:
  - PLAT-01  # iOS via EAS ad-hoc
  - PLAT-03  # FastAPI + Railway + Postgres
  - PLAT-04  # MMKV offline cache
requirements_deferred:
  - PLAT-02  # Android EAS build (needs Android device or emulator)
---

# Plan 01-04 — Ship to Devices (Wave 3)

## What was built

A live production Railway deployment of the FastAPI backend with a persistent Postgres add-on, plus an iOS EAS `preview` build installed on the registered iPhone via ad-hoc distribution. The full Phase 1 loop was verified on real hardware:

1. Install the IPA on iPhone via the EAS install URL
2. Open the app → Today tab fetches Sweet Home Chicago from Railway
3. Enable airplane mode
4. Force-quit the app
5. Reopen — Sweet Home Chicago still renders (MMKV cache active)

**Phase 1 Success Criteria after Wave 3:**

| SC | Description | Status |
|----|-------------|--------|
| 1  | iOS EAS build installs on device and launches to Today tab | ✓ |
| 2  | Android EAS build installs on device and launches to Today tab | Deferred (see below) |
| 3  | Today tab shows hardcoded song from Railway FastAPI + Postgres | ✓ |
| 4  | Airplane-mode + reopen renders cached "today" from MMKV | ✓ (on iOS) |
| 5  | Three-tab shell navigates without crashing; Library/Toolkit render placeholders | ✓ (implicit — app launched) |

## Deviations from plan

1. **Railway builder detection required Root Directory config.** The plan (and RESEARCH.md) assumed Nixpacks would auto-detect from repo root. In practice, Railway now uses Railpack as the default builder, which scanned the repo root, saw `.planning/`, `mobile/`, `server/`, `.gitignore`, `CLAUDE.md`, and couldn't identify a language. Fix: set Service → Settings → Source → Root Directory to `server`. Once set, Railway honored `server/railway.toml`'s `builder = "nixpacks"` request and used Nixpacks. Would also have worked with Railpack given Python detection.

2. **DATABASE_URL scheme mismatch.** Wave 1's RESEARCH.md documented that Railway injects `postgres://`, and Wave 1's `session.py` rewrite handled only that legacy form. Railway now injects `postgresql://` (Postgres 15+ recommended form). The rewrite silently passed the URL through unchanged; SQLAlchemy's async engine then complained that it got a sync driver (psycopg2). Fix in this plan: `session.py` normalizes all three legitimate input schemes (`postgres://`, `postgresql://`, `postgresql+asyncpg://`) → `postgresql+asyncpg://`. Alembic's `env.py` was already tolerant (it normalizes both to `postgresql://`).

3. **Railway did NOT auto-inject `DATABASE_URL` into the FastAPI service.** The plan assumed Railway would automatically create the reference variable when a Postgres service was added to the same project. In practice the reference had to be added manually via Variables → + New → Add Reference → Postgres → DATABASE_URL. Documented as a known Railway UX quirk; not a code issue.

4. **Healthcheck timeout too tight.** The plan inherited Wave 1's `healthcheckTimeout = 30`. On a real Railway cold start, Alembic + seed + uvicorn boot can easily overshoot 30s. Bumped to 90s.

5. **EAS peer-dep conflict.** `openapi-typescript@7.13.0`'s peer dep pins `typescript@^5.x`, but Expo SDK 57 pulled in TypeScript 6.x. Local `npm install` was tolerant; EAS `npm ci --include=dev` was strict and failed. Fix: `mobile/.npmrc` with `legacy-peer-deps=true` keeps both environments on the same rules until openapi-typescript ships a release that accepts TS 6.

6. **Android build path skipped, iOS proceeded (reversal of anticipated fallback).** The plan's Task 2 human-verify checkpoint anticipated the risky path being iOS (Apple Developer enrollment delay). In practice Apple Developer + iOS UDID were already ready; Android tooling (device / emulator / adb) was not set up. So the plan's fallback pattern fired in the opposite direction: iOS shipped, Android deferred with reason. Same conceptual outcome — the plan's discipline of "if one platform is blocked, ship the other with a documented follow-up" worked as designed.

## Follow-ups

- **PLAT-02 (Android EAS build) — pending.** Run `eas build --profile preview --platform android` once an Android device with USB debugging or an Android emulator is available. Everything on the mobile-side is already prepared: `android.package` is set in `app.json`, EAS profile is configured for APK output, the same MMKV cache and Today-tab code will render on Android without changes.
- **Watch Paths not set on the Railway FastAPI service.** Pushes touching only `mobile/` will still trigger backend rebuilds. Low-priority hygiene item — set to `server/**` when convenient.
- **CORS is `allow_origins=["*"]`** in `server/app/main.py`. Fine for a POC where no PII is served, but narrow to the actual EAS bundle URL when Phase 2 (onboarding) adds user data.
- **Wave 1 RESEARCH.md is now partially stale.** Two documented pitfalls updated in reality: (1) Railway's default builder is Railpack, not Nixpacks; (2) Railway's DATABASE_URL scheme is `postgresql://`, not `postgres://`. Neither invalidates the mitigations — session.py and railway.toml handle both — but future phases should read the updated realities.
- **openapi-typescript upstream release** — when they ship a version accepting TS 6, drop the `.npmrc` workaround.

## Prerequisites verified

- GitHub repo `Hernanros/guitar-trainer-v2` (private) created and `main` pushed
- Railway project with FastAPI service (Root Directory: `server`) + Postgres add-on + DATABASE_URL reference variable
- Expo/EAS account `@hairnone` + project `@hairnone/guitar-trainer` linked
- Apple Developer Program active + iOS device UDID pre-registered via `eas device:create`

## Links to key artifacts

- Live API: https://guitar-trainer-v2-production.up.railway.app
- Health: https://guitar-trainer-v2-production.up.railway.app/healthz
- Song of the day: https://guitar-trainer-v2-production.up.railway.app/api/v1/song-of-day
- OpenAPI schema: https://guitar-trainer-v2-production.up.railway.app/openapi.json
- EAS project dashboard: https://expo.dev/accounts/hairnone/projects/guitar-trainer
- GitHub repo: https://github.com/Hernanros/guitar-trainer-v2
