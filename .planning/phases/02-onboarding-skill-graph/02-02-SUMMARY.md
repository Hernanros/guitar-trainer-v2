---
phase: 02-onboarding-skill-graph
plan: "02"
subsystem: mobile
tags: [expo-router, react-native, mmkv, onboarding, wizard, fletcher-voice, tanstack-query]
dependency_graph:
  requires:
    - 02-01 (MMKV user-store, apiFetch, useUserBootstrap, root layout redirect)
  provides:
    - 5-section onboarding wizard (Welcome/Play/Working on/Aspire/Preferences)
    - FletcherIntroCard reusable component (heading/body/CTA/progress-dots/children slot)
    - SongInputArea component (multiline TextInput, 300ms MMKV debounce, 4000-char cap)
    - SessionLengthChips component (single-select, no default, force intentional choice)
    - RetentionFormatRadio component (3-option Fletcher-voiced radio group)
    - MMKV wizard-state helpers (setWizardSection/getWizardSection/setLastSection/getLastSection/clearWizardState/setWizardPreferences/getWizardPreferences)
    - D-03 resume-mid-flow via getLastSection() in index.tsx useEffect
    - Complete-tap UserBootstrapRequest wiring to POST /api/v1/users → /(tabs)
  affects:
    - 02-03 (AI skill graph) — wizard delivers the raw_input payload shape; Sonnet call wired here
    - 02-04 (settings re-run) — clearWizardState() exported and ready; setLastSection('index') on re-run reset
tech_stack:
  added: []
  patterns:
    - FletcherIntroCard shell component with children slot (new-territory, no prior analog)
    - 300ms debounced MMKV write-through on TextInput change (D-03 MMKV-only wizard state)
    - Expo Router router.replace() in useEffect for transparent resume redirect (D-03)
    - T-02-02-03 soft 4000-char cap — silent truncation in SongInputArea.handleChange
    - Pressable disabled state with distinct ctaDisabled style (non-interactive visual signal)
key_files:
  created:
    - mobile/src/components/FletcherIntroCard.tsx
    - mobile/src/components/SongInputArea.tsx
    - mobile/src/components/SessionLengthChips.tsx
    - mobile/src/components/RetentionFormatRadio.tsx
    - mobile/src/app/onboarding/play.tsx
    - mobile/src/app/onboarding/working-on.tsx
    - mobile/src/app/onboarding/aspire.tsx
    - mobile/src/app/onboarding/preferences.tsx
  modified:
    - mobile/src/api/mmkv.ts (added wizard-state helpers — 02-01 exports preserved)
    - mobile/src/app/onboarding/_layout.tsx (registered all 5 Stack.Screen entries)
    - mobile/src/app/onboarding/index.tsx (REPLACED 02-01 placeholder with Welcome screen)
decisions:
  - "MMKV flag approach for resume-mid-flow over URL search params: survives app restarts; consistent with wizard state management throughout"
  - "Resume banner ('Welcome back. Picking up where you left off.') deferred: SongInputArea prefill is the visible resume signal; banner is a polish pass"
  - "Soft 4000-char cap (T-02-02-03): silent truncation in SongInputArea.handleChange — does not block user, prevents pathological bootstrap payload"
  - "Loader rotation (Working on your first lesson plan... / Almost there...) deferred to 02-04 polish"
  - "tsc verified via main checkout node_modules — worktree has no local node_modules (expected)"
metrics:
  duration: "~7 minutes"
  completed: "2026-07-20"
  tasks_completed: 2
  tasks_total: 3
  files_created: 8
  files_modified: 3
---

# Phase 2 Plan 02: Fletcher-Voiced Onboarding Wizard Summary

**One-liner:** 5-section Fletcher-voiced wizard (Welcome/Play/Working on/Aspire/Preferences) with 300ms MMKV debounce, D-03 resume-mid-flow via transparent router.replace(), and Complete-tap UserBootstrapRequest wiring to POST /api/v1/users → /(tabs).

## What Was Built

Four Fletcher UI primitives + MMKV wizard helpers (Task 1), then five wizard screens wired end-to-end (Task 2).

### Task 1: Fletcher UI Primitives + MMKV Helpers

