// mobile/src/api/todaySong.ts
// TanStack Query hooks for Song of the Day (Phase 3, 03-01 Slice A).
//
// Design decisions (03-RESEARCH.md §7, 03-CONTEXT.md D-05/D-09/D-10):
//
// useTodaySong: queryKey includes localCalendarDay() so the query automatically
//   refetches when the user crosses local midnight (new key = new query).
//   staleTime: 12h (not Infinity — day rollover forces new key naturally, but
//   12h cap is defensive against clock drift per RESEARCH anti-patterns).
//   refetchOnWindowFocus: false — Today tab is persistent; window focus events
//   on mobile are not meaningful triggers.
//
// useReroll: useMutation that calls POST /api/v1/today-song/reroll.
//   onSuccess: setQueryData replaces the today-song cache with the fresh pick.
//     Do NOT invalidateQueries — that would cause an extra network round trip
//     and clobber the rerolled=true state the server just returned (D-05).
//   onError with 409: silently refetch so the UI shows the correct "0 left today"
//     state if the client somehow got out of sync (UI-SPEC §10 idempotency signal).
//     Other errors propagate to React Query's error state normally.
//
// localCalendarDay: pure function returning "YYYY-MM-DD" from the device's local
//   clock. Used in the queryKey for automatic daily rotation.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from './apiClient';
import { getOrCreateUserId } from './mmkv';
import type { components } from './generated/schema';

export type TodaySongResponse = components['schemas']['TodaySongResponse'];

/**
 * Returns today's local calendar date as "YYYY-MM-DD".
 *
 * Accounts for device timezone offset: subtracting the UTC offset from the UTC
 * timestamp gives the local wall-clock time, from which we take the date part.
 * This matches the server's DATE((now() AT TIME ZONE 'UTC') + (tz * INTERVAL '1 minute'))
 * computation when tz = -Date().getTimezoneOffset().
 */
export function localCalendarDay(): string {
  const now = new Date();
  const offsetMs = now.getTimezoneOffset() * 60 * 1000;
  const localMidnight = new Date(now.getTime() - offsetMs);
  return localMidnight.toISOString().slice(0, 10); // "YYYY-MM-DD"
}

/**
 * Fetch and cache today's Song of the Day.
 *
 * queryKey: ['today-song', userId, localCalendarDay()] — changes at local midnight,
 * triggering a new fetch for the new day's song automatically (D-09 daily rotation).
 */
export function useTodaySong() {
  const userId = getOrCreateUserId();
  return useQuery<TodaySongResponse>({
    queryKey: ['today-song', userId, localCalendarDay()],
    queryFn: () => apiFetch<TodaySongResponse>('/api/v1/song-of-day'),
    staleTime: 1000 * 60 * 60 * 12, // 12h — day-rollover handled by key change
    refetchOnWindowFocus: false,
  });
}

/**
 * Reroll today's song (D-05: one per day).
 *
 * onSuccess: replaces today-song cache via setQueryData — NOT invalidateQueries.
 *   setQueryData is intentional: the server already computed the fresh pick and
 *   returned it in the response; we trust it directly instead of refetching.
 *   (RESEARCH §7: setQueryData avoids a second network round trip and prevents
 *   a race where invalidation might resolve to the pre-reroll pick.)
 *
 * onError with HTTP 409: the user already rerolled today but the client state
 *   got out of sync (e.g., multi-device or killed-and-relaunched). Silently
 *   refetch so the UI converges to the server's authoritative "0 left" state.
 *   This is a state-sync moment, not an error card (UI-SPEC §10).
 */
export function useReroll() {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();
  return useMutation<TodaySongResponse, Error, void>({
    mutationFn: () =>
      apiFetch<TodaySongResponse>('/api/v1/today-song/reroll', { method: 'POST' }),
    onSuccess: (fresh: TodaySongResponse) => {
      // Replace the today-song cache with the server's fresh rerolled pick.
      qc.setQueryData(['today-song', userId, localCalendarDay()], fresh);
    },
    onError: async (err: Error) => {
      // UI-SPEC §10: 409 = already rerolled — state-sync, not error card.
      const message = err instanceof Error ? err.message : String(err);
      if (message.includes('HTTP 409')) {
        await qc.invalidateQueries({
          queryKey: ['today-song', userId, localCalendarDay()],
        });
      }
      // All other errors propagate so React Query's isError state can show retry UI.
    },
  });
}
