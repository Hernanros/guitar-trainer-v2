# Guitar Trainer v2 — Recovered Design

**Source:** Reconstructed from Claude Code session transcripts `36e2ccdf-2c9c-44c3-b05e-2da60dfb6dd1.jsonl` and `58adaa75-1cc8-4701-ac1c-f0722f0f69d8.jsonl` under `~/.claude/projects/-Users-hernanrosenblum-Documents-inplace-crm-mobile/`.

**Why this doc exists:** The prior session completed design but crashed (API safety classifier false positives during `/gsd-pause-work`) before writing a handoff. This preserves the recovered decisions so they survive future crashes.

---

## Vision (one line)

*A mobile AI teacher that knows what you played yesterday, what you're struggling with, and what to hand you today.*

---

## Why v2 (pain points that killed v1)

- Practice and real music were siloed — no visible connection between exercises and songs.
- **Daily retention hook failed** — users stopped opening it. "You stopped opening it" is v1's killer.
- Coach feedback was disconnected from actual songs.
- Session builder was friction, not payoff.

---

## Core reframing

**v1** was exercise-centric — a web app organized around a technique library.
**v2** is music-centric, mobile-first — real songs are the organizing principle. Practice is in service of songs the user actually wants to play.

---

## Locked decisions

### Product

- **Song of the Day** is the daily ritual — one piece of real music tied to current skill focus, decomposed by the AI teacher.
- **Serious players first** — intermediate/advanced guitarists.
- **Three tabs:** Today · Library · Toolkit (metronome + tuner + utilities).
- **Onboarding** — one-shot, 5–8 minutes: songs you can play (a few), songs you're working on, aspirational songs, style tags, session preferences. Re-runnable from Settings. No permanent onboarding tab.
- **Retention:** streak tracking, with weekly digest / monthly milestone alternatives for users who don't like streaks.
- **POC first** — single user, no auth, multi-user seams stubbed. Built for sharing day 1 but shipped as POC.

### MVP scope — "Approach A: Prove the loop"

Ship the tightest version of the song-of-day loop.

**IN:**
- Song of the day
- Tab + chord diagram output via SVG (react-native-svg)
- Self-report ratings after practice
- Skill graph updates from self-report
- Basic library browsing

**OUT (deferred to v1.1+):**
- Mic listening / DSP (world-class version deferred to v1.2+)
- Conversational coach chat
- Bidirectional discovery UI ("what do I need for Eruption?" / "5 songs for hybrid picking")
- Tab library / notation rendering beyond MVP SVG output
- Paywall (no monetization pre-PMF)
- Streaming integration (Spotify, etc. — becomes paid tier v1.1)
- Video / vision

### Stack

| Layer | Choice |
|---|---|
| Mobile | Expo + React Native 18 |
| UI state | Zustand |
| Server state | TanStack Query |
| Local storage | MMKV (offline reads + session cache) |
| Graphics | react-native-svg (tab + chord diagram rendering) |
| Backend | FastAPI on Railway |
| Database | Postgres |
| AI — router | Claude Haiku 4.5 |
| AI — teaching content | Claude Sonnet 4.6 |
| AI — fallback | Claude Opus 4.7 |

### Skill graph

- **Structure:** DAG, three levels deep. Root domains → sub-domains → concrete leaf skills.
- **Leaf skills** carry tempo bands split into 5-bpm bins (e.g., "Chicken pickin' patterns @ 110–140 bpm").
- **Writes:** deterministic rules from user self-report — no LLM in the write path.
- **Decay:** nightly job, 5% loss on nodes untouched >7 days.
- **New nodes:** embedded dedup + LLM verifier (Sonnet) before joining canonical graph. Uncertain proposals go to a curator queue.
- **Sources:** both users and AI can propose new nodes; verification required.

### AI cost guardrails (4 concentric layers)

- **Layer 0** — Anthropic Console hard caps: $20/month POC budget, billing alerts.
- **Layer 1** — Server-side spend governor: all LLM calls routed through a single module, logged pre-emptively.
- **Layer 2** — Per-user rate limits + feature caps (e.g., "Break down this song" capped at 3/week on free tier).
- **Layer 3** — User-facing alerts on remaining quota.

### Onboarding copy notes

- "Add a few songs" — guidance, not thresholds ("3–10" felt like a chore).
- Aspirational songs included — users respond to hooks like "you're 3 skills away from Eruption."

---

## The 5-stage plan

**Was never written.** The `writing-plans` skill was not invoked before the crash. The design above is what was locked; the phased execution plan is what still needs to be produced.

---

## State at crash

- **Phase:** Design approved. Implementation planning not started.
- **Code written:** None.
- **Files created on disk:** None (until this file).
- **Last user action:** `/gsd-pause-work` — Claude Code hit API safety false positives twice and died before writing the handoff.

---

## Immediate next step

Run `/gsd:new-project` in this directory using this file as the design anchor, then produce ROADMAP.md with the 5 phases the user was going to power through.
