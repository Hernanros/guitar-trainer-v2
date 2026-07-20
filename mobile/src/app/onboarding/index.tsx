// mobile/src/app/onboarding/index.tsx
// DEVELOPER PLACEHOLDER — this screen is REPLACED ENTIRELY in 02-02.
//
// Purpose: makes the end-to-end redirect + bootstrap flow verifiable on device
// BEFORE the full wizard (02-02) ships. The observable flow:
//   1. Fresh install → root layout reads no onboarded_at → redirects here.
//   2. Tap "Bootstrap user (dev)" → calls POST /api/v1/users → writes onboarded_at.
//   3. router.replace('/(tabs)') → app lands on Today tab.
//   4. Force-quit + reopen → onboarded_at is set → root layout skips /onboarding.
//
// Voice contract: "Bootstrap user (dev)" is an intentional dev-mode string.
// Fletcher voice does NOT apply here per <voice_contract> in 02-01-PLAN.md.
// All Fletcher-voiced copy lands in 02-02 (wizard) and 02-04 (settings).
import React from 'react';
import { View, Text, StyleSheet, Pressable, ActivityIndicator } from 'react-native';
import { router } from 'expo-router';
import { useUserBootstrap, type UserBootstrapRequest } from '../../api/users';
import { getOrCreateUserId } from '../../api/mmkv';

export default function OnboardingPlaceholder() {
  const bootstrap = useUserBootstrap();

  const onBootstrap = () => {
    const body: UserBootstrapRequest = {
      user_id: getOrCreateUserId(),
      songs: { can_play: [], working_on: [], aspirational: [] },
      preferences: { session_length_min: 30, retention_format: 'streak' },
      raw_input: {},
    };
    bootstrap.mutate(body, {
      onSuccess: () => router.replace('/(tabs)'),
    });
  };

  return (
    <View style={styles.container}>
      <Text style={styles.text}>Onboarding placeholder (02-02 replaces this)</Text>
      <Pressable
        style={styles.button}
        onPress={onBootstrap}
        disabled={bootstrap.isPending}
        accessibilityRole="button"
        accessibilityLabel="Bootstrap user dev"
      >
        {bootstrap.isPending ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.buttonText}>Bootstrap user (dev)</Text>
        )}
      </Pressable>
      {bootstrap.isError && (
        <Text style={styles.error}>{String(bootstrap.error)}</Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  // Dark palette matches the Today tab (#1A1A1A bg, #F5F5F5 text, #E07B39 accent)
  // Consistent visual continuity between onboarding placeholder and post-onboarding app.
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#1A1A1A',
    padding: 24,
  },
  text: {
    fontSize: 16,
    color: '#F5F5F5',
    marginBottom: 24,
    textAlign: 'center',
  },
  button: {
    backgroundColor: '#E07B39',
    paddingHorizontal: 24,
    paddingVertical: 12,
    borderRadius: 8,
    minWidth: 160,
    alignItems: 'center',
  },
  buttonText: {
    color: '#fff',
    fontWeight: '700',
    fontSize: 14,
  },
  error: {
    color: '#ff6b6b',
    marginTop: 16,
    fontSize: 12,
    textAlign: 'center',
  },
});
