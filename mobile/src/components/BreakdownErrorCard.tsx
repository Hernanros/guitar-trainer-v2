// mobile/src/components/BreakdownErrorCard.tsx
// Fletcher-voiced error card for Sonnet breakdown failure (UI-SPEC §7).
//
// Four variants:
//
//   default (no code):    "Fletcher lost the thread." + connection-dropped copy + Try again button.
//   BREAKDOWN_CAPPED:     daily per-user cap reached (no Try again — not retryable today).
//   PILOT_BUDGET_SPENT:   the pilot's global dollar ceiling is spent (FLE-92; no Try again).
//   FLETCHER_OUT:         "Fletcher's on a break." + org-level quota body + Try again button.
//
// Background: dark red tint #3A1F1F — the ONLY red on the entire breakdown screen.
// Default copy is locked verbatim from UI-SPEC §7 — do not alter existing strings.
//
// Voice contract (fletcher-identity.md):
//   default heading: "Fletcher lost the thread."
//   FLETCHER_OUT heading: "Fletcher's on a break."
//   Secondary link: "Back to today's song" (all variants)
// No emojis. No exclamation points.
//
// FLE-92 — BREAKDOWN_CAPPED used to lead with "Not my tempo.", which is the label on
// a RATING PILL in the player (RatingPills.tsx). A player who hit the wall read the
// app telling them their playing was off, when all it meant was "you've used today's
// allowance". The heading now names the limit, and the body says when it lifts.
//
// PILOT_BUDGET_SPENT is a separate variant rather than a reworded cap, because the
// two refusals have different remedies: the cap lifts at midnight on its own, the
// ceiling only lifts when Hernan raises it. Telling a user to come back tomorrow for
// something that will still refuse tomorrow is worse than telling them the truth.
//
// Both strings are mirrored from the server (app/api/v1/breakdowns.py) — the server
// ships the message in the 429 body, so an older client and a newer server must not
// disagree about what the limit is.
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

interface BreakdownErrorCardProps {
  /** Called when the user taps "Try again" — should trigger refetch(). Not shown for the two limit variants. */
  onRetry: () => void;
  /** Called when the user taps "Back to today's song" — should trigger router.back(). */
  onBack: () => void;
  /** Error code from server HTTP 429/503 body. Null/undefined → default variant. */
  code?: 'BREAKDOWN_CAPPED' | 'PILOT_BUDGET_SPENT' | 'FLETCHER_OUT' | null;
  /**
   * resets_at ISO string from the HTTP 429 body — the user's next local midnight
   * since FLE-92. Not rendered as a countdown any more: the copy names midnight
   * directly, which is both shorter and always right, where "in N days" had to be
   * recomputed and drifted while the screen sat open.
   */
  resets_at?: string | null;
  /** Per-user daily cap from the server, for the BREAKDOWN_CAPPED body. Defaults to 5. */
  cap?: number | null;
}

export function BreakdownErrorCard({ onRetry, onBack, code, cap }: BreakdownErrorCardProps) {
  let heading: string;
  let body: string;
  let showRetry: boolean;

  if (code === 'BREAKDOWN_CAPPED') {
    heading = "That's your breakdowns for today.";
    body =
      `All ${cap ?? 5} of them. The counter resets at midnight — go put the ones ` +
      `you've got into your hands.`;
    showRetry = false;  // cap is not retryable until the day turns over (D-02)
  } else if (code === 'PILOT_BUDGET_SPENT') {
    heading = "The beta's out of breakdown budget.";
    body =
      "Not you, and nothing's broken — Fletcher's beta runs on a fixed budget for " +
      "new breakdowns, and it's spent. Everything you've already pulled apart still opens.";
    showRetry = false;  // retrying cannot help; only a raised ceiling can
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
