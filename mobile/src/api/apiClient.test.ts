// mobile/src/api/apiClient.test.ts
// Unit tests for apiFetch's two request paths (FLE-29).
//
// The `timeoutMs` path exists because `fetch` cannot express a request timeout and iOS
// silently applies NSURLSession's 60s default — below the 72.1s a real breakdown takes.
// These tests pin the two things that make the fix work on device:
//   1. xhr.timeout is actually assigned (an unset/0 timeout is exactly the bug).
//   2. The thrown-Error message keeps the `HTTP <status> <METHOD> <path>` shape that
//      breakdown/[songId].tsx pattern-matches to pick its 429/503 error variants.
jest.mock('react-native-mmkv', () => ({
  createMMKV: () => ({
    getString: jest.fn().mockReturnValue('test-user-id-1234-5678-9012-345678901234'),
    set: jest.fn(),
    remove: jest.fn(),
  }),
}));

jest.mock('./mmkv', () => ({
  getOrCreateUserId: () => 'test-user-id-1234-5678-9012-345678901234',
}));

import { apiFetch } from './apiClient';

/** Minimal XMLHttpRequest stand-in — records what the request path set on it. */
class MockXHR {
  static last: MockXHR;
  method = '';
  url = '';
  timeout = 0;
  headers: Record<string, string> = {};
  sentBody: unknown = undefined;
  status = 200;
  responseText = '{}';
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  /** Set to 'timeout' | 'error' to fire that handler instead of onload. */
  static outcome: 'load' | 'timeout' | 'error' = 'load';
  static nextStatus = 200;
  static nextBody = '{}';

  constructor() {
    MockXHR.last = this;
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  send(body: unknown) {
    this.sentBody = body;
    this.status = MockXHR.nextStatus;
    this.responseText = MockXHR.nextBody;
    // Resolve asynchronously, as the real XHR does.
    setTimeout(() => {
      if (MockXHR.outcome === 'timeout') this.ontimeout?.();
      else if (MockXHR.outcome === 'error') this.onerror?.();
      else this.onload?.();
    }, 0);
  }
}

const originalXHR = global.XMLHttpRequest;
const originalFetch = global.fetch;

beforeEach(() => {
  process.env.EXPO_PUBLIC_API_URL = 'https://api.example.test';
  MockXHR.outcome = 'load';
  MockXHR.nextStatus = 200;
  MockXHR.nextBody = '{}';
  // @ts-expect-error — test double, not a full XMLHttpRequest implementation.
  global.XMLHttpRequest = MockXHR;
});

afterEach(() => {
  global.XMLHttpRequest = originalXHR;
  global.fetch = originalFetch;
  jest.restoreAllMocks();
});

describe('apiFetch with timeoutMs (FLE-29 XHR path)', () => {
  it('assigns xhr.timeout so the value reaches native — the whole point of the fix', async () => {
    MockXHR.nextBody = JSON.stringify({ breakdown: { tab: { measures: [] } } });

    const result = await apiFetch<{ breakdown: unknown }>('/api/v1/songs/7/breakdown', {
      timeoutMs: 240_000,
    });

    expect(MockXHR.last.timeout).toBe(240_000);
    expect(MockXHR.last.url).toBe('https://api.example.test/api/v1/songs/7/breakdown');
    expect(MockXHR.last.method).toBe('GET');
    expect(result).toEqual({ breakdown: { tab: { measures: [] } } });
  });

  it('injects the identity and timezone headers the fetch path injects', async () => {
    await apiFetch('/api/v1/songs/7/breakdown', { timeoutMs: 240_000 });

    expect(MockXHR.last.headers['X-User-ID']).toBe('test-user-id-1234-5678-9012-345678901234');
    expect(MockXHR.last.headers['Content-Type']).toBe('application/json');
    expect(MockXHR.last.headers['X-Timezone-Offset']).toBeDefined();
  });

  it('preserves the "HTTP <status>" message shape the breakdown screen matches on', async () => {
    MockXHR.nextStatus = 429;
    MockXHR.nextBody = JSON.stringify({ code: 'BREAKDOWN_CAPPED', resets_at: '2026-09-21' });

    await expect(
      apiFetch('/api/v1/songs/7/breakdown', { timeoutMs: 240_000 }),
    ).rejects.toThrow(/HTTP 429 GET \/api\/v1\/songs\/7\/breakdown/);
  });

  it('surfaces the server error code alongside the status', async () => {
    MockXHR.nextStatus = 503;
    MockXHR.nextBody = JSON.stringify({ code: 'FLETCHER_OUT' });

    await expect(
      apiFetch('/api/v1/songs/7/breakdown', { timeoutMs: 240_000 }),
    ).rejects.toThrow(/FLETCHER_OUT/);
  });

  it('rejects with a timeout-specific error when the request times out', async () => {
    MockXHR.outcome = 'timeout';

    await expect(
      apiFetch('/api/v1/songs/7/breakdown', { timeoutMs: 240_000 }),
    ).rejects.toThrow(/timed out after 240000ms/);
  });

  it('forwards the request body on writes', async () => {
    await apiFetch('/api/v1/sessions', {
      method: 'POST',
      body: JSON.stringify({ song_id: 1 }),
      timeoutMs: 1_000,
    });

    expect(MockXHR.last.method).toBe('POST');
    expect(MockXHR.last.sentBody).toBe(JSON.stringify({ song_id: 1 }));
  });
});

describe('apiFetch without timeoutMs', () => {
  it('still uses fetch, leaving every other call site untouched', async () => {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    const result = await apiFetch('/api/v1/song-of-day');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result).toEqual({ ok: true });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://api.example.test/api/v1/song-of-day');
    // timeoutMs must not leak into RequestInit.
    expect(init).not.toHaveProperty('timeoutMs');
    expect(init.headers['X-User-ID']).toBe('test-user-id-1234-5678-9012-345678901234');
  });
});
