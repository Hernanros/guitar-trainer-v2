/**
 * Metronome tests — FLE-5 (Task 7).
 *
 * Mirrors the source at mobile/src/metronome/.
 *
 * The headline test is `drift over a session-length run`: 45 minutes of clicking
 * at 120 BPM (5400 beats) under deterministic per-beat jitter, asserting the
 * grid never accumulates error. It runs in milliseconds because MetronomeEngine
 * takes its clock and timers as injected deps, so the whole session is simulated
 * against a virtual clock rather than waited out.
 *
 * A contrast test replays the same jitter through the naive
 * `setInterval`-style scheme (measure the next beat from the last one) and
 * asserts it fails the very assertion the real engine passes — so the test
 * proves the design choice, not just the implementation.
 *
 * Everything here is pure TypeScript + the engine; no React rendering, matching
 * the pure-fn testing convention already used by drill.test.tsx.
 */

import {
  MIN_BPM,
  MAX_BPM,
  barBeatFor,
  beatDeadline,
  clampBpm,
  intervalMsForBpm,
  isDownbeat,
  isLate,
  nextBeatFrom,
} from '../../src/metronome/scheduler';
import { MetronomeEngine } from '../../src/metronome/MetronomeEngine';
import { createRecordingEmitter, silentClickEmitter, withBeatListener } from '../../src/metronome/emitters';
import type { MetronomeBeat } from '../../src/metronome/types';
import {
  activeDotIndex,
  formatBpmLabel,
  formatToggleAccessibilityLabel,
  formatToggleLabel,
} from '../../src/components/MetronomeControl';

// ---------------------------------------------------------------------------
// Virtual clock harness
// ---------------------------------------------------------------------------

/**
 * A single-timer virtual clock. The engine only ever has one timer armed at a
 * time, so this models it exactly.
 *
 * `deliveryLatency` simulates the JS thread handing the callback over late —
 * the thing a real metronome fights. Time jumps to `fireAt + latency`, so the
 * engine observes the same "I ran later than I asked to" condition it sees on
 * a device under load.
 */
class VirtualClock {
  currentTime = 0;
  private pending: { callback: () => void; fireAt: number; id: number } | null = null;
  private nextId = 1;

  /** Milliseconds of late delivery for the next callback. Overridden per test. */
  deliveryLatency: () => number = () => 0;

  now = (): number => this.currentTime;

  setTimer = (callback: () => void, delayMs: number): unknown => {
    const id = this.nextId++;
    this.pending = { callback, fireAt: this.currentTime + delayMs, id };
    return id;
  };

  clearTimer = (handle: unknown): void => {
    if (this.pending && this.pending.id === handle) this.pending = null;
  };

  hasPending(): boolean {
    return this.pending !== null;
  }

  /** Fire the armed timer, advancing time to its deadline plus simulated latency. */
  tick(): boolean {
    const p = this.pending;
    if (!p) return false;
    this.pending = null;
    this.currentTime = p.fireAt + this.deliveryLatency();
    p.callback();
    return true;
  }

  /** Fire `count` timers in sequence. */
  tickTimes(count: number): void {
    for (let i = 0; i < count; i += 1) {
      if (!this.tick()) return;
    }
  }
}

/**
 * Deterministic pseudo-random latency in [0, maxMs). A plain LCG — seeded, so
 * every run of the suite sees the identical jitter pattern and a drift
 * regression cannot hide behind a lucky seed.
 */
function makeLatency(seed: number, maxMs: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return (state / 0x100000000) * maxMs;
  };
}

function buildEngine(bpm: number, clock: VirtualClock, beatsPerBar = 4) {
  const emitter = createRecordingEmitter();
  const engine = new MetronomeEngine(
    { bpm, beatsPerBar },
    {
      now: clock.now,
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
      emitter,
    },
  );
  return { engine, emitter };
}

// ---------------------------------------------------------------------------
// scheduler.ts — pure timing math
// ---------------------------------------------------------------------------

