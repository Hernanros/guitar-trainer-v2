// mobile/src/components/DrillCard.test.tsx
// Phase 4.1 Plan 03 — compile-time contract + pure-fn tests for DrillCard.
//
// Follows the compile-time-contract pattern from
// mobile/__tests__/app/breakdown/[songId].test.tsx per Plan 03 <behavior>:
// pure-fn helpers extracted from DrillCard are tested directly; component
// shape / prop contract is asserted via TypeScript compile-time assignments
// (structural assertions that fail tsc --noEmit if a field drifts).
//
// Render-level tests for the DrillCard visual output (drill name renders,
// Start button appears, etc.) are covered by the on-device verification
// checkpoint in Plan 05 — not attempted here.
import type { components } from '../api/generated/schema';
import {
  DrillCard,
  formatSubtitle,
  formatTempoLadder,
  formatStartAccessibilityLabel,
  shouldShowCommonTrap,
  type DrillCardProps,
  type Drill,
} from './DrillCard';

// ---------------------------------------------------------------------------
// Fixture helper — a valid Drill (all 10 fields populated) with overrides.
// ---------------------------------------------------------------------------

function makeDrill(overrides?: Partial<Drill>): Drill {
  return {
    name: 'Isolate the b3→3 slide',
    target_skill_temp_id: '11111111-1111-1111-1111-111111111111',
    song_specific: true,
    what: 'Play just the slide. Two notes. Nothing else.',
    tab_snippet: {
      measures: [
        {
          beats: [{ notes: [{ string: 3, fret: 3, duration: 'quarter' }] }],
          time_signature: '4/4',
        },
      ],
      tuning: ['Eb', 'Ab', 'Db', 'Gb', 'Bb', 'Eb'],
    },
    start_bpm: 60,
    target_bpm: 70,
    repetitions: 20,
    success_criterion: 'Slide arrives on the beat without a bump.',
    common_trap: 'Beginners over-anchor the ring finger.',
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// drill_type_shape — compile-time assertion that Drill has all 10 fields
// with the expected TypeScript shape. If a field is renamed or dropped in
// schema.d.ts, tsc --noEmit fails at this assignment.
// ---------------------------------------------------------------------------

describe('Drill type shape (compile-time contract)', () => {
  it('Drill type has all 10 fields with the expected TypeScript shape', () => {
    // Compile-time check: this assignment fails tsc --noEmit if the shape drifts.
    const _drillTypeCheck: Drill = {
      name: 'x',
      target_skill_temp_id: 'x',
      song_specific: true,
      what: 'x',
      tab_snippet: { measures: [], tuning: [] },
      start_bpm: 1,
      target_bpm: 2,
      repetitions: 1,
      success_criterion: 'x',
      common_trap: 'x',
    };
    // Runtime sanity: every named field is present on the fixture.
    expect(_drillTypeCheck.name).toBeDefined();
    expect(_drillTypeCheck.target_skill_temp_id).toBeDefined();
    expect(_drillTypeCheck.song_specific).toBeDefined();
    expect(_drillTypeCheck.what).toBeDefined();
    expect(_drillTypeCheck.tab_snippet).toBeDefined();
    expect(_drillTypeCheck.start_bpm).toBeDefined();
    expect(_drillTypeCheck.target_bpm).toBeDefined();
    expect(_drillTypeCheck.repetitions).toBeDefined();
    expect(_drillTypeCheck.success_criterion).toBeDefined();
    expect(_drillTypeCheck.common_trap).toBeDefined();
  });

  it('Drill.common_trap is nullable / optional (per Sonnet contract)', () => {
    // Compile-time: assignment must accept null and omission.
    const _nullTrap: Drill = makeDrill({ common_trap: null });
    const _absentTrap: Drill = { ...makeDrill(), common_trap: undefined };
    expect(_nullTrap.common_trap).toBeNull();
    expect(_absentTrap.common_trap).toBeUndefined();
  });

  it('Drill type is reachable via components["schemas"]["Drill"] from generated schema', () => {
    // Compile-time proof: the local Drill export IS the schema-derived type.
    const _schemaDrill: components['schemas']['Drill'] = makeDrill();
    const _localDrill: Drill = _schemaDrill;
    expect(_localDrill.name).toBe(_schemaDrill.name);
  });
});

// ---------------------------------------------------------------------------
// formatSubtitle — "For this song" vs "Any song"
// ---------------------------------------------------------------------------

describe('formatSubtitle', () => {
  it('returns "For this song" when drill.song_specific === true', () => {
    expect(formatSubtitle(makeDrill({ song_specific: true }))).toBe('For this song');
  });

  it('returns "Any song" when drill.song_specific === false', () => {
    expect(formatSubtitle(makeDrill({ song_specific: false }))).toBe('Any song');
  });
});

// ---------------------------------------------------------------------------
// formatTempoLadder — "60 → 70 BPM"
// ---------------------------------------------------------------------------

describe('formatTempoLadder', () => {
  it('returns "60 → 70 BPM" for start_bpm=60, target_bpm=70', () => {
    expect(formatTempoLadder(makeDrill({ start_bpm: 60, target_bpm: 70 }))).toBe('60 → 70 BPM');
  });

  it('preserves BPM values verbatim (no rounding, no formatting)', () => {
    expect(formatTempoLadder(makeDrill({ start_bpm: 45, target_bpm: 120 }))).toBe(
      '45 → 120 BPM',
    );
  });
});

// ---------------------------------------------------------------------------
// formatStartAccessibilityLabel
// ---------------------------------------------------------------------------

describe('formatStartAccessibilityLabel', () => {
  it('returns "Start drill: {name}" for a drill', () => {
    expect(formatStartAccessibilityLabel(makeDrill({ name: 'Isolate the b3→3 slide' }))).toBe(
      'Start drill: Isolate the b3→3 slide',
    );
  });
});

// ---------------------------------------------------------------------------
// shouldShowCommonTrap — conditional-render helper
// ---------------------------------------------------------------------------

describe('shouldShowCommonTrap', () => {
  it('returns true iff drill.common_trap is a non-empty string', () => {
    expect(shouldShowCommonTrap(makeDrill({ common_trap: 'watch out' }))).toBe(true);
  });

  it('returns false when drill.common_trap is null', () => {
    expect(shouldShowCommonTrap(makeDrill({ common_trap: null }))).toBe(false);
  });

  it('returns false when drill.common_trap is an empty string', () => {
    expect(shouldShowCommonTrap(makeDrill({ common_trap: '' }))).toBe(false);
  });

  it('returns false when drill.common_trap is undefined', () => {
    const drill = { ...makeDrill(), common_trap: undefined } as Drill;
    expect(shouldShowCommonTrap(drill)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// onStart prop contract — compile-time assertion that the callback signature
// is exactly (drillIndex: number) => void.
// ---------------------------------------------------------------------------

describe('DrillCardProps.onStart prop contract', () => {
  it('typechecks with onStart: (drillIndex: number) => void', () => {
    // Compile-time: fails tsc if onStart signature drifts.
    const _propCheck: DrillCardProps = {
      drill: makeDrill(),
      drillIndex: 0,
      onStart: (i) => {
        // narrow that `i` is a number
        const _n: number = i;
        void _n;
      },
    };
    expect(_propCheck.drillIndex).toBe(0);
    expect(typeof _propCheck.onStart).toBe('function');
  });

  it('DrillCard is exported as a callable component', () => {
    // Compile-time: DrillCard must accept DrillCardProps.
    const _componentCheck: (props: DrillCardProps) => unknown = DrillCard;
    expect(typeof _componentCheck).toBe('function');
  });
});
