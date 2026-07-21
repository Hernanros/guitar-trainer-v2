---
phase: 02-onboarding-skill-graph
plan: "04"
subsystem: mobile
tags: [expo-router, react-native, zustand, tanstack-query, mmkv, fletcher-voice, settings, loader-rotation, fail-open]
dependency_graph:
  requires:
    - 02-01 (MMKV user-store, apiFetch, useUser, clearOnboardedAt)
    - 02-02 (clearWizardState, FletcherIntroCard, wizard route group, preferences.tsx)
    - 02-03 (POST /re-run endpoint, GET /skill-graph endpoint, SkillGraphResponse.mode)
  provides:
    - Settings screen as 4th tab (today/library/toolkit/settings)
    - useSkillGraph() hook — GET /api/v1/users/{userId}/skill-graph
    - useUserReonboard() hook — POST /api/v1/users/{userId}/re-run
    - setReRunPending/getReRunPending MMKV helpers for re-run path routing
    - Fletcher loader rotation (3-phase copy at 0s/3s/8s) via Zustand loaderMessageIndex
    - Fail-open card (mode='bootstrap' D-07 copy) with 2s auto-navigate
    - Re-run branch in preferences.tsx: useUserReonboard if getReRunPending(), else useUserBootstrap
    - uiStore.ts loaderMessageIndex slice (Phase 2 Zustand first real usage)
  affects:
    - Phase 3 (song-of-day selector) — useSkillGraph hook ready for Today tab consumption
    - Phase 4 (cost governor) — re-run flow is a Sonnet call; governor will wrap it
tech_stack:
  added: []
  patterns:
    - MMKV flag (RE_RUN_PENDING_KEY) for routing re-run vs first-time bootstrap in wizard
    - Zustand slice for timer-driven state (loaderMessageIndex set by setTimeout side effects)
    - useRef (not useState) for once-on-mount value that must not trigger re-render on change
    - useEffect cleanup pattern for timeout teardown (T-02-04-03 timer-race mitigation)
    - Alert.alert for OS-native confirm dialog (no custom modal chrome — D-14 aesthetics)
key_files:
  created:
    - mobile/src/app/(tabs)/settings.tsx
  modified:
    - mobile/src/api/users.ts (added useSkillGraph + useUserReonboard + SkillNodeResponse type)
    - mobile/src/api/mmkv.ts (added setReRunPending + getReRunPending helpers)
    - mobile/src/app/(tabs)/_layout.tsx (registered 4th Tabs.Screen: name="settings")
    - mobile/src/store/uiStore.ts (replaced empty placeholder with loaderMessageIndex slice)
    - mobile/src/app/onboarding/preferences.tsx (loader rotation + fail-open card + re-run branch)
decisions:
  - "Option A for re-run path: MMKV flag (RE_RUN_PENDING_KEY) distinguishes re-run from first-time bootstrap in preferences.tsx — simpler than passing route params through wizard"
  - "useRef (not useState) for isReRun in preferences.tsx: value must be read once on mount and not change; useRef.current is stable across renders without triggering re-renders"
  - "Alert.alert for confirm dialog in settings.tsx: native OS modal, no custom chrome, consistent iOS/Android behavior"
  - "Both useUserBootstrap and useUserReonboard initialized in preferences.tsx, active computed from isReRun: React rules-of-hooks requires unconditional hook calls"
  - "loaderMessageIndex reset in useEffect cleanup when active.isPending flips false — prevents stale index on retry"
metrics:
  duration: "~45 minutes"
  completed: "2026-07-21"
  tasks_completed: 2
  tasks_total: 3
  files_created: 1
  files_modified: 5
---

# Phase 2 Plan 04: Settings + Fletcher Loader + Fail-Open Card Summary

**One-liner:** Settings screen as 4th tab reads live preferences via useUser and fires a Fletcher-voiced Alert.alert re-run flow; preferences.tsx loader rotates through three copy variants via Zustand loaderMessageIndex, shows a 2s fail-open card on mode='bootstrap', and branches to useUserReonboard on the re-run path via MMKV flag.

## What Was Built

### Task 1: Settings Tab + API Hooks + Re-run Wiring

**`mobile/src/app/(tabs)/settings.tsx`** — New 4th tab screen. Reads `useUser()` for preferences display (session_length_min, retention_format with human labels). "Re-run onboarding" button triggers `Alert.alert` with OS-native confirm dialog (title: "Start over?", body: "You'll keep your session preferences. Songs and skills reset.", buttons: "Keep going" / "Reset"). On confirm: `setReRunPending(true)` + `clearOnboardedAt()` + `clearWizardState()` + `router.replace('/onboarding')`.

