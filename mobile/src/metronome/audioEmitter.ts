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
//   player.seekTo(seconds, toleranceMillisBefore?, toleranceMillisAfter?): Promise<void>
//   player.currentTime: number
//   player.remove(): void
//   setAudioModeAsync(mode: Partial<AudioMode>): Promise<void>
//
// FLE-77 (the second pass): `seekTo`'s tolerances default to CMTime
// .positiveInfinity on iOS (node_modules/expo-audio/ios/AudioPlayer.swift),
// so a seek to 0 with no tolerance is a request every playhead position
// already satisfies — AVFoundation may resolve the promise without moving
// anything. The pool below always passes zero tolerance AND reads
// `currentTime` back after the promise resolves, because the resolution
// alone was never proof.
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
   * The rewind deliberately does not await it on the beat path, but DOES
   * await it (see armRewind) before trusting the voice is actually at 0.
   *
   * Called with explicit zero tolerances (`seekTo(0, 0, 0)`), not just
   * `seekTo(0)` — see the file header for why an un-toleranced seek can
   * resolve without moving the playhead at all.
   */
  seekTo(seconds: number, toleranceMillisBefore?: number, toleranceMillisAfter?: number): unknown;
  /**
   * Playhead position in seconds. Read after `seekTo` resolves — never
   * trusted from the resolution alone — to confirm the rewind actually
   * landed the voice at 0.
   */
  currentTime: number;
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
 * How long after `play()` a voice's rewind is first attempted.
 *
 * The click assets are 35ms (verified with `wave`); 60ms gives the native
 * `play()` room to actually start before anything asks the player to seek
 * out from under it. It is not proof of anything on its own — see
 * REWIND_POSITION_EPSILON_SECONDS below for what actually gates readiness.
 */
export const CLICK_REWIND_DELAY_MS = 60;

/**
 * How close to 0 `currentTime` must read, after `seekTo(0, 0, 0)` resolves,
 * to count the voice as genuinely rewound.
 *
 * A promise resolving is not proof the playhead moved (FLE-77's root cause —
 * see the file header): a zero-tolerance seek still has to be verified, not
 * trusted. This tolerance is slack for float rounding through the native
 * bridge, not for seek imprecision — an exact seek should read back at
 * exactly 0.
 */
export const REWIND_POSITION_EPSILON_SECONDS = 0.005;

export interface AudioClickEmitterOptions {
  /**
   * Players allocated per sound, cycled round-robin among whichever are
   * confirmed rewound to 0 (see createVoicePool). `next()` never forces a
   * voice whose rewind is still in flight (FLE-77 — that forced reuse was
   * the only path that could land a stale seek inside a live click), so more
   * voices is what keeps a fast run from running out of confirmed-ready
   * ones. 8 is sized so that never happens in practice: at MAX_BPM's
   * tightest gap (200ms) against a rewind that normally resolves in single-
   * digit ms, exhausting 8 needs 8 concurrent in-flight seeks, which needs a
   * JS-thread stall already bad enough to be dropping beats at the scheduler
   * level. Preallocated once, so the extra players cost nothing per beat.
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
  /** Wall clock, for seekTo latency instrumentation. Defaults to Date.now; injected in tests. */
  now?: () => number;
}

export const DEFAULT_VOICES_PER_SOUND = 8;

/** Running counters for one voice pool — aggregated into getDiagnostics(). */
interface VoicePoolDiagnostics {
  /** Beats this pool had no confirmed-ready voice for — skipped, not doubled. */
  starvedBeats: number;
  /** seekTo(0, 0, 0) resolved but currentTime was not actually ~0 — a no-op seek. */
  nonZeroRewinds: number;
  /** seekTo(0, 0, 0) rejected outright. */
  rewindErrors: number;
  /** Resolve latency of every seekTo call that completed, successfully or not. */
  seekLatenciesMs: number[];
}

function emptyDiagnostics(): VoicePoolDiagnostics {
  return { starvedBeats: 0, nonZeroRewinds: 0, rewindErrors: 0, seekLatenciesMs: [] };
}

/**
 * Round-robin over a fixed set of players, skipping any voice not yet
 * confirmed rewound.
 *
 * FLE-57 assumed a voice was at 0 once `CLICK_REWIND_DELAY_MS` elapsed —
 * a guess about the timer plus the native round trip. FLE-77's first pass
 * (a5e0852) tied readiness to the `seekTo(0)` promise resolving instead,
 * which was a real improvement but still not sufficient: on the installed
 * expo-audio, `seekTo` defaults both iOS tolerances to `CMTime
 * .positiveInfinity`, so a seek to 0 is a request every position already
 * satisfies and AVFoundation is free to resolve the completion handler
 * without moving anything. A voice's "rewind" could no-op and still report
 * done — reproducing both symptoms, now CLUSTERED instead of scattered,
 * because a voice that no-ops once tends to keep no-opping while the native
 * pipeline stays in that state.
 *
 * This pass makes the seek itself exact (`seekTo(0, 0, 0)`, zero tolerance
 * both sides) and then verifies rather than trusts: only a `currentTime`
 * that actually reads ~0 after the promise resolves clears `busy`. A no-op
 * or a rejected seekTo re-arms the rewind instead of marking the voice
 * ready — self-healing the same way FLE-77's generation counter already was,
 * just gated on the right signal this time.
 *
 * `next()` no longer force-reuses a busy voice when the whole pool is
 * unconfirmed. That fallback was the only path that could land a stale seek
 * inside a live click — the doubled attack. Removing it trades a possible
 * doubled beat for a possible silent one (see DEFAULT_VOICES_PER_SOUND for
 * why that should be rare in practice), and the caller counts it rather than
 * papering over it.
 */
