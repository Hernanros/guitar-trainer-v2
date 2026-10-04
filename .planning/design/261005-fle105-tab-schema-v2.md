---
type: spec
task: FLE-105
created: 2026-10-05
status: draft — awaiting Fret sign-off on §9
author: Ripley (engineering)
parent: FLE-102 (tab error taxonomy), FLE-89 (trainer-quality bar)
inputs:
  - .planning/investigations/261005-fle102-tab-error-taxonomy.md  (T9, T10, root cause)
  - .planning/investigations/261005-fle103-pilot-verified-set.md  (§1b renderer, §4 capo)
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

Seven additions. Every one is optional with a default, so a plain picked note stays exactly
`{string, fret, duration}` — the common case does not get more expensive for the model to emit, and
every cached JSONB breakdown reads back unchanged (§7).

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
remain inexpressible.** A held half-note bass under an eighth-note melody — *Big Love*, *Blackbird*,
the *Hotel California* 12-string arrangement — cannot be written in v2. Expressing it requires a
`voices: list[Voice]` layer above `Beat`, which is a larger change to both schema and renderer than
this spec, and it is not what makes six of FLE-102's ten songs unrepresentable. Open question Q3 in
§9 asks Fret to confirm that split.

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

`0.25` is in the set for the blues curl (the quarter-tone bend that is most of *Pride and Joy*'s
vocabulary). Q2 in §9 asks Fret whether that granularity earns its place.

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

### 7.2 The one thing I will not decide: what goes in `Chord.name`

Under a capo at 2, the Wonderwall shape is fingered as Em7 and sounds as F#m7. `Chord.name` is a
single string. Whether the player is taught the shape name or the sounding name is a pedagogy call,
not an engineering one. Q4 in §9. My recommendation is `name` = the shape the hand makes (`Em7`,
because that is what the player is doing and what the diagram depicts) plus an optional
`sounding_name` for the chart-reading case — but Fret owns this and I have not added the field.

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

`Tab` is embedded in `Drill.tab_snippet` as well as `Breakdown.tab`, so drills inherit capo and
articulation for free — which is required, not incidental: a drill that isolates a bend cannot be
written in v1 either, and FLE-102 T8 showed drill notes inheriting the tab's errors.

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
| `bend: bend` | `b` after the fret + an up-arrow above with `½` / `full` / `1½` |
| `bend: pre_bend` | `pb` + the amount |
| `bend: *_release` | the above plus a `r` and a down-arrow |
| `technique: tap` | `T` above the column |
| `technique: natural_harmonic` | the fret number in angle brackets — `<7>` |
| `technique: pinch_harmonic` | `P.H.` above |
| `technique: artificial_harmonic` | `A.H.` above |
| `technique: rake`, `slap`, `pop` | `rake` bracket, `S`, `P` |
| `effect: vibrato` / `wide_vibrato` | `~~~` / `≈≈≈` trailing the column on that string |
| `effect: palm_mute` | `P.M.` with a dashed bracket spanning the run |
| `effect: let_ring` | `let ring` with a dashed bracket spanning the run |
| `effect: accent` | `>` above |
| `effect: staccato` | a dot above |
| `effect: dead` | fret number replaced by `x` |
| `Beat.rest` | a rest glyph centred vertically in the column, not on a string line |
| `Tab.capo` | `Capo II` badge beside the tuning label + a thick vertical bar at the staff start |

The capo badge renders whenever `capo > 0`, **independently of the tuning label** — which currently
renders only for non-standard tuning (`TabNotation.tsx:142`). Wonderwall is standard tuning, so
without this it would show no setup information at all, which is the exact failure Fret described.

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
| 5 | `server/app/ai/breakdown.py` SYSTEM_PROMPT | teach the new vocabulary | no, but the schema buys nothing until it happens |
| 6 | `server/scripts/fle102_tab_audit.py` | drop its private `DUR` for the §2.2 table | no — drifts |
| 7 | `server/scripts/grade_eval.py`, `grade_cached_drills.py` | their own measure parsers | no — ignore new fields |
| 8 | `server/data/verified_tabs/pilot_verified_set.json` | parses unchanged — all 1612 notes are `quarter`, verified; the four swing/bossa songs should be re-notated once Q1 is answered | no |

