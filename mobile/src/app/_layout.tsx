// mobile/src/app/_layout.tsx
// Root layout — wraps the Stack with PersistQueryClientProvider for offline MMKV caching.
// Pattern 2 from RESEARCH.md.
//
// Phase 2 (02-01): adds onboarded-check redirect gate per D-04.
// - First launch (onboarded_at == null): redirect to /onboarding.
// - Subsequent launches (onboarded_at set): render /(tabs) normally.
//
// Expo Router v57 Redirect API verified against https://docs.expo.dev/versions/v57.0.0/:
// - Import: `import { Redirect } from 'expo-router'` (re-exported from link/Redirect.js).
// - Props: `href: Href` (string-compatible, e.g. "/onboarding").
// - Behavior: redirects to href as soon as the component mounts.
// Rendering <Redirect> inside PersistQueryClientProvider is intentional — the provider
// must wrap all routes (including onboarding) to ensure the MMKV persister is active.
import { Stack, Redirect } from 'expo-router';
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client';
import { queryClient, mmkvPersister } from '../api/queryClient';
import { getOnboardedAt } from '../api/mmkv';

export default function RootLayout() {
  const onboardedAt = getOnboardedAt();
  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{ persister: mmkvPersister }}
    >
      {onboardedAt === null ? <Redirect href="/onboarding" /> : null}
      <Stack>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="onboarding" options={{ headerShown: false }} />
      </Stack>
    </PersistQueryClientProvider>
  );
}
