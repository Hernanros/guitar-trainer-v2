/**
 * BreakdownErrorCard variants — device-walk item 6 (Phase 4), extended by FLE-92.
 *
 * The existing copy test in __tests__/app/breakdown/[songId].test.tsx asserts
 * string literals against themselves and only covers the generic-error variant,
 * so the variants item 6 is actually about were unproven on the client. The server
 * halves are covered (test_governor.py: Anthropic 429 → 503 FLETCHER_OUT;
 * test_breakdowns_mocked.py: over-cap → 429 BREAKDOWN_CAPPED; test_pilot_ceiling.py:
 * ceiling → 429 PILOT_BUDGET_SPENT), and FLETCHER_OUT cannot be forced on hardware
 * without a genuine org-level Anthropic 429, so this file renders the real component
 * and asserts what the phone is supposed to show.
 *
 * FLE-92 changed what this file is for. The old version pinned the day count from
 * daysUntilReset() — but the cap window is now one local day, so there is no day
 * count to render and that helper is gone. What matters instead, and what these
 * tests now hold, is that the two limit refusals stay DISTINGUISHABLE:
 * BREAKDOWN_CAPPED promises midnight because midnight genuinely fixes it,
 * PILOT_BUDGET_SPENT promises nothing because only a raised ceiling fixes it, and
 * neither may borrow the other's copy.
 */

import { render } from '@testing-library/react-native';
import { BreakdownErrorCard } from '../../src/components/BreakdownErrorCard';

const noop = () => {};

describe('BREAKDOWN_CAPPED variant', () => {
  it('names the daily allowance and when it comes back', async () => {
    const view = await render(
      <BreakdownErrorCard code="BREAKDOWN_CAPPED" cap={5} onRetry={noop} onBack={noop} />,
    );

    expect(view.getByText("That's your breakdowns for today.")).toBeTruthy();
    expect(view.getByText(/All 5 of them/)).toBeTruthy();
    expect(view.getByText(/resets at midnight/)).toBeTruthy();
  });

  it('never reuses "Not my tempo" — that is a rating pill, not a refusal (FLE-92)', async () => {
    // The whole reason this variant was rewritten: a player who hit the wall read
    // the app grading their playing instead of reporting a quota.
    const view = await render(
      <BreakdownErrorCard code="BREAKDOWN_CAPPED" cap={5} onRetry={noop} onBack={noop} />,
    );

    expect(view.queryByText(/Not my tempo/)).toBeNull();
  });

  it('does not promise a multi-day wait', async () => {
    const view = await render(
      <BreakdownErrorCard code="BREAKDOWN_CAPPED" cap={5} onRetry={noop} onBack={noop} />,
    );
    const text = JSON.stringify(view.toJSON());

    expect(text).not.toContain('this week');
    expect(text).not.toMatch(/Come back in \d+ days/);
  });

  it('falls back to 5 when the server did not send a cap', async () => {
    // breakdown_quota is null whenever FLETCHER_CAP_BREAKDOWN is off, and the screen
    // passes that straight through — the body must still read as a sentence.
    const view = await render(
      <BreakdownErrorCard code="BREAKDOWN_CAPPED" cap={null} onRetry={noop} onBack={noop} />,
    );

    expect(view.getByText(/All 5 of them/)).toBeTruthy();
  });

  it('renders a server-overridden cap rather than the hardcoded default', async () => {
    const view = await render(
      <BreakdownErrorCard code="BREAKDOWN_CAPPED" cap={12} onRetry={noop} onBack={noop} />,
    );

    expect(view.getByText(/All 12 of them/)).toBeTruthy();
  });

  it('hides "Try again" — the cap is not retryable until the day turns over (D-02)', async () => {
    const view = await render(
      <BreakdownErrorCard code="BREAKDOWN_CAPPED" cap={5} onRetry={noop} onBack={noop} />,
    );

    expect(view.queryByRole('button', { name: 'Try again' })).toBeNull();
    expect(view.getByRole('link', { name: "Back to today's song" })).toBeTruthy();
  });
});

