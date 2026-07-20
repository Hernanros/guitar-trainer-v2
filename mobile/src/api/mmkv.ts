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
//
// expo-crypto v57: Crypto.randomUUID() returns a standard v4 UUID string.
// Verified against https://docs.expo.dev/versions/v57.0.0/ (mobile/AGENTS.md mandate).
import { createMMKV } from 'react-native-mmkv';
import * as Crypto from 'expo-crypto';

// Separate MMKV instance from the query-cache instance in queryClient.ts.
export const userMmkv = createMMKV({ id: 'user-store' });

const USER_ID_KEY = 'user_id';
const ONBOARDED_AT_KEY = 'onboarded_at';

/**
 * Returns the stable device UUID for this installation.
 * Generates a new v4 UUID on first call and persists it.
 * On reinstall: new UUID = re-onboard (acceptable POC behavior per D-04).
 */
export function getOrCreateUserId(): string {
  const existing = userMmkv.getString(USER_ID_KEY);
  if (existing) return existing;
  const fresh = Crypto.randomUUID();
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
