// mobile/src/components/RatingPills.tsx
// Three-tier horizontal rating row for Slice C.
//
// UI-SPEC §5 locked copy — verbatim, no paraphrase:
//   "Not my tempo" | "Getting closer" | "That's what I'm looking for"
//
// Visual pattern: RetentionFormatRadio.tsx Pressable + border-left-active pattern.
// accessibilityRole="button" per PATTERNS.md line 871 (not "radio").
// No pill grouping affordance (no outer border/container) per PATTERNS.md line 872.
// 48px minimum tap target per UI-SPEC §5.
//
// States:
//   default:  all three pills at full opacity, tappable
//   in-flight: selectedRating is set — active pill gets 3px #E07B39 left border;
//              other two dim to opacity 0.5; all three disabled
//   disabled: explicit disabled=true prop — all three non-tappable (used during POST)
//
// Gestures forbidden (UI-SPEC §5): no long-press undo, no swipe between pills.
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { RatingLiteral } from '../api/sessions';

interface RatingPillsProps {
  onSelect: (rating: RatingLiteral) => void;
  selectedRating?: RatingLiteral | null;
  disabled?: boolean;
}

const PILLS: { id: RatingLiteral; label: string }[] = [
  { id: 'not_my_tempo', label: 'Not my tempo' },
  { id: 'getting_closer', label: 'Getting closer' },
  { id: 'thats_what_im_looking_for', label: "That's what I'm looking for" },
];

export function RatingPills({ onSelect, selectedRating, disabled }: RatingPillsProps) {
  return (
    <View style={styles.row}>
      {PILLS.map((pill) => {
        const isActive = selectedRating === pill.id;
        const isDimmed = selectedRating != null && !isActive;
        const isBlocked = !!disabled || selectedRating != null;
        return (
          <Pressable
            key={pill.id}
            style={({ pressed }) => [
              styles.pill,
              isActive && styles.pillActive,
              isDimmed && styles.pillDimmed,
              pressed && !isBlocked && styles.pillPressed,
            ]}
            onPress={() => {
              if (!isBlocked) {
                onSelect(pill.id);
              }
            }}
            disabled={isBlocked}
            accessibilityRole="button"
            accessibilityLabel={pill.label}
            accessibilityState={{ disabled: isBlocked, selected: isActive }}
          >
            <Text style={styles.label}>{pill.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    gap: 12,
    marginTop: 24,
    marginBottom: 24,
  },
  pill: {
    flex: 1,
    minHeight: 48,
    backgroundColor: '#242424',
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 12,
  },
  pillActive: {
    borderLeftWidth: 3,
    borderLeftColor: '#E07B39',
    paddingLeft: 9,
  },
  pillDimmed: {
    opacity: 0.5,
  },
  pillPressed: {
    opacity: 0.85,
  },
  label: {
    fontSize: 14,
    fontWeight: '600',
    color: '#F5F5F5',
    textAlign: 'center',
  },
});
