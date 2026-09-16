// mobile/src/metronome/useMetronome.ts
// React binding for MetronomeEngine — FLE-5 (Task 7).
//
// The engine is framework-free on purpose; this hook is the only file in the
// module that knows React exists. It owns engine lifetime, mirrors a `bpm` prop
// into the engine (which is how a drill tempo ladder drives the click without
// the user typing a number), and republishes each beat as state so the UI can
// pulse in time.

import { useCallback, useEffect, useRef, useState } from 'react';
import { MetronomeEngine } from './MetronomeEngine';
import { silentClickEmitter, withBeatListener } from './emitters';
import { clampBpm, DEFAULT_BEATS_PER_BAR } from './scheduler';
import { emptyDriftStats, observeBeat, type DriftStats } from './driftStats';
import type { ClickEmitter, MetronomeBeat } from './types';

export interface UseMetronomeOptions {
  /** Tempo. Changing this while running re-anchors the grid — see MetronomeEngine.setBpm. */
  bpm: number;
  beatsPerBar?: number;
  /**
   * Factory for the sound backend. Called ONCE, on first render — the engine
   * holds the emitter for its lifetime, so changing this later has no effect.
   * Defaults to the silent emitter; MetronomeControl passes the expo-audio one
   * (see audioEmitter.ts). Keeping this a parameter rather than an import is
   * what keeps the hook free of any native module.
   */
  createEmitter?: () => ClickEmitter;
}

export interface UseMetronomeResult {
  running: boolean;
  /** The clamped tempo actually in use, which may differ from the requested bpm. */
  bpm: number;
  /** The most recent beat, or null while stopped. Identity changes every beat, so it drives animations. */
  beat: MetronomeBeat | null;
  start: () => void;
  stop: () => void;
  toggle: () => void;
  /**
   * Timing telemetry for the current run — read, not subscribed to.
   *
   * A getter rather than state on purpose. Stats change on every beat, and
   * publishing them as state would add a second render per beat on a thread
   * that owes the next beat a deadline (the same reasoning that turned
   * expo-audio's `updateInterval` down to 60s — see D7). The component already
   * re-renders every beat off `beat`, so calling this during that render is
   * free and always current.
   *
   * Reset by start(); retained through stop(), because the device tester reads
   * the run's result off a stopped screen.
   */
  readDriftStats: () => DriftStats;
}

export function useMetronome({
  bpm,
  beatsPerBar = DEFAULT_BEATS_PER_BAR,
  createEmitter,
}: UseMetronomeOptions): UseMetronomeResult {
  const [running, setRunning] = useState(false);
  const [beat, setBeat] = useState<MetronomeBeat | null>(null);

  const statsRef = useRef<DriftStats>(emptyDriftStats());

  const engineRef = useRef<MetronomeEngine | null>(null);
  if (engineRef.current === null) {
    // setBeat is a stable useState setter and statsRef is stable, so this
    // listener never needs rebinding.
    const base = createEmitter ? createEmitter() : silentClickEmitter;
    engineRef.current = new MetronomeEngine(
      { bpm, beatsPerBar },
      {
        emitter: withBeatListener(base, (nextBeat) => {
          // Fold BEFORE publishing the beat: setBeat is what triggers the
          // re-render that reads these stats, so the ref has to be current by
          // the time React gets there or the readout trails a beat behind.
          statsRef.current = observeBeat(statsRef.current, nextBeat);
          setBeat(nextBeat);
        }),
      },
    );
  }

  // Tempo ladder → click. This is the whole "no typing a number" requirement:
  // the drill screen owns currentBpm, passes it down, and the engine follows.
  useEffect(() => {
    engineRef.current?.setBpm(bpm);
  }, [bpm]);

  useEffect(() => {
    engineRef.current?.setBeatsPerBar(beatsPerBar);
  }, [beatsPerBar]);

  // Tear down on unmount — a running timer that outlives the screen would keep
  // clicking over whatever the user navigated to.
  useEffect(() => {
    return () => {
      engineRef.current?.dispose();
      engineRef.current = null;
    };
  }, []);

  const start = useCallback(() => {
    // Each run is its own measurement. Carrying the previous run's jitter into
    // a fresh one would make a 45-minute walk unmeasurable after a single
    // practice tap.
    statsRef.current = emptyDriftStats();
    engineRef.current?.start();
    setRunning(true);
  }, []);

  const stop = useCallback(() => {
    engineRef.current?.stop();
    setRunning(false);
    setBeat(null);
  }, []);

  const toggle = useCallback(() => {
    if (engineRef.current?.isRunning()) {
      stop();
    } else {
      start();
    }
  }, [start, stop]);

  const readDriftStats = useCallback(() => statsRef.current, []);

  return { running, bpm: clampBpm(bpm), beat, start, stop, toggle, readDriftStats };
}
