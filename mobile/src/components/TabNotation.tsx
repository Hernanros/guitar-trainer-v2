// mobile/src/components/TabNotation.tsx
// Hand-rolled react-native-svg tab staff — D-03 compliant.
// Phase 3 refactor: renders ALL measures with horizontal ScrollView + React.memo per Measure.
// Phase 1 rendered only measures[0]; Phase 3 extends for multi-measure scrolling (RESEARCH §2).
//
// CRITICAL: Avoid SVG masking primitives — per RESEARCH §8 landmine 9 (Windows perf regression).
// String lines drawn once for full staff width at the parent Svg level (RESEARCH §2 recommendation).
// React.memo per Measure prevents re-render storms when parent state changes (RESEARCH §2).
//
// Layout constants preserved from Phase 1 (must not change — ChordDiagram calibrates to these):
//   STRING_SPACING = 20, BEAT_WIDTH = 48, LEFT_MARGIN = 30, TOP_PADDING = 20, STRINGS = 6
//
// FLE-105 §9 / FLE-116: width is derived from each beat's duration rather than a fixed 4-beat
// measure (§9.1), and the v2 articulation/effect fields are drawn (§9.2). FLE-115 landed the
// v2 fields server-side and FLE-119 regenerated `schema.d.ts` against it, so the types below
// are aliases onto the generated schema rather than hand-rolled duplicates of it.
import React from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { G, Line, Path, Rect, Svg, Text as SvgText } from 'react-native-svg';
import type { components } from '../api/generated/schema';

// ---------------------------------------------------------------------------
// v2 schema aliases (FLE-105 §1, §2, §4, §5, §6, §7) — see file header.
// ---------------------------------------------------------------------------

type Duration = NonNullable<components['schemas']['Beat']['rest']>;
type Link = NonNullable<components['schemas']['Note']['link_to_next']>;
type BendKind = components['schemas']['Bend']['kind'];
type Technique = NonNullable<components['schemas']['Note']['technique']>;
type Effect = NonNullable<components['schemas']['Note']['effects']>[number];
type Bend = components['schemas']['Bend'];

type Note = components['schemas']['Note'];
type Beat = components['schemas']['Beat'];
type MeasureT = components['schemas']['Measure'];
type Tab = components['schemas']['Tab'];

// Layout constants — preserved verbatim from Phase 1 (PATTERNS.md lines 748-753)
const STRING_SPACING = 20;
const BEAT_WIDTH = 48;
const LEFT_MARGIN = 30;
const TOP_PADDING = 20;
const STRINGS = 6;

// §9.1 — width from content. MIN_COLUMN keeps a thirty-second note's column from collapsing
// to nothing; everything longer than a quarter gets proportionally more room.
const MIN_COLUMN = 20;

// §2.2's beat-value table, as plain numbers — this is layout math, not meter validation, so
// float beat-counts are fine here even though the server's own table MUST be `Fraction` (§2.2).
const DURATION_BEATS: Record<string, number> = {
  whole: 4,
  half: 2,
  quarter: 1,
  eighth: 0.5,
  sixteenth: 0.25,
  'thirty-second': 0.125,
  'dotted-half': 3,
  'dotted-quarter': 1.5,
  'dotted-eighth': 0.75,
  'dotted-sixteenth': 0.375,
  'triplet-half': 4 / 3,
  'triplet-quarter': 2 / 3,
  'triplet-eighth': 1 / 3,
  'triplet-sixteenth': 1 / 6,
};

// Unknown durations (a legacy alias §8.2 missed, or a value from a newer server) degrade to
// one beat rather than crashing or collapsing the column — the same "coerce, don't raise"
// house pattern as the server's §8.2 coercer, applied on the render side.
function beatsOf(duration: string | null | undefined): number {
  if (!duration) return 1;
  return DURATION_BEATS[duration] ?? 1;
}

function durationOf(beat: Beat): string {
  if (beat.rest) return beat.rest;
  return beat.notes?.[0]?.duration ?? 'quarter';
}

