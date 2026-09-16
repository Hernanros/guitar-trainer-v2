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
 * (__mocks__/expo-audio.ts) and asserts what the wiring must do: build players
 * from the click assets, configure the audio session so a phone on the silent
 * switch still clicks, drive the tempo from the `bpm` prop rather than from
 * user input, hold the screen awake for the length of the run, and surface the
 * timing diagnostics the hardware walk is judged on.
 */

import { act, fireEvent, render } from '@testing-library/react-native';
import { MetronomeControl, TIMING_TOGGLE_HINT } from '../../src/components/MetronomeControl';
import { DEFAULT_VOICES_PER_SOUND } from '../../src/metronome/audioEmitter';
import { __audioModes, __players, __resetAudioMock } from '../../__mocks__/expo-audio';
import {
  __activated,
  __deactivated,
  __resetKeepAwakeMock,
} from '../../__mocks__/expo-keep-awake';

beforeEach(() => {
  __resetAudioMock();
  __resetKeepAwakeMock();
});

/** Presses the transport and lets the resulting effects flush. */
async function pressToggle(view: { getByRole: (r: string, o: { name: RegExp }) => unknown }) {
  await act(async () => {
    fireEvent.press(view.getByRole('button', { name: /click at/ }) as never);
  });
}

/**
 * Long-presses the tempo readout to toggle the timing diagnostics.
 *
 * Wrapped in `act` for the same reason pressToggle is. A bare
 * `fireEvent(el, 'longPress')` leaves the resulting state update unflushed, so
 * the panel never appears AND the next test in the file fails to find the
 * readout at all — the failure surfaces one test later than its cause.
 */
async function longPressReadout(
  view: { getByRole: (r: string, o: { name: string }) => unknown },
  bpmLabel: string,
) {
  await act(async () => {
    fireEvent(view.getByRole('button', { name: bpmLabel }) as never, 'longPress');
  });
}

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

