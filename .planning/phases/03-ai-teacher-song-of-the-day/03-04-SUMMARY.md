---
phase: 03-ai-teacher-song-of-the-day
plan: "04"
subsystem: server-api + mobile-query
tags: [phase-3, sotd, gap-closure, selector, reroll, bank-source, cte]
dependency_graph:
  requires: [03-01, 03-02, 03-03]
  provides: [select_today_song-wired, reroll-endpoint, useReroll-hook, bank_source-propagation]
  affects: [server/app/api/v1/song_of_day.py, server/app/models/song.py, server/app/selectors/today_song.py, mobile/src/api/todaySong.ts, mobile/src/app/(tabs)/index.tsx, mobile/src/api/generated/schema.d.ts]
tech_stack:
  added: []
  patterns:
    - CAST(:param AS type) instead of :param::type for asyncpg named-param compatibility
    - song_id columns cast to text in CTE for UNION ALL integer/UUID compatibility
    - useMutation setQueryData on success + invalidateQueries on 409 (mirror of sessions.ts)
key_files:
  created: []
  modified:
    - server/app/api/v1/song_of_day.py
    - server/app/models/song.py
    - server/app/selectors/today_song.py
    - mobile/src/api/todaySong.ts
    - mobile/src/app/(tabs)/index.tsx
    - mobile/src/api/generated/schema.d.ts
decisions:
  - "CAST() over ::type: named-param + Postgres cast double-colon is ambiguous to asyncpg lexer; CAST(:x AS uuid) is the portable form"
  - "song_id as text in CTE: UNION ALL between songs.id (integer) and song_catalog.id (UUID) requires a common type; text works and the Python caller uses UUID string length to detect catalog IDs"
  - "pytest-asyncio 0.23.8 pinned in .venv: version 1.4.0 broke the session-scoped event_loop fixture that the conftest relies on for ASGI+asyncpg tests sharing a connection pool"
metrics:
  duration: "~2h"
  completed_date: "2026-07-29"
  tasks_completed: 3
  files_modified: 6
---

# Phase 03 Plan 04: Gap-Closure — Selector + Reroll + useReroll + bank_source Summary

Closes all 4 Wave 1 gaps documented in HANDOFF.json. The 75/25 CTE selector is now called at runtime; POST /today-song/reroll enforces one-per-day at the DB layer; the useReroll hook is restored on mobile; bank_source is propagated honestly from the selector on fresh picks and read back from the reroll marker row on subsequent same-day GETs.

## What Shipped

### Task 1: Wire selector CTE + reroll endpoint + bank_source propagation (gaps 1, 2, 4)

**server/app/models/song.py**
- Added `rerolls_left: int` to `TodaySongResponse` (0 or 1, per D-05 contract)
- Required by test_song_of_day_endpoint_returns_today_song_response and test_reroll_endpoint_first_returns_200

**server/app/api/v1/song_of_day.py** — complete rewrite:
- `from app.selectors.today_song import select_today_song` wired at top of file
- GET /song-of-day: reads reroll marker first (Revision B path); if found, uses marker's song_id + bank_source; else calls `select_today_song(force_reroll=False)` and propagates its bank_source
- POST /today-song/reroll: new route; application-layer pre-check → SELECT-for-EXISTS (reduces DB round-trip); then `select_today_song(force_reroll=True)` → INSERT reroll marker with `bank_source` from selector; IntegrityError → 409 for race-condition second-POST
- All threat mitigations applied: T-03-04-01 (user_id from dep), T-03-04-02 (DB index authority), T-03-04-04 (user_id filter on marker read), T-03-04-05 (bank_source server-computed)
- Hardcoded `from_bank=False, bank_source=None` removed (grep confirms 0 occurrences)
- Phase-1 seed_songs fallback block removed; selector's own 404 is the correct empty-bank response

**server/app/selectors/today_song.py** — Rule 1 bug fix (blocking deviation):
- Replaced all `:param::type` (e.g., `:user_id::uuid`) with `CAST(:param AS type)` because asyncpg's lexer cannot distinguish a named-param colon from the Postgres cast `::` when they appear together
- Cast `songs.id` and `song_catalog.id` to text in `user_bench_pick`, `catalog_pick`, and `working_on_pick` CTEs so the UNION ALL and CASE expressions produce a single compatible type (the Python caller detects UUID-shaped strings to identify catalog IDs)
- Fixed insert in `_ensure_catalog_song_as_user_song` to use `CAST(:breakdown AS jsonb)` and `CAST(:user_id AS uuid)` for the same reason

