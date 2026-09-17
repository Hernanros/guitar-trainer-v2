/**
 * Toolkit tab — the standalone metronome.
 *
 * The regression this guards: the metronome was reachable only from a drill
 * screen, so a spent breakdown quota also took the click away — the one feature
 * that needs no server at all was gated behind the one that costs the most.
 * These tests pin the properties that fix depends on: the tab renders a real
 * transport rather than a placeholder, the tempo is settable without a drill
 * ladder to supply it, and nothing here reaches for the network.
 *
 * Follows metronomeControl.render.test.tsx: `await render(...)`, query off the
 * returned view, and wrap presses in `act` — React 19 does not flush the state
 * update synchronously, so an unwrapped press asserts against the pre-press tree.
 */

import { act, fireEvent, render } from '@testing-library/react-native';
import ToolkitScreen, {
  nextBpm,
  formatBarLabel,
  DEFAULT_TOOLKIT_BPM,
  BPM_STEP,
  TEMPO_PRESETS,
  BAR_PRESETS,
} from '../../../src/app/(tabs)/toolkit';
import { MIN_BPM, MAX_BPM } from '../../../src/metronome/scheduler';
import { __resetAudioMock } from '../../../__mocks__/expo-audio';
import { __resetKeepAwakeMock } from '../../../__mocks__/expo-keep-awake';

beforeEach(() => {
  __resetAudioMock();
  __resetKeepAwakeMock();
});

type View = Awaited<ReturnType<typeof render>>;

async function press(view: View, label: string) {
  await act(async () => {
    fireEvent.press(view.getByLabelText(label) as never);
  });
}

/** The big number in the tempo readout — matched by testID because the preset
 *  chips carry the same digits. */
function readout(view: View): string {
  return view.getByTestId('toolkit-bpm-readout').props.children.toString();
}

describe('nextBpm', () => {
  it('steps up and down by the given delta', () => {
    expect(nextBpm(80, BPM_STEP)).toBe(85);
    expect(nextBpm(80, -BPM_STEP)).toBe(75);
  });

  it('clamps to the engine bounds rather than inventing its own', () => {
    expect(nextBpm(MAX_BPM, BPM_STEP)).toBe(MAX_BPM);
    expect(nextBpm(MIN_BPM, -BPM_STEP)).toBe(MIN_BPM);
  });

  it('returns a whole number, since the readout has no decimal place', () => {
    expect(Number.isInteger(nextBpm(80.4, 1))).toBe(true);
  });
});

describe('formatBarLabel', () => {
  it('reads as a time signature', () => {
    expect(formatBarLabel(4)).toBe('4/4');
    expect(formatBarLabel(3)).toBe('3/4');
  });
});

describe('ToolkitScreen', () => {
  it('renders the transport, not a "coming soon" placeholder', async () => {
    const view = await render(<ToolkitScreen />);
    expect(view.queryByText(/coming soon/i)).toBeNull();
    expect(view.getByText('Start click')).toBeTruthy();
  });

  it('opens on a usable default tempo', async () => {
    const view = await render(<ToolkitScreen />);
    expect(readout(view)).toBe(String(DEFAULT_TOOLKIT_BPM));
  });

  it('nudges the tempo in both directions', async () => {
    const view = await render(<ToolkitScreen />);

    await press(view, `Faster, ${BPM_STEP} BPM`);
    expect(readout(view)).toBe(String(DEFAULT_TOOLKIT_BPM + BPM_STEP));

    await press(view, `Slower, ${BPM_STEP} BPM`);
    expect(readout(view)).toBe(String(DEFAULT_TOOLKIT_BPM));
  });

  it('jumps to a preset tempo in one tap, and the click follows it', async () => {
    const view = await render(<ToolkitScreen />);
    await press(view, 'Set tempo to 160 BPM');

    expect(readout(view)).toBe('160');
    // MetronomeControl's own readout is the proof the prop reached the engine —
    // a screen that changes its number without moving the click is the bug.
    expect(view.getByText('160 BPM')).toBeTruthy();
  });

  it('offers every tempo and bar preset', async () => {
    const view = await render(<ToolkitScreen />);
    TEMPO_PRESETS.forEach((preset) => {
      expect(view.getByLabelText(`Set tempo to ${preset} BPM`)).toBeTruthy();
    });
    BAR_PRESETS.forEach((bar) => {
      expect(view.getByLabelText(formatBarLabel(bar))).toBeTruthy();
    });
  });

  it('switches the bar length', async () => {
    const view = await render(<ToolkitScreen />);
    await press(view, '3/4');

    expect(view.getByLabelText('3/4').props.accessibilityState.selected).toBe(true);
    expect(view.getByLabelText('4/4').props.accessibilityState.selected).toBe(false);
  });

  it('needs no network — the click must survive a spent quota', async () => {
    const fetchSpy = jest.fn();
    const original = globalThis.fetch;
    globalThis.fetch = fetchSpy as never;
    try {
      const view = await render(<ToolkitScreen />);
      await press(view, 'Set tempo to 120 BPM');
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = original;
    }
  });
});
