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

import type { ClickEmitter, MetronomeBeat, TimerHandle } from './types';

// ---------------------------------------------------------------------------
// The slice of expo-audio this adapter needs. Structural types, not imports —
// the real module satisfies these; the tests satisfy them with a fake.
// ---------------------------------------------------------------------------

export interface AudioPlayerLike {
  play(): void;
  /**
   * expo-audio returns a promise here that resolves once the native seek
   * completes — never on the beat path, only from the rewind timer below.
   * The rewind deliberately does not await it.
   */
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

/**
 * How long after `play()` a voice is rewound to position 0.
 *
 * The click assets are 35ms (verified with `wave`); 60ms gives the native
 * seek room to land before anything downstream could mistake the voice for
 * still-idle-at-zero.
 */
export const CLICK_REWIND_DELAY_MS = 60;

export interface AudioClickEmitterOptions {
  /**
   * Players allocated per sound, cycled round-robin.
   *
   * A voice is rewound to 0 on a timer AFTER it plays, not on the beat path —
   * so a voice must not be reused before that rewind has landed. Two voices
   * cut it close for a bar's run of tick beats at MAX_BPM (200ms apart); three
   * keeps a full extra beat of margin at every tempo this engine allows.
   */
  voicesPerSound?: number;
  /**
   * Called if the native layer throws while playing a beat. A failed click must
   * not take down the session, so the error is swallowed after this runs.
   */
  onError?: (error: unknown, beat: MetronomeBeat) => void;
  /** Schedules the rewind. Defaults to the real timer; injected in tests. */
  setTimer?: (callback: () => void, delayMs: number) => TimerHandle;
  /** Cancels a scheduled rewind. */
  clearTimer?: (handle: TimerHandle) => void;
}

export const DEFAULT_VOICES_PER_SOUND = 3;

/**
 * Round-robin over a fixed set of players.
 *
 * Also owns each player's post-click rewind: `armRewind` schedules the
 * `seekTo(0)` that parks a voice back at the head, well ahead of its next
 * turn, and `removeAll` cancels whatever is still pending so dispose() never
 * lets a rewind land on a player that has already been freed.
 */
function createVoicePool(
  audio: AudioModuleLike,
  source: unknown,
  size: number,
  setTimer: (callback: () => void, delayMs: number) => TimerHandle,
  clearTimer: (handle: TimerHandle) => void,
): { next(): AudioPlayerLike; armRewind(player: AudioPlayerLike): void; removeAll(): void } {
  const players = Array.from({ length: Math.max(1, size) }, () =>
    audio.createAudioPlayer(source, CLICK_PLAYER_OPTIONS),
  );
  const pendingRewinds = new Map<AudioPlayerLike, TimerHandle>();
  let cursor = 0;
  return {
    next() {
      const player = players[cursor];
      cursor = (cursor + 1) % players.length;
      return player;
    },
    armRewind(player) {
      const pending = pendingRewinds.get(player);
      if (pending !== undefined) clearTimer(pending);
      const handle = setTimer(() => {
        pendingRewinds.delete(player);
        player.seekTo(0);
      }, CLICK_REWIND_DELAY_MS);
      pendingRewinds.set(player, handle);
    },
    removeAll() {
      for (const handle of pendingRewinds.values()) clearTimer(handle);
      pendingRewinds.clear();
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
  const {
    voicesPerSound = DEFAULT_VOICES_PER_SOUND,
    onError,
    setTimer = (callback, delayMs) => setTimeout(callback, delayMs),
    clearTimer = (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  } = options;

  const tick = createVoicePool(audio, sources.tick, voicesPerSound, setTimer, clearTimer);
  const accent = createVoicePool(audio, sources.accent, voicesPerSound, setTimer, clearTimer);
  let disposed = false;

  return {
    emit(beat: MetronomeBeat) {
      if (disposed) return;
      // A beat that arrived closer to the NEXT beat than its own is worse than
      // no beat: it is heard as the pulse moving. Swallow it — the engine has
      // already advanced the grid, so the following click lands back in time.
      if (beat.late) return;

      const pool = beat.downbeat ? accent : tick;
      const player = pool.next();
      try {
        // No seekTo here — `play()` is synchronous JSI and `seekTo` is not, so
        // calling both back to back races a voice already parked at EOF from
        // its last turn. Every voice starts at 0 and is rewound (below) well
        // ahead of its next turn, so `play()` only ever fires on a voice that
        // is already sitting at 0.
        player.play();
        pool.armRewind(player);
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
