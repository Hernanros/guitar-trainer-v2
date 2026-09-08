// mobile/src/app/breakdown/[songId].tsx
// Breakdown detail screen — shows tab/chords/technique notes for today's song.
// Slice C: Adds RatingPills below the breakdown stack + AlreadyRatedCard overlay.
// Phase 4 Slice B: Error parsing for BREAKDOWN_CAPPED (429) and FLETCHER_OUT (503);
//   today-song cache invalidation on success so the quota chip decrements.
//
// Rating flow:
//   1. User taps a RatingPill → selectedRating set (confirmed visual + POST fires)
//   2. POST succeeds → submitRating.isSuccess + AlreadyRatedCard overlay slides in
//   3. After 2s (or tap-anywhere) → router.replace('/(tabs)') — back to Today
//
// Read-only mode (already rated today):
//   When today.rated is set AND today.song.id === songId, the RatingPills row
//   is replaced by a static "Rated: {label}" line. User can still see breakdown.
//
// router.replace (not router.push) so tapping back on Today tab does not re-enter breakdown.
import { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';
import { RatingPills } from '../../components/RatingPills';
import { AlreadyRatedCard } from '../../components/AlreadyRatedCard';
import { BreakdownErrorCard } from '../../components/BreakdownErrorCard';
import { ChordDiagram } from '../../components/ChordDiagram';
import { TabNotation } from '../../components/TabNotation';
import { useTodaySong, localCalendarDay } from '../../api/todaySong';
import { getOrCreateUserId } from '../../api/mmkv';
import { useSubmitRating, type RatingLiteral } from '../../api/sessions';
import type { components } from '../../api/generated/schema';

type Chord = components['schemas']['Chord'];
type TechniqueNote = components['schemas']['TechniqueNote'];
type SongResponse = components['schemas']['SongResponse'];

// Genre / BPM / key can each be null on user-onboarded songs from before 33d78ac
// or on placeholder-metadata rows. Render only the fields we have.
function MetaLine({ song }: { song: SongResponse }) {
  const parts = [
    song.genre,
    song.bpm ? `${song.bpm} BPM` : null,
    song.key ? `Key of ${song.key}` : null,
  ].filter((p): p is string => Boolean(p));
  if (parts.length === 0) return null;
  return <Text style={styles.meta}>{parts.join(' · ')}</Text>;
}

const LABELS: Record<RatingLiteral, string> = {
  not_my_tempo: 'Not my tempo',
  getting_closer: 'Getting closer',
  thats_what_im_looking_for: "That's what I'm looking for",
};

export default function BreakdownScreen() {
  const { songId: songIdParam } = useLocalSearchParams<{ songId: string }>();
  const songId = songIdParam ? parseInt(songIdParam, 10) : null;
  const router = useRouter();
  const qc = useQueryClient();
  const userId = getOrCreateUserId();

  const { data: today, isLoading, isError, error } = useTodaySong();
  const submitRating = useSubmitRating();
  const [submittedRating, setSubmittedRating] = useState<RatingLiteral | null>(null);
  // Phase 4 Slice B: error code state for BREAKDOWN_CAPPED / FLETCHER_OUT variants.
  const [errorCode, setErrorCode] = useState<'BREAKDOWN_CAPPED' | 'FLETCHER_OUT' | null>(null);
  const [errorResetsAt, setErrorResetsAt] = useState<string | null>(null);

  // Phase 4 Slice B: invalidate today-song when the breakdown loads successfully so
  // the quota chip on the Today tab reflects the latest governor_calls count.
  // Runs once when today data becomes available (data transitions from undefined to truthy).
  useEffect(() => {
    if (today) {
      qc.invalidateQueries({ queryKey: ['today-song', userId, localCalendarDay()] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(today)]);

  // Phase 4 Slice B: invalidate today-song on a successful breakdown view so the
  // quota chip decrements on next Today-tab open. The chip reads from
  // TodaySongResponse.breakdown_quota which is server-authoritative (D-06).
  // We trigger invalidation here rather than in sessions.ts to avoid touching
  // Slice C's rating logic (sessions.ts is explicitly excluded from files_modified).
  const invalidateTodaySong = () => {
    qc.invalidateQueries({ queryKey: ['today-song', userId, localCalendarDay()] });
  };

  // Helper: parse error from breakdown fetch (HTTP 429/503 body shape from Slice A).
  // If a future refactor adds a direct breakdown endpoint call, this function handles it.
  const handleBreakdownError = (err: unknown) => {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes('BREAKDOWN_CAPPED') || msg.includes('429')) {
      // Try to extract resets_at from error message if available
      const resetsAtMatch = msg.match(/"resets_at"\s*:\s*"([^"]+)"/);
      setErrorCode('BREAKDOWN_CAPPED');
      setErrorResetsAt(resetsAtMatch ? resetsAtMatch[1] : null);
    } else if (msg.includes('FLETCHER_OUT') || msg.includes('503')) {
      setErrorCode('FLETCHER_OUT');
    } else {
      setErrorCode(null);
    }
  };

  if (isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#E07B39" />
        <Text style={styles.loadingText}>Loading...</Text>
      </View>
    );
  }

  if (isError || !today) {
    // Parse error code from the useTodaySong error for code-based rendering
    const parsedCode = (() => {
      const msg = error instanceof Error ? error.message : String(error ?? '');
      if (msg.includes('BREAKDOWN_CAPPED')) return 'BREAKDOWN_CAPPED' as const;
      if (msg.includes('FLETCHER_OUT')) return 'FLETCHER_OUT' as const;
      return errorCode;
    })();
    return (
      <BreakdownErrorCard
        code={parsedCode}
        resets_at={errorResetsAt}
        onRetry={() => {
          setErrorCode(null);
          setErrorResetsAt(null);
          invalidateTodaySong();
        }}
        onBack={() => router.back()}
      />
    );
  }

  const { song } = today;

  // Breakdown-not-ready placeholder. Server marks breakdown_available=false when
  // the song has no Sonnet-generated breakdown yet (or the placeholder-Breakdown
  // shape is present but not real content). Belt-and-suspenders: also treat
  // missing/null song.breakdown the same way.
  if (!today.breakdown_available || !song.breakdown) {
    return (
      <View style={styles.container}>
        <ScrollView contentContainerStyle={styles.content}>
          <View style={styles.header}>
            <Text style={styles.label}>BREAKDOWN</Text>
            <Text style={styles.title}>{song.title}</Text>
            <Text style={styles.artist}>{song.artist}</Text>
            <MetaLine song={song} />
          </View>
          <View style={styles.placeholder}>
            <ActivityIndicator size="small" color="#E07B39" />
            <Text style={styles.placeholderText}>Fletcher preparing this breakdown...</Text>
            <Text style={styles.placeholderSubtext}>Come back in a moment.</Text>
          </View>
        </ScrollView>
      </View>
    );
  }

  // Already-rated read-only mode: server says rated, AND this is today's song
  const alreadyRated = today.rated?.rating ?? null;
  const isAlreadyRatedSong = alreadyRated !== null && today.song.id === songId;

  const handleRatingSelect = (rating: RatingLiteral) => {
    setSubmittedRating(rating);
    submitRating.mutate(
      { song_id: songId!, rating },
      {
        onError: () => {
          // 409 is handled silently by the hook (invalidates today-song).
          // Other errors: reset UI so user can retry.
          setSubmittedRating(null);
        },
      },
    );
  };

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        {/* Song header */}
        <View style={styles.header}>
          <Text style={styles.label}>BREAKDOWN</Text>
          <Text style={styles.title}>{song.title}</Text>
          <Text style={styles.artist}>{song.artist}</Text>
          <MetaLine song={song} />
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

        {/* Rating area — RatingPills or read-only rated line */}
        {isAlreadyRatedSong ? (
          <Text style={styles.ratedLine}>Rated: {LABELS[alreadyRated]}</Text>
        ) : (
          <RatingPills
            onSelect={handleRatingSelect}
            selectedRating={submittedRating}
            disabled={submitRating.isPending}
          />
        )}
      </ScrollView>

      {/* Full-screen post-rating overlay — appears on success */}
      {submitRating.isSuccess && submittedRating && (
        <View style={StyleSheet.absoluteFill}>
          <AlreadyRatedCard
            rating={submittedRating}
            onDismiss={() => router.replace('/(tabs)')}
          />
        </View>
      )}
    </View>
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
  ratedLine: {
    fontSize: 14,
    color: '#E07B39',
    fontWeight: '600',
    marginTop: 8,
    marginBottom: 24,
  },
  placeholder: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 48,
    paddingHorizontal: 24,
  },
  placeholderText: {
    marginTop: 16,
    fontSize: 15,
    color: '#AAA',
    textAlign: 'center',
  },
  placeholderSubtext: {
    marginTop: 6,
    fontSize: 13,
    color: '#666',
    textAlign: 'center',
  },
});