function columnWidth(duration: string | null | undefined): number {
  return Math.max(MIN_COLUMN, BEAT_WIDTH * beatsOf(duration));
}

/** Left edge (relative to the measure's own content start) of every beat in the measure. */
function beatLefts(beats: Beat[]): number[] {
  let acc = 0;
  return beats.map((beat) => {
    const left = acc;
    acc += columnWidth(durationOf(beat));
    return left;
  });
}

function measureWidth(measure: MeasureT): number {
  const beats = measure.beats ?? [];
  return LEFT_MARGIN + beats.reduce((w, b) => w + columnWidth(durationOf(b)), 0) + 8;
}

// §9.2 glyph tables — every lookup below is a plain object so an unrecognised value (a future
// server's enum extension) looks up to `undefined` and draws nothing extra, never throws.
const BEND_AMOUNT_LABEL: Record<number, string> = {
  0.25: '¼',
  0.5: '½',
  1.0: 'full',
  1.5: '1½',
  2.0: '2',
  2.5: '2½',
  3.0: '3',
};

function bendSuffix(bend: Bend): string {
  const amount = BEND_AMOUNT_LABEL[bend.semitones] ?? String(bend.semitones);
  const prefix = bend.kind === 'pre_bend' || bend.kind === 'pre_bend_release' ? 'pb' : 'b';
  const release = bend.kind === 'bend_release' || bend.kind === 'pre_bend_release' ? 'r' : '';
  return `${prefix}${amount}${release}`;
}

function bendArrow(bend: Bend): string {
  const release = bend.kind === 'bend_release' || bend.kind === 'pre_bend_release';
  return release ? '↑↓' : '↑';
}

// technique: natural_harmonic (fret in <>) and rake (beat-level bracket, see beatHasRake) are
// handled separately — everything else here is a short label drawn above the column.
const TECHNIQUE_ABOVE_LABEL: Partial<Record<Technique, string>> = {
  tap: 'T',
  slap: 'S',
  pop: 'P',
  pinch_harmonic: 'P.H.',
  artificial_harmonic: 'A.H.',
};

// effect: dead (replaces the fret label), vibrato/wide_vibrato (trailing glyph), let_ring and
// palm_mute (spanning dashed bracket, drawn by the overlay pass) are handled separately.
const EFFECT_ABOVE_LABEL: Partial<Record<Effect, string>> = {
  accent: '>',
  staccato: '•',
};

function fretLabelFor(note: Note): string {
  if (note.effects?.includes('dead') || note.fret === -1) return 'x'; // -1 is §6.1 cached-row tolerance
  if (note.technique === 'natural_harmonic') return `<${note.fret}>`;
  return String(note.fret);
}

function aboveLabelsFor(note: Note): string[] {
  const labels: string[] = [];
  const techLabel = note.technique ? TECHNIQUE_ABOVE_LABEL[note.technique] : undefined;
  if (techLabel) labels.push(techLabel);
  for (const eff of note.effects ?? []) {
    const l = EFFECT_ABOVE_LABEL[eff];
    if (l) labels.push(l);
  }
  return labels;
}

function trailingGlyphFor(note: Note): string {
  if (note.effects?.includes('wide_vibrato')) return '≈≈≈';
  if (note.effects?.includes('vibrato')) return '~~~';
  return '';
}

function beatHasRake(beat: Beat): boolean {
  return (beat.notes ?? []).some((n) => n.technique === 'rake');
}

const ROMAN = ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII'];
function romanize(n: number): string {
  return ROMAN[n] ?? String(n);
}

