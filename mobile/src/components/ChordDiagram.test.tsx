/**
 * ChordDiagram tests — FLE-105 §7.2 / FLE-116.
 *
 * Only the new `sounding_name` glyph. The rest of the component (fret dots,
 * open/muted markers, base-fret label) predates this schema change and has no
 * dedicated coverage to extend here.
 */

import { render } from '@testing-library/react-native';
import type { components } from '../api/generated/schema';
import { ChordDiagram } from './ChordDiagram';

type Chord = components['schemas']['Chord'];

const baseChord = {
  name: 'Em7',
  positions: [{ string: 6, fret: 0, finger: null }],
  base_fret: 1,
} as Chord;

// react-native-svg's <Text> puts its glyphs in a `content` prop on the host node rather than in
// children, so RNTL's getByText (which matches React Native `Text` nodes) cannot see it — same
// issue TabNotation.test.tsx documents and works around.
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

describe('ChordDiagram sounding_name', () => {
  it('renders the shape name full size and the sounding name beneath it', async () => {
    const chord = { ...baseChord, sounding_name: 'F#m7' } as Chord;
    const view = await render(<ChordDiagram chord={chord} />);
    const labels = renderedStrings(view);

    expect(labels).toContain('Em7');
    expect(labels).toContain('sounds F#m7');
  });

  it('draws no sounding-name row at capo 0, where the field is unset', async () => {
    const view = await render(<ChordDiagram chord={baseChord} />);
    const labels = renderedStrings(view);

    expect(labels).toContain('Em7');
    expect(labels.some((l) => l.startsWith('sounds '))).toBe(false);
  });
});
