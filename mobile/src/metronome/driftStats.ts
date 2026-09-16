// mobile/src/metronome/driftStats.ts
// Timing telemetry for the metronome — FLE-5 (Task 7), D9.
//
// ---------------------------------------------------------------------------
// Why this file exists: the Done-when is measured on hardware, by a human
// ---------------------------------------------------------------------------
//
// `MetronomeBeat.driftMs` has been computed since the engine's first commit,
// with a comment saying it is "surfaced so device testing can measure it". It
// was not. Nothing consumed it, so the 45-minute hold-tempo walk in
// 04.1-05-DEVICE-VERIFICATION.md item 8 had to be judged by ear against a
// second metronome app — which cannot separate the three ways this feature
// fails, and leaves nothing behind to debug if it does:
//
//   1. The grid slides       → accumulated error. What absolute deadlines fix.
//   2. Beats wobble          → per-beat jitter. Unavoidable on a JS timer; the
//                              open question is how MUCH on real hardware.
//   3. The click stops dead  → the OS suspended the app (auto-lock, see D8) and
//                              timers stopped firing.
//
// All three sound like "it drifted" to a listener. They have completely
// different causes and fixes. This accumulator tells them apart, because the
// engine already emits the evidence for each: `scheduledAt`/`actualAt` for 1,
// `driftMs` for 2, and a jump in `beatIndex` for 3 — the stall resync in
// scheduler.ts skips overdue beats, so a gap in the indices is a precise
// count of how many beats the device swallowed.
//
// Pure functions over an immutable snapshot, matching scheduler.ts: no timers,
// no React, no state. The hook owns the accumulation; this file owns the math.

import type { MetronomeBeat } from './types';
import { intervalMsForBpm } from './scheduler';

/**
 * A running measurement of one metronome run. Reset on start(), retained
 * after stop() — the tester reads the numbers off a stopped screen at the end
 * of the walk, so they must survive the stop.
 */
export interface DriftStats {
  /** Beats actually emitted. */
  beatsEmitted: number;
  /**
   * Beats the grid contained but the device never fired, counted from gaps in
   * `beatIndex`. Non-zero means the JS thread stalled or the app was
   * suspended — an audible gap, not a wobble. This is the auto-lock detector.
   */
  skippedBeats: number;
  /**
   * Beats that arrived more than half an interval late, so the audio emitter
   * swallowed the click rather than play it out of position.
   */
  lateBeats: number;
  /** Worst single-beat |error| against its grid deadline. The jitter headline. */
  maxAbsDriftMs: number;
  /** Signed sum of per-beat error, for the mean. Positive means habitually late. */
  totalDriftMs: number;
  /** Wall-clock time of the first and last beats of the run. */
  firstBeatAt: number | null;
  lastBeatAt: number | null;
  /**
   * Tempo re-anchors (ladder pushes). Each one restarts `beatIndex`, which is
   * why it is tracked: without it, a push would read as a giant skip.
   */
  tempoChanges: number;
  /**
   * Accumulated error — where the LAST beat landed relative to where the grid
   * says it should, measured across the whole run rather than per beat. This
   * is the number that distinguishes failure mode 1 from failure mode 2: it
   * stays bounded by one beat's jitter no matter how long the run, whereas a
   * naive `setInterval` grows it without limit.
   */
  gridErrorMs: number;
  /** Highest beat index seen in the current anchor segment. -1 before the first beat. */
  lastBeatIndex: number;
}

export function emptyDriftStats(): DriftStats {
  return {
    beatsEmitted: 0,
    skippedBeats: 0,
    lateBeats: 0,
    maxAbsDriftMs: 0,
    totalDriftMs: 0,
    firstBeatAt: null,
    lastBeatAt: null,
    tempoChanges: 0,
    gridErrorMs: 0,
    lastBeatIndex: -1,
  };
}

