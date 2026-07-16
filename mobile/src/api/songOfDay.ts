// mobile/src/api/songOfDay.ts
// TanStack Query hook for GET /api/v1/song-of-day.
// Pattern 3 from RESEARCH.md.
//
// Generated types: run 'npm run codegen:local' from mobile/ with FastAPI server at localhost:8000 running.
// The generated file lives at src/api/generated/schema.d.ts (gitignored per D-02).
import { useQuery } from '@tanstack/react-query';
import type { components } from './generated/schema';

// Type is generated from the FastAPI Pydantic SongResponse model via openapi-typescript.
// Run `npm run codegen:local` to regenerate after any server model changes.
export type SongResponse = components['schemas']['SongResponse'];

async function fetchSongOfDay(): Promise<SongResponse> {
  const url = `${process.env.EXPO_PUBLIC_API_URL}/api/v1/song-of-day`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} fetching song-of-day`);
  }
  return res.json() as Promise<SongResponse>;
}

export function useSongOfDay() {
  return useQuery({
    queryKey: ['song-of-day'],
    queryFn: fetchSongOfDay,
    // staleTime: Infinity — inherited from queryClient defaults
  });
}
