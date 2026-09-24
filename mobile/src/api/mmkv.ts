// mobile/src/api/mmkv.ts
// Shared MMKV instance for user identity and onboarded_at marker (Phase 2).
//
// SEPARATE instance from the query-cache instance in queryClient.ts.
// Rationale: the query-cache can be invalidated/wiped without losing device
// identity (user_id) or the onboarded_at marker (which controls the root layout
// redirect). Mixing them would cause re-onboarding when the cache expires.
//
// MMKV v4 (NitroModules) API: createMMKV() replaces `new MMKV()` from v3.
// Verified against Phase 1 patterns in queryClient.ts (deviation #4 in 01-01-SUMMARY.md).
import { createMMKV } from 'react-native-mmkv';

// Separate MMKV instance from the query-cache instance in queryClient.ts.
export const userMmkv = createMMKV({ id: 'user-store' });

const USER_ID_KEY = 'user_id';
const ONBOARDED_AT_KEY = 'onboarded_at';

/**
 * Pure-JS UUID v4 generator (no native dep — kept out of expo-crypto so this file
 * stays JS-only and Phase 2 doesn't trigger a fresh dev-client build).
 *
 * The user_id is a device-local scope key, not a security token: Math.random()
 * is fine. Format follows RFC 4122 v4 (version=4, variant=8/9/A/B).
 */
