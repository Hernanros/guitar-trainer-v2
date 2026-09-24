/**
 * Audio click emitter + click assets — FLE-5 (Task 7).
 *
 * `expo-audio` is installed now, but the adapter still takes the player module
 * as an argument instead of importing it. That is what makes this file
 * possible: the behaviour that would otherwise be verifiable only on a physical
 * device — voice pooling, late-beat suppression, accent routing, teardown,
 * surviving a native throw — is covered here against a fake module, with no
 * native mock and no build.
 *
 * The asset checks read the .wav files off disk rather than through a bundler,
 * so they assert the real bytes the device will decode.
 */

import { readFileSync, existsSync } from 'fs';
import { join } from 'path';
import {
  CLICK_PLAYER_OPTIONS,
  CLICK_REWIND_DELAY_MS,
  DEFAULT_VOICES_PER_SOUND,
  createAudioClickEmitter,
  formatVoicePoolDiagnostics,
  prepareClickAudioMode,
} from '../../src/metronome/audioEmitter';
import type { AudioModuleLike, AudioPlayerLike } from '../../src/metronome/audioEmitter';
import type { MetronomeBeat, TimerHandle } from '../../src/metronome/types';
import type * as ExpoAudio from 'expo-audio';

// ---------------------------------------------------------------------------
// Type-level test: the real module must satisfy the structural interface
// ---------------------------------------------------------------------------
//
// Every runtime test below runs against a FAKE expo-audio, which is what keeps
// them fast and device-free — but it also means they would all still pass if
// `AudioModuleLike` had drifted away from the actual package. This assignment
// closes that gap: it fails `tsc` if expo-audio's `createAudioPlayer`,
// `setAudioModeAsync`, or the player's `play`/`seekTo`/`remove` ever stop
// matching what the adapter calls — including across an SDK upgrade.
//
// `import type` is erased at compile time, so this costs the test run nothing
// and never pulls a native module into Jest.
const _expoAudioSatisfiesAdapter: AudioModuleLike = null as unknown as typeof ExpoAudio;
void _expoAudioSatisfiesAdapter;

// ---------------------------------------------------------------------------
// Fake expo-audio
// ---------------------------------------------------------------------------

interface FakePlayer extends AudioPlayerLike {
  /** Which source this player was built from — lets a test tell tick from accent. */
  source: unknown;
  /** The options expo-audio was asked to build this player with. */
  options: Record<string, unknown> | undefined;
  calls: string[];
  removed: boolean;
  /** Set by a test to make the next play() throw, simulating a native failure. */
  failOnPlay: boolean;
  /**
   * 0 = parked at the head, ready to play. 1 = at EOF, mirroring expo-audio's
   * `actionAtItemEnd = .pause` — a `play()` here is a coin-flip on real
   * hardware and silent in this fake, so tests assert against it directly.
   * Only `seekTo`'s resolution moves this back to 0 — never `seekTo` itself —
   * which is what makes `seekTo` genuinely asynchronous here rather than a
   * same-tick no-op.
   */
  position: number;
  /** The position observed at the instant of every `play()` call, in order. */
  positionAtPlay: number[];
  /**
   * FLE-77's actual root cause: on the real package, a seek with no (or
   * infinite) tolerance can resolve its promise without moving the playhead
   * at all. Setting this makes the fake's `seekTo` do the same — "succeed"
   * while leaving `position` untouched — so tests can prove the adapter
   * verifies `currentTime` instead of trusting the resolution.
   */
  seekIsNoop: boolean;
  /** One-shot: makes the next `seekTo` call reject instead of resolving. */
  failOnSeek: boolean;
}

