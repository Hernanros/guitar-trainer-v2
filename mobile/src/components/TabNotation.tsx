// mobile/src/components/TabNotation.tsx
// Hand-rolled react-native-svg tab staff — D-03 compliant.
// Phase 3 refactor: renders ALL measures with horizontal ScrollView + React.memo per Measure.
// Phase 1 rendered only measures[0]; Phase 3 extends for multi-measure scrolling (RESEARCH §2).
//
// CRITICAL: Avoid SVG masking primitives — per RESEARCH §8 landmine 9 (Windows perf regression).
// String lines drawn once for full staff width at the parent Svg level (RESEARCH §2 recommendation).
// React.memo per Measure prevents re-render storms when parent state changes (RESEARCH §2).
//
// Layout constants preserved from Phase 1 (must not change — ChordDiagram calibrates to these):
//   STRING_SPACING = 20, BEAT_WIDTH = 48, LEFT_MARGIN = 30, TOP_PADDING = 20, STRINGS = 6
import React from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { G, Line, Rect, Svg, Text as SvgText } from 'react-native-svg';
import type { components } from '../api/generated/schema';

type Tab = components['schemas']['Tab'];
type MeasureT = components['schemas']['Measure'];
type Note = components['schemas']['Note'];

// Layout constants — preserved verbatim from Phase 1 (PATTERNS.md lines 748-753)
const STRING_SPACING = 20;
const BEAT_WIDTH = 48;
const LEFT_MARGIN = 30;
const TOP_PADDING = 20;
const STRINGS = 6;

// Tuning awareness (Task #16 / quick 260909-01) — surface Sonnet's tuning choice
// so intermediate/advanced players can immediately see whether the tab is in the
// song's canonical tuning. Arrays MUST match server/app/ai/breakdown.py SYSTEM_PROMPT
// TUNING block exactly.
const STANDARD_TUNING = ['E', 'A', 'D', 'G', 'B', 'e'];
const KNOWN_TUNINGS: Record<string, string> = {
  // Half-step-down and whole-step-down (very common in classic rock/blues —
  // SRV, Hendrix, Slash, Van Halen, Alice in Chains, etc.)
  'Eb|Ab|Db|Gb|Bb|Eb': 'E♭ standard',
  'D|G|C|F|A|D': 'D standard',
  // Alternate / open tunings
  'E|B|E|G#|B|E': 'Open E',
  'D|A|D|F#|A|D': 'Open D',
  'D|G|D|G|B|D': 'Open G',
  'D|A|D|G|A|D': 'DADGAD',
  'D|A|D|G|B|E': 'Drop D',
  'C|G|C|F|A|D': 'Drop C',
};

function tuningDisplay(tuning: string[]): { friendly: string | null; raw: string } {
  const key = tuning.join('|');
  const friendly = KNOWN_TUNINGS[key] ?? null;
  const raw = tuning.join(' ');
  return { friendly, raw };
}

function isStandardTuning(tuning: string[] | null | undefined): boolean {
  if (!tuning || tuning.length !== 6) return true; // defensive: treat malformed as standard (no label)
  return tuning.every((s, i) => s === STANDARD_TUNING[i]);
}

// Phase 3: measure width = left margin + 4 beats wide + 8px right-pad between measures.
// Assumes 4 beats per measure (4/4 time is the overwhelming majority; 3/4 uses 3 × BEAT_WIDTH).
const MEASURE_WIDTH = LEFT_MARGIN + 4 * BEAT_WIDTH + 8;

// ---------------------------------------------------------------------------
// Measure component — memoized to prevent re-renders on parent state change
// ---------------------------------------------------------------------------

interface MeasureProps {
  measure: MeasureT;
  offsetX: number;
  measureIndex: number;
}