function generateUuidV4(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/**
 * Returns the stable device UUID for this installation.
 * Generates a new v4 UUID on first call and persists it.
 * On reinstall: new UUID = re-onboard (acceptable POC behavior per D-04).
 */
export function getOrCreateUserId(): string {
  const existing = userMmkv.getString(USER_ID_KEY);
  if (existing) return existing;
  const fresh = generateUuidV4();
  userMmkv.set(USER_ID_KEY, fresh);
  return fresh;
}

/**
 * Returns the ISO timestamp written by setOnboardedAt(), or null if the user
 * has not completed onboarding. Used by the root layout redirect gate.
 */
export function getOnboardedAt(): string | null {
  return userMmkv.getString(ONBOARDED_AT_KEY) ?? null;
}

/**
 * Persists the onboarded_at ISO timestamp after a successful POST /api/v1/users.
 * Once written, the root layout redirect will skip /onboarding on subsequent launches.
 */
export function setOnboardedAt(iso: string): void {
  userMmkv.set(ONBOARDED_AT_KEY, iso);
}

/**
 * Clears the onboarded_at marker.
 * Called by Settings "Re-run onboarding" (02-04) — returns the user to /onboarding
 * on next app open while keeping their user_id unchanged.
 */
export function clearOnboardedAt(): void {
  // MMKV v4 API: remove() replaces delete() from v3 (see 01-01-SUMMARY.md deviation #5)
  userMmkv.remove(ONBOARDED_AT_KEY);
}

// ---------------------------------------------------------------------------
// Wizard state helpers (02-02)
//
// All wizard-section text (play / working-on / aspire) is buffered here.
// Nothing hits Postgres until the user taps Complete (D-03).
// On force-quit + reopen, the wizard reads these keys to resume mid-flow.
// clearWizardState() is called on successful Complete and by 02-04's re-run.
// ---------------------------------------------------------------------------

/**
 * Section IDs — must match Expo Router file names under onboarding/.
 */
export type WizardSection = 'index' | 'play' | 'working-on' | 'aspire' | 'preferences';

const WIZARD_SECTION_PREFIX = 'wizard.section.';
const WIZARD_LAST_SECTION_KEY = 'wizard.last_section';
const WIZARD_PREFS_KEY = 'wizard.preferences';

/**
 * Persists the free-text content typed into a wizard section.
 * Called on every keystroke via 300ms debounce in SongInputArea.
 * D-03: MMKV-only wizard state; nothing touches Postgres until Complete.
 */
export function setWizardSection(section: WizardSection, text: string): void {
  userMmkv.set(`${WIZARD_SECTION_PREFIX}${section}`, text);
}

/**
 * Returns the text previously typed in a section, or '' if none.
 * Used by SongInputArea to prefill on resume (D-03).
 */
export function getWizardSection(section: WizardSection): string {
  return userMmkv.getString(`${WIZARD_SECTION_PREFIX}${section}`) ?? '';
}

/**
 * Records the section the user was last on.
 * index.tsx reads this on mount and redirects when non-null + not 'index'.
 * D-03: resume mid-flow.
 */
export function setLastSection(section: WizardSection): void {
  userMmkv.set(WIZARD_LAST_SECTION_KEY, section);
}

/**
 * Returns the last section the user visited, or null on first launch.
 */
export function getLastSection(): WizardSection | null {
  const v = userMmkv.getString(WIZARD_LAST_SECTION_KEY);
  return (v as WizardSection | undefined) ?? null;
}

/**
 * Wipes all wizard-state keys.
 * Called on successful Complete (preferences.tsx onSuccess) and by 02-04 re-run.
 */
export function clearWizardState(): void {
  const sections: WizardSection[] = ['index', 'play', 'working-on', 'aspire', 'preferences'];
  sections.forEach((s) => userMmkv.remove(`${WIZARD_SECTION_PREFIX}${s}`));
  userMmkv.remove(WIZARD_LAST_SECTION_KEY);
  userMmkv.remove(WIZARD_PREFS_KEY);
}

/**
 * Persists session-length + retention-format selections from the Preferences section.
 * Stored as a JSON blob. Read back on mount to survive app restarts (D-03).
 */
export function setWizardPreferences(prefs: {
  session_length_min: 15 | 30 | 45 | 60 | null;
  retention_format: 'streak' | 'weekly_digest' | 'monthly_milestone' | null;
}): void {
  userMmkv.set(WIZARD_PREFS_KEY, JSON.stringify(prefs));
}

/**
 * Returns the preferences typed so far, defaulting to null fields if unset.
 */
export function getWizardPreferences(): {
  session_length_min: 15 | 30 | 45 | 60 | null;
  retention_format: 'streak' | 'weekly_digest' | 'monthly_milestone' | null;
} {
  const raw = userMmkv.getString(WIZARD_PREFS_KEY);
  if (!raw) return { session_length_min: null, retention_format: null };
  try {
    return JSON.parse(raw);
  } catch {
    return { session_length_min: null, retention_format: null };
  }
}

// ---------------------------------------------------------------------------
// Re-run flow helpers (02-04)
//
// When Settings "Re-run onboarding" fires, it clears onboarded_at + wizard
// state, then routes to /onboarding. The wizard Complete tap needs to know
// whether this is a fresh boot (use POST /users bootstrap) or a re-run
// (use POST /users/{id}/re-run which wipes first). This flag distinguishes
// the two cases without having to pass state through the route.
// ---------------------------------------------------------------------------

const RE_RUN_PENDING_KEY = 'wizard.re_run_pending';

/**
 * Sets or clears the re-run-pending flag.
 * Called by Settings before routing to /onboarding (set true),
 * and by preferences.tsx on successful Complete (set false).
 */
export function setReRunPending(pending: boolean): void {
  if (pending) {
    userMmkv.set(RE_RUN_PENDING_KEY, 'true');
  } else {
    userMmkv.remove(RE_RUN_PENDING_KEY);
  }
}

/**
 * Returns true if the wizard was entered via Settings "Re-run onboarding".
 * Used by preferences.tsx to branch between useUserBootstrap and useUserReonboard.
 */
export function getReRunPending(): boolean {
  return userMmkv.getString(RE_RUN_PENDING_KEY) === 'true';
}

// ---------------------------------------------------------------------------
// Drill tempo-ladder persistence (FLE-73)
//
// The focused drill screen (breakdown/[songId]/drill/[drillIndex].tsx) tracks
// tempo-ladder position (current rung's BPM + rep count within that rung)
// here, keyed per song+drill, so backgrounding or killing the app mid-ladder
// doesn't strand the user before RatingPills unlock — which only appears
// once the ladder completes at target_bpm, the one signal the feedback loop
// needs (FLE-56).
// ---------------------------------------------------------------------------

export type DrillLadderState = { currentBpm: number; repCount: number };

const DRILL_LADDER_PREFIX = 'drill_ladder.';

function drillLadderKey(songId: number, drillIndex: number): string {
  return `${DRILL_LADDER_PREFIX}${songId}.${drillIndex}`;
}

/**
 * Persists the tempo-ladder rung and rep count for one drill.
 * Called on every rep/tempo advance so a killed app resumes mid-ladder.
 */
export function setDrillLadderState(
  songId: number,
  drillIndex: number,
  state: DrillLadderState,
): void {
  userMmkv.set(drillLadderKey(songId, drillIndex), JSON.stringify(state));
}

/**
 * Returns the persisted ladder state for a drill, or null if none was saved
 * (fresh drill, or state already cleared after rating).
 */
export function getDrillLadderState(
  songId: number,
  drillIndex: number,
): DrillLadderState | null {
  const raw = userMmkv.getString(drillLadderKey(songId, drillIndex));
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Clears the persisted ladder state for a drill.
 * Called after a successful rating submission so a future attempt at this
 * drill starts a fresh ladder rather than resuming the just-rated one.
 */
export function clearDrillLadderState(songId: number, drillIndex: number): void {
  userMmkv.remove(drillLadderKey(songId, drillIndex));
}
