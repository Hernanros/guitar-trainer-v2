---
type: decision
task: FLE-127
created: 2026-10-05
status: delivered
author: Doro (owns tab_verification.py — FLE-106/FLE-117/FLE-122/FLE-126)
---

# PITCH_DEGENERACY lands as a warning, not a block

**Doro, 2026-10-05.** Dispatched by the FLE-81 idle watchdog triage as the
third item in my verifier queue against `tab_verification.py`, same file as
FLE-122 and FLE-126. Split out of FLE-109 after the parent verified defect #3
against prod: song 107 (*Eruption*), m1 is `1:0` eight times — eight strikes
of the open high E, not in the record, not a practisable bar — and passes all
five hard blocks plus C4 (UNIFORM_DURATION), which also fires on 7 of the
other 8 bars (legitimate straight-eighth tapping and pentatonic runs) and so
cannot discriminate the filler bar from the real ones.

## The decision

**Add `PITCH_DEGENERACY`: a measure whose every sounding note is the same
pitch. Ship it as a warning, never a block.**

The issue text proposed this disposition itself ("worth a warning rather
than a block — call it with whoever owns the standard") and left the call to
whoever owns this file, which is me (FLE-106/FLE-117/FLE-122/FLE-126). I'm
making it the same way FLE-122 made the V2/T11 call: in the open, in this
doc, rather than silently.

## Why warning, not block

The check's exactness is also its blind spot: distinct-pitch count == 1 is
cheap and binary, but it cannot tell apart two bars that look identical on
the wire —

- **Eruption m1**: eight strikes of one note because nothing else was
  generated — filler occupying a measure slot a player reads as real tab.
- **A deliberate pedal tone or tremolo bar**: eight strikes of one note
  because that repetition IS the musical content — e.g. a tremolo picking
  drill, or a pedal tone under a held chord elsewhere in the arrangement.

Both are `len(set(midis)) == 1`. Nothing in the live schema (`Measure`,
`Beat`, `Note` — checked against `app/models/song.py`) carries a classifier
that would tell them apart, the same structural gap FLE-122 found for
V2/T11's `kind`/`chord_checked` fields. Blocking unconditionally risks
exactly the failure mode FLE-103 §6 already measured once for a
plausible-looking check (C7 cross-song template reuse: 100% false-positive
rate on hand-verified correct data) — refusing a correct, if stylistically
rare, breakdown outright, with no recording-based signal yet to say how often
that would happen in practice.

Orthogonality to C4 is the reason this check earns its place despite that
same-shape caveat: C4 is a RHYTHM property (one note LENGTH across the bar)
and already fires near-indiscriminately on this song (7/8 bars). PITCH_
DEGENERACY is a PITCH property (one pitch CLASS across the bar) and, on this
tab, fires on m1 alone — selective where C4 is not. That selectivity is worth
surfacing to a human (or a future recording-based pass) even before its
false-positive rate against the pedal/tremolo case is measured, same
reasoning C4 and C8 already ship under.

## What changed

- `server/app/ai/tab_verification.py`:
  - `_check_pitch_degeneracy` — collects every sounding note's midi pitch
    per measure (excluding bent notes, same exclusion `_check_foreign_notes`
    uses, since a bend's sounding pitch isn't the fingered fret and this
    module has no target-pitch field to score it at instead), and warns when
    more than one note sounds and all of them share one pitch.
  - `verify()` — added to the warnings list, scoped to the main tab only
    (not drill `tab_snippet`s — a single sustained or pedalled pitch is
    often the entire teaching point of a drill, same scope as C4/C8/
    RENDERER_WIDTH).
  - Module docstring — added PITCH_DEGENERACY to the informational-checks
    list alongside C4/C8/RENDERER_WIDTH, pointing here for the disposition
    call.
- `server/tests/test_tab_verification.py` — three new cases: the Eruption
  shape (warns, does not block), a varied-pitch straight-eighths run (trips
  C4, must NOT trip PITCH_DEGENERACY — the selectivity claim from the issue),
  and a single-note bar (nothing repeated, must not fire).

## For the next check against this file

If a future defect needs to distinguish a *filler* repeated-pitch bar from a
*deliberate* pedal/tremolo one, that needs the same shape of fix FLE-122
named for V2/T11: an upstream per-bar classifier (`kind`, or a narrower
`is_pedal`/`is_tremolo` flag) the generator emits and `verify()` reads, not a
tighter heuristic bolted onto the pitch-count alone.
