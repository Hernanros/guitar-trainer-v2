// mobile/src/app/session/[ordinal].tsx
// The walker — one screen per item, renders by item.kind (FLE-10 Task 6).
//
// One screen per ordinal rather than one screen with internal paging: the
// router's history is the resume cursor when the app is merely backgrounded,
// and it keeps each item's timer mounted to exactly one item's lifecycle
// (design doc §3).
//
// Per-item state is intentionally NOT reconstructed from a local checkpoint on
// resume: the FLE-21 contract accepts active_seconds only on terminal writes
// (design doc §14.1, "answered: no"), so an item's elapsed clock restarts at 0
// on resume by design — there is nothing server-authoritative to restore mid-item.
//
// Rendering rule (issue scope): drill-bank material only, never a per-song
// breakdown blob. song_section / song_play items show title/key/tempo, not a
// rendered tab — that stays on the /breakdown screens.
import { useEffect, useMemo, useRef, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { TabNotation } from '../../components/TabNotation';
import { MetronomeControl } from '../../components/MetronomeControl';
import { RatingPills } from '../../components/RatingPills';
import {
  usePracticeSession,
  useEnterItem,
  useCompleteItem,
  useSkipItem,
  useCompletePracticeSession,
  firstNonTerminalIndex,
  type PracticeSessionItemResponse,
  type PracticeRatingLiteral,
} from '../../api/practiceSessions';
import type { components } from '../../api/generated/schema';

const BLOCK_EYEBROW: Record<PracticeSessionItemResponse['block'], string> = {
  warmup: 'WARM-UP',
  technique: 'THE WORK',
  repertoire: 'THE SONG',
  consolidation: 'ONE YOU OWN',
};

const AUTO_ADVANCE_FACTOR = 1.5;

/** True for the last item in the technique block — the stretch-slot framing line
 * reads off list position, not a hidden generator rule (design doc §7's "client
 * derives nothing" is about generator constants; block adjacency is already in
 * the ordered list the server sent). */
export function isStretchSlot(
  item: PracticeSessionItemResponse,
  items: PracticeSessionItemResponse[],
): boolean {
  if (item.block !== 'technique') return false;
  const next = items[item.item_index + 1];
  return next == null || next.block !== 'technique';
}

export function framingLine(
  item: PracticeSessionItemResponse,
  items: PracticeSessionItemResponse[],
): string | null {
  if (item.is_consolidation) return 'No click. No rating. Just play it.';
  if (item.block === 'warmup') return 'Something you already have. Take it easy.';
  if (isStretchSlot(item, items)) return "This one's above you. That's the point.";
  return null;
}

function formatClock(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export default function SessionWalkerScreen() {
  const { ordinal, sessionId } = useLocalSearchParams<{ ordinal: string; sessionId: string }>();
  const router = useRouter();
  const itemIndex = ordinal != null ? Number.parseInt(ordinal, 10) : NaN;

  const session = usePracticeSession(sessionId ?? null);
  const enterItem = useEnterItem(sessionId ?? '');
  const completeItem = useCompleteItem(sessionId ?? '');
  const skipItem = useSkipItem(sessionId ?? '');
  const completeSession = useCompletePracticeSession(sessionId ?? '');

  const [elapsed, setElapsed] = useState(0);
  const itemStartRef = useRef(Date.now());
  const enteredRef = useRef<number | null>(null);
  const [completedReps, setCompletedReps] = useState(0);

  // Reset the per-item clock and rep counter whenever the ordinal changes.
  useEffect(() => {
    itemStartRef.current = Date.now();
    setElapsed(0);
    setCompletedReps(0);
  }, [itemIndex]);

  // 1Hz tick — item elapsed is a glanceable readout, not a countdown (design doc §6).
  useEffect(() => {
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - itemStartRef.current) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [itemIndex]);

  const items = session.data?.items ?? [];
  const item = items[itemIndex];

  // Fire the enter transition once per ordinal, only for an item the server still
  // considers not_reached — a resumed in_progress item was already entered.
  useEffect(() => {
    if (!sessionId || !item || enteredRef.current === itemIndex) return;
    if (item.state !== 'not_reached') return;
    enteredRef.current = itemIndex;
    enterItem.mutate(itemIndex);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, itemIndex, item?.state]);

  function advanceFrom(nextItems: PracticeSessionItemResponse[]) {
    const nextIndex = firstNonTerminalIndex(nextItems);
    if (nextIndex === null) {
      completeSession.mutate(undefined, {
        onSuccess: (result) =>
          router.replace({
            pathname: '/session/summary',
            params: {
              sessionId,
              completionRatio: String(result.completion_ratio ?? ''),
              doneItems: String(result.done_items),
              skippedItems: String(result.skipped_items),
              plannedItems: String(result.planned_items),
            },
          }),
        onError: () => router.replace({ pathname: '/session/summary', params: { sessionId } }),
      });
    } else {
      router.replace({
        pathname: '/session/[ordinal]',
        params: { ordinal: String(nextIndex), sessionId },
      });
    }
  }

  function handleRating(rating: PracticeRatingLiteral) {
    completeItem.mutate(
      { itemIndex, rating, active_seconds: elapsed, completed_reps: completedReps || null, advance_mode: 'user_tap' },
      { onSuccess: (event) => advanceFrom(event.session.items ?? []) },
    );
  }

  function handleDone() {
    completeItem.mutate(
      { itemIndex, rating: null, active_seconds: elapsed, completed_reps: completedReps || null, advance_mode: 'user_tap' },
      { onSuccess: (event) => advanceFrom(event.session.items ?? []) },
    );
  }

  function handleAutoAdvance() {
    completeItem.mutate(
      { itemIndex, rating: null, active_seconds: elapsed, completed_reps: completedReps || null, advance_mode: 'auto' },
      { onSuccess: (event) => advanceFrom(event.session.items ?? []) },
    );
  }

  function handleSkip() {
    skipItem.mutate(
      { itemIndex, active_seconds: elapsed, completed_reps: completedReps || null },
      { onSuccess: (event) => advanceFrom(event.session.items ?? []) },
    );
  }

  if (session.isPending || !item) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#E07B39" />
      </View>
    );
  }

  const sessionElapsed = (session.data?.elapsed_active_seconds ?? 0) + elapsed;
  const showAutoAdvance = elapsed >= AUTO_ADVANCE_FACTOR * item.planned_seconds;
  const isBusy = completeItem.isPending || skipItem.isPending || completeSession.isPending;
  const framing = framingLine(item, items);

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.clockRow}>
          <Text style={styles.sessionClock}>{formatClock(sessionElapsed)}</Text>
          <Text style={styles.itemClock}>{formatClock(elapsed)}</Text>
        </View>

        <Text style={styles.eyebrow}>{BLOCK_EYEBROW[item.block]}</Text>
        {framing && <Text style={styles.framing}>{framing}</Text>}

        <ItemBody item={item} />

        {item.click_enabled && !item.is_consolidation && (
          <View style={styles.section}>
            <MetronomeControl bpm={item.planned_bpm ?? 80} />
          </View>
        )}

        {item.planned_reps != null && (
          <View style={styles.repRow}>
            <Pressable
              style={styles.repButton}
              onPress={() => setCompletedReps((n) => n + 1)}
              accessibilityRole="button"
              accessibilityLabel="Done one rep"
            >
              <Text style={styles.repButtonLabel}>Done one rep</Text>
            </Pressable>
            <Text style={styles.repCounter}>
              {completedReps} / {item.planned_reps}
            </Text>
          </View>
        )}

        {showAutoAdvance && (
          <Pressable
            style={styles.autoAdvance}
            onPress={handleAutoAdvance}
            disabled={isBusy}
            accessibilityRole="button"
            accessibilityLabel="Move on when you're ready"
          >
            <Text style={styles.autoAdvanceLabel}>Move on when you're ready</Text>
          </Pressable>
        )}

        {item.rated ? (
          <RatingPills onSelect={handleRating} disabled={isBusy} />
        ) : (
          <Pressable
            style={styles.doneButton}
            onPress={handleDone}
            disabled={isBusy}
            accessibilityRole="button"
            accessibilityLabel="Done"
          >
            <Text style={styles.doneButtonLabel}>Done</Text>
          </Pressable>
        )}

        {item.skippable && (
          <Pressable
            onPress={handleSkip}
            disabled={isBusy}
            accessibilityRole="button"
            accessibilityLabel="Skip this one"
            style={styles.skipButton}
          >
            <Text style={styles.skipLabel}>Skip this one</Text>
          </Pressable>
        )}
      </ScrollView>
    </View>
  );
}