**`mobile/src/api/users.ts`** — Added:
- `useSkillGraph()`: TanStack `useQuery` with key `['skill-graph', userId]`, calls `GET /api/v1/users/{userId}/skill-graph`, 1-hour stale time.
- `useUserReonboard()`: TanStack `useMutation` calling `POST /api/v1/users/{userId}/re-run`. `onSuccess`: `setOnboardedAt`, `clearWizardState`, `qc.setQueryData(['skill-graph', userId])`, `qc.invalidateQueries(['user', userId])`.
- `SkillNodeResponse` type export.

**`mobile/src/api/mmkv.ts`** — Added `setReRunPending(pending: boolean)` and `getReRunPending(): boolean` using `RE_RUN_PENDING_KEY = 'wizard.re_run_pending'`. Set by Settings before routing; cleared by preferences.tsx after successful Complete on the re-run path.

**`mobile/src/app/(tabs)/_layout.tsx`** — Added 4th `Tabs.Screen name="settings"` with title "Settings" and Ionicons `settings` icon. All 3 existing tabs preserved.

### Task 2: Fletcher Loader Rotation + Fail-Open Card + Re-run Branch

**`mobile/src/store/uiStore.ts`** — Replaced empty Phase 1 placeholder with `loaderMessageIndex: number`, `setLoaderMessageIndex(n: number): void`, `resetLoader(): void` Zustand slice.

**`mobile/src/app/onboarding/preferences.tsx`** — Full replacement with:

1. **Re-run branch**: `isReRun = useRef<boolean>(getReRunPending()).current` reads flag once on mount. `const active = isReRun ? reonboard : bootstrap` routes the `active.mutate()` call to the correct endpoint. Both hooks always initialized unconditionally per React rules-of-hooks.

2. **Loader rotation**: `useEffect` on `active.isPending` sets `setTimeout(() => setLoaderMessageIndex(1), 3000)` and `setTimeout(() => setLoaderMessageIndex(2), 8000)`. Cleanup function clears both. `resetLoader()` called when `!isPending`. Renders `LOADER_MESSAGES[loaderMessageIndex]` while pending.

3. **Fail-open card**: `useEffect` on `active.data` — when `mode === 'bootstrap'`, sets `showFailOpenCard = true` and `setTimeout(2000)` to navigate. Cleanup clears the 2s timer on unmount (T-02-04-03). When `mode === 'full'` or `mode === 'existing'`, navigates immediately.

4. **Flag cleanup**: `setReRunPending(false)` called before `router.replace('/(tabs)')` in both mode paths when `isReRun` is true.

## Verification Evidence

```
# TypeScript (via main checkout node_modules — worktree has no local node_modules per 02-02 pattern)
cd mobile && /path/to/mobile/node_modules/.bin/tsc --noEmit
→ exit 0 (twice — after Task 1 and after Task 2)

# Grep checks — all PASS:
useSkillGraph in users.ts
useUserReonboard in users.ts
settings tab name="settings" in _layout.tsx
setReRunPending in mmkv.ts
getReRunPending in mmkv.ts
"Re-run onboarding" in settings.tsx
"You'll keep your session preferences. Songs and skills reset." in settings.tsx
"Start over?" in settings.tsx
setReRunPending(true) in settings.tsx
clearOnboardedAt in settings.tsx
clearWizardState in settings.tsx
router.replace('/onboarding') in settings.tsx

loaderMessageIndex in uiStore.ts
resetLoader in uiStore.ts
setLoaderMessageIndex in uiStore.ts
"Fletcher is listening..." in preferences.tsx (index 0)
"Working on your first lesson plan..." in preferences.tsx (index 1)
"Almost there..." in preferences.tsx (index 2)
"Got what you said" in preferences.tsx (fail-open card)
useUserReonboard imported in preferences.tsx
getReRunPending imported in preferences.tsx
setReRunPending imported in preferences.tsx
setTimeout in preferences.tsx (3000ms and 8000ms thresholds)
clearTimeout in preferences.tsx
2000ms fail-open auto-navigate timeout
setReRunPending(false) called on success
useRef<boolean>(getReRunPending()) — stable mount-time read
LOADER_MESSAGES ordering correct (listen → working → almost)
active switch pattern: isReRun ? reonboard : bootstrap
bootstrap and reonboard both initialized

# No emojis in any of 6 modified/created files — PASS
# No bare fetch() calls — all network via apiFetch wrapper — PASS
# No unexpected file deletions in either commit — PASS
# No untracked files — PASS
```

## Task 3: Human-Verify Checkpoint (Batched Device Walkthrough)

