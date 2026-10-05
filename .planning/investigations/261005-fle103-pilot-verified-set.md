# FLE-103 — the hand-verified pilot set

**Fret (Guitar Curriculum Lead), 2026-10-05.** Phase C(a) of the trainer-quality plan.
Sequenced behind FLE-102 (`t-tab-taxonomy`), which is done.

Artefacts:

- `server/data/verified_tabs/pilot_verified_set.json` — the committed ground truth, in the real
  `Tab` schema.
- `server/scripts/fle103_build_pilot_set.py` — builds it from curated chord tables.
- `server/scripts/fle103_verify_pilot_tabs.py` — checks it. **Read-only, no LLM, no DB, no spend.**

**Result: 10 songs, 80 measures, 80/80 passing eight independent checks.** But the deliverable is
not shaped the way the issue assumed, and the reason matters more than the set does.

---

## 1. Three findings that reshape the task

### 1a. There is no stored tab to hand-verify

`song_catalog.breakdown` is **NULL on all 64 rows** (prod, read-only, 2026-10-05). The catalog holds
title/artist/genre/skill-root/difficulty/tuning and nothing else. Tab exists only in `songs` rows,
minted per user per day by the generator — 48 rows, 21 distinct titles, of which **11 actually
carry a `tab` key**.

So "hand-verify the tab for each of the 10 pilot songs" has no object. For 54 of 64 songs there is
no tab to check. For the rest, the tab I could check is *one user's instance from one day* — the
next request regenerates it. FLE-102 already measured that corpus: **0 of 10 fit to practise from**,
and the four newest were no better than the oldest, so this is current generator quality rather than
legacy debt.

**Verifying generated tab cannot make the pilot safe, because the artefact is regenerated after I
verify it.** The only thing that makes a tab trustworthy before strangers see it is *serving tab a
human committed*. That is what this issue's option (a) actually requires, and it is a production
change — spec'd out in §6, not made here.

### 1b. The renderer drops rhythm, and corrupts any measure wider than four beats

> **FIXED 2026-10-05 — read this section as history, not as current state.** Filed as FLE-111 and
> closed by `0b8f2ee` (FLE-116): `MEASURE_WIDTH` no longer exists, `columnWidth()` sizes each column
> from its own duration, and `measureWidth()` sums the real content. The measurement below was right;
> the diagnosis finished with FLE-116 and FLE-117 lifting the four-beat convention out of ground truth.
> **Everything this section was used to justify needs rechecking, and two things did not survive it:**
> filter F1 in §2 (expired — see the note there) and the feel caveat in §4 (its premise is now false,
> and retiring it uncovered three swing tunes in this set carrying no marking at all).

`mobile/src/components/TabNotation.tsx` **never reads `note.duration`.** Not once in 236 lines. Every
beat is a fixed 48px column. So:

