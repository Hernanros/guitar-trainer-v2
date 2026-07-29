// mobile/src/app/(tabs)/index.tsx
// Today tab — fetches and renders the full Song of the Day breakdown.
// Calls useSongOfDay() which hits GET /api/v1/song-of-day via TanStack Query.
// ChordDiagram and TabNotation render SVG from D-03 semantic JSON.
import { router } from 'expo-router';
import React from 'react';
import {
  Pressable,
  View,
  Text,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
} from 'react-native';
import { useSongOfDay } from '../../api/songOfDay';
import { ChordDiagram } from '../../components/ChordDiagram';
import { TabNotation } from '../../components/TabNotation';
import type { components } from '../../api/generated/schema';

type Chord = components['schemas']['Chord'];
type TechniqueNote = components['schemas']['TechniqueNote'];

export default function TodayScreen() {
  const { data: song, isLoading, isError, error } = useSongOfDay();

  if (isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#E07B39" />
        <Text style={styles.loadingText}>Loading...</Text>
      </View>
    );
  }

  if (isError || !song) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>Could not load today's song</Text>
        {error instanceof Error && (
          <Text style={styles.errorDetail}>{error.message}</Text>
        )}
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* Song header */}
      <View style={styles.header}>
        <Text style={styles.label}>SONG OF THE DAY</Text>
        <Text style={styles.title}>{song.title}</Text>
        <Text style={styles.artist}>{song.artist}</Text>
        <Text style={styles.meta}>
          {song.genre} · {song.bpm} BPM · Key of {song.key}
        </Text>
        <View style={styles.difficultyBadge}>
          <Text style={styles.difficultyText}>{song.difficulty}</Text>
        </View>
        {/* See the breakdown CTA — navigates to breakdown/[songId] via expo-router v57 */}
        <Pressable
          style={({ pressed }) => [styles.breakdownCta, pressed && styles.breakdownCtaPressed]}
          onPress={() => router.push(`/breakdown/${song.id}`)}
          accessibilityRole="button"
          accessibilityLabel="See the breakdown"
        >
          <Text style={styles.breakdownCtaText}>See the breakdown</Text>
        </Pressable>
      </View>

      {/* Technique Notes */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>How to play it</Text>
        {song.breakdown.technique_notes.map((note: TechniqueNote, i: number) => (
          <View key={i} style={styles.techniqueCard}>
            <Text style={styles.techniqueHeading}>{note.heading}</Text>
            <Text style={styles.techniqueBody}>{note.body}</Text>
          </View>
        ))}
      </View>

      {/* Tab Notation */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Tab</Text>
        <View style={styles.tabContainer}>
          <TabNotation tab={song.breakdown.tab} />
        </View>
      </View>

      {/* Chord Diagrams */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Chords</Text>
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={styles.chordScroll}
          contentContainerStyle={styles.chordScrollContent}
        >
          {song.breakdown.chords.map((chord: Chord) => (
            <ChordDiagram key={chord.name} chord={chord} />
          ))}
        </ScrollView>
      </View>
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
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 16,
    backgroundColor: '#1A1A1A',
  },
  loadingText: {
    marginTop: 12,
    fontSize: 16,
    color: '#999',
  },
  errorText: {
    fontSize: 16,
    color: '#c0392b',
    fontWeight: 'bold',
    textAlign: 'center',
  },
  errorDetail: {
    marginTop: 8,
    fontSize: 13,
    color: '#999',
    textAlign: 'center',
  },
  header: {
    marginBottom: 28,
    paddingBottom: 20,
    borderBottomWidth: 1,
    borderBottomColor: '#333',
  },
  label: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 1.5,
    color: '#E07B39',
    marginBottom: 6,
  },
  title: {
    fontSize: 24,
    fontWeight: '800',
    color: '#F5F5F5',
    marginBottom: 4,
  },
  artist: {
    fontSize: 16,
    color: '#999',
    marginBottom: 6,
  },
  meta: {
    fontSize: 13,
    color: '#666',
    marginBottom: 10,
  },
  difficultyBadge: {
    alignSelf: 'flex-start',
    backgroundColor: '#2A2A2A',
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderWidth: 1,
    borderColor: '#E07B39',
  },
  difficultyText: {
    fontSize: 12,
    color: '#E07B39',
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  breakdownCta: {
    marginTop: 16,
    backgroundColor: '#E07B39',
    paddingVertical: 14,
    borderRadius: 8,
    alignItems: 'center',
    minHeight: 48,
  },
  breakdownCtaPressed: {
    opacity: 0.8,
  },
  breakdownCtaText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
  },
  section: {
    marginBottom: 28,
  },
  sectionTitle: {
    fontSize: 12,
    fontWeight: '700',
    color: '#999',
    marginBottom: 12,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  techniqueCard: {
    backgroundColor: '#242424',
    borderRadius: 10,
    padding: 14,
    marginBottom: 10,
    borderLeftWidth: 3,
    borderLeftColor: '#E07B39',
  },
  techniqueHeading: {
    fontSize: 15,
    fontWeight: '700',
    color: '#F5F5F5',
    marginBottom: 6,
  },
  techniqueBody: {
    fontSize: 14,
    color: '#AAA',
    lineHeight: 21,
  },
  tabContainer: {
    backgroundColor: '#242424',
    borderRadius: 10,
    padding: 12,
    overflow: 'hidden',
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