// Tuning awareness (Task #16 / quick 260909-01) — surface Sonnet's tuning choice
// so intermediate/advanced players can immediately see whether the tab is in the
// song's canonical tuning. Arrays MUST match server/app/ai/breakdown.py SYSTEM_PROMPT
// TUNING block exactly.
const STANDARD_TUNING = ['E', 'A', 'D', 'G', 'B', 'e'];
const KNOWN_TUNINGS: Record<string, string> = {
  // Half-step-down and whole-step-down (very common in classic rock/blues —
  // SRV, Hendrix, Slash, Van Halen, Alice in Chains, etc.)
  'Eb|Ab|Db|Gb|Bb|Eb': 'E♭ standard',
  'D|G|C|F|A|D': 'D standard',
  // Alternate / open tunings
  'E|B|E|G#|B|E': 'Open E',
  'D|A|D|F#|A|D': 'Open D',
  'D|G|D|G|B|D': 'Open G',
  'D|A|D|G|A|D': 'DADGAD',
  'D|A|D|G|B|E': 'Drop D',
  'C|G|C|F|A|D': 'Drop C',
};

function tuningDisplay(tuning: string[]): { friendly: string | null; raw: string } {
  const key = tuning.join('|');
  const friendly = KNOWN_TUNINGS[key] ?? null;
  const raw = tuning.join(' ');
  return { friendly, raw };
}

function isStandardTuning(tuning: string[] | null | undefined): boolean {
  if (!tuning || tuning.length !== 6) return true; // defensive: treat malformed as standard (no label)
  return tuning.every((s, i) => s === STANDARD_TUNING[i]);
}

function feelLabel(feel: string | null | undefined): string | null {
  if (feel === 'swing') return 'Swing 8ths';
  if (feel === 'shuffle') return 'Shuffle';
  return null; // 'straight', unset, or an unknown future value — nothing drawn (§9.2)
}

// ---------------------------------------------------------------------------
// Measure component — memoized to prevent re-renders on parent state change
// ---------------------------------------------------------------------------

interface MeasureProps {
  measure: MeasureT;
  offsetX: number;
  measureIndex: number;
}

const Measure = React.memo(function Measure({ measure, offsetX, measureIndex }: MeasureProps) {
  const beats = measure.beats ?? [];
  const lefts = beatLefts(beats);
  const staffHeight = (STRINGS - 1) * STRING_SPACING;

  return (
    <G>
      {/* Barline at the start of each measure (x = offsetX) */}
      {measureIndex > 0 && (
        <Line
          x1={offsetX + LEFT_MARGIN - 2}
          y1={0}
          x2={offsetX + LEFT_MARGIN - 2}
          y2={staffHeight}
          stroke="#555"
          strokeWidth={1}
        />
      )}
      {beats.map((beat, beatIndex) => {
        const left = offsetX + LEFT_MARGIN + lefts[beatIndex];
        const width = columnWidth(durationOf(beat));
        const beatX = left + width / 2;
        const notes = beat.notes ?? [];

        // A rest beat (§3): notes == [] and rest set. Centred vertically in the column,
        // deliberately off any string line.
        if (notes.length === 0 && beat.rest) {
          return (
            <SvgText
              key={`m${measureIndex}-b${beatIndex}-rest`}
              x={beatX}
              y={staffHeight / 2 + 3}
              textAnchor="middle"
              fontSize={10}
              fontStyle="italic"
              fill="#777"
            >
              rest
            </SvgText>
          );
        }

        const rake = beatHasRake(beat);
        const rakeYs = notes.map((n) => (n.string - 1) * STRING_SPACING);
        const rakeTop = rake ? Math.min(...rakeYs) : 0;
        const rakeBottom = rake ? Math.max(...rakeYs) : 0;

        return (
          <G key={`m${measureIndex}-b${beatIndex}`}>
            {rake && (
              <G>
                <Line
                  x1={left - 4}
                  y1={rakeTop}
                  x2={left - 4}
                  y2={rakeBottom}
                  stroke="#999"
                  strokeWidth={1}
                />
                <SvgText x={left - 4} y={rakeTop - 4} textAnchor="middle" fontSize={8} fill="#999">
                  rake
                </SvgText>
              </G>
            )}
            {notes.map((note: Note, noteIndex: number) => {
              // string 1 = high e = top line (y=0), string 6 = low E = bottom (y=5*STRING_SPACING)
              const y = (note.string - 1) * STRING_SPACING;
              const fretLabel = fretLabelFor(note);
              const rectWidth = Math.max(12, fretLabel.length * 7);
              const trailing = trailingGlyphFor(note);
              const bendText = note.bend ? bendSuffix(note.bend) : '';
              const above = aboveLabelsFor(note);

              return (
                <G key={`m${measureIndex}-b${beatIndex}-n${noteIndex}`}>
                  {/* Background rect to occlude the string line behind the fret number */}
                  <Rect
                    x={beatX - rectWidth / 2}
                    y={y - 7}
                    width={rectWidth}
                    height={13}
                    fill="#1A1A1A"
                  />
                  <SvgText
                    x={beatX}
                    y={y + 4}
                    textAnchor="middle"
                    fontSize={12}
                    fill="#F5F5F5"
                    fontWeight="600"
                  >
                    {fretLabel}
                  </SvgText>
                  {bendText && (
                    <SvgText
                      x={beatX + rectWidth / 2 + 2}
                      y={y + 4}
                      textAnchor="start"
                      fontSize={10}
                      fill="#E07B39"
                    >
                      {bendText}
                    </SvgText>
                  )}
                  {note.bend && (
                    <SvgText x={beatX} y={y - 10} textAnchor="middle" fontSize={9} fill="#E07B39">
                      {bendArrow(note.bend)}
                    </SvgText>
                  )}
                  {trailing && (
                    <SvgText
                      x={beatX + rectWidth / 2 + 2}
                      y={y + 4}
                      textAnchor="start"
                      fontSize={10}
                      fill="#999"
                    >
                      {trailing}
                    </SvgText>
                  )}
                  {above.map((label, i) => (
                    <SvgText
                      key={`above-${i}`}
                      x={beatX}
                      y={y - 10 - i * 10}
                      textAnchor="middle"
                      fontSize={9}
                      fill="#999"
                    >
                      {label}
                    </SvgText>
                  ))}
                </G>
              );
            })}
          </G>
        );
      })}
    </G>
  );
});

