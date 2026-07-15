<!-- GSD:project-start source:PROJECT.md -->

## Project

**Guitar Trainer v2**

A mobile AI teacher that knows what you played yesterday, what you're struggling with, and what to hand you today. Serious guitar players (intermediate/advanced) open the app, get a Song of the Day tied to their current skill focus, work through the AI teacher's breakdown of that song, and log how it went. The daily ritual builds a skill graph the AI uses to pick tomorrow's song.

Full rewrite of v1 (web app, `~/.Trash/mac migration/guitar-trainer/`, GitHub `Hernanros/guitar-trainer`). Mobile-first, music-centric. Built as a POC for one user (Hernan) with sharing in mind day one.

**Core Value:** **One real song, decomposed by an AI teacher, every day — so practicing feels like playing.**

If everything else fails, this must work: opening the app → getting today's song → seeing tab/chord diagrams → practicing → logging the session → tomorrow's song reflects yesterday's rating.

### Constraints

- **Platform**: iOS + Android via Expo + React Native — mobile-first, not web
- **Stack (locked in RECOVERED-DESIGN.md)**: Expo + RN 18, Zustand (UI state), TanStack Query (server state), MMKV (local storage), react-native-svg (tab/chord rendering), FastAPI on Railway, Postgres
- **AI models**: Haiku 4.5 (routing) · Sonnet 4.6 (teaching content) · Opus 4.7 (fallback)
- **Budget**: $20/month Anthropic Console hard cap during POC — enforced by 4-layer concentric guardrails
- **Single user for POC**: no auth for v1, but data model must leave multi-user seams stubbed
- **MVP scope locked**: "Approach A — Prove the loop" — tightest song-of-day loop, self-report only, no mic / no chat / no streaming

<!-- GSD:project-end -->

<!-- GSD:stack-start source:STACK.md -->

## Technology Stack

Technology stack not yet documented. Will populate after codebase mapping or first phase.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
