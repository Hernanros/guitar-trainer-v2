---
type: investigation
task: 16
created: 2026-09-08
status: findings-complete
next_step: implementation-scope-decision
---

# Investigation: Sonnet-generated tab wrong for songs in non-standard tunings

**Trigger:** User reported (2026-09-08, EAS build a08e7f3b) that the tab rendered for "Lenny" by Stevie Ray Vaughan was wrong.

## Evidence

**Song #60, Lenny by SRV, breakdown generated 2026-09-08 12:53 UTC:**

```
tuning: ['E', 'A', 'D', 'G', 'B', 'e']   ← STANDARD tuning
chords: ['E (open)', 'E7 (open)', 'A (open)', 'A7 (open)', 'B7 (open)']
technique_notes: 5 (high quality — SRV-specific style, b3→3 slides, fingerstyle attack)
tab: 8 measures, first beat = open low E (walking bass)
```

**Ground truth:** Lenny is played in **Open E tuning** (E B E G# B E, low to high — every string except low E is tuned up from standard). The entire song is arranged around Open-E chord voicings and fingerpicked figures unique to that tuning. The actual intro is a Cadd9/Bm-flavored fingerpicked pattern, not a walking-bass I-IV-V progression.

## Diagnosis

**Sonnet made three implicit choices without labeling them:**

1. **Chose to render in standard EADGBE tuning** despite Lenny being famously Open E
2. **Chose to translate the arrangement** into a simplified I-IV-V blues-in-E progression (E, E7, A, A7, B7)
3. **Did not include a tuning technique note** — user has no way to know Sonnet made a translation choice

**The teaching content itself is excellent** — technique notes about SRV's b3→3 slide, walking bass with thumb, tempo discipline, fingerstyle attack are all accurate and useful. But the tab notation is a **hidden simplification**, not a faithful transcription.

**Why this matters:** An intermediate/advanced player (Fletcher's target audience per PROJECT.md) who KNOWS Lenny is Open E will:
- Try the tab, get wrong-sounding notes (E-tuned chord shapes don't work in Open E)
- Assume Fletcher's AI is unreliable
- Lose trust in the app for the whole "one real song, decomposed" premise

## Root cause — the prompt

`server/app/ai/breakdown.py:39` and `:71`:
> "Produce accurate, hand-notated tab in standard notation (E A D G B e tuning **unless otherwise specified**). Tab uses string numbers 1-6 where 1 is high e."
> "standard tuning **unless the song calls for otherwise**"

The "unless" escape hatch exists, but:
- No explicit instruction to IDENTIFY the song's canonical tuning first
- No examples of alt-tuning songs
- No mandate to include a tuning technique note when non-standard
- No mandate to emit the `tuning` array reflecting the chosen tuning

Sonnet defaults conservatively to standard tuning because the prompt doesn't push it to consider tuning as a first-class decision.

**Schema check:** `Tab.tuning: list[str] = ["E", "A", "D", "G", "B", "e"]` — accepts any 6-string tuning array. No schema change needed.

## Recommended fix (multi-pronged)

### Prompt fix (server, highest leverage)

Add to `SYSTEM_PROMPT` in `server/app/ai/breakdown.py`:

```
TUNING (CRITICAL — DO NOT SKIP):
Before writing any tab, identify the song's canonical/authentic tuning based on
what a competent guitarist would recognize. Common non-standard tunings include:

  - Open E:     ['E', 'B', 'E', 'G#', 'B', 'E']   (Lenny, She's a Woman)
  - Open D:     ['D', 'A', 'D', 'F#', 'A', 'D']   (Little Martha, Statesboro Blues)
  - Open G:     ['D', 'G', 'D', 'G', 'B', 'D']    (Start Me Up, Brown Sugar)
  - DADGAD:     ['D', 'A', 'D', 'G', 'A', 'D']    (Kashmir, Black Mountain Side)
  - Drop D:     ['D', 'A', 'D', 'G', 'B', 'E']    (Everlong, Moby Dick)
  - Drop C:     ['C', 'G', 'C', 'F', 'A', 'D']    (metal, hardcore)

Emit the `tab.tuning` array reflecting your choice. If the song is in a non-
standard tuning, ALSO include one technique note titled "Tuning: <name>" that
explains which strings to retune and by how many half-steps. Example for Open E:
"Tune your A, D, and G strings UP by a whole step; leave low E, B, and high E
alone. Your D becomes E, A becomes B, G becomes G#."

Prefer authentic tuning over simplified translations. If you MUST translate to
standard tuning for pedagogical reasons (e.g., beginner user_level), label it
explicitly in a technique note: "Simplified arrangement — original in Open E".
```

### UI fix (mobile)

`mobile/src/components/TabNotation.tsx`:
- Read `tab.tuning` from props
- Compare against standard `['E', 'A', 'D', 'G', 'B', 'e']`
- If different: render a compact label above the tab grid: `Tuning: Open E — E B E G# B E`
- Uses existing typography tokens; no design system work needed

### Optional: Sonnet output validation (out of scope for this investigation)

For a curated set of known-tuning songs (Lenny, Kashmir, Blackbird, Everlong, etc.),
post-generation check whether Sonnet's `tab.tuning` matches ground truth. On
mismatch, log warning + optionally auto-retry with the corrective hint.

This is eval-loop territory — better handled as part of Phase 5 Polish or a
dedicated AI-Quality phase after user has more device miles.

## Scope + placement

**Not a hotfix** — Lenny renders content that isn't dangerous, just wrong-fidelity.

**Right container:** Phase 5 "Polish" or a new dedicated AI-Quality slice. Given
the user reports "core loop works end-to-end" as of 2026-09-08, this is a next-
round quality investment, not a P0.

**If shipped as a Phase 5 slice:**
1. Server: prompt update + one regression test (Lenny → tuning=Open E)
2. Mobile: TabNotation tuning label + one prop test
3. Regen existing breakdowns (or wait for cache expiration)
4. Manual eval: pick 5 known-alt-tuning songs, verify Sonnet gets tuning right after prompt update

## Also flagged: sparse skill graph

Related quality finding from same session: user's re-run onboarding produced 6 roots + 11 sub-domains + only **5 leaf skill_nodes** across 8 songs (13 song_skills junction rows). Song-of-day variety is limited by leaf count. This is a separate Sonnet onboarding prompt gap — not tuning-related but same class of issue (Sonnet under-producing in a domain where more is better).

Filed as its own follow-up under Task #16's ambit or a new Task #18.
