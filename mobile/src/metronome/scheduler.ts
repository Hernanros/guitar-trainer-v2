// mobile/src/metronome/scheduler.ts
// Pure timing math for the metronome — FLE-5 (Task 7).
//
// No timers, no state, no React, no I/O. Every function here is a pure
// function of its arguments, which is what lets the drift behaviour be proven
// by unit test rather than argued about.
//
// ---------------------------------------------------------------------------
// Why this file exists: setInterval accumulates, absolute deadlines do not
// ---------------------------------------------------------------------------
//
// The naive metronome is `setInterval(60000 / bpm)`. It drifts, and it drifts
// in one direction. A timer callback that arrives 4ms late does not just make
// that beat 4ms late — the next interval is measured from the late callback, so
// the error is carried forward and added to by the next one. Over a
// session-length run the accumulated error is the SUM of every individual
// delay, which is unbounded in the length of the session.
//
// The fix is to never measure from the last beat. Every beat's deadline is
// computed from a fixed anchor as `anchor + beatIndex * interval`, and each
// timer is armed for `deadline - now`. A callback that arrives 4ms late now
// causes the NEXT timeout to be armed 4ms shorter, so the error is corrected
// instead of compounded. Total drift is bounded by one beat's jitter no matter
// how long the run — that is the property the tests assert.
//
// What this does NOT fix is per-beat jitter. The JS thread schedules the
// callback, so individual beats still land a few ms off under load. Removing
// that needs a native audio clock, which no in-tree package provides. The grid
// is exact; each beat's arrival is approximate, and MetronomeBeat.driftMs
// reports by how much.

/** Metronome tempo range. Below 30 the click stops reading as a pulse; above 300 the grid outruns the JS thread. */
export const MIN_BPM = 30;
export const MAX_BPM = 300;

/** Default bar length for the downbeat accent. */
export const DEFAULT_BEATS_PER_BAR = 4;

/**
 * Clamp a tempo into the supported range. Drill tempo ladders are authored by
 * Sonnet, so this guards against a model-authored `target_bpm` of 0 or 4000
 * turning into a division by zero or a timer storm.
 *
 * NaN carries no magnitude to clamp, so it falls back to 60 — a silently sane
 * metronome beats a crashed drill screen. ±Infinity does carry a direction, so
 * it clamps to the corresponding bound like any other out-of-range number.
 */
export function clampBpm(bpm: number): number {
  if (Number.isNaN(bpm)) return 60;
  return Math.min(MAX_BPM, Math.max(MIN_BPM, bpm));
}

/** Milliseconds between beats at a given tempo. Input is clamped first, so the result is always > 0. */
export function intervalMsForBpm(bpm: number): number {
  return 60_000 / clampBpm(bpm);
}

/**
 * The exact grid position of a beat: `anchor + beatIndex * interval`.
 *
 * Note the multiplication. Computing the Nth deadline never reads the (N-1)th,
 * so no rounding error or delivery delay can propagate between beats.
 */
export function beatDeadline(anchorMs: number, beatIndex: number, intervalMs: number): number {
  return anchorMs + beatIndex * intervalMs;
}

/** True on the downbeat. `beatsPerBar <= 0` disables accents entirely. */
export function isDownbeat(beatIndex: number, beatsPerBar: number): boolean {
  if (beatsPerBar <= 0) return false;
  return beatIndex % beatsPerBar === 0;
}

/** Position within the bar, 0-indexed. Always 0 when accents are disabled. */
export function barBeatFor(beatIndex: number, beatsPerBar: number): number {
  if (beatsPerBar <= 0) return 0;
  return beatIndex % beatsPerBar;
}

/** A scheduled beat: which beat, and the exact grid time it belongs at. */
export interface ScheduledBeat {
  beatIndex: number;
  scheduledAt: number;
}

/**
 * Pick the next beat to arm a timer for.
 *
 * The normal case is simply `lastBeatIndex + 1`. The interesting case is a
 * stalled JS thread: if a GC pause or a slow render swallows 400ms at 120 BPM,
 * four beat deadlines have already passed by the time we get to run.
 *
 * We do NOT fire those four. Emitting every missed beat back-to-back produces a
 * burst of clicks, which is musically worse than a gap — the player hears a
 * stutter and loses the pulse, where a dropped beat they can play through.
 * Instead we skip to the next deadline strictly in the future, staying on the
 * original grid so `beatIndex` parity is preserved and bar accents keep landing
 * on the downbeat.
 *
 * @param anchorMs      Grid origin — the time of beat 0.
 * @param intervalMs    Milliseconds per beat. Must be > 0.
 * @param lastBeatIndex Index of the last beat emitted; -1 before the first.
 * @param nowMs         Current time.
 */
export function nextBeatFrom(
  anchorMs: number,
  intervalMs: number,
  lastBeatIndex: number,
  nowMs: number,
): ScheduledBeat {
  const candidate = lastBeatIndex + 1;
  const candidateDeadline = beatDeadline(anchorMs, candidate, intervalMs);

  // Common path: the next beat on the grid is still ahead of us.
  if (candidateDeadline > nowMs) {
    return { beatIndex: candidate, scheduledAt: candidateDeadline };
  }

  // Overdue — we stalled. Resync to the next future beat on the SAME grid
  // (hence deriving the index from the anchor, not from a running counter).
  const beatsElapsed = Math.floor((nowMs - anchorMs) / intervalMs);
  const resyncIndex = Math.max(beatsElapsed + 1, lastBeatIndex + 1);
  return { beatIndex: resyncIndex, scheduledAt: beatDeadline(anchorMs, resyncIndex, intervalMs) };
}

/**
 * True when a beat arrived closer to the next beat than to its own slot.
 * An audio emitter should swallow the click rather than play it audibly late.
 */
export function isLate(driftMs: number, intervalMs: number): boolean {
  return driftMs > intervalMs / 2;
}
