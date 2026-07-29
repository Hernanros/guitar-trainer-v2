// mobile/src/components/FromTheBankTag.tsx
// Small chip that surfaces the bank-random signal to the user.
//
// Design contract (UI-SPEC §2, §4):
//   variant='user_bench' → label: "From your bench"
//   variant='seed_catalog' → label: "From the bank"
//   Background: #242424. Left border: 3px #E07B39. Padding: 6px H / 4px V.
//   Label: 14pt semibold #F5F5F5. No icon (terse-direct, no symbol needed).
//   Placement: beneath the difficulty badge, top-left of SongOfDayCard (UI-SPEC §4).
//
// Voice contract (fletcher-identity.md):
//   "your bench" — guitarist metaphor for the pile of songs you know but aren't drilling.
//   "the bank" — seed-catalog pick, unqualified (user has no personal songs to draw from).
//   No emojis. No exclamation points.
import { StyleSheet, Text, View } from 'react-native';

type BankVariant = 'user_bench' | 'seed_catalog';

interface FromTheBankTagProps {
  variant: BankVariant;
}

const LABELS: Record<BankVariant, string> = {
  user_bench: 'From your bench',
  seed_catalog: 'From the bank',
};

export function FromTheBankTag({ variant }: FromTheBankTagProps) {
  return (
    <View
      style={styles.tag}
      accessibilityRole="text"
      accessibilityLabel={LABELS[variant]}
    >
      <Text style={styles.label}>{LABELS[variant]}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  tag: {
    alignSelf: 'flex-start',
    backgroundColor: '#242424',
    borderLeftWidth: 3,
    borderLeftColor: '#E07B39',
    paddingHorizontal: 6,
    paddingVertical: 4,
    marginTop: 4,
  },
  label: {
    fontSize: 14,
    fontWeight: '600',
    color: '#F5F5F5',
  },
});