Task 3 is a `checkpoint:human-verify` gate. Per the batching signal in the plan: EAS quota constraint (3 builds left) — device walkthrough will happen once after both this plan's work AND the orchestrator's follow-up patches land in a single push.

### Verification Protocol for Developer

**Prerequisites:**
- One EAS dev-client build installed on real iOS device (or simulator with MMKV working)
- Railway server running with `ANTHROPIC_API_KEY` set (or local server on :8000)
- Fresh app install: MMKV `onboarded_at` cleared

**Test 1 — Full onboarding + skill-graph verification:**
Walk through all 5 wizard sections with real songs. Tap Complete. Verify:
- Loader shows "Fletcher is listening..." (0–3s)
- Transitions to "Working on your first lesson plan..." at ~3s
- Transitions to "Almost there..." at ~8s if Sonnet takes that long
- On success → Today tab renders

Verify server: `psql $DATABASE_URL -c "SELECT level, COUNT(*) FROM skill_nodes WHERE user_id != '00000000-0000-0000-0000-000000000000' GROUP BY level;"` — expect roots=6, subs>0, leaves>0 with 5-bpm tempo bins.

**Test 2 — Skill graph survives cold-start:**
Force-quit + reopen → opens to Today tab directly (onboarded_at set, redirect skipped). `curl .../api/v1/users/{uuid}/skill-graph` returns the full tree.

**Test 3 — Settings re-run:**
From Today tab → Settings tab → verify "Session length: 30 min" and "Wins marked as: Sunday digest". Tap "Re-run onboarding" → OS Alert appears with "Start over?" and exact body copy → "Keep going" dismisses → "Reset" lands on Welcome screen → Walk through with different songs → Tap Complete → Verify loader rotates → land on Today → Verify server shows only new songs.

**Test 4 — Fail-open UX:**
Blank `ANTHROPIC_API_KEY` on server → Tap Complete → Loader briefly → Fail-open card shows "Got what you said. I'll fill in the details as we go." → Auto-navigates after 2s → Verify only 6 root nodes in DB.

**Test 5 — Navigation regression check:**
All 4 tabs (Today / Library / Toolkit / Settings) navigate without crash. Today still renders Sweet Home Chicago.

## Deviations from Plan

### Auto-fixed Issues

None. The plan was followed exactly as written. Option A (MMKV flag) for re-run routing was the plan's chosen approach — no deviation needed.

### Design Choices Made

**`active.isPending` disables Complete button**: The plan's `canComplete` included `&& !active.isPending` — this prevents double-taps during mutation. Preserved from the plan's code exactly.

**`useRef` for `isReRun`**: The plan suggested reading `getReRunPending()` into a `useRef` so it's stable across renders (doesn't trigger re-renders when MMKV state changes). Implemented exactly as specified.

**Both mutations always initialized**: React rules-of-hooks requires unconditional hook calls. Both `useUserBootstrap()` and `useUserReonboard()` are always called; `active` is computed from the ref to select which one handles the submit.

**`mode === 'existing'` treated like 'full'**: The plan covered `'full'` and `'bootstrap'` cases explicitly. `'existing'` (idempotency guard path from 02-03) is treated identically to `'full'` — navigate immediately, no fail-open card. This is the correct behavior: if the user somehow re-Posts an already-bootstrapped account, they should land in the app without a confusing fail-open message.

## Known Stubs

No stubs introduced in this plan. All functionality is wired end-to-end:
- Settings reads from live `useUser()` query (server-fetched, not mocked)
- `useSkillGraph()` calls the real `/api/v1/users/{userId}/skill-graph` endpoint
- `useUserReonboard()` calls the real `/api/v1/users/{userId}/re-run` endpoint
- Loader rotation and fail-open card are fully implemented (not placeholder text)

The only stub remaining in the Phase 2 codebase is from 02-03: `breakdown={"placeholder": "Phase 3 will populate breakdown"}` on Song rows — unchanged by this plan.

## Phase 2 Success Criteria Status (Code-Complete — Device Verification Pending Task 3)

| SC | Description | Code Status |
|----|-------------|-------------|
| SC 1 | Onboarding wizard captures songs + preferences via 5-section Fletcher UI | Complete (02-02) |
| SC 2 | Sonnet-backed bootstrap persists 3-level DAG skill graph with 5-bpm tempo bins | Complete (02-03) |
| SC 3 | Loader rotation reduces perceived latency on ~15s Sonnet wait | Complete (02-04, this plan) |
| SC 4 | Settings re-run wipes-and-reseeds the graph, wizard re-runs cleanly | Complete (02-04, this plan) |
| SC 5 | Skill graph state survives app cold-start (fetched fresh from server on relaunch) | Complete (02-04, useSkillGraph hook ready; Today tab wiring is Phase 3) |
| D-07 | Fail-open card shows "Got what you said. I'll fill in the details as we go." | Complete (02-04, this plan) |

