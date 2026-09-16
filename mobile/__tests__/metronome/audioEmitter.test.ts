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
  DEFAULT_VOICES_PER_SOUND,
  createAudioClickEmitter,
  prepareClickAudioMode,
} from '../../src/metronome/audioEmitter';
import type { AudioModuleLike, AudioPlayerLike } from '../../src/metronome/audioEmitter';
import type { MetronomeBeat } from '../../src/metronome/types';
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
        seekTo(seconds: number) {
          player.calls.push(`seekTo(${seconds})`);
          return Promise.resolve();
        },
        play() {
          if (player.failOnPlay) throw new Error('native player exploded');
          player.calls.push('play');
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
  it('rewinds before playing, so a retrigger starts at the transient', () => {
    const { audio, played } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES);

    emitter.emit(beat());

    expect(played()).toHaveLength(1);
    // Order matters: play-then-seek would cut the attack off the click.
    expect(played()[0].calls).toEqual(['seekTo(0)', 'play']);
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

  it('cycles voices so a click never retriggers the player used on the previous beat', () => {
    // This is the whole reason the pool exists: seekTo() is async, so reusing
    // one player makes each click race the rewind of the click before it.
    const { audio, forSource } = createFakeAudio();
    const emitter = createAudioClickEmitter(audio, SOURCES, { voicesPerSound: 2 });

    for (let i = 1; i <= 4; i += 1) emitter.emit(beat({ beatIndex: i, barBeat: i % 4 || 1 }));

    const ticks = forSource('tick.wav');
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
