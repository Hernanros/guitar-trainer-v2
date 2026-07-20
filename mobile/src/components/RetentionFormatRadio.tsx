// mobile/src/components/RetentionFormatRadio.tsx
// Fletcher-voiced 3-option radio group for retention format selection (D-13).
//
// Voice contract: fletcher-identity.md — copy below uses the exact strings from
// 02-02-PLAN.md <voice_contract> block.
//   - No emojis.
//   - No exclamation points.
//   - Second person, present tense.
//   - Terse descriptions that land with authority.
//
// D-13: default to 'streak' if user picks nothing (handled in preferences.tsx,
// not this component — this component is a controlled input with no default state).
//
// The section heading ("How should Fletcher mark the wins?") lives in preferences.tsx.
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

type RetentionFormat = 'streak' | 'weekly_digest' | 'monthly_milestone';

interface RetentionFormatRadioProps {
  value: RetentionFormat | null;
  onChange: (value: RetentionFormat) => void;
}

// Copy must match <voice_contract> exactly — no edits without updating the plan.
const OPTIONS: Array<{ id: RetentionFormat; label: string }> = [
  { id: 'streak', label: 'Streak: I show up daily, count me.' },
  { id: 'weekly_digest', label: 'Weekly digest: show me what I did on Sunday.' },
  { id: 'monthly_milestone', label: 'Monthly milestone: mark the big wins.' },
];

export function RetentionFormatRadio({ value, onChange }: RetentionFormatRadioProps) {
  return (
    <View style={styles.group}>
      {OPTIONS.map((opt) => {
        const active = opt.id === value;
        return (
          <Pressable
            key={opt.id}
            style={[styles.row, active && styles.rowActive]}
            onPress={() => onChange(opt.id)}
            accessibilityRole="radio"
            accessibilityState={{ selected: active }}
            accessibilityLabel={opt.label}
          >
            <View style={[styles.radio, active && styles.radioActive]}>
              {active && <View style={styles.radioDot} />}
            </View>
            <Text style={styles.label}>{opt.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  group: {
    gap: 12,
    marginBottom: 24,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 12,
    borderRadius: 8,
    backgroundColor: '#242424',
  },
  rowActive: {
    // Border-left accent mirrors techniqueCard pattern from Today tab.
    borderLeftWidth: 3,
    borderLeftColor: '#E07B39',
    paddingLeft: 9,
  },
  radio: {
    width: 18,
    height: 18,
    borderRadius: 9,
    borderWidth: 2,
    borderColor: '#666',
    marginRight: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  radioActive: {
    borderColor: '#E07B39',
  },
  radioDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: '#E07B39',
  },
  label: {
    color: '#F5F5F5',
    fontSize: 14,
    flex: 1,
    lineHeight: 20,
  },
});