describe('PILOT_BUDGET_SPENT variant (FLE-92)', () => {
  it('says the budget is spent, and says it is not the user', async () => {
    const view = await render(
      <BreakdownErrorCard code="PILOT_BUDGET_SPENT" onRetry={noop} onBack={noop} />,
    );

    expect(view.getByText("The beta's out of breakdown budget.")).toBeTruthy();
    expect(view.getByText(/Not you, and nothing's broken/)).toBeTruthy();
  });

  it('tells the user their existing breakdowns still open', async () => {
    // True, and load-bearing: the ceiling gates dispatch only, so cache hits keep
    // serving. A user who concludes the whole app is dead stops opening it.
    const view = await render(
      <BreakdownErrorCard code="PILOT_BUDGET_SPENT" onRetry={noop} onBack={noop} />,
    );

    expect(view.getByText(/already pulled apart still opens/)).toBeTruthy();
  });

  it('does NOT promise midnight — the ceiling does not lift on a clock', async () => {
    // The assertion that keeps the two 429s from collapsing back into one message.
    const view = await render(
      <BreakdownErrorCard code="PILOT_BUDGET_SPENT" onRetry={noop} onBack={noop} />,
    );
    const text = JSON.stringify(view.toJSON());

    expect(text).not.toContain('midnight');
    expect(text).not.toMatch(/tomorrow/i);
  });

  it('hides "Try again" — retrying cannot clear a spent budget', async () => {
    const view = await render(
      <BreakdownErrorCard code="PILOT_BUDGET_SPENT" onRetry={noop} onBack={noop} />,
    );

    expect(view.queryByRole('button', { name: 'Try again' })).toBeNull();
    expect(view.getByRole('link', { name: "Back to today's song" })).toBeTruthy();
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

  it("does not borrow either limit variant's copy", async () => {
    const view = await render(
      <BreakdownErrorCard code="FLETCHER_OUT" cap={5} onRetry={noop} onBack={noop} />,
    );

    expect(view.queryByText(/breakdowns for today/)).toBeNull();
    expect(view.queryByText(/out of breakdown budget/)).toBeNull();
  });
});

describe('fallback variant', () => {
  it('a null code is the generic connection error, still retryable', async () => {
    const view = await render(<BreakdownErrorCard code={null} onRetry={noop} onBack={noop} />);

    expect(view.getByText('Fletcher lost the thread.')).toBeTruthy();
    expect(view.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('copy contract across all four variants', () => {
  const CODES = ['BREAKDOWN_CAPPED', 'PILOT_BUDGET_SPENT', 'FLETCHER_OUT', null] as const;

  it('no emoji and no exclamation points in any rendered copy', async () => {
    const emoji = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u;

    for (const code of CODES) {
      const view = await render(
        <BreakdownErrorCard code={code} cap={5} onRetry={noop} onBack={noop} />,
      );
      const text = JSON.stringify(view.toJSON());

      expect(emoji.test(text)).toBe(false);
      expect(text).not.toContain('!');
    }
  });

  it('every variant keeps the way out', async () => {
    for (const code of CODES) {
      const view = await render(
        <BreakdownErrorCard code={code} cap={5} onRetry={noop} onBack={noop} />,
      );

      expect(view.getByRole('link', { name: "Back to today's song" })).toBeTruthy();
    }
  });

  it('no two variants share a heading', async () => {
    // Two refusals wearing one heading is the bug FLE-92 fixed; this keeps the
    // client from reintroducing it by copy-pasting a variant.
    const headings = new Set<string>();

    for (const code of CODES) {
      const view = await render(
        <BreakdownErrorCard code={code} cap={5} onRetry={noop} onBack={noop} />,
      );
      // The heading is the card's first Text child.
      const card = view.toJSON() as any;
      headings.add(JSON.stringify(card.children[0].children));
    }

    expect(headings.size).toBe(CODES.length);
  });
});
