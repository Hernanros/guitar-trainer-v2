---
type: decision
task: FLE-122
created: 2026-10-05
status: delivered
author: Doro (owns tab_verification.py — FLE-106/FLE-117)
---

# verify() gating policy: V2-third and T11 stay off the gate

**Doro, 2026-10-05.** From FLE-107 §6c
(`.planning/investigations/261005-fle107-tab-regression-set.md`), addressed to
me by name. This is the second time this shape of warning has been filed —
FLE-103 §6 already found T7 cross-song template reuse to be a 100%
false-positive check on correct data, which is why it stays a human prompt
and was never built into `app/ai/tab_verification.py:verify()` at all.

## The decision

**Do not promote V2-third ("chord stab" — root + third + characteristic tone
present somewhere in a chord bar) or T11 ("chord never sounded whole" — those
three together in one column) to unconditional blocking checks in `verify()`.**

This required no code fix, because neither check exists in `verify()` today —
I read the file to confirm it. The five hard blocks are METER, UNPLAYABLE,
FRET_SPAN, SPLIT_CHORD, OWN_CHORDS; the three warnings are UNIFORM_DURATION,
DOUBLE_SWING, RENDERER_WIDTH. There is no V2 or T11 check to demote. The work
here is to record the decision where the next person touching this file will
see it before adding one, since FLE-126 (point chord-identity checking at
drill snippets) and FLE-127 (a new pitch check) are both open against the
same file and are exactly the kind of change that tempts someone to reach for
V2/T11 next.

## Why

Both checks are correct only on chord-stab bars, and the live schema cannot
tell a chord-stab bar from a riff or an arpeggio bar:

- **A boogie has no third at all.** The 1-5-6-♭7 figure is the entire content
  of eight bars of *Sweet Home Chicago* and eight of *Pride and Joy*. A third
  in it would be the defect, not the fix. V2 run unconditionally fails 16
  correct bars.
- **An arpeggio sounds one note per column.** A per-beat third check fails
  every correct bar of *House of the Rising Sun* — 8 for 8 — while the bar as
  a whole spells its chord perfectly.

Fret's regression ground truth (`server/data/regression_tabs/regression_set.json`,
scored by `server/scripts/fle107_score_tab.py`) carries three fields per bar
to handle this: `kind` (`chord` / `riff` / `arpeggio` / `melody`),
`chord_checked` (false on riff bars) and `chord_spelled_over` (`beat` vs
`bar`). `fle107_score_tab.py:622` only calls `check_chord` — the function
that implements both V2 and T11 — when `chord_checked` is true, and it is a
hand-authored field only Fret's offline ground truth has.

I checked `app/models/song.py`: `Measure` and `Beat` carry neither `kind`,
nor `chord_checked`, nor `chord_spelled_over`. The live generator emits no
classifier a write-time gate could use to tell these bar shapes apart. That
is the structural reason this is a standing policy and not a one-off
judgement call — there is nothing in `verify()`'s input to gate on, so
porting either check unconditionally is not a tuning problem, it is missing
a precondition. The fix, if one is ever wanted, is upstream: the generator
(or its schema) would need to emit an equivalent per-bar classifier first.

T11 carries a second, independent limit even inside the chord-checked case:
it fires only on the consequential split — no single column holds root +
third + characteristic tone — and must **not** fire on a split that leaves a
complete triad in one half, because a multi-beat voicing is a legitimate
arrangement choice. `fle107_score_tab.py --mutation-test` row 10 is the form
it covers; FLE-107 §6c names the form it does not. Partial coverage by
design, not a gap to close.

## What changed

- `server/app/ai/tab_verification.py` — added a paragraph to the module
  docstring, in the same place and the same voice as the existing T7/C7
  warning, naming V2-third and T11 explicitly, citing FLE-107 §6c, and
  stating the missing-classifier reason so the next PR against this file
  carries the warning inline rather than requiring a search through old
  issues.
- No change to `verify()` itself — nothing in it implements V2/T11, so there
  was nothing to remove or gate.

## For FLE-126 / FLE-127

Both are open against the same file and both extend `verify()`'s coverage
(drill `tab_snippet`s; a pitch-degeneracy check). Neither asks for V2/T11
specifically, but if either implementation reaches for a chord-identity check
beyond `OWN_CHORDS`'s existing foreign-pitch model, this decision is the
reason to stop and ask rather than copy `check_chord` in. `OWN_CHORDS` itself
is safe as-is: it checks notes against the union of a breakdown's *own*
declared chords, with no per-bar completeness requirement, so it does not
need the `kind` classifier that V2/T11 do.
