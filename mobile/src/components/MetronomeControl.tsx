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

import { useEffect, useRef, useState } from 'react';
import { Animated, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import * as ExpoAudio from 'expo-audio';
import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import { useMetronome } from '../metronome/useMetronome';
import {
  createAudioClickEmitter,
  loadClickSources,
  prepareClickAudioMode,
} from '../metronome/audioEmitter';
import { DEFAULT_BEATS_PER_BAR } from '../metronome/scheduler';
import { formatDriftSummary, maxDriftAsBeatFraction } from '../metronome/driftStats';

/** Scopes the wake lock to the metronome, so releasing it cannot cancel another
 *  screen's lock (expo-keep-awake reference-counts by tag). */
const KEEP_AWAKE_TAG = 'metronome-click';

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

/**
 * Worst-case jitter stated as a share of the beat, because milliseconds alone
 * are not judgeable: 8ms is inaudible at 60 BPM and obvious at 280. Under 1%
 * of a beat is not hearable by anyone; the tester records the number either
 * way rather than deciding what counts as a pass on the spot.
 */
export function formatJitterShare(fraction: number): string {
  return `max jitter ${(fraction * 100).toFixed(1)}% of a beat`;
}

/** The hint that makes the timing readout discoverable without shipping it visible. */
export const TIMING_TOGGLE_HINT = 'Long press to show timing diagnostics';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MetronomeControl({
  bpm,
  beatsPerBar = DEFAULT_BEATS_PER_BAR,
}: MetronomeControlProps) {
  const { running, beat, toggle, bpm: activeBpm, readDriftStats } = useMetronome({
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

  // Hold the screen awake while the click is running.
  //
  // This is what makes the Done-when's "full session-length run" actually
  // reachable on hardware. A phone is propped on a music stand, untouched, for
  // the length of a drill; iOS auto-lock defaults to 30s. On lock the app is
  // suspended, JS timers stop, and the click dies — so a 45-minute hold-tempo
  // walk would have failed at the first minute no matter how good the grid
  // math is, and the tester would have had to tap the screen to keep it alive.
  //
  // Keyed on `running`, not on mount: the screen is only pinned while the click
  // is actually going, so a paused drill still auto-locks and the battery cost
  // is bounded by the click itself. The tag scopes the lock to this component,
  // so a future session player holding its own lock is unaffected.
  //
  // Deliberately NOT solved with background audio. Keeping the screen on means
  // the beat dots, the tempo readout and the session clock stay visible, which
  // is the point of a drill screen; background playback would trade all of that
  // away and leave a click running after the user has left the app.
  useEffect(() => {
    if (!running) return;
    activateKeepAwakeAsync(KEEP_AWAKE_TAG).catch(() => {
      // Same discipline as the audio mode above: a device that refuses the
      // lock gets a screen that dims, not a drill screen that crashes.
    });
    return () => {
      deactivateKeepAwake(KEEP_AWAKE_TAG).catch(() => {});
    };
  }, [running]);

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

  // Timing diagnostics — hidden by default, revealed by a long press on the
  // tempo readout.
  //
  // This exists because the Done-when ("the click holds tempo on physical iOS
  // and Android hardware over a full session-length run") is measured by a
  // human on a device, and until now the only instrument was a second
  // metronome app and an ear. That cannot tell an accumulating grid from
  // per-beat jitter from the OS having suspended the app, which are three
  // different bugs with three different fixes — and it leaves no number behind
  // for whoever has to fix it. The engine has emitted the evidence all along
  // (driftStats.ts); this is the display.
  //
  // Not shown by default, because a practicing guitarist wants a tempo, not
  // telemetry. Long press rather than tap, so it cannot be opened by someone
  // reaching for the transport mid-drill.
  const [showTiming, setShowTiming] = useState(false);
  // Read during render, not subscribed to: the component already re-renders on
  // every beat off `beat`, so this costs nothing and is never a beat stale.
  const stats = showTiming ? readDriftStats() : null;

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

        <Pressable
          style={styles.readout}
          onLongPress={() => setShowTiming((shown) => !shown)}
          accessibilityRole="button"
          accessibilityLabel={formatBpmLabel(activeBpm)}
          accessibilityHint={TIMING_TOGGLE_HINT}
        >
          <Text style={styles.bpmLabel}>{formatBpmLabel(activeBpm)}</Text>
          <Text style={styles.bpmSource}>From this drill</Text>
        </Pressable>
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

      {stats !== null && (
        <View style={styles.timing}>
          <Text style={styles.timingLine} selectable>
            {formatDriftSummary(stats)}
          </Text>
          {stats.beatsEmitted > 0 && (
            <Text style={styles.timingLine} selectable>
              {formatJitterShare(maxDriftAsBeatFraction(stats, activeBpm))}
            </Text>
          )}
        </View>
      )}
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
  timing: {
    marginTop: 12,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: '#333',
    gap: 3,
  },
  timingLine: {
    // Monospace so the digits do not jump sideways as they tick over — the
    // tester is watching this line for 45 minutes. RN has no cross-platform
    // monospace alias: 'monospace' is Android-only and 'Courier' is iOS-only,
    // and naming the wrong one silently falls back to the system sans.
    fontFamily: Platform.select({ ios: 'Courier', default: 'monospace' }),
    fontSize: 10,
    color: '#8A8A8A',
  },
});
