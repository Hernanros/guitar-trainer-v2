/**
 * Jest manual mock for `expo-keep-awake` — FLE-5 (Task 7).
 *
 * Unlike the sibling `expo-audio` mock, this one is deliberately NOT
 * behaviour-free: the wake lock has no observable effect in a test renderer, so
 * recording the calls is the only way to prove MetronomeControl acquires the
 * lock when the click starts and releases it when the click stops. That pairing
 * is the whole point — a lock taken on mount and never released would pin a
 * paused drill screen on forever and drain the battery it was added to protect.
 *
 * The real package reference-counts by tag, which is why the component passes
 * one; the mock records tags so a test can assert the release targets the same
 * lock it acquired.
 *
 * Type drift is caught the same way it is for expo-audio: MetronomeControl
 * imports the real `expo-keep-awake` types under `tsc` (which resolves
 * node_modules, not this file), so a signature change fails the typecheck even
 * though this mock is untouched.
 */

/** Tags passed to `activateKeepAwakeAsync`, in call order. */
export const __activated: (string | undefined)[] = [];

/** Tags passed to `deactivateKeepAwake`, in call order. */
export const __deactivated: (string | undefined)[] = [];

export const ExpoKeepAwakeTag = 'ExpoKeepAwakeDefaultTag';

export async function activateKeepAwakeAsync(tag?: string): Promise<void> {
  __activated.push(tag);
}

export async function deactivateKeepAwake(tag?: string): Promise<void> {
  __deactivated.push(tag);
}

export async function isAvailableAsync(): Promise<boolean> {
  return true;
}

export function useKeepAwake(): void {
  // Unused by the metronome — it needs a lock keyed on `running`, not on mount.
}

/** Drops recorded state between tests. */
export function __resetKeepAwakeMock(): void {
  __activated.length = 0;
  __deactivated.length = 0;
}
