// mobile/src/components/AlreadyRatedCard.tsx
// Full-card post-rating acknowledgement overlay (Slice C).
//
// UI-SPEC §6 locked copy — verbatim, no paraphrase, zero emojis, zero exclamation marks:
//   not_my_tempo:             "Slow the whole thing 10 bpm and rebuild tomorrow. You'll be back."
//   getting_closer:           "Getting closer. Same routine tomorrow — the next pass gets cleaner."
//   thats_what_im_looking_for: "That's the tempo. Push it 5 bpm next session."
//   secondary (all tiers):    "See you tomorrow."
//
// Behavior:
//   - Auto-invokes onDismiss after autoDismissMs (default 2000ms).
//   - clearTimeout on unmount prevents memory leak / stale callback (T-02-04-03 pattern).
//   - Tap-anywhere calls onDismiss immediately.
import { useEffect } from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';
import type { RatingLiteral } from '../api/sessions';

interface AlreadyRatedCardProps {
  rating: RatingLiteral;
  onDismiss: () => void;
  autoDismissMs?: number;
}

const FLETCHER_ACKS: Record<RatingLiteral, string> = {
  not_my_tempo: "Slow the whole thing 10 bpm and rebuild tomorrow. You'll be back.",
  getting_closer: 'Getting closer. Same routine tomorrow — the next pass gets cleaner.',
  thats_what_im_looking_for: "That's the tempo. Push it 5 bpm next session.",
};

export function AlreadyRatedCard({ rating, onDismiss, autoDismissMs = 2000 }: AlreadyRatedCardProps) {
  useEffect(() => {
    const t = setTimeout(onDismiss, autoDismissMs);
    return () => clearTimeout(t);
  }, [onDismiss, autoDismissMs]);

  return (
    <Pressable
      style={styles.card}
      onPress={onDismiss}
      accessibilityRole="button"
      accessibilityLabel="Dismiss"
    >
      <Text style={styles.heading}>{FLETCHER_ACKS[rating]}</Text>
      <Text style={styles.tomorrow}>See you tomorrow.</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flex: 1,
    backgroundColor: '#1A1A1A',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  heading: {
    fontSize: 24,
    fontWeight: '800',
    color: '#F5F5F5',
    textAlign: 'center',
    marginBottom: 16,
    maxWidth: 320,
    lineHeight: 32,
  },
  tomorrow: {
    fontSize: 14,
    color: '#999',
  },
});
