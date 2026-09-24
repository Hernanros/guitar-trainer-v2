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
   * Players allocated per sound, cycled round-robin among whichever are
   * confirmed rewound to 0 (see createVoicePool). More voices give that
   * search more idle candidates to find before it has to fall back to forcing
   * a still-busy one, but the fallback is what keeps a beat from going
   * silent, not this number — three is enough headroom at MAX_BPM's tightest
   * gap (200ms) without allocating players nothing will use.
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
 * Round-robin over a fixed set of players, skipping any voice not yet
 * confirmed rewound.
 *
 * FLE-57 assumed a voice was safely at position 0 once `CLICK_REWIND_DELAY_MS`
 * had elapsed — a guess about how long the timer delay plus the native
 * `seekTo` round trip would take. On device that guess was sometimes wrong in
 * both directions: a voice could still be mid-rewind (or its click still
 * audibly tailing off) when its next turn came up, which produced FLE-77's
 * doubled attack (a `play()` landing on a voice `seekTo` was still moving) and
 * silent beats (`play()` landing on a voice still parked at EOF).
 *
 * The fix ties "ready" to the actual `seekTo(0)` PROMISE resolving, not to the
 * timer that requested it, and `next()` prefers a confirmed-ready voice over
 * blindly following the round-robin cursor into one that is not. This is
 * self-healing under exactly the jitter the JS thread cannot avoid (see
 * scheduler.ts's own comment on per-beat jitter): a slow rewind just makes
 * `next()` reach for a different idle voice instead of forcing the slow one.
 * Only when every voice is still unconfirmed does it fall back to the cursor's
 * voice anyway — a possible collision beats a dropped beat, and that only
 * happens if the whole pool is starved at once, which needs the same kind of
 * JS-thread stall that already costs the scheduler a beat.
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
  const pendingTimers = new Map<AudioPlayerLike, TimerHandle>();
  /** Voices played but not yet confirmed rewound to 0 by their own seekTo's resolution. */
  const busy = new Set<AudioPlayerLike>();
  /**
   * Bumped every armRewind() call for a player. Guards against a STALE
   * resolution: if a busy voice is force-reused (the all-busy fallback) before
   * its previous seekTo resolves, that earlier promise must not be allowed to
   * mark the voice ready out from under the newer play/rewind cycle.
   */
  const generation = new Map<AudioPlayerLike, number>();
  let cursor = 0;
  return {
    next() {
      for (let i = 0; i < players.length; i += 1) {
        const index = (cursor + i) % players.length;
        const player = players[index];
        if (!busy.has(player)) {
          cursor = (index + 1) % players.length;
          return player;
        }
      }
      // Every voice is still unconfirmed — fall back to the cursor's own turn.
      const player = players[cursor];
      cursor = (cursor + 1) % players.length;
      return player;
    },
    armRewind(player) {
      const pending = pendingTimers.get(player);
      if (pending !== undefined) clearTimer(pending);
      busy.add(player);
      const myGeneration = (generation.get(player) ?? 0) + 1;
      generation.set(player, myGeneration);
      const handle = setTimer(() => {
        pendingTimers.delete(player);
        Promise.resolve(player.seekTo(0)).then(() => {
          // Only the rewind cycle that is still current may clear busy — a
          // resolution from a superseded cycle would otherwise mark a voice
          // ready that a later armRewind() has already re-armed.
          if (generation.get(player) === myGeneration) busy.delete(player);
        });
      }, CLICK_REWIND_DELAY_MS);
      pendingTimers.set(player, handle);
    },
    removeAll() {
      for (const handle of pendingTimers.values()) clearTimer(handle);
      pendingTimers.clear();
      busy.clear();
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
