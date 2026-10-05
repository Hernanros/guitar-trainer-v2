---
type: investigation
task: FLE-107
created: 2026-10-05
status: delivered
author: Fret (Guitar Curriculum Lead)
---

# The tab regression set — accuracy as a diff

**Fret, 2026-10-05.** Priority 4 on my brief, from the FLE-102 audit. The issue I filed said this
was blocked on the schema. It was. FLE-115 (`song.py` v2 — triplet durations, `Bend`, `feel`,
`link_to_next`, rests, `capo`, techniques and effects) and FLE-116 (renderer: width from content,
`b¼` glyphs, feel badge) are both merged, so the objection no longer holds and this is built.

Artefacts:

- `server/data/regression_tabs/regression_set.json` — the committed ground truth, 10 songs, 80 bars.
- `server/scripts/fle107_build_regression_set.py` — builds it. Hand-written; no LLM, no DB, no spend.
- `server/scripts/fle107_score_tab.py` — scores a candidate against it. `--self-test`,
  `--mutation-test`, `--emitted FILE`, `--from-db` (read-only SELECT).

```
server/.venv/bin/python server/scripts/fle107_build_regression_set.py
server/.venv/bin/python server/scripts/fle107_score_tab.py --self-test       # 48/48, 30/30
server/.venv/bin/python server/scripts/fle107_score_tab.py --mutation-test   # 12/12
```

---

## 1. Headline

| | |
|---|---|
| Songs / bars committed | **10 / 80** — 48 `note_exact`, 32 `constraint` |
| Self-test (ground truth vs itself) | **48/48 bars, 30/30 setup fields**, 6 of 6 note-bearing tabs parse as real v2 `Tab`s |
| Mutation test | **12/12** breakages caught *by the check that was supposed to catch them* |
| Baseline — cached prod, note_exact | **0 / 16** |
| Baseline — cached prod, constraint | **8 / 32** |
| Baseline — cached prod, setup | **30 / 34** |
| Baseline — cached prod, tab (conjunctive) | **0 / 10** |

FLE-102's "0 of 10 fit to practise from" was my judgement. It is now a reproducible number with a
denominator, and the two agree. **The scorer re-derived four of FLE-102's bar-level findings from
scratch, having never been told them** — see §4. That is the only evidence I have that the method
measures what I claimed it measures.

---

## 2. The ten, and what each one guards

Every song the issue asked for is covered, and seven of the ten are songs FLE-102 audited — on
purpose, so the audit's verdicts become regressions instead of history.

| # | song | tuning / capo | meter / feel | the mode it guards | basis |
|---|---|---|---|---|---|
| 1 | Sweet Home Chicago | standard | 4/4 **shuffle** | feel marked not notated; T10 | note_exact |
| 2 | Pride and Joy | **Eb** | 4/4 **shuffle** | the 5→6→♭7 boogie FLE-102 found missing | note_exact |
| 3 | Comfortably Numb | standard | 4/4 | **bends**; T5 (C♯ not C) | constraint |
| 4 | House of the Rising Sun | standard | **6/8** | non-4/4 meter; **reverse** mode flip | note_exact |
| 5 | Kashmir | **DADGAD** | 4/4 | T6 invented riff; the drone | constraint |
| 6 | Wonderwall | standard **capo 2** | 4/4 | capo origin, `sounding_name`, drill inheritance | note_exact |
| 7 | Little Wing | **Eb** | 4/4 | T1 mode flip, T7 clusters, T11 split voicing | note_exact |
| 8 | Big Love | standard | 4/4 | **fingerpicked alternating pattern**; T1 | constraint |
| 9 | Eruption | **Eb** | 4/4 | `technique: tap`, `triplet-sixteenth`, T1 | constraint |
| 10 | Sultans of Swing | standard | 4/4 | mode flip **on the dominant**; fingerpicked | note_exact |

Shuffle ✓ (1, 2) · bend-led melody ✓ (3, 10) · fingerpicked ✓ (8, 10, 4) · minor-key mode flip ✓
(4, 7, 10) · altered tuning ✓ (2, 7, 9) · DADGAD ✓ (5). Capo (6) and tapping (9) are extra —
both are FLE-115 fields that had no regression coverage at all, and a schema field with no test is a
field the generator will quietly never use.

