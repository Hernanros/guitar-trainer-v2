# Phase 2: Onboarding & Initial Skill Graph - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-20
**Phase:** 2-onboarding-skill-graph
**Areas discussed:** Onboarding UX shape, Song Input UX, Skill Graph Seeding, Session Preferences

---

## Gray Area Selection

Four gray areas surfaced; user selected three (multi-user seams was resolved as side-effect of identity choice, not discussed as its own area).

| Gray Area | Description | Selected |
|-----------|-------------|----------|
| Onboarding UX shape | Conversational vs sectioned wizard vs standard wizard vs progressive disclosure | ✓ |
| Song input UX | Free-text + Sonnet vs autocomplete-DB vs manual vs hybrid | ✓ |
| Skill graph seeding strategy | Fully LLM vs fixed roots vs emergent vs hybrid curator | ✓ |
| Multi-user seams depth | Hardcoded / users+header / stub JWT | *(resolved side-effect)* |

---

## Onboarding UX shape

### Q1: What's the top-level shape of the onboarding flow?

| Option | Selected |
|--------|----------|
| Sectioned wizard — Fletcher intros each section, standard UI captures data | ✓ |
| Pure conversational — chat-like, Fletcher asks each question | |
| Standard wizard — progress bar + form screens + Fletcher only at intro/outro | |
| Progressive disclosure — single scrollable screen | |

**Choice:** Sectioned wizard (Recommended). Locked as D-02 in CONTEXT.

### Q2: How should the sections be structured?

| Option | Selected |
|--------|----------|
| 4 sections: Play / Working on / Aspire / How you practice | |
| 3 sections: Your songs / Style + preferences / Confirm | |
| 5 sections: Welcome / Play / Working on / Aspire / Preferences | ✓ |
| Songs as one continuous screen + preferences separately | |

**Choice:** 5 sections. Adds a dedicated Fletcher-welcome section as the joke-landing moment. Locked as D-01.

### Q3: What happens if the user quits mid-onboarding and comes back?

| Option | Selected |
|--------|----------|
| MMKV-only persistence, server touch on final Complete tap | ✓ |
| No persistence — restart from scratch on re-open | |
| Persist to server per section | |
| Persist to MMKV, explicit "save & exit" only | |

**Choice:** MMKV-only, server on Complete. Locked as D-03.

### Q4: How does the app track "is this user onboarded" — and what identifies the user for POC?

| Option | Selected |
|--------|----------|
| MMKV `onboarded_at` timestamp + device-generated UUID as user_id | ✓ |
| Server as source of truth — GET /api/v1/user/self returns onboarded status | |
| MMKV `onboarded: true` flag, no user identity at all | |
| Full users table + JWT stub even without login | |

**Choice:** UUID + MMKV `onboarded_at`. Locked as D-04. **Resolves the multi-user seams gray area deferred from Phase 1** — users table introduced, X-User-ID header, no auth.

---

## Song input UX

### Q1: How does the user tell Fletcher which songs they play?

| Option | Selected |
|--------|----------|
| Free-text, Sonnet parses songs + extracts skills in same call | ✓ |
| Autocomplete against a song DB | |
| Manual entry — title + artist fields per song | |
| Hybrid — free text → Sonnet canonicalizes → user confirms | |

**Choice:** Free-text + Sonnet. Cheap (~$0.02–$0.05 per onboarding), Fletcher-native voice fit. Locked as D-05.

### Q2: When does the Sonnet parse actually fire during onboarding?

| Option | Selected |
|--------|----------|
| Batch on Complete — one call after user finishes all sections | ✓ |
| Per-section parse — Sonnet fires when user taps Next on each section | |
| Async in background — fire per section, user moves on while it processes | |
| Deferred — no LLM during onboarding, run after user is in the app | |

**Choice:** Batch on Complete. Single failure surface, cheapest tokens, natural "Fletcher is thinking" beat on the final screen. Locked as D-06.

### Q3: What happens if the Sonnet parse fails on the Complete tap?

| Option | Selected |
|--------|----------|
| Retry once, then fail-open with a bootstrap graph | ✓ |
| Retry once, then block with clear error | |
| No retry, save raw text, background retry, user enters app | |
| Fail-fast, restart onboarding from Complete tap | |

**Choice:** Retry + fail-open. Saves raw text, marks onboarding complete with minimal bootstrap graph so user always enters the app. Locked as D-07.

---

## Skill graph seeding

### Q1: How bounded should the root+sub-domain taxonomy be?

