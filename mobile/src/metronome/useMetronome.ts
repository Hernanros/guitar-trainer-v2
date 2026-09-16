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
import type { ClickEmitter, MetronomeBeat } from './types';

export interface UseMetronomeOptions {
  /** Tempo. Changing this while running re-anchors the grid — see MetronomeEngine.setBpm. */
  bpm: number;
  beatsPerBar?: number;
  /**
   * Factory for the sound backend. Called ONCE, on first render — the engine
   * holds the emitter for its lifetime, so changing this later has no effect.
   * Defaults to the silent emitter, which is all that exists until expo-audio
   * is approved (see emitters.ts).
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
}

export function useMetronome({
  bpm,
  beatsPerBar = DEFAULT_BEATS_PER_BAR,
  createEmitter,
}: UseMetronomeOptions): UseMetronomeResult {
  const [running, setRunning] = useState(false);
  const [beat, setBeat] = useState<MetronomeBeat | null>(null);

  const engineRef = useRef<MetronomeEngine | null>(null);
  if (engineRef.current === null) {
    // setBeat is a stable useState setter, so this listener never needs rebinding.
    const base = createEmitter ? createEmitter() : silentClickEmitter;
    engineRef.current = new MetronomeEngine(
      { bpm, beatsPerBar },
      { emitter: withBeatListener(base, setBeat) },
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

  return { running, bpm: clampBpm(bpm), beat, start, stop, toggle };
}
