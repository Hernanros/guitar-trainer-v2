// mobile/src/components/DrillCard.tsx
// Phase 4.1 Plan 03 — Fletcher-voiced drill card for the breakdown screen.
//
// A drill is Sonnet's decomposition of a song into a named, tempo-laddered
// practice unit that targets a single skill_node. This card is the drill-list
// entry point on the breakdown screen — rendering name + subtitle chips (For
// this song / Any song + tempo ladder) + what/success/trap copy + a Start-drill
// Pressable that fires onStart(drillIndex).
//
// Fletcher voice (per .planning/design/fletcher-identity.md):
//   sharp, diagnostic, next-step. Never vague. Never punitive. No exclamation
//   points. "Start drill" is the CTA — not "Let's go!" — matching RatingPills
//   copy conventions.
//
// Styling matches the existing techniqueCard pattern in breakdown/[songId].tsx
// (backgroundColor #242424, borderLeftWidth 3, borderLeftColor #E07B39) so a
// drill card visually fits the section stack.
//
// Layout inside the card (top to bottom):
//   Row 1: drill.name (title — 15px 700 #F5F5F5)
//   Row 2: subtitle chips — "For this song" | "Any song" · "60 → 70 BPM"
//   Row 3: drill.what (body copy)
//   Row 4: "Unlocked: {success_criterion}"
//   Row 5 (conditional): "Watch for: {common_trap}"
//   Row 6: full-width Pressable — "Start drill" (44px min tap target)
//
// Not included: tab_snippet rendering (lives on the focused drill screen —
// Plan 04) and rep counter (also Plan 04). This card is JUST the list entry.
//
// Expo v57 API surface (verified per mobile/AGENTS.md convention against
// https://docs.expo.dev/versions/v57.0.0/ — same imports as RatingPills.tsx
// which is already in production).
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { components } from '../api/generated/schema';

export type Drill = components['schemas']['Drill'];

export interface DrillCardProps {
  drill: Drill;
  drillIndex: number;
  onStart: (drillIndex: number) => void;
}

// ---------------------------------------------------------------------------
// Pure-fn helpers (exported for compile-time contract tests in DrillCard.test.tsx)
// ---------------------------------------------------------------------------

/** "For this song" if drill.song_specific, else "Any song". Foundation of the subtitle chip. */
export function formatSubtitle(drill: Drill): string {
  return drill.song_specific ? 'For this song' : 'Any song';
}

/** "60 → 70 BPM" — the tempo ladder chip. Uses an en dash arrow, matches Fletcher's sharp tone. */
export function formatTempoLadder(drill: Drill): string {
  return `${drill.start_bpm} → ${drill.target_bpm} BPM`;
}

/** "Start drill: Isolate the b3→3 slide" — accessibility label for the Start Pressable. */
export function formatStartAccessibilityLabel(drill: Drill): string {
  return `Start drill: ${drill.name}`;
}

/** True iff drill.common_trap is a non-empty string. Used to conditionally render Row 5. */
export function shouldShowCommonTrap(drill: Drill): boolean {
  return typeof drill.common_trap === 'string' && drill.common_trap.length > 0;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DrillCard({ drill, drillIndex, onStart }: DrillCardProps) {
  return (
    <View style={styles.card}>
      {/* Row 1: name */}
      <Text style={styles.name}>{drill.name}</Text>

      {/* Row 2: subtitle chips */}
      <Text style={styles.subtitle}>
        {formatSubtitle(drill)} · {formatTempoLadder(drill)}
      </Text>

      {/* Row 3: body */}
      <Text style={styles.what}>{drill.what}</Text>

      {/* Row 4: success criterion */}
      <Text style={styles.success}>Unlocked: {drill.success_criterion}</Text>

      {/* Row 5 (conditional): common trap */}
      {shouldShowCommonTrap(drill) && (
        <Text style={styles.trap}>Watch for: {drill.common_trap}</Text>
      )}

      {/* Row 6: Start drill Pressable */}
      <Pressable
        style={({ pressed }) => [styles.startButton, pressed && styles.startButtonPressed]}
        onPress={() => onStart(drillIndex)}
        accessibilityRole="button"
        accessibilityLabel={formatStartAccessibilityLabel(drill)}
      >
        <Text style={styles.startButtonText}>Start drill</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#242424',
    borderRadius: 10,
    padding: 14,
    marginBottom: 10,
    borderLeftWidth: 3,
    borderLeftColor: '#E07B39',
  },
  name: {
    fontSize: 15,
    fontWeight: '700',
    color: '#F5F5F5',
    marginBottom: 4,
  },
  subtitle: {
    fontSize: 13,
    color: '#999',
    marginBottom: 10,
  },
  what: {
    fontSize: 14,
    color: '#AAA',
    lineHeight: 21,
    marginBottom: 10,
  },
  // Green success-tint palette does not exist in the app yet. Fell back to a
  // muted green (#8FBF6A) that reads as "unlocked / criterion met" while
  // avoiding a full accent-color clash with the Fletcher orange primary.
  success: {
    fontSize: 12,
    color: '#8FBF6A',
    marginBottom: 6,
  },
  trap: {
    fontSize: 12,
    color: '#999',
    fontStyle: 'italic',
    marginBottom: 10,
  },
  startButton: {
    marginTop: 8,
    height: 44,
    backgroundColor: '#E07B39',
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  startButtonPressed: {
    opacity: 0.85,
  },
  startButtonText: {
    fontSize: 14,
    fontWeight: '700',
    color: '#1A1A1A',
  },
});