On #8: the pilot set is **1612 quarter notes and nothing else**. That is not a criticism of it — it
is the measured form of FLE-103 §4's own admission that it verified pitch and not feel, and it is
what the v1 schema permitted. It does mean the committed ground truth currently encodes zero rhythm,
so it cannot catch a rhythm regression until Q1 is answered and the swing/bossa four are re-notated.

**One flag on #5 that is not mine to wave away.** `breakdown.py:162` says of `SYSTEM_PROMPT`: *"this
string is the prompt the 04.1-07 eval graded, so edits invalidate the eval."* Teaching the new
vocabulary necessarily edits it. The FLE-17 §3 eval baseline is invalidated by this work, and
whoever owns FLE-106 should plan a re-run rather than discover it.

---

## 11. Open questions for Fret

Four musical calls I should not make alone. Everything above is settled engineering; these are not.

**Q1 — swing and shuffle: notate it, or mark it?**
The two honest options are (a) write it literally as `triplet-quarter` + `triplet-eighth` pairs, or
(b) add `Tab.feel: Literal["straight","swing","shuffle"] = "straight"` and keep writing eighths, the
way a real chart writes "swing 8ths" at the top. (b) is how charts are actually read and is one
token for the model instead of a per-note transformation it will apply inconsistently; (a) is
unambiguous to the renderer and to any playback. This decides *Pride and Joy*, *Sweet Home Chicago*,
*Folsom Prison Blues*, and the four swing/bossa songs you flagged as "correct as pitch, wrong as
feel" in FLE-103 §4. **I lean (b). Your call — it is a teaching question, not a storage one.**

**Q2 — is the 0.25-semitone bend worth having?** I put the quarter-tone curl in §5's enum for the
blues vocabulary. If a player can't be taught the difference from a half-step on a screen, it is a
value the model will scatter at random and I would rather drop it than ship a field that only adds
noise.

**Q3 — confirm polyphonic voices are out of scope** (§2.4). v2 cannot write a held bass under a
moving melody: *Big Love*, *Blackbird*, the *Hotel California* 12-string reading. I believe that is
the right line for this change and a separate, larger one later. If instead it is the thing that
makes the fingerstyle end of the catalog teachable, say so now — it changes the shape of `Beat`, and
that is much cheaper to decide before implementation than after.

**Q4 — under a capo, what is `Chord.name`?** (§7.2) The Wonderwall shape is fingered Em7 and sounds
F#m7. My recommendation is `name` = the shape, plus an optional `sounding_name`. I have not added
the field pending your answer.

**Q5 — confirm the capo convention itself.** §7 adopts your capo-relative proposal as written,
including the two consequences you did not name: chord diagrams share the origin (§7.1.3), and the
capo badge must render independently of the tuning label (§9.2), or Wonderwall displays no setup
information at all.

---

## 12. Implementation split

Three child issues, to be cut **after** §9 and §11 are signed off, not before — they would otherwise
be implementation against an unreviewed contract, and Q1/Q3 both change what gets built.

1. **Server — schema + coercer + the canonical duration table.** `song.py`, with the table exported
   so the audit scripts can import rather than re-declare it. Unit tests: every cached duration
   round-trips; aliases canonicalise; unknown coerces to `quarter` with a warning and does not
   raise; `Fraction` meter sums exactly for a sextuplet bar; the §4 link checks; capo fret bounds.
2. **Mobile — renderer.** §9, in two commits: width-from-content first (it is a standalone fix for
   FLE-103 §1b and is worth shipping on its own), then glyphs.
3. **Verifier — capo, `Fraction`, bent-note pitch.** §10 row 4. Small, and it gates the pilot set
   staying green.

Prompt work (FLE-106) and the serving path (FLE-103 §6) consume this contract and are already
tracked elsewhere.
