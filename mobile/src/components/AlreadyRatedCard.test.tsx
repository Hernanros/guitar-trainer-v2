// mobile/src/components/AlreadyRatedCard.test.tsx
// Tests for AlreadyRatedCard component (Slice C — UI-SPEC §6).
import React from 'react';
import { render, fireEvent, act, cleanup } from '@testing-library/react-native';
import { AlreadyRatedCard } from './AlreadyRatedCard';

// Clean up after each test to prevent state leaks between timer tests
afterEach(cleanup);

describe('AlreadyRatedCard copy (UI-SPEC §6)', () => {
  it('renders verbatim not_my_tempo copy', async () => {
    const onDismiss = jest.fn();
    const { getByText } = await render(
      <AlreadyRatedCard rating="not_my_tempo" onDismiss={onDismiss} autoDismissMs={30000} />,
    );

    expect(
      getByText("Slow the whole thing 10 bpm and rebuild tomorrow. You'll be back."),
    ).toBeTruthy();
    expect(getByText('See you tomorrow.')).toBeTruthy();
  });

  it('tap-anywhere calls onDismiss immediately (via Pressable)', async () => {
    const onDismiss = jest.fn();
    const { getByText } = await render(
      <AlreadyRatedCard rating="not_my_tempo" onDismiss={onDismiss} autoDismissMs={30000} />,
    );

    // Press any text element inside the Pressable card — tap-anywhere semantics
    fireEvent.press(getByText('See you tomorrow.'));

    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('renders verbatim getting_closer copy', async () => {
    const onDismiss = jest.fn();
    const { getByText } = await render(
      <AlreadyRatedCard rating="getting_closer" onDismiss={onDismiss} autoDismissMs={30000} />,
    );

    expect(
      getByText('Getting closer. Same routine tomorrow — the next pass gets cleaner.'),
    ).toBeTruthy();
    expect(getByText('See you tomorrow.')).toBeTruthy();
  });

  it('renders verbatim thats_what_im_looking_for copy', async () => {
    const onDismiss = jest.fn();
    const { getByText } = await render(
      <AlreadyRatedCard rating="thats_what_im_looking_for" onDismiss={onDismiss} autoDismissMs={30000} />,
    );

    expect(
      getByText("That's the tempo. Push it 5 bpm next session."),
    ).toBeTruthy();
    expect(getByText('See you tomorrow.')).toBeTruthy();
  });
});

describe('AlreadyRatedCard timer behavior', () => {
  it('auto-invokes onDismiss after 2000ms', async () => {
    jest.useFakeTimers();
    const onDismiss = jest.fn();
    await render(<AlreadyRatedCard rating="getting_closer" onDismiss={onDismiss} />);

    expect(onDismiss).not.toHaveBeenCalled();

    act(() => {
      jest.advanceTimersByTime(2000);
    });

    expect(onDismiss).toHaveBeenCalledTimes(1);
    jest.useRealTimers();
  });

  it('respects custom autoDismissMs — large value does not fire early', async () => {
    jest.useFakeTimers();
    const onDismiss = jest.fn();
    await render(
      <AlreadyRatedCard rating="getting_closer" onDismiss={onDismiss} autoDismissMs={10000} />,
    );

    // With autoDismissMs=10000, advancing only 5000ms should NOT call onDismiss
    act(() => { jest.advanceTimersByTime(5000); });
    expect(onDismiss).not.toHaveBeenCalled();
    jest.useRealTimers();
  });

  it('clearTimeout prevents onDismiss after unmount (T-02-04-03)', async () => {
    jest.useFakeTimers();
    const onDismiss = jest.fn();
    const { unmount } = await render(
      <AlreadyRatedCard rating="getting_closer" onDismiss={onDismiss} autoDismissMs={2000} />,
    );

    // Unmount before the timer fires
    act(() => { unmount(); });

    // Advance past the timer — if clearTimeout was NOT called, onDismiss would fire
    act(() => { jest.advanceTimersByTime(3000); });

    // clearTimeout prevented onDismiss from firing after unmount
    expect(onDismiss).not.toHaveBeenCalled();
    jest.useRealTimers();
  });

  it('does not call onDismiss before 2000ms when not tapped', async () => {
    jest.useFakeTimers();
    const onDismiss = jest.fn();
    await render(<AlreadyRatedCard rating="not_my_tempo" onDismiss={onDismiss} />);

    act(() => { jest.advanceTimersByTime(1999); });

    expect(onDismiss).not.toHaveBeenCalled();
    jest.useRealTimers();
  });
});

