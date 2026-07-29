---
phase: 03-ai-teacher-song-of-the-day
plan: "03"
subsystem: session-rating
tags: [phase-3, rating, mastery, session-write, mvp-slice-c, tanstack-query, fastapi, asyncpg]

requires:
  - phase: 03-01
    provides: Migration 0003 (user_sessions table + partial-unique indexes), UserSession ORM, TodaySongResponse shape, X-Timezone-Offset dep
  - phase: 03-02
    provides: breakdown screen skeleton (breakdown/[songId].tsx base, useTodaySong hook stub)
  - phase: 02-04
    provides: SkillNode ORM with mastery column, song_skills junction table, apiFetch wrapper, QueryClient

provides:
  - "POST /api/v1/sessions — atomic INSERT user_sessions + UPDATE mastery on all attached skill_nodes in single AsyncSession.begin() transaction"
  - "LEAST/GREATEST SQL-side clamp [0,1] with typed numeric literals (asyncpg-safe, no :: cast syntax)"
  - "Two-layer idempotency: app-level SELECT COUNT guard + DB partial-unique index (uq_user_sessions_daily_rating) fallback"
  - "TodaySongResponse.rated field — GET /api/v1/song-of-day now surfaces rated field when user has rated today"
  - "useSubmitRating mutation with Revision A setQueryData cache patch (no extra network round-trip)"
  - "RatingPills component with locked UI-SPEC §5 copy, 48px tap targets, active-state orange left border"
  - "AlreadyRatedCard component with per-rating §6 Fletcher copy, 2s auto-navigate, clearTimeout cleanup"
  - "SongOfDayCard already-rated variant (ratedLabel prop replaces CTA, hides re-roll ghost)"
  - "breakdown/[songId].tsx — RatingPills below content, AlreadyRatedCard overlay on success, router.replace to Today"
  - "(tabs)/index.tsx — reads today.rated, switches SongOfDayCard to already-rated variant"

affects: [Phase 4 selector reads updated mastery, Phase 4 skill-graph display, future re-roll feature]

tech-stack:
  added:
    - "jest-expo@57 — test framework for React Native/Expo"
    - "@testing-library/react-native@14 — component test utilities"
    - "test-renderer@1.2 — modern React Native test renderer (replaces react-test-renderer)"
    - "@react-native/jest-preset@0.86 — jest preset peer dep for jest-expo"
  patterns:
    - "Atomic SQLAlchemy transaction: ALL db operations (reads + writes) inside async with db.begin() called BEFORE any autobegin trigger fires"
    - "asyncpg cast pattern: func.least/greatest/cast(literal(Decimal), Numeric(4,3)) instead of :: syntax"
    - "Revision A cache patch: setQueryData patches today-song cache on rating success (no invalidate = no extra network round-trip)"
    - "ORM enum coercion via field_validator: SongResponse.coerce_category_enum mirrors SkillNodeResponse.coerce_level_enum pattern"
    - "Per-day cache key: ['today-song', userId, localCalendarDay()] — natural daily refresh without explicit staleTime"
    - "Two-layer idempotency: app-level COUNT guard (legibility) + DB partial-unique WHERE constraint (race concurrency)"

key-files:
  created:
    - "server/app/api/v1/sessions.py — POST /api/v1/sessions endpoint (atomic write, idempotency, mastery update)"
    - "server/app/api/v1/breakdowns.py — GET /api/v1/songs/{song_id}/breakdown stub (Slice B placeholder)"
    - "server/app/models/session.py — SessionCreate + SessionResponse Pydantic models"
    - "server/tests/test_sessions.py — 13 integration tests (all passing)"
    - "mobile/src/api/sessions.ts — useSubmitRating mutation with Revision A setQueryData patch"
    - "mobile/src/api/todaySong.ts — useTodaySong hook with localCalendarDay() helper"
    - "mobile/src/components/RatingPills.tsx — 3-tier horizontal rating row (locked §5 copy)"
    - "mobile/src/components/AlreadyRatedCard.tsx — post-rating acknowledgement card (locked §6 copy)"
    - "mobile/src/components/SongOfDayCard.tsx — song card with ratedLabel already-rated variant"
    - "mobile/src/app/breakdown/[songId].tsx — breakdown screen with rating flow wire-up"
    - "mobile/src/api/sessions.test.ts — 6 mutation tests (all passing)"
    - "mobile/src/components/RatingPills.test.tsx — 8 component tests (all passing)"
    - "mobile/src/components/AlreadyRatedCard.test.tsx — 8 component tests (all passing)"
  modified:
    - "server/app/api/v1/song_of_day.py — extended to return TodaySongResponse with rated field"
    - "server/app/models/song.py — added TodayRatingInfo + TodaySongResponse, coerce_category_enum validator"
    - "server/app/api/deps.py — added get_tz_offset_minutes dependency (range-validated ±840)"
    - "server/app/models/db.py — added UserSession ORM, RatingLevel enum, SongCatalog ORM, Song.breakdown_generated_at"
    - "server/app/main.py — registered breakdowns_router + sessions_router"
    - "mobile/src/api/generated/schema.d.ts — added SessionCreate, SessionResponse, TodayRatingInfo, TodaySongResponse"
    - "mobile/src/app/(tabs)/index.tsx — wired to useTodaySong, passes ratedLabel to SongOfDayCard"
    - "mobile/package.json — added jest-expo test setup + test script"

