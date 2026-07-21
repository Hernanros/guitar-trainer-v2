// mobile/src/api/users.ts
// TanStack Query hooks for user identity + skill graph (Phase 2).
// Pattern: follows songOfDay.ts — useQuery + useMutation with generated OpenAPI types.
//
// Codegen dependency: types come from npm run codegen:local against the Phase 2 server.
// Must re-run codegen if server Pydantic models change (UserResponse, SkillGraphResponse, etc.).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from './generated/schema';
import { apiFetch } from './apiClient';
import { getOrCreateUserId, setOnboardedAt, clearWizardState } from './mmkv';

// Re-export generated types so callers import from a single source
export type UserResponse = components['schemas']['UserResponse'];
export type SkillGraphResponse = components['schemas']['SkillGraphResponse'];
export type UserBootstrapRequest = components['schemas']['UserBootstrapRequest'];
export type UserPreferences = components['schemas']['UserPreferences'];
export type SkillNodeResponse = components['schemas']['SkillNodeResponse'];

/**
 * Fetches the current user's preferences and onboarded_at timestamp.
 * Returns 404 until the user has been bootstrapped via useUserBootstrap.
 */
export function useUser() {
  const userId = getOrCreateUserId();
  return useQuery({
    queryKey: ['user', userId],
    queryFn: () => apiFetch<UserResponse>(`/api/v1/users/${userId}`),
    // Don't refetch on window focus — mobile apps have no window focus events
    refetchOnWindowFocus: false,
  });
}

/**
 * Bootstrap mutation: POST /api/v1/users with onboarding data.
 *
 * On success:
 * 1. Writes onboarded_at to MMKV — root layout redirect will skip /onboarding.
 * 2. Seeds the TanStack Query skill-graph cache with the server response.
 *    (In 02-01 the graph is always empty; 02-03 populates it via Sonnet.)
 */
export function useUserBootstrap() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UserBootstrapRequest) =>
      apiFetch<SkillGraphResponse>('/api/v1/users', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (graph, variables) => {
      setOnboardedAt(new Date().toISOString());
      // Seed the skill-graph cache — 02-03 will use this as the initial hydration
      qc.setQueryData(['skill-graph', variables.user_id], graph);
    },
  });
}

/**
 * Fetches the current user's full skill graph (3-level DAG).
 * Used by the Today tab (Phase 3+) and for cold-start verification.
 * Long stale time — graph only changes via explicit re-run action.
 */
export function useSkillGraph() {
  const userId = getOrCreateUserId();
  return useQuery({
    queryKey: ['skill-graph', userId],
    queryFn: () => apiFetch<SkillGraphResponse>(`/api/v1/users/${userId}/skill-graph`),
    // Skill graph rarely changes without an explicit action — long stale time OK
    staleTime: 1000 * 60 * 60, // 1 hour
    refetchOnWindowFocus: false,
  });
}

/**
 * Re-run mutation: POST /api/v1/users/{user_id}/re-run.
 *
 * Used when Settings "Re-run onboarding" flows back through the wizard
 * and the user taps Complete again. Wipes songs + song_skills + skill_nodes
 * for this user (preserves user row + preferences per D-14), then re-runs
 * the Sonnet-backed bootstrap. Returns a fresh SkillGraphResponse.
 *
 * On success:
 * 1. Writes onboarded_at to MMKV — ensures redirect gate is set after re-run.
 * 2. Clears wizard MMKV state (sections + last_section + preferences).
 * 3. Seeds the TanStack Query skill-graph cache with the new graph.
 * 4. Invalidates the ['user', userId] query so Settings re-reads fresh preferences.
 */
export function useUserReonboard() {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();
  return useMutation({
    mutationFn: (body: UserBootstrapRequest) =>
      apiFetch<SkillGraphResponse>(`/api/v1/users/${userId}/re-run`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (graph) => {
      setOnboardedAt(new Date().toISOString());
      clearWizardState();
      qc.setQueryData(['skill-graph', userId], graph);
      qc.invalidateQueries({ queryKey: ['user', userId] });
    },
  });
}
