// mobile/src/app/(tabs)/index.tsx
// Today tab — fetches and renders the Song of the Day.
// Calls useSongOfDay() which hits GET /api/v1/song-of-day via TanStack Query.
// Tab/chord SVG rendering deferred to Plan 02.
import React from 'react';
import { View, Text, StyleSheet, ActivityIndicator } from 'react-native';
import { useSongOfDay } from '../../api/songOfDay';

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
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.label}>SONG OF THE DAY</Text>
        <Text style={styles.title}>{song.title}</Text>
        <Text style={styles.artist}>{song.artist}</Text>
        <Text style={styles.meta}>
          {song.genre} · {song.bpm} BPM · Key of {song.key} · {song.difficulty}
        </Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Tab Notation</Text>
        <Text style={styles.placeholder}>[Tab notation — Plan 02]</Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Chord Diagrams</Text>
        <Text style={styles.placeholder}>[Chord diagrams — Plan 02]</Text>
        <Text style={styles.chordList}>
          Chords: {song.breakdown.chords.map((c: { name: string }) => c.name).join(', ')}
        </Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Technique Notes</Text>
        {song.breakdown.technique_notes.map((note: { heading: string; body: string }, i: number) => (
          <View key={i} style={styles.techniqueNote}>
            <Text style={styles.techniqueHeading}>{note.heading}</Text>
            <Text style={styles.techniqueBody}>{note.body}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#fff',
    padding: 16,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 16,
  },
  loadingText: {
    marginTop: 12,
    fontSize: 16,
    color: '#666',
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
    color: '#666',
    textAlign: 'center',
  },
  header: {
    marginBottom: 24,
    paddingBottom: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#eee',
  },
  label: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 1.5,
    color: '#E07B39',
    marginBottom: 4,
  },
  title: {
    fontSize: 28,
    fontWeight: '800',
    color: '#1a1a1a',
    marginBottom: 4,
  },
  artist: {
    fontSize: 18,
    color: '#444',
    marginBottom: 6,
  },
  meta: {
    fontSize: 13,
    color: '#888',
  },
  section: {
    marginBottom: 20,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#333',
    marginBottom: 8,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  placeholder: {
    fontSize: 14,
    color: '#999',
    fontStyle: 'italic',
    backgroundColor: '#f9f9f9',
    padding: 12,
    borderRadius: 8,
    marginBottom: 6,
  },
  chordList: {
    fontSize: 14,
    color: '#555',
  },
  techniqueNote: {
    marginBottom: 12,
  },
  techniqueHeading: {
    fontSize: 15,
    fontWeight: '600',
    color: '#222',
    marginBottom: 3,
  },
  techniqueBody: {
    fontSize: 14,
    color: '#555',
    lineHeight: 20,
  },
});
