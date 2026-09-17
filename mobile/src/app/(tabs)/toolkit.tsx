// mobile/src/app/(tabs)/toolkit.tsx
// Toolkit tab — standalone metronome. Chromatic tuner still to come.
//
// Why this screen exists now: the metronome engine has shipped since FLE-5, but
// MetronomeControl was only ever mounted inside a drill screen. Reaching a drill
// means reaching a breakdown, so a spent breakdown quota took the metronome down
// with it — a capped user had no click at all, even though the click costs
// nothing and depends on no server call. Meanwhile this tab read "Coming soon"
// over a feature that was already built and tested.
//
// The tempo is therefore owned here rather than arriving from a drill's ladder.
// Nothing on this screen touches the API, which is the point: it works capped,
// offline, and before onboarding has produced a single song.
//
// Bounds, clamping and bar counting come from scheduler.ts so this screen and
// the drill screen cannot drift apart on what a legal tempo is.
//
// Fletcher voice (.planning/design/fletcher-identity.md): sharp, diagnostic, no
// exclamation points. A verb and a noun.
import React, { useCallback, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { MetronomeControl } from '../../components/MetronomeControl';
import { clampBpm, DEFAULT_BEATS_PER_BAR, MIN_BPM, MAX_BPM } from '../../metronome/scheduler';

// ---------------------------------------------------------------------------
// Pure-fn helpers — exported for tests, matching the MetronomeControl convention
// of testing logic without rendering.
// ---------------------------------------------------------------------------

/** 80 BPM: slow enough to fix a shape, fast enough not to feel punitive. */
export const DEFAULT_TOOLKIT_BPM = 80;

/** Step size. 5 matches the drill tempo ladder's rung, so a tempo found here
 *  transfers to a drill without re-learning what one step means. */
export const BPM_STEP = 5;

/** Named tempos, so the common case is one tap rather than sixteen. */
export const TEMPO_PRESETS = [60, 80, 100, 120, 160] as const;

/** Time signatures the scheduler's bar counting supports as a top number. */
export const BAR_PRESETS = [2, 3, 4, 6] as const;

/** Nudge the tempo by `delta`, staying inside the engine's own bounds. */
export function nextBpm(current: number, delta: number): number {
  return Math.round(clampBpm(current + delta));
}

export function formatBarLabel(beatsPerBar: number): string {
  return `${beatsPerBar}/4`;
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

export default function ToolkitScreen() {
  const [bpm, setBpm] = useState<number>(DEFAULT_TOOLKIT_BPM);
  const [beatsPerBar, setBeatsPerBar] = useState<number>(DEFAULT_BEATS_PER_BAR);

  const nudge = useCallback((delta: number) => {
    setBpm((current) => nextBpm(current, delta));
  }, []);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Metronome</Text>
      <Text style={styles.subtitle}>
        Set a tempo and hold it. Nothing here needs a connection.
      </Text>

      {/* Tempo selector — the drill screen gets this number from its ladder;
          here the user sets it directly. */}
      <View style={styles.section}>
        <Text style={styles.sectionLabel}>TEMPO</Text>
        <View style={styles.tempoRow}>
          <Pressable
            style={({ pressed }) => [
              styles.nudge,
              pressed && styles.nudgePressed,
              bpm <= MIN_BPM && styles.nudgeDisabled,
            ]}
            onPress={() => nudge(-BPM_STEP)}
            disabled={bpm <= MIN_BPM}
            accessibilityRole="button"
            accessibilityLabel={`Slower, ${BPM_STEP} BPM`}
          >
            <Text style={styles.nudgeLabel}>−</Text>
          </Pressable>

          <View style={styles.readout}>
            <Text testID="toolkit-bpm-readout" style={styles.readoutValue}>
              {bpm}
            </Text>
            <Text style={styles.readoutUnit}>BPM</Text>
          </View>

          <Pressable
            style={({ pressed }) => [
              styles.nudge,
              pressed && styles.nudgePressed,
              bpm >= MAX_BPM && styles.nudgeDisabled,
            ]}
            onPress={() => nudge(BPM_STEP)}
            disabled={bpm >= MAX_BPM}
            accessibilityRole="button"
            accessibilityLabel={`Faster, ${BPM_STEP} BPM`}
          >
            <Text style={styles.nudgeLabel}>+</Text>
          </Pressable>
        </View>

        <View style={styles.presetRow}>
          {TEMPO_PRESETS.map((preset) => (
            <Pressable
              key={preset}
              style={({ pressed }) => [
                styles.preset,
                bpm === preset && styles.presetActive,
                pressed && styles.presetPressed,
              ]}
              onPress={() => setBpm(preset)}
              accessibilityRole="button"
              accessibilityState={{ selected: bpm === preset }}
              // "Set tempo to ..." rather than a bare "80 BPM": MetronomeControl's
              // own tempo readout is already a button labelled "80 BPM" (its long
              // press opens the timing diagnostics), and two buttons with one label
              // is ambiguous to a screen reader, not just to a test query.
              accessibilityLabel={`Set tempo to ${preset} BPM`}
            >
              <Text style={[styles.presetLabel, bpm === preset && styles.presetLabelActive]}>
                {preset}
              </Text>
            </Pressable>
          ))}
        </View>
      </View>

      {/* The transport itself — the same component the drill screen mounts, so
          the click, the beat dots and the timing diagnostics behave identically. */}
      <View style={styles.section}>
        <Text style={styles.sectionLabel}>CLICK</Text>
        <MetronomeControl bpm={bpm} beatsPerBar={beatsPerBar} />
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionLabel}>BAR</Text>
        <View style={styles.presetRow}>
          {BAR_PRESETS.map((bar) => (
            <Pressable
              key={bar}
              style={({ pressed }) => [
                styles.preset,
                beatsPerBar === bar && styles.presetActive,
                pressed && styles.presetPressed,
              ]}
              onPress={() => setBeatsPerBar(bar)}
              accessibilityRole="button"
              accessibilityState={{ selected: beatsPerBar === bar }}
              accessibilityLabel={formatBarLabel(bar)}
            >
              <Text
                style={[styles.presetLabel, beatsPerBar === bar && styles.presetLabelActive]}
              >
                {formatBarLabel(bar)}
              </Text>
            </Pressable>
          ))}
        </View>
      </View>

      <Text style={styles.footnote}>Chromatic tuner — next.</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: '#fff',
  },
  content: {
    padding: 24,
    paddingBottom: 48,
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: '#1a1a1a',
  },
  subtitle: {
    fontSize: 14,
    color: '#888',
    marginTop: 6,
  },
  section: {
    marginTop: 28,
  },
  sectionLabel: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 1.2,
    color: '#888',
    marginBottom: 12,
  },
  tempoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  nudge: {
    width: 64,
    height: 64,
    borderRadius: 32,
    borderWidth: 2,
    borderColor: '#E07B39',
    alignItems: 'center',
    justifyContent: 'center',
  },
  nudgePressed: {
    backgroundColor: '#FBEDE3',
  },
  nudgeDisabled: {
    borderColor: '#DDD',
  },
  nudgeLabel: {
    fontSize: 30,
    fontWeight: '600',
    color: '#E07B39',
    lineHeight: 34,
  },
  readout: {
    alignItems: 'center',
  },
  readoutValue: {
    fontSize: 56,
    fontWeight: '700',
    color: '#1a1a1a',
    fontVariant: ['tabular-nums'],
  },
  readoutUnit: {
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 1.2,
    color: '#888',
    marginTop: -4,
  },
  presetRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginTop: 16,
  },
  preset: {
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: '#DDD',
  },
  presetActive: {
    borderColor: '#E07B39',
    backgroundColor: '#E07B39',
  },
  presetPressed: {
    opacity: 0.7,
  },
  presetLabel: {
    fontSize: 15,
    fontWeight: '600',
    color: '#555',
  },
  presetLabelActive: {
    color: '#fff',
  },
  footnote: {
    fontSize: 13,
    color: '#AAA',
    marginTop: 36,
  },
});