function createFakeAudio() {
  const players: FakePlayer[] = [];
  const modes: Record<string, unknown>[] = [];

  const audio: AudioModuleLike = {
    createAudioPlayer(source: unknown, options?: Record<string, unknown>) {
      const player: FakePlayer = {
        source,
        options,
        calls: [],
        removed: false,
        failOnPlay: false,
        position: 0,
        positionAtPlay: [],
        seekIsNoop: false,
        failOnSeek: false,
        get currentTime() {
          return player.position;
        },
        seekTo(seconds: number, toleranceMillisBefore?: number, toleranceMillisAfter?: number) {
          player.calls.push(`seekTo(${seconds}, ${toleranceMillisBefore}, ${toleranceMillisAfter})`);
          if (player.failOnSeek) {
            player.failOnSeek = false; // one-shot, so a retry can succeed
            return Promise.reject(new Error('native seek failed'));
          }
          // Genuinely async: the position only moves once this promise's
          // continuation runs, which is at least one microtask after the
          // call — never in the same synchronous stretch that called it.
          return Promise.resolve().then(() => {
            if (!player.seekIsNoop) player.position = seconds;
          });
        },
        play() {
          if (player.failOnPlay) throw new Error('native player exploded');
          player.positionAtPlay.push(player.position);
          player.calls.push('play');
          player.position = 1;
        },
        remove() {
          player.removed = true;
        },
      };
      players.push(player);
      return player;
    },
    async setAudioModeAsync(mode: Record<string, unknown>) {
      modes.push(mode);
    },
  };

  const played = () => players.filter((p) => p.calls.includes('play'));
  const forSource = (source: unknown) => players.filter((p) => p.source === source);

  return { audio, players, modes, played, forSource };
}

/**
 * A virtual clock that can hold several concurrent timers — unlike the
 * single-timer clock in metronome.test.ts, which models the engine's own
 * usage. The rewind pool can have one pending rewind per voice at once, so
 * this test file needs its own.
 */
class MultiTimerClock {
  currentTime = 0;
  private readonly timers = new Map<number, { fireAt: number; callback: () => void }>();
  private nextId = 1;

  setTimer = (callback: () => void, delayMs: number): TimerHandle => {
    const id = this.nextId++;
    this.timers.set(id, { fireAt: this.currentTime + delayMs, callback });
    return id;
  };

  clearTimer = (handle: TimerHandle): void => {
    this.timers.delete(handle as number);
  };

  pendingCount(): number {
    return this.timers.size;
  }

  /** Advances time and fires every timer whose deadline has now passed, in deadline order. */
  advanceBy(ms: number): void {
    this.currentTime += ms;
    const due = [...this.timers.entries()]
      .filter(([, timer]) => timer.fireAt <= this.currentTime)
      .sort((a, b) => a[1].fireAt - b[1].fireAt);
    for (const [id, timer] of due) {
      this.timers.delete(id);
      timer.callback();
    }
  }
}

/** Lets a pending `Promise.resolve().then(...)` continuation run before the next assertion. */
const flushMicrotasks = () => Promise.resolve();

const SOURCES = { tick: 'tick.wav', accent: 'accent.wav' };