// ---------------------------------------------------------------------------
// Connectors and brackets — §4 links (tie/hammer_on/pull_off/slide) and the §9.2
// let_ring/palm_mute spanning brackets. These can cross a beat or a measure boundary (a tie
// over a barline is how sustain past the bar is written — Comfortably Numb needs it), so they
// are computed once over the whole flattened tab rather than inside the memoized Measure.
// ---------------------------------------------------------------------------

interface FlatNote {
  x: number;
  y: number;
  string: number;
  note: Note;
}

function flattenNotes(measures: MeasureT[], measureOffsets: number[]): FlatNote[] {
  const out: FlatNote[] = [];
  measures.forEach((measure, mIdx) => {
    const beats = measure.beats ?? [];
    const lefts = beatLefts(beats);
    beats.forEach((beat, bIdx) => {
      const width = columnWidth(durationOf(beat));
      const cx = measureOffsets[mIdx] + LEFT_MARGIN + lefts[bIdx] + width / 2;
      (beat.notes ?? []).forEach((note) => {
        out.push({ x: cx, y: (note.string - 1) * STRING_SPACING, string: note.string, note });
      });
    });
  });
  return out;
}

const LINK_LETTER: Record<'hammer_on' | 'pull_off', string> = { hammer_on: 'h', pull_off: 'p' };

