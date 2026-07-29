// mobile/src/components/BreakdownErrorCard.tsx
// Fletcher-voiced error card for Sonnet breakdown failure (UI-SPEC §7).
//
// Shows when GET /api/v1/songs/{id}/breakdown returns 503 (Sonnet failed after retry).
// Background: dark red tint #3A1F1F — the ONLY red on the entire breakdown screen.
// Copy is locked verbatim from UI-SPEC §7 — do not alter these strings.
//
// Voice contract (fletcher-identity.md):
//   Heading: "Fletcher lost the thread."
//   Body: "The connection dropped mid-thought. Give it a minute — try again."
//   Primary CTA: "Try again"
//   Secondary link: "Back to today's song"
// No emojis. No exclamation points.
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

interface BreakdownErrorCardProps {
  /** Called when the user taps "Try again" — should trigger refetch(). */
  onRetry: () => void;
  /** Called when the user taps "Back to today's song" — should trigger router.back(). */
  onBack: () => void;
}

export function BreakdownErrorCard({ onRetry, onBack }: BreakdownErrorCardProps) {
  return (
    <View style={styles.card}>
      <Text style={styles.heading}>Fletcher lost the thread.</Text>
      <Text style={styles.body}>
        The connection dropped mid-thought. Give it a minute — try again.
      </Text>
      <Pressable
        style={({ pressed }) => [styles.cta, pressed && styles.ctaPressed]}
        onPress={onRetry}
        accessibilityRole="button"
        accessibilityLabel="Try again"
      >
        <Text style={styles.ctaText}>Try again</Text>
      </Pressable>
      <Pressable
        style={styles.backLink}
        onPress={onBack}
        accessibilityRole="link"
        accessibilityLabel="Back to today's song"
      >
        <Text style={styles.backLinkText}>Back to today's song</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    flex: 1,
    justifyContent: 'center',
    backgroundColor: '#3A1F1F',
    padding: 24,
    borderRadius: 12,
    margin: 16,
  },
  heading: {
    fontSize: 20,
    fontWeight: '700',
    color: '#F5F5F5',
    marginBottom: 12,
  },
  body: {
    fontSize: 16,
    lineHeight: 22,
    color: '#A0A0A0',
    marginBottom: 24,
  },
  cta: {
    backgroundColor: '#E07B39',
    paddingVertical: 14,
    borderRadius: 8,
    alignItems: 'center',
    minHeight: 48,
  },
  ctaPressed: {
    opacity: 0.8,
  },
  ctaText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
  },
  backLink: {
    marginTop: 16,
    alignItems: 'center',
    paddingVertical: 8,
  },
  backLinkText: {
    color: '#999',
    fontSize: 14,
  },
});
