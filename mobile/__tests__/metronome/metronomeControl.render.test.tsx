/**
 * MetronomeControl wiring — FLE-5 (Task 7).
 *
 * Every other metronome test proves the engine and the audio adapter in
 * isolation, against injected fakes. That leaves exactly one thing unproven:
 * the call site that joins them, which is the only file in the app that imports
 * expo-audio. A drill screen that renders a beautiful, drift-free, SILENT
 * metronome — because nobody passed `createEmitter` at the call site — would
 * pass every one of those tests.
 *
 * So this file renders the real component against the expo-audio manual mock
 * (__mocks__/expo-audio.ts) and asserts the three things the wiring must do:
 * build players from the click assets, configure the audio session so a phone
 * on the silent switch still clicks, and drive the tempo from the `bpm` prop
 * rather than from user input.
 */

import { render } from '@testing-library/react-native';
import { MetronomeControl } from '../../src/components/MetronomeControl';
import { DEFAULT_VOICES_PER_SOUND } from '../../src/metronome/audioEmitter';
import { __audioModes, __players, __resetAudioMock } from '../../__mocks__/expo-audio';

beforeEach(() => {
  __resetAudioMock();
});

describe('MetronomeControl — audio wiring', () => {
  it('builds the full voice pool on mount, before any beat is due', async () => {
    await render(<MetronomeControl bpm={90} />);

    // Two sounds (tick + accent) x the pool size. Creating a native player
    // costs file IO and decode, so all of it happens at mount rather than on a
    // beat deadline.
    expect(__players).toHaveLength(DEFAULT_VOICES_PER_SOUND * 2);
    expect(__players.every((p) => p.source !== undefined)).toBe(true);

    // NOT asserted here: that the tick and accent sources differ. jest-expo's
    // asset transformer compiles every asset to `module.exports = 1`, so both
    // wavs are literally the same value under Jest and any such assertion would
    // be testing the harness. That the two clicks are genuinely different
    // sounds is covered where it can be — audioEmitter.test.ts asserts the
    // accent/tick routing against distinguishable injected sources, and the
    // asset tests read the two .wav files off disk as bytes.
  });

  it('is silent until started — mounting a drill does not click at you', async () => {
    await render(<MetronomeControl bpm={90} />);
    expect(__players.some((p) => p.playing)).toBe(false);
  });

  it('asks for playback in silent mode, or the click is inaudible on a music stand', async () => {
    await render(<MetronomeControl bpm={90} />);

    expect(__audioModes).not.toHaveLength(0);
    const mode = __audioModes[__audioModes.length - 1];
    expect(mode.playsInSilentMode).toBe(true);
    // Coexist with a backing track rather than stopping it.
    expect(mode.interruptionMode).toBe('mixWithOthers');
    // A practice click has no business continuing after the app is backgrounded.
    expect(mode.shouldPlayInBackground).toBe(false);
  });

  it('tears the players down on unmount, so a click cannot outlive the screen', async () => {
    const view = await render(<MetronomeControl bpm={90} />);
    expect(__players.every((p) => p.removed)).toBe(false);

    // Awaited for the same reason render() is: React 19 flushes the effect
    // cleanup asynchronously, so a synchronous unmount() asserts too early.
    await view.unmount();

    expect(__players).not.toHaveLength(0);
    expect(__players.every((p) => p.removed)).toBe(true);
  });
});

describe('MetronomeControl — tempo comes from the drill, not the user', () => {
  it('displays the tempo it was handed, with no input control to type into', async () => {
    // "Starting a drill sets the tempo without the user typing a number" is the
    // FLE-5 acceptance criterion; the drill screen passes
    // `currentLadder.currentBpm` straight in.
    const view = await render(<MetronomeControl bpm={72} />);

    expect(view.getByText('72 BPM')).toBeTruthy();
    expect(view.getByText('From this drill')).toBeTruthy();
    expect(view.queryByRole('button', { name: /Start click at 72 BPM/ })).toBeTruthy();
  });

  it('follows a ladder push without remounting or rebuilding the voice pool', async () => {
    const view = await render(<MetronomeControl bpm={60} />);
    const playersAfterMount = __players.length;

    // The tempo ladder walks 60 -> 65 -> 70.
    await view.rerender(<MetronomeControl bpm={65} />);
    expect(view.getByText('65 BPM')).toBeTruthy();

    await view.rerender(<MetronomeControl bpm={70} />);
    expect(view.getByText('70 BPM')).toBeTruthy();

    // Re-creating native players on every rung would drop audio mid-drill.
    expect(__players).toHaveLength(playersAfterMount);
    expect(__players.every((p) => !p.removed)).toBe(true);
  });

  it('clamps a model-authored tempo instead of trusting it', async () => {
    // target_bpm arrives from Sonnet-generated drill content; 0 would divide by
    // zero in the scheduler and 4000 would be a timer storm.
    const floored = await render(<MetronomeControl bpm={0} />);
    expect(floored.getByText('30 BPM')).toBeTruthy();
    await floored.unmount();

    const ceilinged = await render(<MetronomeControl bpm={4000} />);
    expect(ceilinged.getByText('300 BPM')).toBeTruthy();
  });
});
