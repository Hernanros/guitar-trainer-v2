---
type: spec
task: FLE-105
created: 2026-10-05
status: accepted — Fret signed off all five questions 2026-10-05; implementation issues cut
author: Ripley (engineering)
parent: FLE-102 (tab error taxonomy), FLE-89 (trainer-quality bar)
inputs:
  - .planning/investigations/261005-fle102-tab-error-taxonomy.md  (T9, T10, root cause)
  - .planning/investigations/261005-fle103-pilot-verified-set.md  (§1b renderer, §4 capo)
  - .planning/design/261005-fle105-musical-decisions.md  (Fret, commit bea87fc — answers to §11)
---

# Tab schema v2 — articulation, rhythm, capo

**What this is.** The data-model change that makes a correct tab *representable*. Today the note
object is exactly `{string, fret, duration}` with three duration values ever used, so six of the
ten songs FLE-102 audited have no correct representation at any prompt quality. This spec defines
the fields, their arithmetic, their validation, their backward-compatibility story, and what the
renderer draws for each.

**What this is not.** Not the generator prompt (FLE-106), not the validation gates (FLE-33/FLE-106),
not the verified-tab serving path (FLE-103 §6). Those consume this contract; they are listed in §8.

---

## 1. The change, in one table

| object | field | today | v2 |
|---|---|---|---|
| `Note` | `duration` | `str`, free-form | closed `Literal`, 14 values incl. dotted + triplet |
| `Note` | `link_to_next` | — | `Optional["tie"\|"hammer_on"\|"pull_off"\|"slide"]` |
| `Note` | `bend` | — | `Optional[Bend]` |
| `Note` | `technique` | — | `Optional[Technique]` — how the note is struck |
| `Note` | `effects` | — | `list[Effect] = []` — stackable sustain/dynamics |
| `Beat` | `rest` | — | `Optional[Duration]`, valid only when `notes == []` |
| `Tab` | `capo` | — | `int = 0`, frets are capo-relative |
| `Tab` | `feel` | — | `Literal["straight","swing","shuffle"] = "straight"` (§2.5) |
| `Chord` | `sounding_name` | — | `Optional[str]`, set iff `capo > 0` (§7.2) |

Nine additions. Every one is optional with a default, so a plain picked note stays exactly
`{string, fret, duration}` — the common case does not get more expensive for the model to emit, and
every cached JSONB breakdown reads back unchanged (§7).

The last two came out of Fret's sign-off (`261005-fle105-musical-decisions.md`), and one sentence in
that document governs every capo-adjacent decision below, so it is quoted here rather than
paraphrased: **anything the hand does is capo-relative; anything about sound is absolute.** Frets,
shapes and diagrams are capo-relative; key, sounding pitch and `sounding_name` are absolute.

---

## 2. Duration — a closed enum with exact arithmetic

### 2.1 The vocabulary

```python
Duration = Literal[
    "whole", "half", "quarter", "eighth", "sixteenth", "thirty-second",
    "dotted-half", "dotted-quarter", "dotted-eighth", "dotted-sixteenth",
    "triplet-half", "triplet-quarter", "triplet-eighth", "triplet-sixteenth",
]
```

Canonical spelling is `<modifier>-<base>`, hyphen, lowercase. **This matters**: the repo currently
carries three independent duration tables that disagree with each other —
`fle102_tab_audit.py:47` accepts both `dotted-half` and `dotted_half` and both `triplet-eighth` and
`eighth-triplet`; `fle103_verify_pilot_tabs.py:42` accepts `triplet-eighth` *and* `eighth-triplet`.
v2 has **one** table and one spelling; the alternates become legacy aliases handled only by the
inbound coercer in §7.2.

### 2.2 The beat-value table — MUST be `Fraction`, not `float`

```python
from fractions import Fraction as F

DURATION_BEATS: dict[str, Fraction] = {
    "whole": F(4),          "half": F(2),            "quarter": F(1),
    "eighth": F(1, 2),      "sixteenth": F(1, 4),    "thirty-second": F(1, 8),
    "dotted-half": F(3),    "dotted-quarter": F(3, 2),
    "dotted-eighth": F(3, 4), "dotted-sixteenth": F(3, 8),
    "triplet-half": F(4, 3), "triplet-quarter": F(2, 3),
    "triplet-eighth": F(1, 3), "triplet-sixteenth": F(1, 6),
}
```

One quarter note = 1 beat, independent of the time signature's denominator (the existing V5 check
already converts: `want = num * (4/den)`).

`Fraction` is not fastidiousness. Measured:

```
sum([1/6] * 6) == 1.0   ->  False      (0.9999999999999999)
sum([1/3]*3 + [1/6]*6 + [0.5]*2) == 3.0  ->  False  (3.0000000000000004)
```

