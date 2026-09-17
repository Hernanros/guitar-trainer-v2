// mobile/src/hooks/use-ota-update.test.ts
// Covers the one-launch gap this hook closes: a bundle published before launch must be
// running after launch, not merely downloaded for next time.
import * as Updates from 'expo-updates';
import { applyUpdateIfAvailable, describeRunningBundle } from './use-ota-update';

const mock = Updates as unknown as {
  __reset(): void;
  __setState(next: Record<string, unknown>): void;
  checkForUpdateAsync: jest.Mock;
  fetchUpdateAsync: jest.Mock;
  reloadAsync: jest.Mock;
};

beforeEach(() => {
  mock.__reset();
});

describe('applyUpdateIfAvailable', () => {
  it('downloads and restarts into a newer bundle', async () => {
    mock.checkForUpdateAsync.mockResolvedValue({ isAvailable: true });
    mock.fetchUpdateAsync.mockResolvedValue({ isNew: true });

    await expect(applyUpdateIfAvailable()).resolves.toBe('reloading');
    expect(mock.reloadAsync).toHaveBeenCalledTimes(1);
  });

  it('leaves the app alone when the channel has nothing newer', async () => {
    mock.checkForUpdateAsync.mockResolvedValue({ isAvailable: false });

    await expect(applyUpdateIfAvailable()).resolves.toBe('up-to-date');
    expect(mock.fetchUpdateAsync).not.toHaveBeenCalled();
    expect(mock.reloadAsync).not.toHaveBeenCalled();
  });

  it('does not restart when the fetch turns out to return the running bundle', async () => {
    mock.checkForUpdateAsync.mockResolvedValue({ isAvailable: true });
    mock.fetchUpdateAsync.mockResolvedValue({ isNew: false });

    await expect(applyUpdateIfAvailable()).resolves.toBe('up-to-date');
    expect(mock.reloadAsync).not.toHaveBeenCalled();
  });

  // Offline launch is the common case on a train or in a practice room. A rejected check
  // must not escape into the root layout, or the app fails to start with no network.
  it('swallows a failed check so the app still launches offline', async () => {
    mock.checkForUpdateAsync.mockRejectedValue(new Error('network down'));

    await expect(applyUpdateIfAvailable()).resolves.toBe('failed');
    expect(mock.reloadAsync).not.toHaveBeenCalled();
  });

  it('skips the check entirely in a dev client', async () => {
    mock.__setState({ isEnabled: false });

    await expect(applyUpdateIfAvailable()).resolves.toBe('disabled');
    expect(mock.checkForUpdateAsync).not.toHaveBeenCalled();
  });
});

describe('describeRunningBundle', () => {
  it('names an applied update by id and publish time', () => {
    mock.__setState({
      updateId: '99842a4a-2767-49c5-b006-10d8bdaf179d',
      createdAt: new Date('2026-09-17T06:55:46.744Z'),
      isEmbeddedLaunch: false,
    });

    expect(describeRunningBundle()).toBe('Bundle: 99842a4a · published 2026-09-17 06:55 UTC');
  });

  it('says so when no OTA has been applied over the installed build', () => {
    mock.__setState({ isEmbeddedLaunch: true, updateId: null });

    expect(describeRunningBundle()).toBe('Bundle: embedded with the build');
  });
});
