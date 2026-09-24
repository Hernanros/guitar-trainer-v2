# Fletcher

**An AI guitar teacher that hands you one real song every morning — chosen because it's exactly what your playing needs today.**

Fletcher is a mobile practice companion for serious guitar players. Not beginners running through chord charts — the players who've been at it five, ten, fifteen years and can't quite explain why practice has stopped feeling like it moves them forward.

_Status: Active development · POC for one player · iOS + Android_

---

## The problem this is trying to solve

If you've played guitar for more than a couple of years, the practice trap probably feels familiar. You pick up the guitar. You don't know what to work on. You play the same three riffs you already know cold. You feel guilty about it. You put the guitar down.

The apps out there don't fix this. The ones aimed at beginners drown you in chord charts. The ones with exercises give you drills that are technically fine but never quite feel like the thing you actually want to be doing — which is playing music. Meanwhile the fastest growth in guitar-playing history — the ten hours a day serious teenagers put in — happens because a teenager has a song they can't yet play but desperately want to. Motivation and direction, in one package.

Fletcher tries to bottle that. Every day it hands you **one real song** that's a little beyond what you can do today, breaks it down with an AI teacher, and remembers what you struggled with so tomorrow's song builds on it.

---

## A day with Fletcher

You open the app in the morning. Today's song is *Wish You Were Here*. Fletcher noticed you've been working on your fingerstyle over the last week and picked this one because it'll stretch you without wrecking you.

```
┌───────────────────────────────────────┐
│  Today · September 14, 2026           │
│  ───────────────────────────────────  │
│                                        │
│  🎸  Song of the Day                  │
│                                        │
│  Wish You Were Here                    │
│  Pink Floyd  ·  1975                   │
│  Key: Em  ·  BPM: 60  ·  E♭ standard  │
│                                        │
│  Skill focus:  fingerstyle arpeggios  │
│                                        │
│  ┌────────────────────────────────┐   │
│  │  Start the Breakdown       →   │   │
│  └────────────────────────────────┘   │
│                                        │
│  Yesterday · Blackbird        ★★★☆☆   │
│  ↳ "Struggled with the pull-offs."    │
└───────────────────────────────────────┘
```

You tap **Start the Breakdown**. Fletcher's teacher opens the song up section by section — intro, verse, chorus, outro — with chord diagrams, tab, and a single one-line coaching cue for each. Not overwhelming. One thing at a time.

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
└───────────────────────────────────────┘
```

You play through it. Twice. Three times. You slow Section 2 down to half speed and loop the passage that trips you up. Twenty-seven minutes go by. It didn't feel like practice.

When you're ready to stop, Fletcher asks how it went.

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

You rate it, note what still felt sloppy, and save.

Tomorrow's song already knows.

---

## Why it works (or is trying to)

Three product bets drive the whole design:

**One real song beats twenty exercises.** Nobody remembers what a chromatic drill felt like. Everyone remembers the song they were struggling to play. Motivation follows meaning, and meaning lives in songs.

**Decomposition is where teaching lives.** A song listed as "here are the chords" isn't taught. A song broken into named sections with a one-line coaching cue for each is taught. The breakdown is the actual product.

**The skill graph builds from real practice, not questionnaires.** Fletcher doesn't ask you to rate your finger independence out of ten. It watches how you rated the sessions you played, what you said clicked, what you said didn't, and picks tomorrow accordingly.

---

## Under the hood

For engineers curious about the build:

- **Mobile:** Expo + React Native (iOS/Android), Zustand for UI state, TanStack Query for server state, react-native-svg for chord and tab rendering.
- **Backend:** FastAPI + Postgres on Railway. `git push origin main` is the whole deploy; Alembic migrations run automatically in a pre-deploy step before the new container takes traffic — see [`server/DEPLOY.md`](server/DEPLOY.md) before running `alembic` against anything.
- **AI:** Multi-model routing on the Anthropic API — Haiku 4.5 as a fast router/classifier, Sonnet 4.6 as the primary teacher generating breakdowns and coaching cues, Opus 4.7 as a fallback for edge cases.
- **Cost:** A $20/month hard budget for the whole thing. Enforced through four concentric guardrail layers — per-request predictive check, daily rolling budget, monthly application budget, and the Anthropic console-level failsafe. The budget shapes model choice, prompt design, and caching; it's a first-class architectural concern, not an afterthought.

The `.planning/` folder in this repo has the roadmap, requirements, and design docs — how this project is actually being planned and shipped.

---

## Status

Active development. Built as a POC for a single serious player (me) with multi-user seams stubbed for eventual sharing. Not on the App Store yet — this is the workshop, not the storefront.

---

## Author

**Hernan Rosenblum** — Senior Data Scientist / Applied ML Engineer.
[LinkedIn](https://www.linkedin.com/in/hernanros) · [hernan.rosenblum89@gmail.com](mailto:hernan.rosenblum89@gmail.com)