The existing V5 check survives this only because of its `1e-6` tolerance. Once meter validation
becomes a **write gate** rather than a report, an exact zero is the only honest comparison, and a
tolerance is a place for a real half-beat-off bar to hide behind rounding. Costs nothing: `Fraction`
arithmetic on 14 fixed denominators is exact and fast.

### 2.3 Sextuplets are expressible; quintuplets are not

A sextuplet is six notes in one beat → six `triplet-sixteenth` (6 × 1/6 = 1). *Eruption*'s tapping
is notatable. Quintuplets, septuplets and nested tuplets are **not**, and that is deliberate: a
general `{n, in_space_of}` tuplet object costs the model a nested emit on every note and buys
repertoire this catalog does not contain. If a song needs one, it is an enum extension, not a
redesign.

### 2.4 The rule that survives from v1: one duration per beat

`fle103_verify_pilot_tabs.py:88` already fails a beat whose notes disagree on duration —
*"simultaneous notes of one voicing must agree"*. v2 **keeps** this and promotes it to a Pydantic
model validator on `Beat`.

The consequence must be stated plainly rather than discovered later: **independent polyphonic voices
remain inexpressible.** Expressing them requires a `voices: list[Voice]` layer above `Beat`, which is
a larger change to both schema and renderer than this spec, and it is not what makes six of
FLE-102's ten songs unrepresentable. **Confirmed out of scope by Fret (Q3).**

### 2.4.1 The sustain convention — `let_ring` at nominal duration

My first draft said a held bass under a moving melody "cannot be written in v2". Fret's answer is
that this overstates the loss, and the difference decides the whole fingerstyle end of the catalog,
so the convention is written down here rather than left to whoever notates *Blackbird*:

**A sustained note under motion is written as `effects: ["let_ring"]` on the note, at the nominal
duration of its own beat.** The bass note is placed where it is struck and marked; the melody
continues in later beats. Pitch is right, placement is right, and the meter arithmetic in §2.2 keeps
its single branch. What is lost is the exact *release point*: a `let_ring` note's printed duration is
the beat it occupies, **not how long it sounds**.

Two conditions, both of which are failure modes if skipped:

1. **§9.2's `let_ring` dashed bracket is load-bearing, not decoration.** Undrawn, the sustain exists
   only in the JSON and the workaround delivers nothing to the player — the capo failure a fourth
   time. It outranks most of the glyphs below it in that table.
2. **The nominal-duration rule binds two consumers, and both get it wrong by default.** No meter
   check may try to reconcile printed duration with sounding length, and no ground truth may express
   sustain by *lengthening* a duration — that breaks one-duration-per-beat and the bar sum at once.

**What stays genuinely inexpressible, named so v2 is not oversold:** real counterpoint, where the
release points *are* the music — *Big Love*'s independent slapped bass against the melody. v2 can
write *sustain*, not *independent voices*. Those songs stay out of the pilot set and out of the
regression set until a `voices` layer exists.

### 2.5 Feel — swing and shuffle are marked, not notated

```python
class Tab(BaseModel):
    feel: Literal["straight", "swing", "shuffle"] = "straight"
```

Fret's call on Q1, and the reasoning matters more than the field because it is what keeps the enum
at three values. I had offered writing swing literally as `triplet-quarter` + `triplet-eighth` pairs
as the "unambiguous" option. **It is not unambiguous, it is specifically wrong:** a triplet pair
hard-codes a 2:1 ratio, and real swing varies with tempo. *Autumn Leaves* at 120 is near 2:1;
*Giant Steps* at 240 is played almost dead even. Notating both as identical triplet pairs teaches a
player to bounce *Giant Steps* like a shuffle. The marking is vaguer and truer, and it is how every
fake book is read.

`swing` and `shuffle` stay **distinct values**: swing is tempo-dependent jazz eighths; shuffle is a
hard triplet subdivision where the *omitted middle triplet* is the groove (*Pride and Joy*,
*Sweet Home Chicago*, *Folsom Prison Blues*). Different instruction to the hand.

Three rules ship with the field:

1. **`feel` renders at the staff head** — "Swing 8ths" / "Shuffle" beside the capo badge (§9.2). A
   marking that lives only in the data teaches nobody; that is precisely what `tuning: standard`
   bought Wonderwall.
2. **Never both.** Swing applied to an already-swung triplet pair yields 4/9 + 2/9 — nonsense.
   Triplets stay legal, but when `feel != "straight"` they mean *a real triplet against the
   prevailing feel*. This is a free mechanical check for FLE-106: `feel != "straight"` plus a bar
   made entirely of `triplet-quarter`/`triplet-eighth` pairs is double-applied swing. **Fret's note
   that the check is worth more than the field is the reason it is listed as deliverable, not
   advice.**
3. **No `bossa` or `latin` value.** It would let the model emit one token in place of writing the
   pattern — the same substitution of a label for musical content that produced this spec.

