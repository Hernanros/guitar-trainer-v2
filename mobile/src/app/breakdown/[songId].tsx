// mobile/src/app/breakdown/[songId].tsx
// Breakdown detail screen — Expo Router v57 dynamic route.
// Docs: https://docs.expo.dev/versions/v57.0.0/router/
// Navigated to via: router.push(`/breakdown/${song.id}`) from the Today tab.
//
// States (UI-SPEC §5 states 6, 7, 8):
//   6. isPending  → FletcherLoader with breakdown-specific messages (UI-SPEC §3)
//   7. isError    → BreakdownErrorCard with Try again + Back to today's song
//   8. data       → Full breakdown: HOW TO PLAY IT / TAB / CHORDS sections
//
// Cache semantics: useBreakdown has staleTime:Infinity so second visit returns
// cached data immediately without re-fetching (D-11 cache-forever).
//
// No RefreshControl (UI-SPEC §6 — pull-to-refresh forbidden on breakdown screen).
// No ClipPath in TabNotation (RESEARCH §8 landmine 9 — enforced in TabNotation.tsx).
//
// Voice contract: no emojis, no exclamation points.
// Section eyebrows from UI-SPEC §4 (unchanged from Phase 1): HOW TO PLAY IT / TAB / CHORDS.
import { router, useLocalSearchParams } from 'expo-router';
import React from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { BreakdownErrorCard } from '../../components/BreakdownErrorCard';
import { ChordDiagram } from '../../components/ChordDiagram';
import { FletcherLoader } from '../../components/FletcherLoader';
import { TabNotation } from '../../components/TabNotation';
import { useBreakdown } from '../../api/todaySong';
import type { components } from '../../api/generated/schema';

type TechniqueNote = components['schemas']['TechniqueNote'];
type Chord = components['schemas']['Chord'];

// Fletcher loader copy for breakdown fetch (UI-SPEC §3)
const BREAKDOWN_LOADER_MESSAGES = [
  'Fletcher is listening...',
  'Working out the fingering...',
  'Almost there...',
] as const;

export default function BreakdownScreen() {
  // useLocalSearchParams: Expo Router v57 hook for dynamic segment extraction.
  // Docs: https://docs.expo.dev/versions/v57.0.0/router/navigating-pages/
  const { songId: songIdParam } = useLocalSearchParams<{ songId: string }>();
  const songId = songIdParam ? parseInt(songIdParam, 10) : undefined;

  const { data: breakdown, isPending, isError, refetch } = useBreakdown(songId);

  // State 6: loading
  if (isPending) {
    return (
      <FletcherLoader
        messages={BREAKDOWN_LOADER_MESSAGES}
        isPending={true}
      />
    );
  }

  // State 7: error
  if (isError || !breakdown) {
    return (
      <BreakdownErrorCard
        onRetry={() => refetch()}
        onBack={() => router.back()}
      />
    );
  }

  // State 8: data — full breakdown render
  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      // No RefreshControl per UI-SPEC §6
    >
      {/* HOW TO PLAY IT — technique notes section (UI-SPEC §4) */}
      <Text style={styles.sectionEyebrow}>HOW TO PLAY IT</Text>
      {breakdown.technique_notes.map((note: TechniqueNote, i: number) => (
        <View key={i} style={styles.techniqueCard}>
          <Text style={styles.techniqueHeading}>{note.heading}</Text>
          <Text style={styles.techniqueBody}>{note.body}</Text>
        </View>
      ))}

      {/* TAB — multi-measure horizontal scroll (UI-SPEC §4, RESEARCH §2) */}
      <Text style={styles.sectionEyebrow}>TAB</Text>
      <View style={styles.tabContainer}>
        <TabNotation tab={breakdown.tab} />
      </View>

      {/* CHORDS — horizontally scrollable chord diagrams (UI-SPEC §4) */}
      <Text style={styles.sectionEyebrow}>CHORDS</Text>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.chordScroll}
        contentContainerStyle={styles.chordScrollContent}
      >
        {breakdown.chords.map((chord: Chord, i: number) => (
          <ChordDiagram key={i} chord={chord} />
        ))}
      </ScrollView>
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
  sectionEyebrow: {
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 1,
    color: '#E07B39',
    marginTop: 24,
    marginBottom: 12,
  },
  techniqueCard: {
    backgroundColor: '#242424',
    padding: 12,
    borderRadius: 8,
    borderLeftWidth: 3,
    borderLeftColor: '#E07B39',
    marginBottom: 12,
  },
  techniqueHeading: {
    color: '#F5F5F5',
    fontSize: 15,
    fontWeight: '600',
    marginBottom: 4,
  },
  techniqueBody: {
    color: '#AAA',
    fontSize: 14,
    lineHeight: 21,
  },
  tabContainer: {
    backgroundColor: '#242424',
    borderRadius: 8,
    padding: 12,
  },
  chordScroll: {
    flexGrow: 0,
  },
  chordScrollContent: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    paddingVertical: 8,
    paddingHorizontal: 4,
    backgroundColor: '#242424',
    borderRadius: 10,
  },
});
