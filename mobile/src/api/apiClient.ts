// mobile/src/api/apiClient.ts
// Single fetch entry point for all API calls (Phase 2+).
//
// Injects X-User-ID header on every request per D-04 device-UUID identity contract.
// Phase 3 (03-01): injects X-Timezone-Offset header for per-user local-day computation.
// All API modules route through apiFetch — no direct calls to the native fetch API outside this file.
// Rationale: Phase 4 cost governor client-side hooks will wrap apiFetch at this boundary.
//
// Verified against expo-router v57 / AGENTS.md mandate:
// - EXPO_PUBLIC_API_URL is a build-time env var (set in .env + eas.json for each profile).
// - X-User-ID header carries the stable device UUID from userMmkv (D-04).
// - X-Timezone-Offset: Date().getTimezoneOffset() returns minutes WEST of UTC (positive for UTC-5).
//   We negate it so X-Timezone-Offset uses ISO-8601 sign convention: UTC-5 → -300.
//   Server uses this to compute: DATE((now() AT TIME ZONE 'UTC') + (offset * INTERVAL '1 minute')).
import { getOrCreateUserId } from './mmkv';

/**
 * Fetch wrapper that:
 * 1. Prepends EXPO_PUBLIC_API_URL to all paths.
 * 2. Injects X-User-ID: <device-uuid> header on every request (D-04).
 * 3. Injects X-Timezone-Offset: <minutes> header on every request (D-10, Phase 3).
 * 4. Adds Content-Type: application/json as the default content type.
 * 5. Throws on non-2xx responses with the HTTP status code included.
 *
 * @param path - API path starting with `/`, e.g. `/api/v1/song-of-day`.
 * @param init - Standard RequestInit; caller-provided headers are merged after defaults.
 */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const base = process.env.EXPO_PUBLIC_API_URL;
  if (!base) {
    throw new Error('EXPO_PUBLIC_API_URL is not set — check your .env or eas.json env config.');
  }
  const url = `${base}${path}`;
  const userId = getOrCreateUserId();
  // Date().getTimezoneOffset() returns minutes WEST of UTC (opposite sign of ISO offset).
  // For UTC-5 (Eastern Time), getTimezoneOffset() returns +300.
  // Negate so X-Timezone-Offset = -300 for UTC-5 — matching ISO-8601 convention.
  const tzOffset = -new Date().getTimezoneOffset();
  const res = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'X-User-ID': userId,
      'X-Timezone-Offset': String(tzOffset),
      // Caller-provided headers override defaults (allows Content-Type override if needed)
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} ${init.method ?? 'GET'} ${path}`);
  }
  return res.json() as Promise<T>;
}