1. **FletcherIntroCard** (`mobile/src/components/FletcherIntroCard.tsx`): Reusable shell across all 5 sections. Props: `heading`, `body`, `cta` (default "Continue"), `onNext`, `disabled`, `progress` (1-indexed dots), `showPortrait` (deferred), `children` slot. Progress dots render as `#E07B39` (active) vs `#3A3A3A` (inactive) in a flex row. Pressable CTA with `ctaDisabled` background (`#4A3A2A`) when `disabled=true`.

2. **SongInputArea** (`mobile/src/components/SongInputArea.tsx`): `multiline` `TextInput` with `autoCapitalize="none"` `autoCorrect={false}` `textAlignVertical="top"`. Reads `getWizardSection(section)` on mount (D-03 prefill). 300ms debounced `setWizardSection()` on change. T-02-02-03 soft 4000-char cap: `handleChange` silently truncates input at the cap boundary before state update and MMKV write.

3. **SessionLengthChips** (`mobile/src/components/SessionLengthChips.tsx`): 4 chips labelled `15 min` / `30 min` / `45 min` / `60 min` (space before "min" per `<specifics>`). Single-select, no default (D-12). Active chip: orange fill, dark text. Inactive: orange border, orange text.

4. **RetentionFormatRadio** (`mobile/src/components/RetentionFormatRadio.tsx`): 3-option radio with exact `<voice_contract>` copy: `"Streak: I show up daily, count me."` / `"Weekly digest: show me what I did on Sunday."` / `"Monthly milestone: mark the big wins."` Active row gets border-left accent (mirrors techniqueCard pattern from Today tab).

5. **mmkv.ts extended**: `WizardSection` type, `setWizardSection`, `getWizardSection`, `setLastSection`, `getLastSection`, `clearWizardState`, `setWizardPreferences`, `getWizardPreferences`. All 02-01 exports preserved (`userMmkv`, `getOrCreateUserId`, `getOnboardedAt`, `setOnboardedAt`, `clearOnboardedAt`).

### Task 2: 5-Section Wizard Screens

1. **_layout.tsx** updated: all 5 `Stack.Screen` entries registered (`index`, `play`, `working-on`, `aspire`, `preferences`); `gestureEnabled: false` prevents swipe-back from bypassing MMKV state writes.

2. **index.tsx** (replaces 02-01 placeholder): Welcome screen — `"Meet Fletcher."` / `"He's the teacher Fletcher should have been. Five short questions. Then we practice."` / CTA `"Start"`. `useEffect` reads `getLastSection()` on mount; if non-null and not `'index'`, `router.replace(`/onboarding/${last}`)` fires before render paint — no flicker on resume.

3. **play.tsx**: `"What can you play?"` / `SongInputArea section="play"` / progress 2/5 / Continue → working-on. `setLastSection('working-on')` on Continue.

4. **working-on.tsx**: `"What are you working on?"` / `"close but not clean yet"` (respects the grind) / `SongInputArea section="working-on"` / progress 3/5 / Continue → aspire. `setLastSection('aspire')` on Continue.

5. **aspire.tsx**: `"What are you chasing?"` / `"Say it out loud — Fletcher will build toward it."` (confidence signal per fletcher-identity.md line 78) / `SongInputArea section="aspire" minHeight={200}` (larger input per `<specifics>`) / progress 4/5 / Continue → preferences. `setLastSection('preferences')` on Continue.

6. **preferences.tsx**: `SessionLengthChips` + `RetentionFormatRadio` / `FletcherIntroCard heading="How long do you have most days?"` / `cta="Complete"` / Complete disabled until `sessionLength !== null` (D-12) / `retention ?? 'streak'` default (D-13) / `useUserBootstrap.mutate()` with full `UserBootstrapRequest` (split song lines + raw verbatim text + session preferences) / `clearWizardState()` + `setLastSection('index')` + `router.replace('/(tabs)')` on success / `isPending`: `"Fletcher is listening..."` / `isError`: `"Fletcher lost the thread. Try that again."`

## Verification Evidence

