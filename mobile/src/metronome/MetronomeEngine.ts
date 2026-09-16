// mobile/src/metronome/MetronomeEngine.ts
// The metronome clock — FLE-5 (Task 7).
//
// Imperative, framework-free, and driven entirely through injected deps (`now`,
// `setTimer`, `clearTimer`, `emitter`). Nothing here imports React or an audio
// library. That injection is what lets a 45-minute session-length run be
// simulated against a virtual clock in a unit test, jitter and all.
//
// The timing argument lives in scheduler.ts. In short: every beat's deadline is
// `anchor + beatIndex * interval`, recomputed from the anchor each time, so a
// late timer shortens the next timeout instead of pushing it later. Error is
// corrected, never accumulated.

import {
  barBeatFor,
  clampBpm,
  DEFAULT_BEATS_PER_BAR,
  intervalMsForBpm,
  isDownbeat,
  isLate,
  nextBeatFrom,
  type ScheduledBeat,
} from './scheduler';
import { silentClickEmitter } from './emitters';
import type {
  MetronomeDeps,
  MetronomeOptions,
  MetronomeState,
  TimerHandle,
} from './types';

/** Real-world deps. Overridden wholesale in tests. */
export function defaultMetronomeDeps(): MetronomeDeps {
  return {
    now: () => Date.now(),
    setTimer: (callback, delayMs) => setTimeout(callback, delayMs),
    clearTimer: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
    emitter: silentClickEmitter,
  };
}

export class MetronomeEngine {
  private readonly deps: MetronomeDeps;

  private bpm: number;
  private beatsPerBar: number;

  private running = false;
  /** Grid origin — the wall-clock time of beat 0. Every deadline derives from this. */
  private anchorMs = 0;
  private lastBeatIndex = -1;
  private pending: ScheduledBeat | null = null;
  private timer: TimerHandle | null = null;
  private beatsEmitted = 0;

  constructor(options: MetronomeOptions, deps?: Partial<MetronomeDeps>) {
    this.deps = { ...defaultMetronomeDeps(), ...deps };
    this.bpm = clampBpm(options.bpm);
    this.beatsPerBar = options.beatsPerBar ?? DEFAULT_BEATS_PER_BAR;
  }

  getState(): MetronomeState {
    return {
      running: this.running,
      bpm: this.bpm,
      beatsPerBar: this.beatsPerBar,
      beatsEmitted: this.beatsEmitted,
    };
  }

  isRunning(): boolean {
    return this.running;
  }

  /**
   * Start clicking. Beat 0 fires immediately — pressing play should make a
   * sound now, not one interval from now — and becomes the grid anchor.
   */
  start(): void {
    if (this.running) return;
    this.running = true;

    const now = this.deps.now();
    this.anchorMs = now;
    this.lastBeatIndex = -1;
    this.emitBeat(0, now, now);
    this.scheduleNext();
  }

  /** Stop clicking and disarm the pending timer. Safe to call when already stopped. */
  stop(): void {
    if (!this.running) return;
    this.running = false;
    this.clearPendingTimer();
    this.pending = null;
  }

  toggle(): void {
    if (this.running) this.stop();
    else this.start();
  }

  /**
   * Change tempo, typically because a drill tempo ladder advanced.
   *
   * While running this re-anchors the grid: the new beat 0 lands one NEW
   * interval from now. Two reasons. First, keeping the old anchor would make
   * the next beat land at an arbitrary fraction of the new interval — an
   * audible stutter exactly at the moment the player is trying to feel a new
   * tempo. Second, re-anchoring restarts the bar, so the first click at the new
   * tempo is a downbeat, which is how a player expects a tempo change to
   * arrive.
   *
   * No click fires at the instant of the change — the grid simply resumes one
   * interval later.
   */
  setBpm(nextBpm: number): void {
    const bpm = clampBpm(nextBpm);
    if (bpm === this.bpm) return;
    this.bpm = bpm;

    if (!this.running) return;

    this.clearPendingTimer();
    // Anchor is the time of beat 0 — place it one new interval ahead so the
    // next emitted beat is both in the future and on the downbeat.
    this.anchorMs = this.deps.now() + intervalMsForBpm(bpm);
    this.lastBeatIndex = -1;
    this.scheduleNext();
  }

  setBeatsPerBar(beatsPerBar: number): void {
    this.beatsPerBar = beatsPerBar;
  }

  /** Stop and release the emitter. Call on unmount. */
  dispose(): void {
    this.stop();
    this.deps.emitter.dispose?.();
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  private scheduleNext(): void {
    if (!this.running) return;

    const intervalMs = intervalMsForBpm(this.bpm);
    const now = this.deps.now();
    const next = nextBeatFrom(this.anchorMs, intervalMs, this.lastBeatIndex, now);
    this.pending = next;

    // The self-correction: the delay is measured from the grid deadline to NOW,
    // so a callback that ran late produces a correspondingly shorter delay.
    const delayMs = Math.max(0, next.scheduledAt - now);
    this.timer = this.deps.setTimer(() => this.onTick(), delayMs);
  }

  private onTick(): void {
    // A stop() racing an in-flight timer lands here with running === false.
    if (!this.running || this.pending === null) return;

    const { beatIndex, scheduledAt } = this.pending;
    this.emitBeat(beatIndex, scheduledAt, this.deps.now());
    this.scheduleNext();
  }

  private emitBeat(beatIndex: number, scheduledAt: number, actualAt: number): void {
    this.lastBeatIndex = beatIndex;
    this.beatsEmitted += 1;

    const intervalMs = intervalMsForBpm(this.bpm);
    const driftMs = actualAt - scheduledAt;

    this.deps.emitter.emit({
      beatIndex,
      barBeat: barBeatFor(beatIndex, this.beatsPerBar),
      downbeat: isDownbeat(beatIndex, this.beatsPerBar),
      bpm: this.bpm,
      scheduledAt,
      actualAt,
      driftMs,
      late: isLate(driftMs, intervalMs),
    });
  }

  private clearPendingTimer(): void {
    if (this.timer !== null) {
      this.deps.clearTimer(this.timer);
      this.timer = null;
    }
  }
}
