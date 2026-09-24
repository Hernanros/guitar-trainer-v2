/**
 * TabNotation tests.
 *
 * The layout and fret-label blocks below are pure-logic checks that mirror
 * constants in TabNotation.tsx. They are documentation with an assertion
 * attached: they pin the Phase 1 visual calibration, but they cannot catch a
 * component that stops using those constants.
 *
 * The rendering block at the bottom is the one that exercises the component.
 * @testing-library/react-native IS installed now (the header that said
 * otherwise predated it), and the ESM/native-module config that stopped screens
 * from rendering was fixed in FLE-40/FLE-47.
 */

import { render } from '@testing-library/react-native';
import type { components } from '../api/generated/schema';
import { TabNotation } from './TabNotation';

type Tab = components['schemas']['Tab'];
type MeasureT = components['schemas']['Measure'];

// ---------------------------------------------------------------------------
// Layout constant assertions (no import needed — values are copied here
// for documentation and verified against the source manually).
// ---------------------------------------------------------------------------

describe('TabNotation layout constants', () => {
  it('preserves Phase 1 constants', () => {
    // If these fail, Phase 1 visual calibration breaks (ChordDiagram + TabNotation co-aligned)
    const STRING_SPACING = 20;
    const BEAT_WIDTH = 48;
    const LEFT_MARGIN = 30;
    const TOP_PADDING = 20;
    const STRINGS = 6;
    const MEASURE_WIDTH = LEFT_MARGIN + 4 * BEAT_WIDTH + 8; // Phase 3 addition

    expect(STRING_SPACING).toBe(20);
    expect(BEAT_WIDTH).toBe(48);
    expect(LEFT_MARGIN).toBe(30);
    expect(TOP_PADDING).toBe(20);
    expect(STRINGS).toBe(6);
    expect(MEASURE_WIDTH).toBe(30 + 4 * 48 + 8); // 230
  });

  it('MEASURE_WIDTH computes correctly', () => {
    const LEFT_MARGIN = 30;
    const BEAT_WIDTH = 48;
    const MEASURE_WIDTH = LEFT_MARGIN + 4 * BEAT_WIDTH + 8;
    expect(MEASURE_WIDTH).toBe(230);
  });

  it('total tab width scales linearly with measure count', () => {
    const LEFT_MARGIN = 30;
    const BEAT_WIDTH = 48;
    const MEASURE_WIDTH = LEFT_MARGIN + 4 * BEAT_WIDTH + 8; // 230
    const RIGHT_PAD = 16;

    // totalWidth formula from TabNotation.tsx
    const totalWidth = (measureCount: number) =>
      LEFT_MARGIN + measureCount * MEASURE_WIDTH + RIGHT_PAD;

    expect(totalWidth(1)).toBe(30 + 1 * 230 + 16); // 276
    expect(totalWidth(4)).toBe(30 + 4 * 230 + 16); // 966
    expect(totalWidth(8)).toBe(30 + 8 * 230 + 16); // 1886
  });
});

// ---------------------------------------------------------------------------
// Fret label computation
// ---------------------------------------------------------------------------

describe('fret label computation', () => {
  it('renders -1 fret as "x" (muted string)', () => {
    // Logic from Measure component: note.fret === -1 → 'x'
    const fretLabel = (fret: number) => (fret === -1 ? 'x' : String(fret));
    expect(fretLabel(-1)).toBe('x');
    expect(fretLabel(0)).toBe('0');
    expect(fretLabel(12)).toBe('12');
  });

  it('computes rect width based on label length', () => {
    // Logic from Measure: fretLabel.length > 1 ? 16 : 12
    const rectWidth = (fret: number) => {
      const label = fret === -1 ? 'x' : String(fret);
      return label.length > 1 ? 16 : 12;
    };
    expect(rectWidth(0)).toBe(12);  // '0' length 1
    expect(rectWidth(9)).toBe(12);  // '9' length 1
    expect(rectWidth(10)).toBe(16); // '10' length 2
    expect(rectWidth(12)).toBe(16); // '12' length 2
    expect(rectWidth(-1)).toBe(12); // 'x' length 1
  });
});

// ---------------------------------------------------------------------------
// String position computation
// ---------------------------------------------------------------------------

