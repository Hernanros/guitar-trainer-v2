// mobile/src/components/FletcherIntroCard.tsx
// Reusable intro card rendered at the top of every onboarding wizard section.
//
// Voice contract: fletcher-identity.md
//   - No emojis in any string passed to heading/body/cta.
//   - No exclamation points except at genuine peak moments.
//   - Diagnosis → next step pattern where applicable; for Welcome/section intros,
//     "diagnosis" becomes warm framing (there is no user output to diagnose yet).
//
// Slot: children renders below the body text, above the CTA.
// This lets each section inject its own input UI (SongInputArea, chips, radio)
// without duplicating the card shell.
//
// progress prop: 1-indexed current + total drive the orange dot indicator.
// progress.current === 1 means dot 0 is filled (first dot active).
// showPortrait: deferred to Phase 2 UI-phase; defaults false.
import React from 'react';
import { View, Text, StyleSheet, Pressable, ScrollView } from 'react-native';

export interface FletcherIntroCardProps {
  heading: string;
  body: string;
  /** Defaults to 'Continue'. Use 'Start' on Welcome, 'Complete' on Preferences. */
  cta?: string;
  onNext: () => void;
  disabled?: boolean;
  /** Progress dots: current is 1-indexed (1 = first section). total = 5. */
  progress?: { current: number; total: number };
  /** Character portrait slot — deferred to Phase 2 UI-phase. Defaults false. */
  showPortrait?: boolean;
  /** Slot for section input UI rendered between body and CTA. */
  children?: React.ReactNode;
}

export function FletcherIntroCard({
  heading,
  body,
  cta = 'Continue',
  onNext,
  disabled = false,
  progress,
  children,
}: FletcherIntroCardProps) {
  return (
    <View style={styles.container}>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {progress && (
          <View style={styles.progressRow} accessibilityLabel={`Step ${progress.current} of ${progress.total}`}>
            {Array.from({ length: progress.total }, (_, i) => (
              <View
                key={i}
                style={[styles.dot, i < progress.current ? styles.dotActive : null]}
              />
            ))}
          </View>
        )}
        <Text style={styles.heading}>{heading}</Text>
        <Text style={styles.body}>{body}</Text>
        {children}
      </ScrollView>
      <Pressable
        style={({ pressed }) => [
          styles.cta,
          disabled && styles.ctaDisabled,
          pressed && !disabled && styles.ctaPressed,
        ]}
        onPress={onNext}
        disabled={disabled}
        accessibilityRole="button"
        accessibilityLabel={cta}
      >
        <Text style={styles.ctaText}>{cta}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  // Dark palette matches Today tab (#1A1A1A bg, #F5F5F5 text, #E07B39 accent).
  container: {
    flex: 1,
    backgroundColor: '#1A1A1A',
    paddingHorizontal: 20,
    paddingTop: 24,
    paddingBottom: 16,
  },
  scrollContent: {
    paddingBottom: 24,
  },
  progressRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 24,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: '#3A3A3A',
  },
  dotActive: {
    backgroundColor: '#E07B39',
  },
  heading: {
    fontSize: 24,
    fontWeight: '700',
    color: '#F5F5F5',
    marginBottom: 12,
  },
  body: {
    fontSize: 16,
    color: '#A0A0A0',
    lineHeight: 22,
    marginBottom: 24,
  },
  cta: {
    backgroundColor: '#E07B39',
    paddingVertical: 14,
    borderRadius: 8,
    alignItems: 'center',
  },
  ctaPressed: {
    opacity: 0.8,
  },
  ctaDisabled: {
    backgroundColor: '#4A3A2A',
  },
  ctaText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
  },
});
