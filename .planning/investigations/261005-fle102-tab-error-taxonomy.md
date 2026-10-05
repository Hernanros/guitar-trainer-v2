---
type: investigation
task: FLE-102
created: 2026-10-05
status: findings-complete
next_step: schema-and-verification-specs
author: Fret (Guitar Curriculum Lead)
---

# What kind of wrong the cached tabs are

**Trigger:** Hernan, 2026-10-04 — *"the tabs aren't correct most of the time."* Nobody on the
team could say what kind of wrong. FLE-89 plan revision 5 ruled out the two explanations we had
(tuning, and the anti-slice rule), so the defect is in the notes themselves.

**Reproduce:** `server/scripts/fle102_tab_audit.py` (read-only, no LLM, no spend).

```
PGURL=$(railway variables --service Postgres --json \
        | python3 -c "import json,sys;print(json.load(sys.stdin)['DATABASE_PUBLIC_URL'])") \
  server/.venv/bin/python server/scripts/fle102_tab_audit.py
```

---

## Headline

**0 of 10 cached tabs are a correct rendering of the song they name. None is fit for a stranger
to practise from.**

That is not a close call on any of the ten. It is not one bug — it is eleven distinct failure
modes, and the two biggest of them cannot be fixed by prompt work at all because the data model
cannot represent a correct answer.

## Corpus correction: ten, not six — and two of them are off-catalog

FLE-102 was written against six cached tabs. There are now **ten** (four generated 2026-10-04,
after the earlier probe ran). Two have **no `song_catalog` row at all**:

| cached song | catalog row | consequence |
|---|---|---|
| Cause We've Ended as Lovers — Jeff Beck | **none** | no tuning ground truth, no difficulty gate |
| Big Love — **Fleetwood Mac** | exists as *Lindsey Buckingham* | artist string missed the join |

So the generator will happily produce a breakdown for a title that is not in the curated catalog,
and nothing downstream notices. `song_catalog` now has `tuning` set on all 64 rows, so the tuning
check that FLE-89 reported as "4 of 4" is now **8 of 8 agreeing** — tuning is confirmed *not* the
bug, more firmly than before. But 2 of the 10 are never checked against anything.

## The accuracy number, and why the computable one is the wrong one

Two numbers, kept apart on purpose.

**1. Mechanical validity** — seven checks that need no recording, because they are wrong against
notation or against the guitar itself. Unit: the bar (n=72).

| | bars |
|---|---|
| pass all seven checks | **2 / 72 (3%)** |
| pass all except C4 (uniform-duration grid) | **45 / 72 (62%)** |

**2. Musical correctness against the song named** — my judgement, song by song. Unit: the tab
(n=10) and the bar (n=72).

| | |
|---|---|
| tabs that are a correct rendering of the named song | **0 / 10** |
| bars I would certify as correct for the named song | **0 / 72** |

**The gap between 62% and 0% is the finding.** Take Kashmir: it passes **8 of 8** mechanical
checks — clean on every machine-checkable dimension — and it is a completely invented riff, a
D5–G5–A5 power-chord vamp where the song has a chromatic ascent in a 3-against-4 polyrhythm.
A tab can be perfectly well-formed and still have nothing to do with the song.

So: **no automated accuracy number available today measures the defect Hernan heard.** Any gate
built on the mechanical checks alone would have passed Kashmir. I am stating the 62% so it is on
record and reproducible, not because it means the tabs are 62% right. They are not 62% right.

## The error taxonomy

Eleven classes. "songs" counts how many of the ten show it.

### Note-content errors

**T1 — Mode flip: major third where the minor third belongs.** *(3 songs)*
The most frequent *musical* error in the corpus, and the most audible.
- *Little Wing* m4, m7: `E + M3 + 5` = **E major**. The song opens on **Em**.
- *Little Wing* m6, labeled `Am7`: `A + M3 + 5` = **A major**. Am7 needs C natural; the tab plays C#.
- *Eruption* m2–m3, labeled `A minor (open)`: the arpeggio spells `A E A C# E C# A E` = **A major**.
- *Big Love* m7–m8: an E bass alternating with G#. Big Love is in **Em**; that G should be natural.