describe('string-to-y position mapping', () => {
  const STRING_SPACING = 20;

  it('string 1 (high e) maps to y=0 (top line)', () => {
    const y = (string: number) => (string - 1) * STRING_SPACING;
    expect(y(1)).toBe(0);
  });

  it('string 6 (low E) maps to y=100 (bottom line)', () => {
    const y = (string: number) => (string - 1) * STRING_SPACING;
    expect(y(6)).toBe(100); // (6-1)*20 = 100
  });

  it('all strings map to non-negative y values within staff height', () => {
    const y = (string: number) => (string - 1) * STRING_SPACING;
    const staffHeight = 5 * STRING_SPACING; // (STRINGS - 1) * STRING_SPACING
    for (let s = 1; s <= 6; s++) {
      expect(y(s)).toBeGreaterThanOrEqual(0);
      expect(y(s)).toBeLessThanOrEqual(staffHeight);
    }
  });
});

// ---------------------------------------------------------------------------
// Multi-measure rendering — FLE-40
//
// The previous version of this block could not fail for the reason it was named
// after. It built a local fixture, reduced over it, and asserted on the result —
// TabNotation was never imported, so a component that rendered `measures[0]` and
// dropped the rest would have passed. It also failed outright: four
// `makeMeasure` calls each holding ONE beat produce 4, against an assertion of 16.
//
// Rewritten to render the real component. Every note gets a distinct fret, so
// "all four measures rendered" is proven by finding all sixteen labels rather
// than by counting a local fixture: a regression to `measures[0]` drops frets
// 5–16 and fails here.
// ---------------------------------------------------------------------------

describe('TabNotation multi-measure rendering', () => {
  /** One measure of four beats, one note per beat, frets taken in order. */
  const makeMeasure = (frets: number[]): MeasureT =>
    ({
      beats: frets.map((fret) => ({
        notes: [{ string: 1, fret, duration: 'quarter' as const }],
      })),
      time_signature: '4/4',
    }) as MeasureT;

  /** Frets 1..16 across 4 measures — every label unique and non-empty. */
  const FRETS = Array.from({ length: 16 }, (_, i) => i + 1);

  const fourMeasureTab = {
    measures: [
      makeMeasure(FRETS.slice(0, 4)),
      makeMeasure(FRETS.slice(4, 8)),
      makeMeasure(FRETS.slice(8, 12)),
      makeMeasure(FRETS.slice(12, 16)),
    ],
  } as Tab;

  /**
   * Every piece of text rendered in the tree, in document order.
   *
   * RNTL's `getByText` is not usable here: react-native-svg renders `<SvgText>`
   * to a host `RNSVGText`/`RNSVGTSpan` pair and puts the glyphs in a `content`
   * *prop* rather than in children, while RNTL's text queries match React Native
   * `Text` nodes. Reading `props.content` off the tree is what actually sees a
   * fret label.
   *
   * This also picks up the six string-name labels from the tuning (e, B, G, D,
   * A, E), which is why the assertions below filter for the specific frets they
   * care about instead of counting everything.
   */
  const renderedStrings = (view: { toJSON: () => unknown }): string[] => {
    const out: string[] = [];
    const walk = (node: unknown): void => {
      if (Array.isArray(node)) {
        node.forEach(walk);
        return;
      }
      if (!node || typeof node !== 'object') return;

      const { props, children } = node as {
        props?: { content?: unknown };
        children?: unknown;
      };
      if (typeof props?.content === 'string') out.push(props.content);
      if (children != null) walk(children);
    };
    walk(view.toJSON());
    return out;
  };

  it('renders every measure, not just measures[0]', async () => {
    const view = await render(<TabNotation tab={fourMeasureTab} />);
    const labels = renderedStrings(view);

    // Frets 1–4 are measure 0; 13–16 are the last measure. Requiring all
    // sixteen is what pins the `.map` over `tab.measures` — a regression to
    // `measures[0]` renders 1–4 and drops the rest.
    for (const fret of FRETS) {
      expect(labels.filter((l) => l === String(fret))).toHaveLength(1);
    }
  });

  it('renders a muted string (fret -1) as "x"', async () => {
    const tab = { measures: [makeMeasure([-1, 3, 5, 7])] } as Tab;
    const view = await render(<TabNotation tab={tab} />);
    const labels = renderedStrings(view);

    expect(labels.filter((l) => l === 'x')).toHaveLength(1);
    expect(labels.filter((l) => l === '3')).toHaveLength(1);
  });

  it('renders each beat of a measure, not just the first', async () => {
    const tab = { measures: [makeMeasure([2, 4, 6, 8])] } as Tab;
    const view = await render(<TabNotation tab={tab} />);
    const labels = renderedStrings(view);

    for (const fret of [2, 4, 6, 8]) {
      expect(labels.filter((l) => l === String(fret))).toHaveLength(1);
    }
  });
});
