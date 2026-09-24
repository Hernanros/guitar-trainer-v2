// mobile/src/app/(tabs)/index.tsx
// Today tab — fetches and renders Song of the Day via useTodaySong (Phase 3 per-user selector).
//
// Phase 3 Slice C: reads today.rated to switch SongOfDayCard to the already-rated variant.
//   - rated == null:     SongOfDayCard shows primary CTA + optional re-roll ghost
//   - rated != null:     SongOfDayCard shows "Rated: {label}" static row; no CTA; no re-roll
//
// Phase 3 gap-closure (03-04):
//   - useReroll wired: onReroll passed to SongOfDayCard iff user has NOT rated AND NOT rerolled.
//   - FromTheBankTag chip rendered above SongOfDayCard when today.from_bank && today.bank_source.
//   - bankChipLabel: "From your bench" (user_bench) / "From the bank" (seed_catalog) — UI-SPEC §2 verbatim.
//
// The song title/metadata region wraps in a Pressable (onTitlePress) so the user can
// re-open the breakdown in read-only mode even after rating (UI-SPEC §8).
import React, { useCallback, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  Pressable,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';
import { useTodaySong, useReroll } from '../../api/todaySong';
import { SongOfDayCard } from '../../components/SongOfDayCard';
import { FromTheBankTag } from '../../components/FromTheBankTag';
import { useCurrentPracticeSession } from '../../api/practiceSessions';
import type { RatingLiteral } from '../../api/sessions';

const LABELS: Record<RatingLiteral, string> = {
  not_my_tempo: 'Not my tempo',
  getting_closer: 'Getting closer',
  thats_what_im_looking_for: "That's what I'm looking for",
};

export default function TodayScreen() {
  const { data: today, isLoading, isError, error } = useTodaySong();
  const reroll = useReroll();
  const router = useRouter();
  const qc = useQueryClient();
  const currentSession = useCurrentPracticeSession();

  // Pull-to-refresh — invalidate the today-song query so the response refetches from server.
  // Needed because staleTime: Infinity + MMKV persistence means a stale cache survives force-quit;
  // the only user-facing escape hatch without waiting for a calendar-day boundary.
  // Broad predicate ['today-song'] catches every user/day tuple; TanStack refetches those active.
  const [refreshing, setRefreshing] = useState(false);
  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await qc.invalidateQueries({ queryKey: ['today-song'] });
    } finally {
      setRefreshing(false);
    }
  }, [qc]);

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

  // Derive rerolls remaining from the server-authoritative rerolled field (D-05).
  // Contract per index.test.tsx assertRerollsLeft: 0 when rerolled=true, 1 when rerolled=false.
  const rerollsLeft = today.rerolled ? 0 : 1;

  // Pass onReroll iff the user has NOT already rated AND has NOT spent their reroll.
  // When undefined, SongOfDayCard hides the ghost button (SongOfDayCard.tsx lines 72-75).
  // No error-card handling for reroll failures — 409 is swallowed inside useReroll.onError.
  const onReroll = ratedLabel || rerollsLeft === 0 ? undefined : () => reroll.mutate();

  // Bank-source chip visibility (index.test.tsx shouldRenderBankChip contract).
  const showBankChip = Boolean(today.from_bank && today.bank_source);

  // Bank chip copy — UI-SPEC §2 verbatim (DO NOT paraphrase).
  const bankChipLabel =
    today.bank_source === 'user_bench'
      ? 'From your bench'
      : today.bank_source === 'seed_catalog'
        ? 'From the bank'
        : null;

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={onRefresh}
          tintColor="#E07B39"
          colors={['#E07B39']}
        />
      }
    >
      {showBankChip && today.bank_source && (
        <FromTheBankTag variant={today.bank_source} />
      )}
      <SongOfDayCard
        song={today.song}
        ratedLabel={ratedLabel}
        breakdown_quota={today.breakdown_quota}
        onTitlePress={() => router.push(`/breakdown/${today.song.id}`)}
        onSeekBreakdown={
          ratedLabel ? undefined : () => router.push(`/breakdown/${today.song.id}`)
        }
        onReroll={onReroll}
      />
      <SessionStartCard
        session={currentSession.data ?? null}
        onPress={() => router.push('/session')}
      />
    </ScrollView>
  );
}

/**
 * Start-today's-session CTA (UI-SPEC §11). Resume copy takes priority over the
 * subline whenever a session is already `in_progress` — both routes go to the
 * same /session entry, which resolves start-vs-resume server-side (FLE-63).
 */
function SessionStartCard({
  session,
  onPress,
}: {
  session: { state: string; target_minutes: number; item_count: number } | null;
  onPress: () => void;
}) {
  const isResume = session?.state === 'in_progress';
  const subline = isResume
    ? "You left off partway. Pick it back up."
    : session
      ? // "1 things." shipped to device and Hernan flagged it (FLE-76). A one-item
        // session is the common case on a light day, not an edge case.
        `${session.target_minutes} minutes. ${session.item_count} ${
          session.item_count === 1 ? 'thing' : 'things'
        }.`
      : null;

  return (
    <Pressable
      style={styles.sessionCard}
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel="Start today's session"
    >
      <Text style={styles.sessionCta}>Start today's session</Text>
      {subline && <Text style={styles.sessionSubline}>{subline}</Text>}
    </Pressable>
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
  sessionCard: {
    backgroundColor: '#242424',
    borderRadius: 12,
    padding: 20,
    marginTop: 16,
    alignItems: 'center',
  },
  sessionCta: {
    fontSize: 17,
    fontWeight: '700',
    color: '#E07B39',
  },
  sessionSubline: {
    fontSize: 13,
    color: '#999',
    marginTop: 6,
  },
});