- Rhythm is not merely mis-notated (FLE-102's T9, 58/72 bars) — it is **never displayed at all**.
  "Correct rhythm", which this issue asks me to verify, is currently unobservable in the product.
- Conversely, writing correct triplets into ground truth would change nothing a user sees.

Worse, the width constant is hardcoded to four beats while beats lay out unbounded:

```
MEASURE_WIDTH = LEFT_MARGIN + 4 * BEAT_WIDTH + 8     // = 230
note x         = i * MEASURE_WIDTH + LEFT_MARGIN + beatIndex * BEAT_WIDTH + BEAT_WIDTH/2
```

For an 8-beat measure the overflow is exact:

| measure 0, beat | x | renders inside | collides with |
|---|---|---|---|
| 4 | 246 | measure 0 | — |
| 5 | 294 | **measure 1** | measure 1 beat 0 (x=284) |
| 6 | 342 | **measure 1** | measure 1 beat 1 (x=332) |
| 7 | 390 | **measure 1** | measure 1 beat 2 (x=380) |

10px apart, with glyph boxes 12–16px wide — direct overlap. **31 of the 72 cached prod bars (43%)
are over four beats**, concentrated in exactly four tabs where it is total:

| tab | beats per measure | over 4 |
|---|---|---|
| Big Love | 8,8,8,8,8,8,8,8 | 8/8 |
| Eruption | 8,8,8,8,8,4,8,8 | 7/8 |
| Pride and Joy | 8,8,8,8,8,4,8,8 | 7/8 |
| Lenny | 6,6,6,6,6,6 | 6/6 |
| Cause We've Ended as Lovers | 4,5,4,3,5,5,4,3 | 3/8 |

Those four tabs are **visually corrupt on the device regardless of musical correctness** — fret
numbers from one bar printed on top of the next. This is recording-free and provable, and it is a
separate defect from everything FLE-102 named. It is also why every measure in my set is capped at
four beats: a constraint imposed by the client, not a musical choice.

(Also found: *No Woman, No Cry*'s cached breakdown has a `tab` key holding **zero measures** — an
empty staff.)

### 1c. `rights_status` is not in prod, and could not rank the roster anyway

The issue describes the catalog as "64 rows, `rights_status` tagged per FLE-51". Two corrections:

- **The column does not exist in production.** `alembic_version` reads `0015`, and
  `0015_song_catalog_rights_status.py` is an unconditional `ALTER TABLE ... ADD COLUMN`. Prod is
  stamped at a migration whose effects are absent. Schema-drift bug, filed out.
- **Even applied, it cannot select a pilot roster.** The migration's own map is
  `copyrighted: 60, public_domain: 2, traditional: 1, unknown: 1`. A field that holds one value for
  94% of rows cannot rank anything. It also answers a different question by design (per its
  docstring: "is a user's own clip postable", a v3 concern) — not "may we teach this".

So rights played no part in selection beyond a tiebreak toward *Canon in D* (`public_domain`). I did
not treat it as a gate, because we generate our own tab and distribute no licensed transcription.

---

## 2. How I chose the ten

Five filters, applied in order.

**F1 — the material must survive the renderer.** String + fret, in order, with no duration and no
articulation, must still be a usable instruction. This eliminates most of the catalog's top end:
bends (*Comfortably Numb*, *Europa*, *Sultans of Swing*, *Crazy Train*), slide (*Statesboro Blues*,
*Death Letter*), tapping (*Eruption*, *Thunderstruck*), tremolo (*Recuerdos de la Alhambra*), picado
(*Entre Dos Aguas*), independent fingerstyle voices (*Blackbird*, *Windy and Warm*), and every
shuffle (*Pride and Joy*, *Sweet Home Chicago*, *Folsom Prison Blues*) — a shuffle rendered on a
straight grid is a different rhythm, and the renderer shows no rhythm to correct.

> **F1 EXPIRED 2026-10-05.** This filter was a statement about the renderer of 2026-10-04, and
> FLE-111/FLE-116/FLE-117 have since answered every clause of it: durations size the columns (§9.1),
> the v2 glyphs cover articulation, `capo` exists (§7), and `feel` is marked and drawn (§2.5/§9.2).
> The shuffle exclusion in particular is now wrong on its own terms — `tab_feel: "shuffle"` is a legal
> marking that renders. **So the selection this document defends is narrower than today's product
> requires, and §5's "the top of this catalog is unteachable" is the part of this writeup most likely
> to be out of date.** Re-running F1 against the current renderer is the obvious way to widen the set
> past ten and past six-of-ten jazz and classical; it is not in this issue's scope and is the first
> thing to pick up if Hernan's answer on §5 is "widen it". What has NOT changed is F2: a renderer that
> can draw a bend does not make me able to certify one cold.

**F2 — I must be able to certify it cold.** No song where I would need the record to publish a
bar-by-bar reading. This is why *Everlong* is out despite being renderer-safe and drop-D (I am not
confident of its chart without hearing it), and why *Kashmir* is out (I know the riff is a chromatic
ascent in 3-against-4, not FLE-102's invented D5–G5–A5 vamp, but I would want the record before
committing bars). Contested tuning tags are also out: *Hey Joe*, *Crazy Train* and *Tears Don't
Fall* are all tagged in a tuning I cannot confirm.

**F3 — every chord quality must be checkable without a recording.** Each song declares its chords,
so the voicing can be checked against the name by arithmetic. This is the lever against FLE-102's
two most common defects — T1 mode flip (3 songs) and T2 chord quality never sounded (6 songs, only
15 of 72 bars contained *any* third).

**F4 — the selector window must stay fed.** `today_song.py` filters the catalog by
`ABS(difficulty - player_level) <= 0.15`, floored at 0.20 (`player_level.py`). If the pilot sees only
these ten, every player_level a pilot user can occupy needs at least one — ideally two, since reroll
(FLE-98) must return something different.

**F5 — spread the traps.** Minor tonics, a major-seventh dominant inside a minor key, an altered
dominant, a tritone sub, and barre positions where a one-fret slip changes the chord.

### The ten

| # | song | diff | key | the trap it sets |
|---|---|---|---|---|
| 1 | A Horse with No Name | 0.19 | E minor | simplest possible minor-tonic spelling (Em needs G natural) |
| 2 | Zombie | 0.28 | E minor | i–VI–III–VII; four chords, four thirds |
| 3 | Ain't No Sunshine | 0.36 | A minor | two different minor chords in one passage |
| 4 | Canon in D | 0.43 | D major | two barre minors — a one-fret slip is T3 |
| 5 | Autumn Leaves | 0.47 | G minor | m7 / dom7 / maj7 / m7b5 in eight bars |
| 6 | Take the 'A' Train | 0.55 | C major | D7#11 — drop the #11 and it's the wrong tune |
| 7 | Blue Bossa | 0.58 | C minor | minor ii–V–i a third from Autumn Leaves' |
| 8 | The Girl from Ipanema | 0.60 | F major | Gb7 tritone sub — unreachable by guessing from the key |
| 9 | Hotel California | 0.65 | B minor | **Bm needs D, F#7 needs A#** — opposite directions, same 8 bars |
| 10 | Giant Steps | 0.95 | modulating | three key centres in eight bars |

**#9 is the centrepiece.** FLE-102's most common defect was a major third where the minor belongs.
*Hotel California* demands both at once: a minor tonic (Bm, D natural) and a major-seventh dominant
(F#7, A# — *not* A) one bar apart, plus Em and E major two bars apart. A generator with one borrowed
scale cannot pass it in both directions.

**#7 is paired with #5 on purpose.** Two minor ii–V–i cadences a minor third apart. A generator
sliding one house pattern around the neck (FLE-102's T7) produces an exact shape match between them.

---

## 3. Verification: 80/80, and my own error caught

`fle103_verify_pilot_tabs.py` is deliberately **ignorant of the chord shapes**. It reads only
`{string, fret, duration}` plus the declared chord *name*, re-derives every pitch from the tuning
array, and asks whether the sounding notes spell the chord claimed. The builder knows the shapes;
the verifier does not. Do not merge the two files — that separation is the whole point.

| check | what it catches |
|---|---|
| V1 foreign note | a sounding pitch class outside the declared chord |
| V2 third present | FLE-102 T2 — the defect in 6 of 10 cached tabs |
| V3 characteristic tone | the b5 of a m7b5, #11 of a 7#11, b9 of a 7b9, maj7 of a maj7 |
| V4 root present | — |
| V5 meter | beat durations sum to the time signature (T10), exactly, under `Fraction` |
| V6 renderer parity | ~~≤ 4 beats per measure (§1b)~~ → the renderer's duration table matches §2.2, and the rhythm §9.1 draws is distinguishable (rewritten by FLE-117 once §1b was fixed) |
| V7 one note per string | two notes on one string in one beat is unplayable |
| V8 fret span | fretted span ≤ 5 within a beat — one hand position |
| V9 feel marking | **added 2026-10-05** — `tab.feel` present, legal, agreeing with the prose, and not double-applied as triplet pairs (C8). Unit: the song. It fails all ten songs on the pre-V9 file; see §4. |

**Result: 80 measures, 80 carrying a declared chord, all checks pass** — including V9 as of `10a1fe1`.
V1–V8 were green before the feel fix and are byte-for-byte unaffected by it: that commit touched no
measure data, so the pitch verification below stands exactly as first recorded.

### What I corrected

**My own D7#11 was wrong, and the verifier caught it.** I first wrote the *Take the 'A' Train* chord
as `5:5 4:4 3:5 2:6` and recorded in my notes that `2:6` was the G# — it is **F**. B-string fret 6
is F; G# is fret 9. The voicing I was about to publish had no #11 at all and a foreign F, i.e. it
was a D7#9. Corrected to `5:5 4:6 3:5 2:7` — root, #11, b7, third from the bottom, span 2 frets.

**Second entry, 2026-10-05: three swing tunes carrying no feel marking.** Not an arithmetic slip this
time but an omission, and a worse class for it — the D7#11 was caught before publication by a check
that existed, while this one *was* published and sat in ground truth until an unrelated renderer fix
made it visible. Nothing was red, because `Tab.feel` defaults to `straight` and a default is not a
disagreement. V9 now exists so that silence is no longer available: see the §4 revision.

I am recording these prominently rather than quietly fixing them. The first is the same class as
FLE-102's T1/T4 — a confident hand writing a shape whose arithmetic it did not check — produced by
the person hired to catch that error, on the fourth chord of ten songs. The second is the class
FLE-102 never had a name for: **a field that is true as data and misleading as instruction**, which is
also what `tuning: standard` does to a capo song. **No ground truth should be committed on anybody's
ear alone, mine included — and no field that reaches a player should be allowed to default.** That is the argument for the verifier existing,
and for FLE-107 scoring by diff rather than by judgement.

No other corrections were needed: the remaining nine songs' voicings passed as first written.

### Corrections to inherited claims

- **FLE-102 said the cached corpus is 10.** By my count it is **21 distinct titles across 48 rows,
  11 of them carrying a `tab` key.** FLE-102's ten is the set with usable tab minus *No Woman, No
  Cry*, whose tab is an empty array. Its verdicts stand; the denominator is just narrower than the
  corpus.
- **FLE-102 said 2 of the cached songs are off-catalog.** It is **6** — *Beat It*, *Big Love*
  (under both "Lindsey Buckingham" and "Fleetwood Mac"), *Cause We've Ended as Lovers*, *Layla*,
  *Sweet Child O' Mine*. FLE-108 already covers the mechanism.
- **FLE-102 gave Chan Chan's progression as Dm–Fm–Gm–A7.** I read the second chord as **F major**,
  not Fm. Neither of us should publish it without the record; *Chan Chan* is not in my set.

---

## 4. What I could not verify

Per the honesty requirement, stated rather than folded into the pass rate.

**Set-level, and the most important one: I verified pitch, not feel.** Five of the ten are swing or
bossa (*Autumn Leaves*, *Take the 'A' Train*, *Giant Steps*, *Blue Bossa*, *Ipanema*). I committed
them as quarter-note chord stabs, which is correct as **pitch** and wrong as **feel**.

> **REVISED 2026-10-05, after FLE-111/FLE-116 — and the revision found a defect in this set.**
> This paragraph used to continue: *"Because the renderer displays no rhythm (§1b), the feel cannot
> be delivered to the player or scored against the generator today."* That was true when written and
> is now **false**. §9.1 sizes columns from `note.duration` and §9.2 draws a feel badge on the setup
> line, so the product gap closed — and closing it exposed that **this file's ten tabs were not
> carrying a feel at all.** `Tab.feel` defaults to `straight`, this set declared feel only as a
> per-song prose string nothing downstream reads, and so *Autumn Leaves*, *Take the 'A' Train* and
> *Giant Steps* validated clean, loaded clean, and would have been drawn for a pilot player with no
> swing marking: told, in the only language the product speaks, to play Coltrane's changes dead even.
> Nothing was red, because a default is not a disagreement. Same shape as `tuning: standard` on a
> capo song (§7) — true as data, misleading as instruction, and produced here by the person hired to
> catch it. Second entry in this document's "no ground truth on anybody's ear alone" column.
>
> Fixed in `10a1fe1`: `tab_feel` is declared per song and emitted into `tab.feel`, and **V9** fails
> the run if it is absent, illegal, contradicts the prose, or is double-applied as triplet pairs. V9
> fails all ten songs on the pre-fix file. Each song's provenance `sha256` moved, so FLE-124's prod
> load must run against the current file.
>
> *Giant Steps* was also missing from this paragraph's own count — the same four-vs-five slip
> corrected on FLE-105 Q1.

What remains after that fix is narrower and real: the **ratio** is now marked (`swing`, with the
player supplying the degree from the tempo — FLE-105 Q1), but the **comping rhythm** is not. These
are quarter-note stabs on all four beats, where a medium-swing comp anticipates and leaves holes.
Bossa is worse off: it is correctly marked `straight` because it is even-eighth music, but its groove
*is* the syncopation, and the syncopation is not here. FLE-107 re-notates these. I did not count any
of it against the generator. **A player practising these five from the screen alone gets the changes
and not the groove.**

**I did not hear any of the ten against a recording in this pass.** These readings come from
knowing the tunes. For these ten that is a defensible basis — nine are standard repertoire whose
changes are not in dispute — which is exactly why I chose songs where it is defensible (F2).

Per song:

- **Zombie** — whether the recorded voicing is D/F# or plain D. Both are correct harmony; scored as
  D major either way.
- **Ain't No Sunshine** — not a guitar record. The harmony is certain; whether it should be *taught*
  as a guitar song is a curation call for Junie/Nadia, not a tab question. (Same flag on
  *Superstition*, a clavinet riff, and *Jerry Was a Race Car Driver*, a bass tune — both still in
  the 64 and both teaching the wrong instrument.)
- **Autumn Leaves / Blue Bossa / Ipanema** — the swing and bossa feel, as above.
- **Blue Bossa** — charts vary between G7alt / G7b9 / G7#5 in bar 6. I committed G7b9; any altered
  G dominant should score correct, a plain G7 should not.
- **Hotel California** — the record is a 12-string arrangement across two guitars. I committed the
  single-guitar chord reading, which is the correct harmony and how it is taught; a 6-string
  transcription cannot be note-for-note identical here.
- **Giant Steps** — some fake books split bar 6's Ebmaj7. Harmonically identical either way.

**A gap I found but did not fix: there is no capo field.** *Wonderwall* is played in standard tuning
with a capo at 2. Our schema has `tuning` but nothing for capo, so the shapes and the sounding
pitches cannot both be expressed — emit the shapes and the pitches are wrong by two semitones, emit
the pitches and the player is handed unidiomatic barres. I excluded *Wonderwall* for this reason and
noted it on FLE-105 rather than working around it.

---

## 5. The limitation Hernan should see: the top of this catalog is unteachable today

The coverage arithmetic, from the verifier:

```
no dead windows in [0.20, 1.00]
THIN  player_level 0.75-0.79: only 'Hotel California'  — no reroll variety
THIN  player_level 0.80-1.00: only 'Giant Steps'       — no reroll variety
difficulties: [0.19, 0.28, 0.36, 0.43, 0.47, 0.55, 0.58, 0.60, 0.65, 0.95]
```

No pilot user can hit an empty window, so the app never dies. But above 0.75 the set is one song
deep, and that is not a gap I can close by picking harder songs. **Above roughly 0.65, everything in
this catalog is difficult precisely *because* of articulation** — bends, slide, tapping, tremolo,
independent voices — and articulation is exactly what the schema omits and the renderer discards.
The hard end of the catalog is not hard in a way we can currently express.

Two consequences, both of which are product decisions rather than mine:

1. **Genre skew.** The renderer-safe, certifiable subset is overwhelmingly chord-changes music —
   six of my ten are jazz or classical standards. Fletcher's promise is "one real song a day,
   decomposed, so practising feels like playing," and the roster is being recruited as serious
   intermediate/advanced rock players. *Giant Steps* and *Canon in D* are real songs honestly
   taught, but they are not what that player signed up for. **The part of the catalog the pilot was
   recruited for is the part we cannot render.**
2. **Seeding.** While the pilot is restricted to this set, a user seeded above ~0.70 sees one song
   with no reroll — the FLE-49 collapse in miniature. My recommendation is to cap seeded
   `player_level` at 0.70 for the pilot; that is a spec, filed out, not an edit.

My recommendation: ship the pilot on these ten, with the skew stated to the roster up front
("ten songs we have checked by hand"), and treat FLE-105 as the gate on widening it. I would rather
hand five guitarists ten songs that are right than sixty-four that are not. But the skew changes
what v1 feels like, so it goes to Hernan through Miagi rather than being settled here.

---

## 6. Specs out, and why the pilot set is not the regression set

> **ALL THREE SPECS BELOW ARE CLOSED as of 2026-10-05.** FLE-110 (serve the verified tab, `26428d2`),
> FLE-111 (renderer width, fixed by `0b8f2ee`/FLE-116), FLE-112 (prod drift — only 0015 was actually
> missing). The capo note went into FLE-105 and shipped as `Tab.capo` in FLE-117. Everything this
> issue delegated has landed in the repo; what has **not** happened is the prod rollout, which is a
> deploy behind the closed FLE-88/FLE-89 beta gate and is filed as **FLE-124** (`backlog`, human-only).
> FLE-124 must load the *current* file: every song's provenance `sha256` changed with the feel fix.
>
> One consequence worth stating plainly, because it is the thing this section was written to prevent:
> **the ten are now genuinely servable, so "checked but still served generator output" is no longer
> true in code — only in prod.**

**To Ripley/Kaya — serve committed tab (the actual "done when" of this issue).** Marking ten songs
verified is only half of it; something must *serve* the verified tab instead of generating one. A
`verified_tab` column or join table on `song_catalog`, the selector restricted to rows that have
one while a pilot flag is set, and the breakdown path short-circuited to the stored tab. Until that
exists, these ten are checked but the pilot is still served generator output. Also: cap seeded
`player_level` at 0.70 (§5).

**To Kaya / mobile — `TabNotation` measure overflow** (§1b). Either clamp beats per measure or
compute width from the actual beat count. 43% of cached bars render on top of their neighbour today.

**To Kaya — prod schema drift** (§1c). `alembic_version` = 0015 with `rights_status` absent.

**On FLE-105** — add capo to the articulation gap (§4).

**FLE-107 stays blocked, and the two sets must stay different.** FLE-107 wants a regression set
deliberately spanning the failure modes — a shuffle, a bend-led melody, a fingerpicked pattern, an
altered tuning. That set *must* contain the hard cases; that is its whole function. This set
*excludes* them, because we cannot render them correctly yet. **Opposite requirements, so they are
not the same set, and finishing this one does not unblock that one.** Writing shuffle or bend ground
truth still needs the schema (FLE-105) — otherwise the ground truth is written in the same lossy
form as the thing it is meant to catch.

What this set *does* give FLE-107 immediately: a scoring harness and 80 machine-checked bars for the
defect classes that need no articulation — T1 mode flip, T2 missing third, T3 position, T4 wrong
voicing, T10 bars that don't add up. Those five are checkable today against committed ground truth.
The articulation classes are not.

**One warning for FLE-106 (Doro).** The verifier reports cross-song identical measures and
explicitly does **not** fail on them: *Cm7* legitimately appears in both *Autumn Leaves* and *Blue
Bossa*, *F#7* in both *Ipanema* (as Gb7) and *Hotel California*. FLE-102's T7 template-reuse
heuristic would flag all of these as defects. **T7 must stay a human prompt and must never become
an automated gate** — on this set it is a 100% false-positive rate.
