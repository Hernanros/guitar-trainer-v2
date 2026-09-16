// mobile/src/components/MetronomeControl.tsx
// Metronome transport for the drill screen — FLE-5 (Task 7).
//
// Takes a `bpm` and clicks at it. The drill screen passes the tempo ladder's
// current rung, so the tempo is never typed — it arrives from the drill and
// follows every ladder push automatically.
//
// The hook lives inside this component rather than on the screen so the engine
// only exists once a drill has actually loaded; the screen's early-return
// guards would otherwise make the hook call conditional.
//
// This is the ONLY file that imports expo-audio (approved on FLE-5). Everything
// else in src/metronome/ is written against the ClickEmitter interface, so the
// engine, scheduler, hook and adapter all stay testable without a native mock.
// The click is audible AND visual: the dots pulse off the same drift-free grid
// that fires the sound, so what you see is what you hear.
//
// Fletcher voice (.planning/design/fletcher-identity.md): sharp, diagnostic,
// no exclamation points. "Start click" / "Stop click", not "Let's go".
//
// Expo v57 / RN 0.86 surface only — Animated and Pressable are React Native
// core, so this adds no dependency.

import { useEffect, useRef } from 'react';
import { Animated, Pressable, StyleSheet, Text, View } from 'react-native';
import * as ExpoAudio from 'expo-audio';
import { useMetronome } from '../metronome/useMetronome';
import {
  createAudioClickEmitter,
  loadClickSources,
  prepareClickAudioMode,
} from '../metronome/audioEmitter';
import { DEFAULT_BEATS_PER_BAR } from '../metronome/scheduler';

export interface MetronomeControlProps {
  /** Tempo to click at — the drill tempo ladder's current rung. */
  bpm: number;
  beatsPerBar?: number;
}

// ---------------------------------------------------------------------------
// Pure-fn helpers — exported for tests, matching the DrillCard / drill-screen
// convention of testing logic without rendering.
// ---------------------------------------------------------------------------

/** "120 BPM" — the tempo readout. */
export function formatBpmLabel(bpm: number): string {
  return `${Math.round(bpm)} BPM`;
}

/** Transport label. Fletcher voice: a verb and a noun, nothing else. */
export function formatToggleLabel(running: boolean): string {
  return running ? 'Stop click' : 'Start click';
}

/** Accessibility label carrying the tempo, since the readout is visual. */
export function formatToggleAccessibilityLabel(running: boolean, bpm: number): string {
  return running ? `Stop click at ${formatBpmLabel(bpm)}` : `Start click at ${formatBpmLabel(bpm)}`;
}

/**
 * Which bar position dot should read as active.
 * Returns -1 while stopped so every dot renders dim.
 */
export function activeDotIndex(barBeat: number | null, running: boolean): number {
  if (!running || barBeat === null) return -1;
  return barBeat;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MetronomeControl({
  bpm,
  beatsPerBar = DEFAULT_BEATS_PER_BAR,
}: MetronomeControlProps) {
  const { running, beat, toggle, bpm: activeBpm } = useMetronome({
    bpm,
    beatsPerBar,
    // Called once, on first render (see useMetronome). Allocating the native
    // players here rather than on the first beat keeps file IO and decode off a
    // beat deadline.
    createEmitter: () => createAudioClickEmitter(ExpoAudio, loadClickSources()),
  });

  // Audio session config, once per mount. `playsInSilentMode` is the one that
  // matters: a phone propped on a music stand is very often on the silent
  // switch, and without it the metronome runs, keeps perfect time, and is
  // completely inaudible — which reads as a bug, not as a setting.
  // Failure here is not fatal; the click may just be quiet on a silenced phone.
  useEffect(() => {
    prepareClickAudioMode(ExpoAudio).catch(() => {
      // Swallowed deliberately: a rejected audio-mode call must not blank the
      // drill screen the user is mid-practice on.
    });
  }, []);

  // Flash on every beat. `beat` is a fresh object per emit, so identity change
  // is the trigger — no beat counter needed in the dependency array.
  const flash = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!beat) {
      flash.setValue(0);
      return;
    }
    flash.setValue(1);
    Animated.timing(flash, {
      toValue: 0,
      duration: 110,
      useNativeDriver: true,
    }).start();
  }, [beat, flash]);

  const active = activeDotIndex(beat?.barBeat ?? null, running);
  const dots = Array.from({ length: Math.max(1, beatsPerBar) }, (_, i) => i);

  return (
    <View style={styles.container}>
      <View style={styles.row}>
        <Pressable
          style={({ pressed }) => [
            styles.toggle,
            running && styles.toggleRunning,
            pressed && styles.togglePressed,
          ]}
          onPress={toggle}
          accessibilityRole="button"
          accessibilityState={{ selected: running }}
          accessibilityLabel={formatToggleAccessibilityLabel(running, activeBpm)}
        >
          <Text style={[styles.toggleLabel, running && styles.toggleLabelRunning]}>
            {formatToggleLabel(running)}
          </Text>
        </Pressable>

        <View style={styles.readout}>
          <Text style={styles.bpmLabel}>{formatBpmLabel(activeBpm)}</Text>
          <Text style={styles.bpmSource}>From this drill</Text>
        </View>
      </View>

      {/* Bar position — the downbeat dot is the beat that plays the accent sound. */}
      <View style={styles.dots} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
        {dots.map((i) => (
          <Animated.View
            key={i}
            style={[
              styles.dot,
              i === 0 && styles.dotDownbeat,
              i === active && styles.dotActive,
              i === active && { opacity: flash.interpolate({ inputRange: [0, 1], outputRange: [0.45, 1] }) },
            ]}
          />
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: '#242424',
    borderRadius: 10,
    padding: 14,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  toggle: {
    minHeight: 44,
    paddingHorizontal: 18,
    justifyContent: 'center',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#E07B39',
  },
  toggleRunning: {
    backgroundColor: '#E07B39',
  },
  togglePressed: {
    opacity: 0.85,
  },
  toggleLabel: {
    fontSize: 14,
    fontWeight: '700',
    color: '#E07B39',
  },
  toggleLabelRunning: {
    color: '#1A1A1A',
  },
  readout: {
    alignItems: 'flex-end',
  },
  bpmLabel: {
    fontSize: 18,
    fontWeight: '800',
    color: '#F5F5F5',
  },
  bpmSource: {
    fontSize: 11,
    color: '#777',
    marginTop: 2,
  },
  dots: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 14,
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#3A3A3A',
    opacity: 0.45,
  },
  dotDownbeat: {
    backgroundColor: '#4A4A4A',
  },
  dotActive: {
    backgroundColor: '#E07B39',
  },
});
