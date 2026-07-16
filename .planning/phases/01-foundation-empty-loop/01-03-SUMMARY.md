---
phase: 01-foundation-empty-loop
plan: "03"
subsystem: mobile-persistence
tags: [mmkv, tanstack-query, offline, expo, react-native, typescript, openapi]

dependency_graph:
  requires:
    - phase: 01-foundation-empty-loop
      plan: "01"
      provides: "QueryClient + mmkvPersister scaffolded; PersistQueryClientProvider at root layout; MMKV v4 + TanStack persist packages installed"
  provides:
    - "MMKV write-through persister verified and wired at root layout level (code-level, PLAT-04 prerequisite)"
    - "Committed baseline schema.d.ts so TypeScript compiles without running codegen"
    - "Updated .gitignore rules to track schema.d.ts while ignoring other generated artifacts"
  affects:
    - "01-04 (EAS dev-client build) — runtime airplane-mode verification of MMKV cache belongs here"
    - "All subsequent plans — TypeScript compiles cleanly without server-side codegen step"

tech_stack:
  added: []
  patterns:
    - "Committed baseline OpenAPI schema.d.ts (minimal types mirroring Pydantic models) so tsc is not gated on codegen"
    - "gitignore by file extension (*.js, *.json) rather than directory so .d.ts baseline stays tracked"

key_files:
  created:
    - mobile/src/api/generated/schema.d.ts (baseline OpenAPI types matching server Pydantic models)
  modified:
    - mobile/src/api/queryClient.ts (added offline strategy comment block)
    - .gitignore (directory-level ignore replaced with extension-level ignore for generated/)
    - mobile/.gitignore (same fix for mobile-scoped gitignore)

key-decisions:
  - "Commit baseline schema.d.ts rather than requiring codegen before tsc — fixes cold-checkout TypeScript compilation"
  - "gitignore generated/ by extension (*.js, *.json) rather than directory — allows .d.ts to be tracked"
  - "Runtime MMKV verification (airplane mode cache hit) delegated to Plan 04 per MMKV v4 NitroModules constraint"

requirements-completed:
  - PLAT-04

duration: ~8min
completed: 2026-07-16
---

# Phase 1 Plan 03: MMKV Offline Persistence Summary

**Verification-only plan: Wave 1 scaffold met all must_haves; Plan 03 adds offline strategy comment, a committed baseline OpenAPI schema.d.ts, and gitignore fixes so TypeScript compiles on cold checkout without running codegen.**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-07-16T12:20:00Z
- **Completed:** 2026-07-16T12:28:00Z
- **Tasks:** 1
- **Files modified:** 4 (1 created, 3 modified)

## Accomplishments

- Verified `mobile/src/api/queryClient.ts` fully implements the MMKV write-through persister per RESEARCH.md Pattern 2 — `createMMKV({ id: 'query-cache' })`, `mmkvStorage` adapter, `createAsyncStoragePersister({ throttleTime: 1000 })`, `staleTime: Infinity`, `gcTime: 86400000` — all correct from Wave 1
- Verified `mobile/src/app/_layout.tsx` wraps the Stack in `PersistQueryClientProvider` with `client={queryClient}` and `persistOptions={{ persister: mmkvPersister }}` — correct from Wave 1
- Added the required offline strategy comment block to `queryClient.ts` explaining throttle behavior, rehydration on restart, `staleTime=Infinity` rationale, and Phase 3 evolution
- Fixed cold-checkout TypeScript compilation failure: created committed baseline `mobile/src/api/generated/schema.d.ts` with minimal types matching the FastAPI Pydantic models (Note, Beat, Measure, Tab, ChordPosition, Chord, TechniqueNote, Breakdown, SongResponse)
- Updated root `.gitignore` and `mobile/.gitignore` to ignore generated files by extension (`*.js`, `*.json`) rather than by directory, so the baseline `schema.d.ts` is tracked by git
- `npx tsc --noEmit` exits 0 — zero TypeScript errors

## Task Commits

1. **Task 1: Verify/complete MMKV persister and PersistQueryClientProvider** - `1aeef6f` (feat)

## Files Created/Modified

