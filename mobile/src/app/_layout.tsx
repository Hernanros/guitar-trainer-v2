// mobile/src/app/_layout.tsx
// Root layout — wraps the Stack with PersistQueryClientProvider for offline MMKV caching.
// Pattern 2 from RESEARCH.md.
import { Stack } from 'expo-router';
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client';
import { queryClient, mmkvPersister } from '../api/queryClient';

export default function RootLayout() {
  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{ persister: mmkvPersister }}
    >
      <Stack>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      </Stack>
    </PersistQueryClientProvider>
  );
}