function beat(overrides: Partial<MetronomeBeat> = {}): MetronomeBeat {
  return {
    beatIndex: 0,
    barBeat: 0,
    downbeat: false,
    bpm: 120,
    scheduledAt: 0,
    actualAt: 0,
    driftMs: 0,
    late: false,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------

describe('createAudioClickEmitter — allocation', () => {
  it('builds every player up front, so no beat pays for file IO on its deadline', () => {
    const { audio, players } = createFakeAudio();
    createAudioClickEmitter(audio, SOURCES);

    // Two sounds x the default pool size, all before a single beat has fired.
    expect(players).toHaveLength(DEFAULT_VOICES_PER_SOUND * 2);
    expect(players.every((p) => p.calls.length === 0)).toBe(true);
  });

  it('honours an explicit pool size', () => {
    const { audio, forSource } = createFakeAudio();
    createAudioClickEmitter(audio, SOURCES, { voicesPerSound: 3 });

    expect(forSource('tick.wav')).toHaveLength(3);
    expect(forSource('accent.wav')).toHaveLength(3);
  });

  it('never drops below one player per sound', () => {
    const { audio, forSource } = createFakeAudio();
    createAudioClickEmitter(audio, SOURCES, { voicesPerSound: 0 });

    expect(forSource('tick.wav')).toHaveLength(1);
    expect(forSource('accent.wav')).toHaveLength(1);
  });

  it('turns status updates down on every player, keeping the bridge quiet between beats', () => {
    // expo-audio defaults updateInterval to 500ms, so four players would post
    // eight events a second onto the JS thread that owes the next beat a
    // deadline — for a status nothing in this module ever reads.
    const { audio, players } = createFakeAudio();
    createAudioClickEmitter(audio, SOURCES);

    expect(players).not.toHaveLength(0);
    for (const player of players) {
      expect(player.options).toEqual(CLICK_PLAYER_OPTIONS);
      expect(player.options?.updateInterval).toBeGreaterThan(500);
    }
  });
});

describe('createAudioClickEmitter — playback', () => {
  it('plays without seeking on the beat path', () => {
    // seekTo(0) is async and play() is not — calling both back to back on the
    // beat path races them. The fix moves the rewind off this path entirely.
    const { audio, played } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES);

    emitter.emit(beat());

    expect(played()).toHaveLength(1);
    expect(played()[0].calls).toEqual(['play']);
  });

  it('arms a rewind via the injected timer after playing, parking the voice back at 0', async () => {
    const clock = new MultiTimerClock();
    const { audio, played } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, {
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
    });

    emitter.emit(beat());
    const player = played()[0];
    expect(player.calls).toEqual(['play']);
    expect(player.position).toBe(1); // parked at EOF until the rewind lands

    clock.advanceBy(CLICK_REWIND_DELAY_MS);
    await flushMicrotasks();

    // Zero tolerance both sides (FLE-77) — see audioEmitter.ts's file header
    // for why an un-toleranced seekTo(0) can resolve without moving anything.
    expect(player.calls).toEqual(['play', 'seekTo(0, 0, 0)']);
    expect(player.position).toBe(0);
  });

  it('plays every voice from position 0 even though the rewind that parks it there is asynchronous', async () => {
    // Confirms the fixed pooling mechanics hold up over sustained cycling at
    // the tightest real gap (MAX_BPM). It does NOT independently reproduce
    // FLE-57: this fake's seekTo resolves after one microtask, and this test
    // flushes microtasks once per beat, which is enough real-world slack for
    // even the OLD (buggy) emitter's own same-round seekTo call to land
    // before the voice's next turn — so it does not fail against pre-fix
    // code. The test above ("plays without seeking on the beat path") is the
    // one that actually catches the regression: it proves no seekTo call
    // ever precedes play() on the beat path, which is the one guarantee that
    // makes the race structurally impossible, independent of timing.
    const clock = new MultiTimerClock();
    const { audio, forSource } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, {
      voicesPerSound: 3,
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
    });

    // 9 beats cycles a 3-voice pool three times — every voice reused twice.
    for (let i = 1; i <= 9; i += 1) {
      emitter.emit(beat({ beatIndex: i }));
      clock.advanceBy(200); // MAX_BPM's own beat interval — the tightest real gap.
      await flushMicrotasks();
    }

    const ticks = forSource('tick.wav') as FakePlayer[];
    expect(ticks).toHaveLength(3);
    for (const voice of ticks) {
      expect(voice.positionAtPlay).toEqual(voice.positionAtPlay.map(() => 0));
    }
  });

  it('FLE-77: skips a voice whose rewind has not resolved yet, picking a confirmed-ready one instead', async () => {
    // FLE-57 treated the rewind TIMER firing as proof the voice was at 0. On
    // device the timer firing only means seekTo(0) was *issued* — the promise
    // it returns can still resolve later, especially under exactly the kind
    // of JS-thread jitter this whole module already expects (see
    // scheduler.ts). This simulates that: three voices are played through
    // once each, but only the middle one's seekTo has actually resolved by
    // the time the 4th beat needs a voice. The cursor's own turn would land
    // back on voice 0 (still unconfirmed) — the fix must reach past it for
    // voice 1 instead of forcing a voice that might still be mid-rewind.
    const timers: Array<() => void> = [];
    const setTimer = (callback: () => void): TimerHandle => {
      timers.push(callback);
      return timers.length - 1;
    };
    const clearTimer = () => {};
    const { audio, forSource } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, {
      voicesPerSound: 3,
      setTimer,
      clearTimer,
    });

    emitter.emit(beat({ beatIndex: 1 })); // voice 0
    emitter.emit(beat({ beatIndex: 2 })); // voice 1
    emitter.emit(beat({ beatIndex: 3 })); // voice 2

    const ticks = forSource('tick.wav') as FakePlayer[];
    // Fire only voice 1's rewind timer and let its seekTo promise resolve.
    // Voice 0's and voice 2's timers are deliberately left unfired — on
    // hardware this is a rewind that simply has not landed yet.
    timers[1]();
    await flushMicrotasks();
    await flushMicrotasks();

    emitter.emit(beat({ beatIndex: 4 }));

    expect(ticks[1].calls.filter((c) => c === 'play')).toHaveLength(2); // reused
    expect(ticks[0].calls.filter((c) => c === 'play')).toHaveLength(1); // skipped, still unconfirmed
    expect(ticks[2].calls.filter((c) => c === 'play')).toHaveLength(1); // skipped, still unconfirmed
    // The reused voice was genuinely at 0 both times it played — never a
    // doubled attack on top of a click still finishing.
    expect(ticks[1].positionAtPlay).toEqual([0, 0]);
  });

  it('FLE-77: skips a beat rather than forcing a busy voice, when every rewind is starved', () => {
    // The adversarial case: every rewind is slower than the gap between
    // beats (no clock is injected here, so none ever resolves). `next()`
    // then has no confirmed-ready voice to offer for any beat past the first
    // cycle. FLE-57's fix forced the cursor's own busy voice here — exactly
    // the path that could land a stale seek inside a live click, i.e. the
    // doubled attack this whole rework exists to remove. This pass never
    // does that: a starved pool skips the click and counts it instead.
    const { audio, forSource } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, { voicesPerSound: 2 });

    for (let i = 1; i <= 6; i += 1) emitter.emit(beat({ beatIndex: i }));

    const ticks = forSource('tick.wav') as FakePlayer[];
    expect(ticks).toHaveLength(2);
    // The first 2 beats claim the pool; the other 4 have nothing confirmed
    // ready (nothing ever rewinds, since no clock is injected) and are
    // silently skipped rather than doubling either voice.
    expect(ticks[0].calls.filter((c) => c === 'play')).toHaveLength(1);
    expect(ticks[1].calls.filter((c) => c === 'play')).toHaveLength(1);
    expect(emitter.getDiagnostics?.().starvedBeats).toBe(4);
  });

  it('never plays a still-busy single-voice pool twice, and leaves exactly one rewind armed', () => {
    // A voice reused before its rewind fired is the exact FLE-77 mechanism —
    // this pins that it cannot happen through the public emit() path, and
    // that armRewind() does not leak a second timer trying.
    const clock = new MultiTimerClock();
    const { audio, played } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, {
      voicesPerSound: 1,
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
    });

    emitter.emit(beat({ beatIndex: 1 }));
    emitter.emit(beat({ beatIndex: 2 })); // the only voice is still busy — must be skipped

    expect(played()).toHaveLength(1);
    expect(clock.pendingCount()).toBe(1); // exactly one rewind armed, not leaked or doubled
  });

  it('FLE-77: does not trust a resolved seekTo that left the playhead non-zero — retries instead of clearing busy', async () => {
    // The actual root cause: expo-audio's seekTo tolerances default to
    // +-infinity, so AVFoundation can resolve a seek to 0 without moving the
    // playhead at all. A promise resolving must not be trusted on its own.
    const clock = new MultiTimerClock();
    const { audio, forSource } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, {
      voicesPerSound: 1,
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
    });

    emitter.emit(beat({ beatIndex: 1 }));
    const player = forSource('tick.wav')[0] as FakePlayer;
    player.seekIsNoop = true; // the seek "succeeds" without ever reaching 0

    clock.advanceBy(CLICK_REWIND_DELAY_MS);
    await flushMicrotasks();
    await flushMicrotasks();

    expect(player.position).toBe(1); // still parked at EOF — the no-op was not trusted
    emitter.emit(beat({ beatIndex: 2 })); // still busy, so this must be skipped, not doubled
    expect(player.calls.filter((c) => c === 'play')).toHaveLength(1);

    player.seekIsNoop = false; // the retried rewind actually lands this time
    clock.advanceBy(CLICK_REWIND_DELAY_MS);
    await flushMicrotasks();
    await flushMicrotasks();

    emitter.emit(beat({ beatIndex: 3 }));
    expect(player.calls.filter((c) => c === 'play')).toHaveLength(2); // now confirmed ready
    expect(emitter.getDiagnostics?.().nonZeroRewinds).toBeGreaterThan(0);
  });

  it('FLE-77: retries after a rejected seekTo instead of stranding the voice as busy forever', async () => {
    const clock = new MultiTimerClock();
    const { audio, forSource } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, {
      voicesPerSound: 1,
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
    });

    emitter.emit(beat({ beatIndex: 1 }));
    const player = forSource('tick.wav')[0] as FakePlayer;
    player.failOnSeek = true;

    clock.advanceBy(CLICK_REWIND_DELAY_MS);
    for (let i = 0; i < 4; i += 1) await flushMicrotasks(); // rejection propagates through an extra .then()/.catch() hop

    emitter.emit(beat({ beatIndex: 2 })); // the rejected seek must not have cleared busy
    expect(player.calls.filter((c) => c === 'play')).toHaveLength(1);

    clock.advanceBy(CLICK_REWIND_DELAY_MS); // the retried seekTo, which now succeeds
    await flushMicrotasks();
    await flushMicrotasks();

    emitter.emit(beat({ beatIndex: 3 }));
    expect(player.calls.filter((c) => c === 'play')).toHaveLength(2);
    expect(emitter.getDiagnostics?.().rewindErrors).toBeGreaterThan(0);
  });

  it('routes the downbeat to the accent sound and everything else to the tick', () => {
    const { audio, forSource } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES);

    emitter.emit(beat({ beatIndex: 0, barBeat: 0, downbeat: true }));
    emitter.emit(beat({ beatIndex: 1, barBeat: 1 }));
    emitter.emit(beat({ beatIndex: 2, barBeat: 2 }));

    expect(forSource('accent.wav').filter((p) => p.calls.includes('play'))).toHaveLength(1);
    expect(forSource('tick.wav').filter((p) => p.calls.includes('play'))).toHaveLength(2);
  });

  it('cycles voices so a click never retriggers the player used on the previous beat', async () => {
    // This is the whole reason the pool exists: seekTo() is async, so reusing
    // one player makes each click race the rewind of the click before it.
    // Each beat here is spaced far enough apart for the previous rewind to be
    // confirmed, so round-robin distribution is the only thing under test —
    // a starved pool is covered separately above.
    const clock = new MultiTimerClock();
    const { audio, forSource } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, {
      voicesPerSound: 2,
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
    });

    for (let i = 1; i <= 4; i += 1) {
      emitter.emit(beat({ beatIndex: i, barBeat: i % 4 || 1 }));
      clock.advanceBy(CLICK_REWIND_DELAY_MS);
      await flushMicrotasks();
      await flushMicrotasks();
    }

    const ticks = forSource('tick.wav') as FakePlayer[];
    expect(ticks).toHaveLength(2);
    // Four beats, alternating: each player took exactly two of them.
    expect(ticks[0].calls.filter((c) => c === 'play')).toHaveLength(2);
    expect(ticks[1].calls.filter((c) => c === 'play')).toHaveLength(2);
  });

  it('swallows a late beat instead of clicking out of position', () => {
    // A beat closer to the next beat than its own is heard as the pulse moving.
    // Silence there is recoverable; a misplaced click is not.
    const { audio, played } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES);

    emitter.emit(beat({ beatIndex: 1, late: true, driftMs: 400 }));

    expect(played()).toHaveLength(0);
  });

  it('keeps clicking after a native failure, and reports it', () => {
    const { audio, players, played } = createFakeAudio();
    const onError = jest.fn();
    const emitter = createAudioClickEmitter(audio, SOURCES, { voicesPerSound: 1, onError });

    const tickPlayer = players.find((p) => p.source === 'tick.wav') as FakePlayer;
    tickPlayer.failOnPlay = true;
    emitter.emit(beat({ beatIndex: 1 }));

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][1].beatIndex).toBe(1);

    // A dead click must not end the session.
    tickPlayer.failOnPlay = false;
    emitter.emit(beat({ beatIndex: 2 }));
    expect(played()).toHaveLength(1);
  });

  it('survives a failure with no onError handler', () => {
    const { audio, players } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, { voicesPerSound: 1 });
    (players.find((p) => p.source === 'tick.wav') as FakePlayer).failOnPlay = true;

    expect(() => emitter.emit(beat({ beatIndex: 1 }))).not.toThrow();
  });
});

