// mobile/src/components/SongOfDayCard.tsx
// Song of the Day card — used on the Today tab (Slice C).
//
// Two variants per UI-SPEC §8:
//   default:      Shows song metadata + primary CTA "See the breakdown" + re-roll ghost (optional).
//   already-rated: When ratedLabel is set — replaces CTA with "Rated: {label}" static line;
//                  hides re-roll ghost button entirely.
//
// Phase 4 (D-05, D-06): breakdown_quota prop adds inline chip + disabled CTA state.
//   - remaining >= 1: chip "{N} left this week" renders below CTA
//   - remaining === 0: CTA disabled, label "Come back in {N} days" (derived from resets_at)
//
// onTitlePress: optional prop — wraps the header/metadata region in a Pressable so the user can
//   re-open the breakdown in read-only mode even after rating.
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { BreakdownQuota } from '../api/todaySong';
import type { components } from '../api/generated/schema';
import { daysUntilReset } from '../utils/quota';

type SongResponse = components['schemas']['SongResponse'];

interface SongOfDayCardProps {
  song: SongResponse;
  ratedLabel?: string | null;
  breakdown_quota?: BreakdownQuota | null;
  onTitlePress?: () => void;
  onSeekBreakdown?: () => void;
  onReroll?: () => void;
}

export function SongOfDayCard({
  song,
  ratedLabel,
  breakdown_quota,
  onTitlePress,
  onSeekBreakdown,
  onReroll,
}: SongOfDayCardProps) {
  const HeaderContent = (
    <View style={styles.header}>
      <Text style={styles.label}>SONG OF THE DAY</Text>
      <Text style={styles.title}>{song.title}</Text>
      <Text style={styles.artist}>{song.artist}</Text>
      <Text style={styles.meta}>
        {song.genre} · {song.bpm} BPM · Key of {song.key}
      </Text>
      <View style={styles.difficultyBadge}>
        <Text style={styles.difficultyText}>{song.difficulty}</Text>
      </View>
    </View>
  );

  return (
    <View style={styles.card}>
      {onTitlePress ? (
        <Pressable onPress={onTitlePress} accessibilityRole="button" accessibilityLabel="Open breakdown">
          {HeaderContent}
        </Pressable>
      ) : (
        HeaderContent
      )}

      <View style={styles.footer}>
        {ratedLabel ? (
          // Already-rated variant (UI-SPEC §8): static "Rated: {label}" — no CTA, no re-roll
          <Text style={styles.ratedLine}>Rated: {ratedLabel}</Text>
        ) : (
          // Default variant: primary CTA (with quota-aware state) + optional re-roll ghost
          <View>
            {onSeekBreakdown && (
              <Pressable
                style={[
                  styles.ctaButton,
                  breakdown_quota?.remaining === 0 && styles.ctaDisabled,
                ]}
                onPress={breakdown_quota?.remaining === 0 ? undefined : onSeekBreakdown}
                disabled={breakdown_quota?.remaining === 0}
                accessibilityRole="button"
                accessibilityLabel={
                  breakdown_quota?.remaining === 0
                    ? `Come back in ${daysUntilReset(breakdown_quota?.resets_at)} days`
                    : 'See the breakdown'
                }
              >
                <Text style={styles.ctaText}>
                  {breakdown_quota?.remaining === 0
                    ? `Come back in ${daysUntilReset(breakdown_quota?.resets_at)} days`
                    : 'See the breakdown'}
                </Text>
              </Pressable>
            )}
            {breakdown_quota && breakdown_quota.remaining >= 1 && (
              <Text style={styles.quotaChip}>{breakdown_quota.remaining} left this week</Text>
            )}
            {onReroll && (
              <Pressable
                style={styles.rerollGhost}
                onPress={onReroll}
                accessibilityRole="button"
                accessibilityLabel="Try a different song"
              >
                <Text style={styles.rerollText}>Try a different song</Text>
              </Pressable>
            )}
          </View>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#242424',
    borderRadius: 12,
    padding: 16,
    marginBottom: 20,
  },
  header: {
    marginBottom: 16,
  },
  label: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 1.5,
    color: '#E07B39',
    marginBottom: 6,
  },
  title: {
    fontSize: 22,
    fontWeight: '800',
    color: '#F5F5F5',
    marginBottom: 4,
  },
  artist: {
    fontSize: 15,
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
    backgroundColor: '#1A1A1A',
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderWidth: 1,
    borderColor: '#E07B39',
  },
  difficultyText: {
    fontSize: 12,
    color: '#E07B39',
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  footer: {
    marginTop: 4,
  },
  ratedLine: {
    fontSize: 14,
    color: '#E07B39',
    fontWeight: '600',
  },
  ctaButton: {
    backgroundColor: '#E07B39',
    borderRadius: 8,
    paddingVertical: 14,
    alignItems: 'center',
    marginBottom: 10,
  },
  ctaDisabled: {
    backgroundColor: '#555',
    opacity: 0.6,
  },
  ctaText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#1A1A1A',
  },
  quotaChip: {
    fontSize: 12,
    color: '#999',
    textAlign: 'center',
    marginTop: 6,
    marginBottom: 4,
  },
  rerollGhost: {
    alignItems: 'center',
    paddingVertical: 8,
  },
  rerollText: {
    fontSize: 14,
    color: '#666',
  },
});