key-decisions:
  - "Revision A (BLOCKER fix): useSubmitRating.onSuccess uses setQueryData to patch today-song cache instead of invalidateQueries — avoids extra network round-trip while immediately reflecting rated state"
  - "Revision C: Slice C does NOT modify migration 0003 — partial-unique index uq_user_sessions_daily_rating already ships in Slice A's migration; no 0004 follow-up needed"
  - "asyncpg cast incompatibility: abandoned :: numeric cast syntax in UPDATE SET clause; switched to func.least/greatest/cast(literal(Decimal(...)), Numeric(4,3)) — generates ANSI SQL without :: cast"
  - "AutoBegin conflict resolution: moved ALL db operations (reads + writes) inside async with db.begin() block to prevent SQLAlchemy InvalidRequestError from autobegin trigger"
  - "ORM enum coercion: added field_validator to SongResponse.coerce_category_enum to convert SongCategory enum instance to .value string on ORM read-back"
  - "D-07 equal-weight: grep-verified zero references to SongSkill.weight or song_skill weight column in sessions.py"
  - "SKILL-03: grep-verified zero imports from app.ai in sessions.py (comment rewritten to avoid false positive)"
  - "Test framework selection: jest-expo@57 + @testing-library/react-native@14 + test-renderer@1.2 installed (all verified on npmjs.com before install)"

patterns-established:
  - "Async SQLAlchemy begin() pattern: call async with db.begin() BEFORE any db.scalar/execute — prevents autobegin conflict. ALL operations (local_day calc, ownership check, idempotency SELECT, INSERT, UPDATE) live inside one block."
  - "asyncpg-safe numeric expression: use cast(literal(Decimal('...')), Numeric(4,3)) instead of ::numeric cast. Works with asyncpg's extended query protocol."
  - "TanStack Query cache patch pattern: setQueryData<T>(key, old => old ? {...old, newField} : old) — safe no-op when cache is cold"
  - "Per-day cache key with localCalendarDay(): ['today-song', userId, localCalendarDay()] — cache naturally expires on day boundary without explicit staleTime"

requirements-completed: [SOTD-05, SKILL-03]

duration: 90min
completed: 2026-07-29
---

# Phase 03 Plan 03: Rating + Mastery + Already-Rated States (Slice C) Summary

**Atomic session rating write with D-08 mastery shifts clamped [0,1] at the SQL level, two-layer idempotency guard, and full mobile rating flow (RatingPills + AlreadyRatedCard + already-rated SongOfDayCard variant) — daily loop closes.**

## Performance

- **Duration:** ~90 min (resumed from previous context)
- **Started:** 2026-07-29T09:00:00Z (continuation from prior session)
- **Completed:** 2026-07-29T10:29:00Z
- **Tasks:** 2/2 auto tasks complete (Task 3 = checkpoint:human-verify, awaiting human)
- **Files modified:** 25 (13 created, 12 modified)

## Accomplishments

- POST /api/v1/sessions is fully atomic: INSERT user_sessions + UPDATE mastery on all attached skill_nodes in a single AsyncSession.begin() transaction; either both succeed or both roll back.
- LEAST/GREATEST SQL-side clamp passes all 3 edge cases (clamp-to-zero, clamp-to-one, normal +0.05) — verified by 13 pytest tests, all passing.
- Revision A setQueryData cache patch: Today tab renders already-rated variant immediately after rating without a second GET to the server.
- 22 mobile tests passing: RatingPills locked copy and visual behavior (8), AlreadyRatedCard per-rating §6 copy + 2s timer cleanup (8), useSubmitRating cache patch + 409 silent handling (6).
- TypeScript compile clean (npx tsc --noEmit exits 0).

