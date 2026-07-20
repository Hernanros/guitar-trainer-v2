// mobile/src/components/SessionLengthChips.tsx
// Single-select chip group for session length selection (D-12).
//
// D-12: no default — the user must make an intentional choice.
// The Complete button in preferences.tsx stays disabled until a chip is selected.
//
// Chip labels: '15 min' / '30 min' / '45 min' / '60 min'
// The space before 'min' matters for chip-width legibility on mobile (per <specifics>).
//
// Voice contract: component copy is not Fletcher-voiced (labels are purely functional).
// The section heading ("How long do you have most days?") is Fletcher-voiced and lives
// in preferences.tsx.
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

type LengthValue = 15 | 30 | 45 | 60;

interface SessionLengthChipsProps {
  value: LengthValue | null;
  onChange: (value: LengthValue) => void;
}

const OPTIONS: LengthValue[] = [15, 30, 45, 60];

export function SessionLengthChips({ value, onChange }: SessionLengthChipsProps) {
  return (
    <View style={styles.row}>
      {OPTIONS.map((n) => {
        const active = n === value;
        return (
          <Pressable
            key={n}
            style={({ pressed }) => [
              styles.chip,
              active && styles.chipActive,
              pressed && !active && styles.chipPressed,
            ]}
            onPress={() => onChange(n)}
            accessibilityRole="radio"
            accessibilityState={{ selected: active }}
            accessibilityLabel={`${n} min`}
          >
            <Text style={[styles.label, active && styles.labelActive]}>{n} min</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 24,
    flexWrap: 'wrap',
  },
  chip: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: '#E07B39',
  },
  chipActive: {
    backgroundColor: '#E07B39',
  },
  chipPressed: {
    opacity: 0.7,
  },
  label: {
    color: '#E07B39',
    fontSize: 14,
    fontWeight: '600',
  },
  labelActive: {
    color: '#1A1A1A',
  },
});
