// mobile/src/app/(tabs)/settings.tsx
// Settings tab — shows current preferences + Re-run onboarding action.
//
// Voice contract (02-04 <voice_contract>):
//   Section label: "Your setup"
//   Line 1: "Session length: {N} min"
//   Line 2: "Wins marked as: {label}"
//   Note: "Change these by re-running onboarding."
//   Button: "Re-run onboarding"
//   Dialog title: "Start over?"
//   Dialog body: "You'll keep your session preferences. Songs and skills reset."
//   Confirm CTA: "Reset"
//   Cancel CTA: "Keep going"
//
// Re-run flow (Option A with MMKV flag):
//   1. Alert.alert — native OS dialog, no custom modal chrome needed.
//   2. On confirm: setReRunPending(true) → clearOnboardedAt() → clearWizardState()
//      → router.replace('/onboarding').
//   3. preferences.tsx checks getReRunPending() on mount to branch between
//      useUserBootstrap and useUserReonboard.
//
// Build row (03-UI-SPEC §12, FLE-30): a pilot tester's running bundle/runtime, readable
// without developer help so bug reports are attributable to a version. Deliberately its
// own section, not folded into "Dev" below — a tester is meant to find and quote it.
//
// No emojis. No exclamation points. Fletcher voice: direct + warm.
import React from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text } from 'react-native';
import { router } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';
import { useUser } from '../../api/users';
import { clearOnboardedAt, clearWizardState, setReRunPending } from '../../api/mmkv';
import { applyUpdateIfAvailable, describeRunningBundle } from '../../hooks/use-ota-update';

// Map server enum values to Fletcher-voiced display labels (D-13).
const RETENTION_LABELS: Record<string, string> = {
  streak: 'daily streaks',
  weekly_digest: 'Sunday digest',
  monthly_milestone: 'monthly milestones',
};

export default function SettingsScreen() {
  const { data: user, isLoading } = useUser();
  const qc = useQueryClient();

  // POC dev affordance — invalidate all TanStack Query cache entries so the next
  // render refetches from server. Needed for testing when server-side state has
  // changed (quota reset, reroll marker inserted, song regen) but the client's
  // staleTime: Infinity + MMKV persistence keeps serving the stale response.
  const onRefreshCache = () => {
    Alert.alert(
      'Refresh cache?',
      'Discards local cache. Next screen open refetches from server. Use when server-side state has been changed manually.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Refresh',
          onPress: () => {
            qc.invalidateQueries();
          },
        },
      ],
    );
  };

  // Which JS bundle is this phone actually running? Without a visible answer, a shipped
  // fix that hasn't been applied yet looks identical to a fix that was never shipped.
  const onCheckForUpdate = () => {
    void applyUpdateIfAvailable().then((outcome) => {
      if (outcome === 'reloading') return; // the app is already restarting
      const message =
        outcome === 'up-to-date'
          ? `You have the newest version.\n\n${describeRunningBundle()}`
          : outcome === 'disabled'
            ? 'Updates are off in this build.'
            : 'Could not reach the update server. Try again on a better connection.';
      Alert.alert('Update check', message);
    });
  };

  const onReRun = () => {
    Alert.alert(
      'Start over?',
      "You'll keep your session preferences. Songs and skills reset.",
      [
        { text: 'Keep going', style: 'cancel' },
        {
          text: 'Reset',
          style: 'destructive',
          onPress: () => {
            // Flag the re-run path so preferences.tsx calls useUserReonboard.mutate.
            setReRunPending(true);
            // Clear onboarded_at: root layout redirect will route to /onboarding.
            clearOnboardedAt();
            // Clear wizard state: fresh start, no prefill from previous run.
            clearWizardState();
            router.replace('/onboarding');
          },
        },
      ],
    );
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.sectionLabel}>Your setup</Text>

      {isLoading ? (
        <Text style={styles.value}>Loading...</Text>
      ) : user ? (
        <>
          <Text style={styles.value}>Session length: {user.preferences.session_length_min} min</Text>
          <Text style={styles.value}>
            Wins marked as:{' '}
            {RETENTION_LABELS[user.preferences.retention_format] ??
              user.preferences.retention_format}
          </Text>
          <Text style={styles.note}>Change these by re-running onboarding.</Text>
        </>
      ) : (
        <Text style={styles.value}>Could not load preferences.</Text>
      )}

      <Pressable
        style={({ pressed }) => [styles.button, pressed && styles.buttonPressed]}
        onPress={onReRun}
      >
        <Text style={styles.buttonText}>Re-run onboarding</Text>
      </Pressable>

      {/* UI-SPEC §12 — quotable without developer help; not grouped under "Dev" below. */}
      <Text style={styles.devSectionLabel}>Build</Text>
      <Text testID="settings-bundle-stamp" style={styles.stamp}>
        {describeRunningBundle()}
      </Text>
      <Text style={styles.note}>Include this if you're reporting a bug.</Text>

      <Text style={styles.devSectionLabel}>Dev</Text>
      <Pressable
        style={({ pressed }) => [styles.devButton, pressed && styles.buttonPressed]}
        onPress={onCheckForUpdate}
      >
        <Text style={styles.devButtonText}>Check for update</Text>
      </Pressable>
      <Pressable
        style={({ pressed }) => [styles.devButton, styles.devButtonSpaced, pressed && styles.buttonPressed]}
        onPress={onRefreshCache}
      >
        <Text style={styles.devButtonText}>Refresh cache</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#1A1A1A',
  },
  content: {
    padding: 20,
    paddingTop: 32,
  },
  sectionLabel: {
    color: '#A0A0A0',
    fontSize: 12,
    letterSpacing: 1.2,
    textTransform: 'uppercase',
    marginBottom: 12,
  },
  value: {
    color: '#F5F5F5',
    fontSize: 16,
    marginBottom: 8,
  },
  note: {
    color: '#A0A0A0',
    fontSize: 13,
    marginTop: 4,
    marginBottom: 32,
  },
  button: {
    backgroundColor: '#E07B39',
    paddingVertical: 14,
    borderRadius: 8,
    alignItems: 'center',
    marginTop: 24,
  },
  buttonPressed: {
    opacity: 0.8,
  },
  buttonText: {
    color: '#fff',
    fontWeight: '700',
    fontSize: 16,
  },
  devSectionLabel: {
    color: '#666',
    fontSize: 11,
    letterSpacing: 1.2,
    textTransform: 'uppercase',
    marginTop: 48,
    marginBottom: 12,
  },
  stamp: {
    color: '#666',
    fontSize: 12,
    marginBottom: 12,
  },
  devButtonSpaced: {
    marginTop: 10,
  },
  devButton: {
    borderWidth: 1,
    borderColor: '#444',
    paddingVertical: 12,
    borderRadius: 8,
    alignItems: 'center',
  },
  devButtonText: {
    color: '#A0A0A0',
    fontWeight: '600',
    fontSize: 14,
  },
});