describe('createAudioClickEmitter — teardown', () => {
  it('frees every native player', () => {
    const { audio, players } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES);

    emitter.dispose?.();

    expect(players).toHaveLength(DEFAULT_VOICES_PER_SOUND * 2);
    expect(players.every((p) => p.removed)).toBe(true);
  });

  it('ignores beats after disposal', () => {
    // The engine's teardown and a trailing timer callback can race; a click
    // through a removed native player is a crash, not a sound.
    const { audio, played } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES);

    emitter.dispose?.();
    emitter.emit(beat());

    expect(played()).toHaveLength(0);
  });

  it('is safe to dispose twice', () => {
    const { audio } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES);

    emitter.dispose?.();
    expect(() => emitter.dispose?.()).not.toThrow();
  });

  it('cancels pending rewinds on dispose, so a removed player is never touched again', () => {
    const clock = new MultiTimerClock();
    const { audio, played } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, {
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
    });

    emitter.emit(beat());
    const player = played()[0];

    emitter.dispose?.();
    clock.advanceBy(CLICK_REWIND_DELAY_MS * 2);

    expect(player.calls).toEqual(['play']); // no seekTo after dispose
    expect(player.removed).toBe(true);
  });
});

describe('createAudioClickEmitter — diagnostics (FLE-77)', () => {
  it('reports a clean run as zero no-ops, zero rejections, zero starved beats', async () => {
    const clock = new MultiTimerClock();
    const { audio } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, {
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
    });

    emitter.emit(beat({ beatIndex: 1 }));
    clock.advanceBy(CLICK_REWIND_DELAY_MS);
    await flushMicrotasks();
    await flushMicrotasks();

    const diagnostics = emitter.getDiagnostics?.();
    expect(diagnostics?.seekCount).toBe(1);
    expect(diagnostics?.nonZeroRewinds).toBe(0);
    expect(diagnostics?.rewindErrors).toBe(0);
    expect(diagnostics?.starvedBeats).toBe(0);
  });

  it('measures seekTo latency off the injected clock, not a guess', async () => {
    const clock = new MultiTimerClock();
    let wallClockMs = 0;
    const { audio } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, {
      voicesPerSound: 1,
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
      now: () => wallClockMs,
    });

    emitter.emit(beat({ beatIndex: 1 }));
    clock.advanceBy(CLICK_REWIND_DELAY_MS);
    wallClockMs = 42; // the seekTo call "took" 42ms to resolve, from now()'s perspective
    await flushMicrotasks();
    await flushMicrotasks();

    expect(emitter.getDiagnostics?.().seekLatencyP50Ms).toBe(42);
  });
});

