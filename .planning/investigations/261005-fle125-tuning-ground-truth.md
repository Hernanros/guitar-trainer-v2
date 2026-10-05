---
type: investigation
task: FLE-125
created: 2026-10-05
status: delivered
author: Fret (Guitar Curriculum Lead)
---

# Tuning ground truth for off-catalog songs

**Fret, 2026-10-05.** FLE-125, split out of FLE-109 defect #1. Dispatched to me rather than to
Kaya (FLE-33's owner) because the hard part is ground truth, not plumbing. Ripley holds the
plumbing half as FLE-108.

Scope I was given: **the ground-truth source/decision, and the Jeff Beck wrong-tuning call.** Not
production code. Read-only against prod, no LLM, no generation calls, zero spend.

Artefacts:

- `server/scripts/fle125_tuning_coherence.py` — the content-only coherence check. `--self-test`
  (8/8), `--from-db` (read-only SELECT), `--emitted FILE`.
- This document — the decision, the taxonomy entry, and the ground-truth rows.

---

## 1. Headline

| | |
|---|---|
| Song 73 verdict | **Wrong. The record is standard tuning; the tab's frets are already standard.** |
| The issue's option 3, as written | **Not viable** — it flags 4 of 4 correct E-flat songs |
| The check that does work | Frame agreement — 1 true positive, 0 false positives on 9 rows |
| The residual risk FLE-125 wanted closed | **Cannot be closed by any content check.** Ground truth is irreducible |
| The decision | **Default to standard off-catalog. Never ask the model. Record provenance.** |
| Off-catalog share of cached generations | **2 of 9** — and 1 of those 2 is the corpus's only wrong tuning |

The one thing I would change about how FLE-125 framed this: it called the coherence check
"probably the highest-value item here, since it works on every song." It does work on every song,
and it does not measure tuning. I built it, it flagged four songs whose tuning is verified
correct, and the reason is a notation convention I should have expected. §3.

---

## 2. Song 73 — *Cause We've Ended as Lovers*, Jeff Beck

### The verdict

**The declared tuning is wrong. It should be `standard`.** The frets do not move.

### Why, from the row's own content

I re-derived all 33 notes myself rather than taking FLE-109's table on trust. It reproduces exactly.
Pitch-class frequency across the 8 bars, in each frame:

| frame | pitch classes | reading |
|---|---|---|
| declared E-flat | F♯×11, C♯×7, G♯×6, D♯×5, B×3, A×1 | F♯ Dorian |
| standard | **G×11**, D×7, A×6, E×5, C×3, B♭×1 | **G Dorian** |

The standard reading is `G A Bb C D E` — six of the seven notes of G Dorian, with no F. The E
natural rules out G natural minor; this is Dorian, which is exactly the colour a Stevie Wonder
tune sits in. G is the tonic by a wide margin (11 of 33) and it ends the phrase in m1, m2, m6
and m7.

The declared frame gives the same shape rooted on F♯/G♭. **Nobody plays this tune in G♭.** That is
the whole argument, and it needs no reference transcription.

### What I know from outside the row

Beck cut *Blow by Blow* (1975) in standard tuning — the record is a Les Paul into a Marshall, no
down-tuning anywhere on it. That matches the content. I did not hear the record in this session, so
the thing that would move this from *high confidence* to *certain* is a pitch check of the master
against A440 — and I would not spend anything on that, because the G♭-Dorian argument above already
settles it on its own.

### The row is wrong in three further ways, independent of tuning

Worth recording because they are separate defects and only one of them is FLE-125's:

1. `songs.key` is `'Bm'`. B minor is `B C# D E F# G A`. The melody contains B♭, which B minor does
   not have; in the declared E-flat frame it contains G♯ and D♯, neither of which B minor has.
   **`'Bm'` is wrong in both frames** — it agrees with nothing in the row.
2. The chords are `Bbm` / `Gb` / `Db` — B-flat minor. The melody is in G Dorian. B-flat minor and
   G Dorian share only B♭, C and F. **Chords and melody are in different keys.** At the
   transposition that reconciles the chord names with their shapes, 32 of 33 melody notes fall
   outside the declared chords.
3. `Gb (IV chord)` is a mislabelled function. The IV of B-flat minor is E♭/E♭m. **G♭ is the ♭VI.**
   (`Db (III chord)` is defensible as ♭III.) A player who reads that learns a false piece of
   theory, which is a content defect on its own terms.

So the row has four unreconciled frames, as FLE-109 said. I confirmed all four independently.

### This corrects FLE-89 root cause #2 — strike it

FLE-89 says *"a wrong tuning makes every fret number wrong."* **That is not what happened and the
claim should be struck.** A uniform tuning error makes every fret number *uniformly a semitone
flat* — the intervals, the shapes, the fingering and the relative pitch are all preserved. The tab
is internally a correct standard-tuning transcription. One array is wrong; 33 notes are right.

This matters for triage: it is the difference between "regenerate the tab" and "fix one label."

---

## 3. The negative result: the issue's option 3 does not work

FLE-125 option 3: *"does the declared `tuning` agree with the key implied by the note content and
by the declared chords?"*

I built it. Each chord diagram declares both a name and a set of (string, fret) positions, so the
lowest sounding pitch should be the named root. No ground truth needed. Run on the nine cached rows:

```
  60 Lenny          Eb  catalog eb_standard   0ok/4bad   <- flagged
  71 Pride and Joy  Eb  catalog eb_standard   0ok/3bad   <- flagged
 106 Little Wing    Eb  catalog eb_standard   1ok/5bad   <- flagged
 107 Eruption       Eb  catalog eb_standard   0ok/3bad   <- flagged
```

**Four for four on the E-flat songs, every one of whose tunings FLE-33 verified correct against the
catalog.** The check does not detect wrong tunings. It detects down-tuned guitars.

The reason is a convention, not a defect:

> **On a uniformly down-tuned guitar, chord names are shape names.**

SRV's *Lenny* is in E-flat standard. SRV, every transcription of it, and this cached row all write
the changes as E / A / B7, because you play open E / A / B7 **shapes**. The sounding pitches are
E♭ / A♭ / B♭7. The row says so out loud — the chord names are literally `"E (open)"`, `"A (open)"`,
`"B7 (open)"`, and `songs.key` is `'E'`, the shape key. A checker that requires the chord name to
equal the sounding pitch condemns the correct notation.

**Any tuning check built on name-versus-sounding-pitch will block Lenny, Pride and Joy, Little
Wing and Eruption.** That is four of our nine cached songs and three of the pilot roster. Do not
ship it.

### Worse: the signature is genuinely ambiguous

Absorb the convention and the ambiguity does not go away — it moves:

| | declared | T\* | chord names match shapes at T\* |
|---|---|---|---|
| Lenny — **tuning correct**, shape names | E-flat | +1 | 4ok / 0bad |
| Song 73 — **tuning wrong**, standard record | E-flat | +1 | 3ok / 0bad |

**Identical.** An E-flat guitar notated with shape names and a standard-tuning tab mislabelled
E-flat produce the same reading, because they *are* the same reading — the difference is entirely in
which one is true of the recording, and the recording is not in the row.

---

## 4. The check that does work, and exactly how far it goes

`server/scripts/fle125_tuning_coherence.py`. Two steps, the first of which exists only to absorb
the shape-name convention so the second can do the real work.

- **Step 1** — find `T*`, the uniform transposition of the declared tuning at which the chord
  diagrams best match their own names. `T* = 0` means sounding names; `T* ≠ 0` means shape names,
  which is legitimate. *This step is not the gate* (§3).
- **Step 2** — at that same `T*`, what fraction of the tab's notes fall outside the pitch classes
  the declared chords assert? **Chords and melody must be in one key.**

```
  id  song                          catalog      decl  T*    names    chords@T*     notes outside  verdict
  60  Lenny                         eb_standard  Eb    +1    shape    4ok/0bad      19/64 = 29.7%  coherent
  71  Pride and Joy                 eb_standard  Eb    +1    shape    2ok/1bad     13/110 = 11.8%  coherent
  73  Cause We've Ended as Lovers   (no row)     Eb    +1    shape    3ok/0bad      32/33 = 97.0%  REVIEW <<<
  74  Comfortably Numb              standard     E     +0 sounding    4ok/0bad       5/29 = 17.2%  coherent
  76  Big Love                      (no row)     E     +0 sounding    3ok/0bad       1/64 =  1.6%  coherent
 103  Chan Chan                     standard     E     +0 sounding    3ok/0bad       2/31 =  6.5%  coherent
 106  Little Wing                   eb_standard  Eb    +1    shape    5ok/1bad       6/98 =  6.1%  coherent
 107  Eruption                      eb_standard  Eb    +1    shape    3ok/0bad      25/60 = 41.7%  coherent
 108  Kashmir                       dadgad       D     +0 sounding    3ok/0bad       0/32 =  0.0%  coherent
```

Eight rows span **0.0% – 41.7%**. Song 73 is **97.0%**. Nothing lands between 42% and 97%, so the
threshold is 70% — placed in the middle of an empty gap rather than fitted to the single positive.

**One true positive and zero false positives over nine rows is not a validated precision.** The
script returns `review_flag`, not a verdict, and `inconclusive` (distinct from "coherent") when
there are no parseable chords or no notes to compare — a vacuous check must never report a pass.

### The limit, stated plainly

**This check cannot verify a tuning. It detects that a row contradicts itself.**

A row that is globally transposed but internally consistent — notes, chord names and chord shapes
all coherently in E-flat for a record cut in standard — scores perfectly, because there is nothing
inside it to disagree with. That row is precisely the residual risk FLE-125 asked to close, and
**no content check closes it.** Song 73 is caught only because it is *additionally* incoherent,
which the issue correctly called luck.

Ground truth is irreducible. That is the finding, and it is what forces §5.

---

## 5. The decision

Since no content check can substitute for ground truth, the only question is what we assert when
we have none. Three things, and one explicit rejection.

### 5.1 Off catalog, default to `standard`. Do not ask the model.

**The harms are not symmetric, and that decides it.**

- A wrong **non-standard** claim makes the user physically retune. Five minutes, and then their
  guitar no longer matches the record — so they cannot play along, which is the entire promise
  ("practising feels like playing"). For a player who knows the song, it also destroys trust
  instantly; that is the *Lenny* incident in `260908-sonnet-tuning-quality.md`, pointed the other
  way.
- A wrong **standard** claim on a genuinely down-tuned song leaves a tab that is internally
  consistent, fully playable, every shape intact, and a semitone sharp of the record. The player
  notices when they play along and can drop their tuning themselves.

The second failure is recoverable by the user in one move. The first is not. **A
transposed-but-consistent tab is still a practisable tab; a retuned guitar that does not match the
record is not.**

The statistical case points the same way but I will not overstate it. Our catalog is 42 of 64
standard — but that catalog is *curated to span tunings* (I put DADGAD, open G, drop C and open D
in it deliberately), so 66% is a floor, not an estimate of what users type. I have no corpus that
would give me the real base rate for free-text entry and I am not going to invent one. What I do
have is 2 off-catalog generations, 1 right and 1 wrong — not a rate, just an existence proof that
inference fails.

### 5.2 Reject option 2 — "state the tuning with its evidence"

FLE-125 option 2 was to require the model to cite an album or recording and treat an
unevidenced non-standard claim as low-confidence. **No.**

The model will produce fluent evidence for a wrong answer. It already produced a confident E-flat
for a standard record; asked to justify it, it will attach "*Blow by Blow*, 1975" — correct album,
wrong tuning — and the claim comes out looking *better* sourced than before. Evidence attached to a
claim is only a check if somebody verifies the evidence, and nobody will. This converts a guess
into a number, which is the failure mode, not the fix.

### 5.3 Record provenance, and let the UI be honest instead of certain

The real fix is to stop claiming ground truth we do not have. Two states, both true:

| state | meaning | what the player sees |
|---|---|---|
| `catalog` | verified against the 64-row tag | assert the tuning, show the retune instruction |
| `default` | no row; we chose standard | **no retune instruction.** "This tab is written in standard tuning" — not "the song is in standard tuning" |

That distinction is honest and it is also useful: the tab stays correct *as a tab* because it is
self-consistent in standard, and we never make a user retune on a guess. It is also the field that
makes the blind spot measurable, which was FLE-125's option 1 — a `default` count *is* the
off-catalog miss count.

### 5.4 Extend the catalog — and the honest cost of 5.1

Catalog extension is the only thing that actually produces ground truth, and it can never reach
free-text entry. §6 has the rows for every off-catalog song in prod today.

**And one of them shows my own recommendation failing.** *Sweet Child O' Mine* is in E-flat
standard — Appetite-era Guns N' Roses tuned down a half step. Default-to-standard would mislabel
it. I am not going to hide that: it is the known cost, the harm is the recoverable kind by §5.1,
and the fix is a catalog row, not a cleverer default. Popular user entries should be *in* the
catalog; that is Junie's roster call and I have flagged it in §6.

---

## 6. Ground truth for the off-catalog songs in prod today

Five titles, six (title, artist) rows. Confidence stated per row, with what would settle the
uncertain ones. **These are my calls, not catalog rows — inserting them is Junie's roster decision
and Ripley's migration.**

| song | artist as entered | tuning | confidence | basis / what would settle it |
|---|---|---|---|---|
| Cause We've Ended as Lovers | Jeff Beck | `standard` | **high** | §2 — G Dorian in standard, G♭ Dorian in the declared frame; *Blow by Blow* has no down-tuning |
| Big Love | Lindsey Buckingham | `standard` | **high** | Standard on both the *Tango in the Night* studio cut and the solo live arrangement. The **arrangement** is still undecided — already a Hernan question from FLE-107, unchanged |
| Big Love | Fleetwood Mac | `standard` | **high** | Same record. See §7 on the artist string |
| Layla | Eric Clapton | `standard` | **medium-high** | Derek and the Dominos, 1970, standard. Caveat: Duane Allman's slide overdubs are sometimes transcribed in open E — a reference transcription of the slide part would settle it. The *Unplugged* arrangement is a different song to teach, same tuning |
| Beat It | Michael Jackson | `standard` | **medium** | Lukather's rhythm parts and the Van Halen solo are standard. **This is the next song 73** — see below |
| Sweet Child O' Mine | Guns N' Roses | `eb_standard` | **medium-high** | Appetite-era GN'R tuned down a half step; the riff is written in D and sounds in D♭. Worth a reference check before it becomes a catalog row |

### *Beat It* is the trap that song 73 fell into, waiting to happen again

*Beat It* is **in the key of E♭ minor** and is played in **standard tuning**. A model that conflates
"the song is in E♭" with "the guitar is in E♭" will emit `eb_standard` — which is exactly the error
in song 73, where a G-minor record got an E-flat tuning. Prod already has `songs.key = 'Em'` on the
*Beat It* row, which is wrong in its own right.

**Key and tuning are different things and the generator does not reliably distinguish them.** That
is the one-line summary of this whole issue, and it is why §5.1 says do not ask.

---

## 7. Two corrections for FLE-108 (Ripley)

Measured against prod today, read-only. Both save work.

1. **"Big Love — Fleetwood Mac | exists as *Lindsey Buckingham* | artist string missed the join" is
   not what happened.** `song_catalog` has **no Big Love row under either artist** — no Buckingham
   row, no Fleetwood Mac row, nothing matching `title ILIKE '%big love%'`. The join did not miss;
   there is nothing to join to.

   I checked whether *any* current miss is an artist-string problem: joining on title alone and
   taking rows where the artists differ returns **0 rows**. The brittle-join risk is real in
   principle — *Layla* under "Derek and the Dominos" would be the live version of it — but it has
   **zero instances in the data today**. Fuzzy-join machinery would be solving a problem we do not
   have; adding the missing rows would.

2. **FLE-108 still carries the struck FLE-89 claim** — "which would put every fret number a
   semitone off." It would not. See §2: uniformly flat, intervals and shapes intact, one array
   wrong and 33 notes right.

Also for the plumbing half, measured: **`songs.tuning` is populated on 0 of 48 rows.** Migration
0014 added the column and nothing writes it, so every consumer still reads tuning out of
`breakdown.tab.tuning` — including the four E-flat songs whose catalog tag is correct.

---

## 8. Taxonomy entry — T13

For FLE-89's error taxonomy (`261005-fle102-tab-error-taxonomy.md`, which has T1–T12; T12 is already
"off-catalog generation", so this is **T13**). This is a
class that was not in it, and it is the only one where the notes are all correct.

