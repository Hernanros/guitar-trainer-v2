// mobile/src/metronome/types.ts
// Shared types for the metronome module — FLE-5 (Task 7).
//
// The module is deliberately split so that NOTHING here depends on an audio
// library. The click is delivered through ClickEmitter, a one-method interface.
// Today the only shipped emitter is silent (see emitters.ts); an expo-audio
// backed emitter drops in behind this same interface once the dependency is
// approved. Nothing else in the module changes when that happens.

/** A single metronome beat, handed to the ClickEmitter as it fires. */
export interface MetronomeBeat {
  /**
   * Monotonically increasing beat counter since the current anchor. Resets to 0
   * on start() and on setBpm() while running (a tempo change restarts the bar).
   */
  beatIndex: number;
  /** Position within the bar — 0 is the downbeat. Always 0 when beatsPerBar <= 0. */
  barBeat: number;
  /** True on the downbeat, so an emitter can play an accented click. */
  downbeat: boolean;
  /** Tempo this beat was scheduled at. */
  bpm: number;
  /**
   * The beat's exact position on the grid: `anchor + beatIndex * interval`.
   * This is computed from the anchor every time, never accumulated — that is
   * what makes the grid drift-free. See scheduler.ts.
   */
  scheduledAt: number;
  /** Wall-clock time the beat actually fired. */
  actualAt: number;
  /**
   * `actualAt - scheduledAt`. Positive means the JS thread delivered the timer
   * late. This is per-beat jitter, which a JS-timer metronome cannot eliminate
   * — only refuse to accumulate. Surfaced so device testing can measure it.
   */
  driftMs: number;
  /**
   * True when the beat arrived more than half an interval late — i.e. closer to
   * the next beat than its own. An audio emitter should swallow the click
   * rather than play it audibly out of place; the grid still advances.
   */
  late: boolean;
}

/**
 * The sound (or haptic, or visual) backend. Kept to one method on purpose: it
 * is the entire surface a new audio dependency would need to satisfy.
 */
export interface ClickEmitter {
  emit(beat: MetronomeBeat): void;
  /** Optional teardown — releases audio players, etc. */
  dispose?(): void;
}

/** Opaque timer handle. `number` under RN's setTimeout, a Timeout object under Node. */
export type TimerHandle = unknown;

/**
 * Everything the engine touches from the outside world, injected so tests can
 * drive it with a virtual clock instead of real time. This is what makes a
 * 45-minute session-length drift run testable in milliseconds.
 */
export interface MetronomeDeps {
  now: () => number;
  setTimer: (callback: () => void, delayMs: number) => TimerHandle;
  clearTimer: (handle: TimerHandle) => void;
  emitter: ClickEmitter;
}

/** Engine configuration. */
export interface MetronomeOptions {
  bpm: number;
  /** Beats per bar for the downbeat accent. 4 is the default; 0 disables accents. */
  beatsPerBar?: number;
}

/** Snapshot of engine state, for UI. */
export interface MetronomeState {
  running: boolean;
  bpm: number;
  beatsPerBar: number;
  /** Total beats emitted since the engine was constructed — diagnostic only. */
  beatsEmitted: number;
}
