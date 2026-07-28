# Phase 3 Discussion Log

**Discussed:** 2026-07-28
**Areas selected:** Song-of-Day selector · Rating UX + mastery math · Daily rotation trigger
**Areas deferred (Claude's Discretion):** Sonnet breakdown timing + caching

---

## Area 1 — Song-of-Day selector algorithm

### Q1: Primary selection rule
- Options presented: Weakest skill in working_on / Round-robin categories / Random within tempo bin / Aspiration-forward shortest path
- **User picked:** Weakest skill in working_on songs (recommended)
- → **D-01**: `argmin(mastery)` over `skill_nodes` joined to `working_on` songs; tiebreaker `updated_at ASC`

### Q2: Empty working_on handling
- Options presented: Cascade to can_play → aspirational → seed / Empty-state pushing to Settings / Cascade + soft banner
- **User answered (freeform):** "add a 0.25 chance of random from bank, maybe from bank according to player level, thats also the fallback"
- → **D-02**: 25% override on top of the deterministic pick, from bank; same bank-random is the empty-working_on fallback

### Q3: Re-roll policy
- Options presented: No re-roll / One re-roll per day / Unlimited until first rating
- **User picked:** One re-roll per day
- → **D-05**

### Q4: What is "the bank"?
- Options presented: Curated seed catalog / User's own aggregate / Hybrid (user's ∪ seed)
- **User picked:** Hybrid
- → **D-02** + **D-03**: hybrid bank; new `song_catalog` table for the seed half

### Q5: How is "player level" defined?
- Options presented: Mean mastery across leaf skills / Self-declared during onboarding / Auto-inferred from onboarding songs
- **User picked:** Mean mastery across all leaf skills (recommended)
- → **D-03**: `player_level = AVG(mastery over user's leaf skill_nodes)`; bank filtered to ±0.15 of this level

---

## Area 2 — Rating UX + mastery math

### Q1: Rating UI shape
- Options presented: 3-tier Fletcher pills / 5-tier stars / 0–100 slider / Binary
- **User picked:** 3-tier Fletcher-voiced pills (recommended)
- → **D-06**: "Not my tempo" / "Getting closer" / "That's what I'm looking for"

### Q2: Which nodes update per rating?
- Options presented: All attached skills equal-weight / Only the "primary" trigger skill / All attached skills weighted by song_skills.weight
- **User picked:** All attached skills, equal-weight (recommended)
- → **D-07**

### Q3: Mastery math
- Options presented: Fixed additive / EMA α=0.3 / Multiplier + cap / Fixed + streak bonus
- **User picked:** Fixed additive (recommended)
- → **D-08**: `-0.05 / +0.05 / +0.15` clamped [0, 1]

---

## Area 3 — Daily rotation trigger

### Q1: When does today's song expire?
- Options presented: Midnight + rating required / Midnight unconditionally / 24h from selection / Only after rating
- **User picked:** Local midnight, unconditionally
- → **D-09**

### Q2: Timezone source
- Options presented: Device timezone / Onboarding-declared / Server UTC only
- **User picked:** Device timezone at first launch each day (recommended)
- → **D-10**

---

## Claude's Discretion (planner decides — flagged for visibility)

- **Sonnet breakdown timing = lazy on first tap, cache-forever per song** — reasoning in CONTEXT.md `<decisions> → Claude's Discretion`
- Sonnet call file: `server/app/ai/breakdown.py`, function signature mirrors `run_onboarding_parse`
- Fletcher loader copy for breakdown: 3-message rotation reusing 02-04's pattern
- Rating write endpoint: `POST /api/v1/sessions`
- Player level cached in `users.preferences` JSONB, recomputed on Song-of-Day request

## Scope creep redirected
- None flagged during discussion — user stayed within phase boundary throughout

## Deferred ideas captured
- Weighted `song_skills.weight` (needs Phase 2 wizard reopen)
- Streak bonus on mastery
- Multiplier mastery math
- Slider / 5-tier rating UI
- Sonnet breakdown pre-generation at midnight
- Breakdown regeneration
- Multi-song days
- Skip-without-rating penalty
- Cross-user song_catalog contributions
- Song lyrics / chord charts beyond semantic breakdown
- Practice timer / session length enforcement

See `03-CONTEXT.md` `<deferred>` for full list with reasoning.
