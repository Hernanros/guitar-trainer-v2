// mobile/src/api/mmkv.test.ts
// Unit tests for the drill tempo-ladder persistence helpers (FLE-73).
//
// Uses the real manual mock at __mocks__/react-native-mmkv.ts (an in-memory
// Map keyed by instance id, not a jest.fn() stub) so these tests exercise
// actual read-after-write behavior — the thing that matters for "resume where
// you left off after the app is killed".

import { __resetAllStores } from '../../__mocks__/react-native-mmkv';
import {
  userMmkv,
  setDrillLadderState,
  getDrillLadderState,
  clearDrillLadderState,
} from './mmkv';

beforeEach(() => {
  __resetAllStores();
});

describe('getDrillLadderState — before any write', () => {
  it('returns null for a drill with no persisted state', () => {
    expect(getDrillLadderState(42, 0)).toBeNull();
  });
});

describe('setDrillLadderState / getDrillLadderState — round trip', () => {
  it('returns exactly what was persisted', () => {
    setDrillLadderState(42, 0, { currentBpm: 65, repCount: 3 });
    expect(getDrillLadderState(42, 0)).toEqual({ currentBpm: 65, repCount: 3 });
  });

  it('overwrites the previous rung on a later advance', () => {
    setDrillLadderState(42, 0, { currentBpm: 60, repCount: 5 });
    setDrillLadderState(42, 0, { currentBpm: 65, repCount: 0 });
    expect(getDrillLadderState(42, 0)).toEqual({ currentBpm: 65, repCount: 0 });
  });
});

describe('setDrillLadderState — keys are scoped per song+drill', () => {
  it('does not leak state between two drills on the same song', () => {
    setDrillLadderState(42, 0, { currentBpm: 60, repCount: 5 });
    setDrillLadderState(42, 1, { currentBpm: 80, repCount: 1 });
    expect(getDrillLadderState(42, 0)).toEqual({ currentBpm: 60, repCount: 5 });
    expect(getDrillLadderState(42, 1)).toEqual({ currentBpm: 80, repCount: 1 });
  });

  it('does not leak state between the same drill index on two songs', () => {
    setDrillLadderState(1, 0, { currentBpm: 60, repCount: 5 });
    setDrillLadderState(2, 0, { currentBpm: 90, repCount: 2 });
    expect(getDrillLadderState(1, 0)).toEqual({ currentBpm: 60, repCount: 5 });
    expect(getDrillLadderState(2, 0)).toEqual({ currentBpm: 90, repCount: 2 });
  });
});

describe('clearDrillLadderState', () => {
  it('makes a subsequent get return null', () => {
    setDrillLadderState(42, 0, { currentBpm: 65, repCount: 3 });
    clearDrillLadderState(42, 0);
    expect(getDrillLadderState(42, 0)).toBeNull();
  });

  it('only clears the targeted drill, not sibling drills', () => {
    setDrillLadderState(42, 0, { currentBpm: 65, repCount: 3 });
    setDrillLadderState(42, 1, { currentBpm: 80, repCount: 1 });
    clearDrillLadderState(42, 0);
    expect(getDrillLadderState(42, 0)).toBeNull();
    expect(getDrillLadderState(42, 1)).toEqual({ currentBpm: 80, repCount: 1 });
  });
});

describe('getDrillLadderState — malformed storage', () => {
  it('returns null instead of throwing when the stored value is not valid JSON', () => {
    // Simulates on-disk corruption / a future format change reading an old key.
    userMmkv.set('drill_ladder.42.0', 'not-json{');
    expect(getDrillLadderState(42, 0)).toBeNull();
  });
});