| Option | Selected |
|--------|----------|
| Fixed root domains, Sonnet proposes sub-domains + leaves | ✓ |
| Fixed roots + fixed sub-domains, Sonnet only proposes leaves | |
| Fully emergent — Sonnet proposes everything including roots | |
| Hybrid — fixed roots + LLM proposes roots via curator queue | |

**Choice:** Fixed roots + Sonnet writes below. Proposed root vocabulary in CONTEXT: Rhythm, Lead, Chord Voicings, Fingerstyle, Music Theory, Timing. Locked as D-08.

### Q2: How is the skill graph stored in Postgres?

| Option | Selected |
|--------|----------|
| Normalized per-user tree — users + skill_nodes (parent_id + user_id) | ✓ |
| Canonical shared nodes + user_skill_mastery junction from day one | |
| JSONB blob — users.skill_graph as one big JSON | |
| Full DAG day one — skill_nodes + skill_edges tables | |

**Choice:** Normalized per-user tree. Defers multi-parent DAG + canonical shared nodes to Phase 4. Locked as D-09.

### Q3: How does the data model handle aspirational songs (the retention hook)?

| Option | Selected |
|--------|----------|
| Songs table with `category` enum + song_skills junction | ✓ |
| Aspirational lives on skill nodes as a flag | |
| No persistent song data — songs consumed at parse, only skills persist | |
| Songs table without junction | |

**Choice:** Songs table + junction. Also foundation for Phase 5 Library tab. Locked as D-10.

### Q4: How is initial mastery set on skill nodes after onboarding?

| Option | Selected |
|--------|----------|
| All mastery starts at 0; song category is metadata Fletcher uses for recommendation | ✓ |
| Provisional mastery from category, first rating overwrites | |
| Sonnet estimates mastery per song-skill during parse | |
| User self-rates each song's mastery at end of onboarding | |

**Choice:** All start at 0. Preserves "deterministic writes only, no LLM in write path" principle from PROJECT.md. Locked as D-11.

---

## Session preferences

### Q1: How does the user pick session length?

| Option | Selected |
|--------|----------|
| Preset chips: 15 / 30 / 45 / 60 min | ✓ |
| Slider 10–90 min in 5-min steps | |
| Free-text number field | |
| Two categories: 'Quick session' / 'Full session' | |

**Choice:** Preset chips. Locked as D-12.

### Q2: How is retention format captured?

| Option | Selected |
|--------|----------|
| Single-select from 3 options with Fletcher-voiced descriptions | ✓ |
| Multi-select — user can combine streak + weekly digest + monthly milestone | |
| Skip retention format for POC — always show all three surfaces | |
| Ask later — after 7 days, prompt with "which of these clicked for you?" | |

**Choice:** Single-select radio. Streak default if user picks nothing. Locked as D-13.

---

## Claude's Discretion

Areas the user did not select for granular discussion — Claude picks sensible defaults, documented in CONTEXT.md `## Claude's Discretion`:

- **Root domain vocabulary** — proposed: Rhythm, Lead, Chord Voicings, Fingerstyle, Music Theory, Timing (iterate in Phase 2 UI-phase)
- **Onboarding copy** — Fletcher voice per [[fletcher-identity]]; concrete strings in Phase 2 UI-phase
- **Character portrait per section** — deferred to UI-phase
- **Tempo bin representation** — columns on skill_nodes (tempo_bin_low/high), not separate table
- **Settings re-run behavior** — full wipe of songs + skill_nodes, preserve preferences
- **Empty section handling** — no minimums, soft nudge only if all 3 song sections are empty
- **X-User-ID header validation** — accept any well-formed UUID for POC; Phase 4 attaches rate limits
- **Existing songs table migration** — attach Wave 1 Sweet Home Chicago row to a bootstrap "system" user via Alembic revision 0002

## Deferred Ideas

Ideas raised during discussion but deferred to future phases (see CONTEXT.md `## Deferred Ideas`):
- Autocomplete-against-song-DB (MusicBrainz/Spotify) — v1.1+
- Hybrid input with user confirmation of proposed matches — v1.1+
- Cross-device onboarding continuity — deferred
- Push notifications for streak reminders — dedicated Notifications phase
- Skill graph re-run "diff" mode — polish
- Onboarding-time mastery self-rating — violates deterministic-writes principle
- Multi-parent DAG (canonical shared nodes) — Phase 4
- Full stub JWT — dedicated Auth phase
- Fletcher-conversational onboarding UI — v1.1+ experiment
