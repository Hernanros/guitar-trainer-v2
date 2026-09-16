// mobile/src/metronome/emitters.ts
// ClickEmitter implementations — FLE-5 (Task 7).
//
// ---------------------------------------------------------------------------
// There is no audio emitter here, and that is deliberate
// ---------------------------------------------------------------------------
//
// mobile/package.json contains no audio dependency: no expo-audio, no expo-av,
// no expo-haptics. React Native core ships no audio-playback API, and Web Audio
// does not exist on native. So an AUDIBLE click genuinely cannot be built from
// what is in the tree — it needs a new package, and FLE-5 requires that be
// justified to Miagi before it lands. That justification is raised as a
// confirmation on FLE-5; until it is answered, nothing is installed.
//
// RN core's Vibration API was considered as a zero-dependency substitute and
// rejected: iOS ignores the duration argument and always buzzes ~400ms, which at
// 120 BPM (500ms/beat) is very nearly continuous. It would be a worse artifact
// than silence, not a stopgap.
//
// What ships instead is the interface plus a silent default, so the timing
// engine is complete, tested, and wired to the drill screen today. Adding sound
// later means adding ONE file next to this one:
//
//   // emitters.audio.ts  — after `npx expo install expo-audio`
//   import { createAudioPlayer, setAudioModeAsync } from 'expo-audio';
//   export function createAudioClickEmitter(): ClickEmitter {
//     const tick = createAudioPlayer(require('../../assets/audio/tick.wav'));
//     const accent = createAudioPlayer(require('../../assets/audio/accent.wav'));
//     return {
//       emit(beat) {
//         if (beat.late) return;           // swallow, don't click out of place
//         const p = beat.downbeat ? accent : tick;
//         p.seekTo(0);                     // replay from the top
//         p.play();
//       },
//       dispose() { tick.remove(); accent.remove(); },
//     };
//   }
//
// No other file in the module changes. The engine, the scheduler, the hook and
// the drill screen are all written against ClickEmitter, not against a player.

import type { ClickEmitter, MetronomeBeat } from './types';

/**
 * The shipped default: correct timing, no sound.
 *
 * This is not a stub in the pejorative sense — the drill screen uses it today
 * to drive the visual beat indicator, which is real feedback. It is simply
 * missing the audio half, pending the dependency decision.
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
