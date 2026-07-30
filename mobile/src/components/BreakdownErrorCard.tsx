// mobile/src/components/BreakdownErrorCard.tsx
// Fletcher-voiced error card for Sonnet breakdown failure (UI-SPEC §7).
//
// Three variants (Phase 4 Slice B extension):
//
//   default (no code):    "Fletcher lost the thread." + connection-dropped copy + Try again button.
//   BREAKDOWN_CAPPED:     "Not my tempo." + per-user cap body (no Try again — cap is not retryable).
//   FLETCHER_OUT:         "Fletcher's on a break." + org-level quota body + Try again button.
//
// Background: dark red tint #3A1F1F — the ONLY red on the entire breakdown screen.
// Default copy is locked verbatim from UI-SPEC §7 — do not alter existing strings.
//
// Voice contract (fletcher-identity.md):
//   default heading: "Fletcher lost the thread."
//   BREAKDOWN_CAPPED heading: "Not my tempo."
//   FLETCHER_OUT heading: "Fletcher's on a break."
//   Secondary link: "Back to today's song" (all variants)
// No emojis. No exclamation points.
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { daysUntilReset } from '../utils/quota';

interface BreakdownErrorCardProps {
  /** Called when the user taps "Try again" — should trigger refetch(). Not shown for BREAKDOWN_CAPPED. */
  onRetry: () => void;
  /** Called when the user taps "Back to today's song" — should trigger router.back(). */
  onBack: () => void;
  /** Error code from server HTTP 429/503 body. Null/undefined → default variant. */
  code?: 'BREAKDOWN_CAPPED' | 'FLETCHER_OUT' | null;
  /** resets_at ISO string from HTTP 429 body — used by BREAKDOWN_CAPPED variant to compute days. */
  resets_at?: string | null;
}

export function BreakdownErrorCard({ onRetry, onBack, code, resets_at }: BreakdownErrorCardProps) {
  let heading: string;
  let body: string;
  let showRetry: boolean;

  if (code === 'BREAKDOWN_CAPPED') {
    heading = 'Not my tempo.';
    body = `You've had 3 breakdowns this week. Come back in ${daysUntilReset(resets_at)} days.`;
    showRetry = false;  // cap is not retryable (D-02)
  } else if (code === 'FLETCHER_OUT') {
    heading = "Fletcher's on a break.";
    body = 'Try again in an hour.';
    showRetry = true;
  } else {
    heading = 'Fletcher lost the thread.';
    body = 'The connection dropped mid-thought. Give it a minute — try again.';
    showRetry = true;
  }

  return (
    <View style={styles.card}>
      <Text style={styles.heading}>{heading}</Text>
      <Text style={styles.body}>{body}</Text>
      {showRetry && (
        <Pressable
          style={({ pressed }) => [styles.cta, pressed && styles.ctaPressed]}
          onPress={onRetry}
          accessibilityRole="button"
          accessibilityLabel="Try again"
        >
          <Text style={styles.ctaText}>Try again</Text>
        </Pressable>
      )}
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
