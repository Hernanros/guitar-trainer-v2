# Fletcher — AI Guitar Practice Assistant

> A mobile AI teacher that knows what you played yesterday, what you're struggling with, and what to hand you today.

Fletcher gives serious guitar players a **Song of the Day** tied to their current skill focus, walks them through an AI-generated breakdown of that song, and logs how it went. Every daily rating updates a per-user skill graph the AI uses to pick tomorrow's song. Practice becomes playing; playing becomes learning.

**Status:** Active development · POC for a single user (multi-user seams stubbed) · iOS + Android via Expo

---

## The Loop

The whole product is one tight daily ritual. If the rest of it breaks and this still works, Fletcher works.

```
   1. Open app             →   Today's song, framed by your current skill focus
   2. Read the breakdown   →   AI teacher decomposes it into sections + chord/tab
   3. Practice             →   Play along, slow down, loop hard passages
   4. Log the session      →   Rate 1–5, note wins and struggles
   5. Tomorrow's song      →   Chosen based on yesterday's rating + skill graph
```

Everything else — the library, the toolkit, the skill graph internals — is scaffolding around that loop.

---

## Screens

### 1 · Song of the Day (home)

```
┌───────────────────────────────────────┐
│  Today · September 14, 2026           │
│  ───────────────────────────────────  │
│                                        │
│  🎸  Song of the Day                  │
│                                        │
│  Wish You Were Here                    │
│  Pink Floyd  · 1975                   │
│  Key: Em  ·  BPM: 60  ·  E♭ standard  │
│                                        │
│  Skill focus:  fingerstyle arpeggios  │
│                                        │
│  ┌────────────────────────────────┐   │
│  │  Start the Breakdown       →   │   │
│  └────────────────────────────────┘   │
│                                        │
│  Yesterday · Blackbird       ★★★☆☆   │
│  ↳ "Struggled with the pull-offs."    │
│                                        │
│  ───────────────────────────────────  │
│  🏠  Home    📚 Library    🛠 Toolkit  │
└───────────────────────────────────────┘
```

### 2 · Breakdown (AI teacher's decomposition)

```
┌───────────────────────────────────────┐
│  ←   Wish You Were Here                │
│                                        │
│  Section 1 · Intro  (0:00 – 0:32)     │
│  ───────────────────────────────────  │
│                                        │
│  Chord · Em7                           │
│    ╭───────────╮                       │
│    │0│●│●│0│0│0│                       │
│    ╰───────────╯                       │
│                                        │
│  Tab                                   │
│    e|——0—2—3—2——|                     │
│    B|—0———————3—|                     │
│    G|——————————0|                     │
│                                        │
│  💡  "Anchor the pinky at fret 3.     │
│       Let the ring finger lead —      │
│       don't rush the pull-offs."      │
│                                        │
│  ┌───────┬────────────┬────────────┐  │
│  │ Play  │  Slow 50%  │  Loop A→B  │  │
│  └───────┴────────────┴────────────┘  │
│                                        │
│  Section 1 of 4      ●  ○  ○  ○       │
└───────────────────────────────────────┘
```

### 3 · Rate the session

```
┌───────────────────────────────────────┐
│  How did that go?                     │
│  ───────────────────────────────────  │
│                                        │
│  Wish You Were Here                    │
│  ⏱  27 minutes practiced              │
│                                        │
│  Rate this session                    │
│    😰    🤔    🙂    😌    🔥         │
│     1     2     3     4     5         │
│                                        │
│  What clicked?                         │
│  ┌─────────────────────────────────┐  │
│  │ Fingerstyle rolls in Section 2  │  │
│  │ finally landed clean.           │  │
│  └─────────────────────────────────┘  │
│                                        │
│  What still feels sloppy?             │
│  ┌─────────────────────────────────┐  │
│  │ The transition into Em7 —       │  │
│  │ my ring finger keeps landing    │  │
│  │ on the wrong string.            │  │
│  └─────────────────────────────────┘  │
│                                        │
│  ┌───────────────────────────────┐    │
│  │  Save  ·  Get tomorrow's song │    │
│  └───────────────────────────────┘    │
└───────────────────────────────────────┘
```

The rating and free-text fields flow back into the skill graph. Tomorrow's song is chosen against the delta between what you rated well and what you rated poorly.

---

## Architecture

```
┌──────────────────────────┐       ┌─────────────────────────────┐
│   Mobile app             │       │   Backend                   │
│   Expo + React Native    │◀────▶│   FastAPI (Python) · Postgres│
│   iOS · Android          │       │   Hosted on Railway         │
│                          │       │                             │
│   • Zustand (UI state)   │       │   • Session + rating store  │
│   • TanStack Query       │       │   • Skill-graph updates     │
│     (server state)       │       │   • Content generation      │
│   • MMKV (offline cache) │       │   • Guardrails & cost meter │
│   • react-native-svg     │       │                             │
│     (chord / tab render) │       │                             │
└──────────────────────────┘       └──────────────┬──────────────┘
                                                  │
                                                  ▼
                                      ┌────────────────────────┐
                                      │   Anthropic API        │
                                      │   Multi-model routing  │
                                      │   (see below)          │
                                      └────────────────────────┘
```

The mobile app is a thin client. All content generation and orchestration happens server-side so cost, quality, and eval live in one place.

---

