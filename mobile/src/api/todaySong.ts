// mobile/src/api/todaySong.ts
// TanStack Query hooks for Phase 3 song-of-day + breakdown flow.
//
// Slice B (this file): useBreakdown(songId) — staleTime: Infinity (cache-forever per D-11).
// Slice A adds: useTodaySong, useReroll — wired to GET /api/v1/today-song (03-01 scope).
//
// Pattern: mirrors useSkillGraph from users.ts; routes through apiFetch (D-04).
// Generated types: run 'npm run codegen:local' to regenerate schema.d.ts after server changes.
import { useQuery } from '@tanstack/react-query';
import type { components } from './generated/schema';
import { apiFetch } from './apiClient';

// Type from generated schema (Pydantic Breakdown → OpenAPI → openapi-typescript).
export type Breakdown = components['schemas']['Breakdown'];

/**
 * Fetch and cache the Sonnet technique breakdown for a given song.
 *
 * Cache semantics (D-11 cache-forever):
 *   - staleTime: Infinity — result never considered stale; no background refetch
 *   - gcTime: 30 days — retain in memory even if all subscribers unmount
 *   - retry: 0 — T-03-02-05: no auto-retry storm; user-driven retry via "Try again" button
 *   - enabled: songId !== undefined — disables query entirely if no song selected
 *
 * @param songId - The integer song ID from TodaySongResponse.song.id
 */
export function useBreakdown(songId: number | undefined) {
  return useQuery({
    queryKey: ['breakdown', songId],
    queryFn: () => apiFetch<Breakdown>(`/api/v1/songs/${songId}/breakdown`),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60 * 24 * 30, // 30 days
    enabled: songId !== undefined,
    refetchOnWindowFocus: false,
    retry: 0, // T-03-02-05: no auto-retry; Try Again button is user-driven intent
  });
}
