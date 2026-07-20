// mobile/src/api/songOfDay.ts
// TanStack Query hook for GET /api/v1/song-of-day.
// Pattern 3 from RESEARCH.md.
//
// Phase 2 (02-01): refactored to route through apiFetch wrapper.
// Rationale: apiFetch injects X-User-ID header on every request (D-04 contract).
// Single fetch entry point also enables Phase 4 cost governor client-side hooks.
//
// Generated types: run 'npm run codegen:local' from mobile/ with FastAPI server at localhost:8000 running.
// The generated file lives at src/api/generated/schema.d.ts (gitignored per D-02).
import { useQuery } from '@tanstack/react-query';
import type { components } from './generated/schema';
import { apiFetch } from './apiClient';

// Type is generated from the FastAPI Pydantic SongResponse model via openapi-typescript.
// Run `npm run codegen:local` to regenerate after any server model changes.
export type SongResponse = components['schemas']['SongResponse'];

async function fetchSongOfDay(): Promise<SongResponse> {
  return apiFetch<SongResponse>('/api/v1/song-of-day');
}

export function useSongOfDay() {
  return useQuery({
    queryKey: ['song-of-day'],
    queryFn: fetchSongOfDay,
    // staleTime: Infinity — inherited from queryClient defaults
  });
}