**T2 — Chord quality never sounded.** *(6 songs)*
The tab is monophonic root–fifth–octave motion, so the harmony the song depends on is never
actually played. Corpus-wide: 302 of 385 beats hold exactly one note; **only 15 of 72 bars contain
any third at all**, and six tabs contain **zero** simultaneous thirds across all 8 bars
(*Cause We've Ended as Lovers, Comfortably Numb, Big Love, Chan Chan, Eruption, Kashmir*).
A "Bm" that is only ever a B is not a Bm.

**T3 — Position error: right shape, wrong fret.** *(1 song)*
*Lenny* m3 is an E-shape barre at fret 2, which in E♭ tuning sounds **F major**. The chord list
calls it `B7`/`B (barre, fret 2)`. A B needs that shape at fret 7 — **five frets flat**, so a
different chord entirely.

**T4 — Named chord voiced as a different chord.** *(1 song)*
*Lenny*'s `E (open)` is voiced as the three open middle strings, D–G–B = **G major**. Its
`A (open)` is voiced `A–B–E` = **Asus2**, no third anywhere.

**T5 — Systematic off-by-one scale degree.** *(1 song)*
*Comfortably Numb*: every C is **C natural** (string 2, fret 13) where B minor requires **C#**
(fret 14). Five bars. This turns B Aeolian into **B Phrygian** — the darkest available wrong note
over Bm. It contradicts the breakdown's own chord list, which names A major (A–C#–E), so no
recording is needed to call it.

**T6 — Invented material.** *(4 songs)*
A passage that is not in the song in any arrangement.
- *Kashmir*: a I–IV–V power-chord vamp at frets 0/5/7 substituted for the riff.
- *Eruption* m1: eight repeated open high-E eighth notes. Nothing like the opening.
- *Chan Chan*: root motion E–F#–B–E. The song is in D minor.
- *Pride and Joy* m6–m7: generic blues double-stops — in key, in the right pentatonic, not the song's licks.

**T7 — Chromatic clusters that are not chords.** *(1 song)*
*Little Wing* m3 and m8b3: `E + m3 + 4 + 5 + M7` — a five-note stack holding both a minor third
and a major seventh. m5: `G + m3 + 4 + 5 + b7` = Gm11, a chord the song does not contain. The
8-bar excerpt uses **all 12 pitch classes**.

**T8 — Borrowed from the wrong song by the same artist.** *(1 song)*
*Little Wing*'s chord list includes `E7#9 (Hendrix chord)`. That is Purple Haze / Foxey Lady.
Little Wing does not use it. The model reached for the artist's signature instead of the song's.

This one did not stay in the chord list — **it became a drill.** Little Wing's two drills are
`Lock the Open E Shape — All Six Strings` and `E → E7#9 Colour Swap`, and both teach
`E + M3 + 5` = **E major**, for a song in E minor. Worse, the chord the second drill calls
`E7#9` spells `E–F#–G#–B–D` = **E9**. A real E7#9 needs the ♯9 (G natural) grinding against the
major third; that is the entire character of the chord, and it is absent. So the drill is named
after a chord from the wrong song and then does not contain that chord either. A player who
practises it learns a wrong shape for a song that never used the right one.

### Rhythm and notation errors

**T9 — Rhythm flattened to a grid.** *(9 songs, 58 of 72 bars)*
Every note in the bar carries the same duration. The entire corpus uses only **three** duration
values — `quarter` (218), `eighth` (308), `half` (6). There are no triplets, no sixteenths, no
dotted values, no rests and no ties anywhere in 532 notes. Consequences:
- Every shuffle is misnotated as straight eighths. *Pride and Joy* is a Texas shuffle; a Texas
  shuffle played straight is not the song. Same for *Sweet Home Chicago*.
- *Eruption*'s tapping is sextuplets; it is written as even eighths.
- *Pride and Joy* also loses the 5→6→♭7 boogie movement that defines the figure — the tab
  alternates root+5th with root+octave, which is a rock pattern, not a shuffle.

**T10 — Bars that do not add up.** *(3 songs, 6 bars)*
*Sweet Home Chicago* m1 (3.5 beats in 4/4), m2 (3), *Pride and Joy* m7 (5), *Little Wing* m1/m4/m7
(3.5). If the player app sequences these literally, the bar is simply the wrong length.

**T11 — Chord split across adjacent beats.** *(1 song, 3 bars)*
*Little Wing* m1: beat 1 holds strings 6/2/1 and beat 2 holds strings 5/4/3 — two disjoint halves
of one six-string voicing, so the chord is never sounded whole. Read together they spell E major
plus a C natural (a ♭6 clashing with the 5th), where **Em** belongs.

### Structural

**T12 — Off-catalog generation.** *(2 songs)* — see the corpus correction above.

**T13 — Wrong frame label.** *(1 song)* — added by FLE-125, 2026-10-05. The only class in which
every note is correct.
The `tuning` array that converts fret numbers to pitches is wrong while every fret number is
right. Intervals, shapes, fingering and relative pitch are all preserved, so the passage sounds
uniformly transposed rather than wrong.
*Cause We've Ended as Lovers* declares E♭ standard for what is a standard-tuning transcription: in
the declared frame its 8 bars are **F♯ Dorian**, in standard they are **G Dorian**, and the record
is in G minor. **Correcting one array fixes the tab — regenerating it would be the wrong repair.**
Distinguishing feature: T13 is the only class that survives a global transposition. Every other
class is a wrong *relationship* between notes; this is a wrong *origin* for all of them.
Detectability: **not detectable from content when the row is otherwise coherent** — see
`261005-fle125-tuning-ground-truth.md` §3–4, where the obvious coherence check turns out to flag
4 of 4 correctly-tuned E♭ songs.

> **This strikes FLE-89 root cause #2, "a wrong tuning makes every fret number wrong."** It does
> not. A uniform tuning error makes every fret number uniformly a semitone flat, with the
> intervals intact. One array is wrong; 33 notes are right.

---

## The root cause that no prompt change can fix

Across all 532 notes in the corpus, the note object is **exactly** `{string, fret, duration}`.
One key-set, no exceptions. There is no field for a bend, slide, hammer-on, pull-off, vibrato,
tap, rake, palm mute, rest, or tie — and only three duration values ever appear, so no triplet
is expressible either.

For *this* catalog that is fatal, because articulation **is** the material:

| song | what the song is made of | what the schema can say |
|---|---|---|
| Comfortably Numb | bends, sustain, vibrato | fret numbers |
| Little Wing | thumb-over chord fills, slides | block chords |
| Eruption | two-hand tapping, sextuplets | even eighths on one string |
| Pride and Joy | shuffle feel, rake, double-stop bends | straight eighths |
| Lenny | volume swells, b3→3 slides | quantised chord stabs |
| Big Love | fingerpicked alternating pattern | undifferentiated notes |

T9 and T10 are schema defects, not prompt defects. You cannot prompt your way to a Gilmour bend
through a field that does not exist. **This is the first thing to fix**, and it is a spec for
Ripley/Kaya, not a generator-prompt change.

## Per-tab verdict: fit for a stranger to practise from?

| # | song | tuning | classes present | fit? | the one-line reason |
|---|---|---|---|---|---|
| 1 | Sweet Home Chicago — R. Johnson | standard ✓ | T2 T9 T10 | **No** | 2 bars, neither of which adds up to 4/4; no shuffle, no turnaround |
| 60 | Lenny — SRV | E♭ ✓ | T3 T4 T9 | **No** | all three chords wrong: "E" sounds G, "A" has no third, "B7" sounds F |
| 71 | Pride and Joy — SRV | E♭ ✓ | T2 T6 T9 T10 | **No** | the shuffle — the entire point — is notated straight, and the 6/♭7 movement is missing |
| 73 | Cause We've Ended as Lovers — Beck | E♭ **✗ → standard** | T2 T9 **T13** | **No** | monophonic and bendless on a track that is nothing but bends — *and* the tuning is resolved wrong (FLE-125 §2: the frets are standard, the label is not) |
| 74 | Comfortably Numb — Pink Floyd | standard ✓ | T2 T5 T9 | **No** | B Phrygian, not B minor — every C is a semitone flat, 5 bars |
| 76 | Big Love — "Fleetwood Mac" | standard **✓** | T1 T2 T9 | **No** | E major where the song is Em; off-catalog, so the arrangement is unspecified. Tuning resolved correct by FLE-125 §6 — and the catalog has no Big Love row under *either* artist, so this was never an artist-string join miss |
| 103 | Chan Chan — Buena Vista | standard ✓ | T2 T6 T9 | **No** | in E; the song is in D minor, and no chord quality is ever sounded |
| 106 | Little Wing — Hendrix | E♭ ✓ | T1 T7 T8 T10 T11 | **No** | 1 of 8 chords right; opens E major-plus-♭6 where Em belongs |
| 107 | Eruption — Van Halen | E♭ ~ | T1 T6 T9 | **No** | A major arpeggio labeled A minor; tapping written as plain eighths |
| 108 | Kashmir — Led Zeppelin | DADGAD ✓ | T6 T9 | **No** | right tuning, invented riff — a I–IV–V vamp, and the polyrhythm is gone |

The one genuinely correct musical object in the whole corpus is *Little Wing* m2 (a G major
triad, the right chord in the right place). I will not certify even that bar, because T11 splits
it across two beats so the voicing never sounds.

Worth noting: the four most recent tabs (*Chan Chan, Little Wing, Eruption, Kashmir*, all
2026-10-04) are no better than the 2026-09-08 batch. *Little Wing* is the worst of the ten and is
among the newest. This is current generator quality, not legacy debt.

## What I could not verify, and what I would need

Stated plainly rather than folded into the numbers above.

- **Cause We've Ended as Lovers — the key, and the E♭ tuning call.** I believe Beck recorded
  *Blow by Blow* in standard tuning, which would make the E♭ here wrong and every fret number a
  semitone off. I am not certain, and there is no catalog row to check against. The "unfit"
  verdict above rests on recording-independent grounds (monophonic, no articulation), not on the
  key. **Need:** the track plus a reference transcription.
- **Chan Chan — the exact chart.** I am confident it is in D minor and that the emitted tab's E–F#–B
  is wrong. The commonly published progression is Dm–Fm–Gm–A7, and I would want the record before
  I publish a bar-by-bar correction against it. **Need:** the track.
- **Big Love — which arrangement.** The 1987 *Tango in the Night* studio version and Buckingham's
  solo live arrangement are different guitar parts. The breakdown names neither, so "correct" is
  undefined. **Need:** a product decision on which arrangement we teach, then the record.
- **Lenny — 6/8 vs. 4/4-with-triplets.** The breakdown says 6/8. That is a defensible notation of
  a 12/8 feel, so I am *not* calling it a defect. The defect I am calling is the uniform
  eighth-note grid (T9).
- **Eruption — the tuning.** VH1 was recorded roughly a quarter-tone flat. E♭ is a defensible
  approximation; **not** counted as a defect.

I have not verified any of the ten against a recording in this pass. These verdicts come from
knowledge of the records plus internal contradictions in the breakdowns themselves (a tab that
disagrees with the chord diagrams shipped beside it needs no external reference). Where those two
sources were not enough, it is listed above rather than scored.

**Open caveat from FLE-89:** the pending question to Hernan — *which songs was he looking at?* —
is still unanswered. If his songs are outside these ten, the generator has produced more tab than
the DB kept and the corpus grows. His answer extends this work; it does not invalidate it, because
the taxonomy above is drawn from defect classes, not from his specific examples.

## What follows from this

1. **Schema first** (Ripley/Kaya). Articulation, rests, ties, and triplet durations. Until the
   model can express a bend, six of these ten songs have no correct representation. T9/T10 are
   62 of the 72 bars' worth of defects and are not prompt-addressable.
2. **A verification pass in the generator.** The cheap, recording-free checks catch real defects —
   C6 (tab contradicts its own chord list) alone flags 21 bars, including the whole Comfortably
   Numb C-natural error. Those are free and should block a write. But see the Kashmir caveat: they
   are a floor, not a gate.
3. **A regression set with known ground truth.** I own this (priority 4 on my brief). Ten songs
   whose tab we know cold, with the correct tab committed, so "accuracy" becomes a diff instead of
   a judgement call. This is the only thing that turns the 0/10 above into a number that can be
   tracked release over release.
4. **Harden the catalog join.** Off-catalog generation should fail loudly, not silently skip the
   tuning ground truth.
5. **The drill content standard** (FLE-89's other half) is downstream of this. A drill that
   prepares a passage cannot be better than the passage — and T8 shows the error propagating:
   both of Little Wing's drills teach E major for a song in E minor, one of them named after a
   chord from a different song, which it then misspells. Fixing drill *copy* will not help while
   the drill *notes* inherit the tab's errors.
