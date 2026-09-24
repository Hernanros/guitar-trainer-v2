// mobile/src/app/breakdown/[songId]/drill/[drillIndex].tsx
// Focused drill-detail screen — Plan 04.1-04 Task 3.
//
// Expo Router nested dynamic route at:
//   breakdown/[songId]/drill/[drillIndex]
// Docs: https://docs.expo.dev/versions/v57.0.0/router/
//
// N3 FIX: Route params resolved with guard clauses + early return. No `!` non-null
// assertions on parsed params anywhere in this file.
//
// B2 FIX: Pure-fn helpers (advanceRepOrTempo, isRatingUnlocked, buildRatingPayload,
// isDrillAlreadyRatedToday) exported at file top for compile-time + pure-logic test
// coverage in mobile/__tests__/app/breakdown/drill.test.tsx.
//
// FLE-73 FIX: Drill state (currentBpm, repCount) lives in component-local useState,
// mirrored to MMKV (src/api/mmkv.ts) on every rep/tempo advance and restored on mount
// so backgrounding or killing the app mid-ladder doesn't lose rep progress. No server
// persistence — Per CONTEXT.md: rep counter resets per-session in 4.1.
//
// CONTEXT.md-locked copy strings (do NOT alter):
//   "Done one rep" — tap target label when below repetitions
//   "Push to next tempo →" — tap target label when reps complete and below target BPM
//   "For this song" / "Any song" — drill scope badge (from Plan 03 DrillCard)
//
// Rating flow:
//   1. User taps "Done one rep" / "Push to next tempo →" until ladder complete
//   2. RatingPills appear — user taps a pill
//   3. useSubmitDrillRating.mutate fires with drill_index + target_skill_node_id
//   4. AlreadyRatedCard overlay appears → router.replace('/breakdown/[songId]')
//   5. Envelope refetch (triggered by ['breakdown', songId] invalidation) carries
//      fresh drill_rated_today_indices → parent screen disables whole-song RatingPills (B1 fix)

import { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  Pressable,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { BreakdownErrorCard } from '../../../../components/BreakdownErrorCard';
import { FletcherLoader } from '../../../../components/FletcherLoader';
import { RatingPills } from '../../../../components/RatingPills';
import { AlreadyRatedCard } from '../../../../components/AlreadyRatedCard';
import { TabNotation } from '../../../../components/TabNotation';
import { MetronomeControl } from '../../../../components/MetronomeControl';
import { useBreakdown } from '../../../../api/breakdown';
import { useSubmitDrillRating, type RatingLiteral } from '../../../../api/sessions';
import {
  getDrillLadderState,
  setDrillLadderState,
  clearDrillLadderState,
} from '../../../../api/mmkv';

// ---------------------------------------------------------------------------
// Pure-fn helpers — exported for B2 test coverage in drill.test.tsx
// ---------------------------------------------------------------------------

export type TempoLadderState = { currentBpm: number; repCount: number };

/**
 * Advance the tempo ladder OR the rep counter based on current state.
 * Returns the new state OR null when the ladder is complete (rating unlocked).
 *
 * Tempo ladder rules (CONTEXT.md):
 *   - Each tempo step requires `drill.repetitions` reps before advancing.
 *   - Tempo advances by 5 BPM per step (capped at target_bpm).
 *   - When at target_bpm AND reps are complete, the ladder is done → return null.
 */
export function advanceRepOrTempo(
  state: TempoLadderState,
  drill: { start_bpm: number; target_bpm: number; repetitions: number },
): TempoLadderState | null {
  const { currentBpm, repCount } = state;
  // Not yet at the rep count for this tempo step — increment reps.
  if (repCount + 1 < drill.repetitions) {
    return { currentBpm, repCount: repCount + 1 };
  }
  // Just hit N reps at the target tempo → ladder complete.
  if (currentBpm >= drill.target_bpm) {
    return null;
  }
  // Advance to next tempo step (5 BPM increments per CONTEXT.md).
  const nextBpm = Math.min(currentBpm + 5, drill.target_bpm);
  return { currentBpm: nextBpm, repCount: 0 };
}

/**
 * True when the tempo ladder is complete and RatingPills should appear.
 */
export function isRatingUnlocked(
  state: TempoLadderState,
  drill: { target_bpm: number; repetitions: number },
): boolean {
  return state.currentBpm >= drill.target_bpm && state.repCount + 1 >= drill.repetitions;
}

/**
 * Build the useSubmitDrillRating body payload from screen context.
 * Maps drill.target_skill_temp_id (Sonnet-authored client field) →
 * payload.target_skill_node_id (server/deterministic field name).
 */
export function buildRatingPayload(
  songId: number,
  drillIndex: number,
  drill: { target_skill_temp_id: string },
  rating: RatingLiteral,
) {
  return {
    song_id: songId,
    rating,
    drill_index: drillIndex,
    target_skill_node_id: drill.target_skill_temp_id,
  };
}

/**
 * Derive whether this drill is already rated for today from server state.
 * Read from envelope.drill_rated_today_indices (B1 fix — server-derived, durable
 * across app restart, cache invalidation, cross-device sync).
 */
export function isDrillAlreadyRatedToday(
  drillIndex: number,
  envelope: { drill_rated_today_indices?: number[] } | null | undefined,
): boolean {
  return (envelope?.drill_rated_today_indices ?? []).includes(drillIndex);
}

// ---------------------------------------------------------------------------
// DrillScreen — main component
// ---------------------------------------------------------------------------

export default function DrillScreen() {
  const { songId: songIdParam, drillIndex: drillIndexParam } =
    useLocalSearchParams<{ songId: string; drillIndex: string }>();
  const router = useRouter();

  // N3 FIX: parse with guards, never use `!` non-null assertions downstream.
  const songId = songIdParam != null ? Number.parseInt(songIdParam, 10) : NaN;
  const drillIndex = drillIndexParam != null ? Number.parseInt(drillIndexParam, 10) : NaN;

  // Early-return guard: if either param is NaN (missing or unparseable), render error.
  // After this guard, songId and drillIndex are safely `number` — no `!` needed below.
  if (!Number.isFinite(songId) || !Number.isFinite(drillIndex)) {
    return (
      <BreakdownErrorCard
        onBack={() => router.back()}
        onRetry={() => router.back()}
      />
    );
  }

  return <DrillScreenInner songId={songId} drillIndex={drillIndex} />;
}

// Inner component receives narrowed (non-NaN) numbers from the outer guard.
function DrillScreenInner({ songId, drillIndex }: { songId: number; drillIndex: number }) {
  const router = useRouter();
  const breakdown = useBreakdown(songId);
  const submitDrillRating = useSubmitDrillRating(songId);

  // ladder is null until the drill is loaded (initialized on first render with drill data),
  // OR restored from MMKV (FLE-73) if a prior session for this exact song+drill left one.
  const [ladderState, setLadderState] = useState<TempoLadderState | null>(() =>
    getDrillLadderState(songId, drillIndex),
  );
  const [submittedRating, setSubmittedRating] = useState<RatingLiteral | null>(null);
  const [ratingUnlocked, setRatingUnlocked] = useState(false);

  // Guard rendering — loading / error / not-found states
  if (breakdown.isPending) {
    return (
      <FletcherLoader
        messages={['Fletcher is listening...', 'Working out the fingering...', 'Almost there...']}
        isPending={true}
      />
    );
  }

  if (breakdown.isError) {
    return (
      <BreakdownErrorCard
        onBack={() => router.back()}
        onRetry={() => breakdown.refetch()}
      />
    );
  }

  const envelope = breakdown.data;
  const drill = envelope?.breakdown?.drills?.[drillIndex];

  if (!drill) {
    // Deep-link with stale index or breakdown missing drills — show error card.
    return (
      <BreakdownErrorCard
        onBack={() => router.back()}
        onRetry={() => router.back()}
      />
    );
  }

  // Initialize ladder state from drill after data is confirmed present
  const currentLadder = ladderState ?? { currentBpm: drill.start_bpm, repCount: 0 };

  const handleRepPress = () => {
    const next = advanceRepOrTempo(currentLadder, drill);
    if (next === null) {
      // Ladder complete — unlock rating pills
      setRatingUnlocked(true);
    } else {
      setLadderState(next);
      // FLE-73: mirror to MMKV so a backgrounded/killed app resumes this rung on remount.
      setDrillLadderState(songId, drillIndex, next);
    }
  };

  const handleRatingSelect = (rating: RatingLiteral) => {
    setSubmittedRating(rating);
    submitDrillRating.mutate(
      buildRatingPayload(songId, drillIndex, drill, rating),
      {
        onError: () => {
          // Reset so the user can retry
          setSubmittedRating(null);
        },
        onSuccess: () => {
          // FLE-73: rating submitted — clear the persisted ladder so a future attempt
          // at this drill starts a fresh ladder instead of resuming the rated one.
          clearDrillLadderState(songId, drillIndex);
          // Navigate back after a short delay so the AlreadyRatedCard overlay is visible.
          // envelope.drill_rated_today_indices refetch on the parent screen will disable
          // the whole-song RatingPills (B1 fix — server-derived drill-primary UI state).
          setTimeout(() => router.replace(`/breakdown/${songId}`), 800);
        },
      },
    );
  };

  // Determine drill scope badge (CONTEXT.md-locked strings from Plan 03 DrillCard)
  const scopeBadge = drill.song_specific ? 'For this song' : 'Any song';

  // Rep counter label depends on ladder state (CONTEXT.md-locked strings)
  const isAtMaxReps = currentLadder.repCount + 1 >= drill.repetitions;
  const isAtTargetBpm = currentLadder.currentBpm >= drill.target_bpm;
  const repButtonLabel =
    isAtMaxReps && !isAtTargetBpm ? 'Push to next tempo →' : 'Done one rep';

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        {/* Drill name header with Fletcher orange accent border-left */}
        <View style={styles.header}>
          <Text style={styles.drillName}>{drill.name}</Text>

          {/* Scope badge + tempo indicator */}
          <View style={styles.subtitleRow}>
            <View style={styles.scopeBadge}>
              <Text style={styles.scopeBadgeText}>{scopeBadge}</Text>
            </View>
            <Text style={styles.tempoIndicator}>
              {'Now: '}{currentLadder.currentBpm}{' BPM · Target: '}{drill.target_bpm}{' BPM'}
            </Text>
          </View>
        </View>

        {/* Body prose */}
        <View style={styles.section}>
          <Text style={styles.whatText}>{drill.what}</Text>
        </View>

        {/* Tab snippet */}
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>TAB</Text>
          <View style={styles.tabContainer}>
            <TabNotation tab={drill.tab_snippet} />
          </View>
        </View>

        {/* Metronome — FLE-5 Task 7.
            The click tempo IS the ladder's current rung: currentLadder.currentBpm is
            passed straight down, so opening a drill arms the metronome at the right
            tempo and every "Push to next tempo →" retunes it. The user never types a
            number. Hidden once rating is unlocked, alongside the rep block. */}
        {!ratingUnlocked && (
          <View style={styles.section}>
            <Text style={styles.sectionLabel}>METRONOME</Text>
            <MetronomeControl bpm={currentLadder.currentBpm} />
          </View>
        )}

        {/* Rep counter / tempo ladder block — hidden when rating is unlocked */}
        {!ratingUnlocked && (
          <View style={styles.section}>
            <Pressable
              style={({ pressed }) => [styles.repButton, pressed && styles.repButtonPressed]}
              onPress={handleRepPress}
              accessibilityRole="button"
              accessibilityLabel={repButtonLabel}
            >
              <Text style={styles.repButtonLabel}>{repButtonLabel}</Text>
            </Pressable>
            <Text style={styles.repCounter}>
              {currentLadder.repCount + 1}{' / '}{drill.repetitions}{' @ '}{currentLadder.currentBpm}{' BPM'}
            </Text>
          </View>
        )}

        {/* Rating area — shown after ladder complete */}
        {ratingUnlocked && (
          <View style={styles.section}>
            <Text style={styles.successCriterion}>{drill.success_criterion}</Text>
            {drill.common_trap ? (
              <Text style={styles.commonTrap}>{'Watch for: '}{drill.common_trap}</Text>
            ) : null}
            <RatingPills
              onSelect={handleRatingSelect}
              selectedRating={submittedRating}
              disabled={submitDrillRating.isPending}
            />
          </View>
        )}
      </ScrollView>

      {/* Full-screen post-rating overlay — appears on success */}
      {submitDrillRating.isSuccess && submittedRating && (
        <View style={StyleSheet.absoluteFill}>
          <AlreadyRatedCard
            rating={submittedRating}
            onDismiss={() => router.replace(`/breakdown/${songId}`)}
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
  header: {
    marginBottom: 24,
    paddingBottom: 20,
    paddingLeft: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#333',
    borderLeftWidth: 3,
    borderLeftColor: '#E07B39',
  },
  drillName: {
    fontSize: 22,
    fontWeight: '800',
    color: '#F5F5F5',
    marginBottom: 10,
  },
  subtitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 10,
  },
  scopeBadge: {
    backgroundColor: '#2A2A2A',
    borderRadius: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  scopeBadgeText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#E07B39',
    letterSpacing: 0.5,
  },
  tempoIndicator: {
    fontSize: 13,
    color: '#999',
  },
  section: {
    marginBottom: 28,
  },
  sectionLabel: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 1.5,
    color: '#666',
    marginBottom: 8,
  },
  whatText: {
    fontSize: 15,
    color: '#CCC',
    lineHeight: 23,
  },
  tabContainer: {
    backgroundColor: '#242424',
    borderRadius: 10,
    padding: 12,
    overflow: 'hidden',
  },
  repButton: {
    backgroundColor: '#E07B39',
    borderRadius: 10,
    paddingVertical: 18,
    paddingHorizontal: 24,
    alignItems: 'center',
    minHeight: 56,
    marginBottom: 12,
  },
  repButtonPressed: {
    opacity: 0.85,
  },
  repButtonLabel: {
    fontSize: 17,
    fontWeight: '700',
    color: '#fff',
  },
  repCounter: {
    fontSize: 14,
    color: '#777',
    textAlign: 'center',
  },
  successCriterion: {
    fontSize: 14,
    color: '#AAA',
    lineHeight: 21,
    marginBottom: 8,
    fontStyle: 'italic',
  },
  commonTrap: {
    fontSize: 13,
    color: '#666',
    marginBottom: 8,
  },
});
