// mobile/src/api/users.ts
// TanStack Query hooks for user identity + skill graph (Phase 2).
// Pattern: follows songOfDay.ts — useQuery + useMutation with generated OpenAPI types.
//
// Codegen dependency: types come from npm run codegen:local against the Phase 2 server.
// Must re-run codegen if server Pydantic models change (UserResponse, SkillGraphResponse, etc.).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from './generated/schema';
import { apiFetch } from './apiClient';
import { getOrCreateUserId, setOnboardedAt } from './mmkv';

// Re-export generated types so callers import from a single source
export type UserResponse = components['schemas']['UserResponse'];
export type SkillGraphResponse = components['schemas']['SkillGraphResponse'];
export type UserBootstrapRequest = components['schemas']['UserBootstrapRequest'];
export type UserPreferences = components['schemas']['UserPreferences'];

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