**Two entries are the mode flip pointing the other way, which nothing guarded before.** *House of
the Rising Sun* is in A minor and contains **D major** (F♯) and **E major** (G♯); *Sultans of Swing*
is in D minor and its fourth chord is **A major** (C♯). FLE-102's T1 was always a major third where a
minor belonged, so a generator patched to "use the natural minor scale in a minor key" would pass
every existing check and fail five of these sixteen bars.

**This is not the FLE-103 pilot set, and must not be merged with it.** The pilot set *excludes*
every song whose material is articulation, because a stranger has to be able to practise from it.
This set is *chosen* for them, because its job is to fail. Opposite requirements, as FLE-103 §6 said.

---

## 3. 48 bars of notes, 32 bars of constraints — and why I did not write 80

Every bar carries a `basis`:

- **`note_exact`** — I commit the notes; the scorer diffs them.
- **`constraint`** — I commit assertions the correct tab must satisfy, and no notes.

I will not publish a bar-by-bar transcription of Gilmour's first solo, Bonham-era Kashmir, Eruption's
tapped section, or a *Big Love* arrangement, **from memory.** I know those records as a listener, not
as charts I could defend bar by bar, and writing them down anyway is precisely the failure mode I was
hired to catch — a confident hand publishing something whose arithmetic it never did. FLE-103 is the
proof that the failure mode includes me: I wrote a D7♯11 with the ♯11 at a fret that is F, not G♯,
and the independent verifier caught it on the fourth chord of ten songs.

**The constraints are not a consolation prize.** For *Comfortably Numb*, "the passage contains no
structural C natural" **is the entire T5 defect FLE-102 measured** — five bars of C naturals turning
B Aeolian into B Phrygian. It needs no recording, it contradicts the breakdown's own chord list, and
the scorer found exactly five. For *Eruption*, "it is tapped, it is in sextuplets, it is in A minor,
it is built on pull-offs" is four assertions the cached tab failed four times over. What the
constraints cannot do is tell you the tab is *the song*, which is why they are scored and reported
separately and labelled weaker evidence in the data file itself.

Per song, `cannot_verify` names **what I would need**: for six of the ten, the record plus a
reference transcription. For *Big Love* it is not a recording at all — FLE-102 already found that the
1987 studio part and Buckingham's solo live arrangement are different guitar parts and the breakdown
names neither, so **"correct" is undefined until someone decides which arrangement Fletcher teaches.**
That is a product question and I should not settle it by picking one.

---

## 4. The scoring method

### Units, stated

- **The bar** is primary. A bar passes only if every `fail`-severity check on it passes. Denominators
  are always printed.
- **The tab** is secondary and **conjunctive**: setup correct *and* every bar passes. Not an average
  — a tab with one wrong bar is not fit for a stranger to practise from, which is the question
  FLE-89's bar actually asks.
- **Setup** gets its own table, per (song × field) over tuning / capo / time_signature / feel / key.

### Three scores, never blended

FLE-102's central finding was that **62% mechanical validity and 0% musical correctness were both
true of the same corpus**, and that a gate built on the first would have passed Kashmir — a tab that
cleared 8 of 8 machine checks and had nothing to do with the song. Blending a note-level diff with a
constraint check rebuilds that confusion inside one percentage, so the scorer refuses to:

```
setup       (song x field)
note_exact  bars where I committed notes      — a real diff
constraint  bars where I committed assertions — "nothing disqualifying was found"
```

Two severities. `fail` counts; `flag` is reported and never counted, and exists for checks a *correct*
tab could plausibly trip — a chromatic passing tone, eight bars that legitimately contain no bend, a
sextuplet run that is legitimately uniform. A flag is a prompt for an ear, which is the only thing
that can settle it.

### Tolerance for arrangement differences — 15 numbered rules

Committed in `regression_set.json` under `scoring.tolerances`, so a disagreement cites a rule instead
of an opinion. The load-bearing ones:

