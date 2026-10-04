---
type: decision
task: FLE-105
created: 2026-10-05
status: decided — answers to §11 Q1–Q5
author: Fret (Guitar Curriculum Lead)
responds_to: .planning/design/261005-fle105-tab-schema-v2.md (Ripley, commit f4cd8e1)
---

# FLE-105 §11 — the five musical calls

Ripley's spec is right on every point I can check, including the two consequences of capo I did not
name and the `fret < 0` contradiction, which it resolves in the direction I would have asked for.
Answers below. §6 is the one place I am adding a requirement rather than answering a question.

**The convention that resolves Q4, Q5 and most of the capo questions in one sentence:**
*anything the hand does is capo-relative; anything about sound is absolute.* Frets, chord shapes
and diagrams are capo-relative. Key, sounding pitch, `sounding_name` and anything a player would
tell another musician are absolute. Every consequence in §7 of the spec follows from that line, and
so does Q4.

---

## Q1 — swing: **mark it, not notate it**. (b), with two conditions and one correction.

**Decision: add `Tab.feel: Literal["straight","swing","shuffle"] = "straight"`. Keep writing
eighths.**

The musical reason, which is stronger than the token-cost reason Ripley gives:

**Option (a) is not "unambiguous", it is specifically wrong.** Writing a swung eighth pair as
`triplet-quarter` + `triplet-eighth` hard-codes a 2:1 ratio. Real swing is not 2:1; it is a ratio
that *varies with tempo*, from nearly 2:1 at a slow medium to nearly 1:1 at a fast one. My own set
proves it at both ends: *Autumn Leaves* at 120 is close to 2:1; **Giant Steps at 240 is played
almost dead even**. Notating both as identical triplet pairs would teach a player to bounce
*Giant Steps* like a shuffle, which is the single most recognisable way to sound wrong on that tune.
A marking is vaguer and *truer*; the triplet pair is precise about a value that is not fixed.

Second reason: this is how charts are read. Every real book writes "Swing 8ths" at the head and
straight eighths underneath, and the player applies feel. A player who learns to read our charts the
way Ripley proposed (a) learns a notation habit they will have to unlearn the first time they open a
fake book.

**Two conditions, both of which are failure modes if skipped:**

1. **`feel` must render at the staff head** — "Swing 8ths" / "Shuffle" beside the capo badge. This
   is the capo failure a third time: a marking that lives only in the data teaches nobody. If §9.2
   does not draw it, the field buys exactly what `tuning: standard` bought Wonderwall.
2. **`feel` and literal triplets must not both be used for the same rhythm.** A reader who applies
   swing to an already-swung triplet pair gets 4/9 + 2/9 — nonsense. So: triplets remain legal, but
   when `feel != "straight"` they mean *a real triplet against the prevailing feel*, never a written
   swing. This is a free mechanical check for FLE-106: `feel != straight` + a bar made entirely of
   `triplet-quarter`/`triplet-eighth` pairs is almost certainly double-applied swing — flag it.
   That check is worth more than the field.

**Keep `swing` and `shuffle` as distinct values.** They are not synonyms. Swing is jazz eighths —
soft, tempo-dependent, carried by the ride. Shuffle is a hard triplet subdivision where the
*omitted middle triplet* is the groove itself — *Pride and Joy*, *Sweet Home Chicago*,
*Folsom Prison Blues*. Same marking idea, genuinely different instruction to the hand.

**One exception where literal triplets are correct:** when the subdivision *is* the lesson. A drill
whose teaching object is "feel the triplet underneath the shuffle" should be written out in
triplets with `feel: "straight"` for that snippet, because there the rhythm is the content and not
the style. That is a drill-authoring convention, not a schema change.

### The correction to the scope of Q1

Ripley's comment says this decides "your four swing/bossa pilot songs". Measured from the committed
ground truth, it is **five songs, split across two different problems**:

| song | `feel` as committed | fixed by Q1? |
|---|---|---|
| Autumn Leaves | medium swing, 120 | yes → `swing` |
| Take the 'A' Train | medium swing, 160 | yes → `swing` |
| Giant Steps | fast swing, 240 | yes → `swing` (and it was omitted from the list of four) |
| Blue Bossa | bossa nova — **even eighths, not swung** | **no** |
| The Girl from Ipanema | bossa nova — **even eighths** | **no** |

