---
phase: 02-onboarding-skill-graph
plan: "01"
subsystem: full-stack
tags: [fastapi, alembic, postgres, pydantic, expo, react-native, mmkv, tanstack-query, openapi-codegen, user-identity]
dependency_graph:
  requires:
    - 01-01 (Walking Skeleton — server + mobile scaffold)
    - 01-04 (Ship to devices — Railway, EAS, postgresql:// scheme)
  provides:
    - Alembic 0002 migration (users, songs upgrade, song_skills, skill_nodes)
    - POST /api/v1/users (bootstrap stub, idempotent ON CONFLICT DO UPDATE)
    - GET /api/v1/users/{user_id} (UserResponse with onboarded_at)
    - MMKV user-store instance (user_id + onboarded_at)
    - apiFetch wrapper (X-User-ID header injection on every request)
    - Root layout onboarded-check redirect (/ → /onboarding on first launch)
    - /onboarding dev placeholder screen (Bootstrap user (dev) button)
  affects:
    - 02-02 (wizard UI) — depends on redirect gate + bootstrap endpoint
    - 02-03 (AI skill graph) — depends on users table, skill_nodes schema, apiFetch wrapper
    - 02-04 (settings re-run) — depends on clearOnboardedAt from mmkv.ts
tech_stack:
  added:
    - anthropic==0.117.0 (pinned in requirements.txt — used by 02-03 Sonnet call)
    - expo-crypto ~57.0.1 (Crypto.randomUUID() for stable device UUID generation)
  patterns:
    - Device UUID identity via MMKV user-store (separate from query-cache MMKV instance)
    - X-User-ID header pattern on every API request (D-04 POC scope)
    - apiFetch single-entry-point for all mobile → server requests
    - ON CONFLICT DO UPDATE upsert for idempotent bootstrap endpoint
    - DEFERRABLE INITIALLY DEFERRED self-referential FK on skill_nodes.parent_id
    - Raw SQL in Alembic migration for enum type creation (avoids SQLAlchemy re-emit bug)
key_files:
  created:
    - server/alembic/versions/0002_users_songs_categorization_skill_graph.py
    - server/app/models/user.py (UserPreferences, UserBootstrapRequest, UserResponse, SkillGraphResponse)
    - server/app/models/skill_node.py (SkillNodeResponse)
    - server/app/api/deps.py (get_user_id — X-User-ID header dependency)
    - server/app/api/v1/users.py (POST /api/v1/users, GET /api/v1/users/{user_id})
    - mobile/src/api/mmkv.ts (userMmkv, getOrCreateUserId, getOnboardedAt, setOnboardedAt, clearOnboardedAt)
    - mobile/src/api/apiClient.ts (apiFetch<T> with X-User-ID injection)
    - mobile/src/api/users.ts (useUser, useUserBootstrap)
    - mobile/src/app/onboarding/_layout.tsx
    - mobile/src/app/onboarding/index.tsx (dev placeholder)
  modified:
    - server/app/models/db.py (User class, SongCategory/SkillLevel enums, Song extended)
    - server/app/models/song.py (SongResponse + optional category + user_id)
    - server/app/main.py (users router, CORS allow_methods + POST)
    - server/app/db/seed.py (seed with system user + category=can_play)
    - server/alembic/env.py (import User for target_metadata)
    - mobile/src/api/songOfDay.ts (refactored to use apiFetch)
    - mobile/src/app/_layout.tsx (Redirect gate + onboarding Stack.Screen)
    - mobile/package.json (added expo-crypto ~57.0.1)
    - mobile/src/api/generated/schema.d.ts (regenerated with Phase 2 schemas)
    - server/requirements.txt (added anthropic==0.117.0)
decisions:
  - "D-04 implemented: device UUID stored in MMKV user-store instance, sent as X-User-ID on every apiFetch call"
  - "D-14 approach A chosen: song-of-day endpoint left at SELECT LIMIT 1 for Phase 2 (Phase 3 refactors to per-user selector)"
  - "Raw SQL used for enum CREATE TYPE in Alembic 0002 to avoid SQLAlchemy re-emitting CREATE TYPE inside op.create_table/op.add_column (runtime bug, create_type=False insufficient)"
  - "seed.py updated to assign system user + category=can_play on Song insert (correctness fix — migration backfill only applies on fresh DB, not after downgrade+upgrade cycle)"
  - "Revision E compliance: skill_nodes.parent_id FK is DEFERRABLE INITIALLY DEFERRED verified in pg_constraint (condeferrable=t, condeferred=t)"
  - "Anthropic SDK pinned at 0.117.0 (current stable as of 2026-07-20 verified via pip index versions)"
metrics:
  duration: "~90 minutes"
  completed: "2026-07-20"
  tasks_completed: 3
  tasks_total: 3
  files_created: 10
  files_modified: 9
---

# Phase 2 Plan 01: User Identity Foundation Summary

**One-liner:** Alembic 0002 migration adds users + skill_nodes + song_skills schema with DEFERRABLE INITIALLY DEFERRED self-ref FK; POST /api/v1/users bootstrap stub returns SkillGraphResponse(nodes=[]); mobile apiFetch wrapper injects X-User-ID on every request; root layout redirects first-launch users to /onboarding via MMKV onboarded_at gate.

## What Was Built

End-to-end user identity foundation enabling the "first launch → onboarding, tapping Complete bootstraps a real users row, second launch bypasses onboarding" flow:

1. **Alembic 0002 migration** (`server/alembic/versions/0002_...py`): creates the `users` table (UUID PK, preferences JSONB, onboarded_at timestamptz), upgrades `songs` with nullable `user_id` FK + `category` enum column, seeds the system user `00000000-0000-0000-0000-000000000000`, backfills Sweet Home Chicago with `user_id=system` and `category=can_play`, creates `skill_nodes` (3-level tree, DEFERRABLE INITIALLY DEFERRED self-ref FK on `parent_id`), and creates `song_skills` junction (composite PK song_id + skill_node_id, weight Numeric).

2. **ORM + Pydantic models**: `User` class in db.py, `SongCategory` + `SkillLevel` enums; new `server/app/models/user.py` (UserPreferences, UserBootstrapRequest, UserResponse, SkillGraphResponse) and `server/app/models/skill_node.py` (SkillNodeResponse with mastery=0.0 default, enforcing D-11 deterministic-writes principle).

3. **Server endpoints**: `POST /api/v1/users` stub (ON CONFLICT DO UPDATE idempotent upsert, returns nodes=[]) and `GET /api/v1/users/{user_id}` (404 if not bootstrapped). `server/app/api/deps.py` exposes `get_user_id` as a reusable FastAPI dependency for X-User-ID header reading. CORS updated to allow POST.

4. **Mobile identity plumbing**: `mobile/src/api/mmkv.ts` — separate MMKV `user-store` instance (distinct from query-cache), `getOrCreateUserId()` via `expo-crypto` `Crypto.randomUUID()`, `getOnboardedAt/setOnboardedAt/clearOnboardedAt`; `mobile/src/api/apiClient.ts` — `apiFetch<T>` wrapper injecting `X-User-ID` on every request; `mobile/src/api/users.ts` — `useUser` + `useUserBootstrap` hooks.

5. **Root layout redirect**: `mobile/src/app/_layout.tsx` reads `getOnboardedAt()` and renders `<Redirect href="/onboarding" />` when null. Both `(tabs)` and `onboarding` Stack.Screens registered.

6. **Onboarding placeholder**: `mobile/src/app/onboarding/index.tsx` — dev scaffold with "Bootstrap user (dev)" button that calls `useUserBootstrap.mutate(...)` and navigates to `/(tabs)` on success. Replaced in 02-02.

## Verification Evidence

```
# Migration round-trip
alembic downgrade base && alembic upgrade head → exit 0
alembic downgrade -1 → exit 0, no orphaned enum types (SELECT typname FROM pg_type → 0 rows)
alembic upgrade head → exit 0

# Database after upgrade
SELECT id FROM users WHERE id='00000000-0000-0000-0000-000000000000' → 1 row
SELECT category FROM songs WHERE title='Sweet Home Chicago' → can_play
\d skill_nodes → fk_skill_nodes_parent: DEFERRABLE INITIALLY DEFERRED
SELECT condeferrable, condeferred FROM pg_constraint WHERE conname='fk_skill_nodes_parent' → t | t

# Server import check
python -c "from app.models.db import User, Song, SongCategory, SkillLevel; from app.models.user import UserBootstrapRequest, UserResponse, SkillGraphResponse, UserPreferences; from app.models.skill_node import SkillNodeResponse; print('imports ok')"
→ imports ok

# Routes registered
python -c "from app.main import app; print([r.path for r in app.routes])"
→ includes /api/v1/users and /api/v1/users/{user_id}

# API endpoints tested
POST /api/v1/users → 201 {"nodes":[]}
POST /api/v1/users (same UUID again) → 201 {"nodes":[]} (idempotent)
GET /api/v1/users/{uuid} → 200 {"id":"...","preferences":{"session_length_min":30,"retention_format":"streak"},"onboarded_at":"2026-07-20T..."}
GET /api/v1/users/{unknown} → 404
GET /api/v1/song-of-day → 200 {"title":"Sweet Home Chicago",...} (no regression)

# OpenAPI schemas
curl /openapi.json | python3 -c "...assert 'UserBootstrapRequest' in schemas; assert 'SkillGraphResponse' in schemas"
→ UserBootstrapRequest OK, SkillGraphResponse OK

# TypeScript check
cd mobile && npx tsc --noEmit
→ (no output — zero errors)

# Schema content checks
grep 'UserBootstrapRequest' mobile/src/api/generated/schema.d.ts → OK
grep 'X-User-ID' mobile/src/api/apiClient.ts → OK
grep 'apiFetch' mobile/src/api/songOfDay.ts → OK
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Python 3.9 union syntax `dict | None` not supported**
- **Found during:** Task 1 — Python import check
- **Issue:** `server/app/models/db.py` used `dict | None` and `uuid.UUID | None` union syntax which is Python 3.10+ only. Local dev runs Python 3.9.6.
- **Fix:** Changed to `Optional[dict]` and `Optional[uuid.UUID]` from `typing.Optional`.
- **Files modified:** `server/app/models/db.py`
- **Commit:** e6d43e7

**2. [Rule 1 - Bug] SQLAlchemy emits `CREATE TYPE` even with `create_type=False` in `op.create_table`/`op.add_column`**
- **Found during:** Task 1 — `alembic upgrade head` on first attempt
- **Issue:** Using `sa.Enum(..., name="song_category", create_type=False)` in `op.add_column` still triggered `_on_table_create` SQLAlchemy event that emitted `CREATE TYPE song_category` even after it was already created via `sa.Enum(...).create(op.get_bind(), checkfirst=True)`. Same issue with `skill_level` inside `op.create_table`. Result: `psycopg2.errors.DuplicateObject: type "skill_level" already exists`.
- **Fix:** Replaced all enum type creation with raw SQL (`op.execute("CREATE TYPE skill_level AS ENUM (...)")`) and created the `skill_nodes` table entirely via raw SQL to keep the enum column out of the SQLAlchemy `create_table` pipeline. Similarly used `ALTER TABLE songs ADD COLUMN ...` raw SQL for songs columns. Drop uses `DROP TYPE IF EXISTS ...` raw SQL.
- **Files modified:** `server/alembic/versions/0002_users_songs_categorization_skill_graph.py`
- **Commit:** e6d43e7

**3. [Rule 2 - Missing] seed.py didn't set `user_id` and `category` on the seed Song row**
- **Found during:** Task 2 server endpoint testing
- **Issue:** The migration backfill (`UPDATE songs SET user_id = '00000000-...', category = 'can_play' WHERE user_id IS NULL`) only runs during migration execution. After a downgrade-to-base + upgrade-to-head cycle (used in testing), the songs table starts empty and the seed function re-inserts Sweet Home Chicago without setting the Phase 2 fields. This would leave the song without `user_id` and `category` after any round-trip.
- **Fix:** Updated `server/app/db/seed.py` to (a) ensure the system user exists via `INSERT ... ON CONFLICT DO NOTHING` before inserting the song, (b) set `user_id=SYSTEM_USER_ID` and `category="can_play"` on the `SongORM` insert.
- **Files modified:** `server/app/db/seed.py`
- **Commit:** 1bd7965

**4. [Rule 3 - Blocking] Docker daemon stopped mid-execution**
- **Found during:** Task 3 — server start attempt on port 8000
- **Issue:** Docker daemon had stopped, causing Postgres connection to fail with `[Errno 61] Connect call failed`. All server operations were blocked.
- **Fix:** Restarted Docker daemon (`open -a Docker`), then `docker start gt-postgres`.
- **Impact:** No code changes required — purely operational.

## Output Details

### Anthropic SDK version pinned
`anthropic==0.117.0` in `server/requirements.txt` (verified via `pip index versions anthropic` on 2026-07-20 against the working venv).

### Expo Router v57 API deviations
None. `<Redirect>` is exported from `expo-router` via `link/Redirect.js → link/Link.js → main index`. Import `import { Redirect } from 'expo-router'` works. Props: `href: Href` (string-compatible). The plan's code was accurate — no adaptations needed.

### expo-crypto v57 API
`Crypto.randomUUID()` is a named export from `expo-crypto` (verified in `expo-crypto/build/Crypto.d.ts`). Import via `import * as Crypto from 'expo-crypto'` then `Crypto.randomUUID()`. No deviations from plan.

### Codegen run confirmation
Task 2's server was running on `localhost:8000` when `npm run codegen:local` ran in Task 3. Generated `mobile/src/api/generated/schema.d.ts` includes `UserBootstrapRequest`, `UserResponse`, `SkillGraphResponse`, `UserPreferences` (verified via grep).

### Railway Postgres snapshot
No production deploy happened during this plan execution (Plan 02-01 is a local dev slice). The migration will be deployed to Railway when Phase 2 ships to production (Plan 02-04 or a dedicated deploy plan). The developer should take a Railway Postgres snapshot before running `alembic upgrade head` in production per T-02-03 mitigation.

### skill_nodes.parent_id DEFERRABLE INITIALLY DEFERRED confirmation
Verified in two ways:
1. Migration file grep: `grep "deferrable|DEFERRED"` → both `deferrable=True` in comment + `DEFERRABLE INITIALLY DEFERRED` in raw SQL DDL present.
2. Database confirmation: `SELECT condeferrable, condeferred FROM pg_constraint WHERE conname='fk_skill_nodes_parent'` → `t | t`.

## Known Stubs

| Stub | File | Reason |
|------|------|--------|
| `onboarding/index.tsx` "Bootstrap user (dev)" placeholder | `mobile/src/app/onboarding/index.tsx` | Intentional dev scaffold per plan <voice_contract>. Replaced by full 5-section wizard in 02-02. |
| `SkillGraphResponse(nodes=[])` always empty | `server/app/api/v1/users.py` | Sonnet call + skill_nodes/song_skills writes deferred to 02-03. The stub is functional (creates user row, returns valid empty response). |

## Threat Flags

No new security-relevant surfaces beyond the plan's `<threat_model>`. All STRIDE entries in the threat register were addressed:
- T-02-01 (X-User-ID spoofing): accepted per D-04 POC scope — FastAPI UUID type coercion rejects malformed UUIDs with 422.
- T-02-02 (migration tampering): mitigated — nullable columns, idempotent backfill, verified round-trip.
- T-02-03 (destructive rollback): mitigated — downgrade tested, enum types cleaned via `DROP TYPE IF EXISTS`.
- T-02-05 (DoS on bootstrap): mitigated — ON CONFLICT DO UPDATE makes repeated calls cheap.

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| All 17 key files exist | PASSED |
| Task 1 commit e6d43e7 exists | PASSED |
| Task 2 commit 1bd7965 exists | PASSED |
| Task 3 commit 17eff1c exists | PASSED |
| alembic upgrade head exits 0 | PASSED |
| alembic downgrade -1 exits 0, no orphaned enums | PASSED |
| system user row in DB after upgrade | PASSED |
| Sweet Home Chicago category=can_play after seed | PASSED |
| skill_nodes.parent_id FK DEFERRABLE INITIALLY DEFERRED | PASSED |
| POST /api/v1/users returns 201 + {"nodes":[]} | PASSED |
| GET /api/v1/users/{user_id} returns 200 + onboarded_at | PASSED |
| GET /api/v1/users/{unknown} returns 404 | PASSED |
| GET /api/v1/song-of-day returns Sweet Home Chicago (no regression) | PASSED |
| npx tsc --noEmit exits 0 | PASSED |
| X-User-ID in apiClient.ts | PASSED |
| getOrCreateUserId in mmkv.ts | PASSED |
| Redirect + onboarding Screen in _layout.tsx | PASSED |
| apiFetch in songOfDay.ts (no direct fetch() calls) | PASSED |
| UserBootstrapRequest in generated schema.d.ts | PASSED |