| id | rule | verdict |
|---|---|---|
| A-1 | same pitches, different string/fret | PASS, noted. A different **inversion** is FAIL |
| A-3 | the fifth omitted from a 4+-note chord | PASS. The **third** omitted is always FAIL — that is T2 |
| A-6 | extra notes | PASS where the bar says `superset_ok`, FAIL where it says `exact`. The two also differ in granularity: `exact` is column-by-column with the column count checked, `superset_ok` is scored over the whole bar, because a correct arpeggiation of the same harmony spreads it across more columns |
| A-7 | durations on a `rhythm_scored: false` bar | UNSCORED. **The meter check is never relaxed** |
| A-9 | `feel` | `straight` where `shuffle` is specified is FAIL. `swing` and `shuffle` are not interchangeable (FLE-115 §2.5). Literal triplet-eighths **plus** `feel: shuffle` is also FAIL — the swing applied twice, which is what FLE-118 has to teach the prompt not to do |
| A-11 | capo shapes flattened to absolute frets with the right sounding pitches | **FLAG, not FAIL**, in its own column. The pitches are right and the fingering is unidiomatic — a pedagogy defect, not a pitch defect |
| A-12 | a different passage of the same song | **NOT SCORED**, reported as `passage_mismatch`. A diff against the wrong eight bars is noise |
| A-13 | a forbidden pitch class appearing only below the stated `min_duration` | FLAG. A passing tone is not T5 |
| A-15 | `key` naming the shape frame (`Em` on an Eb-tuned song) | PASS, either frame accepted — see §6 |

### The mutation test, and why it exists

A self-test only proves the ground truth is self-consistent. It cannot show the checks *bite* — a
scorer of 80 vacuous assertions passes its own self-test perfectly. So `--mutation-test` breaks the
ground truth twelve ways and **asserts which check fires**, not merely that something failed:

```
CAUGHT  Little Wing       T1: raise the Em third to G#        -> pitch_class_absent: 1 structural note on G
CAUGHT  House of Rising   D major -> Dm (F# -> F)             -> foreign pitch F against D
CAUGHT  Sultans of Swing  A major -> Am (C# -> C)             -> foreign pitch C against A
CAUGHT  Sweet Home Chi.   drop the shuffle mark               -> feel_is
CAUGHT  Sweet Home Chi.   notate the swing literally as well  -> no_literal_swing_subdivision: 24 literal triplets
CAUGHT  Wonderwall        drop the capo                       -> capo_is
CAUGHT  Wonderwall        drop sounding_name                  -> sounding_name_present
CAUGHT  House of Rising   call a 6/8 bar 4/4                  -> T10: sums to 3, 4/4 needs 4
CAUGHT  Pride and Joy     flatten the boogie to root+fifth    -> scale_degrees_present: want C/C#/Eb/Bb, got Eb/Bb
CAUGHT  Little Wing       the third never sounds with the root-> Em never sounded whole in one column (T11)
CAUGHT  Pride and Joy     standard tuning instead of Eb       -> tuning_is
CAUGHT  Sweet Home Chi.   emit 6 bars instead of 8            -> A-12 passage_mismatch
```

**Writing the `expect` column is what made this test worth running.** The first version only asked
"did anything fail", and three rows were being credited to checks that had not fired at all:

- The double-applied-swing mutation was caught by the **meter** check, because reusing 8 columns of
  triplet-eighths makes the bar 8/3 beats long. A real catch of an unreal defect — the check I cared
  about had never run. Rewritten as 12 triplet-eighths, which sums correctly, and
  `no_literal_swing_subdivision` now does the catching.
- The harness was handing each mutation **references into the loaded ground truth**, so every
  mutation leaked into the rows after it. Three verdicts were measured against an already-corrupted
  set. Now deep-copied.
- A latent crash: `names()` was being passed raw MIDI values from the note diff and blew up on
  `CHROM[57]`. The self-test never reached that line, because the self-test never produces a
  mismatch. The mutation test found it on the first row.

The honest reading: **the first version of my own scorer would have reported 12/12 while three of its
checks were untested and one of its code paths crashed.** Checks are not evidence until something has
tried to break them.

---

## 5. The baseline: what the cached prod tabs score

Read-only SELECT against prod, zero spend, no generation. Six of the ten have a cached tab.

```
SETUP (song x field)            tuning  capo  meter  feel   key
  Sweet Home Chicago              ok     ok    ok    WRONG   ok
  Pride and Joy                   ok     ok    ok    WRONG   ok
  Comfortably Numb                ok     ok    ok     ok     ok
  Kashmir                         ok     ok    ok     ok     --
  Little Wing                     ok     ok    ok     ok     ok
  Big Love                        ok     ok    ok     ok    WRONG
  Eruption                        ok     ok    ok     ok    WRONG
  SETUP SCORE: 30/34

  NOTE_EXACT  0/16      CONSTRAINT  8/32      PASSAGE  4/13      TAB  0/10
```

