// mobile/src/api/todaySong.ts
// TanStack Query hook for GET /api/v1/song-of-day (Phase 3 per-user selector).
// useReroll mutation for POST /api/v1/today-song/reroll (Phase 3 gap-closure 03-04).
//
// Query key: ['today-song', userId, localCalendarDay()]
//   - userId: stable device UUID (D-04 contract)
//   - localCalendarDay: YYYY-MM-DD in device's local timezone
//
// The per-day cache key ensures fresh fetch on calendar-day boundary:
// staleTime=Infinity holds the response for the day; on next day open,
// the new date in the key causes a cache miss and a fresh fetch.
//
// localCalendarDay() is exported so useSubmitRating can patch the same key.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from './generated/schema';
import { apiFetch } from './apiClient';
import { getOrCreateUserId } from './mmkv';

// Phase 3 response type — includes song + rated + rerolled + from_bank + bank_source.
// Phase 4 addition: TodaySongResponse.breakdown_quota + BreakdownQuota convenience re-export (D-06).
export type TodaySongResponse = components['schemas']['TodaySongResponse'];
export type SongResponse = components['schemas']['SongResponse'];
export type BreakdownQuota = components['schemas']['BreakdownQuota'];

/**
 * Returns the current date as YYYY-MM-DD in the device's local timezone.
 * Used as the third segment of the ['today-song', userId, localCalendarDay()] cache key.
 * Exported so useSubmitRating.onSuccess can patch the exact same cache key.
 */
export function localCalendarDay(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

async function fetchTodaySong(userId: string): Promise<TodaySongResponse> {
  return apiFetch<TodaySongResponse>('/api/v1/song-of-day');
}

/**
 * Fetches and caches today's Song of the Day per the per-user, per-day cache key.
 * Returns TodaySongResponse which includes rated, rerolled, from_bank, bank_source.
 */
export function useTodaySong() {
  const userId = getOrCreateUserId();
  const day = localCalendarDay();
  return useQuery({
    queryKey: ['today-song', userId, day],
    queryFn: () => fetchTodaySong(userId),
    // staleTime: Infinity — inherited from queryClient defaults; per-day key causes natural refresh
  });
}

/**
 * Mutation for POST /api/v1/today-song/reroll — swaps today's song once per day.
 *
 * Mirrors useSubmitRating pattern from sessions.ts — setQueryData replace on success,
 * 409-silent-invalidate on error.
 *
 * onSuccess: replaces the cached today-song entry with the reroll response so Today
 *   re-renders immediately without an extra network round-trip (rerolls_left becomes 0).
 *
 * onError (409): UI-SPEC §10 — a 409 means the user already rerolled today. Invalidate
 *   the today-song cache so it resyncs from the server and the reroll button disappears.
 *   Do NOT re-throw; no error card surfaced. Any other error propagates to mutation.error.
 *
 * The server enforces one-per-day via the DB partial-unique index (D-05). The client
 * mirrors this by passing onReroll=undefined when rerolled=true (index.tsx contract).
 */
export function useReroll() {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();

  return useMutation<TodaySongResponse, Error, void>({
    mutationFn: () =>
      apiFetch<TodaySongResponse>('/api/v1/today-song/reroll', { method: 'POST' }),

    onSuccess: (response) => {
      // Replace the cached today-song with the reroll response — immediate re-render,
      // no extra network round-trip (Revision A pattern from sessions.ts::useSubmitRating).
      qc.setQueryData<TodaySongResponse>(
        ['today-song', userId, localCalendarDay()],
        (old) => (old ? response : response),
      );
    },

    onError: async (err: Error) => {
      // UI-SPEC §10: HTTP 409 means "already rerolled today" — treat as state-sync signal.
      // Invalidate today-song so the cached rerolled=true + rerolls_left=0 repopulates
      // from the server and the ghost reroll button disappears. Swallow — no error card.
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('HTTP 409')) {
        await qc.invalidateQueries({ queryKey: ['today-song', userId, localCalendarDay()] });
        return;
      }
      // Other errors (500/network): propagate to mutation.error state naturally.
    },
  });
}