function createVoicePool(
  audio: AudioModuleLike,
  source: unknown,
  size: number,
  setTimer: (callback: () => void, delayMs: number) => TimerHandle,
  clearTimer: (handle: TimerHandle) => void,
  now: () => number,
): {
  next(): AudioPlayerLike | undefined;
  armRewind(player: AudioPlayerLike): void;
  removeAll(): void;
  diagnostics: VoicePoolDiagnostics;
} {
  const players = Array.from({ length: Math.max(1, size) }, () =>
    audio.createAudioPlayer(source, CLICK_PLAYER_OPTIONS),
  );
  const pendingTimers = new Map<AudioPlayerLike, TimerHandle>();
  /** Voices played but not yet confirmed rewound to 0. */
  const busy = new Set<AudioPlayerLike>();
  /**
   * Bumped every armRewind() call for a player. Guards against a STALE
   * resolution: a superseded rewind cycle (retried after a no-op, or
   * re-armed after a rejection) must not have its late resolution mark the
   * voice ready out from under the cycle that replaced it.
   */
  const generation = new Map<AudioPlayerLike, number>();
  const diagnostics = emptyDiagnostics();
  let cursor = 0;

  function armRewind(player: AudioPlayerLike): void {
    const pending = pendingTimers.get(player);
    if (pending !== undefined) clearTimer(pending);
    busy.add(player);
    const myGeneration = (generation.get(player) ?? 0) + 1;
    generation.set(player, myGeneration);

    const handle = setTimer(() => {
      pendingTimers.delete(player);
      const startedAt = now();

      Promise.resolve(player.seekTo(0, 0, 0))
        .then(() => {
          if (generation.get(player) !== myGeneration) return;
          diagnostics.seekLatenciesMs.push(now() - startedAt);

          const position = player.currentTime ?? 0;
          if (Math.abs(position) > REWIND_POSITION_EPSILON_SECONDS) {
            // The zero-tolerance seek still didn't move the playhead — do not
            // trust the resolution, try again instead of marking it ready.
            diagnostics.nonZeroRewinds += 1;
            armRewind(player);
            return;
          }
          busy.delete(player);
        })
        .catch(() => {
          if (generation.get(player) !== myGeneration) return;
          // A rejected seekTo must not strand the voice as busy forever —
          // that is a second, silent path into a starved pool.
          diagnostics.rewindErrors += 1;
          armRewind(player);
        });
    }, CLICK_REWIND_DELAY_MS);
    pendingTimers.set(player, handle);
  }

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
      // Every voice still has a seek in flight. No forced reuse (see the
      // function comment) — the caller must treat this as a beat with
      // nothing safe to play.
      diagnostics.starvedBeats += 1;
      return undefined;
    },
    armRewind,
    removeAll() {
      for (const handle of pendingTimers.values()) clearTimer(handle);
      pendingTimers.clear();
      busy.clear();
      for (const player of players) player.remove();
    },
    diagnostics,
  };
}

function percentile(sortedAscending: number[], p: number): number {
  if (sortedAscending.length === 0) return 0;
  const index = Math.min(sortedAscending.length - 1, Math.floor(p * sortedAscending.length));
  return sortedAscending[index];
}

/**
 * Merges both pools' counters into the flat shape getDiagnostics() exposes.
 * Percentiles are computed here, at read time, rather than per beat — the
 * same "read, not subscribed" discipline as driftStats.ts.
 */
function combineDiagnostics(
  a: VoicePoolDiagnostics,
  b: VoicePoolDiagnostics,
): Record<string, number> {
  const latencies = [...a.seekLatenciesMs, ...b.seekLatenciesMs].sort((x, y) => x - y);
  return {
    starvedBeats: a.starvedBeats + b.starvedBeats,
    nonZeroRewinds: a.nonZeroRewinds + b.nonZeroRewinds,
    rewindErrors: a.rewindErrors + b.rewindErrors,
    seekCount: latencies.length,
    seekLatencyP50Ms: percentile(latencies, 0.5),
    seekLatencyP95Ms: percentile(latencies, 0.95),
  };
}

/**
 * Compact diagnostics line for the timing panel — the direct confirmation or
 * refutation of the FLE-77 root cause. `no-op` counting above 0 means a seek
 * is still resolving without moving the playhead even at zero tolerance;
 * `starved` counting above a handful means DEFAULT_VOICES_PER_SOUND needs to
 * go higher, not that the verify-and-retry logic is wrong.
 */
export function formatVoicePoolDiagnostics(diagnostics: Record<string, number>): string {
  return [
    `${diagnostics.seekCount} seeks`,
    `p50 ${Math.round(diagnostics.seekLatencyP50Ms)}ms / p95 ${Math.round(diagnostics.seekLatencyP95Ms)}ms`,
    `${diagnostics.nonZeroRewinds} no-op`,
    `${diagnostics.rewindErrors} rejected`,
    `${diagnostics.starvedBeats} starved`,
  ].join(' · ');
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
    now = () => Date.now(),
  } = options;

  const tick = createVoicePool(audio, sources.tick, voicesPerSound, setTimer, clearTimer, now);
  const accent = createVoicePool(audio, sources.accent, voicesPerSound, setTimer, clearTimer, now);
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
      // Every voice still has an unconfirmed rewind in flight (FLE-77:
      // diagnostics.starvedBeats already counted this) — nothing is safe to
      // play without risking a stale seek landing inside this click.
      if (!player) return;
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
    getDiagnostics(): Record<string, number> {
      return combineDiagnostics(tick.diagnostics, accent.diagnostics);
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