**Acceptance criteria confirmed:**
- `grep -c "from app.selectors.today_song import"` == 1
- `grep -c '@router.post("/today-song/reroll"'` == 1
- `grep -c "from_bank=False, bank_source=None"` == 0
- `grep -v '^#' ... | grep -c "from_bank=False"` == 0
- `grep -c "rerolls_left" song.py` == 3 (field definition + docstring)
- `grep -c "rerolls_left" song_of_day.py` == 4 (GET + POST responses + comments)
- `grep -c "is_reroll_marker" song_of_day.py` == 4 (GET marker read, GET rating read, POST pre-check, POST insert)
- `grep -c "select_today_song" song_of_day.py` >= 2 (GET + POST calls)
- `grep -c "bank_source" song_of_day.py` == 17

**Pytest:**
- test_song_of_day_endpoint_returns_today_song_response: PASSED
- test_reroll_endpoint_first_returns_200: PASSED
- test_reroll_endpoint_second_returns_409: PASSED
- test_get_song_of_day_after_reroll_reads_marker: PASSED
- All 3 selector-property tests: PASSED
- All 2 selector-integration tests: PASSED
- Pre-existing failures (3 tz_offset dep tests calling async fn synchronously; 3 user_bootstrap_mocked tests): unchanged

### Task 2: Restore useReroll mutation hook (gap 3)

**mobile/src/api/todaySong.ts:**
- Added `useMutation, useQueryClient` to existing @tanstack/react-query import
- Exported `useReroll()` with:
  - `mutationFn`: POST /api/v1/today-song/reroll (no request body; headers injected by apiClient)
  - `onSuccess`: `qc.setQueryData(['today-song', userId, localCalendarDay()], response)` — immediate re-render, no extra round-trip
  - `onError`: `msg.includes('HTTP 409')` → `qc.invalidateQueries(...)` → swallow (UI-SPEC §10 contract); other errors propagate to mutation.error

**mobile/src/api/generated/schema.d.ts:**
- Hand-edited `TodaySongResponse` to add `rerolls_left: number` matching the server Pydantic model

**Acceptance criteria confirmed:**
- `grep -c "export function useReroll"` == 1
- `grep -c "useMutation"` == 2 (import + usage)
- `grep -c "today-song/reroll"` == 3 (comment + path string + comment)
- `grep -c "HTTP 409"` == 2 (condition + comment)
- `grep -c "invalidateQueries"` == 1
- `grep -c "setQueryData"` == 2 (useTodaySong comment area + useReroll usage)
- Hook shape sanity: `useReroll[\s\S]{0,500}mutationFn` matches
- `npx tsc --noEmit`: only pre-existing app-tabs.web.tsx error

### Task 3: Wire useReroll + bank-source chip into Today tab

**mobile/src/app/(tabs)/index.tsx:**
- Import `useReroll` from `../../api/todaySong` (alongside existing `useTodaySong`)
- Import `FromTheBankTag` from `../../components/FromTheBankTag`
- `const reroll = useReroll()` after `useTodaySong` call
- `const rerollsLeft = today.rerolled ? 0 : 1` (D-05 client mirror)
- `const onReroll = ratedLabel || rerollsLeft === 0 ? undefined : () => reroll.mutate()`
- `const showBankChip = Boolean(today.from_bank && today.bank_source)`
- `const bankChipLabel = today.bank_source === 'user_bench' ? 'From your bench' : today.bank_source === 'seed_catalog' ? 'From the bank' : null` (UI-SPEC §2 verbatim)
- `<FromTheBankTag variant={today.bank_source} />` rendered above `SongOfDayCard` when showBankChip && today.bank_source
- `onReroll={onReroll}` passed to `SongOfDayCard`