## Q5 Outcome (RESEARCH §9 Q5)

SQLAlchemy's `onupdate=func.now()` on the `SkillNode.updated_at` column does NOT fire automatically on bulk UPDATE via `session.execute(update(...))`. The explicit `updated_at=func.now()` in the `.values()` call was required. This was confirmed by the `test_updated_at_bumped_after_rating` test which seeds skill_nodes with `updated_at` set to 24h ago and verifies it was bumped after the rating write.

## Idempotency Observed

Two-layer guard confirmed working:
- App-level SELECT COUNT before INSERT catches the normal double-tap case (first: 201, second: 409 "Already rated this song today.")
- DB partial-unique index `uq_user_sessions_daily_rating` (WHERE is_reroll_marker = false) catches concurrent races that slip through the SELECT gap.
- Reroll markers and real ratings can coexist in user_sessions because the index only covers `is_reroll_marker = false` rows.

## Migration 0003 Status (Revision C)

Revision C confirmed: Slice C did NOT modify migration 0003. The partial-unique index `uq_user_sessions_daily_rating ON user_sessions (user_id, song_id, local_calendar_day) WHERE is_reroll_marker = false` was verified present in the migration file before Slice C implementation began. No 0004 follow-up migration was created.

## Task Commits

1. **Task 1 RED — server tests** - `94928d5` (test: failing tests for POST /api/v1/sessions)
2. **Task 1 GREEN — server implementation** - `33c2fa2` (feat: POST /api/v1/sessions + song_of_day rated field)
3. **Task 2 — mobile layer** - `9a28741` (feat: useSubmitRating + RatingPills + AlreadyRatedCard + Today wire-up)

## Files Created/Modified

### Server

- `server/app/api/v1/sessions.py` — POST /api/v1/sessions atomic write, SKILL-03, D-07, D-08
- `server/app/api/v1/breakdowns.py` — GET /api/v1/songs/{song_id}/breakdown Slice B stub
- `server/app/api/v1/song_of_day.py` — extended to return TodaySongResponse with rated field
- `server/app/models/session.py` — SessionCreate + SessionResponse Pydantic models
- `server/app/models/song.py` — TodayRatingInfo + TodaySongResponse + coerce_category_enum
- `server/app/models/db.py` — UserSession ORM, RatingLevel enum, Song.breakdown_generated_at
- `server/app/api/deps.py` — get_tz_offset_minutes dependency
- `server/app/main.py` — router registration
- `server/tests/test_sessions.py` — 13 integration tests

### Mobile

- `mobile/src/api/sessions.ts` — useSubmitRating with Revision A setQueryData patch
- `mobile/src/api/todaySong.ts` — useTodaySong + localCalendarDay()
- `mobile/src/api/generated/schema.d.ts` — SessionCreate, SessionResponse, TodayRatingInfo, TodaySongResponse
- `mobile/src/components/RatingPills.tsx` — locked §5 copy + visual
- `mobile/src/components/AlreadyRatedCard.tsx` — locked §6 copy + 2s timer
- `mobile/src/components/SongOfDayCard.tsx` — ratedLabel already-rated variant
- `mobile/src/app/breakdown/[songId].tsx` — rating flow wire-up
- `mobile/src/app/(tabs)/index.tsx` — today.rated → ratedLabel → SongOfDayCard
- `mobile/src/api/sessions.test.ts` — 6 mutation tests
- `mobile/src/components/RatingPills.test.tsx` — 8 component tests
- `mobile/src/components/AlreadyRatedCard.test.tsx` — 8 component tests

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed false-positive SKILL-03 grep due to comment containing "from app.ai"**
- **Found during:** Task 1 GREEN verification
- **Issue:** sessions.py comment read "SKILL-03 compliance: ZERO imports from app.ai in this file" — the substring "from app.ai" matched the grep
- **Fix:** Rewrote comment to "SKILL-03 compliance: no LLM imports in this write path"
- **Files modified:** server/app/api/v1/sessions.py
- **Commit:** 33c2fa2