**One authoring exception**, from Fret: when the subdivision *is* the lesson ("feel the triplet
underneath the shuffle"), the drill is written in literal triplets with `feel: "straight"` for that
snippet. A drill-authoring convention, not a schema change.

### 2.5.1 Scope correction — five songs, two different problems

My comment said Q1 decided "the four swing/bossa pilot songs". Measured against the committed ground
truth it is five, and only three of them are a `feel` problem:

| song | committed feel | fixed by `feel`? |
|---|---|---|
| Autumn Leaves | medium swing, 120 | yes |
| Take the 'A' Train | medium swing, 160 | yes |
| Giant Steps | fast swing, 240 | yes — **omitted from my list of four** |
| Blue Bossa | bossa — *even eighths* | **no** |
| The Girl from Ipanema | bossa — *even eighths* | **no** |

Bossa is straight-eighth music; a feel marking does nothing for it. What those two lack is **notated
syncopation** — the chord anticipated onto the "and of 2" — which the §2.1 duration enum and §3
`Beat.rest` already deliver with no new field. So `feel` unlocks three songs and the enum unlocks
two more for free. Re-notation of all five is FLE-107, after the schema lands.

---

## 3. Rests

```python
class Beat(BaseModel):
    notes: list[Note] = []
    rest: Optional[Duration] = None   # set iff notes == []
```

A beat is either a sounding column or a silent column, never both. Model validator: exactly one of
`notes` (non-empty) / `rest` is set; a beat with neither is invalid.

Rests live on `Beat`, not `Note`, because a rest has no string and no fret — putting it on `Note`
forces a sentinel string/fret on an object that has neither, and every consumer then has to know
the sentinel. At the beat level the meter arithmetic in §2.2 stays uniform: a beat contributes
`DURATION_BEATS[rest]` or `DURATION_BEATS[notes[0].duration]`, one branch.

---

## 4. Legato links — tie, hammer-on, pull-off, slide

```python
Link = Literal["tie", "hammer_on", "pull_off", "slide"]
```

`Note.link_to_next: Optional[Link] = None`.

All four say the same structural thing — *the next note on this string is reached without a fresh
pick* — which is why they are one field rather than a tie flag plus an articulation enum. The target
is implicit: **the next note on the same string, in beat order, crossing the barline if necessary**
(a tie over a barline is how sustain past the bar is written; *Comfortably Numb* needs it).

Each link carries a free, exact validation that needs no recording — three new mechanical checks for
FLE-106:

| link | the check |
|---|---|
| `tie` | next note on this string has the **same** fret |
| `hammer_on` | next note on this string has a **higher** fret |
| `pull_off` | next note on this string has a **lower** fret |
| `slide` | next note on this string has a **different** fret |
| all four | a next note on this string **exists** |

Slide direction is derived from the target fret, not declared — a declared direction is one more
thing that can contradict the notes.

Durations are unaffected by a tie: each note keeps its own value and the bar sums the same. A tie
means "do not re-pick", not "merge the two notes into one".

**Out of scope, named:** slide-into-nothing (fall-off / scoop), trills, and bend-to-a-tied-note
chains. The first two need a target that does not exist as a note.

---

## 5. Bends

```python
class Bend(BaseModel):
    semitones: Literal[0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    kind: Literal["bend", "pre_bend", "bend_release", "pre_bend_release"]
```

`Note.bend: Optional[Bend] = None`. `fret` stays the **fingered** fret; the bend says where the pitch
goes from there. The four `kind` values are the four standard forms and they are complete for
single-target bends:

| kind | what happens |
|---|---|
| `bend` | struck at pitch, bent up by `semitones`, held |
| `pre_bend` | already bent at the attack, held |
| `bend_release` | struck, bent up, released back to the fretted pitch |
| `pre_bend_release` | already bent at the attack, released to the fretted pitch |

### 5.1 `0.25` — in the schema, withheld from the v1 prompt

I asked (Q2) whether the quarter-tone curl earned its place or was a value the model would scatter at
random. Fret's answer splits the two halves of my worry, and the resolution is a vocabulary split,
not a schema change:

**The value stays.** The curl is not an ornament on a note, it *is* the note: in a blues in E the b3
(G) curled toward the major 3rd (G#) is a pitch deliberately neither. Rounding it to `0.5` writes the
major third and rounding it to `0` writes the minor third — both are **pitch errors**, not lost
nuance, and *Pride and Joy*'s main vocabulary has no honest representation without it. It is
teachable on screen provided it is labelled the way players say it: render `b¼` and call it
**"curl"**, never "quarter-tone bend".

**The scatter risk is handled in the prompt, not the schema.** The FLE-106 generator vocabulary is
allowed to be narrower than this contract and **must not teach `0.25` in v1** — so ground truth can
be right and FLE-107 can score a curl the generator is permitted to find, while v1 model scatter risk
is zero. Revisit only when the generator places `0.5` bends correctly on blues repertoire: an
under-taught model emitting half-step bends on the b3 is a defensible simplification, one emitting
random quarter-tones is not.

**Out of scope, named:** a bend released to a *different* non-zero amount, unison bends as a single
object (write them as two notes, one bent), and vibrato applied to the bent pitch specifically
(use `effects: ["vibrato"]`, which does not distinguish).

**The sounding pitch of a bent note is not an integer fret.** Any consumer deriving pitch —
the FLE-103 verifier's V1/V2/V3 chord checks — must treat a bent note as sounding
`fret + semitones` and therefore *skip* it for chord-spelling purposes rather than scoring the
fingered pitch. Scoring the fingered pitch would flag every correct bend as a foreign note.

---

## 6. Technique and effects — two fields, deliberately

```python
Technique = Literal[
    "tap", "slap", "pop", "rake",
    "natural_harmonic", "pinch_harmonic", "artificial_harmonic",
]
Effect = Literal[
    "vibrato", "wide_vibrato", "palm_mute", "staccato", "accent", "let_ring", "dead",
]
```

`Note.technique: Optional[Technique] = None` — **how the note is struck.** Mutually exclusive: a
note cannot be both tapped and slapped. `None` means an ordinary pick.

`Note.effects: list[Effect] = []` — **what happens to it while it rings, and how hard.** Stackable:
an accented, palm-muted note with vibrato is one real note, so this cannot collapse into the
single-value field above. Duplicates are de-duplicated on parse.

The two cannot be one field, and the reason is the reason the whole schema is being changed: a
palm-muted, accented hammer-on into a bent note with vibrato is a *single* note in *Comfortably
Numb*, and a one-articulation-per-note model is the same kind of lossy as the one we are replacing.

### 6.1 `dead` resolves a live contradiction

`TabNotation.tsx:94` renders `note.fret === -1` as `x` (a dead note). `fle103_verify_pilot_tabs.py:143`
**rejects** `fret < 0` — *"muted strings must be omitted, not -1"*. The renderer supports a shape the
verifier bans.

v2 resolves it: **`Note.fret` is `>= 0`, always.** A dead/ghost note is the fret it is fingered at
with `effects: ["dead"]`. `fret = -1` keeps its meaning only on `ChordPosition`, where it means "this
string is not played" in a chord diagram and is a different concept. The renderer keeps its `-1 → x`
branch as tolerance for cached rows; nothing new emits it.

---

## 7. Capo — Fret's finding, folded in

From FLE-103 §4 and Fret's 2026-10-04 comment on this issue. *Wonderwall* (catalog difficulty 0.20,
tagged `standard`) is played in standard tuning with a capo at 2. With `tuning` as the only setup
field, emitting the shapes makes every sounding pitch two semitones wrong and emitting the pitches
hands the player unidiomatic barres instead of the open shapes the song is actually played with.
`tuning: standard` is true and actively misleading. Not caught by FLE-33 (the tuning genuinely *is*
standard) and not caught by FLE-102's seven checks (the note objects are individually well-formed).
*Wagon Wheel* and *Street Fighting Man* are in the 64 with the same problem.

```python
class Tab(BaseModel):
    measures: list[Measure]
    tuning: list[str] = ["E", "A", "D", "G", "B", "e"]
    capo: int = Field(0, ge=0, le=12)
```

**I am taking Fret's proposed convention: fret numbers are relative to the capo.** The capoed string
is fret 0. Absolute-frets-plus-a-label makes every open-position shape unreadable, which defeats the
purpose of having the field.

### 7.1 The arithmetic every consumer must adopt

```
sounding_pitch(note) = open_pitch(tuning[note.string]) + capo + note.fret
```

Uniform — no special case for `fret == 0`. `tuning` continues to mean *the pitch of the open strings
before the capo*, so Wonderwall is `tuning: standard, capo: 2`.

Three consequences that are easy to miss and each break something:

1. **Fret bound is `0 <= fret <= 24 - capo`.** There is no way to express a note behind the capo,
   which is correct — physically there isn't one.
2. **`fle103_verify_pilot_tabs.py` derives pitch from `tuning` alone.** Unchanged, it scores every
   capo song two semitones flat and V1/V2/V3 fail across the board on correct tab. The verifier
   change ships with the schema change or the capo field makes the audit worse, not better.
3. **Chord diagrams are capo-relative too.** `ChordPosition.fret` and `Chord.base_fret` must use the
   same origin as the tab, or Wonderwall's diagrams contradict its tab. This needs no new field —
   it needs to be written down, which it now is.

### 7.1.4 The silent one: `capo` must be inherited by `tab_snippet`, never defaulted

Fret's catch on Q5, and neither of us named it in the first draft. `Tab` is embedded in
`Drill.tab_snippet` (§8.3) and `capo` defaults to `0`. **A drill lifted from a capo-2 song whose
snippet takes the default is two semitones wrong with no error anywhere** — the notes are
well-formed, the meter sums, and the verifier derives a pitch that is simply the wrong one. That is
FLE-102's T8 with a new cause, and the *default* is what makes it invisible.

Requirement, on the server side of the split:

- `drill.tab_snippet.capo` and `drill.tab_snippet.tuning` are **inherited from the parent `Tab` at
  construction**, not defaulted. `tuning` has the same defaulting shape and the same exposure today.
- Breakdown-level validation asserts `drill.tab_snippet.capo == tab.capo` and
  `drill.tab_snippet.tuning == tab.tuning` for every drill.

**Out of scope, named so it is a decision:** `capo` means a **full six-string capo**. Partial and cut
capos are not expressible and **must not be faked with a doctored `tuning` array** — that puts a lie
in the one field pitch derivation trusts. Nothing in the 64 needs one; if something does, it is a new
field, not a workaround.

### 7.2 `Chord.name` is the shape; `sounding_name` carries the sound

Resolved by Fret (Q4), taking the recommendation with the pedagogy pinned down so the field cannot
drift later. Under a capo at 2 the Wonderwall shape is fingered Em7 and sounds F#m7.

```python
class Chord(BaseModel):
    name: str                            # the shape as fingered — "Em7"
    sounding_name: Optional[str] = None  # absolute — "F#m7"; set iff capo > 0
```

- **`name` = the shape as fingered.** Wonderwall's is `Em7`: it is what the hand does, it is what the
  diagram beside it depicts (§7.1.3 already makes the diagram capo-relative, so any other answer
  makes the name contradict the picture), and it is the transferable unit — "Em7 shape" works on
  fifty songs, "F#m7 at capo 2" is knowledge about one.
- **`sounding_name` is set only when `capo > 0`.** Not optional *to teach* — anyone playing along
  with the record needs it — but redundant at capo 0, and a redundant duplicate is where a
  contradiction lives.
- **Free validation**, worth stating because it is mechanically checkable for every chord quality in
  this catalog: if `capo == 0`, `sounding_name` must be unset; if `capo > 0` and set, it must equal
  `name` transposed up `capo` semitones with the quality unchanged. Wonderwall checks out —
  Em7/G/D/A7sus4 → F#m7/A/E/B7sus4.
- **Display:** `Em7` full size, `sounds F#m7` small beneath — same treatment as the `Capo II` badge.
- **Song-level `key` stays absolute.** Wonderwall is in F# minor even though every shape on screen
  comes from E minor. That is the governing sentence in §1 applied.

---

## 8. Backward compatibility and migration

### 8.1 No Alembic migration

`songs.breakdown` is JSONB. Every v2 field is optional with a default, so every cached breakdown
parses unchanged into a v2 `Breakdown`. `_REQUIRED_BREAKDOWN_KEYS` (`song.py:316`) is derived from
the model's required fields and is unaffected — no new required key is introduced, so
`is_renderable_breakdown` and `coerce_placeholder_breakdown` keep their current behaviour.

### 8.2 The one narrowing change, and its coercer

`duration: str` → `duration: Duration` is the only field that gets *stricter*. A cached or emitted
value outside the 14 is now a `ValidationError`, and unlike `drills` — which the breakdowns endpoint
soft-fails to `[]` — a `tab` validation error has no soft-fail path and would take the whole
breakdown down.

So: **coerce, do not raise.** This is the house pattern, for the house reason — see
`Drill._clamp_repetitions` (`song.py:218`), whose docstring makes the argument: the cost of raising
is not "this note is dropped", it is "this song is dropped".

```python
_DURATION_ALIASES = {
    "dotted_half": "dotted-half", "dotted_quarter": "dotted-quarter",
    "dotted_eighth": "dotted-eighth", "eighth-triplet": "triplet-eighth",
    "quarter-triplet": "triplet-quarter", "sixteenth-triplet": "triplet-sixteenth",
}

@field_validator("duration", mode="before")
@classmethod
def _canonicalise_duration(cls, v: object) -> object:
    if not isinstance(v, str):
        return v
    s = v.strip().lower().replace(" ", "-")
    s = _DURATION_ALIASES.get(s, s)
    if s not in DURATION_BEATS:
        logger.warning("Unknown note duration %r — coerced to 'quarter'.", v)
        return "quarter"
    return s
```

The alias map is exactly the alternate spellings the two existing audit scripts already accept, so
nothing that passes today's audit fails tomorrow's parse.

**Measured risk today: zero rows coerce.** FLE-102 counted all 532 notes in the cached corpus:
`quarter` (218), `eighth` (308), `half` (6), no other value. All three are in the enum verbatim.

### 8.3 Regeneration

`mobile/src/api/generated/schema.d.ts` is generated from the FastAPI OpenAPI schema and must be
regenerated. `Note` there is currently `{string: number; fret: number; duration: string}`
(`schema.d.ts:1013`).

`Tab` is embedded in `Drill.tab_snippet` as well as `Breakdown.tab`, so drills get the articulation
vocabulary for free — which is required, not incidental: a drill that isolates a bend cannot be
written in v1 either, and FLE-102 T8 showed drill notes inheriting the tab's errors.

**"For free" does not extend to `capo` and `tuning`** — those default rather than inherit, which is a
silent two-semitone error. See §7.1.4; the inheritance and the equality assertion are part of
deliverable 1.

---

## 9. Renderer contract

`mobile/src/components/TabNotation.tsx`. **The renderer change is not optional scope that can follow
later, and this is the single most load-bearing point in this spec.**

FLE-103 §1b measured that the component never reads `note.duration` (not once in 236 lines) and that
`MEASURE_WIDTH` is hardcoded to four beats (`TabNotation.tsx:61`) while beats lay out unbounded — so
31 of 72 cached prod bars (43%) already print fret numbers on top of their neighbour.

**The duration enum turns that 43% into 100%.** A 4/4 bar of straight sixteenths is 16 columns; at
`BEAT_WIDTH = 48` the last column lands at x = 774 inside a 230px measure box — three measures
downstream. Every bar that uses the new vocabulary for the thing it was added for is visually
corrupt. Shipping the schema without the renderer makes the product worse at exactly the songs the
schema was added to serve.

### 9.1 Layout — width from content

Replace the constant with a per-measure computation:

```ts
const MIN_COLUMN = 20;
const columnWidth = (d: Duration) => Math.max(MIN_COLUMN, BEAT_WIDTH * beatsOf(d));
const measureWidth = (m: Measure) =>
  LEFT_MARGIN + m.beats.reduce((w, b) => w + columnWidth(durationOf(b)), 0) + 8;
```

Measure offsets become a running sum rather than `i * MEASURE_WIDTH`. Two things fall out:

- **FLE-103 §1b is fixed as a side effect** — no measure can overflow a box sized from its own content.
- **Rhythm becomes visible for the first time.** Proportional column width is not engraved notation,
  but it is the difference between a rhythm the user can see and one that is discarded. Today,
  writing correct triplets into ground truth would change nothing a user sees (FLE-103 §1b), which
  is why Fret could not verify feel on four of the ten pilot songs.

Full notation — stems, beams, flags, a rhythm staff under the tab — is **out of scope**. Named so it
is a decision and not an omission.

`ChordDiagram` calibrates to the layout constants (`TabNotation.tsx:10-11`); `STRING_SPACING`,
`BEAT_WIDTH`, `LEFT_MARGIN`, `TOP_PADDING` all keep their values, so only the measure-width
derivation changes.

### 9.2 Glyphs — what each field draws

| field | drawn as |
|---|---|
| `link: hammer_on` | `h` between the two columns, on the string's line |
| `link: pull_off` | `p`, same placement |
| `link: slide` | `/` if the target fret is higher, `\` if lower |
| `link: tie` | slur arc above the two columns; no letter |
| `bend: bend` | `b` after the fret + an up-arrow above with `¼` / `½` / `full` / `1½` |
| `bend: semitones == 0.25` | `b¼`, labelled **"curl"** in any legend or tooltip — never "quarter-tone bend" (§5.1) |
| `bend: pre_bend` | `pb` + the amount |
| `bend: *_release` | the above plus a `r` and a down-arrow |
| `technique: tap` | `T` above the column |
| `technique: natural_harmonic` | the fret number in angle brackets — `<7>` |
| `technique: pinch_harmonic` | `P.H.` above |
| `technique: artificial_harmonic` | `A.H.` above |
| `technique: rake`, `slap`, `pop` | `rake` bracket, `S`, `P` |
| `effect: vibrato` / `wide_vibrato` | `~~~` / `≈≈≈` trailing the column on that string |
| `effect: palm_mute` | `P.M.` with a dashed bracket spanning the run |
| `effect: let_ring` | `let ring` with a dashed bracket spanning the run — **load-bearing (§2.4.1), not decoration: it is the entire sustain story, and outranks most rows below it** |
| `effect: accent` | `>` above |
| `effect: staccato` | a dot above |
| `effect: dead` | fret number replaced by `x` |
| `Beat.rest` | a rest glyph centred vertically in the column, not on a string line |
| `Tab.capo` | `Capo II` badge beside the tuning label + a thick vertical bar at the staff start |
| `Tab.feel` | `Swing 8ths` / `Shuffle` at the staff head, beside the capo badge; nothing drawn for `straight` |
| `Chord.sounding_name` | `sounds F#m7`, small, beneath the full-size shape name (§7.2) |

The capo badge renders whenever `capo > 0`, **independently of the tuning label** — which currently
renders only for non-standard tuning (`TabNotation.tsx:142`). Wonderwall is standard tuning, so
without this it would show no setup information at all, which is the exact failure Fret described.

**The same argument applies to `feel`, and Fret made it a condition of Q1 rather than a nicety:** an
unrendered marking teaches nobody, so a `feel` that reaches the data but not the staff head is the
`tuning: standard` defect a third time. The three staff-head items — tuning, capo, feel — are one
piece of work: the setup line.

Unknown enum values from a future server (a client older than the server) must render as the bare
fret number rather than crash. Every glyph above is additive decoration on a fret number that is
already correct.

---

## 10. Everything downstream that must change

Not a suggestion list — these are the places that break or silently mislead if they are not touched.

| # | file | change | breaks if skipped? |
|---|---|---|---|
| 1 | `server/app/models/song.py` | the schema itself + the §8.2 coercer | — |
| 2 | `mobile/src/components/TabNotation.tsx` | §9 | **yes** — 100% bar corruption |
| 3 | `mobile/src/api/generated/schema.d.ts` | regenerate from OpenAPI | **yes** — TS won't compile against new fields |
| 4 | `server/scripts/fle103_verify_pilot_tabs.py` | capo in pitch derivation (§7.1); `Fraction` meter; skip bent notes in V1–V3 (§5); V6 rewritten against §9.1 | **yes** — fails correct capo tab |
| 5 | `server/app/ai/breakdown.py` SYSTEM_PROMPT | teach the new vocabulary — **FLE-118**, not FLE-106 (which closed 2026-10-04 having shipped C1–C7) | no, but the schema buys nothing until it happens |
| 6 | `server/scripts/fle102_tab_audit.py` | drop its private `DUR` for the §2.2 table | no — drifts |
| 7 | `server/scripts/grade_eval.py`, `grade_cached_drills.py` | their own measure parsers | no — ignore new fields |
| 8 | `server/data/verified_tabs/pilot_verified_set.json` | parses unchanged — all 1612 notes are `quarter`, verified; **five** songs re-notated under FLE-107 once the schema lands (§2.5.1) | no |
| 9 | `server/scripts/fle103_build_pilot_set.py` | drop `conventions.max_beats_per_measure: 4` when §9.1 lands | no — but ground truth stays shaped by a dead renderer limit |

On #8: the pilot set is **1612 quarter notes and nothing else**. That is not a criticism of it — it
is the measured form of FLE-103 §4's own admission that it verified pitch and not feel, and it is
what the v1 schema permitted.

Fret's two additions to this point, both of which strengthen it:

- **The stronger version of the finding.** As all-quarters ground truth the set does not merely fail
  to *catch* a rhythm regression — it would **score a correctly swung generated bar as wrong**. It is
  actively misleading as a rhythm oracle, which is why FLE-107 blocking on the schema rather than on
  the prompt is correct. The pitch/harmony half stands and is still worth running.
- **Row 9 is a renderer constant that leaked into ground truth.** The set's `conventions` block
  justifies `max_beats_per_measure: 4` with `TabNotation.tsx MEASURE_WIDTH` — the music was bounded
  to what the component could draw. §9.1 removes that limit, so the constraint has to come out of the
  builder or the ground truth stays shaped by a limitation that no longer exists.

**One flag on #5 that is not mine to wave away.** `breakdown.py:162` says of `SYSTEM_PROMPT`: *"this
string is the prompt the 04.1-07 eval graded, so edits invalidate the eval."* Teaching the new
vocabulary necessarily edits it. The FLE-17 §3 eval baseline is invalidated by this work, and
whoever owns FLE-106 should plan a re-run rather than discover it.

---

## 11. The five musical calls — resolved

All five answered by Fret on 2026-10-05 (`.planning/design/261005-fle105-musical-decisions.md`,
commit `bea87fc`). The contract above is updated in place; this section is the index, so no reader
has to reconstruct a decision from two documents.

| Q | question | decision | lands in |
|---|---|---|---|
| Q1 | swing/shuffle — notate or mark? | **mark it.** `Tab.feel`, three values, keep writing eighths. Literal triplet pairs hard-code a 2:1 ratio that real swing does not have. | §2.5, §2.5.1, §9.2 |
| Q2 | is the `0.25` bend worth having? | **keep in schema, withhold from the v1 prompt.** The curl is a pitch, not an ornament; rounding it either way is a pitch error. Render `b¼`, call it "curl". | §5.1 |
| Q3 | are polyphonic voices out of scope? | **confirmed out** — but sustain is expressible: `let_ring` at nominal duration. Real counterpoint stays out. | §2.4, §2.4.1 |
| Q4 | under a capo, what is `Chord.name`? | **the shape** (`Em7`), plus `sounding_name` set iff `capo > 0`. Song `key` stays absolute. | §7.2 |
| Q5 | confirm the capo convention | **confirmed as written**, both consequences included, plus the inheritance defect neither of us had named. | §7, §7.1.4 |

Four things changed in the contract because of the review rather than merely being confirmed, and
they are the reason this review was worth blocking on:

1. **`Tab.feel` exists at all** (Q1) — and the *double-applied-swing check* it enables is, in Fret's
   assessment, worth more than the field. It is a named deliverable in §12, not a suggestion.
2. **`Chord.sounding_name` exists** (Q4), with a mechanical validation that makes the pair
   self-checking rather than a place for a contradiction to live.
3. **§7.1.4 — `tab_snippet` inheritance.** A capo-2 drill that takes the `capo: 0` default is two
   semitones wrong with **no error anywhere**. Neither of us saw this in the first draft; it is the
   single highest-value catch of the review, because the default is what makes it invisible.
4. **§2.4.1 — the sustain convention.** My draft wrote off the entire fingerstyle catalog. It was
   overstated: *Blackbird* and the *Hotel California* chord reading are teachable under `let_ring`
   at nominal duration. *Big Love* is genuinely out.

My original scope claim for Q1 was also wrong in a way worth recording: I said four swing/bossa
songs, measured against ground truth it is five, split across two different problems (§2.5.1).

Nothing in the contract is now waiting on a musical answer.

---

## 12. Implementation split

Sign-off is in, so the issues below are cut: **FLE-115** (server), **FLE-116** (mobile renderer),
**FLE-117** (verifier), **FLE-118** (generator prompt). Each is implementable against this document
alone. Ordering: 1 and 2 are independent and run in parallel; 3 and 4 both depend on 1 — 3 imports
the §2.2 table, and 4 must not teach a vocabulary the model cannot be validated against.

**1. Server — schema, coercer, canonical duration table.** `server/app/models/song.py`.
The §1 table, §2.1 enum, §2.2 `Fraction` table (exported, so the audit scripts import it instead of
re-declaring a third disagreeing copy), §3 rests, §4 links, §5 bends, §6 technique/effects, §6.1
`fret >= 0`, §7 capo, §7.1.4 inheritance + equality assertion, §7.2 `sounding_name` + its transpose
validation, §8.2 coercer.
Tests: every cached duration round-trips; the §8.2 aliases canonicalise; an unknown duration coerces
to `quarter` with a warning and **does not raise**; a sextuplet bar sums to exactly 1 under
`Fraction`; each §4 link check fires; `0 <= fret <= 24 - capo`; a drill snippet inherits a non-zero
capo and the mismatch assertion fails when it does not; `sounding_name` unset at capo 0 and
transpose-checked above it.

**2. Mobile — renderer.** `mobile/src/components/TabNotation.tsx`, two commits.
First **width-from-content** (§9.1) — a standalone fix for the 43% of cached prod bars that already
overprint, worth shipping on its own merits. Then **glyphs** (§9.2), inside which three items are
load-bearing rather than decorative and should land before the long tail: the **setup line**
(tuning + `Capo II` + `Swing 8ths`, each rendering independently of the others), the **`let_ring`
dashed bracket** (§2.4.1 — without it, sustain exists only in JSON), and **`b¼` labelled "curl"**.
Unknown enum values from a newer server must degrade to the bare fret number, never crash.
`mobile/src/api/generated/schema.d.ts` is regenerated from the OpenAPI schema as part of this.

**3. Verifier — capo, `Fraction`, bent-note pitch.** `server/scripts/fle103_verify_pilot_tabs.py`,
§10 row 4. Capo in the pitch derivation (§7.1 — unchanged, it scores every capo song two semitones
flat and fails V1–V3 across correct tab); `Fraction` meter with no `1e-6` tolerance; skip bent notes
in the V1–V3 chord-spelling checks (§5 — scoring the fingered pitch flags every correct bend as a
foreign note); V6 rewritten against §9.1; import the §2.2 table rather than its own. Also drop
`conventions.max_beats_per_measure: 4` from `fle103_build_pilot_set.py` (§10 row 9).

**4. Generator prompt — FLE-118.** Earlier drafts of this section said the prompt work was "tracked
elsewhere" under FLE-106. **That was wrong, and the error was load-bearing:** FLE-106 shipped the
C1–C7 recording-free audit checks and closed on 2026-10-04, so the prompt half had no owner at all.
Without it the schema buys nothing (§10 row 5) — the model keeps emitting `{string, fret, duration}`
because nothing taught it otherwise. FLE-118 carries: teaching the §2.1–§7 vocabulary; the two
deliberate narrowings this review created (**do not teach `0.25`**, §5.1; **no `bossa` feel**, §2.5);
the **double-applied-swing check** (§2.5, which Fret rated above the field itself); and the eval
problem from §10 — `breakdown.py:162` states that editing `SYSTEM_PROMPT` invalidates the 04.1-07
eval, so the FLE-17 §3 baseline needs a *planned* re-run. Blocked on deliverable 1.

**One consumer outside this split.** FLE-107 owns re-notating the five songs in §2.5.1; its blocker
is now deliverable 1, not this spec — the schema is what it waits on, not the contract.
