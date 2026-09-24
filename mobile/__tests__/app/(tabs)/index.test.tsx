// mobile/__tests__/app/(tabs)/index.test.tsx
// Today tab — Phase 3 helpers and the bank chip.
//
// Kept out of mobile/src/app/ so Expo Router doesn't auto-discover it as a route
// (which would leak as a 5th bottom tab and crash on tap).
//
// FLE-40/FLE-47 rewrite. This file used to be a set of module-load-time
// assertions written when there was no test runner, under a header instructing
// the reader to "run npx tsc --noEmit to validate these compile". That has not
// been true since tsconfig.json started excluding **/*.test.tsx — which is how
// the file kept importing `FletcherLineVariant` from SongOfDayCard long after
// that type, and the FLETCHER_LINE map it guarded, were deleted from the app.
// Nothing ran it and nothing typechecked it, so it could not fail.
//
// Under Jest it then failed a second way: no describe/it blocks meant "Your test
// suite must contain at least one test".
//
// What changed, and what was deliberately dropped:
//   - localCalendarDay: kept. It imports the real function, so it is a real test.
//   - FLETCHER_LINE copy contract: DELETED. It compared a locally declared map
//     against itself, and the constant it was pinning no longer exists in src/.
//     Re-add it against the real export if the Fletcher line copy comes back.
//   - rerollsLeft / bank-chip gating: these re-implemented the component's
//     one-line conditionals locally and asserted on the copy, so they could not
//     fail if TodayScreen changed. The chip half is now a real render of the real
//     FromTheBankTag. The rerollsLeft half lives inline in TodayScreen and is
//     covered honestly only by rendering that screen — see the note at the bottom.
//   - 409 handling: DELETED. It asserted that a string literal it had just
//     written contained 'HTTP 409'. The real contract is apiFetch's error
//     message shape, which src/api/apiClient.test.ts already pins.

import { render } from '@testing-library/react-native';
import { localCalendarDay } from '@/api/todaySong';
import { FromTheBankTag } from '@/components/FromTheBankTag';

// ---------------------------------------------------------------------------
// localCalendarDay — the queryKey rotation signal
// ---------------------------------------------------------------------------

describe('localCalendarDay', () => {
  it('returns a 10-char YYYY-MM-DD string', () => {
    const day = localCalendarDay();

    // Wrong format breaks daily rotation: the queryKey stops changing at
    // midnight and the Today tab serves yesterday's song.
    expect(day).toHaveLength(10);
    expect(day).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it('returns a plausible current date, not an epoch fallback', () => {
    const day = localCalendarDay();
    const year = Number(day.slice(0, 4));

    expect(year).toBeGreaterThanOrEqual(2025);
  });

  it('reports the local calendar day rather than the UTC one', () => {
    // The distinction matters for anyone west of UTC late at night: a UTC-based
    // key rolls the song over before their midnight. Compare against a locally
    // derived date rather than toISOString().
    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const expected = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;

    expect(localCalendarDay()).toBe(expected);
  });
});

// ---------------------------------------------------------------------------
// FromTheBankTag — the chip's copy is a voice contract (UI-SPEC §2, §4)
// ---------------------------------------------------------------------------

describe('FromTheBankTag', () => {
  it('renders "From your bench" for a user_bench pick', async () => {
    const view = await render(<FromTheBankTag variant="user_bench" />);

    expect(view.getByText('From your bench')).toBeTruthy();
  });

  it('renders "From the bank" for a seed_catalog pick', async () => {
    const view = await render(<FromTheBankTag variant="seed_catalog" />);

    expect(view.getByText('From the bank')).toBeTruthy();
  });

  it('exposes the label to assistive tech', async () => {
    const view = await render(<FromTheBankTag variant="user_bench" />);

    expect(view.getByLabelText('From your bench')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Not covered here
//
// `rerollsLeft` (`today.rerolled ? 0 : 1`) and the chip gate
// (`today.from_bank && today.bank_source`) are inline in TodayScreen and are not
// exported. Testing them for real means rendering TodayScreen against a mocked
// useTodaySong + QueryClientProvider + router. The infrastructure for that now
// works — FLE-47 fixed the ESM and native-module config that made any screen
// render impossible — but the harness itself is follow-up work, not part of
// turning this suite green. Asserting on a local copy of those conditionals, as
// this file used to, is worse than leaving them uncovered: it reads as coverage
// and provides none.
// ---------------------------------------------------------------------------