**30 of 34 setup fields are right and 0 of 10 tabs are.** That is FLE-89's ruled-out explanation
restated with a denominator: tuning is right 6 for 6, capo is right, the meter *declaration* is
right, and the defect is in the notes. The four setup failures are worth naming individually because
each is a different thing:

- **`feel` on both shuffles** — `straight` where `shuffle` belongs. Pure T9, and newly measurable:
  before FLE-115 there was no field to get wrong.
- **`key` on Big Love: `E`** — i.e. E *major*, for a song in E minor. T1 appearing in the key field,
  not just the notes.
- **`key` on Eruption: `E`** — the figure is in A minor. Wrong in either frame.

### The scorer re-derived FLE-102's findings without being told them

This is the part I would want checked. The scorer has no access to FLE-102's conclusions; it reads
`{string, fret, duration}` and re-derives pitch from the tuning.

| FLE-102 said | the scorer found, independently |
|---|---|
| Comfortably Numb: every C is C natural, **five bars** | `pitch_class_absent` fires on **m2, m3, m4, m5, m7 — five bars** |
| Pride and Joy **m7** is 5 beats in 4/4 | `T10: m7 sums to 5 beat(s), 4/4 needs 4` |
| Little Wing m1/m4/m7 are 3.5 beats | `T10: m1 sums to 7/2 beat(s)` |
| Little Wing's 8 bars use **all 12 pitch classes** | `max_distinct_pitch_classes: 12 distinct (C/C♯/D/Eb/E/F/F♯/G/Ab/A/Bb/B)` |
| Little Wing T11: the voicing split across beats | `Em is never sounded whole in one column (T11)` |
| Sweet Home Chicago has only **2 bars** | `A-12 passage_mismatch: candidate has 2 bar(s)` |
| Eruption's tapping written as plain eighths | `contains_technique: none` on all 8 bars; `durations used: ['eighth','quarter']` |
| Eruption's "A minor" arpeggio spells A major | `pitch_class_absent: 4 structural notes on C` (shape C♯) |
| Big Love: E alternating with G♯ in Em | `pitch_class_absent: 8 structural notes on Ab` (shape G♯) |
| Kashmir: an invented I–IV–V vamp, polyrhythm gone | `drone_present` fails 8/8; `stepwise_ascent: longest run 1 step`; `not_uniform_duration: 1 distinct duration` |

Two agreements are worth more than the rest, because they are *numerically exact and I did not aim
them*: five C-natural bars, and Pride and Joy m7 specifically. The taxonomy and the diff are
measuring the same thing.

### What the baseline cannot tell us yet, and the one spend it would take

**Only 2 of the 10 songs reach the note-exact diff at all.** *House of the Rising Sun*, *Wonderwall*
and *Sultans of Swing* have never been generated, and *Sweet Home Chicago*'s cached tab is 2 bars, so
A-12 correctly refuses to score it. The note_exact denominator is 16, not 48.

So **0/16 is a floor, not the measurement.** The set is built to measure the *next* generator run.
Closing the gap needs one breakdown generated per missing song — 4 songs, ~$0.19 each, ~**$0.80**,
plus the 3 songs whose cached tab predates FLE-115 if we want a post-schema number, so ~**$1.50**
worst case. **I am not spending it.** If Hernan wants a full 10-song note-exact baseline before
FLE-118 changes the prompt, that is the ask, and it should probably wait until FLE-118 lands so the
money buys a post-fix number instead of a second pre-fix one.

---

## 6. Four findings that are not about this set

### 6a. `QUAL` cannot spell a `7sus4` — one line, production, not mine

`app/ai/tab_verification.py`'s suffix matcher reaches `sus4` before it would reach a `7sus4` entry,
and there is no `7sus4` entry, so `A7sus4` resolves to `(0, 5, 7)` and **the ♭7 is dropped**. A
subset rather than a wrong answer, so nothing today reports a false defect — but a missing ♭7 in an
`A7sus4` would pass the live write gate silently, and Wonderwall's verse is three-quarters sus
chords. My scorer carries a local `QUAL_EXT` with `"7sus4": (0, 5, 7, 10)` and a comment pointing
here. **Spec to Ripley/Kaya:** add it to `QUAL` and delete my copy. FLE-115 collapsed three
disagreeing duration tables; I am not starting the same thing with intervals.