> **T13 — Wrong frame label.** *(1 song)*
> The `tuning` array that converts fret numbers to pitches is wrong, while every fret number is
> right. Intervals, shapes, fingering and relative pitch are all preserved; the whole passage
> sounds uniformly transposed.
> *Cause We've Ended as Lovers* declares E-flat standard for a standard-tuning transcription: in
> the declared frame the 8 bars are F♯ Dorian, in standard they are G Dorian, and the record is in
> G minor. **Correcting one array fixes the tab; regenerating it would be the wrong repair.**
> Distinguishing feature: T13 is the only class that survives a global transposition. Every other
> class is a wrong *relationship* between notes; this is a wrong *origin* for all of them.
> Detectability: **not detectable from content when the row is otherwise coherent.** §4.

This also strikes FLE-89 root cause #2 (§2).

---

## 9. What I could not verify, and what I would need

- **The tuning of any record, from the content alone.** This is not a gap in method; it is
  impossible. §4. The only sources are a catalog row, a reference transcription, or the record at
  pitch.
- **A real base rate for standard tuning in free-text entry.** Our catalog is curated to span
  tunings, so its 66% is a floor, not an estimate. I would need a corpus of what users actually
  type. §5.1 does not rest on this number.
- **Precision of the §4 check.** 1 positive and 0 false positives on 9 rows. It would take a
  corpus with several known-wrong tunings to call it a gate, and we have exactly one. It ships as
  a review flag.
- **Three of the §6 rows by ear.** *Beat It* (medium), *Layla*'s slide parts, *Sweet Child O' Mine*.
  Each needs a reference transcription or the record; none needs spend on generation.

## 10. What follows

Mine, done: the song 73 call, the §5 decision, T12, the §6 rows, the check.

Handed to Ripley (FLE-108 + the spec I filed): `tuning_source` provenance, default-to-standard on a
catalog miss, the two §7 corrections, writing `songs.tuning` at all, and wiring §4's script as a
review flag — **not** as a hard gate, and **not** as the name-versus-sounding-pitch check, which
blocks four correct songs.

To Junie, roster: *Sweet Child O' Mine*, *Layla*, *Beat It*, *Big Love* and the Beck are all songs
users have already entered and none is in the 64. Popular entries belong in the catalog, because a
catalog row is the only thing that makes tuning ground truth rather than policy.

To Hernan, through Miagi: nothing here changes what v1 promises. §5.3 changes what the UI *claims* —
from an assertion to an honest default — which I think is strictly a trust improvement, but it is a
player-facing wording change and Hernan should see it.