## How the AI Works — Multi-Model Orchestration

Fletcher uses three Anthropic models in specific roles. The idea: match each model to the shape of the task so cost, latency, and quality stay balanced.

```
                        Request enters server
                                │
                                ▼
                        ┌───────────────┐
                        │    Router     │ ← Haiku 4.5
                        │  (Haiku 4.5)  │   fast · cheap · classifies task
                        └───────┬───────┘
                                │
      ┌─────────────────────────┼─────────────────────────┐
      │                         │                          │
      ▼                         ▼                          ▼
  metadata /              teaching content            complex breakdown /
  simple lookups          (song section, chord         edge cases
   → Haiku                 diagram, coaching cue)             │
                                 │                            │
                                 ▼                            ▼
                          ┌──────────────┐            ┌──────────────┐
                          │  Sonnet 4.6  │            │   Opus 4.7   │
                          │   primary    │            │   fallback   │
                          │   teacher    │            │  last resort │
                          └──────────────┘            └──────────────┘
```

- **Haiku 4.5** — router and classifier. Every request hits Haiku first to decide what kind of work needs to happen.
- **Sonnet 4.6** — the actual teacher. Generates song breakdowns, chord/tab explanations, and per-section coaching cues.
- **Opus 4.7** — reserved for hard cases and fallback. Higher cost, so gated behind explicit escalation criteria.

Every model call is metered, budgeted, and observed.

---

## Cost Guardrails

Fletcher is built under a hard **$20 / month** Anthropic API budget. The whole architecture is shaped by that constraint — nothing about model choice, prompt design, or caching would look the same without it.

The guardrails are **four concentric layers**, from innermost to outermost:

```
        ┌───────────────────────────────────────────┐
        │ 4 · Anthropic Console spend cap ($20/mo)  │  ← last-resort failsafe
        │   ┌───────────────────────────────────┐   │
        │   │ 3 · Monthly application budget    │   │  ← soft-then-hard cutoff
        │   │   ┌───────────────────────────┐   │   │
        │   │   │ 2 · Daily rolling budget  │   │   │  ← smooths spikes
        │   │   │   ┌───────────────────┐   │   │   │
        │   │   │   │ 1 · Per-request   │   │   │   │  ← reject before call
        │   │   │   │    budget check   │   │   │   │
        │   │   │   └───────────────────┘   │   │   │
        │   │   └───────────────────────────┘   │   │
        │   └───────────────────────────────────┘   │
        └───────────────────────────────────────────┘
```

Each inner layer catches the common case; each outer layer catches what the inner ones miss. Nothing gets past layer 4 — the console-level cap is a hard ceiling the app can't override.

This is the pattern I'd take into any LLM system with real production constraints: **the budget is a first-class architectural concern, not an afterthought.**

---

## Tech Stack

| Layer | Tools |
|---|---|
| **Mobile** | Expo · React Native 18 · Zustand (UI state) · TanStack Query (server state) · MMKV (offline cache) · react-native-svg (chord + tab rendering) |
| **Backend** | FastAPI · Python · Postgres · Railway (hosting) |
| **AI** | Anthropic API — Haiku 4.5 · Sonnet 4.6 · Opus 4.7 |
| **Type safety** | OpenAPI → TS codegen from server schema |

---

## Repo Structure

```
guitar-trainer-v2/
├── mobile/           Expo + React Native app (iOS/Android)
│   └── src/
│       ├── app/      Expo Router — screens (tabs, breakdown, onboarding)
│       ├── api/      Generated TS types + client
│       ├── components/
│       ├── hooks/
│       ├── store/    Zustand stores
│       └── types/
├── server/           FastAPI backend
│   └── app/
│       ├── ai/       Model routing, guardrails, prompt orchestration
│       └── ...
├── .planning/        Planning artifacts — roadmap, requirements, runbook
└── README.md
```

The `.planning/` folder is where product and engineering plans live. It's public deliberately — the way this project is planned is part of the project.

---

## Local Development

Prerequisites: Node 20+, Python 3.11+, an Anthropic API key.

```bash
# Backend
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
uvicorn app.main:app --reload

# Mobile (separate terminal)
cd mobile
npm install
npx expo start
```

Open the Expo Go app on your phone and scan the QR code, or press `i` / `a` to open in a simulator.

---

## Design Principles

A few things this project takes seriously that a lot of AI apps don't:

- **The daily ritual is the product.** Everything else — library, tools, skill graph — is scaffolding around one tight loop: open → play → rate → tomorrow.
- **The budget is architecture.** A $20/month cap is not a footnote. It shapes model choice, prompt design, caching, and the shape of every request.
- **Multi-model routing over single-model complexity.** Haiku for triage, Sonnet for teaching, Opus for edge cases — each model doing what it's uniquely good at, priced accordingly.
- **The skill graph is a side effect.** It's built from real practice sessions, not questionnaires. What you actually play tells the model more than what you claim to know.
- **One user first.** POC for a single serious player before general release. Sharing is designed in from day one, but proven for one before scaled to many.

---

## Author

Built by **Hernan Rosenblum** — Senior Data Scientist / Applied ML Engineer.
Reach me on [LinkedIn](https://www.linkedin.com/in/YOUR-HANDLE) or at [hernan.rosenblum89@gmail.com](mailto:hernan.rosenblum89@gmail.com).