/**
 * Fold one beat into the stats. Returns a new snapshot; never mutates.
 *
 * The one subtlety is the `beatIndex` reset. `MetronomeEngine.setBpm` re-anchors
 * the grid and restarts the bar, so beat indices go (…, 41, 42, 0, 1, 2). A
 * naive gap check would read that as a skip of minus forty-two, or with
 * `Math.max` as a skip of nothing while silently mismeasuring the rest of the
 * run. So a non-increasing index is treated as a new segment: it closes the
 * old one, counts a tempo change, and resets the gap baseline.
 */
export function observeBeat(stats: DriftStats, beat: MetronomeBeat): DriftStats {
  const reAnchored = beat.beatIndex <= stats.lastBeatIndex;
  const gap = reAnchored ? 0 : beat.beatIndex - stats.lastBeatIndex - 1;

  return {
    beatsEmitted: stats.beatsEmitted + 1,
    skippedBeats: stats.skippedBeats + Math.max(0, gap),
    lateBeats: stats.lateBeats + (beat.late ? 1 : 0),
    maxAbsDriftMs: Math.max(stats.maxAbsDriftMs, Math.abs(beat.driftMs)),
    totalDriftMs: stats.totalDriftMs + beat.driftMs,
    firstBeatAt: stats.firstBeatAt ?? beat.actualAt,
    lastBeatAt: beat.actualAt,
    tempoChanges: stats.tempoChanges + (reAnchored ? 1 : 0),
    // Not an accumulation of driftMs — that would sum the jitter and report a
    // drifting metronome that is not drifting. It is the current beat's own
    // distance from the grid, which is exactly what "has the grid slid?" means.
    gridErrorMs: beat.driftMs,
    lastBeatIndex: beat.beatIndex,
  };
}

/** Mean per-beat error in ms. 0 for an empty run rather than NaN. */
export function meanDriftMs(stats: DriftStats): number {
  if (stats.beatsEmitted === 0) return 0;
  return stats.totalDriftMs / stats.beatsEmitted;
}

/** Length of the run so far, from first beat to last. */
export function runDurationMs(stats: DriftStats): number {
  if (stats.firstBeatAt === null || stats.lastBeatAt === null) return 0;
  return stats.lastBeatAt - stats.firstBeatAt;
}

/**
 * Worst-case jitter as a share of the beat interval — the number that decides
 * whether jitter is *audible*. 8ms is inaudible at 60 BPM and obvious at 280,
 * so a raw millisecond figure cannot be judged without the tempo beside it.
 */
export function maxDriftAsBeatFraction(stats: DriftStats, bpm: number): number {
  return stats.maxAbsDriftMs / intervalMsForBpm(bpm);
}

/** `m:ss` for the run clock. Session-length runs stay under an hour. */
export function formatRunClock(durationMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(durationMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

/**
 * The line the device tester reads off the screen and copies into item 8's
 * blank. Compact because it renders inside a drill card, and ordered by what
 * answers the Done-when first: how long it ran, then whether the grid held,
 * then how rough each beat was.
 *
 * Fletcher voice: measurements, no reassurance.
 */
export function formatDriftSummary(stats: DriftStats): string {
  if (stats.beatsEmitted === 0) return 'No beats yet';

  return [
    formatRunClock(runDurationMs(stats)),
    `${stats.beatsEmitted} beat${stats.beatsEmitted === 1 ? '' : 's'}`,
    `grid ${formatSignedMs(stats.gridErrorMs)}`,
    `jitter max ${Math.round(stats.maxAbsDriftMs)}ms / avg ${formatSignedMs(meanDriftMs(stats))}`,
    `${stats.skippedBeats} skipped`,
    `${stats.lateBeats} dropped`,
  ].join(' · ');
}

/** "+4ms" / "-1ms" — the sign carries the diagnosis, so it is never dropped. */
export function formatSignedMs(ms: number): string {
  const rounded = Math.round(ms);
  return `${rounded >= 0 ? '+' : ''}${rounded}ms`;
}
