/**
 * Drift telemetry tests — FLE-5 (Task 7), D9.
 *
 * The existing session-length test proves the engine's grid is exact against a
 * virtual clock. What it cannot prove is the thing the Done-when actually
 * gates on: that a human running the click for 45 minutes on real hardware can
 * tell what happened. That needs an instrument, and an instrument that lies is
 * worse than none — a readout reporting "0 skipped" through an app suspension,
 * or summing per-beat jitter into a scary-looking "grid" number, would send
 * whoever reads it after the wrong bug.
 *
 * So these tests drive the REAL engine (injected clock, jitter and stalls) and
 * assert the numbers a tester would read off the screen, rather than testing
 * the accumulator against hand-written beat literals.
 */

import { MetronomeEngine } from '../../src/metronome/MetronomeEngine';
import { createRecordingEmitter } from '../../src/metronome/emitters';
import { intervalMsForBpm } from '../../src/metronome/scheduler';
import {
  emptyDriftStats,
  formatDriftSummary,
  formatRunClock,
  formatSignedMs,
  maxDriftAsBeatFraction,
  meanDriftMs,
  observeBeat,
  runDurationMs,
  type DriftStats,
} from '../../src/metronome/driftStats';
import type { MetronomeBeat } from '../../src/metronome/types';

// ---------------------------------------------------------------------------
// Virtual clock — same shape as the harness in metronome.test.ts: one pending
// timer at a time (which is all the engine ever arms), advanced by hand.
// ---------------------------------------------------------------------------

interface Harness {
  engine: MetronomeEngine;
  /** Fold every beat the engine has emitted so far into a stats snapshot. */
  stats: () => DriftStats;
  /** Run the pending timer, adding `jitterMs` of lateness to the clock. */
  tick: (jitterMs?: number) => void;
  /** Jump the clock forward WITHOUT running timers — models an OS suspension. */
  suspend: (ms: number) => void;
  setNow: (ms: number) => void;
}

function harness(bpm: number, startAt = 1_000_000): Harness {
  let now = startAt;
  let pending: { callback: () => void; dueAt: number } | null = null;
  const recorder = createRecordingEmitter();

  const engine = new MetronomeEngine(
    { bpm, beatsPerBar: 4 },
    {
      now: () => now,
      setTimer: (callback, delayMs) => {
        pending = { callback, dueAt: now + delayMs };
        return 1;
      },
      clearTimer: () => {
        pending = null;
      },
      emitter: recorder,
    },
  );

  return {
    engine,
    stats: () => recorder.beats.reduce(observeBeat, emptyDriftStats()),
    tick: (jitterMs = 0) => {
      if (pending === null) throw new Error('no timer armed');
      const { callback, dueAt } = pending;
      pending = null;
      // Math.max, not assignment: after suspend() the clock is already past the
      // deadline, and rewinding it to `dueAt` would erase the very stall the
      // test is modelling. (It did, on the first run of this file.)
      now = Math.max(now, dueAt + jitterMs);
      callback();
    },
    suspend: (ms) => {
      now += ms;
    },
    setNow: (ms) => {
      now = ms;
    },
  };
}

describe('emptyDriftStats', () => {
  it('reads as a run that has not happened, not as a perfect run', () => {
    const stats = emptyDriftStats();
    expect(stats.beatsEmitted).toBe(0);
    expect(stats.firstBeatAt).toBeNull();
    expect(stats.lastBeatIndex).toBe(-1);
    // The distinction matters on screen: 0 beats must not render as a pass.
    expect(formatDriftSummary(stats)).toBe('No beats yet');
  });

  it('reports 0 rather than NaN for the derived numbers of an empty run', () => {
    const stats = emptyDriftStats();
    expect(meanDriftMs(stats)).toBe(0);
    expect(runDurationMs(stats)).toBe(0);
  });
});

