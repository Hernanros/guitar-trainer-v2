/// <reference types="jest" />
// mobile/__mocks__/expo-updates.ts
// expo-updates is a native module; under jest there is no host object behind it, so any
// test that mounts the root layout (which checks for an OTA at launch) needs this stand-in.
//
// The real module exposes launch facts as plain consts. Babel compiles `export let` to a
// live getter, so __setState below can move them between tests the way a relaunch would.
export let isEnabled = true;
export let isEmbeddedLaunch = false;
export let updateId: string | null = null;
export let createdAt: Date | null = null;

export const checkForUpdateAsync = jest.fn(async () => ({ isAvailable: false }));
export const fetchUpdateAsync = jest.fn(async () => ({ isNew: false }));
export const reloadAsync = jest.fn(async () => {});

/** Test helper — set the launch facts a real build would have baked in. */
export function __setState(next: {
  isEnabled?: boolean;
  isEmbeddedLaunch?: boolean;
  updateId?: string | null;
  createdAt?: Date | null;
}): void {
  if (next.isEnabled !== undefined) isEnabled = next.isEnabled;
  if (next.isEmbeddedLaunch !== undefined) isEmbeddedLaunch = next.isEmbeddedLaunch;
  if (next.updateId !== undefined) updateId = next.updateId;
  if (next.createdAt !== undefined) createdAt = next.createdAt;
}

/** Test helper — back to a plain enabled OTA build with nothing applied yet. */
export function __reset(): void {
  isEnabled = true;
  isEmbeddedLaunch = false;
  updateId = null;
  createdAt = null;
  checkForUpdateAsync.mockReset().mockResolvedValue({ isAvailable: false });
  fetchUpdateAsync.mockReset().mockResolvedValue({ isNew: false });
  reloadAsync.mockReset().mockResolvedValue(undefined);
}
