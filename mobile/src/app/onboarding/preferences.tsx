// mobile/src/app/onboarding/preferences.tsx
// Wizard section 5 of 5 — session preferences + Complete tap.
//
// Voice contract (fletcher-identity.md + 02-04 <voice_contract>):
//   Section 1 heading: "How long do you have most days?" (D-12)
//   Body: "Pick a session length. Fletcher will size lessons to fit."
//   Section 2 heading: "How should Fletcher mark the wins?" (D-13)
//   CTA: "Complete" — moment of commitment; no exclamation point.
//   Loader rotation (during isPending):
//     0-3s:  "Fletcher is listening..."
//     3-8s:  "Working on your first lesson plan..."
//     8+s:   "Almost there..."
//   Fail-open card (mode='bootstrap', D-07):
//     "Got what you said. I'll fill in the details as we go."
//     Auto-navigates to /(tabs) after 2 seconds.
//   Error: "Fletcher lost the thread. Try that again."
//   No emojis.
//
// Re-run branch (Option A MMKV flag):
//   On mount, reads getReRunPending() into a ref (stable across renders).
//   If true → uses useUserReonboard (POST /users/{id}/re-run, wipes first).
//   If false → uses useUserBootstrap (POST /users, upsert).
//   On success, setReRunPending(false) clears the flag before navigating.
//
// D-12: Complete disabled until session length is selected.
// D-13: retention format defaults to 'streak' if user picks nothing.
// D-03: preferences persisted to MMKV via setWizardPreferences on every change.
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { FletcherIntroCard } from '../../components/FletcherIntroCard';
import { SessionLengthChips } from '../../components/SessionLengthChips';
import { RetentionFormatRadio } from '../../components/RetentionFormatRadio';
import { useUserBootstrap, useUserReonboard, type UserBootstrapRequest } from '../../api/users';
import {
  clearWizardState,
  getOrCreateUserId,
  getReRunPending,
  getWizardPreferences,
  getWizardSection,
  setLastSection,
  setReRunPending,
  setWizardPreferences,
} from '../../api/mmkv';
import { useUIStore } from '../../store/uiStore';

type LengthValue = 15 | 30 | 45 | 60;
type RetentionFormat = 'streak' | 'weekly_digest' | 'monthly_milestone';

// Fletcher loader rotation copy — index matches loaderMessageIndex in uiStore.
// Exact strings per 02-04 <voice_contract>.
const LOADER_MESSAGES = [
  'Fletcher is listening...',
  'Working on your first lesson plan...',
  'Almost there...',
] as const;

export default function OnboardingPreferences() {
  // D-03: prefill from MMKV on mount.
  const initial = getWizardPreferences();
  const [sessionLength, setSessionLength] = useState<LengthValue | null>(
    initial.session_length_min,
  );
  const [retention, setRetention] = useState<RetentionFormat | null>(initial.retention_format);

  // Fail-open card state: shown when active.data?.mode === 'bootstrap'.
  const [showFailOpenCard, setShowFailOpenCard] = useState(false);

  // Re-run flag: read once on mount (stable ref — does not change during this render cycle).
  // getReRunPending() reads MMKV synchronously.
  const isReRun = useRef<boolean>(getReRunPending()).current;

  // Mutation hooks: both initialized, but only the active one is called.
  const bootstrap = useUserBootstrap();
  const reonboard = useUserReonboard();
  // active points to whichever mutation handles this wizard flow.
  const active = isReRun ? reonboard : bootstrap;

  // Loader rotation state from Zustand.
  const loaderMessageIndex = useUIStore((s) => s.loaderMessageIndex);
  const setLoaderMessageIndex = useUIStore((s) => s.setLoaderMessageIndex);
  const resetLoader = useUIStore((s) => s.resetLoader);

  // D-03: persist preference selections to MMKV on every change.
  useEffect(() => {
    setWizardPreferences({ session_length_min: sessionLength, retention_format: retention });
  }, [sessionLength, retention]);

  // Fletcher loader rotation: setTimeout at 3s and 8s while active.isPending.
  // Cleanup clears both timeouts on unmount or when isPending flips to false.
  useEffect(() => {
    if (!active.isPending) {
      resetLoader();
      return;
    }
    const t1 = setTimeout(() => setLoaderMessageIndex(1), 3000);
    const t2 = setTimeout(() => setLoaderMessageIndex(2), 8000);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [active.isPending, setLoaderMessageIndex, resetLoader]);

  // Fail-open transition + navigation on mutation success.
  // mode='bootstrap': show fail-open card for 2s, then navigate.
  // mode='full' or mode='existing': navigate immediately.
  // T-02-04-03 mitigation: useEffect cleanup clears the 2s timer if component unmounts.
  useEffect(() => {
    if (!active.data) return;

    if (active.data.mode === 'bootstrap' && !showFailOpenCard) {
      // Show the D-07 fail-open card.
      setShowFailOpenCard(true);
      const t = setTimeout(() => {
        if (isReRun) setReRunPending(false);
        router.replace('/(tabs)');
      }, 2000);
      return () => clearTimeout(t);
    }

    if ((active.data.mode === 'full' || active.data.mode === 'existing') && !showFailOpenCard) {
      // Full success: navigate immediately without showing the fail-open card.
      if (isReRun) setReRunPending(false);
      router.replace('/(tabs)');
    }
  }, [active.data, showFailOpenCard, isReRun]);

  const onComplete = () => {
    const parseLines = (raw: string): string[] =>
      raw
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean);

    const rawCanPlay = getWizardSection('play');
    const rawWorking = getWizardSection('working-on');
    const rawAspire = getWizardSection('aspire');

    // Soft cap per T-02-03-DOS — client-side mirror of the server's 10,000-char hard cap.
    const CAP = 10000;

    const body: UserBootstrapRequest = {
      user_id: getOrCreateUserId(),
      songs: {
        can_play: parseLines(rawCanPlay),
        working_on: parseLines(rawWorking),
        aspirational: parseLines(rawAspire),
      },
      preferences: {
        session_length_min: sessionLength!,
        retention_format: retention ?? 'streak',
      },
      raw_input: {
        can_play: rawCanPlay.slice(0, CAP),
        working_on: rawWorking.slice(0, CAP),
        aspirational: rawAspire.slice(0, CAP),
      },
    };

    active.mutate(body);
  };

  // D-12: Complete disabled until session length is selected (force intentional choice).
  const canComplete = sessionLength !== null && !active.isPending;

  // Fail-open card: D-07 copy, 2-second auto-transition (timer managed in useEffect above).
  if (showFailOpenCard) {
    return (
      <View style={styles.loader}>
        <ActivityIndicator size="large" color="#E07B39" />
        <Text style={styles.failOpenText}>
          Got what you said. I'll fill in the details as we go.
        </Text>
      </View>
    );
  }

  // Loader with Fletcher rotation during pending.
  if (active.isPending) {
    return (
      <View style={styles.loader}>
        <ActivityIndicator size="large" color="#E07B39" />
        <Text style={styles.loaderText}>{LOADER_MESSAGES[loaderMessageIndex]}</Text>
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

      <Text style={styles.subHeading}>How should Fletcher mark the wins?</Text>
      <RetentionFormatRadio value={retention} onChange={setRetention} />

      {active.isError && (
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
    padding: 24,
  },
  loaderText: {
    color: '#F5F5F5',
    marginTop: 12,
    fontSize: 14,
  },
  failOpenText: {
    color: '#F5F5F5',
    marginTop: 16,
    fontSize: 15,
    textAlign: 'center',
    maxWidth: 300,
    lineHeight: 22,
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