- `mobile/src/api/generated/schema.d.ts` — committed baseline OpenAPI types (SongResponse + nested models), matches server Pydantic models; overwritten in-place by `npm run codegen:local` when server is running
- `mobile/src/api/queryClient.ts` — added offline strategy comment block per plan requirement
- `.gitignore` — changed `mobile/src/api/generated/` (directory) to `mobile/src/api/generated/*.js` + `*.json` (extensions) so schema.d.ts baseline is trackable
- `mobile/.gitignore` — same fix for mobile-scoped ignore rules

## Decisions Made

- Committed baseline `schema.d.ts` rather than using a `skipLibCheck` workaround — types are meaningful and match the Pydantic models exactly; codegen overwrites in-place, same filename
- Ignored `src/api/generated/` by extension rather than by directory because git cannot un-ignore files inside an ignored directory via `!` negation patterns when the parent directory is the ignore target

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Missing baseline schema.d.ts caused TypeScript compilation to fail on cold checkout**
- **Found during:** Task 1 (TypeScript verification step)
- **Issue:** `mobile/src/api/songOfDay.ts` imports `from './generated/schema'` (openapi-typescript output). The `src/api/generated/` directory was gitignored entirely, so the file didn't exist in a fresh worktree. `tsc --noEmit` exited 2: `error TS2307: Cannot find module './generated/schema'`. Plan 01's TSC check passed only because codegen had been run in that session.
- **Fix:** Created `mobile/src/api/generated/schema.d.ts` with minimal baseline types mirroring all FastAPI Pydantic models. Updated `.gitignore` and `mobile/.gitignore` to ignore generated files by extension instead of by directory.
- **Files modified:** `mobile/src/api/generated/schema.d.ts` (new), `.gitignore`, `mobile/.gitignore`
- **Verification:** `npx tsc --noEmit` exits 0, `git check-ignore mobile/src/api/generated/schema.d.ts` returns non-zero (file is trackable)
- **Committed in:** `1aeef6f`

---

**Total deviations:** 1 auto-fixed (Rule 1 bug)
**Impact on plan:** Necessary for CI correctness. No scope creep — the fix ensures the plan's own success criterion (tsc exits 0) is met on any clean checkout, not just the developer's local machine after running codegen.

## Issues Encountered

- Main repo `.gitignore` path vs. worktree path confusion: the worktree's working files live under `.claude/worktrees/agent-a2856a459f9b3ceeb/` while the main repo checkout lives at the project root. Both `.gitignore` locations needed to be updated.

## Known Stubs

None introduced in this plan. Pre-existing stubs from Plan 01 (Tab/Chord rendering placeholders, Railway URL) are unchanged and tracked in that plan's SUMMARY.

## Threat Flags

None. This plan modifies only `.gitignore` and TypeScript infrastructure files. No new network surface, auth paths, or trust boundary changes.

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| `createMMKV({ id: 'query-cache' })` in queryClient.ts | PASS |
| `createAsyncStoragePersister` with throttleTime=1000 | PASS |
| `staleTime: Infinity` in QueryClient defaultOptions | PASS |
| `export const queryClient` present | PASS |
| `export const mmkvPersister` present | PASS |
| `PersistQueryClientProvider` in _layout.tsx | PASS |
| `mmkvPersister` imported in _layout.tsx | PASS |
| `Stack.Screen name="(tabs)"` with headerShown: false | PASS |
| No expo-modules-core or expo-constants in queryClient.ts | PASS |
| `mobile/src/api/generated/schema.d.ts` exists | PASS |
| `npx tsc --noEmit` exits 0 | PASS |
| Task commit 1aeef6f exists | PASS |

## Next Phase Readiness

- PLAT-04 code-level prerequisite satisfied: PersistQueryClientProvider + MMKV persister wired at root; the first successful `GET /api/v1/song-of-day` response will be written to MMKV; subsequent app opens with no network will serve from cache
- Runtime verification (actual airplane-mode cache hit on device) is Plan 04's responsibility — MMKV v4 uses NitroModules which requires a dev-client build; it cannot run in Expo Go
- TypeScript compilation is clean and does not require the FastAPI server to be running (`npm run codegen:local` is optional, not gating)

---
*Phase: 01-foundation-empty-loop*
*Completed: 2026-07-16*