### 6b. `songs.key` has no frame marker, and is NULL on 8 of 20 cached titles

On Eb-tuned *Little Wing* the cached key is `Em`. That is **correct in the shape frame** and a
semitone off in the sounding frame, and the field says nothing about which it means. Scoring it wrong
would be a false positive of exactly the kind FLE-89 already spent a plan revision on, so tolerance
A-15 accepts either and the report says which was used. Two consequences:

- `key` needs a frame marker, or a stated convention, before it can gate anything. **Spec to
  Ripley/Kaya.** My recommendation: store the **sounding** key and let the UI derive the shape name,
  the same direction FLE-115 §7.2 already took with `Chord.sounding_name`.
- It is NULL on 8 of 20 cached titles including *Little Wing*, *Kashmir*, *Eruption*, *Blackbird* and
  *Sultans of Swing*, so it cannot be a gate at all today. Filed as a note, not a defect of this set.

Also fixed in passing: my own loader first read `key` out of the breakdown JSON, where it does not
exist — `Breakdown` holds tab/chords/technique_notes/drills and no key. That made the whole column
read as unscorable, which is how I noticed.

### 6c. For FLE-117 (Doro) — two checks that must not become automated gates

This is the second such warning; FLE-103 §6 already flagged T7 template-reuse as a 100%
false-positive on the pilot set.

- **FLE-103's V2 "third present" is a chord-stab check and must not run on riff or arpeggio bars.**
  A boogie has **no third at all** — the 1–5–6–♭7 figure is the whole content of eight bars of *Sweet
  Home Chicago*, and a third in it would be wrong. An arpeggio sounds **one note per column**, so a
  per-beat third check fails every correct bar of *House of the Rising Sun*. That is why ground truth
  here carries `kind` (`chord` / `riff` / `arpeggio` / `melody`) and `chord_spelled_over`
  (`beat` / `bar`) per bar, and why `chord_checked` is false on riff bars.
- **The T11 "chord never sounded whole" check catches the consequential form only.** It fires when no
  single column holds root + third + the characteristic tone. It does **not** fire on a split that
  leaves a complete triad in one half — and it should not: a three-note voicing is legitimate. So
  T11 coverage is partial by design, and claiming more would be the thing I keep warning about.

### 6d. A blind spot this set states rather than scores around

**Kashmir's 3-against-4 is inexpressible.** `time_signature` is per measure and there is one voice,
so notating the guitar in 3/4 loses its relationship to the 4/4 bar and notating it in 4/4 loses the
polyrhythm. A `voices` layer was already ruled out of scope (FLE-105 Q3) and I am **not** asking for
it in v1. It is recorded in the data file under `schema_gap` so the set declares its own limit.

And the general limit, committed in `scoring.what_this_cannot_measure`: **a well-formed invention
still scores well.** Kashmir's specific invented vamp now fails (`pitch_set_not`), but a generator
that emits a plausible, in-key, meter-correct riff for a song whose notes I did not commit passes the
constraint checks and is still wrong. The 48 note_exact bars are the only part of this set immune to
that, and they cover 6 of 10 songs. **Do not report the constraint score as accuracy.**

---

## 7. What follows

**Done here.** 10 songs, 80 bars, tuning / key / meter / feel / passage boundaries per song, a
scoring method with stated units and 15 numbered tolerances, and a measured baseline. Accuracy is a
diff now, and it survives me: the next person runs two commands.

**Specs out, none of which block this issue:**

1. **Ripley/Kaya** — add `"7sus4": (0, 5, 7, 10)` to `QUAL` and delete my `QUAL_EXT` (§6a).
2. **Ripley/Kaya** — give `key` a frame, preferably by storing the sounding key (§6b).
3. **Doro / FLE-117** — do not promote V2-third or T11 to unconditional gates (§6c).
4. **FLE-118** — the double-applied-swing case is now testable: `--mutation-test` row 5 is exactly
   the defect the prompt must avoid, and `no_literal_swing_subdivision` is the check.

**One product question, to Hernan through Miagi:** which *Big Love* arrangement do we teach? Until
that is answered, no note-exact ground truth for it can exist (§3). It is not a tab question.

**One spend request, not made:** ~$1.50 of breakdown generation would move the note_exact denominator
from 16 to 48 (§5). Better spent after FLE-118 lands.
