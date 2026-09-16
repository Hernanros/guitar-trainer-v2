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
//
// FLE-29: `fetch` cannot express a request timeout, and the RN/native default bites us.
//   RN's fetch is the whatwg-fetch polyfill, which registers `xhr.ontimeout` but never
//   assigns `xhr.timeout` — so XMLHttpRequest.timeout keeps its default of 0
//   (XMLHttpRequest.js:155) and that 0 is handed to native verbatim
//   (RCTNetworking.mm:335 → `request.timeoutInterval = 0`). The iOS session is built from
//   [NSURLSessionConfiguration defaultSessionConfiguration] (RCTHTTPRequestHandler.mm:93,
//   no custom provider registered in this app), whose timeoutIntervalForRequest defaults
//   to 60s. A real breakdown averages 72.1s, so the platform default is below our own
//   latency floor. Callers that can legitimately run long pass an explicit `timeoutMs`,
//   which routes through XMLHttpRequest so the value actually reaches native.
//   (Android's OkHttp path already defaults to 0 = no timeout, so this only tightens it.)
import { getOrCreateUserId } from './mmkv';

/** RequestInit plus an opt-in, explicitly-plumbed request timeout (FLE-29). */
export interface ApiFetchInit extends RequestInit {
  /**
   * Request timeout in milliseconds. When set, the request is issued via XMLHttpRequest
   * instead of `fetch` so the value reaches the native layer (iOS
   * NSURLRequest.timeoutInterval, Android OkHttp call timeout). When omitted, the
   * platform default applies — ~60s on iOS, none on Android.
   */
  timeoutMs?: number;
}

/**
 * XHR-backed request path used when a caller supplies `timeoutMs`.
 *
 * Deliberately mirrors the `fetch` path's observable contract: same thrown-Error message
 * shape (`HTTP <status> <METHOD> <path>`), which breakdown/[songId].tsx pattern-matches
 * for its 429/503 error variants, and the same parsed-JSON resolution value.
 */
function xhrFetch<T>(
  url: string,
  path: string,
  method: string,
  headers: Record<string, string>,
  body: BodyInit | null | undefined,
  timeoutMs: number,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    // Must be set after open() — flows to native as NSURLRequest.timeoutInterval (seconds).
    xhr.timeout = timeoutMs;
    for (const [name, value] of Object.entries(headers)) {
      xhr.setRequestHeader(name, value);
    }
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        // Append the response body so server error codes (BREAKDOWN_CAPPED, FLETCHER_OUT)
        // remain visible to callers that match on them alongside the status.
        reject(new Error(`HTTP ${xhr.status} ${method} ${path} ${xhr.responseText ?? ''}`.trim()));
        return;
      }
      try {
        resolve(JSON.parse(xhr.responseText) as T);
      } catch {
        reject(new Error(`Malformed JSON response for ${method} ${path}`));
      }
    };
    xhr.onerror = () => reject(new Error(`Network request failed for ${method} ${path}`));
    xhr.ontimeout = () =>
      reject(new Error(`Request timed out after ${timeoutMs}ms for ${method} ${path}`));
    xhr.send((body as string | null | undefined) ?? null);
  });
}

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
export async function apiFetch<T>(path: string, init: ApiFetchInit = {}): Promise<T> {
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
  const { timeoutMs, ...requestInit } = init;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-User-ID': userId,
    'X-Timezone-Offset': String(tzOffset),
    // Caller-provided headers override defaults (allows Content-Type override if needed)
    ...((requestInit.headers as Record<string, string> | undefined) ?? {}),
  };
  if (timeoutMs != null) {
    return xhrFetch<T>(url, path, requestInit.method ?? 'GET', headers, requestInit.body, timeoutMs);
  }
  const res = await fetch(url, {
    ...requestInit,
    headers,
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} ${init.method ?? 'GET'} ${path}`);
  }
  return res.json() as Promise<T>;
}
