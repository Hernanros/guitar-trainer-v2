// mobile/src/app/onboarding/preferences.tsx
// Wizard section 5 of 5 — session preferences + Complete tap.
//
// Voice contract: fletcher-identity.md
//   Section 1 heading: "How long do you have most days?" (D-12)
//   Body (sub-text): "Pick a session length. Fletcher will size lessons to fit."
//   Section 2 heading: "How should Fletcher mark the wins?" (D-13)
//   Chip labels: "15 min" / "30 min" / "45 min" / "60 min"
//   Radio options (exact):
//     "Streak: I show up daily, count me."
//     "Weekly digest: show me what I did on Sunday."
//     "Monthly milestone: mark the big wins."
//   CTA: "Complete" — moment-of-commitment; no exclamation point.
//   Loader (isPending): "Fletcher is listening..." — per <voice_contract>.
//   Error (isError): "Fletcher lost the thread. Try that again."
//   Pattern: fletcher-identity.md line 36: "sarcasm about the app itself is fine."
//   The error copy uses mild self-deprecation ("lost the thread") that lands warm.
//   No emojis.
//
// D-12: session length has no default — Complete button disabled until a chip is selected.
// D-13: retention format defaults to 'streak' if user picks nothing.
// D-03: preferences persisted to MMKV via setWizardPreferences on every change.
//       On resume, getWizardPreferences() prefills the selections.
//
// Complete tap builds UserBootstrapRequest:
//   - songs: {can_play, working_on, aspirational} split from wizard MMKV sections.
//   - raw_input: verbatim text as typed (for D-07 fail-open in 02-03).
//   - preferences: session_length_min + retention_format.
//   On success: clearWizardState(), setLastSection('index'), router.replace('/(tabs)').
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { FletcherIntroCard } from '../../components/FletcherIntroCard';
import { SessionLengthChips } from '../../components/SessionLengthChips';
import { RetentionFormatRadio } from '../../components/RetentionFormatRadio';
import { useUserBootstrap, type UserBootstrapRequest } from '../../api/users';
import {
  clearWizardState,
  getOrCreateUserId,
  getWizardPreferences,
  getWizardSection,
  setLastSection,
  setWizardPreferences,
} from '../../api/mmkv';

type LengthValue = 15 | 30 | 45 | 60;
type RetentionFormat = 'streak' | 'weekly_digest' | 'monthly_milestone';

export default function OnboardingPreferences() {
  // Prefill from MMKV on mount (D-03 resume behavior for preferences section).
  const initial = getWizardPreferences();
  const [sessionLength, setSessionLength] = useState<LengthValue | null>(
    initial.session_length_min,
  );
  const [retention, setRetention] = useState<RetentionFormat | null>(initial.retention_format);

  const bootstrap = useUserBootstrap();

  // D-03: persist preference selections to MMKV on every change.
  useEffect(() => {
    setWizardPreferences({ session_length_min: sessionLength, retention_format: retention });
  }, [sessionLength, retention]);

  const onComplete = () => {
    // Split free-text lines: trim whitespace, filter empty strings.
    const parseLines = (raw: string): string[] =>
      raw
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean);

    const rawCanPlay = getWizardSection('play');
    const rawWorking = getWizardSection('working-on');
    const rawAspire = getWizardSection('aspire');

    const body: UserBootstrapRequest = {
      user_id: getOrCreateUserId(),
      songs: {
        can_play: parseLines(rawCanPlay),
        working_on: parseLines(rawWorking),
        aspirational: parseLines(rawAspire),
      },
      preferences: {
        // sessionLength is guaranteed non-null here: Complete is disabled while null (D-12).
        session_length_min: sessionLength!,
        // D-13: default to 'streak' if user picks nothing.
        retention_format: retention ?? 'streak',
      },
      raw_input: {
        can_play: rawCanPlay,
        working_on: rawWorking,
        aspirational: rawAspire,
      },
    };

    bootstrap.mutate(body, {
      onSuccess: () => {
        // Clear wizard state (T-02-02-01: abandoned data should not linger).
        clearWizardState();
        // Reset resume pointer for potential future re-run (02-04).
        setLastSection('index');
        router.replace('/(tabs)');
      },
    });
  };

  // D-12: Complete disabled until session length is selected (force intentional choice).
  const canComplete = sessionLength !== null;

  // Loader: "Fletcher is listening..." — minimal in this slice.
  // Full rotation (Working on your first lesson plan... / Almost there...) ships in 02-04.
  if (bootstrap.isPending) {
    return (
      <View style={styles.loader}>
        <ActivityIndicator size="large" color="#E07B39" />
        <Text style={styles.loaderText}>Fletcher is listening...</Text>
      </View>
    );
  }

  return (
    <FletcherIntroCard
      heading="How long do you have most days?"
      body="Pick a session length. Fletcher will size lessons to fit."
      cta="Complete"
      onNext={onComplete}
      disabled={!canComplete}
      progress={{ current: 5, total: 5 }}
    >
      <SessionLengthChips value={sessionLength} onChange={setSessionLength} />

      {/* Section 2 heading: D-13 retention format */}
      <Text style={styles.subHeading}>How should Fletcher mark the wins?</Text>
      <RetentionFormatRadio value={retention} onChange={setRetention} />

      {/* Error state: fletcher-identity.md line 36 — sarcasm about the app is fine. */}
      {bootstrap.isError && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>Fletcher lost the thread. Try that again.</Text>
        </View>
      )}
    </FletcherIntroCard>
  );
}

const styles = StyleSheet.create({
  loader: {
    flex: 1,
    backgroundColor: '#1A1A1A',
    alignItems: 'center',
    justifyContent: 'center',
  },
  loaderText: {
    color: '#F5F5F5',
    marginTop: 12,
    fontSize: 14,
  },
  subHeading: {
    color: '#F5F5F5',
    fontSize: 16,
    fontWeight: '700',
    marginTop: 8,
    marginBottom: 12,
  },
  errorBox: {
    backgroundColor: '#3A1F1F',
    padding: 12,
    borderRadius: 6,
    marginBottom: 12,
    borderLeftWidth: 3,
    borderLeftColor: '#ff6b6b',
  },
  errorText: {
    color: '#ffb0b0',
    fontSize: 14,
  },
});
