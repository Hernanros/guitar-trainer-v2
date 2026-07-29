// mobile/src/components/FletcherLoader.tsx
// Shared Fletcher loader rotation component.
//
// Extracted from onboarding/preferences.tsx's loader block (Phase 2 02-04).
// Used by: preferences.tsx (onboarding flow) + breakdown/[songId].tsx (Sonnet breakdown fetch).
//
// Timing behavior: the CALLER manages the rotation timers via Zustand loaderMessageIndex
// (uiStore setLoaderMessageIndex / resetLoader). This component is pure display.
// When isPending flips to false, the caller resets the index.
//
// Phase 3 UI-SPEC §3 copy contract (breakdown loader):
//   0-3s   "Fletcher is listening..."
//   3-8s   "Working out the fingering..."
//   8+s    "Almost there..."
//
// Voice contract: no emojis, no exclamation points.
import React, { useEffect } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { useUIStore } from '../store/uiStore';

interface FletcherLoaderProps {
  /** Array of 3 message strings to rotate through (index driven by uiStore). */
  messages: readonly [string, string, string];
  /** When true, renders the loader. When false (never should render but guard included), renders nothing. */
  isPending: boolean;
}

export function FletcherLoader({ messages, isPending }: FletcherLoaderProps) {
  const loaderMessageIndex = useUIStore((s) => s.loaderMessageIndex);
  const setLoaderMessageIndex = useUIStore((s) => s.setLoaderMessageIndex);
  const resetLoader = useUIStore((s) => s.resetLoader);

  // Manage rotation timers while isPending.
  // Cleanup on unmount or when isPending becomes false.
  useEffect(() => {
    if (!isPending) {
      resetLoader();
      return;
    }
    const t1 = setTimeout(() => setLoaderMessageIndex(1), 3000);
    const t2 = setTimeout(() => setLoaderMessageIndex(2), 8000);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [isPending, setLoaderMessageIndex, resetLoader]);

  if (!isPending) return null;

  return (
    <View style={styles.container}>
      <ActivityIndicator size="large" color="#E07B39" />
      <Text style={styles.message}>{messages[loaderMessageIndex as 0 | 1 | 2]}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#1A1A1A',
    padding: 24,
  },
  message: {
    marginTop: 16,
    fontSize: 16,
    color: '#A0A0A0',
    textAlign: 'center',
  },
});