**2. [Rule 1 - Bug] Fixed asyncpg :: cast syntax incompatibility in mastery UPDATE**
- **Found during:** Task 1 GREEN (test run)
- **Issue:** asyncpg's extended query protocol does not support `::numeric` Postgres cast syntax in parameterized positions — `PostgresSyntaxError: syntax error at or near ":"`
- **Fix:** Replaced raw text SQL with SQLAlchemy ORM expressions: `func.least(cast(literal(Decimal("1.0")), Numeric(4,3)), func.greatest(cast(literal(Decimal("0.0")), Numeric(4,3)), SkillNode.mastery + cast(literal(shift), Numeric(4,3))))`
- **Files modified:** server/app/api/v1/sessions.py
- **Commit:** 33c2fa2

**3. [Rule 1 - Bug] Fixed SQLAlchemy autobegin conflict with db.begin()**
- **Found during:** Task 1 GREEN (test run)
- **Issue:** `InvalidRequestError: A transaction is already begun on this Session` — the plan's code template placed db operations before `async with db.begin()`, triggering autobegin
- **Fix:** Moved ALL db operations (local_day calc, ownership SELECT, idempotency COUNT, INSERT, UPDATE) inside a single `async with db.begin():` block called as the first database operation
- **Files modified:** server/app/api/v1/sessions.py
- **Commit:** 33c2fa2

**4. [Rule 2 - Missing Critical] Added coerce_category_enum validator to SongResponse**
- **Found during:** Task 1 GREEN (test run)
- **Issue:** SQLAlchemy ORM returns SongCategory enum instance; Pydantic Literal["can_play", "working_on", "aspirational"] constraint requires plain string — ValidationError on read-back
- **Fix:** Added field_validator mirroring SkillNodeResponse.coerce_level_enum pattern from Phase 2
- **Files modified:** server/app/models/song.py
- **Commit:** 33c2fa2

**5. [Rule 3 - Blocking] Installed jest-expo test framework (no test runner existed)**
- **Found during:** Task 2 start — package.json had no test script, no jest config
- **Reason:** The plan's verify step uses `npm test` but zero testing infrastructure existed in mobile/
- **Fix:** Installed jest-expo@57, @testing-library/react-native@14, test-renderer@1.2, @react-native/jest-preset@0.86, jest@29 (versions matched jest-expo's internal peer deps); added "test" script to package.json; added jest config block; added "jest" to tsconfig.json types
- **Verification:** All packages verified on npmjs.com before install; 22 tests pass
- **Commit:** 9a28741

**6. [Rule 1 - Bug] Fixed sessions.ts onError for 500 — do not rethrow inside TQ onError**
- **Found during:** Task 2 test run
- **Issue:** Rethrowing inside TanStack Query v5 global `onError` does not propagate to `.mutate()` callback's onError; it causes an unhandled promise rejection instead
- **Fix:** Removed `throw err` from non-409 branch — TQ v5 naturally surfaces the mutation error to the isError state and to the `.mutate()` onError callback
- **Files modified:** mobile/src/api/sessions.ts
- **Commit:** 9a28741

## Known Stubs

- `mobile/src/app/breakdown/[songId].tsx` uses `useTodaySong()` to get breakdown data. The actual breakdown is embedded in `today.song.breakdown`. This works for Slice C but Slice B's `GET /api/v1/songs/{song_id}/breakdown` endpoint (with Sonnet LLM generation) is not yet wired to a separate fetch — the breakdown/[songId].tsx reads from the today-song cache. Slice B's full implementation will add a separate `useBreakdown(songId)` hook and wire the LLM path.

- `server/app/api/v1/breakdowns.py` is a stub — serves existing breakdown JSONB or 503. Full Sonnet integration is Slice B work.

## Threat Flags

No new threat surface beyond what was in the plan's threat model. All T-03-03-01 through T-03-03-08 mitigations implemented and verified.

## Self-Check: PASSED

**Files created — verified:**
- server/app/api/v1/sessions.py: exists
- server/tests/test_sessions.py: exists (13 passing)
- mobile/src/api/sessions.ts: exists
- mobile/src/components/RatingPills.tsx: exists
- mobile/src/components/AlreadyRatedCard.tsx: exists
- mobile/src/app/breakdown/[songId].tsx: exists

**Commits verified:**
- 94928d5: test(03-03) RED phase server tests
- 33c2fa2: feat(03-03) GREEN server implementation
- 9a28741: feat(03-03) mobile Slice C

**Test results:**
- server: 13/13 passing (pytest)
- mobile: 22/22 passing (jest)
- TypeScript: 0 errors (tsc --noEmit)
- Zero emojis: confirmed across all Slice C files
