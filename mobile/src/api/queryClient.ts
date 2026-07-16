// mobile/src/api/queryClient.ts
// TanStack Query client with MMKV offline persistence.
// Pattern 2 from RESEARCH.md — MMKV write-through on every successful response.
//
// Offline strategy:
// MMKV write-through persister: every successful TanStack Query response is written to MMKV
// immediately (throttled to 1s). On app restart with no network, PersistQueryClientProvider
// rehydrates from MMKV before the first render. staleTime=Infinity means the app never
// silently refetches in Phase 1 — data is fresh until explicitly invalidated. Phase 3 will
// introduce shorter stale times when AI-generated content can change daily.
//
// NOTE: MMKV v4 (NitroModules) requires a native dev-client build.
// This file CANNOT run in Expo Go — always use npx expo start --dev-client.
import { QueryClient } from '@tanstack/react-query';
import { createAsyncStoragePersister } from '@tanstack/query-async-storage-persister';
import { createMMKV } from 'react-native-mmkv';

// MMKV v4 API: createMMKV() replaces `new MMKV()` from v3.
const mmkv = createMMKV({ id: 'query-cache' });

// MMKV storage adapter — async interface over MMKV's sync methods
const mmkvStorage = {
  setItem: (key: string, value: string): Promise<void> => {
    mmkv.set(key, value);
    return Promise.resolve();
  },
  getItem: (key: string): Promise<string | null> => {
    return Promise.resolve(mmkv.getString(key) ?? null);
  },
  removeItem: (key: string): Promise<void> => {
    // MMKV v4 API: remove() replaces delete() from v3
    mmkv.remove(key);
    return Promise.resolve();
  },
};

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: Infinity,              // Phase 1: hardcoded data never goes stale
      gcTime: 1000 * 60 * 60 * 24,    // 24h in memory cache
    },
  },
});

export const mmkvPersister = createAsyncStoragePersister({
  storage: mmkvStorage,
  throttleTime: 1000,
});
