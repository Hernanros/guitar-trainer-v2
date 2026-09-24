// mobile/src/api/practiceSessions.ts
// TanStack Query hooks over /api/v1/practice-sessions/* (FLE-10 Task 6).
//
// Contract source: FLE-63 (routes + schemas) + FLE-21 (lifecycle writes), reconciled
// against the FLE-10 design doc (§10 R1-R10). Full rulings are in the docstrings on
// schema.d.ts's `operations` entries — this file does not repeat them, it implements them.
//
// FLE-10 R5 (client derives nothing): every hook here is a thin wrapper over one HTTP
// call. No hook computes state, block order, ratios, or ladder outcomes — those are
// server-told fields on the response and the caller reads them directly.
//
// Query key shape:
//   ['practice-session', sessionId]                    — one session, by id (resume-by-id, summary re-read)
//   ['practice-session', 'current', userId, localDay]   — today's open session, if any (404 is normal)
//
// Every mutation that returns a fresh PracticeSessionResponse writes it into BOTH keys
// (when a session id is known) so the walker and the "resume?" check on Today never
// disagree with each other after a transition.
//
// What this file deliberately does NOT build yet: the offline outbox (MMKV-backed FIFO
// queue with retry) that design doc §5/§14.4 calls for. These hooks are the network
// calls the outbox will eventually wrap; the walker screens land in a follow-up slice.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from './generated/schema';
import { apiFetch, apiFetchWithStatus } from './apiClient';
import { getOrCreateUserId } from './mmkv';
import { localCalendarDay } from './todaySong';

export type PracticeSessionResponse = components['schemas']['PracticeSessionResponse'];
export type PracticeSessionItemResponse = components['schemas']['PracticeSessionItemResponse'];
export type ItemEventResponse = components['schemas']['ItemEventResponse'];
export type SessionCompleteResponse = components['schemas']['SessionCompleteResponse'];
export type EmbeddedDrill = components['schemas']['EmbeddedDrill'];
export type EmbeddedSong = components['schemas']['EmbeddedSong'];
export type PracticeRatingLiteral = 'not_my_tempo' | 'getting_closer' | 'thats_what_im_looking_for';

export function practiceSessionQueryKey(sessionId: string) {
  return ['practice-session', sessionId] as const;
}

export function currentPracticeSessionQueryKey(userId: string, day: string) {
  return ['practice-session', 'current', userId, day] as const;
}

/**
 * First not-yet-finished item, server-authoritative (design doc §5 rule 1): "the
 * server's first non-terminal item wins — always. It decides where you are." Used
 * by both the session entry route (where to land) and the walker (where to go
 * next after a complete/skip response carries a fresh item list).
 *
 * Returns null when every item is completed/skipped — the session is done.
 */
export function firstNonTerminalIndex(
  items: PracticeSessionItemResponse[] | undefined,
): number | null {
  if (!items) return null;
  const idx = items.findIndex((it) => it.state === 'not_reached' || it.state === 'in_progress');
  return idx === -1 ? null : idx;
}

/** Writes a fresh session snapshot into both the by-id and the current-day cache slots. */
function cacheSession(
  qc: ReturnType<typeof useQueryClient>,
  userId: string,
  session: PracticeSessionResponse,
) {
  qc.setQueryData(practiceSessionQueryKey(session.id), session);
  qc.setQueryData(currentPracticeSessionQueryKey(userId, session.local_calendar_day), session);
}

/**
 * Resolve-or-generate today's session (FLE-63). The one call that starts a session.
 *
 * A mutation, not a query, because it can write (a fresh plan) — but unlike a typical
 * mutation it is also safe to fire on every cold open of the session entry route: the
 * server's partial unique index makes a double call resolve to the same row (no lock,
 * no debounce needed on this side either, per the route's own doc comment).
 *
 * `status` on the resolved value distinguishes 201 (generated — a new plan, empty
 * progress) from 200 (resolved — the user may be mid-session). The FLE-10 design doc is
 * explicit that flattening this is wrong: a 200 must not re-animate a progress bar or
 * fire a "session started" signal. Callers branch on `result.status`, not on inspecting
 * item states themselves.
 */
export function useTodaySession() {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();

  return useMutation({
    mutationFn: (body?: { song_id?: number; target_minutes?: number }) =>
      apiFetchWithStatus<PracticeSessionResponse>('/api/v1/practice-sessions/today', {
        method: 'POST',
        body: JSON.stringify(body ?? {}),
      }),

    onSuccess: ({ data: session }) => {
      cacheSession(qc, userId, session);
    },
  });
}

/**
 * The caller's open session for today, read-only (FLE-21 §5). 404 is the normal answer
 * on a day with no session yet — not an error state, so callers should check
 * `query.isError` only to distinguish "no session" (404) from a real failure if they
 * need to; most callers just want `query.data` to decide whether to show a resume banner.
 *
 * Read-only by contract: this hook must never be the thing that starts a session. Use
 * useTodaySession for that.
 */
export function useCurrentPracticeSession() {
  const userId = getOrCreateUserId();
  const day = localCalendarDay();

  return useQuery({
    queryKey: currentPracticeSessionQueryKey(userId, day),
    queryFn: () => apiFetch<PracticeSessionResponse>('/api/v1/practice-sessions/current'),
    retry: false, // a 404 here is a normal "nothing to resume" answer, not a transient failure
  });
}

