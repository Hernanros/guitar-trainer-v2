// mobile/src/components/TabNotation.tsx
// Hand-rolled react-native-svg tab staff — D-03 compliant.
// Renders measures[0] in Phase 1; Phase 3 will handle multi-measure scrolling.
// Accepts a Tab from the generated schema (Pydantic → OpenAPI → openapi-typescript).
import React from 'react';
import { View, StyleSheet } from 'react-native';
import { Svg, G, Line, Text, Rect } from 'react-native-svg';
import type { components } from '../api/generated/schema';

type Tab = components['schemas']['Tab'];
type Note = components['schemas']['Note'];

// Layout constants
const STRING_SPACING = 20;
const BEAT_WIDTH = 48;
const LEFT_MARGIN = 30;
const TOP_PADDING = 20;
const STRINGS = 6;

export function TabNotation({ tab }: { tab: Tab }) {
  // Render measures[0] only in Phase 1; guard against empty measures
  const measure = tab.measures?.[0];
  const beats = measure?.beats ?? [];
  const tuning = tab.tuning ?? ['E', 'A', 'D', 'G', 'B', 'e'];

  // String labels: tuning is low-to-high [E, A, D, G, B, e].
  // Reverse so index 0 = high e (top line) and index 5 = low E (bottom line).
  const stringLabels = [...tuning].reverse();

  const staffHeight = (STRINGS - 1) * STRING_SPACING;
  const staffWidth = LEFT_MARGIN + beats.length * BEAT_WIDTH + 20;
  const svgWidth = staffWidth;
  const svgHeight = staffHeight + TOP_PADDING * 2;

  return (
    <View style={styles.wrapper}>
      <Svg width={svgWidth} height={svgHeight}>
        <G translateX={0} translateY={TOP_PADDING}>
          {/* Six horizontal string lines */}
          {Array.from({ length: STRINGS }).map((_, i) => (
            <Line
              key={`str-${i}`}
              x1={LEFT_MARGIN - 4}
              y1={i * STRING_SPACING}
              x2={staffWidth - 4}
              y2={i * STRING_SPACING}
              stroke="#999"
              strokeWidth={1}
            />
          ))}

          {/* String name labels on the left */}
          {stringLabels.map((label, i) => (
            <Text
              key={`label-${i}`}
              x={LEFT_MARGIN - 8}
              y={i * STRING_SPACING + 4}
              textAnchor="end"
              fontSize={10}
              fill="#999"
            >
              {label}
            </Text>
          ))}

          {/* Fret numbers for each beat */}
          {beats.map((beat, beatIndex) => {
            const beatX = LEFT_MARGIN + beatIndex * BEAT_WIDTH + BEAT_WIDTH / 2;
            return beat.notes.map((note: Note, noteIndex: number) => {
              // string 1 = high e = top line (y=0), string 6 = low E = bottom (y=5*STRING_SPACING)
              const y = (note.string - 1) * STRING_SPACING;
              const fretLabel = String(note.fret);
              const rectWidth = fretLabel.length > 1 ? 16 : 12;

              return (
                <G key={`beat-${beatIndex}-note-${noteIndex}`}>
                  {/* White background rect to occlude the string line behind the number */}
                  <Rect
                    x={beatX - rectWidth / 2}
                    y={y - 7}
                    width={rectWidth}
                    height={13}
                    fill="#1A1A1A"
                  />
                  <Text
                    x={beatX}
                    y={y + 4}
                    textAnchor="middle"
                    fontSize={12}
                    fill="#F5F5F5"
                    fontWeight="600"
                  >
                    {fretLabel}
                  </Text>
                </G>
              );
            });
          })}
        </G>
      </Svg>
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    marginVertical: 4,
  },
});