function Connectors({ flat }: { flat: FlatNote[] }) {
  const byString = new Map<number, FlatNote[]>();
  for (const fn of flat) {
    const list = byString.get(fn.string) ?? [];
    list.push(fn);
    byString.set(fn.string, list);
  }

  const elements: React.ReactNode[] = [];

  byString.forEach((list, stringNum) => {
    list.forEach((fn, i) => {
      const link = fn.note.link_to_next;
      if (!link) return;
      const next = list[i + 1];
      if (!next) return; // §4 "a next note on this string exists" — defensive, not a crash

      const midX = (fn.x + next.x) / 2;
      const key = `link-${stringNum}-${i}`;

      if (link === 'tie') {
        const top = fn.y - 12;
        elements.push(
          <Path
            key={key}
            d={`M ${fn.x} ${fn.y - 7} Q ${midX} ${top} ${next.x} ${next.y - 7}`}
            stroke="#999"
            strokeWidth={1}
            fill="none"
          />
        );
        return;
      }

      if (link === 'hammer_on' || link === 'pull_off') {
        elements.push(
          <SvgText key={key} x={midX} y={fn.y - 9} textAnchor="middle" fontSize={9} fill="#999">
            {LINK_LETTER[link]}
          </SvgText>
        );
        return;
      }

      if (link === 'slide') {
        const rising = next.note.fret > fn.note.fret;
        elements.push(
          <SvgText key={key} x={midX} y={fn.y + 4} textAnchor="middle" fontSize={11} fill="#999">
            {rising ? '/' : '\\'}
          </SvgText>
        );
      }
      // Unknown link values (a future server's enum) draw nothing — never crash (§9.2).
    });
  });

  // §2.4.1 / §9.2 — let_ring and palm_mute: a dashed bracket spanning each contiguous run of
  // notes (on one string) that carries the effect. let_ring is the entire sustain story for the
  // fingerstyle catalog and is load-bearing, not decoration.
  const BRACKET_Y: Record<'let_ring' | 'palm_mute', number> = { let_ring: -16, palm_mute: -8 };
  const BRACKET_LABEL: Record<'let_ring' | 'palm_mute', string> = {
    let_ring: 'let ring',
    palm_mute: 'P.M.',
  };

  (['let_ring', 'palm_mute'] as const).forEach((effect) => {
    byString.forEach((list, stringNum) => {
      let runStart: FlatNote | null = null;
      let runEnd: FlatNote | null = null;
      let runIndex = 0;

      const flushRun = () => {
        if (!runStart || !runEnd) return;
        const y = BRACKET_Y[effect];
        const key = `bracket-${effect}-${stringNum}-${runIndex}`;
        elements.push(
          <G key={key}>
            <Line
              x1={runStart.x}
              y1={y}
              x2={runEnd.x}
              y2={y}
              stroke="#E07B39"
              strokeWidth={1}
              strokeDasharray="4,3"
            />
            <SvgText x={runStart.x} y={y - 3} textAnchor="start" fontSize={8} fill="#E07B39">
              {BRACKET_LABEL[effect]}
            </SvgText>
          </G>
        );
        runIndex += 1;
        runStart = null;
        runEnd = null;
      };

      for (const fn of list) {
        const has = fn.note.effects?.includes(effect) ?? false;
        if (has) {
          if (!runStart) runStart = fn;
          runEnd = fn;
        } else {
          flushRun();
        }
      }
      flushRun();
    });
  });

  return <G>{elements}</G>;
}

// ---------------------------------------------------------------------------
// TabNotation — outer component with horizontal ScrollView
// ---------------------------------------------------------------------------

