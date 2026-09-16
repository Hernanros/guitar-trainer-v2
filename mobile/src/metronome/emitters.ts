// mobile/src/metronome/emitters.ts
// ClickEmitter implementations — FLE-5 (Task 7).
//
// ---------------------------------------------------------------------------
// The audible emitter is NOT in this file, and that is deliberate
// ---------------------------------------------------------------------------
//
// It lives in audioEmitter.ts, built over expo-audio (approved on FLE-5 — RN
// core ships no audio-playback API and Web Audio does not exist on native, so
// there was no in-tree path to a sound). Keeping it in its own file is what
// lets this one stay import-free: the engine, the scheduler, the hook and the
// drill screen all depend on ClickEmitter, never on a player, so they test with
// no native module in the picture.
//
// What is here is the interface's non-audio implementations: a silent default,
// a recorder for the drift tests, and a listener wrapper for the UI pulse.
//
// (For the record, the rejected zero-dependency substitute was RN core's
// Vibration API: iOS ignores the duration argument and always buzzes ~400ms,
// which at 120 BPM — 500ms/beat — is near-continuous. Worse than silence.)

import type { ClickEmitter, MetronomeBeat } from './types';

/**
 * Correct timing, no sound — the default when no `createEmitter` is supplied.
 *
 * Not dead code: it is what `useMetronome` falls back to, so the engine and the
 * visual beat indicator can be exercised (in tests, or by any future caller
 * that wants the grid without the click) without constructing native players.
 * The drill screen passes the audio emitter explicitly.
 */
export const silentClickEmitter: ClickEmitter = {
  emit() {
    // Intentionally empty. See the file header.
  },
};

/** A recording emitter plus the beats it has captured. */
export interface RecordingEmitter extends ClickEmitter {
  beats: MetronomeBeat[];
  clear(): void;
}

/**
 * Captures every beat for assertions. Used by the drift tests to replay a
 * session-length run and check the grid afterwards.
 */
export function createRecordingEmitter(): RecordingEmitter {
  const beats: MetronomeBeat[] = [];
  return {
    beats,
    emit(beat: MetronomeBeat) {
      beats.push(beat);
    },
    clear() {
      beats.length = 0;
    },
  };
}

/**
 * Wraps an emitter with a callback — lets the UI observe beats (for a flashing
 * indicator) without replacing whatever emitter is actually making sound.
 */
export function withBeatListener(
  inner: ClickEmitter,
  listener: (beat: MetronomeBeat) => void,
): ClickEmitter {
  return {
    emit(beat: MetronomeBeat) {
      inner.emit(beat);
      listener(beat);
    },
    dispose() {
      inner.dispose?.();
    },
  };
}