const Measure = React.memo(function Measure({ measure, offsetX, measureIndex }: MeasureProps) {
  const beats = measure.beats ?? [];
  return (
    <G>
      {/* Barline at the start of each measure (x = offsetX) */}
      {measureIndex > 0 && (
        <Line
          x1={offsetX + LEFT_MARGIN - 2}
          y1={0}
          x2={offsetX + LEFT_MARGIN - 2}
          y2={(STRINGS - 1) * STRING_SPACING}
          stroke="#555"
          strokeWidth={1}
        />
      )}
      {/* Fret numbers for each beat */}
      {beats.map((beat, beatIndex) => {
        const beatX = offsetX + LEFT_MARGIN + beatIndex * BEAT_WIDTH + BEAT_WIDTH / 2;
        return beat.notes.map((note: Note, noteIndex: number) => {
          // string 1 = high e = top line (y=0), string 6 = low E = bottom (y=5*STRING_SPACING)
          const y = (note.string - 1) * STRING_SPACING;
          const fretLabel = note.fret === -1 ? 'x' : String(note.fret);
          const rectWidth = fretLabel.length > 1 ? 16 : 12;

          return (
            <G key={`m${measureIndex}-b${beatIndex}-n${noteIndex}`}>
              {/* Background rect to occlude the string line behind the fret number */}
              <Rect
                x={beatX - rectWidth / 2}
                y={y - 7}
                width={rectWidth}
                height={13}
                fill="#1A1A1A"
              />
              <SvgText
                x={beatX}
                y={y + 4}
                textAnchor="middle"
                fontSize={12}
                fill="#F5F5F5"
                fontWeight="600"
              >
                {fretLabel}
              </SvgText>
            </G>
          );
        });
      })}
    </G>
  );
});

// ---------------------------------------------------------------------------
// TabNotation — outer component with horizontal ScrollView
// ---------------------------------------------------------------------------

export function TabNotation({ tab }: { tab: Tab }) {
  const tuning = tab.tuning ?? STANDARD_TUNING;

  // String labels: tuning is low-to-high [E, A, D, G, B, e].
  // Reverse so index 0 = high e (top line) and index 5 = low E (bottom line).
  const stringLabels = [...tuning].reverse();

  const staffHeight = (STRINGS - 1) * STRING_SPACING;
  const svgHeight = staffHeight + TOP_PADDING * 2;
  const totalWidth = LEFT_MARGIN + (tab.measures?.length ?? 0) * MEASURE_WIDTH + 16;

  // Tuning label — render only when Sonnet chose a non-standard tuning.
  // Standard / null / undefined / malformed tuning renders no label (defensive).
  const isNonStandard = !isStandardTuning(tab.tuning);
  const { friendly, raw } = isNonStandard
    ? tuningDisplay(tuning)
    : { friendly: null, raw: '' };

  return (
    <View style={styles.wrapper}>
      {isNonStandard && (
        <Text style={styles.tuningLabel}>
          <Text style={styles.tuningPrefix}>Tuning: </Text>
          {friendly ? (
            <>
              <Text style={styles.tuningValue}>{friendly}</Text>
              <Text style={styles.tuningRaw}>{` — ${raw}`}</Text>
            </>
          ) : (
            <Text style={styles.tuningValue}>{raw}</Text>
          )}
        </Text>
      )}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={true}
        contentContainerStyle={styles.scrollContent}
      >
        <Svg width={totalWidth} height={svgHeight}>
          <G translateY={TOP_PADDING}>
            {/* String lines drawn ONCE for the full staff width (RESEARCH §2 recommendation) */}
            {Array.from({ length: STRINGS }).map((_, i) => (
              <Line
                key={`str-${i}`}
                x1={LEFT_MARGIN - 4}
                y1={i * STRING_SPACING}
                x2={totalWidth - 8}
                y2={i * STRING_SPACING}
                stroke="#999"
                strokeWidth={1}
              />
            ))}

            {/* String name labels on the left (tuning high-to-low) */}
            {stringLabels.map((label, i) => (
              <SvgText
                key={`label-${i}`}
                x={LEFT_MARGIN - 8}
                y={i * STRING_SPACING + 4}
                textAnchor="end"
                fontSize={10}
                fill="#999"
              >
                {label}
              </SvgText>
            ))}

            {/* Measures — memoized per React.memo(Measure); tab.measures.map loops ALL measures */}
            {tab.measures.map((measure, i) => (
              <Measure
                key={i}
                measure={measure}
                offsetX={i * MEASURE_WIDTH}
                measureIndex={i}
              />
            ))}
          </G>
        </Svg>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    marginVertical: 4,
  },
  scrollContent: {
    flexGrow: 0,
  },
  tuningLabel: {
    marginBottom: 6,
    fontSize: 12,
  },
  tuningPrefix: {
    color: '#999',
    fontSize: 12,
  },
  tuningValue: {
    color: '#E07B39',
    fontSize: 12,
    fontWeight: '600',
  },
  tuningRaw: {
    color: '#999',
    fontSize: 12,
  },
});