```
# TypeScript check (verified via main checkout node_modules)
cd mobile && node_modules/.bin/tsc --noEmit → exit 0

# Screen files exist
ls mobile/src/app/onboarding/ → _layout.tsx index.tsx play.tsx working-on.tsx aspire.tsx preferences.tsx

# _layout.tsx registers all 5 sections
grep -c 'Stack.Screen' → 6 (including screenOptions row — 5 named screens + options)
grep 'name="preferences"' → found

# Voice contract copy verified
grep '"Meet Fletcher."' index.tsx → found
grep "He's the teacher Fletcher should have been" index.tsx → found
grep 'router.replace' index.tsx → found
grep "What can you play?" play.tsx → found
grep "What are you working on?" working-on.tsx → found
grep "What are you chasing?" aspire.tsx → found
grep "minHeight={200}" aspire.tsx → found
grep "useUserBootstrap" preferences.tsx → found
grep "clearWizardState" preferences.tsx → found
grep "Fletcher lost the thread" preferences.tsx → found
grep "Fletcher is listening" preferences.tsx → found
grep "I show up daily, count me" RetentionFormatRadio.tsx → found
grep "show me what I did on Sunday" RetentionFormatRadio.tsx → found
grep "mark the big wins" RetentionFormatRadio.tsx → found
grep "15 min" && "30 min" && "45 min" && "60 min" SessionLengthChips.tsx → all found

# Emoji check (0 in all files)
grep -P '[\x{1F300}-\x{1F9FF}]' all 10 task files → 0 matches each
```

## Task 3: Human-Verify Checkpoint

Task 3 is a blocking human-verify checkpoint (type="checkpoint:human-verify", gate="blocking"). The wizard implementation is complete and ready for device verification.

### Verification Instructions

**Prep — reset MMKV for fresh-install path:**
1. On simulator: Device → Erase All Content and Settings, OR uninstall + reinstall the app.
2. Run `cd mobile && npm run ios` (or `npm run android`) with the dev-client build.

**Test 1 — Happy path + ONB-03 timing:**
1. Start a stopwatch when the app opens on "Meet Fletcher."
2. Walk through all 5 sections. Type at least one song per section (e.g., `Sweet Home Chicago`, `Little Wing`, `Eruption`).
3. Pick a session length chip. Pick a retention format (optional — defaults to streak).
4. Tap `Complete`. Stop stopwatch when Today tab renders.
5. PASS = elapsed time ≤ 8 minutes (ONB-03 budget).

**Test 2 — Resume mid-flow:**
1. Reinstall fresh.
2. Walk to the Aspire section, type a few songs, do NOT tap Continue. Force-quit the app.
3. Reopen. PASS = you land on the Aspire section with your text still prefilled (SongInputArea reads MMKV on mount). The resume banner copy is deferred; prefilled text is the resume signal.

**Test 3 — Fletcher voice audit:**
1. Screenshot each of the 5 sections.
2. Read each heading + body + CTA aloud.
3. PASS = strings match `<voice_contract>` exactly. No emojis. No exclamation points.
4. Verify retention radio labels: `Streak: I show up daily, count me.` / `Weekly digest: show me what I did on Sunday.` / `Monthly milestone: mark the big wins.`

**Test 4 — Error path:**
1. Kill FastAPI server (or airplane mode after app loads).
2. Walk through wizard and tap Complete on Preferences.
3. PASS = error box shows `"Fletcher lost the thread. Try that again."` Complete is still tappable to retry.

**Test 5 — DB verification (requires FastAPI running + Postgres accessible):**
```sql
SELECT id, preferences, raw_onboarding_text, onboarded_at
FROM users
WHERE id != '00000000-0000-0000-0000-000000000000'
ORDER BY created_at DESC LIMIT 1;
```
PASS = row shows `preferences.session_length_min` populated, `preferences.retention_format` populated, `raw_onboarding_text` JSONB shows verbatim typed text, `onboarded_at` is recent.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing] T-02-02-03 soft DoS cap not in original plan spec**
- **Found during:** Task 1, reviewing the threat model.
- **Issue:** The threat model entry T-02-02-03 specified "Add a soft cap in SongInputArea: reject input longer than 4000 chars per section (silently truncate on write to MMKV)." This mitigation was in the threat model but not in the Task 1 action spec.
- **Fix:** Added `const CHAR_CAP = 4000` constant and truncation logic in `SongInputArea.handleChange`: `const safe = next.length > CHAR_CAP ? next.slice(0, CHAR_CAP) : next`.
- **Files modified:** `mobile/src/components/SongInputArea.tsx`
- **Commit:** 7972abd

