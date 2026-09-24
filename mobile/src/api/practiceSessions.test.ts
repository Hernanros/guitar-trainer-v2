// mobile/src/api/practiceSessions.test.ts
// Unit tests for the practice-sessions hooks (FLE-10 Task 6).
//
// Covers the two things most likely to be gotten wrong per the design doc:
//   - useTodaySession must expose 201-vs-200 (generated vs resolved), not flatten it.
//   - useCompleteItem must treat a 409 as a committed write + state resync, not an error
//     the caller has to special-case (mirrors sessions.ts::useSubmitRating's 409 pattern).
//   - Every mutation that returns a session caches it under BOTH query keys so the
//     walker and the Today "resume?" check never disagree.
import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import {
  useTodaySession,
  useCurrentPracticeSession,
  useCompleteItem,
  useSkipItem,
  practiceSessionQueryKey,
  currentPracticeSessionQueryKey,
} from './practiceSessions';

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

jest.mock('./todaySong', () => ({
  localCalendarDay: () => '2026-09-24',
}));

jest.mock('./apiClient', () => ({
  apiFetch: jest.fn(),
  apiFetchWithStatus: jest.fn(),
}));

import { apiFetch, apiFetchWithStatus } from './apiClient';

const mockApiFetch = apiFetch as jest.MockedFunction<typeof apiFetch>;
const mockApiFetchWithStatus = apiFetchWithStatus as jest.MockedFunction<typeof apiFetchWithStatus>;

const USER_ID = 'test-user-id-1234-5678-9012-345678901234';

function makeWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: qc }, children);
  };
}

const baseSession = {
  id: 'session-1',
  user_id: USER_ID,
  local_calendar_day: '2026-09-24',
  tz_offset_minutes: -300,
  target_minutes: 20,
  mode: 'BALANCED' as const,
  state: 'planned' as const,
  generated_at: '2026-09-24T12:00:00Z',
  last_activity_at: '2026-09-24T12:00:00Z',
  elapsed_active_seconds: 0,
  item_count: 3,
  items: [],
};

describe('useTodaySession', () => {
  let qc: QueryClient;

  beforeEach(() => {
    qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    jest.clearAllMocks();
  });

  it('does not flatten 201 (generated) vs 200 (resolved)', async () => {
    mockApiFetchWithStatus.mockResolvedValueOnce({ data: baseSession, status: 201 });

    const { result } = await renderHook(() => useTodaySession(), { wrapper: makeWrapper(qc) });
    result.current.mutate(undefined);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.status).toBe(201);
    expect(mockApiFetchWithStatus).toHaveBeenCalledWith(
      '/api/v1/practice-sessions/today',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('caches the resolved session under both the by-id and current-day keys', async () => {
    mockApiFetchWithStatus.mockResolvedValueOnce({ data: baseSession, status: 200 });

    const { result } = await renderHook(() => useTodaySession(), { wrapper: makeWrapper(qc) });
    result.current.mutate(undefined);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(qc.getQueryData(practiceSessionQueryKey('session-1'))).toEqual(baseSession);
    expect(qc.getQueryData(currentPracticeSessionQueryKey(USER_ID, '2026-09-24'))).toEqual(baseSession);
  });
});

describe('useCurrentPracticeSession', () => {
  it('fetches GET /api/v1/practice-sessions/current', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mockApiFetch.mockResolvedValueOnce(baseSession);

    const { result } = await renderHook(() => useCurrentPracticeSession(), { wrapper: makeWrapper(qc) });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/practice-sessions/current');
    expect(result.current.data).toEqual(baseSession);
  });
});

describe('useCompleteItem', () => {
  let qc: QueryClient;

  beforeEach(() => {
    qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    jest.clearAllMocks();
  });

  it('posts the rating body and caches the returned session', async () => {
    const event = {
      item_index: 1,
      state: 'completed' as const,
      applied: true,
      active_seconds: 90,
      clamped: false,
      daily_verdict_recorded: false,
      session: { ...baseSession, state: 'in_progress' as const },
    };
    mockApiFetch.mockResolvedValueOnce(event);

    const { result } = await renderHook(() => useCompleteItem('session-1'), { wrapper: makeWrapper(qc) });
    result.current.mutate({ itemIndex: 1, rating: 'getting_closer', active_seconds: 90 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mockApiFetch).toHaveBeenCalledWith(
      '/api/v1/practice-sessions/session-1/items/1/complete',
      { method: 'POST', body: JSON.stringify({ rating: 'getting_closer', active_seconds: 90 }) },
    );
    expect(qc.getQueryData(practiceSessionQueryKey('session-1'))).toEqual(event.session);
  });

  it('on 409 (daily verdict already recorded), resyncs from the session instead of throwing an unhandled state', async () => {
    mockApiFetch
      .mockRejectedValueOnce(new Error('HTTP 409 POST /api/v1/practice-sessions/session-1/items/2/complete'))
      .mockResolvedValueOnce({ ...baseSession, state: 'in_progress' as const });

    const { result } = await renderHook(() => useCompleteItem('session-1'), { wrapper: makeWrapper(qc) });
    result.current.mutate({ itemIndex: 2, rating: 'thats_what_im_looking_for', active_seconds: 60 });

    await waitFor(() => expect(result.current.isError).toBe(true));

    // The write is committed server-side on a 409 — the hook must resync the cache
    // rather than leave it stale, even though the mutation itself ends in error state.
    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/practice-sessions/session-1');
    expect(qc.getQueryData(practiceSessionQueryKey('session-1'))).toEqual({
      ...baseSession,
      state: 'in_progress',
    });
  });
});

describe('useSkipItem', () => {
  it('posts to the skip endpoint, never to complete', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const event = {
      item_index: 0,
      state: 'skipped' as const,
      applied: true,
      active_seconds: 12,
      clamped: false,
      daily_verdict_recorded: false,
      session: baseSession,
    };
    mockApiFetch.mockResolvedValueOnce(event);

    const { result } = await renderHook(() => useSkipItem('session-1'), { wrapper: makeWrapper(qc) });
    result.current.mutate({ itemIndex: 0 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mockApiFetch).toHaveBeenCalledWith(
      '/api/v1/practice-sessions/session-1/items/0/skip',
      { method: 'POST', body: JSON.stringify({ active_seconds: 0 }) },
    );
  });
});
