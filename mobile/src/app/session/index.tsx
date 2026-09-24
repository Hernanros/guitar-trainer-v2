// mobile/src/app/session/index.tsx
// Session entry — resolve-or-generate today's session, then redirect (FLE-10 Task 6).
//
// POST /api/v1/practice-sessions/today answers 201 (fresh plan) or 200 (an
// already-open session — the user may be mid-session). Either way this screen's
// only job is: land the walker on the first non-terminal item, or the summary if
// every item is already terminal. It never renders session content itself.
import { useEffect, useRef } from 'react';
import { View, ActivityIndicator, Text, StyleSheet, Pressable } from 'react-native';
import { useRouter } from 'expo-router';
import { useTodaySession, firstNonTerminalIndex } from '../../api/practiceSessions';

export default function SessionEntryScreen() {
  const router = useRouter();
  const todaySession = useTodaySession();
  // React 18 StrictMode / fast-refresh double-invokes effects; the mutation itself
  // is safe to double-fire (server-side partial unique index), but a ref keeps this
  // screen from racing two redirects.
  const fired = useRef(false);

  useEffect(() => {
    if (fired.current) return;
    fired.current = true;
    todaySession.mutate(undefined, {
      onSuccess: ({ data: session }) => {
        const nextIndex = firstNonTerminalIndex(session.items);
        if (nextIndex === null) {
          router.replace({ pathname: '/session/summary', params: { sessionId: session.id } });
        } else {
          router.replace({
            pathname: '/session/[ordinal]',
            params: { ordinal: String(nextIndex), sessionId: session.id },
          });
        }
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (todaySession.isError) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>Could not start today's session.</Text>
        <Pressable
          style={styles.retryButton}
          onPress={() => {
            fired.current = false;
            todaySession.reset();
          }}
          accessibilityRole="button"
          accessibilityLabel="Retry"
        >
          <Text style={styles.retryLabel}>Retry</Text>
        </Pressable>
        <Pressable onPress={() => router.back()} accessibilityRole="button" accessibilityLabel="Back">
          <Text style={styles.backLabel}>Back</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.center}>
      <ActivityIndicator size="large" color="#E07B39" />
    </View>
  );
}

const styles = StyleSheet.create({
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 16,
    backgroundColor: '#1A1A1A',
    gap: 16,
  },
  errorText: {
    fontSize: 15,
    color: '#CCC',
    textAlign: 'center',
  },
  retryButton: {
    backgroundColor: '#E07B39',
    borderRadius: 10,
    paddingVertical: 14,
    paddingHorizontal: 28,
  },
  retryLabel: {
    fontSize: 15,
    fontWeight: '700',
    color: '#fff',
  },
  backLabel: {
    fontSize: 14,
    color: '#999',
  },
});
