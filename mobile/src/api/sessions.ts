// mobile/src/api/sessions.ts
// useSubmitRating mutation — POST /api/v1/sessions (Slice C).
//
// Revision A (BLOCKER): onSuccess patches the today-song cache with the freshly-rated
// payload using setQueryData (NOT invalidateQueries) to avoid an extra network round-trip.
// After patching, SongOfDayCard on Today immediately renders the already-rated variant.
//
// Cache invalidation contract (RESEARCH §7):
//   - today-song: PATCHED via setQueryData on success (not invalidated — Revision A)
//   - skill-graph: INVALIDATED (mastery changed)
//   - breakdown: NOT invalidated (D-11 cache-forever)
//
// 409 handling (UI-SPEC §10):
//   - Silent state sync: invalidate today-song so the rated field repopulates from server.
//   - No error toast or UI error state.
//   - All other errors propagate to the caller's error state.
//
// Plan 04.1-04 Task 2: adds useSubmitDrillRating below useSubmitRating.
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { components } from './generated/schema';
import { apiFetch } from './apiClient';
import { getOrCreateUserId } from './mmkv';
import { localCalendarDay, type TodaySongResponse } from './todaySong';

export type RatingLiteral = 'not_my_tempo' | 'getting_closer' | 'thats_what_im_looking_for';
export type SessionResponse = components['schemas']['SessionResponse'];

export function useSubmitRating() {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();

  return useMutation({
    mutationFn: (body: { song_id: number; rating: RatingLiteral }) =>
      apiFetch<SessionResponse>('/api/v1/sessions', {
        method: 'POST',
        body: JSON.stringify(body),
      }),

    onSuccess: (response, variables) => {
      // Revision A (BLOCKER fix): patch the today-song cache with the freshly-rated payload.
      // Using setQueryData (not invalidateQueries) avoids an extra network round-trip.
      // The server's deterministic pick does not change within a day (D-01/D-09),
      // so patching is safe — the cache gains its `rated` field and Today re-renders immediately.
      const localDay = localCalendarDay();
      qc.setQueryData<TodaySongResponse>(
        ['today-song', userId, localDay],
        (old) =>
          old
            ? { ...old, rated: { rating: variables.rating, rated_at: response.rated_at } }
            : old,
      );

      // Skill graph mastery changed — invalidate so the graph reflects the new values.
      qc.invalidateQueries({ queryKey: ['skill-graph', userId] });

      // D-11: breakdown cache-forever — do NOT invalidate breakdown.
    },

    onError: async (err: Error) => {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('HTTP 409')) {
        // UI-SPEC §10: 409 is a state-sync signal, not an error.
        // Invalidate today-song so the `rated` field populates from the server
        // (covers the case where the app missed the initial success write).
        await qc.invalidateQueries({ queryKey: ['today-song', userId, localCalendarDay()] });
        // Swallow 409 — do not re-throw. The mutation enters error state but the UI
        // transitions silently to the already-rated variant (no error toast).
        return;
      }
      // Other errors (500/503/network): do not handle — TanStack Query surfaces them
      // to the mutation's error state and the caller's onError callback.
    },
  });
}

// ---------------------------------------------------------------------------
// useSubmitDrillRating — Plan 04.1-04 Task 2
// ---------------------------------------------------------------------------
// Mirrors useSubmitRating but POSTs drill-specific fields (drill_index +
// target_skill_node_id) so the server can write mastery on the targeted skill node.
//
// songId is passed to the hook (not the mutate call) because it is needed for
// the ['breakdown', songId] invalidation that triggers the BreakdownEnvelope
// refetch — which carries the fresh drill_rated_today_indices back to the
// parent breakdown screen (B1 fix: server-derived drill-primary UI state).
//
// 409 handling:
//   "Already rated this drill today." → silent state sync (invalidate + swallow).
//   "SONG_RATING_BLOCKED_BY_DRILL: …" → this code only fires on whole-song POSTs,
//   not drill POSTs. If a drill POST ever surfaces it, that is a server bug — we
//   propagate it as an error rather than silently swallowing it.

interface DrillRatingBody {
  song_id: number;
  rating: RatingLiteral;
  drill_index: number;
  target_skill_node_id: string; // UUID string matching Drill.target_skill_temp_id
}

export function useSubmitDrillRating(songId: number | null) {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();

  return useMutation({
    mutationFn: (body: DrillRatingBody) =>
      apiFetch<SessionResponse>('/api/v1/sessions', {
        method: 'POST',
        body: JSON.stringify(body),
      }),

    onSuccess: () => {
      const localDay = localCalendarDay();
      qc.invalidateQueries({ queryKey: ['today-song', userId, localDay] });
      // B1 FIX: invalidating ['breakdown', songId] forces a refetch of the
      // BreakdownEnvelope, which returns the fresh drill_rated_today_indices
      // that the parent breakdown screen reads for its drill-primary UI state.
      // This is the durable replacement for the fragile QueryClient mutation-cache
      // subscribe pattern from the pre-revision plan.
      if (songId != null) {
        qc.invalidateQueries({ queryKey: ['breakdown', songId] });
      }
      qc.invalidateQueries({ queryKey: ['skill-graph', userId] });
    },

    onError: async (err: Error) => {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('HTTP 409')) {
        // 409 = drill already rated today (idempotency). Silent state sync:
        // invalidate so the envelope refetches and drill_rated_today_indices
        // populates — the drill-detail screen can then show the rated state.
        const localDay = localCalendarDay();
        await qc.invalidateQueries({ queryKey: ['today-song', userId, localDay] });
        if (songId != null) {
          await qc.invalidateQueries({ queryKey: ['breakdown', songId] });
        }
        return; // swallow — do not re-throw
      }
      // 500 / network / other → propagate to caller's onError callback.
    },
  });
}