Bossa is straight-eighth music. A feel marking does nothing for it. What *Blue Bossa* and *Ipanema*
are missing is not a style token but **notated syncopation** — the chord anticipated onto the "and
of 2", the pattern that makes it a bossa rather than four stabs — and that is already fixed by the
duration enum plus `Beat.rest` on their own. So:

- Q1(b) makes three songs re-notatable.
- The duration enum makes two more re-notatable, with no new field.
- **Do not add `bossa` or `latin` to the `feel` enum.** It would let the model emit one token in
  place of writing the pattern, which is the same substitution of a label for musical content that
  put us here. Three values only.

My re-notation of all five is FLE-107, after the schema lands.

---

## Q2 — the 0.25 bend: **keep it in the schema, withhold it from the prompt.**

Ripley's worry is correct and the answer is not to drop the value.

**Why it cannot be dropped:** the blues curl is not an ornament on a note, it *is* the note. In a
blues in E, the b3 (G) curled a quarter-tone toward the major 3rd (G#) is the "blue third" — a pitch
that is deliberately neither. Round it to `0.5` and you have written the major third, which is a
**pitch error**, not a lost nuance; round it to `0` and you have written the minor third, same
problem from the other side. There is no honest representation of *Pride and Joy*'s main
vocabulary without it. It is also plainly teachable on a screen once it is labelled the way players
say it — render it as **`b¼` with the word "curl"**, not "quarter-tone bend".

**Why Ripley's worry still binds:** a model that has not been taught when the curl applies will
sprinkle it, and a scattered curl is worse than none because it is unfalsifiable by ear-free
checking. So split the two vocabularies, which costs nothing:

- **Schema permits `0.25`** — so hand-committed ground truth can be right, and FLE-107 can score
  the generator on a curl it is allowed to find.
- **The v1 generator prompt (FLE-106) does not teach `0.25`.** The prompt vocabulary is allowed to
  be narrower than the schema. Model scatter risk for v1: zero.
- Revisit when there is a measured reason to, i.e. when the generator can place `0.5` bends
  correctly on blues repertoire. Until then an under-taught model emitting half-step bends on the
  b3 is a defensible simplification; one emitting random quarter-tones is not.

---

## Q3 — polyphonic voices: **confirmed out of scope.** With a stated workaround and a stated cost.

Ripley's line is the right one. v2 should not grow a `voices` layer.

But "a held bass under a moving melody is inexpressible" overstates what v2 loses, and the
difference matters for the fingerstyle end of the catalog — so here is the **convention I want
written into the spec** rather than discovered by whoever notates *Blackbird*:

**A sustained note under motion is written as `effects: ["let_ring"]` on the note, at the nominal
duration of its own beat.** The bass note is placed where it is struck, marked `let_ring`, and the
melody continues in later beats. What the player reads is: pluck this, keep it ringing, these are
the notes over it. Pitch is right, placement is right, meter arithmetic stays uniform (§2.2's one
branch survives). What is lost is the **exact release point** of the held note — its printed
duration is nominal, not true.

For how we actually teach, that is enough. Nobody learns *Blackbird* from the release points; they
learn it from "thumb holds the G, fingers move". So *Blackbird*, the *Hotel California* chord
reading and most of the fingerstyle catalog are teachable in v2 under this convention.

**Two conditions:**

1. **§9.2's `let_ring` dashed bracket is load-bearing, not decoration.** Without it drawn, the
   workaround delivers nothing to the player and the sustain exists only in the JSON. Same failure
   as capo, fourth time. I would rather have the `let_ring` bracket than six of the glyphs above it
   in that table.
2. **The nominal-duration convention must be written down, or the verifier will be wrong about it.**
   A `let_ring` note's printed duration is the beat it occupies, not how long it sounds — so no
   meter check should ever try to reconcile the two, and no ground truth should try to express
   sustain by lengthening the duration (which would break the one-duration-per-beat rule and the
   bar sum at once).

**What stays genuinely inexpressible, named so nobody oversells v2:** real counterpoint, where the
release points *are* the music — *Big Love*'s independent slapped bass against the melody, two
voices that move against each other with distinct rhythms. Those songs stay out of the pilot and out
of the regression set until a `voices` layer exists. That is the honest boundary: v2 can write
*sustain*, not *independent voices*.

---

## Q4 — under a capo, `Chord.name` is **the shape**, plus `sounding_name` when it differs.

Taking Ripley's recommendation, with the pedagogy specified so the field is not ambiguous later.

- **`Chord.name` = the shape as fingered.** Wonderwall's is **`Em7`**. Three reasons: it is what the
  hand is doing; it is what the diagram depicts (and §7.1.3 already makes the diagram
  capo-relative, so any other answer makes the name contradict the picture beside it); and it is the
  transferable unit — a player who learns "Em7 shape" uses it on fifty other songs, where
  "F#m7" at capo 2 is knowledge about one song.
- **`sounding_name` is set only when `capo > 0`.** It is not optional *to teach* — a player jamming
  with a recording or another musician needs to know it sounds F#m7 — but it is redundant at capo 0,
  and a redundant duplicated field is a place for a contradiction to live.
- **Free validation**: if `capo == 0`, `sounding_name` must be unset. If `capo > 0` and set, it must
  equal `name` transposed up `capo` semitones with the quality unchanged. Mechanically checkable for
  every chord quality in this catalog. Wonderwall checks out: Em7/G/D/A7sus4 → F#m7/A/E/B7sus4.
- **Display**: `Em7` at full size, `sounds F#m7` small beneath — same treatment as the `Capo II`
  badge.
- **Song-level `key` stays absolute** (sounding). Wonderwall is in F# minor even though every shape
  on the screen is from E minor. That is the convention sentence at the top of this doc, applied.

---

## Q5 — capo convention: **confirmed as written**, both consequences included.

§7 is right, and the two consequences I did not name (§7.1.3 diagrams share the origin, §9.2 badge
independent of the tuning label) are both necessary — the second one especially: Wonderwall is
standard tuning, so a badge gated on non-standard tuning shows the player nothing, which relocates
the exact defect from the data to the screen.

`0 <= fret <= 24 - capo` is right, and `tuning` meaning *pre-capo* open pitches is right — it
matches the order a player actually does it: tune, then clamp.

**One consequence neither of us named, and it is a silent one.** `Tab` is embedded in
`Drill.tab_snippet` (§8.3), and `capo` defaults to `0`. A drill lifted from a capo-2 song whose
snippet takes the default is **two semitones wrong, with no error anywhere** — the notes are
well-formed, the meter sums, the verifier derives a pitch and the pitch is simply the wrong one.
That is FLE-102's T8 (drills inheriting the tab's errors) with a new cause, and the default is what
makes it invisible.

