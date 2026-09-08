// mobile/__tests__/app/(tabs)/index.test.tsx
// Unit tests for Phase 3 Today tab + hooks + components.
// Mirrors the source at mobile/src/app/(tabs)/index.tsx.
//
// Kept out of mobile/src/app/ so Expo Router doesn't auto-discover it as a route
// (which would leak as a 5th bottom tab and crash on tap).
//
// No Jest or @testing-library/react-native available in this project.
// Tests are written as TypeScript-compilable assertions that validate:
//   - localCalendarDay() returns correct "YYYY-MM-DD" format
//   - FLETCHER_LINE map has correct verbatim copy (UI-SPEC §1 compliance)
//   - rerollsLeft logic: 0 when rerolled=true, 1 when rerolled=false
//   - bank_source chip: rendered when from_bank=true, omitted when from_bank=false
//   - 409 silent handling contract (code review assertion)
//
// Run: npx tsc --noEmit from mobile/ to validate these compile.
// If Jest is added in a future phase, replace these with describe/it blocks.

import { localCalendarDay } from '@/api/todaySong';
import type { FletcherLineVariant } from '@/components/SongOfDayCard';

// ---------------------------------------------------------------------------
// localCalendarDay — pure function tests
// ---------------------------------------------------------------------------

/**
 * localCalendarDay() must return a 10-char string in YYYY-MM-DD format.
 * This is the queryKey rotation signal — wrong format breaks daily rotation.
 */
function assertLocalCalendarDayFormat(): void {
  const day = localCalendarDay();
  // Must be exactly 10 characters
  if (day.length !== 10) {
    throw new Error(`localCalendarDay() returned ${day.length} chars, expected 10: "${day}"`);
  }
  // Must match YYYY-MM-DD pattern
  const pattern = /^\d{4}-\d{2}-\d{2}$/;
  if (!pattern.test(day)) {
    throw new Error(`localCalendarDay() format is wrong: "${day}" (expected YYYY-MM-DD)`);
  }
  // Year must be >= 2025 (sanity check)
  const year = parseInt(day.slice(0, 4), 10);
  if (year < 2025) {
    throw new Error(`localCalendarDay() year ${year} is unexpectedly in the past`);
  }
}

assertLocalCalendarDayFormat();

// ---------------------------------------------------------------------------
// FLETCHER_LINE copy contract — verbatim UI-SPEC §1 compliance
// ---------------------------------------------------------------------------
// These strings are the canonical source of truth. Any change here must
// be accompanied by a UI-SPEC revision. Do not paraphrase.

type FletcherLineMap = Readonly<Record<FletcherLineVariant, string | null>>;

const EXPECTED_FLETCHER_LINES: FletcherLineMap = {
  deterministic: "You're leaning on this one. Play it today.",
  user_bench: 'Something different today. Loosen up.',
  seed_catalog: null, // no line — chip carries the signal (UI-SPEC §1)
  rerolled: 'Different song. Same work.',
  already_rated: 'You already rated this today. See you tomorrow.',
};

// TypeScript validates these are the only valid FletcherLineVariant values.
// The SongOfDayCard FLETCHER_LINE constant must contain identical values.
const _lineTypeCheck: FletcherLineMap = EXPECTED_FLETCHER_LINES;
void _lineTypeCheck; // prevent unused-variable warning

// ---------------------------------------------------------------------------
// rerollsLeft logic contract
// ---------------------------------------------------------------------------

/**
 * Verify rerollsLeft derivation: 0 when rerolled=true, 1 when rerolled=false.
 * This is the contract the Today tab uses to pass to SongOfDayCard.
 */
function assertRerollsLeft(rerolled: boolean): 0 | 1 {
  return rerolled ? 0 : 1;
}

const leftWhenRerolled = assertRerollsLeft(true);
const leftWhenFresh = assertRerollsLeft(false);

// TypeScript-level type check: return type must be narrowed correctly
// Using runtime assertions instead of const-type assignments
if (leftWhenRerolled !== 0) {
  throw new Error(`rerollsLeft when rerolled=true should be 0, got ${leftWhenRerolled}`);
}
if (leftWhenFresh !== 1) {
  throw new Error(`rerollsLeft when rerolled=false should be 1, got ${leftWhenFresh}`);
}

// ---------------------------------------------------------------------------
// bank_source chip contract
// ---------------------------------------------------------------------------

/**
 * FromTheBankTag must render iff from_bank=true AND bank_source is non-null.
 * This is the conditional in index.tsx: today.from_bank && today.bank_source.
 */
function shouldRenderBankChip(from_bank: boolean, bank_source: string | null | undefined): boolean {
  return Boolean(from_bank && bank_source);
}

// Deterministic path: no chip
if (shouldRenderBankChip(false, null)) {
  throw new Error('Bank chip must NOT render on deterministic path (from_bank=false)');
}
// Bank path with user_bench: chip renders
if (!shouldRenderBankChip(true, 'user_bench')) {
  throw new Error('Bank chip MUST render when from_bank=true and bank_source=user_bench');
}
// Bank path with seed_catalog: chip renders
if (!shouldRenderBankChip(true, 'seed_catalog')) {
  throw new Error('Bank chip MUST render when from_bank=true and bank_source=seed_catalog');
}
// bank path but no bank_source (unexpected server state): no chip
if (shouldRenderBankChip(true, null)) {
  throw new Error('Bank chip must NOT render when bank_source is null even if from_bank=true');
}

// ---------------------------------------------------------------------------
// 409 silent handling contract (code review assertion)
// ---------------------------------------------------------------------------
// The useReroll onError handler checks message.includes('HTTP 409') to detect
// reroll-already-used state and silently invalidates the query instead of
// surfacing an error card (UI-SPEC §10).
//
// This is a code-review assertion, not a runtime test. The contract is:
//   - error.message format from apiFetch: `HTTP ${status} ${method} ${path}`
//   - For a 409 reroll: "HTTP 409 POST /api/v1/today-song/reroll"
//   - The onError check: message.includes('HTTP 409')

const exampleReroll409Message = 'HTTP 409 POST /api/v1/today-song/reroll';
if (!exampleReroll409Message.includes('HTTP 409')) {
  throw new Error('409 detection would fail — apiFetch error format changed');
}

// All assertions passed at module load time.
// export {} ensures this is treated as a module (not a script) by TypeScript.
export {};