function ItemBody({ item }: { item: PracticeSessionItemResponse }) {
  if (item.kind === 'drill' && item.drill) {
    const drill = item.drill;
    return (
      <View>
        <Text style={styles.title}>{drill.name}</Text>
        <Text style={styles.body}>{drill.what}</Text>
        <View style={styles.tabContainer}>
          <TabNotation tab={drill.tab_snippet as components['schemas']['Tab']} />
        </View>
        {!item.rated ? null : (
          <>
            <Text style={styles.successCriterion}>{drill.success_criterion}</Text>
            {drill.common_trap ? (
              <Text style={styles.commonTrap}>Watch for: {drill.common_trap}</Text>
            ) : null}
          </>
        )}
      </View>
    );
  }

  if (item.song) {
    const song = item.song;
    return (
      <View>
        <Text style={styles.title}>{song.title}</Text>
        <Text style={styles.body}>
          {song.artist}
          {item.planned_bpm ? ` · ${item.planned_bpm} BPM` : ''}
          {song.key ? ` · ${song.key}` : ''}
        </Text>
      </View>
    );
  }

  return null;
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1A1A1A' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#1A1A1A' },
  content: { padding: 16, paddingBottom: 40 },
  clockRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 16,
  },
  sessionClock: { fontSize: 13, color: '#666' },
  itemClock: { fontSize: 13, color: '#999', fontVariant: ['tabular-nums'] },
  eyebrow: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 1.5,
    color: '#E07B39',
    marginBottom: 8,
  },
  framing: {
    fontSize: 14,
    color: '#999',
    fontStyle: 'italic',
    marginBottom: 20,
  },
  title: { fontSize: 22, fontWeight: '800', color: '#F5F5F5', marginBottom: 10 },
  body: { fontSize: 15, color: '#CCC', lineHeight: 22, marginBottom: 16 },
  tabContainer: {
    backgroundColor: '#242424',
    borderRadius: 10,
    padding: 12,
    overflow: 'hidden',
    marginBottom: 16,
  },
  successCriterion: { fontSize: 14, color: '#AAA', lineHeight: 21, marginBottom: 8, fontStyle: 'italic' },
  commonTrap: { fontSize: 13, color: '#666', marginBottom: 8 },
  section: { marginBottom: 20 },
  repRow: { marginBottom: 20 },
  repButton: {
    backgroundColor: '#E07B39',
    borderRadius: 10,
    paddingVertical: 16,
    alignItems: 'center',
    minHeight: 56,
    marginBottom: 8,
  },
  repButtonLabel: { fontSize: 16, fontWeight: '700', color: '#fff' },
  repCounter: { fontSize: 13, color: '#777', textAlign: 'center' },
  autoAdvance: {
    alignItems: 'center',
    paddingVertical: 12,
    marginBottom: 8,
  },
  autoAdvanceLabel: { fontSize: 13, color: '#E07B39', fontWeight: '600' },
  doneButton: {
    backgroundColor: '#E07B39',
    borderRadius: 10,
    paddingVertical: 16,
    alignItems: 'center',
    minHeight: 56,
    marginTop: 16,
  },
  doneButtonLabel: { fontSize: 16, fontWeight: '700', color: '#fff' },
  skipButton: { alignItems: 'center', paddingVertical: 16 },
  skipLabel: { fontSize: 14, color: '#666' },
});
