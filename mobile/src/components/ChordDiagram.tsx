// mobile/src/components/ChordDiagram.tsx
// Hand-rolled react-native-svg chord diagram — D-03 compliant.
// Accepts a Chord from the generated schema (Pydantic → OpenAPI → openapi-typescript).
// No third-party guitar chord library — see RESEARCH.md "Don't Hand-Roll" section.
import React from 'react';
import { View, StyleSheet } from 'react-native';
import { Svg, G, Line, Circle, Text, Rect } from 'react-native-svg';
import type { components } from '../api/generated/schema';

type Chord = components['schemas']['Chord'];
type ChordPosition = components['schemas']['ChordPosition'];

// Layout constants — from RESEARCH.md Pattern 9
const STRING_SPACING = 24;
const FRET_SPACING = 28;
const STRINGS = 6;
const FRETS_SHOWN = 4;
const PADDING_LEFT = 20;
const PADDING_TOP = 36; // extra top padding for chord name + open/muted markers

export function ChordDiagram({ chord }: { chord: Chord }) {
  const gridWidth = (STRINGS - 1) * STRING_SPACING;
  const gridHeight = FRETS_SHOWN * FRET_SPACING;
  const svgWidth = gridWidth + PADDING_LEFT * 2;
  const svgHeight = gridHeight + PADDING_TOP + 20; // 20px bottom padding

  return (
    <View style={styles.wrapper}>
      <Svg width={svgWidth} height={svgHeight}>
        <G translateX={PADDING_LEFT} translateY={PADDING_TOP}>
          {/* Chord name label */}
          <Text
            x={gridWidth / 2}
            y={-22}
            textAnchor="middle"
            fontSize={14}
            fontWeight="bold"
            fill="#F5F5F5"
          >
            {chord.name}
          </Text>

          {/* Base fret label (only when chord window starts above fret 1) */}
          {chord.base_fret > 1 && (
            <Text
              x={gridWidth + 6}
              y={FRET_SPACING / 2 + 4}
              textAnchor="start"
              fontSize={11}
              fill="#999"
            >
              {chord.base_fret}fr
            </Text>
          )}

          {/* Fret lines */}
          {Array.from({ length: FRETS_SHOWN + 1 }).map((_, i) => (
            <Line
              key={`fret-${i}`}
              x1={0}
              y1={i * FRET_SPACING}
              x2={gridWidth}
              y2={i * FRET_SPACING}
              stroke="#F5F5F5"
              strokeWidth={i === 0 ? 3 : 1}
            />
          ))}

          {/* String lines (vertical) */}
          {Array.from({ length: STRINGS }).map((_, i) => (
            <Line
              key={`string-${i}`}
              x1={i * STRING_SPACING}
              y1={0}
              x2={i * STRING_SPACING}
              y2={gridHeight}
              stroke="#F5F5F5"
              strokeWidth={1}
            />
          ))}

          {/* Open and muted markers above the nut, and finger dots on the grid */}
          {chord.positions.map((pos: ChordPosition, idx: number) => {
            const x = (pos.string - 1) * STRING_SPACING;

            if (pos.fret === -1) {
              // Muted string: render × above the nut
              return (
                <Text
                  key={`mute-${idx}`}
                  x={x}
                  y={-8}
                  textAnchor="middle"
                  fontSize={13}
                  fill="#999"
                >
                  ×
                </Text>
              );
            }

            if (pos.fret === 0) {
              // Open string: render ○ above the nut
              return (
                <Circle
                  key={`open-${idx}`}
                  cx={x}
                  cy={-11}
                  r={5}
                  stroke="#F5F5F5"
                  strokeWidth={1.5}
                  fill="none"
                />
              );
            }

            // Fretted note: filled dot at correct fret position
            const fretIndex = pos.fret - chord.base_fret;
            if (fretIndex < 0 || fretIndex >= FRETS_SHOWN) {
              // Out-of-window fret — skip to avoid rendering off-grid
              return null;
            }
            const y = fretIndex * FRET_SPACING + FRET_SPACING / 2;
            return (
              <Circle
                key={`dot-${idx}`}
                cx={x}
                cy={y}
                r={10}
                fill="#E07B39"
              />
            );
          })}
        </G>
      </Svg>
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    marginRight: 8,
  },
});