### Design Choices Made

**Resume banner deferred:** The plan's Task 2 action offered two alternatives for the resume banner ("Welcome back. Picking up where you left off."):
1. Show banner above FletcherIntroCard on resume.
2. Simpler: SongInputArea prefill is the visible resume signal; no explicit banner.

Option 2 was chosen. Rationale: the prefilled text is a clear resume signal; a banner adds UI complexity for minimal UX gain in a wizard flow where the user knows they were mid-task. The banner copy is ready in `mmkv.ts` comment for 02-04 polish if desired.

**Loader rotation deferred to 02-04:** The plan explicitly called this out — `"Fletcher is listening..."` static text ships in this slice. Rotation to `"Working on your first lesson plan..."` and `"Almost there..."` is a 02-04 task.

**Stray file cleanup:** A typo during Write created `.claire/worktrees/.../aspire.tsx` instead of `.claude/worktrees/.../aspire.tsx`. The correct file was already created at the right path. The stray `.claire/` directory was removed with `rm -rf`.

## Known Stubs

| Stub | File | Reason |
|------|------|--------|
| `"Fletcher is listening..."` static loader | `preferences.tsx` | Rotation to 3-phase copy is a 02-04 polish task per plan |
| Resume banner not rendered | `index.tsx` / section screens | Deferred per Task 2 "simpler alternative" guidance — SongInputArea prefill is the visible resume signal |
| `showPortrait` prop always false | `FletcherIntroCard.tsx` | Character portrait asset deferred to Phase 2 UI-phase per `<deferred>` section of 02-CONTEXT.md |

These stubs do not prevent the plan's goal from being achieved — the wizard flows end-to-end, MMKV state persists, and Complete tap posts a correctly-shaped payload.

## Threat Flags

No new security-relevant surfaces beyond the plan's `<threat_model>`. All STRIDE entries addressed:
- T-02-02-01 (MMKV text disclosure): accepted per POC scope + `clearWizardState()` on success.
- T-02-02-02 (raw_input PII): mitigated — HTTPS transport, Railway at-rest encryption; PII redaction deferred to Phase 4+.
- T-02-02-03 (large payload DoS): mitigated — 4000-char soft cap in `SongInputArea.handleChange`.
- T-02-02-04 (MMKV tampering on jailbroken device): accepted per POC single-user scope.
- T-02-02-SC (package installs): no new packages added; all components hand-rolled from existing RN primitives.

## ONB-03 Completion Time Estimate

Developer walk-through timing (code analysis, not device run):
- Welcome → play: ~15 seconds (just a tap)
- Play section: 30-90 seconds to type 1-3 songs
- Working on section: 30-90 seconds
- Aspire section: 30-90 seconds (slightly longer — larger input, more evocative)
- Preferences section: 15-30 seconds (tap a chip, tap a radio)
- Total estimated: 2-5 minutes for a natural pace walk-through

ONB-03 budget: ≤ 8 minutes. The 5-section design comfortably fits within this envelope. Task 3 Test 1 will produce the actual measured time.

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| FletcherIntroCard.tsx exists | PASSED |
| SongInputArea.tsx exists | PASSED |
| SessionLengthChips.tsx exists | PASSED |
| RetentionFormatRadio.tsx exists | PASSED |
| mmkv.ts has all 8 wizard exports | PASSED |
| All 5 wizard screen files exist | PASSED |
| _layout.tsx has all 5 Stack.Screen entries | PASSED |
| Task 1 commit 7972abd exists | PASSED |
| Task 2 commit 2252e9a exists | PASSED |
| npx tsc --noEmit exits 0 (main checkout) | PASSED |
| "Meet Fletcher." in index.tsx | PASSED |
| resume router.replace in index.tsx | PASSED |
| setLastSection in index/play/working-on/aspire | PASSED |
| minHeight={200} in aspire.tsx | PASSED |
| useUserBootstrap in preferences.tsx | PASSED |
| clearWizardState in preferences.tsx | PASSED |
| "Fletcher is listening..." in preferences.tsx | PASSED |
| "Fletcher lost the thread. Try that again." in preferences.tsx | PASSED |
| 0 emojis in all 10 task files | PASSED |
| Exact retention radio copy strings | PASSED |
| Exact chip labels (15/30/45/60 min) | PASSED |