describe('jitter measurement', () => {
  it('reports the worst beat, not the last one', () => {
    const h = harness(120);
    h.engine.start();
    h.tick(3);
    h.tick(41); // the outlier
    h.tick(2);
    h.tick(1);

    expect(h.stats().maxAbsDriftMs).toBe(41);
  });

  it('averages every beat including the exact first one', () => {
    const h = harness(120);
    h.engine.start(); // beat 0 fires at the anchor: drift exactly 0
    h.tick(10);
    h.tick(20);

    // (0 + 10 + 20) / 3
    expect(h.stats().beatsEmitted).toBe(3);
    expect(meanDriftMs(h.stats())).toBeCloseTo(10, 6);
  });

  it('states worst-case jitter as a share of the beat, since ms alone is unjudgeable', () => {
    const slow = harness(60);
    slow.engine.start();
    slow.tick(10);
    // 10ms against a 1000ms beat.
    expect(maxDriftAsBeatFraction(slow.stats(), 60)).toBeCloseTo(0.01, 6);

    const fast = harness(240);
    fast.engine.start();
    fast.tick(10);
    // The same 10ms against a 250ms beat is four times as audible.
    expect(maxDriftAsBeatFraction(fast.stats(), 240)).toBeCloseTo(0.04, 6);
  });
});

describe('grid error vs jitter — the distinction the whole readout exists for', () => {
  it('does NOT accumulate jitter into the grid number', () => {
    const h = harness(120);
    h.engine.start();

    // 400 beats, every one of them 20ms late. A summing implementation would
    // report a grid 8 SECONDS out and fail a device walk the engine passed.
    for (let i = 0; i < 400; i += 1) h.tick(20);

    const stats = h.stats();
    expect(stats.totalDriftMs).toBeCloseTo(400 * 20, 6);
    expect(stats.gridErrorMs).toBeCloseTo(20, 6);
    expect(Math.abs(stats.gridErrorMs)).toBeLessThan(intervalMsForBpm(120));
  });

  it('holds the grid number bounded over a session-length run', () => {
    const h = harness(120);
    h.engine.start();

    // 45 minutes at 120 BPM = 5400 beats, with jitter on every beat — the same
    // scenario as the headline engine test, now read through the instrument.
    for (let i = 1; i < 5400; i += 1) h.tick((i * 7) % 23);

    const stats = h.stats();
    expect(stats.beatsEmitted).toBe(5400);
    expect(stats.skippedBeats).toBe(0);
    // Bounded by one beat's jitter regardless of run length. This is the
    // number item 8 records as "drift at 45 min".
    expect(Math.abs(stats.gridErrorMs)).toBeLessThan(23);
    expect(runDurationMs(stats)).toBeGreaterThan(44 * 60 * 1000);
  });
});

describe('skipped beats — the app-suspension detector', () => {
  // Worth stating once, because it sets the shape of every test here and it is
  // not obvious from the engine: a stall produces ONE very late beat and THEN
  // a gap. The beat already pending when the thread froze is still emitted
  // when the thread returns — arriving hugely late, so `late` is set and the
  // audio emitter swallows the click — and only the following `scheduleNext`
  // resyncs onto a future deadline. So the skip is visible on the tick AFTER
  // the stall, not on the tick that ends it.

  it('counts the beats a stall swallowed', () => {
    const h = harness(120); // 500ms per beat
    h.engine.start();
    h.tick();

    // The JS thread disappears for 2.2s. Beat 2 was already armed, so it fires
    // 1700ms late; beats 3, 4 and 5 came due while frozen and are skipped
    // rather than fired as a burst.
    h.suspend(2200);
    h.tick(); // the stranded beat 2
    h.tick(); // resynced — this is where the gap shows up

    const stats = h.stats();
    expect(stats.skippedBeats).toBe(3);
    expect(stats.lateBeats).toBe(1);
    // Emitted + skipped must account for every beat index the grid contained,
    // or the readout is quietly losing beats.
    expect(stats.beatsEmitted + stats.skippedBeats).toBe(stats.lastBeatIndex + 1);
  });

  it('reports a screen-lock-sized suspension as skips, not as drift', () => {
    const h = harness(120);
    h.engine.start();
    h.tick();

    // 30 seconds of iOS auto-lock (D8) = 60 beats at 120 BPM.
    h.suspend(30_000);
    h.tick();
    h.tick();

    const stats = h.stats();
    expect(stats.skippedBeats).toBe(59);
    // The point of the distinction: the click DIED for half a minute, and the
    // grid number on its own says everything is fine — because it is. The grid
    // never moved. Only the skip count tells the tester the app was suspended,
    // which is a completely different bug from a wobbling click.
    expect(Math.abs(stats.gridErrorMs)).toBeLessThan(500);
    expect(stats.beatsEmitted + stats.skippedBeats).toBe(stats.lastBeatIndex + 1);
  });

  it('stays at zero through an ordinary run', () => {
    const h = harness(90);
    h.engine.start();
    for (let i = 0; i < 200; i += 1) h.tick(5);
    expect(h.stats().skippedBeats).toBe(0);
  });
});