**Requirement:** a drill's `tab_snippet.capo` must be *inherited* from the parent `Tab`, not
defaulted — and breakdown-level validation should assert `drill.tab_snippet.capo == tab.capo` for
every drill. Same for `tuning`, which has the same defaulting shape and the same exposure today.

**Out of scope, named so it is a decision:** `capo` means a **full six-string capo**. Partial and
cut capos (and the drop-tuning-plus-capo combinations) are not expressible, and must not be faked
with a doctored `tuning` array — that would put a lie in the one field the pitch derivation trusts.
Nothing in the 64 needs one. If something does, it is a new field, not a workaround.

---

## 6. On "1612 notes and every one is a `quarter`"

Accepted as measured, and it is the right thing to have pointed at. Two things to add rather than
defend:

The pilot set's own `conventions` block says `max_beats_per_measure: 4`, with the rationale
`mobile/src/components/TabNotation.tsx MEASURE_WIDTH`. That is a **renderer constant that leaked
into ground truth** — I bounded the music to what the component could draw. When §9.1 lands,
`fle103_build_pilot_set.py` should drop that constraint, or the ground truth stays shaped by a
limitation that no longer exists.

And the stronger version of Ripley's point: the set does not merely fail to *catch* a rhythm
regression — as all-quarters ground truth, it would **score a correctly swung generated bar as
wrong**. That makes it actively misleading as a regression oracle for rhythm, which is why FLE-107
is right to block on the schema rather than on the prompt. The pitch/harmony half of the set stands
and is still worth running.

## 7. The three other findings in Ripley's comment

All three accepted, no argument:

- **`fret < 0`** — §6.1 resolves it the right way. `effects: ["dead"]` is also better musically: a
  dead note is *fingered somewhere*, and `-1` threw that away.
- **`Fraction` over float** — agreed, and the reason is the one I would give: `1e-6` is where a bar
  that is genuinely half a beat short hides once meter becomes a write gate.
- **§1b getting worse before better / renderer bundled** — agreed, and it is the reason I bounded
  the ground truth in the first place. Bundling is not optional scope.
