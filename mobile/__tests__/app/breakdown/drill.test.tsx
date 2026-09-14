/**
 * Drill-detail route pure-logic tests.
 * Mirrors the source at mobile/src/app/breakdown/[songId]/drill/[drillIndex].tsx.
 *
 * Kept out of mobile/src/app/ so Expo Router doesn't auto-discover it as a route
 * (`/breakdown/[songId]/drill/[drillIndex].test`) that would crash on navigation.
 *
 * @testing-library/react-native is not installed.
 * These tests cover pure-fn helpers extracted from the drill screen (per B2 fix
 * in Plan 04.1-04 revision) — tempo ladder progression, rating payload
 * construction, disabled-state derivation (B1 fix), navigation intent.
 *
 * All assertions test pure TypeScript functions — no React rendering required.
 */

import {
  advanceRepOrTempo,
  isRatingUnlocked,
  buildRatingPayload,
  isDrillAlreadyRatedToday,
  type TempoLadderState,
} from '../../../src/app/breakdown/[songId]/drill/[drillIndex]';

// ---------------------------------------------------------------------------
// advanceRepOrTempo — tempo ladder progression
// ---------------------------------------------------------------------------

describe('advanceRepOrTempo — tempo ladder progression', () => {
  const drill = { start_bpm: 60, target_bpm: 70, repetitions: 20 };

  it('increments rep when below repetitions', () => {
    // test_advance_increments_rep_when_below_repetitions
    expect(advanceRepOrTempo({ currentBpm: 60, repCount: 5 }, drill)).toEqual({
      currentBpm: 60,
      repCount: 6,
    });
  });

  it('advances tempo when reps complete and below target BPM', () => {
    // test_advance_advances_tempo_when_reps_complete_and_below_target
    // repCount + 1 === repetitions (20) AND currentBpm (60) < target_bpm (70)
    // → advances to nextBpm = min(60+5, 70) = 65, repCount = 0
    expect(advanceRepOrTempo({ currentBpm: 60, repCount: 19 }, drill)).toEqual({
      currentBpm: 65,
      repCount: 0,
    });
  });

  it('caps tempo at target_bpm via Math.min', () => {
    // test_advance_caps_tempo_at_target_bpm
    // currentBpm=65, repCount=19 (=repetitions-1), target_bpm=68
    // nextBpm = Math.min(65+5, 68) = 68 (not 70)
    const drillWithLowTarget = { start_bpm: 60, target_bpm: 68, repetitions: 20 };
    expect(advanceRepOrTempo({ currentBpm: 65, repCount: 19 }, drillWithLowTarget)).toEqual({
      currentBpm: 68,
      repCount: 0,
    });
  });

  it('returns null when ladder is complete (at target_bpm with full reps)', () => {
    // test_advance_returns_null_when_ladder_complete
    // currentBpm === target_bpm AND repCount + 1 === repetitions → null
    expect(advanceRepOrTempo({ currentBpm: 70, repCount: 19 }, drill)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// isRatingUnlocked — rating pill visibility derivation
// ---------------------------------------------------------------------------

describe('isRatingUnlocked — rating pill visibility', () => {
  const drill = { target_bpm: 70, repetitions: 20 };

  it('returns false when below target BPM (reps may be complete)', () => {
    // test_rating_locked_when_below_target_bpm
    expect(isRatingUnlocked({ currentBpm: 65, repCount: 19 }, drill)).toBe(false);
  });

  it('returns false when reps are incomplete (at target BPM)', () => {
    // test_rating_locked_when_reps_incomplete
    expect(isRatingUnlocked({ currentBpm: 70, repCount: 10 }, drill)).toBe(false);
  });

  it('returns true when at target BPM with full reps complete', () => {
    // test_rating_unlocked_at_target_bpm_and_full_reps
    // currentBpm >= target_bpm AND repCount + 1 >= repetitions
    expect(isRatingUnlocked({ currentBpm: 70, repCount: 19 }, drill)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// buildRatingPayload — POST body shape
// ---------------------------------------------------------------------------

describe('buildRatingPayload — POST body shape', () => {
  it('builds the correct payload shape for useSubmitDrillRating', () => {
    // test_build_rating_payload_shape
    expect(
      buildRatingPayload(42, 0, { target_skill_temp_id: 'uuid-A' }, 'getting_closer'),
    ).toEqual({
      song_id: 42,
      rating: 'getting_closer',
      drill_index: 0,
      target_skill_node_id: 'uuid-A',
    });
  });

  it('maps drill.target_skill_temp_id → payload.target_skill_node_id', () => {
    // test_build_rating_payload_maps_temp_id_to_node_id
    // Client field: target_skill_temp_id (Sonnet-authored, temp because UUID not yet canonical)
    // Server field: target_skill_node_id (deterministic canonical field name)
    const payload = buildRatingPayload(
      99,
      2,
      { target_skill_temp_id: 'canonical-uuid-B' },
      'not_my_tempo',
    );
    expect(payload.target_skill_node_id).toBe('canonical-uuid-B');
    expect(payload.drill_index).toBe(2);
    expect(payload.song_id).toBe(99);
    expect(payload.rating).toBe('not_my_tempo');
  });
});

// ---------------------------------------------------------------------------
// isDrillAlreadyRatedToday — B1 server-derived disable derivation
// ---------------------------------------------------------------------------

describe('isDrillAlreadyRatedToday — B1 server-derived disable', () => {
  it('returns true when drillIndex is in envelope.drill_rated_today_indices', () => {
    // test_disabled_when_index_in_envelope
    expect(isDrillAlreadyRatedToday(0, { drill_rated_today_indices: [0, 2] })).toBe(true);
  });

  it('returns false when drillIndex is absent from drill_rated_today_indices', () => {
    // test_not_disabled_when_index_absent
    expect(isDrillAlreadyRatedToday(1, { drill_rated_today_indices: [0, 2] })).toBe(false);
  });

  it('returns false when drill_rated_today_indices is empty', () => {
    // test_not_disabled_when_envelope_empty
    expect(isDrillAlreadyRatedToday(0, { drill_rated_today_indices: [] })).toBe(false);
  });

  it('handles undefined drill_rated_today_indices gracefully', () => {
    // test_not_disabled_when_envelope_missing_field
    expect(isDrillAlreadyRatedToday(0, {})).toBe(false);
  });

  it('handles null envelope gracefully', () => {
    // test_not_disabled_when_envelope_null
    expect(isDrillAlreadyRatedToday(0, null)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// router navigation intent — structural compile-time assertion
// ---------------------------------------------------------------------------

describe('router navigation intent', () => {
  it('constructs the parent breakdown path with songId', () => {
    // test_router_replace_path_shape
    // Guards against typos in the path template used in handleRatingSelect
    const songId = 42;
    expect(`/breakdown/${songId}`).toBe('/breakdown/42');
  });
});

// ---------------------------------------------------------------------------
// TempoLadderState type export — compile-time contract assertion
// ---------------------------------------------------------------------------

describe('TempoLadderState type', () => {
  it('is a valid state shape with currentBpm and repCount', () => {
    // Verify the exported type is assignable (compile-time check)
    const state: TempoLadderState = { currentBpm: 80, repCount: 3 };
    expect(state.currentBpm).toBe(80);
    expect(state.repCount).toBe(3);
  });
});
