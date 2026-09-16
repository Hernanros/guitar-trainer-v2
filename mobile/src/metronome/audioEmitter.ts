// mobile/src/metronome/audioEmitter.ts
// The audible ClickEmitter — FLE-5 (Task 7).
//
// ---------------------------------------------------------------------------
// Why this file still imports nothing from expo-audio
// ---------------------------------------------------------------------------
//
// `expo-audio` IS now installed — Miagi approved it on FLE-5, and
// MetronomeControl.tsx passes the real module in. This file nonetheless keeps
// taking the module as an ARGUMENT, the same discipline the engine uses for
// `now` / `setTimer` / `clearTimer`, because that is what lets the parts most
// likely to be wrong — voice pooling, late-beat suppression, accent routing,
// teardown, surviving a native throw — be covered by unit tests on a laptop
// instead of discovered on a device, where each round trip costs an EAS build
// slot. Importing the module here would put all of it behind a native mock.
//
// API verified against the installed package's own type declarations
// (expo-audio@57.0.5, node_modules/expo-audio/build), not from memory:
//   createAudioPlayer(source?, options?: AudioPlayerOptions): AudioPlayer
//   player.play(): void
//   player.seekTo(seconds): Promise<void>
//   player.remove(): void
//   setAudioModeAsync(mode: Partial<AudioMode>): Promise<void>
//
// The sounds are in the tree: assets/audio/tick.wav and accent.wav, generated
// by scripts/generate-click-assets.py.

import type { ClickEmitter, MetronomeBeat } from './types';

// ---------------------------------------------------------------------------
// The slice of expo-audio this adapter needs. Structural types, not imports —
// the real module satisfies these; the tests satisfy them with a fake.
// ---------------------------------------------------------------------------

export interface AudioPlayerLike {
  play(): void;
  /** expo-audio returns a promise here; the click deliberately does not await it. */
  seekTo(seconds: number): unknown;
  /** Frees the native player. */
  remove(): void;
}

export interface AudioModuleLike {
  createAudioPlayer(source: unknown, options?: Record<string, unknown>): AudioPlayerLike;
  setAudioModeAsync?(mode: Record<string, unknown>): Promise<unknown>;
}

/**
 * Options every click player is created with.
 *
 * `updateInterval` defaults to 500ms in expo-audio, meaning each player posts a
 * playback-status event across the bridge twice a second. Four players is eight
 * events per second landing on the same JS thread that has to hit beat
 * deadlines — and nothing here ever reads playback status. Turning the interval
 * down to once a minute removes that traffic. It cannot suppress anything we
 * need, because the click is fire-and-forget.
 */
export const CLICK_PLAYER_OPTIONS: Record<string, unknown> = { updateInterval: 60_000 };

/** `require()`d asset handles for the two clicks. */
export interface ClickSources {
  tick: unknown;
  accent: unknown;
}

/**
 * The shipped click assets. Kept beside the adapter so the call site names one
 * thing, and so a re-tune (scripts/generate-click-assets.py) needs no code edit.
 *
 * A function rather than a module-level constant on purpose: `require` of an
 * asset is resolved by Metro at call time, so importing this module — which the
 * unit tests do — must not drag a .wav through whatever bundler is running.
 */
export function loadClickSources(): ClickSources {
  return {
    tick: require('../../assets/audio/tick.wav'),
    accent: require('../../assets/audio/accent.wav'),
  };
}

export interface AudioClickEmitterOptions {
  /**
   * Players allocated per sound, cycled round-robin.
   *
   * One player per sound is the obvious choice and the wrong one: `seekTo(0)`
   * is asynchronous, so re-triggering a single player means every click races
   * the rewind of the click before it. Two players means a beat always lands on
   * a player that has been idle for a full interval. More than two buys nothing
   * — a 35ms sound cannot still be playing two beats later, even at 300 BPM.
   */
  voicesPerSound?: number;
  /**
   * Called if the native layer throws while playing a beat. A failed click must
   * not take down the session, so the error is swallowed after this runs.
   */
  onError?: (error: unknown, beat: MetronomeBeat) => void;
}

export const DEFAULT_VOICES_PER_SOUND = 2;

/** Round-robin over a fixed set of players. */
function createVoicePool(
  audio: AudioModuleLike,
  source: unknown,
  size: number,
): { next(): AudioPlayerLike; removeAll(): void } {
  const players = Array.from({ length: Math.max(1, size) }, () =>
    audio.createAudioPlayer(source, CLICK_PLAYER_OPTIONS),
  );
  let cursor = 0;
  return {
    next() {
      const player = players[cursor];
      cursor = (cursor + 1) % players.length;
      return player;
    },
    removeAll() {
      for (const player of players) player.remove();
    },
  };
}

/**
 * Builds the audible emitter over an injected expo-audio module.
 *
 * Allocates every player up front: creating a native player costs file IO and
 * decode, which is not something to do on a beat deadline.
 */
export function createAudioClickEmitter(
  audio: AudioModuleLike,
  sources: ClickSources,
  options: AudioClickEmitterOptions = {},
): ClickEmitter {
  const { voicesPerSound = DEFAULT_VOICES_PER_SOUND, onError } = options;

  const tick = createVoicePool(audio, sources.tick, voicesPerSound);
  const accent = createVoicePool(audio, sources.accent, voicesPerSound);
  let disposed = false;

  return {
    emit(beat: MetronomeBeat) {
      if (disposed) return;
      // A beat that arrived closer to the NEXT beat than its own is worse than
      // no beat: it is heard as the pulse moving. Swallow it — the engine has
      // already advanced the grid, so the following click lands back in time.
      if (beat.late) return;

      const player = (beat.downbeat ? accent : tick).next();
      try {
        player.seekTo(0);
        player.play();
      } catch (error) {
        onError?.(error, beat);
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      tick.removeAll();
      accent.removeAll();
    },
  };
}

/**
 * Audio mode for a practice click. Call once before starting.
 *
 * `playsInSilentMode` is the load-bearing one: a phone propped on a music stand
 * is very often on the silent switch, and without this the metronome is
 * correct, running, and completely inaudible — which reads as a bug.
 *
 * `interruptionMode: 'mixWithOthers'` so the click coexists with a backing
 * track or the song the user is playing along to, rather than stopping it.
 */
export async function prepareClickAudioMode(audio: AudioModuleLike): Promise<void> {
  await audio.setAudioModeAsync?.({
    playsInSilentMode: true,
    shouldPlayInBackground: false,
    interruptionMode: 'mixWithOthers',
  });
}