describe('clampBpm', () => {
  it('passes through an in-range tempo', () => {
    expect(clampBpm(120)).toBe(120);
  });

  it('clamps below MIN_BPM and above MAX_BPM', () => {
    expect(clampBpm(1)).toBe(MIN_BPM);
    expect(clampBpm(10_000)).toBe(MAX_BPM);
  });

  it('falls back to 60 for non-finite input rather than throwing', () => {
    // Drill tempos are model-authored; a NaN target_bpm must not crash the screen.
    expect(clampBpm(Number.NaN)).toBe(60);
    expect(clampBpm(Number.POSITIVE_INFINITY)).toBe(MAX_BPM);
  });

  it('never yields an interval of zero or infinity', () => {
    for (const bpm of [0, -5, Number.NaN, 1e9]) {
      const interval = intervalMsForBpm(bpm);
      expect(interval).toBeGreaterThan(0);
      expect(Number.isFinite(interval)).toBe(true);
    }
  });
});

describe('intervalMsForBpm', () => {
  it('converts tempo to milliseconds per beat', () => {
    expect(intervalMsForBpm(120)).toBe(500);
    expect(intervalMsForBpm(60)).toBe(1000);
    expect(intervalMsForBpm(200)).toBe(300);
  });
});

describe('beatDeadline', () => {
  it('multiplies from the anchor instead of accumulating', () => {
    // The Nth deadline never reads the (N-1)th — that is the whole design.
    expect(beatDeadline(1000, 0, 500)).toBe(1000);
    expect(beatDeadline(1000, 4, 500)).toBe(3000);
    expect(beatDeadline(1000, 10_000, 500)).toBe(5_001_000);
  });
});

describe('isDownbeat / barBeatFor', () => {
  it('accents beat 0 of each bar', () => {
    expect(isDownbeat(0, 4)).toBe(true);
    expect(isDownbeat(4, 4)).toBe(true);
    expect([1, 2, 3, 5].every((i) => !isDownbeat(i, 4))).toBe(true);
  });

  it('disables accents when beatsPerBar <= 0', () => {
    expect(isDownbeat(0, 0)).toBe(false);
    expect(barBeatFor(7, 0)).toBe(0);
  });

  it('reports bar position', () => {
    expect([0, 1, 2, 3, 4, 5].map((i) => barBeatFor(i, 4))).toEqual([0, 1, 2, 3, 0, 1]);
  });
});

describe('isLate', () => {
  it('is true only past half an interval', () => {
    expect(isLate(100, 500)).toBe(false);
    expect(isLate(250, 500)).toBe(false);
    expect(isLate(251, 500)).toBe(true);
  });
});

describe('nextBeatFrom', () => {
  it('returns the next beat on the grid in the normal case', () => {
    expect(nextBeatFrom(0, 500, 3, 1600)).toEqual({ beatIndex: 4, scheduledAt: 2000 });
  });

  it('returns beat 0 before the first beat has fired', () => {
    expect(nextBeatFrom(1000, 500, -1, 900)).toEqual({ beatIndex: 0, scheduledAt: 1000 });
  });

  it('skips overdue beats rather than queueing a burst', () => {
    // Stalled 1.2s at 500ms/beat after beat 3: beats 4 and 5 are gone, not stacked.
    const next = nextBeatFrom(0, 500, 3, 2700);
    expect(next.beatIndex).toBe(6);
    expect(next.scheduledAt).toBe(3000);
  });

  it('stays on the original grid after a resync', () => {
    // The resync deadline is still an exact multiple of the interval from the
    // anchor, so bar accents keep landing on the downbeat.
    const next = nextBeatFrom(0, 500, 3, 2700);
    expect(next.scheduledAt % 500).toBe(0);
    expect(next.beatIndex % 4).toBe(barBeatFor(next.beatIndex, 4));
  });

  it('never returns a beat at or before now', () => {
    for (const now of [0, 499, 500, 501, 12_345]) {
      const next = nextBeatFrom(0, 500, -1, now);
      expect(next.scheduledAt).toBeGreaterThan(now);
    }
  });

  it('never goes backwards past the last emitted beat', () => {
    const next = nextBeatFrom(10_000, 500, 40, 0);
    expect(next.beatIndex).toBeGreaterThan(40);
  });
});

