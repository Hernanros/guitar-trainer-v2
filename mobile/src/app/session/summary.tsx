// mobile/src/app/session/summary.tsx
// Completion state (FLE-10 Task 6, R8).
//
// completion_ratio / done_items / skipped_items / planned_items are server-computed
// (design doc §8) and arrive as router params from the walker's own /complete call —
// this screen displays them, it does not recompute a ratio from item states it
// reads itself. If the walker's completeSession call failed (network drop right at
// the finish line), completionRatio arrives empty and this screen falls back to
// reading the session's own completion_ratio field instead of guessing.
//
// UI-SPEC §11 copy — verbatim, no paraphrase.
import { View, Text, StyleSheet, Pressable } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { usePracticeSession } from '../../api/practiceSessions';

export function completionBody(ratio: number | null): string {
  if (ratio != null && ratio >= 0.8) {
    return "You finished what was in front of you. Same time tomorrow.";
  }
  return "You got through most of it. Tomorrow's will be shorter.";
}

export default function SessionSummaryScreen() {
  const { sessionId, completionRatio } = useLocalSearchParams<{
    sessionId: string;
    completionRatio?: string;
  }>();
  const router = useRouter();
  const session = usePracticeSession(sessionId ?? null);

  const parsedRatio = completionRatio ? Number.parseFloat(completionRatio) : NaN;
  const ratio = Number.isFinite(parsedRatio) ? parsedRatio : (session.data?.completion_ratio ?? null);

  return (
    <View style={styles.container}>
      <View style={styles.content}>
        <Text style={styles.heading}>Session done.</Text>
        <Text style={styles.body}>{completionBody(ratio)}</Text>
        <Text style={styles.secondary}>See you tomorrow.</Text>

        <Pressable
          style={styles.doneButton}
          onPress={() => router.replace('/(tabs)')}
          accessibilityRole="button"
          accessibilityLabel="Back to today"
        >
          <Text style={styles.doneButtonLabel}>Back to today</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1A1A1A' },
  content: { flex: 1, justifyContent: 'center', padding: 24 },
  heading: { fontSize: 26, fontWeight: '800', color: '#F5F5F5', marginBottom: 16 },
  body: { fontSize: 16, color: '#CCC', lineHeight: 24, marginBottom: 8 },
  secondary: { fontSize: 14, color: '#777', marginBottom: 32 },
  doneButton: {
    backgroundColor: '#E07B39',
    borderRadius: 10,
    paddingVertical: 16,
    alignItems: 'center',
    minHeight: 56,
  },
  doneButtonLabel: { fontSize: 16, fontWeight: '700', color: '#fff' },
});
