// mobile/src/app/(tabs)/index.tsx
// Today tab — fetches and renders Song of the Day via useTodaySong (Phase 3 per-user selector).
//
// Phase 3 Slice C: reads today.rated to switch SongOfDayCard to the already-rated variant.
//   - rated == null:     SongOfDayCard shows primary CTA + optional re-roll ghost
//   - rated != null:     SongOfDayCard shows "Rated: {label}" static row; no CTA; no re-roll
//
// The song title/metadata region wraps in a Pressable (onTitlePress) so the user can
// re-open the breakdown in read-only mode even after rating (UI-SPEC §8).
import React from 'react';
import {
  View,
  Text,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useTodaySong } from '../../api/todaySong';
import { SongOfDayCard } from '../../components/SongOfDayCard';
import type { RatingLiteral } from '../../api/sessions';

const LABELS: Record<RatingLiteral, string> = {
  not_my_tempo: 'Not my tempo',
  getting_closer: 'Getting closer',
  thats_what_im_looking_for: "That's what I'm looking for",
};

export default function TodayScreen() {
  const { data: today, isLoading, isError, error } = useTodaySong();
  const router = useRouter();

  if (isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#E07B39" />
        <Text style={styles.loadingText}>Loading...</Text>
      </View>
    );
  }

  if (isError || !today) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>Could not load today's song</Text>
        {error instanceof Error && (
          <Text style={styles.errorDetail}>{error.message}</Text>
        )}
      </View>
    );
  }

  // Derive already-rated label for SongOfDayCard variant (UI-SPEC §8)
  const ratedLabel = today.rated ? LABELS[today.rated.rating as RatingLiteral] : null;

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <SongOfDayCard
        song={today.song}
        ratedLabel={ratedLabel}
        onTitlePress={() => router.push(`/breakdown/${today.song.id}`)}
        onSeekBreakdown={
          ratedLabel ? undefined : () => router.push(`/breakdown/${today.song.id}`)
        }
      />
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
});