/**
 * Read back one session by id, terminal or not. Used for the summary screen (which
 * already holds the id from the completion response) and for resuming a session the
 * walker was mid-render for when the app restarted cold with a route param but no
 * warm cache.
 */
export function usePracticeSession(sessionId: string | null) {
  return useQuery({
    queryKey: practiceSessionQueryKey(sessionId ?? ''),
    queryFn: () => apiFetch<PracticeSessionResponse>(`/api/v1/practice-sessions/${sessionId}`),
    enabled: sessionId != null,
  });
}

/**
 * Mark the session opened. Write-once, idempotent server-side (FLE-21 R5) — safe to
 * call on every resume, not just the first open; a retry must not move `started_at`.
 */
export function useStartPracticeSession(sessionId: string) {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();

  return useMutation({
    mutationFn: () =>
      apiFetch<PracticeSessionResponse>(`/api/v1/practice-sessions/${sessionId}/start`, {
        method: 'POST',
      }),
    onSuccess: (session) => cacheSession(qc, userId, session),
  });
}

/**
 * Item -> in_progress. Fired when an item becomes the active one — not on mount of the
 * walker route, but on the transition into that item, so a late-arriving `enter` from
 * an offline replay can't resurrect an item the user already finished
 * (`applied: false` on that path — callers should treat it as a no-op, not an error).
 */
export function useEnterItem(sessionId: string) {
  return useMutation({
    mutationFn: (itemIndex: number) =>
      apiFetch<ItemEventResponse>(
        `/api/v1/practice-sessions/${sessionId}/items/${itemIndex}/enter`,
        { method: 'POST' },
      ),
  });
}

interface CompleteItemBody {
  itemIndex: number;
  rating: PracticeRatingLiteral | null;
  active_seconds: number;
  completed_reps?: number | null;
  advance_mode?: 'user_tap' | 'auto' | null;
}

/**
 * Item -> completed, with or without a rating (`rating: null` is valid and expected —
 * warm-up, song_play, consolidation, and any slot the MAX_RATING_TAPS cap dropped are
 * unrated by design). This is the player's ONLY rating write; the server fans out to
 * drill_attempts / the user_sessions daily verdict, so callers must not also call
 * useSubmitDrillRating or useSubmitRating from sessions.ts for a session item.
 *
 * A non-null rating on the rated repertoire item can 409 if the daily verdict was
 * already recorded (e.g. the user also rated the song from the Today card). Per
 * UI-SPEC §10's existing meaning that is a state-sync signal, not an error — the
 * item's rating is already committed when the 409 is thrown, so this treats 409 as a
 * normal ItemEventResponse rather than rejecting the mutation.
 */
export function useCompleteItem(sessionId: string) {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();

  return useMutation({
    mutationFn: async ({ itemIndex, ...body }: CompleteItemBody) => {
      try {
        return await apiFetch<ItemEventResponse>(
          `/api/v1/practice-sessions/${sessionId}/items/${itemIndex}/complete`,
          { method: 'POST', body: JSON.stringify(body) },
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes('HTTP 409')) {
          // The write committed; the 409 body is still an ItemEventResponse (schema.d.ts).
          // apiFetch discards non-2xx bodies, so re-fetch the session to resync state
          // rather than parse the thrown error's message for JSON.
          const session = await apiFetch<PracticeSessionResponse>(
            `/api/v1/practice-sessions/${sessionId}`,
          );
          cacheSession(qc, userId, session);
        }
        throw err;
      }
    },
    onSuccess: (event) => {
      cacheSession(qc, userId, event.session);
    },
  });
}

interface SkipItemBody {
  itemIndex: number;
  active_seconds?: number;
  completed_reps?: number | null;
}

/**
 * Item -> skipped. Its own call, fired only on a deliberate user skip — never inferred
 * from forward navigation (FLE-21 §5.4). This is the endpoint the pilot's "which block
 * do they skip" readout is built from; a walker that calls this on ordinary advance
 * would turn that number into noise.
 */
export function useSkipItem(sessionId: string) {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();

  return useMutation({
    mutationFn: ({ itemIndex, ...body }: SkipItemBody) =>
      apiFetch<ItemEventResponse>(
        `/api/v1/practice-sessions/${sessionId}/items/${itemIndex}/skip`,
        { method: 'POST', body: JSON.stringify({ active_seconds: 0, ...body }) },
      ),
    onSuccess: (event) => cacheSession(qc, userId, event.session),
  });
}

/**
 * Session -> completed, with the summary numbers (FLE-10 R8). Outranks the clock
 * (FLE-21 §3.1): even a session the day-roll sweep already abandoned underneath the
 * user can still be completed here, and that call is the one that's believed.
 *
 * `completion_ratio`, `done_items`, `skipped_items`, `planned_items` come from the
 * server — the summary screen displays them, it does not recompute a ratio from item
 * states it read itself (there must be exactly one definition of this number; it feeds
 * mode rule 3).
 */
export function useCompletePracticeSession(sessionId: string) {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();

  return useMutation({
    mutationFn: () =>
      apiFetch<SessionCompleteResponse>(`/api/v1/practice-sessions/${sessionId}/complete`, {
        method: 'POST',
      }),
    onSuccess: (result) => cacheSession(qc, userId, result.session),
  });
}
