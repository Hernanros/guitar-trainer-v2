// mobile/src/components/SongOfDayCard.tsx
// Today tab hero card.
//
// Design contract (UI-SPEC §1, §2, §4, §8, §10):
//   Eyebrow: "TODAY'S SONG" — all-caps, orange, 11pt 700 letterSpacing 1.5.
//   Fletcher line: dynamic per selector path (see FLETCHER_LINE map).
//   Song title: 24pt 800 #F5F5F5. Artist: 16pt #999. Meta: 13pt #666.
//   Difficulty badge: #2A2A2A bg, orange 1px border, 12pt 700 orange uppercase text.
//   FromTheBankTag rendered below difficulty badge when passed as a child or via prop.
//   Primary CTA: "See the breakdown" — orange fill, 14pt white 700, minHeight 48.
//   Re-roll ghost button: right-aligned, "Not this one? Give me another. (N left today)"
//     hitSlop 8px all sides (Apple HIG 44pt minimum effective target).
//     Disabled state (rerollsLeft=0): text color #666.
//
// Voice contract (fletcher-identity.md, UI-SPEC §1, §10):
//   FLETCHER_LINE map is the canonical source — values are verbatim UI-SPEC copy.
//   No emojis. No exclamation points. All strings terse-direct second-person.
//
// ratedLabel prop (UI-SPEC §8):
//   When set, replaces primary CTA + re-roll row with a static "Rated: {label}" line.
//   Used when the user has already submitted a rating today.
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { components } from '../api/generated/schema';

type SongResponse = components['schemas']['SongResponse'];

/** Selector path → Fletcher line mapping (UI-SPEC §1, §10). */
export type FletcherLineVariant =
  | 'deterministic'  // "You're leaning on this one. Play it today."
  | 'user_bench'     // "Something different today. Loosen up."
  | 'seed_catalog'   // (no line — chip carries the signal)
  | 'rerolled'       // "Different song. Same work."
  | 'already_rated'; // "You already rated this today. See you tomorrow."

const FLETCHER_LINE: Record<FletcherLineVariant, string | null> = {
  deterministic: "You're leaning on this one. Play it today.",
  user_bench: 'Something different today. Loosen up.',
  seed_catalog: null,
  rerolled: 'Different song. Same work.',
  already_rated: 'You already rated this today. See you tomorrow.',
};

interface SongOfDayCardProps {
  song: SongResponse;
  fletcherLineVariant: FletcherLineVariant;
  /** 0 = re-roll spent (disabled button, #666 text); 1 = re-roll available. */
  rerollsLeft: 0 | 1;
  /** Fired when the primary "See the breakdown" CTA is pressed. */
  onTapBreakdown: () => void;
  /** Fired when the re-roll ghost button is pressed (only when rerollsLeft=1). */
  onReroll: () => void;
  /**
   * When set, primary CTA + re-roll button are replaced by "Rated: {label}" static row.
   * Used for the UI-SPEC §8 post-rating same-day return state.
   */
  ratedLabel?: string | null;
  /** Optional chip rendered below the difficulty badge (pass <FromTheBankTag /> here). */
  bankChip?: React.ReactNode;
}

export function SongOfDayCard({
  song,
  fletcherLineVariant,
  rerollsLeft,
  onTapBreakdown,
  onReroll,
  ratedLabel,
  bankChip,
}: SongOfDayCardProps) {
  const line = FLETCHER_LINE[fletcherLineVariant];

  return (
    <View style={styles.card}>
      {/* Eyebrow — "TODAY'S SONG" */}
      <Text style={styles.label}>TODAY'S SONG</Text>

      {/* Fletcher line (optional — null for seed_catalog path) */}
      {line ? <Text style={styles.fletcherLine}>{line}</Text> : null}

      {/* Song title */}
      <Text style={styles.title}>{song.title}</Text>

      {/* Artist */}
      <Text style={styles.artist}>{song.artist ?? ''}</Text>

      {/* Meta line */}
      <Text style={styles.meta}>
        {song.genre} · {song.bpm} BPM · Key of {song.key}
      </Text>

      {/* Difficulty badge */}
      {song.difficulty ? (
        <View style={styles.difficultyBadge}>
          <Text style={styles.difficultyText}>{song.difficulty}</Text>
        </View>
      ) : null}

      {/* Bank chip (FromTheBankTag) rendered below difficulty badge (UI-SPEC §4) */}
      {bankChip ?? null}

      {/* Divider */}
      <View style={styles.divider} />

      {/* CTA area: either rated state or interactive CTA + re-roll */}
      {ratedLabel != null ? (
        <Text style={styles.ratedLine}>Rated: {ratedLabel}</Text>
      ) : (
        <>
          {/* Primary CTA */}
          <Pressable
            style={({ pressed }) => [styles.cta, pressed && styles.ctaPressed]}
            onPress={onTapBreakdown}
            accessibilityRole="button"
            accessibilityLabel="See the breakdown"
          >
            <Text style={styles.ctaText}>See the breakdown</Text>
          </Pressable>

          {/* Re-roll ghost button */}
          <Pressable
            style={styles.rerollGhost}
            onPress={rerollsLeft > 0 ? onReroll : undefined}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            disabled={rerollsLeft === 0}
            accessibilityRole="button"
            accessibilityLabel={`Give me another. ${rerollsLeft} left today.`}
          >
            <Text
              style={[
                styles.rerollText,
                rerollsLeft === 0 && styles.rerollTextDisabled,
              ]}
            >
              Not this one? Give me another. ({rerollsLeft} left today)
            </Text>
          </Pressable>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#242424',
    borderRadius: 12,
    padding: 16,
    marginBottom: 24,
  },
  label: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 1.5,
    color: '#E07B39',
    marginBottom: 6,
  },
  fletcherLine: {
    fontSize: 16,
    lineHeight: 22,
    color: '#A0A0A0',
    marginBottom: 12,
  },
  title: {
    fontSize: 24,
    fontWeight: '800',
    color: '#F5F5F5',
    marginBottom: 4,
  },
  artist: {
    fontSize: 16,
    color: '#999',
    marginBottom: 6,
  },
  meta: {
    fontSize: 13,
    color: '#666',
    marginBottom: 10,
  },
  difficultyBadge: {
    alignSelf: 'flex-start',
    backgroundColor: '#2A2A2A',
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderWidth: 1,
    borderColor: '#E07B39',
  },
  difficultyText: {
    color: '#E07B39',
    fontSize: 12,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  divider: {
    height: 1,
    backgroundColor: '#333',
    marginVertical: 16,
  },
  cta: {
    backgroundColor: '#E07B39',
    paddingVertical: 14,
    borderRadius: 8,
    alignItems: 'center',
    minHeight: 48,
    justifyContent: 'center',
  },
  ctaPressed: {
    opacity: 0.8,
  },
  ctaText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
  },
  rerollGhost: {
    marginTop: 12,
    alignItems: 'flex-end',
  },
  rerollText: {
    color: '#999',
    fontSize: 14,
    fontWeight: '600',
  },
  rerollTextDisabled: {
    color: '#666',
  },
  ratedLine: {
    color: '#E07B39',
    fontSize: 14,
    fontWeight: '600',
  },
});