describe('MetronomeControl — the screen stays awake for the length of the run', () => {
  // The Done-when is "holds tempo over a FULL SESSION-LENGTH run" on hardware.
  // iOS auto-lock defaults to 30s; on lock the app suspends, JS timers stop and
  // the click dies. Without this the drift math is irrelevant — a 45-minute
  // device walk fails in the first minute and burns a scarce EAS build slot to
  // discover something no simulator would ever have shown.

  it('takes no wake lock until the click is actually running', async () => {
    await render(<MetronomeControl bpm={30} />);
    // A drill screen sitting idle with the click stopped must still auto-lock.
    expect(__activated).toHaveLength(0);
  });

  it('holds the screen awake once started', async () => {
    const view = await render(<MetronomeControl bpm={30} />);
    await pressToggle(view);

    expect(__activated).toEqual(['metronome-click']);
    expect(__deactivated).toHaveLength(0);

    await view.unmount();
  });

  it('releases the lock when the click stops — the battery cost is bounded by the click', async () => {
    const view = await render(<MetronomeControl bpm={30} />);
    await pressToggle(view); // start
    await pressToggle(view); // stop

    // Same tag both ways: expo-keep-awake reference-counts by tag, so releasing
    // an untagged lock here would leave this one held forever.
    expect(__activated).toEqual(['metronome-click']);
    expect(__deactivated).toEqual(['metronome-click']);

    await view.unmount();
  });

  it('releases the lock on unmount, so leaving the drill mid-click cannot pin the screen', async () => {
    const view = await render(<MetronomeControl bpm={30} />);
    await pressToggle(view);
    expect(__deactivated).toHaveLength(0);

    // Navigating away mid-drill is the common case, not an edge case.
    await view.unmount();

    expect(__deactivated).toEqual(['metronome-click']);
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

  it('leaves the tempo readout as a display, not an input, even though it is now pressable', async () => {
    // The readout gained a long-press (timing diagnostics), which makes it a
    // button. The acceptance criterion is about not TYPING a tempo, so the
    // thing to pin is that pressing it cannot change the tempo.
    const view = await render(<MetronomeControl bpm={72} />);

    await act(async () => {
      fireEvent.press(view.getByRole('button', { name: '72 BPM' }) as never);
    });
    await longPressReadout(view, '72 BPM');

    expect(view.getByText('72 BPM')).toBeTruthy();
    expect(view.queryByRole('slider')).toBeNull();
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

describe('MetronomeControl — timing diagnostics', () => {
  // The Done-when is verified by a human with a phone on a music stand. Until
  // this readout existed, their only instrument was a second metronome app and
  // an ear, which cannot separate an accumulating grid from per-beat jitter
  // from the OS suspending the app — three different bugs that all sound like
  // "it drifted" — and leaves no number behind for whoever has to fix it.
  //
  // What these tests pin is the seam no unit test can reach: that the numbers
  // are actually REACHABLE on a device, and that they are not shipped in the
  // face of a guitarist who just wants a tempo.

  /** The diagnostics line is monospaced; matching on its shape, not its values. */
  const TIMING_LINE = /beats? · grid /;

  it('ships hidden — a practicing guitarist gets a tempo, not telemetry', async () => {
    const view = await render(<MetronomeControl bpm={90} />);

    expect(view.queryByText(TIMING_LINE)).toBeNull();
    expect(view.queryByText(/max jitter/)).toBeNull();
  });

  it('is reachable by long-pressing the tempo readout', async () => {
    // Long press, not tap: the tester must be able to open this mid-walk, and
    // nobody reaching for the transport must open it by accident.
    const view = await render(<MetronomeControl bpm={90} />);

    await longPressReadout(view, '90 BPM');

    // Nothing has run yet, so it must say so rather than render a clean pass.
    expect(view.getByText('No beats yet')).toBeTruthy();
  });

  it('advertises itself to a screen reader, since nothing on screen hints at it', async () => {
    const view = await render(<MetronomeControl bpm={90} />);
    const readout = view.getByRole('button', { name: '90 BPM' });

    expect(readout.props.accessibilityHint).toBe(TIMING_TOGGLE_HINT);
  });

  it('reports the run once beats have fired', async () => {
    const view = await render(<MetronomeControl bpm={90} />);

    await pressToggle(view); // beat 0 fires on start, synchronously
    await longPressReadout(view, '90 BPM');

    // Singular, because "1 beats" is the kind of thing a tester screenshots.
    expect(view.getByText(/1 beat · grid \+0ms/)).toBeTruthy();
    expect(view.getByText(/0 skipped · 0 dropped/)).toBeTruthy();
    // Jitter in ms is unjudgeable without the tempo beside it.
    expect(view.getByText(/max jitter 0\.0% of a beat/)).toBeTruthy();

    await view.unmount();
  });

  it('survives the stop, because the tester reads the result off a stopped screen', async () => {
    const view = await render(<MetronomeControl bpm={90} />);

    await pressToggle(view); // start
    await longPressReadout(view, '90 BPM');
    await pressToggle(view); // stop, 45 minutes later

    // stop() clears the current beat; it must not clear the measurement.
    expect(view.queryByText('No beats yet')).toBeNull();
    expect(view.getByText(/1 beat · grid/)).toBeTruthy();

    await view.unmount();
  });

  it('starts a fresh measurement on each run, so a stray tap cannot poison a walk', async () => {
    const view = await render(<MetronomeControl bpm={90} />);
    await longPressReadout(view, '90 BPM');

    await pressToggle(view); // a stray tap
    await pressToggle(view);
    expect(view.getByText(/1 beat · grid/)).toBeTruthy();

    await pressToggle(view); // the real run begins
    // Still 1 beat, not 2 — the counter reset rather than carrying the tap in.
    expect(view.getByText(/1 beat · grid/)).toBeTruthy();

    await view.unmount();
  });

  it('closes again, so it cannot be left on over a drill', async () => {
    const view = await render(<MetronomeControl bpm={90} />);

    await longPressReadout(view, '90 BPM');
    expect(view.getByText('No beats yet')).toBeTruthy();

    await longPressReadout(view, '90 BPM');
    expect(view.queryByText('No beats yet')).toBeNull();
  });
});