Device-observed confirmation of SC 1–SC 5 pending Task 3 human-verify checkpoint.

## Observed Timing (Code Analysis — Device Confirmation Pending)

**Loader rotation timing (code-side):**
- `setLoaderMessageIndex(1)` fires at exactly 3000ms after `active.isPending` becomes true.
- `setLoaderMessageIndex(2)` fires at exactly 8000ms.
- Sonnet calls on Railway typically complete in 5–12s based on 02-03 mocked test timings. A 5s call would show message 0→1 only. A 12s call would show all three. Real-world observation pending device walkthrough.

**Fail-open card timing (code-side):**
- Card shows immediately when `active.data.mode === 'bootstrap'` is observed in the useEffect.
- Auto-navigates at 2000ms. Judgment: 2s feels right for "I acknowledged you, now moving on" — not so short it's unreadable, not so long it's stalled. If device walkthrough finds it too long, 1500ms is the suggested tweak.

## Phase 3 Follow-ups Discovered

1. **Today tab still shows Sweet Home Chicago regardless of onboarded user**: Phase 3's per-user song selector will change `GET /api/v1/song-of-day` to accept X-User-ID and return a user-scoped song. For now the system user's Sweet Home Chicago row is the LIMIT 1 result for all users.

2. **useSkillGraph hook not yet consumed by Today tab**: Added in this plan for Phase 3 consumption. The Today tab does not call it yet. SC 5's cold-start verification requires an API-level curl check until Phase 3 wires the Today tab.

3. **Settings preferences are read-only in this slice**: The plan intentionally deferred in-place preference editing. "Change these by re-running onboarding." copy guides users to the re-run flow. Phase 5+ can add inline editing.

4. **Android walkthrough not validated**: Phase 2's device walkthrough (Task 3) will happen on iOS first (EAS quota constraint). Android validation deferred to the same EAS build cycle once iOS is confirmed.

## Threat Flags

No new security-relevant surfaces beyond the plan's `<threat_model>`. All STRIDE entries in the threat register were addressed at the implementation level:
- T-02-04-01 (elevation of privilege via arbitrary user_id re-run): `getOrCreateUserId()` only returns the device's own UUID; server has 403 guard on system UUID (02-03).
- T-02-04-02 (DoS via repeated re-run taps): Alert.alert is modal — no concurrent taps possible.
- T-02-04-03 (timer race on unmount): All `setTimeout` calls have `return () => clearTimeout(t)` cleanup in `useEffect`.
- T-02-04-04 (settings PII disclosure): Preferences (session length, retention format) are non-sensitive.
- T-02-04-SC (package installs): No new packages added this plan. All UI uses existing RN primitives.

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| mobile/src/app/(tabs)/settings.tsx exists | PASSED |
| mobile/src/api/users.ts has useSkillGraph | PASSED |
| mobile/src/api/users.ts has useUserReonboard | PASSED |
| mobile/src/api/mmkv.ts has setReRunPending + getReRunPending | PASSED |
| mobile/src/app/(tabs)/_layout.tsx has name="settings" | PASSED |
| mobile/src/store/uiStore.ts has loaderMessageIndex slice | PASSED |
| mobile/src/app/onboarding/preferences.tsx has LOADER_MESSAGES array | PASSED |
| preferences.tsx has 3000ms + 8000ms setTimeout thresholds | PASSED |
| preferences.tsx has 2000ms fail-open auto-nav timer | PASSED |
| preferences.tsx has setReRunPending(false) before navigation | PASSED |
| preferences.tsx has useRef for isReRun (mount-time read) | PASSED |
| preferences.tsx shows fail-open card on mode='bootstrap' | PASSED |
| preferences.tsx navigates immediately on mode='full'/'existing' | PASSED |
| settings.tsx voice copy: "Start over?" / exact dialog body / "Keep going" / "Reset" | PASSED |
| settings.tsx has setReRunPending(true) + clearOnboardedAt + clearWizardState on confirm | PASSED |
| Task 1 commit eed61fc exists | PASSED |
| Task 2 commit 450199d exists | PASSED |
| npx tsc --noEmit exits 0 (both tasks) | PASSED |
| 0 emojis in all 6 modified/created files | PASSED |
| 0 bare fetch() calls — all via apiFetch | PASSED |
| No unexpected file deletions in either commit | PASSED |
| No untracked files | PASSED |