export function TabNotation({ tab }: { tab: Tab }) {
  const tuning = tab.tuning ?? STANDARD_TUNING;
  const measures = tab.measures ?? [];

  // String labels: tuning is low-to-high [E, A, D, G, B, e].
  // Reverse so index 0 = high e (top line) and index 5 = low E (bottom line).
  const stringLabels = [...tuning].reverse();

  const staffHeight = (STRINGS - 1) * STRING_SPACING;
  const svgHeight = staffHeight + TOP_PADDING * 2;

  // §9.1 — measure offsets are a running sum of each measure's own content width, not
  // `i * MEASURE_WIDTH`. A 4/4 bar of straight sixteenths no longer overflows its box.
  const measureOffsets: number[] = [];
  {
    let acc = 0;
    for (const m of measures) {
      measureOffsets.push(acc);
      acc += measureWidth(m);
    }
  }
  const totalWidth =
    LEFT_MARGIN + measures.reduce((w, m) => w + measureWidth(m), 0) + 16;

  const flat = flattenNotes(measures, measureOffsets);

  // Tuning label — render only when Sonnet chose a non-standard tuning.
  // Standard / null / undefined / malformed tuning renders no label (defensive).
  const isNonStandard = !isStandardTuning(tab.tuning);
  const { friendly, raw } = isNonStandard
    ? tuningDisplay(tuning)
    : { friendly: null, raw: '' };

  // §9.2 — the setup line. Tuning, capo, and feel each render independently of the others:
  // Wonderwall is standard tuning at capo 2, so gating the capo badge on non-standard tuning
  // would show the player nothing at all.
  const capo = tab.capo ?? 0;
  const feel = feelLabel(tab.feel);

  return (
    <View style={styles.wrapper}>
      {(isNonStandard || capo > 0 || feel) && (
        <View style={styles.setupLine}>
          {isNonStandard && (
            <Text style={styles.tuningLabel}>
              <Text style={styles.tuningPrefix}>Tuning: </Text>
              {friendly ? (
                <>
                  <Text style={styles.tuningValue}>{friendly}</Text>
                  <Text style={styles.tuningRaw}>{` — ${raw}`}</Text>
                </>
              ) : (
                <Text style={styles.tuningValue}>{raw}</Text>
              )}
            </Text>
          )}
          {capo > 0 && (
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{`Capo ${romanize(capo)}`}</Text>
            </View>
          )}
          {feel && (
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{feel}</Text>
            </View>
          )}
        </View>
      )}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={true}
        contentContainerStyle={styles.scrollContent}
      >
        <Svg width={totalWidth} height={svgHeight}>
          <G translateY={TOP_PADDING}>
            {/* String lines drawn ONCE for the full staff width (RESEARCH §2 recommendation) */}
            {Array.from({ length: STRINGS }).map((_, i) => (
              <Line
                key={`str-${i}`}
                x1={LEFT_MARGIN - 4}
                y1={i * STRING_SPACING}
                x2={totalWidth - 8}
                y2={i * STRING_SPACING}
                stroke="#999"
                strokeWidth={1}
              />
            ))}

            {/* Capo bar — a thick vertical bar at the staff start (§9.2) */}
            {capo > 0 && (
              <Line
                x1={LEFT_MARGIN - 4}
                y1={-2}
                x2={LEFT_MARGIN - 4}
                y2={staffHeight + 2}
                stroke="#E07B39"
                strokeWidth={3}
              />
            )}

            {/* String name labels on the left (tuning high-to-low) */}
            {stringLabels.map((label, i) => (
              <SvgText
                key={`label-${i}`}
                x={LEFT_MARGIN - 8}
                y={i * STRING_SPACING + 4}
                textAnchor="end"
                fontSize={10}
                fill="#999"
              >
                {label}
              </SvgText>
            ))}

            {/* Measures — memoized per React.memo(Measure); tab.measures.map loops ALL measures */}
            {measures.map((measure, i) => (
              <Measure key={i} measure={measure} offsetX={measureOffsets[i]} measureIndex={i} />
            ))}

            {/* Links and spanning brackets — computed over the whole flattened tab (see Connectors) */}
            <Connectors flat={flat} />
          </G>
        </Svg>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    marginVertical: 4,
  },
  scrollContent: {
    flexGrow: 0,
  },
  setupLine: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 6,
  },
  tuningLabel: {
    fontSize: 12,
  },
  tuningPrefix: {
    color: '#999',
    fontSize: 12,
  },
  tuningValue: {
    color: '#E07B39',
    fontSize: 12,
    fontWeight: '600',
  },
  tuningRaw: {
    color: '#999',
    fontSize: 12,
  },
  badge: {
    borderWidth: 1,
    borderColor: '#E07B39',
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  badgeText: {
    color: '#E07B39',
    fontSize: 11,
    fontWeight: '600',
  },
});
