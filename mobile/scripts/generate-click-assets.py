#!/usr/bin/env python3
"""Generate the metronome click assets — FLE-5 (Task 7).

Writes `assets/audio/tick.wav` (offbeat) and `assets/audio/accent.wav` (downbeat).

Why a generator instead of two checked-in mystery blobs: the click is a tuning
decision, not a found artifact. Pitch, length and level are the things you argue
about after hearing it against a real guitar, so they live here as named
constants you can turn and re-run. Committing the .wav files too means the app
never depends on Python at build time — this script is the source, the wavs are
the build output, and both are in the tree.

    python3 scripts/generate-click-assets.py

Standard library only (`wave`, `struct`, `math`) — no pip install, no package
added to the repo.

Design notes:
- Decaying sine, not a noise burst. A metronome has to stay audible next to a
  guitar without masking it; a narrow band around 1 kHz sits above the guitar's
  fundamentals and below its brightest harmonics.
- The accent is a higher pitch AND hotter, because pitch alone is easy to lose
  when the phone is in a pocket or on a couch.
- ~35 ms long. Long enough to read as a tone, short enough that at 300 BPM
  (200 ms/beat) consecutive clicks never overlap.
- A 2 ms raised-cosine attack and a decay that reaches zero before the buffer
  ends, so neither edge produces the DC step that would add a click to the click.
"""

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 44100
BIT_DEPTH = 16
DURATION_S = 0.035
ATTACK_S = 0.002

# (filename, frequency Hz, peak amplitude 0..1)
CLICKS = (
    ("tick.wav", 1000.0, 0.55),
    ("accent.wav", 1600.0, 0.85),
)

OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "audio"


def envelope(t: float) -> float:
    """Raised-cosine attack into an exponential decay that lands on zero."""
    if t < ATTACK_S:
        return 0.5 * (1.0 - math.cos(math.pi * t / ATTACK_S))
    progress = (t - ATTACK_S) / (DURATION_S - ATTACK_S)
    # exp decay, then subtract the tail so the final sample is exactly silent
    tail = math.exp(-6.0)
    return (math.exp(-6.0 * progress) - tail) / (1.0 - tail)


def render(frequency: float, amplitude: float) -> bytes:
    frame_count = int(SAMPLE_RATE * DURATION_S)
    full_scale = 2 ** (BIT_DEPTH - 1) - 1
    frames = bytearray()
    for n in range(frame_count):
        t = n / SAMPLE_RATE
        sample = amplitude * envelope(t) * math.sin(2.0 * math.pi * frequency * t)
        frames += struct.pack("<h", int(round(sample * full_scale)))
    return bytes(frames)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, frequency, amplitude in CLICKS:
        path = OUT_DIR / name
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(BIT_DEPTH // 8)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(render(frequency, amplitude))
        print(f"wrote {path.relative_to(OUT_DIR.parent.parent)} "
              f"({path.stat().st_size} bytes, {frequency:.0f} Hz)")


if __name__ == "__main__":
    main()
