// mobile/src/api/sessions.test.ts
// Unit tests for useSubmitRating mutation.
//
// Tests validate:
//   - Fires POST /api/v1/sessions with correct body
//   - onSuccess: patches today-song cache (Revision A) + invalidates skill-graph
//   - onSuccess: does NOT invalidate today-song or breakdown (RESEARCH §7)
//   - onError 409: silent state-sync (invalidates today-song; no throw)
//   - onError 500: propagates to caller
//
// Plan 04.1-04 Task 2: adds 4 useSubmitDrillRating tests below useSubmitRating tests.
import { renderHook, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useSubmitRating, useSubmitDrillRating } from './sessions';

// Mock the native modules that cannot run in Node.js test environment
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

jest.mock('./apiClient', () => ({
  apiFetch: jest.fn(),
}));

jest.mock('./todaySong', () => ({
  localCalendarDay: () => '2026-07-29',
}));

import { apiFetch } from './apiClient';

const mockApiFetch = apiFetch as jest.MockedFunction<typeof apiFetch>;

function makeWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: qc }, children);
  };
}

describe('useSubmitRating', () => {
  let qc: QueryClient;

  beforeEach(() => {
    qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    jest.clearAllMocks();
  });

  afterEach(() => {
    qc.clear();
  });

  it('fires POST /api/v1/sessions with correct body', async () => {
    const fakeResponse = {
      id: 'abc',
      user_id: 'test-user-id-1234-5678-9012-345678901234',
      song_id: 1,
      rating: 'getting_closer' as const,
      local_calendar_day: '2026-07-29',
      rated_at: '2026-07-29T10:00:00Z',
    };
    mockApiFetch.mockResolvedValueOnce(fakeResponse);

    const { result } = await renderHook(() => useSubmitRating(), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({ song_id: 1, rating: 'getting_closer' });
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/sessions', {
      method: 'POST',
      body: JSON.stringify({ song_id: 1, rating: 'getting_closer' }),
    });
  });

  it('onSuccess: patches today-song cache (Revision A)', async () => {
    const fakeResponse = {
      id: 'abc',
      user_id: 'test-user-id-1234-5678-9012-345678901234',
      song_id: 1,
      rating: 'getting_closer' as const,
      local_calendar_day: '2026-07-29',
      rated_at: '2026-07-29T10:00:00Z',
    };
    mockApiFetch.mockResolvedValueOnce(fakeResponse);

    // Seed the today-song cache with an unrated payload
    const seedData = {
      song: { id: 1, title: 'Test', artist: 'A', genre: 'Blues', difficulty: 'intermediate', bpm: 80, key: 'E', breakdown: { tab: { measures: [], tuning: [] }, chords: [], technique_notes: [] } },
      breakdown_available: true,
      from_bank: false,
      rerolled: false,
      rated: null,
    };
    qc.setQueryData(['today-song', 'test-user-id-1234-5678-9012-345678901234', '2026-07-29'], seedData);

    const { result } = await renderHook(() => useSubmitRating(), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({ song_id: 1, rating: 'getting_closer' });
      await new Promise((r) => setTimeout(r, 50));
    });

    // Revision A: today-song cache should be patched with rated field
    const cached = qc.getQueryData(['today-song', 'test-user-id-1234-5678-9012-345678901234', '2026-07-29']) as { rated?: { rating: string; rated_at: string } | null };
    expect(cached?.rated).toEqual({
      rating: 'getting_closer',
      rated_at: '2026-07-29T10:00:00Z',
    });
  });

  it('onSuccess: invalidates skill-graph', async () => {
    const fakeResponse = {
      id: 'abc',
      user_id: 'test-user-id-1234-5678-9012-345678901234',
      song_id: 1,
      rating: 'getting_closer' as const,
      local_calendar_day: '2026-07-29',
      rated_at: '2026-07-29T10:00:00Z',
    };
    mockApiFetch.mockResolvedValueOnce(fakeResponse);

    const invalidateSpy = jest.spyOn(qc, 'invalidateQueries');

    const { result } = await renderHook(() => useSubmitRating(), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({ song_id: 1, rating: 'getting_closer' });
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['skill-graph', 'test-user-id-1234-5678-9012-345678901234'] }),
    );
  });

  it('onSuccess: does NOT invalidate today-song or breakdown', async () => {
    const fakeResponse = {
      id: 'abc',
      user_id: 'test-user-id-1234-5678-9012-345678901234',
      song_id: 1,
      rating: 'getting_closer' as const,
      local_calendar_day: '2026-07-29',
      rated_at: '2026-07-29T10:00:00Z',
    };
    mockApiFetch.mockResolvedValueOnce(fakeResponse);

    const invalidateSpy = jest.spyOn(qc, 'invalidateQueries');

    const { result } = await renderHook(() => useSubmitRating(), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({ song_id: 1, rating: 'getting_closer' });
      await new Promise((r) => setTimeout(r, 50));
    });

    // Should NOT invalidate breakdown (D-11 cache-forever)
    const breakdownInvalidated = invalidateSpy.mock.calls.some(
      (call: Parameters<typeof qc.invalidateQueries>) => JSON.stringify(call[0]).includes('breakdown'),
    );
    expect(breakdownInvalidated).toBe(false);
    // today-song is only invalidated in the 409 error path (not success)
    const todaySongInvalidated = invalidateSpy.mock.calls.some(
      (call: Parameters<typeof qc.invalidateQueries>) => JSON.stringify(call[0]).includes('today-song'),
    );
    expect(todaySongInvalidated).toBe(false);
  });

  it('onError with 409: silently invalidates today-song without surface error', async () => {
    mockApiFetch.mockRejectedValueOnce(new Error('HTTP 409 POST /api/v1/sessions'));

    const invalidateSpy = jest.spyOn(qc, 'invalidateQueries');

    const { result } = await renderHook(() => useSubmitRating(), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({ song_id: 1, rating: 'getting_closer' });
      await new Promise((r) => setTimeout(r, 50));
    });

    // 409 triggers today-song invalidation for state sync
    const todaySongInvalidated = invalidateSpy.mock.calls.some(
      (call: Parameters<typeof qc.invalidateQueries>) => JSON.stringify(call[0]).includes('today-song'),
    );
    expect(todaySongInvalidated).toBe(true);
  });

  it('onError with 500: mutation enters error state (not swallowed)', async () => {
    mockApiFetch.mockRejectedValueOnce(new Error('HTTP 500 POST /api/v1/sessions'));

    const { result } = await renderHook(() => useSubmitRating(), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({ song_id: 1, rating: 'getting_closer' });
      await new Promise((r) => setTimeout(r, 50));
    });

    // For 500 errors, the mutation enters isError state (not swallowed like 409)
    expect(result.current.isError).toBe(true);
    expect(result.current.error?.message).toContain('HTTP 500');
  });
});

