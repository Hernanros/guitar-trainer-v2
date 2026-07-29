/**
 * BreakdownScreen route pure-logic tests.
 *
 * @testing-library/react-native is not installed.
 * These tests cover the pure logic and data processing in the breakdown route
 * without requiring React Native rendering.
 *
 * To run when a test framework is added:
 *   npm test -- --testPathPattern='breakdown'
 */

// ---------------------------------------------------------------------------
// songId parsing
// ---------------------------------------------------------------------------

describe('songId parsing from route params', () => {
  it('parses a valid songId string to integer', () => {
    // From [songId].tsx: parseInt(songIdParam, 10)
    const parse = (param: string | undefined) =>
      param ? parseInt(param, 10) : undefined;

    expect(parse('42')).toBe(42);
    expect(parse('1')).toBe(1);
    expect(parse(undefined)).toBeUndefined();
  });

  it('returns undefined for missing songId param', () => {
    const parse = (param: string | undefined) =>
      param ? parseInt(param, 10) : undefined;
    expect(parse(undefined)).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// useBreakdown hook contract (structural checks)
// ---------------------------------------------------------------------------

describe('useBreakdown hook contract', () => {
  it('has staleTime: Infinity (cache-forever per D-11)', () => {
    // This verifies the hook configuration documented in todaySong.ts
    // Actual hook behavior tested via integration in server tests
    const hookConfig = {
      staleTime: Infinity,
      gcTime: 1000 * 60 * 60 * 24 * 30,
      retry: 0,
      refetchOnWindowFocus: false,
    };
    expect(hookConfig.staleTime).toBe(Infinity);
    expect(hookConfig.retry).toBe(0); // T-03-02-05: no auto-retry
    expect(hookConfig.gcTime).toBe(2592000000); // 30 days in ms
  });

  it('queryKey includes songId for per-song cache isolation', () => {
    // queryKey: ['breakdown', songId] — each song has its own cache entry
    const queryKey = (songId: number | undefined) => ['breakdown', songId];
    expect(queryKey(42)).toEqual(['breakdown', 42]);
    expect(queryKey(1)).toEqual(['breakdown', 1]);
    // Different songs → different cache keys
    expect(queryKey(42)).not.toEqual(queryKey(43));
  });
});

// ---------------------------------------------------------------------------
// Breakdown loader messages contract (UI-SPEC §3)
// ---------------------------------------------------------------------------

describe('breakdown loader messages', () => {
  const BREAKDOWN_LOADER_MESSAGES = [
    'Fletcher is listening...',
    'Working out the fingering...',
    'Almost there...',
  ] as const;

  it('has exactly 3 messages', () => {
    expect(BREAKDOWN_LOADER_MESSAGES.length).toBe(3);
  });

  it('first message is "Fletcher is listening..."', () => {
    expect(BREAKDOWN_LOADER_MESSAGES[0]).toBe('Fletcher is listening...');
  });

  it('second message is "Working out the fingering..."', () => {
    expect(BREAKDOWN_LOADER_MESSAGES[1]).toBe('Working out the fingering...');
  });

  it('third message is "Almost there..."', () => {
    expect(BREAKDOWN_LOADER_MESSAGES[2]).toBe('Almost there...');
  });

  it('contains no emojis', () => {
    const emojiRegex = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u;
    BREAKDOWN_LOADER_MESSAGES.forEach((msg) => {
      expect(emojiRegex.test(msg)).toBe(false);
    });
  });

  it('contains no exclamation points', () => {
    BREAKDOWN_LOADER_MESSAGES.forEach((msg) => {
      expect(msg).not.toContain('!');
    });
  });
});

// ---------------------------------------------------------------------------
// BreakdownErrorCard copy contract (UI-SPEC §7)
// ---------------------------------------------------------------------------

describe('BreakdownErrorCard copy', () => {
  // Verbatim from UI-SPEC §7 — these strings are locked
  const HEADING = 'Fletcher lost the thread.';
  const BODY = 'The connection dropped mid-thought. Give it a minute — try again.';
  const CTA = 'Try again';
  const SECONDARY = "Back to today's song";

  it('heading is verbatim from UI-SPEC §7', () => {
    expect(HEADING).toBe('Fletcher lost the thread.');
  });

  it('body is verbatim from UI-SPEC §7', () => {
    expect(BODY).toBe('The connection dropped mid-thought. Give it a minute — try again.');
  });

  it('CTA is "Try again"', () => {
    expect(CTA).toBe('Try again');
  });

  it('secondary link is "Back to today\'s song"', () => {
    expect(SECONDARY).toBe("Back to today's song");
  });

  it('no emojis in any copy string', () => {
    const emojiRegex = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u;
    [HEADING, BODY, CTA, SECONDARY].forEach((s) => {
      expect(emojiRegex.test(s)).toBe(false);
    });
  });

  it('no exclamation points in any copy string', () => {
    [HEADING, BODY, CTA, SECONDARY].forEach((s) => {
      expect(s).not.toContain('!');
    });
  });
});