describe('formatVoicePoolDiagnostics', () => {
  it('renders every counter the device tester needs to confirm or refute the FLE-77 root cause', () => {
    const line = formatVoicePoolDiagnostics({
      seekCount: 120,
      seekLatencyP50Ms: 8,
      seekLatencyP95Ms: 24,
      nonZeroRewinds: 0,
      rewindErrors: 0,
      starvedBeats: 0,
    });

    expect(line).toBe('120 seeks · p50 8ms / p95 24ms · 0 no-op · 0 rejected · 0 starved');
  });
});

describe('prepareClickAudioMode', () => {
  it('plays through the silent switch', async () => {
    // A phone propped on a music stand is usually on silent. Without this the
    // metronome is running, correct, and completely inaudible.
    const { audio, modes } = createFakeAudio();

    await prepareClickAudioMode(audio);

    expect(modes).toHaveLength(1);
    expect(modes[0].playsInSilentMode).toBe(true);
    expect(modes[0].shouldPlayInBackground).toBe(false);
    // The click has to coexist with the track the user is playing along to.
    expect(modes[0].interruptionMode).toBe('mixWithOthers');
  });

  it('tolerates a module without setAudioModeAsync', async () => {
    const audio: AudioModuleLike = { createAudioPlayer: () => ({} as AudioPlayerLike) };
    await expect(prepareClickAudioMode(audio)).resolves.toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// The assets themselves
// ---------------------------------------------------------------------------

const AUDIO_DIR = join(__dirname, '..', '..', 'assets', 'audio');

describe('click assets', () => {
  const expected = {
    channels: 1,
    sampleRate: 44100,
    bitsPerSample: 16,
    durationMs: 35,
  };

  /** Minimal RIFF/WAVE reader — enough to prove the device gets decodable PCM. */
  function readWav(name: string) {
    const buffer = readFileSync(join(AUDIO_DIR, name));
    expect(buffer.toString('ascii', 0, 4)).toBe('RIFF');
    expect(buffer.toString('ascii', 8, 12)).toBe('WAVE');

    let offset = 12;
    let fmt: { audioFormat: number; channels: number; sampleRate: number; bitsPerSample: number } | null = null;
    let samples: number[] = [];

    while (offset + 8 <= buffer.length) {
      const id = buffer.toString('ascii', offset, offset + 4);
      const size = buffer.readUInt32LE(offset + 4);
      const body = offset + 8;
      if (id === 'fmt ') {
        fmt = {
          audioFormat: buffer.readUInt16LE(body),
          channels: buffer.readUInt16LE(body + 2),
          sampleRate: buffer.readUInt32LE(body + 4),
          bitsPerSample: buffer.readUInt16LE(body + 14),
        };
      } else if (id === 'data') {
        samples = [];
        for (let i = body; i + 1 < body + size; i += 2) samples.push(buffer.readInt16LE(i));
      }
      offset = body + size + (size % 2);
    }

    if (!fmt) throw new Error(`${name} has no fmt chunk`);
    return { fmt, samples };
  }

  it.each(['tick.wav', 'accent.wav'])('%s exists', (name) => {
    expect(existsSync(join(AUDIO_DIR, name))).toBe(true);
  });

  it.each(['tick.wav', 'accent.wav'])('%s is uncompressed PCM at the expected format', (name) => {
    const { fmt } = readWav(name);
    expect(fmt.audioFormat).toBe(1); // 1 = PCM. Anything else needs a decoder.
    expect(fmt.channels).toBe(expected.channels);
    expect(fmt.sampleRate).toBe(expected.sampleRate);
    expect(fmt.bitsPerSample).toBe(expected.bitsPerSample);
  });

  it.each(['tick.wav', 'accent.wav'])('%s is short enough never to overlap the next beat', (name) => {
    const { fmt, samples } = readWav(name);
    const durationMs = (samples.length / fmt.sampleRate) * 1000;
    expect(durationMs).toBeCloseTo(expected.durationMs, 0);
    // 300 BPM is the engine's ceiling — 200ms per beat. A click longer than
    // that would still be sounding when the next one fires.
    expect(durationMs).toBeLessThan(200);
  });

  it.each(['tick.wav', 'accent.wav'])('%s starts and ends at silence, so the click has no click', (name) => {
    // A non-zero first or last sample is a DC step: the speaker pops on top of
    // the tone, which at 120 BPM is a pop twice a second for 45 minutes.
    const { samples } = readWav(name);
    expect(samples[0]).toBe(0);
    expect(samples[samples.length - 1]).toBe(0);
  });

  it('gives the downbeat both a higher pitch and more level than the offbeat', () => {
    const tick = readWav('tick.wav');
    const accent = readWav('accent.wav');

    const peak = (s: number[]) => Math.max(...s.map(Math.abs));
    expect(peak(accent.samples)).toBeGreaterThan(peak(tick.samples));

    // Zero crossings stand in for pitch: the accent is the higher tone, so it
    // crosses zero more often over the same duration.
    const crossings = (s: number[]) =>
      s.reduce((n, v, i) => (i > 0 && Math.sign(v) !== Math.sign(s[i - 1]) ? n + 1 : n), 0);
    expect(crossings(accent.samples)).toBeGreaterThan(crossings(tick.samples));
  });
});
