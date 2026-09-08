// mobile/src/api/breakdown.ts
// TanStack Query hook for GET /api/v1/songs/{song_id}/breakdown (Phase 4 Slice B follow-up).
//
// Query key: ['breakdown', songId]
//   - songId: numeric song ID from the route param
//   - userId is NOT in the key; it is already injected via the X-User-ID header inside apiFetch
//     (D-04) and the server enforces per-user ownership on the endpoint.
//
// Caching semantics (D-11):
//   Server caches forever after first generate — songs.breakdown_generated_at NOT NULL
//   short-circuits the Sonnet call. Client mirrors with staleTime: Infinity — one entry
//   per song per client session, no background refetch.
//
// Retry semantics:
//   retry: 0 — 429 (BREAKDOWN_CAPPED) is a cap, 503 (FLETCHER_OUT) is a Fletcher voice
//   error surface. Auto-retry would either burn quota or spam a downed dependency;
//   both must be user-driven via BreakdownErrorCard's Try again button (T-quick-01).
import { useQuery } from '@tanstack/react-query';
import type { components } from './generated/schema';
import { apiFetch } from './apiClient';

// Re-exported so the breakdown screen can type `breakdown.data` without importing
// the generated schema directly (mirrors todaySong.ts::TodaySongResponse pattern).
export type Breakdown = components['schemas']['Breakdown'];

async function fetchBreakdown(songId: number): Promise<Breakdown> {
  return apiFetch<Breakdown>(`/api/v1/songs/${songId}/breakdown`);
}

/**
 * Fetches and caches the Sonnet-generated breakdown for a song.
 *
 * @param songId - Numeric song ID from the route param, or null while the param resolves.
 *                 When null, the query is disabled (no fetch fires).
 */
export function useBreakdown(songId: number | null) {
  return useQuery<Breakdown>({
    queryKey: ['breakdown', songId],
    queryFn: () => fetchBreakdown(songId as number),
    enabled: songId != null,
    staleTime: Infinity,
    retry: 0,
  });
}