// ---------------------------------------------------------------------------
// MetronomeEngine — lifecycle
// ---------------------------------------------------------------------------

describe('MetronomeEngine lifecycle', () => {
  it('clicks immediately on start so pressing play makes a sound now', () => {
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(120, clock);

    engine.start();

    expect(emitter.beats).toHaveLength(1);
    expect(emitter.beats[0].beatIndex).toBe(0);
    expect(emitter.beats[0].downbeat).toBe(true);
    expect(emitter.beats[0].driftMs).toBe(0);
  });

  it('emits beats on the grid once running', () => {
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(120, clock);

    engine.start();
    clock.tickTimes(4);

    expect(emitter.beats.map((b) => b.beatIndex)).toEqual([0, 1, 2, 3, 4]);
    expect(emitter.beats.map((b) => b.scheduledAt)).toEqual([0, 500, 1000, 1500, 2000]);
    expect(emitter.beats.map((b) => b.barBeat)).toEqual([0, 1, 2, 3, 0]);
  });

  it('is idempotent on repeated start', () => {
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(120, clock);

    engine.start();
    engine.start();

    expect(emitter.beats).toHaveLength(1);
  });

  it('stops clicking and disarms the timer', () => {
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(120, clock);

    engine.start();
    clock.tickTimes(2);
    const countAtStop = emitter.beats.length;
    engine.stop();

    expect(clock.hasPending()).toBe(false);
    clock.tickTimes(5);
    expect(emitter.beats).toHaveLength(countAtStop);
    expect(engine.isRunning()).toBe(false);
  });

  it('survives stop() racing an in-flight timer callback', () => {
    // The engine holds a pending beat; stopping before it fires must not emit.
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(120, clock);

    engine.start();
    const before = emitter.beats.length;
    engine.stop();
    clock.tickTimes(3);

    expect(emitter.beats).toHaveLength(before);
  });

  it('restarts cleanly after a stop', () => {
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(120, clock);

    engine.start();
    clock.tickTimes(3);
    engine.stop();
    emitter.clear();

    engine.start();
    expect(emitter.beats[0].beatIndex).toBe(0);
    expect(emitter.beats[0].downbeat).toBe(true);
  });

  it('toggles between running and stopped', () => {
    const clock = new VirtualClock();
    const { engine } = buildEngine(120, clock);

    engine.toggle();
    expect(engine.isRunning()).toBe(true);
    engine.toggle();
    expect(engine.isRunning()).toBe(false);
  });

  it('dispose stops the engine and releases the emitter', () => {
    const clock = new VirtualClock();
    const dispose = jest.fn();
    const engine = new MetronomeEngine(
      { bpm: 120 },
      {
        now: clock.now,
        setTimer: clock.setTimer,
        clearTimer: clock.clearTimer,
        emitter: { emit: () => {}, dispose },
      },
    );

    engine.start();
    engine.dispose();

    expect(engine.isRunning()).toBe(false);
    expect(dispose).toHaveBeenCalledTimes(1);
    expect(clock.hasPending()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// MetronomeEngine — tempo ladder integration
// ---------------------------------------------------------------------------

describe('MetronomeEngine.setBpm — tempo ladder pushes', () => {
  it('re-anchors so the next beat is one NEW interval away, on the downbeat', () => {
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(60, clock); // 1000ms/beat

    engine.start();
    clock.tickTimes(2); // beats 0,1,2 at t=0,1000,2000
    emitter.clear();

    // Ladder pushes 60 → 120 BPM at t=2000. New interval is 500ms.
    engine.setBpm(120);
    expect(emitter.beats).toHaveLength(0); // no click at the instant of the change

    clock.tick();
    expect(emitter.beats).toHaveLength(1);
    expect(emitter.beats[0].bpm).toBe(120);
    expect(emitter.beats[0].beatIndex).toBe(0);
    expect(emitter.beats[0].downbeat).toBe(true); // bar restarts at the new tempo
    expect(emitter.beats[0].scheduledAt).toBe(2500); // exactly one new interval later
  });

  it('holds the NEW tempo exactly after a push', () => {
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(60, clock);

    engine.start();
    clock.tickTimes(1);
    engine.setBpm(120);
    emitter.clear();
    clock.tickTimes(6);

    const gaps = emitter.beats.slice(1).map((b, i) => b.scheduledAt - emitter.beats[i].scheduledAt);
    expect(gaps.every((g) => g === 500)).toBe(true);
  });

  it('walks a full drill ladder 60 → 70 BPM in 5 BPM rungs without drift', () => {
    // Mirrors advanceRepOrTempo in the drill screen: 5 BPM per rung, capped at target.
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(60, clock);
    engine.start();

    for (const bpm of [65, 70]) {
      clock.tickTimes(8);
      engine.setBpm(bpm);
    }
    clock.tickTimes(8);

    // Every beat after the final push must sit exactly on the 70 BPM grid.
    const finalRung = emitter.beats.filter((b) => b.bpm === 70);
    const interval = intervalMsForBpm(70);
    const anchor = finalRung[0].scheduledAt;
    finalRung.forEach((b) => {
      expect(b.scheduledAt).toBeCloseTo(anchor + b.beatIndex * interval, 9);
    });
  });

  it('ignores a redundant setBpm to the tempo already running', () => {
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(120, clock);

    engine.start();
    clock.tickTimes(2);
    const scheduledBefore = emitter.beats[emitter.beats.length - 1].scheduledAt;

    engine.setBpm(120); // same tempo — must not re-anchor
    clock.tick();

    expect(emitter.beats[emitter.beats.length - 1].scheduledAt).toBe(scheduledBefore + 500);
  });

  it('clamps a model-authored nonsense tempo instead of stalling the timer', () => {
    const clock = new VirtualClock();
    const { engine } = buildEngine(120, clock);

    engine.start();
    engine.setBpm(0);

    expect(engine.getState().bpm).toBe(MIN_BPM);
    expect(clock.hasPending()).toBe(true);
  });

  it('updates tempo while stopped without starting the click', () => {
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(120, clock);

    engine.setBpm(90);

    expect(engine.getState().bpm).toBe(90);
    expect(engine.isRunning()).toBe(false);
    expect(emitter.beats).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// MetronomeEngine — the drift guarantee
// ---------------------------------------------------------------------------

describe('drift over a session-length run', () => {
  const BPM = 120;
  const INTERVAL = 500; // ms
  const SESSION_MS = 45 * 60 * 1000; // 45 minutes
  const BEATS = SESSION_MS / INTERVAL; // 5400

  it('holds the grid for 5400 beats under per-beat jitter', () => {
    const clock = new VirtualClock();
    clock.deliveryLatency = makeLatency(0xc0ffee, 12); // up to 12ms late, every beat
    const { engine, emitter } = buildEngine(BPM, clock);

    engine.start();
    clock.tickTimes(BEATS - 1);

    expect(emitter.beats).toHaveLength(BEATS);

    // 1. No beat was dropped — jitter below one interval must never resync.
    expect(emitter.beats.map((b) => b.beatIndex)).toEqual(
      Array.from({ length: BEATS }, (_, i) => i),
    );

    // 2. THE ASSERTION. Every beat sits exactly on anchor + n * interval. Not
    //    "close to" — exactly, because each deadline is multiplied from the
    //    anchor rather than added to its predecessor.
    const anchor = emitter.beats[0].scheduledAt;
    emitter.beats.forEach((b, i) => {
      expect(b.scheduledAt).toBe(anchor + i * INTERVAL);
    });

    // 3. The last beat of a 45-minute run is where it should be, to the ms.
    const last = emitter.beats[BEATS - 1];
    expect(last.scheduledAt - anchor).toBe((BEATS - 1) * INTERVAL);

    // 4. Per-beat jitter stays bounded by the simulated latency and never
    //    compounds — beat 5400 is no later than beat 5.
    const drifts = emitter.beats.slice(1).map((b) => b.driftMs);
    expect(Math.max(...drifts)).toBeLessThanOrEqual(12);
    expect(Math.min(...drifts)).toBeGreaterThanOrEqual(0);

    const firstTen = drifts.slice(0, 10).reduce((a, b) => a + b, 0) / 10;
    const lastTen = drifts.slice(-10).reduce((a, b) => a + b, 0) / 10;
    expect(Math.abs(lastTen - firstTen)).toBeLessThan(12);
  });

  it('stays on the grid at 200 BPM, where jitter is a larger share of the interval', () => {
    const clock = new VirtualClock();
    clock.deliveryLatency = makeLatency(0x5eed, 20);
    const { engine, emitter } = buildEngine(200, clock); // 300ms/beat
    const interval = intervalMsForBpm(200);

    engine.start();
    clock.tickTimes(2000);

    const anchor = emitter.beats[0].scheduledAt;
    emitter.beats.forEach((b) => {
      expect(b.scheduledAt).toBeCloseTo(anchor + b.beatIndex * interval, 9);
    });
  });

  it('CONTRAST: the naive setInterval scheme fails this same assertion', () => {
    // Replays the identical jitter through "schedule the next beat one interval
    // after the callback actually ran" — i.e. plain setInterval semantics.
    // Each delay is carried forward, so the error is the SUM of every latency.
    const latency = makeLatency(0xc0ffee, 12);
    let naiveTime = 0;
    for (let i = 1; i < BEATS; i += 1) {
      naiveTime = naiveTime + INTERVAL + latency();
    }

    const ideal = (BEATS - 1) * INTERVAL;
    const accumulated = naiveTime - ideal;

    // ~6ms mean latency × 5399 beats ≈ 32 seconds adrift by the end of one session.
    expect(accumulated).toBeGreaterThan(10_000);

    // And the engine, given the same jitter, is exactly on the grid — the
    // assertion above passes with toBe(), not toBeCloseTo().
    const clock = new VirtualClock();
    clock.deliveryLatency = makeLatency(0xc0ffee, 12);
    const { engine, emitter } = buildEngine(BPM, clock);
    engine.start();
    clock.tickTimes(BEATS - 1);

    const anchor = emitter.beats[0].scheduledAt;
    expect(emitter.beats[BEATS - 1].scheduledAt - anchor).toBe(ideal);
  });
});

// ---------------------------------------------------------------------------
// MetronomeEngine — stall recovery
// ---------------------------------------------------------------------------

describe('stall recovery', () => {
  it('skips missed beats instead of firing a burst', () => {
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(120, clock); // 500ms

    engine.start();
    clock.tickTimes(3); // beats 0..3, now t=1500

    // A 1.2s stall — GC, navigation, a slow render.
    clock.deliveryLatency = () => 1200;
    clock.tick();
    clock.deliveryLatency = () => 0;

    const afterStall = emitter.beats[emitter.beats.length - 1];
    expect(afterStall.late).toBe(true);

    // The next beat skips ahead rather than replaying beats 5 and 6 immediately.
    clock.tick();
    const resumed = emitter.beats[emitter.beats.length - 1];
    expect(resumed.beatIndex).toBeGreaterThan(afterStall.beatIndex + 1);
    expect(resumed.driftMs).toBeLessThan(500);
  });

  it('resyncs onto the original grid so accents stay on the downbeat', () => {
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(120, clock);

    engine.start();
    const anchor = emitter.beats[0].scheduledAt;

    clock.tickTimes(2);
    clock.deliveryLatency = () => 2300;
    clock.tick();
    clock.deliveryLatency = () => 0;
    clock.tickTimes(4);

    // Every beat — including everything after the stall — is still an exact
    // multiple of the interval from the ORIGINAL anchor.
    emitter.beats.forEach((b) => {
      expect((b.scheduledAt - anchor) % 500).toBe(0);
      expect(b.scheduledAt).toBe(anchor + b.beatIndex * 500);
    });

    // And downbeats still land on beatIndex % 4 === 0.
    emitter.beats
      .filter((b) => b.downbeat)
      .forEach((b) => expect(b.beatIndex % 4).toBe(0));
  });

  it('marks a beat late only when it lands past the halfway point', () => {
    const clock = new VirtualClock();
    const { engine, emitter } = buildEngine(120, clock); // 500ms, halfway = 250ms

    engine.start();
    clock.deliveryLatency = () => 100;
    clock.tick();
    expect(emitter.beats[emitter.beats.length - 1].late).toBe(false);

    clock.deliveryLatency = () => 300;
    clock.tick();
    expect(emitter.beats[emitter.beats.length - 1].late).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// emitters.ts
// ---------------------------------------------------------------------------

describe('emitters', () => {
  it('silentClickEmitter accepts beats without throwing', () => {
    expect(() =>
      silentClickEmitter.emit({
        beatIndex: 0,
        barBeat: 0,
        downbeat: true,
        bpm: 120,
        scheduledAt: 0,
        actualAt: 0,
        driftMs: 0,
        late: false,
      }),
    ).not.toThrow();
  });

  it('withBeatListener forwards to the inner emitter and the listener', () => {
    const inner = createRecordingEmitter();
    const seen: MetronomeBeat[] = [];
    const wrapped = withBeatListener(inner, (b) => seen.push(b));

    const clock = new VirtualClock();
    const engine = new MetronomeEngine(
      { bpm: 120 },
      { now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer, emitter: wrapped },
    );
    engine.start();
    clock.tickTimes(2);

    expect(inner.beats).toHaveLength(3);
    expect(seen).toHaveLength(3);
    expect(seen[2].beatIndex).toBe(2);
  });

  it('withBeatListener forwards dispose to the inner emitter', () => {
    const dispose = jest.fn();
    withBeatListener({ emit: () => {}, dispose }, () => {}).dispose?.();
    expect(dispose).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// MetronomeControl — pure-fn helpers
// ---------------------------------------------------------------------------

describe('MetronomeControl helpers', () => {
  it('formats the tempo readout', () => {
    expect(formatBpmLabel(120)).toBe('120 BPM');
    expect(formatBpmLabel(93.4)).toBe('93 BPM');
  });

  it('uses Fletcher-voiced transport labels', () => {
    expect(formatToggleLabel(false)).toBe('Start click');
    expect(formatToggleLabel(true)).toBe('Stop click');
    expect(formatToggleLabel(true)).not.toContain('!');
  });

  it('carries the tempo in the accessibility label', () => {
    expect(formatToggleAccessibilityLabel(false, 120)).toBe('Start click at 120 BPM');
    expect(formatToggleAccessibilityLabel(true, 70)).toBe('Stop click at 70 BPM');
  });

  it('dims every dot while stopped', () => {
    expect(activeDotIndex(2, false)).toBe(-1);
    expect(activeDotIndex(null, true)).toBe(-1);
    expect(activeDotIndex(2, true)).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// Guard: exactly one audio dependency, and only the approved one
// ---------------------------------------------------------------------------

describe('dependency guard', () => {
  // This test used to assert the opposite — that NO audio package was present —
  // while the dependency decision was open on FLE-5. Miagi approved `expo-audio`
  // and it was installed in the same commit that flipped this. The guard is kept
  // rather than deleted because its job never was "stay silent"; it is "no audio
  // dependency arrives without a decision", and that still holds for the others.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const pkg = require('../../package.json');
  const deps = Object.keys(pkg.dependencies ?? {});

  it('ships expo-audio — the approved backend', () => {
    expect(deps).toContain('expo-audio');
  });

  it('ships no OTHER audio package', () => {
    // expo-av is the deprecated predecessor: having both means two audio
    // sessions fighting over the same iOS category. expo-haptics was rejected
    // on FLE-5 (iOS ignores vibration duration; ~400ms buzz at 500ms/beat).
    expect(deps).not.toContain('expo-av');
    expect(deps).not.toContain('expo-haptics');
    expect(deps).not.toContain('react-native-sound');
  });
});
