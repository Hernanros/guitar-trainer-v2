# 260915-ksk — Metronome wired to drill tempo ladders

**Board issue:** FLE-5 (Task 7) · **Owner:** Kaya (Mobile Engineer) · **Base:** `9aa7dc3`
**Mode:** GSD quick (gsd-sdk CLI unavailable in this environment — workflow followed manually:
`.planning/quick/` artifacts, atomic commits, STATE.md quick-task row)

## Task

A tempo ladder with no click is a number on a card. Ship an accurate, drift-free metronome and
wire it to the drill tempo ladder so starting a drill sets the tempo with no manual entry.

## Scope decisions (locked before writing code)

### D1 — No audio package is installed, and that is a blocker for *sound only*

Verified against `mobile/package.json`: there is no `expo-audio`, no `expo-av`, no `expo-haptics`.
React Native core exposes no audio-playback API, and Web Audio does not exist on native (only
through `react-native-web`). So an audible click has genuinely no in-tree alternative.

FLE-5 constrains new packages ("justify it to Miagi first"). Therefore this plan **adds no
dependency**. It builds everything that does not depend on that decision and puts the audio
backend behind a one-method interface so approving `expo-audio` later is a single new file, not a
rewrite.

Escalation is filed on the issue thread. Verified from the SDK 57 docs
(<https://docs.expo.dev/versions/v57.0.0/sdk/audio/>): package is `expo-audio`, installed with
`npx expo install expo-audio`; `useAudioPlayer` / `createAudioPlayer` / `player.seekTo(0)` +
`player.play()` is the replay path; a config plugin is only needed for background audio or
recording. Note the docs' "no native rebuild" line refers to config-plugin settings — this repo has
a committed `mobile/ios/` prebuild with Pods, so any new native module still needs a fresh native
binary. That cost rides along with the already-queued EAS batch rather than adding a build.

### D2 — "Drift-free" means no *accumulated* error, and the scheduler must say so honestly

`setInterval(60000 / bpm)` accumulates error: every late callback pushes the next one later, so a
45-minute run ends measurably behind the grid. The fix is absolute-deadline scheduling — every beat
deadline is computed as `anchor + n × interval` from a fixed anchor, and each timer is armed for
`deadline − now`. A late callback then *shortens* the next timeout instead of delaying it, so error
is corrected rather than compounded and total drift stays bounded by one beat's jitter regardless
of run length.

What this does **not** buy: per-beat jitter. A JS-timer metronome is at the mercy of the JS thread,
so individual beats land a few ms early or late even when the grid is perfect. Sample-accurate
scheduling would need a native audio-clock API that no in-tree package provides. Per-beat jitter is
exactly what the physical-device check in the Done-when measures; the grid correctness is what the
tests here prove.

### D3 — Overdue beats resync, they do not machine-gun

If the JS thread stalls (GC, navigation, a slow render), several beat deadlines can pass at once.
Firing all of them back-to-back produces a burst of clicks — worse than a dropped beat. The engine
skips overdue beats and resumes at the next future deadline, preserving `beatIndex` parity so bar
accents stay on the downbeat.

### D4 — The session player does not exist yet

Verified: `mobile/src/app/` has onboarding, tabs, breakdown and the drill screen. There is no
session player, no session clock, and no planning doc that defines either. The third scope bullet
("usable inside the session player alongside the session clock") has nothing to integrate with.
This plan builds the metronome as a self-contained module whose only coupling is a BPM number, so
the session player becomes a drop-in consumer. The bullet is reported unmet, not faked.

### D5 — Bar accent in, subdivisions out

The downbeat accent needs `beatsPerBar` and is load-bearing for a usable click, so it is in.
Subdivisions (REQUIREMENTS TOOL-01) are Phase 5 Toolkit scope and are not needed to drive a tempo
ladder — left out, but the beat payload carries enough for a subdivision layer to be added later
without reshaping the API.

## Tasks

- **T1** — `src/metronome/types.ts`: `MetronomeBeat`, `ClickEmitter`, `MetronomeDeps`.
- **T2** — `src/metronome/scheduler.ts`: pure timing math — `intervalMsForBpm`, `beatDeadline`,
  `nextBeatFrom` (the resync rule from D3), `isDownbeat`, `clampBpm`. No timers, no state.
- **T3** — `src/metronome/MetronomeEngine.ts`: imperative engine over injected `now` / `setTimer` /
  `clearTimer` / `emitter`. `start`, `stop`, `setBpm` (re-anchors so a tempo change starts a clean
  bar), `getState`.
- **T4** — `src/metronome/emitters.ts`: `silentClickEmitter` (default, no-op) and
  `createRecordingEmitter()` for tests. Audio emitter deliberately absent pending D1.
- **T5** — `src/metronome/useMetronome.ts`: React hook — owns engine lifecycle, mirrors BPM prop
  changes into `setBpm`, tears down on unmount.
- **T6** — `src/components/MetronomeControl.tsx`: play/pause + BPM readout, Fletcher-voiced,
  44px tap target, matching the existing dark palette (`#1A1A1A` / `#242424` / `#E07B39`).
- **T7** — Wire into `src/app/breakdown/[songId]/drill/[drillIndex].tsx`: the ladder's
  `currentBpm` drives the click; no number entry anywhere.
- **T8** — Tests in `__tests__/metronome/`: pure-fn coverage plus a virtual-clock session-length
  run (45 min @ 120 BPM = 5400 beats) under deterministic jitter, asserting zero accumulated
  drift. Contrast test proves naive `setInterval` accumulation would fail the same assertion.

### D6 — Everything downstream of the package decision, built before the decision (added 260916)

The `expo-audio` confirmation is open on FLE-5 and only Miagi can close it. Waiting idle on it would
leave three things to author *after* approval: the sound files, the adapter, and its tests — all of
which would land untested into an EAS batch build, where a bad click costs a whole build slot to
discover.

None of them actually need the package:

- **The sounds are not a dependency.** `scripts/generate-click-assets.py` renders `assets/audio/tick.wav`
  (1000 Hz) and `accent.wav` (1600 Hz, hotter) — 35 ms decaying sines, 44.1 kHz/16-bit mono PCM,
  Python standard library only, nothing installed. A generator rather than two checked-in blobs
  because pitch/level/length are the things you argue about after hearing it against a real guitar.
- **The adapter is not a dependency either, if the module is injected.** `src/metronome/audioEmitter.ts`
  takes the expo-audio module as an argument — the same discipline the engine already uses for
  `now`/`setTimer`/`clearTimer`. So it typechecks and unit-tests today against a fake player.

What that buys: the parts that are easy to get wrong are covered now rather than discovered on
hardware — voice pooling (`seekTo(0)` is async, so a single player makes every click race the
previous click's rewind), swallowing `late` beats instead of clicking out of position, accent
routing, idempotent teardown, and surviving a native throw without ending the session. Plus
`playsInSilentMode: true`, the gotcha that otherwise ships a metronome that is running, correct and
completely inaudible on a phone left on the silent switch.

**If the answer is "ship it silent",** none of this is installed or shipped — it is inert TypeScript
and 6 KB of wav, and the `dependency guard` test still passes. **If the answer is approve,** the
remaining change is `npx expo install expo-audio` plus a `createEmitter` argument at the
MetronomeControl call site.

- **T9** — `scripts/generate-click-assets.py` + generated `assets/audio/{tick,accent}.wav`.
- **T10** — `src/metronome/audioEmitter.ts`: injected-module audio emitter, voice pool, late-beat
  suppression, `prepareClickAudioMode`.
- **T11** — `__tests__/metronome/audioEmitter.test.ts`: adapter behaviour against a fake player, plus
  RIFF-level assertions on the committed wavs (PCM/44.1k/16-bit/mono, <200 ms so clicks never
  overlap at the 300 BPM ceiling, first and last sample exactly zero so the click has no pop).

## Verification

- `npx jest __tests__/metronome` green, including the 5400-beat drift assertion.
  (260916: 67 tests green across both files, re-run on `main` after FLE-29 landed on top.)
- `npx tsc --noEmit` clean.
  (260916: the only error reported repo-wide is a pre-existing expo-router route-type mismatch in
  `src/components/app-tabs.web.tsx`, an untouched file. No metronome file appears in the output.)
- Existing drill tests still green (the screen is edited).
- **Not verifiable here:** physical iOS/Android hold-tempo run. Needs the EAS batch + a human on
  provisioned hardware. Reported as outstanding against the Done-when.
