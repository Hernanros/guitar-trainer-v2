// mobile/src/app/(tabs)/index.tsx
// Today tab — Phase 3 (03-01 Slice A) wire-up.
//
// Replaces Phase 1's useSongOfDay (hardcoded fetch) with useTodaySong (per-user
// deterministic selector, keyed by local calendar day for automatic daily rotation).
//
// Loading state: FletcherLoader with Phase 3 breakdown-specific messages.
//   "Fletcher is listening..." → "Working out the fingering..." → "Almost there..."
//
// Data state: SongOfDayCard renders the full hero card with Fletcher line variant,
//   primary "See the breakdown" CTA, and re-roll ghost button.
//   FromTheBankTag chip renders below difficulty badge when from_bank=true.
//
// Error state: plain retry Pressable on #3A1F1F bg — BreakdownErrorCard lands in Slice B.
//
// Breakdown detail: removed (Slice A does not render Tab/ChordDiagram).
//   TabNotation and ChordDiagram imports are preserved so Slice B can restore them
//   without merge conflicts.
//
// Voice contract (fletcher-identity.md, UI-SPEC §1, §10):
//   All strings delegated to SongOfDayCard.FLETCHER_LINE (verbatim UI-SPEC copy).
//   No inline string literals — all user-visible copy lives in the component layer.
import React from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useTodaySong, useReroll } from '../../api/todaySong';
import { FletcherLoader } from '../../components/FletcherLoader';
import { SongOfDayCard } from '../../components/SongOfDayCard';
import { FromTheBankTag } from '../../components/FromTheBankTag';
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import { ChordDiagram } from '../../components/ChordDiagram';
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import { TabNotation } from '../../components/TabNotation';
import type { FletcherLineVariant } from '../../components/SongOfDayCard';

// Loader messages for Today tab (Phase 3 Slice A: while fetching today's song).
// Slice B will use these for the breakdown fetch. For Slice A (selector only, no Sonnet),
// they show briefly until the fast SQL query returns — typically < 500ms.
const TODAY_LOADER_MESSAGES = [
  'Fletcher is listening...',
  'Working out the fingering...',
  'Almost there...',
] as const;

export default function TodayScreen() {
  const { data: today, isPending, isError, error, refetch } = useTodaySong();
  const reroll = useReroll();

  // --- Loading state ---
  if (isPending) {
    return (
      <FletcherLoader messages={TODAY_LOADER_MESSAGES} isPending={true} />
    );
  }

  // --- Error state ---
  // BreakdownErrorCard arrives in Slice B; for Slice A a minimal retry is acceptable.
  if (isError || !today) {
    return (
      <View style={styles.errorContainer}>
        <Text style={styles.errorText}>Could not load today's song.</Text>
        {error instanceof Error && (
          <Text style={styles.errorDetail}>{error.message}</Text>
        )}
        <Pressable
          style={styles.retryButton}
          onPress={() => refetch()}
          accessibilityRole="button"
          accessibilityLabel="Try again"
        >
          <Text style={styles.retryText}>Try again</Text>
        </Pressable>
      </View>
    );
  }

  // --- Derive Fletcher line variant from selector metadata ---
  let fletcherLineVariant: FletcherLineVariant;
  if (today.rerolled) {
    fletcherLineVariant = 'rerolled';
  } else if (today.from_bank && today.bank_source === 'user_bench') {
    fletcherLineVariant = 'user_bench';
  } else if (today.from_bank) {
    fletcherLineVariant = 'seed_catalog';
  } else {
    fletcherLineVariant = 'deterministic';
  }

  // --- Data state ---
  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* SongOfDayCard — hero card with Fletcher line + CTA + re-roll */}
      <SongOfDayCard
        song={today.song}
        fletcherLineVariant={fletcherLineVariant}
        rerollsLeft={today.rerolled ? 0 : 1}
        onTapBreakdown={() => {
          // Slice B wires this to the breakdown route.
          Alert.alert('Breakdown', 'Coming in Slice B.');
        }}
        onReroll={() => reroll.mutate()}
        bankChip={
          // FromTheBankTag chip: visible when bank path fired (25% override or empty working_on).
          today.from_bank && today.bank_source ? (
            <FromTheBankTag variant={today.bank_source} />
          ) : null
        }
      />

      {/* Breakdown detail arrives in Slice B */}
      {/* TabNotation and ChordDiagram imports preserved above for Slice B restoration */}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#1A1A1A',
  },
  content: {
    padding: 16,
    paddingBottom: 40,
  },
  errorContainer: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    backgroundColor: '#3A1F1F',
  },
  errorText: {
    fontSize: 16,
    color: '#F5F5F5',
    fontWeight: '700',
    textAlign: 'center',
    marginBottom: 8,
  },
  errorDetail: {
    fontSize: 13,
    color: '#999',
    textAlign: 'center',
    marginBottom: 16,
  },
  retryButton: {
    backgroundColor: '#E07B39',
    paddingHorizontal: 24,
    paddingVertical: 12,
    borderRadius: 8,
    minHeight: 48,
    alignItems: 'center',
    justifyContent: 'center',
  },
  retryText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
  },
});