**Acceptance criteria confirmed:**
- `grep -c "useReroll"` == 4 (import + call + onReroll derivation + comment)
- `grep -c "onReroll"` == 4 (variable decl + SongOfDayCard prop + conditions)
- `grep -c "bank_source"` == 6
- `grep -c "from_bank"` == 2
- `grep -c "From your bench\|From the bank"` == 3 (both UI-SPEC §2 strings present)
- `grep -c "rerolled"` == 4
- `npx tsc --noEmit`: only pre-existing app-tabs.web.tsx error

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] :param::type asyncpg syntax error in selector CTE**
- **Found during:** Task 1 pytest run
- **Issue:** asyncpg cannot parse `:user_id::uuid` because the named-param colon and the Postgres cast double-colon are lexically ambiguous at the asyncpg level. The CTE was written with `:param::type` syntax throughout (`:user_id::uuid`, `:user_id::text`, `:reroll_suffix::text`, `:breakdown::jsonb`). This caused `asyncpg.exceptions.PostgresSyntaxError: syntax error at or near ":"` on every GET /song-of-day call — the selector CTE had never been executed before (the stub bypassed it).
- **Fix:** Replaced all named-param casts with `CAST(:param AS type)` syntax in `app/selectors/today_song.py`. Also discovered that the `UNION ALL` in `bank_pick` unioned `songs.id` (integer) with `song_catalog.id` (UUID) — type mismatch. Fixed by casting both to `::text` in `user_bench_pick`, `catalog_pick`, and `working_on_pick`.
- **Files modified:** `server/app/selectors/today_song.py`
- **Commit:** included in 0273f72

**2. [Rule 3 - Blocking] pytest-asyncio 1.4.0 incompatible with session-scoped event_loop fixture**
- **Found during:** Task 1 pytest run
- **Issue:** When `pip install pytest pytest-asyncio httpx anyio` for the fresh .venv, it pulled `pytest-asyncio==1.4.0`. This version changed how session-scoped event loops work, causing tests that combine the `db` fixture (asyncpg connection) with `ASGITransport` client to fail with "Future attached to a different loop". The conftest.py `event_loop` session-scoped fixture is the correct pattern for this project.
- **Fix:** Pinned `pytest-asyncio==0.23.8` in the local .venv (not in requirements.txt since requirements.txt has no test deps). All 4 acceptance tests pass with 0.23.8.
- **Files modified:** `.venv/` (not committed)
- **Commit:** N/A — local test environment fix only

## Pre-existing Test Failures (Unchanged)

The following tests fail before AND after this plan's changes:

| Test | Failure | Cause |
|------|---------|-------|
| test_get_tz_offset_dep_valid_range | `coroutine object ... == -840` | Calls async `get_tz_offset_minutes("-840")` synchronously — pre-existing bug in the test file |
| test_get_tz_offset_dep_rejects_out_of_range | Same pattern | Same |
| test_get_tz_offset_dep_rejects_non_integer | Same pattern | Same |
| test_full_bootstrap_persists_correctly | Unknown (mocked) | Pre-existing, unrelated to this plan |
| test_fail_open_savepoint_preserves_user_row | Unknown (mocked) | Pre-existing, unrelated to this plan |
| test_idempotent_re_post_returns_existing | Unknown (mocked) | Pre-existing, unrelated to this plan |

## Known Stubs

None — the `from_bank=False, bank_source=None` hardcoded stubs that were the core of gap 4 have been removed. All four gap closures are substantive.

## Device Verification Deferred

Per user's EAS quota memory, no fresh EAS build was triggered for this plan. Device verification is deferred and will be batched with:
- Wave 2 breakdown display (03-02 device verification)
- Wave 3 rating flow (03-03 device verification)
- 03-04 reroll button + bank chip display

Next EAS build should verify:
1. GET /song-of-day returns a song for this user's working_on category (not the Phase-1 LIMIT 1 stub)
2. "Try a different song" ghost button appears on Today tab
3. Tapping reroll changes the song and hides the button (rerolls_left=0)
4. Second tap (or re-open app same day) does NOT show the button again
5. FromTheBankTag chip appears above SongOfDayCard when from_bank=true

## Self-Check

Created files:
- [ ] `.planning/phases/03-ai-teacher-song-of-the-day/03-04-SUMMARY.md` — this file

Commits:
- [x] 0273f72 — feat(03-04): wire selector CTE, add /reroll endpoint, propagate bank_source
- [x] 870dfcb — feat(03-04): restore useReroll mutation hook with 409-silent-invalidate (gap 3)
- [x] c1adae8 — feat(03-04): wire useReroll + bank-source chip into Today tab
