// mobile/src/api/todaySong.ts
// TanStack Query hook for GET /api/v1/song-of-day (Phase 3 per-user selector).
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
import { useQuery } from '@tanstack/react-query';
import type { components } from './generated/schema';
import { apiFetch } from './apiClient';
import { getOrCreateUserId } from './mmkv';

// Phase 3 response type — includes song + rated + rerolled + from_bank + bank_source.
export type TodaySongResponse = components['schemas']['TodaySongResponse'];
export type SongResponse = components['schemas']['SongResponse'];

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
