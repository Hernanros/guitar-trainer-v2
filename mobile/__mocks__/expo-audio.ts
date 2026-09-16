/**
 * Jest manual mock for `expo-audio` — FLE-5 (Task 7).
 *
 * ---------------------------------------------------------------------------
 * Why this file has to exist
 * ---------------------------------------------------------------------------
 *
 * expo-audio patches native prototypes at MODULE SCOPE:
 *
 *     // node_modules/expo-audio/src/ExpoAudio.ts
 *     const replace = AudioModule.AudioPlayer.prototype.replace;
 *
 * Under Jest there is no native module, so `AudioModule.AudioPlayer` is
 * undefined and merely *importing* the package throws
 * `Cannot read properties of undefined (reading 'prototype')` — before any test
 * body runs. That is not specific to the metronome: it takes down any suite
 * that transitively imports the drill screen, which imports MetronomeControl.
 *
 * Jest applies a manual mock for a node_modules package automatically, with no
 * `jest.mock()` call in the test file, as long as it sits in a `__mocks__`
 * directory adjacent to node_modules. So this unblocks every suite at once.
 *
 * ---------------------------------------------------------------------------
 * Why a permissive mock is not a hole in the coverage
 * ---------------------------------------------------------------------------
 *
 * A hand-written mock can drift from the real package and quietly keep tests
 * green. Two things stop that here:
 *
 *  1. The metronome's audio adapter (src/metronome/audioEmitter.ts) never
 *     imports expo-audio — it takes the module as an argument. Its tests inject
 *     their own fake and assert against it, so the behaviour that matters
 *     (voice pooling, late-beat suppression, accent routing, teardown) is NOT
 *     verified through this file.
 *  2. __tests__/metronome/audioEmitter.test.ts contains a type-level assertion
 *     that the REAL package satisfies the adapter's interface. TypeScript
 *     resolves `expo-audio` to node_modules, not to this mock, so an SDK
 *     upgrade that changes `createAudioPlayer` or `setAudioModeAsync` fails
 *     `tsc` even though this file is untouched.
 *
 * This mock's only job is to let modules import expo-audio without exploding.
 * Keep it that way: no assertions should depend on its behaviour.
 */

export interface MockAudioPlayer {
  source: unknown;
  options: Record<string, unknown> | undefined;
  playing: boolean;
  removed: boolean;
  currentTime: number;
  play(): void;
  pause(): void;
  seekTo(seconds: number): Promise<void>;
  remove(): void;
}

/** Every player built during a test run, in creation order. Cleared by `__resetAudioMock`. */
export const __players: MockAudioPlayer[] = [];

/** Every audio mode requested during a test run. */
export const __audioModes: Record<string, unknown>[] = [];

export function createAudioPlayer(
  source?: unknown,
  options?: Record<string, unknown>,
): MockAudioPlayer {
  const player: MockAudioPlayer = {
    source,
    options,
    playing: false,
    removed: false,
    currentTime: 0,
    play() {
      player.playing = true;
    },
    pause() {
      player.playing = false;
    },
    async seekTo(seconds: number) {
      player.currentTime = seconds;
    },
    remove() {
      player.removed = true;
      player.playing = false;
    },
  };
  __players.push(player);
  return player;
}

export async function setAudioModeAsync(mode: Record<string, unknown>): Promise<void> {
  __audioModes.push(mode);
}

/** Drops recorded state between tests. */
export function __resetAudioMock(): void {
  __players.length = 0;
  __audioModes.length = 0;
}