describe('a tempo ladder push is not a skip', () => {
  it('counts the re-anchor instead of reading the beatIndex reset as lost beats', () => {
    const h = harness(60);
    h.engine.start();
    for (let i = 0; i < 20; i += 1) h.tick(2);

    // Ladder pushes 60 → 65. setBpm re-anchors, so beatIndex restarts at 0.
    h.engine.setBpm(65);
    for (let i = 0; i < 10; i += 1) h.tick(2);

    const stats = h.stats();
    expect(stats.tempoChanges).toBe(1);
    expect(stats.skippedBeats).toBe(0);
    expect(stats.beatsEmitted).toBe(31);
  });

  it('keeps measuring jitter across the push rather than restarting the run', () => {
    const h = harness(60);
    h.engine.start();
    h.tick(30); // the worst beat happens BEFORE the push

    h.engine.setBpm(80);
    for (let i = 0; i < 5; i += 1) h.tick(1);

    // A walk up a five-rung ladder must not forget the first rung's jitter.
    expect(h.stats().maxAbsDriftMs).toBe(30);
  });

  it('walks a full drill ladder and accounts for every beat', () => {
    const h = harness(60);
    h.engine.start();
    for (const bpm of [65, 70, 75, 80]) {
      for (let i = 0; i < 8; i += 1) h.tick(3);
      h.engine.setBpm(bpm);
    }
    for (let i = 0; i < 8; i += 1) h.tick(3);

    const stats = h.stats();
    expect(stats.tempoChanges).toBe(4);
    expect(stats.skippedBeats).toBe(0);
    expect(stats.beatsEmitted).toBe(41);
  });
});

describe('dropped clicks', () => {
  it('counts beats the audio emitter would swallow as out-of-position', () => {
    const h = harness(120); // 500ms per beat, so `late` is > 250ms
    h.engine.start();
    h.tick(10);
    h.tick(300); // past the halfway point — swallowed, not clicked
    h.tick(10);

    const stats = h.stats();
    expect(stats.lateBeats).toBe(1);
    // Still emitted and still measured: the grid advanced, the sound did not.
    expect(stats.beatsEmitted).toBe(4);
  });
});

describe('the line the tester reads', () => {
  it('leads with the run clock and carries all three failure modes', () => {
    const h = harness(120);
    h.engine.start();
    for (let i = 0; i < 240; i += 1) h.tick(4); // ~2 minutes

    const summary = formatDriftSummary(h.stats());
    expect(summary).toContain('2:00');
    expect(summary).toContain('241 beats');
    expect(summary).toContain('grid +4ms');
    expect(summary).toContain('jitter max 4ms');
    expect(summary).toContain('0 skipped');
    expect(summary).toContain('0 dropped');
  });

  it('keeps the sign, because the sign is the diagnosis', () => {
    expect(formatSignedMs(4)).toBe('+4ms');
    expect(formatSignedMs(-4)).toBe('-4ms');
    expect(formatSignedMs(0)).toBe('+0ms');
    expect(formatSignedMs(3.6)).toBe('+4ms');
  });

  it('formats the run clock as m:ss with a padded seconds field', () => {
    expect(formatRunClock(0)).toBe('0:00');
    expect(formatRunClock(9_000)).toBe('0:09');
    expect(formatRunClock(70_000)).toBe('1:10');
    expect(formatRunClock(45 * 60 * 1000)).toBe('45:00');
    // Never renders a negative clock, whatever the clock did.
    expect(formatRunClock(-5_000)).toBe('0:00');
  });
});

describe('observeBeat is pure', () => {
  it('does not mutate the snapshot it is given', () => {
    const before = emptyDriftStats();
    const beat: MetronomeBeat = {
      beatIndex: 0,
      barBeat: 0,
      downbeat: true,
      bpm: 120,
      scheduledAt: 1000,
      actualAt: 1007,
      driftMs: 7,
      late: false,
    };

    const after = observeBeat(before, beat);

    expect(before).toEqual(emptyDriftStats());
    expect(after).not.toBe(before);
    expect(after.beatsEmitted).toBe(1);
  });
});
