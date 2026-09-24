// mobile/src/hooks/use-ota-update.ts
// Applies a published OTA update on the launch that finds it, instead of the one after.
//
// Why this exists: expo-updates' default (checkAutomatically: ON_LOAD, no
// fallbackToCacheTimeout) downloads a new bundle in the background and swaps it in at
// the *next* cold start. So the first launch after a publish still renders the old JS.
// From the outside that is indistinguishable from "the fix was never shipped" — which is
// exactly how the Toolkit metronome read: published, live on the channel, invisible on
// device. Awaiting the fetch at launch and reloading closes that one-launch gap.
//
// Cold start only, deliberately. Reloading tears down React state, and the metronome
// keeps clicking through a background/foreground round trip; reloading on foreground
// would cut off a running practice session. At launch there is nothing to interrupt.
// Settings exposes checkForUpdateNow() for an on-demand check mid-session.
import { useEffect } from 'react';
import * as Updates from 'expo-updates';

export type OtaOutcome =
  /** A new bundle was downloaded; reloadAsync has been called and the app is restarting. */
  | 'reloading'
  /** The running bundle is already the newest one on the channel. */
  | 'up-to-date'
  /** Dev client / Expo Go / updates not configured — nothing to check. */
  | 'disabled'
  /** Offline or the update server errored. Non-fatal: keep running what we have. */
  | 'failed';

/**
 * Check the channel, and if it has something newer, download it and restart into it.
 * Never throws: a failed check must not keep the app from launching offline.
 */
export async function applyUpdateIfAvailable(): Promise<OtaOutcome> {
  if (!Updates.isEnabled) return 'disabled';
  try {
    const check = await Updates.checkForUpdateAsync();
    if (!check.isAvailable) return 'up-to-date';
    const fetched = await Updates.fetchUpdateAsync();
    if (!fetched.isNew) return 'up-to-date';
    await Updates.reloadAsync();
    return 'reloading';
  } catch {
    return 'failed';
  }
}

/** Run applyUpdateIfAvailable once, at mount. Mounted from the root layout. */
export function useOtaUpdateOnLaunch(): void {
  useEffect(() => {
    void applyUpdateIfAvailable();
  }, []);
}

/**
 * One line naming the JS bundle and native runtime actually running, so "is the fix on
 * my phone?" is a question the phone can answer — and a pilot tester can read aloud or
 * paste into a bug report without developer help (FLE-30). Embedded = the bundle baked
 * into the installed build, i.e. no OTA has been applied yet. Runtime version is the
 * fingerprint FLE-27 gates updates on: two testers quoting different runtimes are on
 * different native binaries, not just different JS.
 */
export function describeRunningBundle(): string {
  if (!Updates.isEnabled) return 'Development bundle';
  const runtime = Updates.runtimeVersion ?? 'unknown';
  if (Updates.isEmbeddedLaunch || !Updates.updateId) return `Bundle: embedded · runtime ${runtime}`;
  const short = Updates.updateId.slice(0, 8);
  const when = Updates.createdAt ? Updates.createdAt.toISOString().slice(0, 16).replace('T', ' ') : 'unknown date';
  return `Bundle: ${short} · runtime ${runtime} · published ${when} UTC`;
}