// ---------------------------------------------------------------------------
// useSubmitDrillRating — Task 2 (Plan 04.1-04)
// ---------------------------------------------------------------------------
describe('useSubmitDrillRating', () => {
  let qc: QueryClient;

  beforeEach(() => {
    qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    jest.clearAllMocks();
  });

  afterEach(() => {
    qc.clear();
  });

  it('posts correct body including drill_index and target_skill_node_id', async () => {
    const fakeResponse = {
      id: 'abc',
      user_id: 'test-user-id-1234-5678-9012-345678901234',
      song_id: 1,
      rating: 'getting_closer' as const,
      local_calendar_day: '2026-07-29',
      rated_at: '2026-07-29T10:00:00Z',
      drill_index: 2,
      target_skill_node_id: 'uuid-A',
    };
    mockApiFetch.mockResolvedValueOnce(fakeResponse);

    const { result } = await renderHook(() => useSubmitDrillRating(1), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({
        song_id: 1,
        rating: 'getting_closer',
        drill_index: 2,
        target_skill_node_id: 'uuid-A',
      });
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/sessions', {
      method: 'POST',
      body: JSON.stringify({
        song_id: 1,
        rating: 'getting_closer',
        drill_index: 2,
        target_skill_node_id: 'uuid-A',
      }),
    });
  });

  it('onSuccess invalidates today-song, breakdown, and skill-graph query keys', async () => {
    const fakeResponse = {
      id: 'abc',
      user_id: 'test-user-id-1234-5678-9012-345678901234',
      song_id: 1,
      rating: 'getting_closer' as const,
      local_calendar_day: '2026-07-29',
      rated_at: '2026-07-29T10:00:00Z',
      drill_index: 0,
      target_skill_node_id: 'uuid-B',
    };
    mockApiFetch.mockResolvedValueOnce(fakeResponse);

    const invalidateSpy = jest.spyOn(qc, 'invalidateQueries');

    const { result } = await renderHook(() => useSubmitDrillRating(1), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({
        song_id: 1,
        rating: 'getting_closer',
        drill_index: 0,
        target_skill_node_id: 'uuid-B',
      });
      await new Promise((r) => setTimeout(r, 50));
    });

    // Must invalidate all three query key families
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['today-song', 'test-user-id-1234-5678-9012-345678901234', '2026-07-29'],
      }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['breakdown', 1] }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['skill-graph', 'test-user-id-1234-5678-9012-345678901234'],
      }),
    );
  });

  it('409 drill-already-rated is swallowed silently (state sync — still invalidates)', async () => {
    mockApiFetch.mockRejectedValueOnce(
      new Error('HTTP 409 POST /api/v1/sessions'),
    );

    const invalidateSpy = jest.spyOn(qc, 'invalidateQueries');

    const { result } = await renderHook(() => useSubmitDrillRating(1), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({
        song_id: 1,
        rating: 'getting_closer',
        drill_index: 0,
        target_skill_node_id: 'uuid-C',
      });
      await new Promise((r) => setTimeout(r, 50));
    });

    // 409 is swallowed — enters error state but still invalidates for state sync
    const todaySongInvalidated = invalidateSpy.mock.calls.some(
      (call: Parameters<typeof qc.invalidateQueries>) =>
        JSON.stringify(call[0]).includes('today-song'),
    );
    expect(todaySongInvalidated).toBe(true);
  });

  it('500 error propagates to mutation error state', async () => {
    mockApiFetch.mockRejectedValueOnce(new Error('HTTP 500 POST /api/v1/sessions'));

    const { result } = await renderHook(() => useSubmitDrillRating(1), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({
        song_id: 1,
        rating: 'getting_closer',
        drill_index: 0,
        target_skill_node_id: 'uuid-D',
      });
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(result.current.isError).toBe(true);
    expect(result.current.error?.message).toContain('HTTP 500');
  });
});
