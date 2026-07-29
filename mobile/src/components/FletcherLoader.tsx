// mobile/src/components/FletcherLoader.tsx
// Shared Fletcher 3-phase loader rotation.
//
// Extracted from mobile/src/app/onboarding/preferences.tsx (Phase 2, 02-04).
// Phase 3 (03-01): generalized so different screens can pass different messages.
//
// Voice contract (fletcher-identity.md):
//   No emojis. No exclamation points.
//   Today tab breakdown loader messages: 'Fletcher is listening...' / 'Working out the fingering...' / 'Almost there...'
//   Onboarding loader messages (preferences.tsx): 'Fletcher is listening...' / 'Working on your first lesson plan...' / 'Almost there...'
//
// Timer contract (T-02-04-03 mitigation):
//   Both setTimeout handles are stored and cleared on unmount or when isPending flips false.
//   This prevents stale state updates after the component is removed (e.g., navigation).
//
// Zustand slice (uiStore.loaderMessageIndex):
//   The index advances from 0 → 1 → 2 via setLoaderMessageIndex.
//   resetLoader() returns to 0 when pending ends or component unmounts.
//   Shared across all FletcherLoader instances — this is safe because only one loader
//   can be visible at a time (navigation stack ensures mutual exclusion).
import { useEffect } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { useUIStore } from '../store/uiStore';

interface FletcherLoaderProps {
  /**
   * Exactly 3 messages cycling through the 0–3s / 3–8s / 8s+ time windows.
   * Use `as const` at call sites so TypeScript infers a tuple type.
   */
  messages: readonly [string, string, string];
  /** When true, timers fire and loader shows. When false, loader clears and resets. */
  isPending: boolean;
  /**
   * Override the default 3s / 8s thresholds (useful for testing).
   * @default [3000, 8000]
   */
  thresholdsMs?: readonly [number, number];
}

export function FletcherLoader({
  messages,
  isPending,
  thresholdsMs = [3000, 8000],
}: FletcherLoaderProps) {
  const loaderMessageIndex = useUIStore((s) => s.loaderMessageIndex);
  const setLoaderMessageIndex = useUIStore((s) => s.setLoaderMessageIndex);
  const resetLoader = useUIStore((s) => s.resetLoader);

  useEffect(() => {
    if (!isPending) {
      resetLoader();
      return;
    }
    const t1 = setTimeout(() => setLoaderMessageIndex(1), thresholdsMs[0]);
    const t2 = setTimeout(() => setLoaderMessageIndex(2), thresholdsMs[1]);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [isPending, setLoaderMessageIndex, resetLoader, thresholdsMs]);

  return (
    <View style={styles.loader}>
      <ActivityIndicator size="large" color="#E07B39" />
      <Text style={styles.loaderText}>{messages[loaderMessageIndex as 0 | 1 | 2]}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  loader: {
    flex: 1,
    backgroundColor: '#1A1A1A',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  loaderText: {
    color: '#F5F5F5',
    marginTop: 16,
    fontSize: 15,
    textAlign: 'center',
    maxWidth: 300,
    lineHeight: 22,
  },
});
