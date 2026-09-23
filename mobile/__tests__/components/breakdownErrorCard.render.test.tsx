/**
 * BreakdownErrorCard variants — device-walk item 6 (Phase 4).
 *
 * The existing copy test in __tests__/app/breakdown/[songId].test.tsx asserts
 * string literals against themselves and only covers the generic-error variant,
 * so the two variants item 6 is actually about — BREAKDOWN_CAPPED and
 * FLETCHER_OUT — were unproven on the client. The server halves are covered
 * (test_governor.py: Anthropic 429 → 503 FLETCHER_OUT;
 * test_breakdowns_mocked.py: 4th call → 429 BREAKDOWN_CAPPED), and FLETCHER_OUT
 * cannot be forced on hardware without a genuine org-level Anthropic 429, so
 * this file renders the real component and asserts what the phone was supposed
 * to show: locked copy, the retry affordance present on exactly one variant,
 * and the day count coming from the shared daysUntilReset() helper.
 */

import { render } from '@testing-library/react-native';
import { BreakdownErrorCard } from '../../src/components/BreakdownErrorCard';
import { daysUntilReset } from '../../src/utils/quota';

const noop = () => {};

/** An ISO resets_at just under `days` out, so daysUntilReset()'s ceil lands on `days`. */
function resetsInDays(days: number): string {
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000 - 60_000).toISOString();
}

describe('BREAKDOWN_CAPPED variant', () => {
  it('shows the cap heading and body, with the day count from daysUntilReset()', async () => {
    const resets_at = resetsInDays(4);
    const view = await render(
      <BreakdownErrorCard code="BREAKDOWN_CAPPED" resets_at={resets_at} onRetry={noop} onBack={noop} />,
    );

    expect(view.getByText('Not my tempo.')).toBeTruthy();
    expect(daysUntilReset(resets_at)).toBe(4);
    expect(
      view.getByText("You've had 3 breakdowns this week. Come back in 4 days."),
    ).toBeTruthy();
  });

  it('hides "Try again" — the cap is not retryable (D-02)', async () => {
    const view = await render(
      <BreakdownErrorCard
        code="BREAKDOWN_CAPPED"
        resets_at={resetsInDays(2)}
        onRetry={noop}
        onBack={noop}
      />,
    );

    expect(view.queryByRole('button', { name: 'Try again' })).toBeNull();
    expect(view.getByRole('link', { name: "Back to today's song" })).toBeTruthy();
  });

  it('renders "0 days" rather than NaN when resets_at is missing', async () => {
    const view = await render(
      <BreakdownErrorCard code="BREAKDOWN_CAPPED" resets_at={null} onRetry={noop} onBack={noop} />,
    );

    expect(
      view.getByText("You've had 3 breakdowns this week. Come back in 0 days."),
    ).toBeTruthy();
  });
});

describe('FLETCHER_OUT variant', () => {
  it('shows the org-quota copy and keeps "Try again" visible', async () => {
    const view = await render(
      <BreakdownErrorCard code="FLETCHER_OUT" onRetry={noop} onBack={noop} />,
    );

    expect(view.getByText("Fletcher's on a break.")).toBeTruthy();
    expect(view.getByText('Try again in an hour.')).toBeTruthy();
    expect(view.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('does not borrow the cap copy', async () => {
    const view = await render(
      <BreakdownErrorCard code="FLETCHER_OUT" resets_at={resetsInDays(3)} onRetry={noop} onBack={noop} />,
    );

    expect(view.queryByText('Not my tempo.')).toBeNull();
    expect(view.queryByText(/breakdowns this week/)).toBeNull();
  });
});

describe('fallback variant', () => {
  it('a null code is the generic connection error, still retryable', async () => {
    const view = await render(<BreakdownErrorCard code={null} onRetry={noop} onBack={noop} />);

    expect(view.getByText('Fletcher lost the thread.')).toBeTruthy();
    expect(view.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('copy contract across all three variants', () => {
  const CODES = ['BREAKDOWN_CAPPED', 'FLETCHER_OUT', null] as const;

  it('no emoji and no exclamation points in any rendered copy', async () => {
    const emoji = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u;

    for (const code of CODES) {
      const view = await render(
        <BreakdownErrorCard code={code} resets_at={resetsInDays(3)} onRetry={noop} onBack={noop} />,
      );
      const text = JSON.stringify(view.toJSON());

      expect(emoji.test(text)).toBe(false);
      expect(text).not.toContain('!');
    }
  });
});
