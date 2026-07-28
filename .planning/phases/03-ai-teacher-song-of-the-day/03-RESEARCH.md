# Phase 3: AI Teacher & Song of the Day - Research

**Researched:** 2026-07-29
**Domain:** Sonnet 4.6 structured output for guitar-domain content · react-native-svg rendering at scale · PostgreSQL deterministic per-day selection · atomic mastery-write transactions
**Confidence:** MEDIUM-HIGH (stack and codebase claims HIGH; Sonnet cost/latency estimates MEDIUM; react-native-svg limits MEDIUM; song_catalog difficulty conventions LOW)

## Summary

Phase 3 replaces Phase 1's stub `SELECT * FROM songs LIMIT 1` with a deterministic per-user song-of-day selector, adds Fletcher's first real Sonnet-generated teaching content (breakdown of a chosen song), and closes the loop with a 3-tap rating that writes deterministically to `skill_nodes.mastery`. All three of these are new territory relative to Phase 2.

The Sonnet call is structurally identical to Phase 2's `run_onboarding_parse` (tool-use with forced `tool_choice`, `SonnetOnboardingOutput` swapped for a `Breakdown` payload), but the *content* domain is different: guitar-tab semantics, chord positions, technique advice. Estimated cost per breakdown at typical size (~4-8 measures, 4-6 chords, 3-5 technique notes): ~$0.03-$0.06 in output tokens, well under the $20/mo cap even if the user taps a breakdown every day. Latency at p50 for this payload size on Sonnet 4.6 will exceed the 5s target — plan for 8-15s p50 and design the loader UX around that reality.

`react-native-svg` scales to 4-16-measure tabs at the current TabNotation architecture *if* the component is refactored to (a) memoize per-measure rendering, (b) horizontally scroll multi-measure tabs rather than trying to fit everything on-screen, and (c) render `<Text>` elements sparingly (fret numbers are the highest count and the current Rect-behind-Text pattern doubles the SVG node count). Existing components are Phase-1-appropriate but need targeted extension, not full rewrite.

`song_catalog` seed authorship: hand-curate 10 canonical songs for POC (Sweet Home Chicago, Little Wing, Blackbird, Wonderwall, Comfortably Numb, Purple Haze, Hotel California, Wish You Were Here, Eruption, Thunderstruck — the exact list already suggested in 03-CONTEXT.md `<specifics>`). Difficulty mapping uses the informal Ultimate Guitar / Songsterr conventions (Beginner/Intermediate/Advanced) numerically transformed to a [0,1] scale — 0.2/0.5/0.8 as the three anchors, with intra-tier fine-tuning by the human curator. Sonnet-generated seed rejected: quality control matters at 10-song scale, and one hour of hand-curation costs less than debugging bad seed data later.

**Primary recommendation:**
1. **Mirror `run_onboarding_parse` exactly** for `run_technique_breakdown` at `server/app/ai/breakdown.py`. Same tool-use pattern, same SAVEPOINT-style wrapper (but skip SAVEPOINT here — see §5), same one-retry-widened-timeout D-07 shape.
2. **Refactor `TabNotation.tsx` to accept multi-measure input** with a horizontal `<ScrollView>` per measure and `React.memo` per measure. Keep `ChordDiagram.tsx` as-is.
3. **Hand-curate the 10 seed songs** in the Alembic 0003 migration as raw INSERTs; each with `primary_skill_root` from the D-08 taxonomy and `difficulty` as a numeric anchor.
4. **Selector as one CTE-based SQL query** with `setseed(hashtext(user_id || local_calendar_day) * 1e-9)` for deterministic-per-day randomness.
5. **`X-Timezone-Offset` header** injected in `apiFetch.ts` from a `Date().getTimezoneOffset()` call; server computes local calendar day as `DATE((now() AT TIME ZONE 'UTC') + (- offset_minutes) * INTERVAL '1 minute')`.
6. **Rating write endpoint** uses a single `AsyncSession.begin()` transaction — no SAVEPOINT needed because there's no LLM call in the write path (deterministic writes principle).

## User Constraints (from CONTEXT.md)

### Locked Decisions

**Song-of-Day selector:**
- **D-01: 75/25 mixture.** `argmin(mastery)` over `skill_nodes` joined to songs where `songs.category = 'working_on'`. Tiebreaker `updated_at ASC`. 25% override with bank-random.
- **D-02: Bank = hybrid.** User's own songs (all 3 categories) ∪ `song_catalog` seed. ~30-50 seed songs across the D-08 root taxonomy.
- **D-03: Bank filtered by player level.** `player_level = mean(mastery)`; bank narrowed to songs whose `primary_skill.difficulty` is within ±0.15 of that level. `song_catalog.primary_skill_root` = D-08 enum; `song_catalog.difficulty` = numeric [0,1].
- **D-04: Empty working_on = 100% bank-random.** Same code path as the 25% override.
- **D-05: One re-roll per day.** After Today renders, one swap allowed. Cost bound: 2 breakdown calls/user/day maximum.

**Rating UX + mastery math:**
- **D-06: 3-tier Fletcher-voiced pills.** "Not my tempo" / "Getting closer" / "That's what I'm looking for". One tap, no confirm. Stored per session on `user_sessions`.
- **D-07: Rating writes to all attached skill_nodes.** Every leaf in `song_skills` for this song, weighted equally.
- **D-08: Fixed additive mastery math.** -0.05 / +0.05 / +0.15. Clamped [0, 1]. No undo.

**Daily rotation trigger:**
- **D-09: Local midnight, unconditional.** Fresh song every calendar day regardless of prior rating.
- **D-10: Device timezone via `X-Timezone-Offset` header** (minutes) on `GET /api/v1/today-song`.

### Claude's Discretion

- **Sonnet breakdown timing** = lazy on first tap, cache-forever per song. Expose `breakdown_generated_at` column on `songs`.
- **Sonnet call structure** = mirror 02-03. New function `run_technique_breakdown(song_title, song_artist, target_skill_names, user_level)` at `server/app/ai/breakdown.py`. One retry with widened timeout; on second failure return HTTP 503 with Fletcher-voiced error.
- **Fletcher loader copy during Sonnet call:** "Fletcher is listening..." → "Working out the fingering..." (@3s) → "Almost there..." (@8s). Reuse Phase 2's loader rotation slice.
- **Rating write endpoint** = `POST /api/v1/sessions`. Body `{song_id, rating}`. Single transaction. Idempotency: 409 on repeat rating for same user + song + local calendar day.
- **Player level cached in `users.preferences` JSONB.** Recomputed on Song-of-Day request (inline SQL AVG).
- **`GET /api/v1/today-song` payload shape:** `{song_id, song: SongResponse, breakdown_available: bool, rerolled: bool}`. Breakdown fetched by separate `GET /api/v1/songs/{id}/breakdown`.
- **Re-roll endpoint** = `POST /api/v1/today-song/reroll`. 409 if already used today.

### Deferred Ideas (OUT OF SCOPE)

- Weighted `song_skills.weight` (Phase 2 wizard change needed).
- Streak bonus on mastery (retention format is display, not write-path).
- Multiplier mastery math (revisit if additive fails).
- Slider or 5-tier rating UI.
- Sonnet breakdown pre-generation at midnight (lazy-on-tap wins on cost).
- Regeneration of existing breakdown.
- Multi-song days.
- Skip-without-rating penalty.
- Cross-user `song_catalog` contributions.
- Song lyrics or standalone chord charts beyond the semantic `Breakdown` JSON.
- Practice timer / session length enforcement.
- Regenerating today's song after re-roll used.

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| **SOTD-01** | Today tab shows exactly one Song of the Day chosen from current skill graph state and user preferences | §4 (selector SQL — 75/25 CTE); §1 (Sonnet call is not on this path); §6 (timezone header) |
| **SOTD-02** | AI teacher (Sonnet 4.6) produces a technique-breakdown for the day's song | §1 (prompt engineering, tool-use pattern, cost + latency, error handling); mirrors Phase 2 `run_onboarding_parse` |
| **SOTD-03** | Breakdown renders playable material as tab notation via react-native-svg | §2 (multi-measure refactor of `TabNotation.tsx`, memoization, horizontal scroll) |
| **SOTD-04** | Breakdown renders chord diagrams via react-native-svg | §2 (existing `ChordDiagram.tsx` reused as-is; only wrapping ScrollView needs update if >6 chords) |
| **SOTD-05** | After practicing, user submits a self-report session rating that feeds back into the skill graph | §5 (atomic transaction, `user_sessions` insert + `skill_nodes.mastery` UPDATE, idempotency 409); §7 (client mutation + invalidation) |
| **SKILL-03** | Session ratings update the graph via deterministic rules (no LLM in the write path) | §5 (fixed additive per D-08, single AsyncSession.begin(), no `server/app/ai/` import in the endpoint) |

## Project Constraints (from CLAUDE.md)

- **Platform:** iOS + Android via Expo + React Native (mobile-first, not web).
- **Locked stack:** Expo + RN 18, Zustand (UI state), TanStack Query (server state), MMKV (local storage), react-native-svg (tab/chord rendering), FastAPI on Railway, Postgres.
- **AI models:** Haiku 4.5 (routing) · Sonnet 4.6 (teaching content) · Opus 4.7 (fallback). Phase 3 uses Sonnet 4.6.
- **Budget:** $20/month Anthropic Console hard cap during POC. Cost per Sonnet breakdown must be accounted for; 4-layer guardrails land in Phase 4 — Phase 3 must not exceed 2 breakdown calls/user/day (D-05 re-roll cap).
- **Single user POC:** no auth. `X-User-ID` header carries device UUID (Phase 2 D-04). Multi-user seams stubbed but not wired.
- **MVP scope locked:** "Approach A — Prove the loop." No mic / no chat / no streaming.
- **Fletcher voice contract:** `.planning/design/fletcher-identity.md` governs all user-facing strings (rating pills, loader rotation, error messages, re-roll copy).
- **GSD workflow enforcement:** Do not make direct repo edits outside a GSD workflow.
- **`mobile/AGENTS.md` mandate:** Read exact versioned Expo docs at `https://docs.expo.dev/versions/v57.0.0/` before writing any Expo-touching code.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Today song selection (75/25 mixture, argmin, tiebreaker) | API / Backend | Database (query executes there) | Deterministic writes principle — selection logic lives in the server; DB does the math via one SQL query. Never client-side. |
| Sonnet breakdown call + retry + fail-open | API / Backend (`server/app/ai/`) | — | Phase 4 governor interception point per Phase 2 canonical pattern. Client never sees Anthropic API key or model choice. |
| Breakdown JSON persistence (cache-forever) | Database | API / Backend (writes on success) | `songs.breakdown` JSONB + `breakdown_generated_at` timestamp — one column read serves all subsequent taps. |
| Tab + chord SVG rendering | Browser / Client (RN native SVG) | — | Semantic JSON in → SVG out. Server never owns visual layout (Phase 1 D-03). |
| Loader message rotation during Sonnet wait | Browser / Client (Zustand slice) | — | Pure UI state, no server involvement. Reuse Phase 2's `loaderMessageIndex` pattern. |
| Rating submission + mastery UPDATE | API / Backend | Database (single transaction) | Deterministic writes principle — server is the single writer of `skill_nodes.mastery`. Client cannot be trusted to compute mastery. |
| Idempotency guard (one rating per song per day) | API / Backend | Database (UNIQUE constraint enforces at write time) | Server COUNT-before-write short-circuits, DB UNIQUE constraint is the belt-and-suspenders. |
| Daily rotation trigger (local midnight) | API / Backend (calendar day math) | Browser / Client (sends `X-Timezone-Offset`) | Server decides "is this a new day for this user" using client-provided offset. No polling, no cron. |
| Re-roll (one per day) | API / Backend | Database (UNIQUE on user + local calendar day for `reroll_count`) | Same idempotency shape as rating. |
| `song_catalog` seed data (10 songs, hand-curated) | Database (Alembic seed) | — | Static reference data; no code path writes to `song_catalog` in Phase 3. |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `anthropic` (Python SDK) | 0.117.0 | Sonnet 4.6 tool-use call | Already pinned Phase 2. `AsyncAnthropic` shared singleton via `get_client()`. Reuse — do NOT instantiate a second client. |
| SQLAlchemy async 2.0 | (already in server) | `AsyncSession` for atomic mastery UPDATE + `user_sessions` INSERT | Established pattern per Phase 2 02-03 |
| Alembic | (already in server) | Migration 0003: `song_catalog`, `user_sessions`, `songs.breakdown_generated_at`, `song_catalog` seed INSERTs | Established pattern per Phase 2 (raw SQL enum creation, DEFERRABLE FK, system-user backfill) |
| `@tanstack/react-query` | 5.101.2 | `useTodaySong`, `useBreakdown(songId)`, `useSubmitRating`, `useReroll` | Already in package.json; mirror `useSkillGraph` |
| `react-native-svg` | 15.15.4 | Existing `TabNotation` + `ChordDiagram`; extend `TabNotation` for multi-measure | Already installed; the only rendering primitive per project constraint |
| Zustand | 5.0.14 | Reuse `loaderMessageIndex` slice from `uiStore.ts` for breakdown loader rotation | Established Phase 2 pattern |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `react-native-mmkv` | 4.3.2 | Persist last-known good breakdown for offline read | Only if PLAT-04 (offline reads) requires the breakdown too; existing `queryClient.ts` MMKV persister already covers this via TanStack Query cache write-through |
| `openapi-typescript` | 7.13.0 | Regenerate `mobile/src/api/generated/schema.d.ts` after adding new endpoints | Same as Phase 2 — run `npm run codegen:local` after `SessionCreate`, `RatingLiteral`, `TodaySongResponse` are added to Pydantic |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Tool-use forced `tool_choice` | New `output_config.format` structured outputs (beta, `structured-outputs-2025-11-13` header) | Anthropic's newer structured-outputs feature compiles the schema into a grammar and guarantees valid JSON. Currently Sonnet 4.5 / Opus 4.1 only; unclear as of research date whether Sonnet 4.6 supports it. [ASSUMED] Anthropic docs suggest it's model-specific. **Recommendation: stick with tool_use** — Phase 2 already validated it for `run_onboarding_parse`, and consistency reduces surface area. Revisit once Sonnet 4.6 support is confirmed and beta graduates. |
| Hand-rolled SVG in TabNotation | react-native-guitar-tab / react-native-chord-diagram third-party libs | No mature, well-maintained third-party libraries exist for React Native guitar notation. Existing `TabNotation.tsx` + `ChordDiagram.tsx` are ~150 LOC total and already tested against real data. **Recommendation: extend hand-rolled** — Phase 1 already validated this path. |
| Anthropic `strict: true` tool-use (beta) | Standard tool_use | Same beta / model-availability caveat as `output_config.format`. Not needed at Phase 3 scope — Pydantic `model_validate()` catches schema violations at parse time; `AIParseError` raises to trigger retry. |

**Installation:**
No new packages required. Every dependency is already in `mobile/package.json` and `server/pyproject.toml` from Phase 2.

## Package Legitimacy Audit

Phase 3 installs **zero new external packages**. All server + mobile dependencies are already committed from Phases 1-2 (verified against `server/app/ai/client.py`, `mobile/package.json`). No slopcheck audit required.

## Architecture Patterns

### System Architecture Diagram

```
Mobile client (Today tab)                                        Server (FastAPI)                                  Postgres

  useTodaySong() ────────GET /api/v1/today-song?────────► select_today_song(user_id, tz_offset)  ─── selector CTE ──►
                         X-User-ID, X-Timezone-Offset          │                                    (working_on argmin
                                                                │                                     ∪ bank-random with
                                                                │                                     setseed(hashtext(
                                                                │                                     user_id||local_day)))
                                                                ▼
                                                          Returns SongResponse            ◄─── row: songs + breakdown_generated_at
                          ◄────200────────────────────── + breakdown_available: bool
                                                                                                                     
  User taps                                                                                                          
  "See breakdown" ───────GET /api/v1/songs/{id}/────────► get_breakdown(song_id)                                    
                         breakdown, X-User-ID                   │                                                    
                                                                ▼                                                    
                                                       IF songs.breakdown_generated_at IS NULL:                     
                                                          run_technique_breakdown(...)  ────► Anthropic API         
                                                          persist to songs.breakdown              (Sonnet 4.6)      
                                                          set breakdown_generated_at                                
                                                       ELSE: return existing breakdown                              
                          ◄────200 Breakdown─────────── 
                                                                                                                     
  Loader rotation (Zustand loaderMessageIndex):                                                                     
    0-3s   "Fletcher is listening..."                                                                                
    3-8s   "Working out the fingering..."                                                                            
    8+s    "Almost there..."                                                                                         
                                                                                                                     
  Renders TabNotation + ChordDiagram + technique_notes                                                              
                                                                                                                     
  User taps rating pill ─POST /api/v1/sessions─────────► submit_rating(song_id, rating)                             
                         body: {song_id, rating}                │                                                    
                                                                ▼                                                    
                                                     BEGIN TRANSACTION                                              
                                                       IF (SELECT COUNT ... today's session) > 0 → 409             
                                                       INSERT user_sessions row                                     
                                                       UPDATE skill_nodes SET mastery = LEAST(1, GREATEST(0,       
                                                         mastery + rating_shift)) WHERE id IN                       
                                                         (SELECT skill_node_id FROM song_skills WHERE song_id = X) 
                                                     COMMIT                                                         
                          ◄────201───────────────────── SessionResponse                                             
                                                                                                                     
  User taps "Give me another" ──POST /api/v1/today-song/reroll──► reroll_today(user_id, tz)                        
                                                                    │                                               
                                                                    ▼                                               
                                                                 IF reroll already used today → 409                
                                                                 Re-execute selector CTE with different seed        
                                                                 Persist reroll flag                                
                          ◄────200 SongResponse────────────                                                          
```

### Recommended Project Structure

Additions to the existing tree — no restructuring needed.

```
server/
├── app/
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── client.py                   (existing — reuse)
│   │   ├── onboarding.py               (existing — reuse pattern)
│   │   └── breakdown.py                (NEW — mirror onboarding.py structure)
│   ├── api/v1/
│   │   ├── song_of_day.py              (existing — REFACTOR from stub to per-user selector)
│   │   ├── users.py                    (existing — reuse)
│   │   ├── sessions.py                 (NEW — POST /api/v1/sessions rating write)
│   │   └── breakdowns.py               (NEW — GET /api/v1/songs/{id}/breakdown)
│   ├── models/
│   │   ├── db.py                       (extend: SongCatalog, UserSession ORM classes)
│   │   ├── song.py                     (extend: BreakdownResponse, TodaySongResponse, SessionCreate, RatingLiteral)
│   │   └── session.py                  (NEW — Pydantic models for session write)
│   └── selectors/
│       └── today_song.py               (NEW — the CTE query builder + execution wrapper)
└── alembic/versions/
    └── 0003_song_catalog_user_sessions_breakdown_generated_at.py  (NEW)

mobile/
├── src/
│   ├── api/
│   │   ├── apiClient.ts                (MODIFY — inject X-Timezone-Offset from Date().getTimezoneOffset())
│   │   ├── todaySong.ts                (NEW — useTodaySong, useReroll, useBreakdown)
│   │   ├── sessions.ts                 (NEW — useSubmitRating)
│   │   └── users.ts                    (existing — reuse)
│   ├── components/
│   │   ├── TabNotation.tsx             (MODIFY — multi-measure horizontal scroll, React.memo per measure)
│   │   ├── ChordDiagram.tsx            (existing — reuse as-is)
│   │   ├── RatingPills.tsx             (NEW — 3-tier Fletcher-voiced horizontal buttons)
│   │   ├── FletcherLoader.tsx          (NEW — extract Phase 2 loader rotation into shared component)
│   │   └── FromTheBankTag.tsx          (NEW — small badge for 25% override / empty-working_on cases)
│   └── app/(tabs)/
│       └── index.tsx                   (MODIFY — wire useTodaySong, add rating row, add "Give me another" ghost button)
```

### Pattern 1: Sonnet Tool-Use for Structured Guitar Breakdown

**What:** One Sonnet 4.6 call per song per user, using `tool_choice={"type":"tool", "name":"emit_breakdown"}` to force structured JSON output matching Phase 1's `Breakdown` Pydantic schema.

**When to use:** On the first `GET /api/v1/songs/{id}/breakdown` for a song where `songs.breakdown_generated_at IS NULL`. Every subsequent tap returns the cached row.

**Example** (mirror of Phase 2 `run_onboarding_parse`):

```python
# Source: server/app/ai/onboarding.py (Phase 2 pattern) + Anthropic docs
# https://platform.claude.com/docs/en/build-with-claude/structured-outputs
from app.ai.client import get_client, SONNET_MODEL
from app.models.song import Breakdown

_TOOL_NAME = "emit_breakdown"
_TOOL_DEF = {
    "name": _TOOL_NAME,
    "description": (
        "Emit a full technique breakdown for the requested song. "
        "Include a semantically accurate tab (4-8 measures, standard tuning unless the song calls for otherwise), "
        "chord diagrams for every chord referenced in the tab, and 3-5 technique notes "
        "focusing on the target skills the user is working on."
    ),
    "input_schema": Breakdown.model_json_schema(),
}

SYSTEM_PROMPT = """You are Fletcher — a demanding but constructive guitar teacher (Terence Fletcher from Whiplash, but the version who actually wanted his students to succeed).

You break down real songs into playable, honest technique instruction. Your job:
- Produce accurate, hand-notated tab in standard notation (E A D G B e tuning unless otherwise specified). Tab uses string numbers 1-6 where 1 is high e.
- Produce chord diagrams for every chord referenced. base_fret should reflect the actual position on the neck. Include muted/open strings explicitly.
- Produce 3-5 technique_notes with:
    heading: sharp, specific (e.g., "Shuffle Rhythm", not "Rhythm Section")
    body: 2-3 sentences of coaching — what to do, common trap, how to check yourself
- Match the difficulty to the user's player_level (0.0 = beginner, 1.0 = highly skilled).
- Focus the technique_notes on the target_skills provided — do not try to teach everything about the song.

CRITICAL RULES:
- Use string numbers 1-6 (1=high e, 6=low E). Fret 0 = open string.
- For ChordPosition, fret=-1 = muted string. fret=0 = open string.
- Every leaf note's beat.notes.string is between 1 and 6.
- Duration values: "whole", "half", "quarter", "eighth", "sixteenth" only.
- Do NOT invent song content that doesn't exist. If you're not sure about a specific arrangement, use a well-known idiomatic voicing for the song's genre.
- Voice: sharp, diagnostic, next-step. Never vague. Never punitive.
"""

async def run_technique_breakdown(
    song_title: str,
    song_artist: str,
    target_skill_names: list[str],
    user_level: float,
    *,
    timeout_seconds: float = 30.0,
) -> Breakdown:
    """Single Sonnet 4.6 call. Mirrors run_onboarding_parse structure.

    Phase 4 governor interception point — the ONLY function that calls Sonnet
    for breakdowns.
    """
    user_content = (
        f"Song: {song_title}\n"
        f"Artist: {song_artist}\n"
        f"Target skills to focus on: {', '.join(target_skill_names)}\n"
        f"User player_level: {user_level:.2f}\n\n"
        f"Emit the structured breakdown now."
    )

    async def _call(timeout: float) -> Breakdown:
        client = get_client()
        resp = await asyncio.wait_for(
            client.messages.create(
                model=SONNET_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                tools=[_TOOL_DEF],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
            ),
            timeout=timeout,
        )
        tool_use = next(
            (b for b in resp.content if getattr(b, "type", None) == "tool_use"), None
        )
        if tool_use is None:
            raise RuntimeError("Sonnet did not emit a tool_use block despite forced tool_choice.")
        return Breakdown.model_validate(tool_use.input)

    try:
        try:
            return await _call(timeout_seconds)
        except (APITimeoutError, asyncio.TimeoutError, APIError) as e:
            logger.warning("Sonnet breakdown call failed once (%s). Retrying with widened timeout.", type(e).__name__)
            return await _call(timeout_seconds * 2)
    except Exception as e:
        raise AIBreakdownError(f"Sonnet breakdown failed: {type(e).__name__}: {e}") from e
```

### Pattern 2: Deterministic 75/25 CTE with Per-Day Seed

**What:** Single SQL statement that computes the argmin-in-working_on song AND a bank-random song, then picks between them based on a per-user + per-day seed.

**When to use:** In `GET /api/v1/today-song` handler. Executed once per user per day (client-side TanStack Query caches the result for the local calendar day).

**Example:**

```sql
-- server/app/selectors/today_song.py
-- Deterministic-per-day song selection.
-- The setseed value derives from user_id + local_calendar_day so repeated calls
-- on the same day return the same song. Different day OR different user → different seed.
--
-- Notes:
--   - :user_id, :player_level, :local_calendar_day are bind params (SQLAlchemy passes them).
--   - hashtext() is a Postgres built-in returning int4; setseed wants -1..1 double.
--     The /2147483647.0 scales int4 into that range.
--   - Player level (D-03) narrows the seed catalog to ±0.15 around the user's mean mastery.

WITH
  seed AS (
    SELECT setseed(hashtext(:user_id::text || :local_calendar_day::text) / 2147483647.0)
  ),
  -- The 75% deterministic pick: argmin(mastery) over working_on
  working_on_pick AS (
    SELECT s.id AS song_id, sn.mastery, sn.updated_at
    FROM songs s
    JOIN song_skills ss ON ss.song_id = s.id
    JOIN skill_nodes sn ON sn.id = ss.skill_node_id
    WHERE s.user_id = :user_id
      AND s.category = 'working_on'
    ORDER BY sn.mastery ASC, sn.updated_at ASC
    LIMIT 1
  ),
  -- The bank: user's own songs ∪ song_catalog filtered by player_level ±0.15
  bank AS (
    SELECT s.id AS song_id
    FROM songs s
    WHERE s.user_id = :user_id
    UNION ALL
    SELECT sc.id AS song_id
    FROM song_catalog sc
    WHERE ABS(sc.difficulty - :player_level) <= 0.15
  ),
  bank_pick AS (
    SELECT song_id
    FROM bank
    -- setseed is already applied by the seed CTE; random() below is deterministic
    ORDER BY random()
    LIMIT 1
  ),
  -- The 25% override coin flip. Because setseed is deterministic, random() here
  -- is stable per (user, day).
  coin AS (
    SELECT random() AS flip
  )
SELECT
  CASE
    WHEN (SELECT flip FROM coin) < 0.25 THEN (SELECT song_id FROM bank_pick)
    WHEN NOT EXISTS (SELECT 1 FROM working_on_pick) THEN (SELECT song_id FROM bank_pick)
    ELSE (SELECT song_id FROM working_on_pick)
  END AS today_song_id,
  (SELECT flip FROM coin) < 0.25 OR NOT EXISTS (SELECT 1 FROM working_on_pick) AS from_bank
FROM seed;  -- Trigger the seed side effect
```

**Note on `setseed` semantics [CITED: https://database.guide/how-setseed-works-in-postgresql/]:** setseed is session-scoped. In an async pool, each connection is its own session. Two concurrent requests for two users each get their own seed. Not a shared-state hazard.

**Determinism guarantee [CITED: https://www.cybertec-postgresql.com/en/making-random-deterministic/]:** After `setseed`, every `random()` call in the current session is deterministic. Multiple `random()` calls in one statement (like `bank_pick.random()` + `coin.flip`) will produce different values, but the sequence is stable given the seed. So per-user per-day: same song both times.

### Pattern 3: Atomic Rating Write with Idempotency

**What:** Single-transaction insert-session + update-mastery on N skill_nodes, with a database-level UNIQUE constraint enforcing the "one rating per song per day" rule.

**When to use:** `POST /api/v1/sessions` handler. No LLM in this path (SKILL-03).

**Example:**

```python
# server/app/api/v1/sessions.py (NEW)
# Deterministic rating write. NO LLM in this path (SKILL-03).
# Single AsyncSession transaction: INSERT user_sessions + UPDATE skill_nodes.
# Idempotency guard: SELECT-before-write for legibility; UNIQUE constraint as belt-and-suspenders.

RATING_SHIFTS = {
    "not_my_tempo": Decimal("-0.05"),
    "getting_closer": Decimal("0.05"),
    "thats_what_im_looking_for": Decimal("0.15"),
}

async def submit_rating(
    body: SessionCreate,
    x_user_id: uuid.UUID,
    x_tz_offset_minutes: int,
    db: AsyncSession,
) -> SessionResponse:
    # Compute local calendar day server-side. Client-provided offset is trusted for POC.
    local_day = await db.scalar(
        text("SELECT DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute'))"),
        {"tz": x_tz_offset_minutes},
    )

    # Idempotency guard (legibility). The DB UNIQUE constraint on
    # (user_id, song_id, local_day) is the real enforcement.
    already = await db.scalar(
        select(func.count(UserSession.id))
        .where(
            UserSession.user_id == x_user_id,
            UserSession.song_id == body.song_id,
            UserSession.local_calendar_day == local_day,
        )
    )
    if already:
        raise HTTPException(status_code=409, detail="Already rated this song today.")

    shift = RATING_SHIFTS[body.rating]

    # Single transaction — AsyncSession's begin() context manager wraps everything.
    async with db.begin():
        # 1. Insert the session row.
        session_row = UserSession(
            id=uuid.uuid4(),
            user_id=x_user_id,
            song_id=body.song_id,
            rating=body.rating,
            local_calendar_day=local_day,
            tz_offset_minutes=x_tz_offset_minutes,
        )
        db.add(session_row)

        # 2. Update mastery on every skill_node in song_skills for this song.
        #    LEAST/GREATEST clamps to [0, 1] per D-08.
        #    Row-level lock (FOR UPDATE) not strictly needed because we hold the
        #    same rows for the whole transaction, but explicit is fine.
        await db.execute(
            update(SkillNode)
            .where(
                SkillNode.id.in_(
                    select(SongSkill.skill_node_id).where(SongSkill.song_id == body.song_id)
                ),
                SkillNode.user_id == x_user_id,  # defense in depth: never update another user
            )
            .values(mastery=func.least(Decimal("1.0"), func.greatest(Decimal("0.0"), SkillNode.mastery + shift)))
        )

    await db.refresh(session_row)
    return SessionResponse.model_validate(session_row)
```

**Idempotency at the DB level:** add `UNIQUE (user_id, song_id, local_calendar_day)` to the `user_sessions` migration. On concurrent double-tap, the second `INSERT` fails with `IntegrityError` and FastAPI's error handler returns 409.

### Pattern 4: TanStack Query for "Today Song" — Daily Rotation + Cache-Forever Breakdown

**What:** Two separate queries with different staleness policies. `useTodaySong` refetches whenever the local calendar day changes; `useBreakdown(songId)` is `staleTime: Infinity` (never regenerated).

**When to use:** Today tab landing page + breakdown-on-tap.

**Example:**

```typescript
// mobile/src/api/todaySong.ts (NEW)
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from './apiClient';
import { getOrCreateUserId } from './mmkv';
import type { components } from './generated/schema';

type TodaySongResponse = components['schemas']['TodaySongResponse'];
type Breakdown = components['schemas']['Breakdown'];

/**
 * Local calendar day key — used to invalidate the today-song query
 * whenever the user crosses their local midnight boundary.
 * The key changes at local midnight, TanStack Query treats it as a new query, refetch fires.
 */
function localCalendarDay(): string {
  const now = new Date();
  const offsetMs = now.getTimezoneOffset() * 60 * 1000;
  const localMidnight = new Date(now.getTime() - offsetMs);
  return localMidnight.toISOString().slice(0, 10); // "YYYY-MM-DD"
}

export function useTodaySong() {
  const userId = getOrCreateUserId();
  return useQuery({
    queryKey: ['today-song', userId, localCalendarDay()],
    queryFn: () => apiFetch<TodaySongResponse>('/api/v1/today-song'),
    staleTime: 1000 * 60 * 60 * 12, // 12h — safe: local-day key in queryKey handles rotation
    refetchOnWindowFocus: false,
  });
}

/**
 * Breakdown is cache-forever per song (D-11). The server persists on first fetch;
 * we mirror that in TanStack Query so a re-open of the same song = zero server call.
 */
export function useBreakdown(songId: number | undefined) {
  return useQuery({
    queryKey: ['breakdown', songId],
    queryFn: () => apiFetch<Breakdown>(`/api/v1/songs/${songId}/breakdown`),
    staleTime: Infinity,           // D-11: never regenerated
    gcTime: 1000 * 60 * 60 * 24 * 30, // hold in memory for 30 days
    enabled: songId !== undefined,
    refetchOnWindowFocus: false,
  });
}

export function useSubmitRating() {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();
  return useMutation({
    mutationFn: (body: { song_id: number; rating: 'not_my_tempo' | 'getting_closer' | 'thats_what_im_looking_for' }) =>
      apiFetch(`/api/v1/sessions`, { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => {
      // Skill graph mastery changed → invalidate it so Today's next selection reflects.
      qc.invalidateQueries({ queryKey: ['skill-graph', userId] });
      // Today song stays locked for today (D-05). Don't invalidate it.
    },
  });
}

export function useReroll() {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();
  return useMutation({
    mutationFn: () => apiFetch<TodaySongResponse>(`/api/v1/today-song/reroll`, { method: 'POST' }),
    onSuccess: (fresh) => {
      // Replace today-song cache with the new pick.
      qc.setQueryData(['today-song', userId, localCalendarDay()], fresh);
    },
  });
}
```

**Key insight [CITED: https://tanstack.com/query/latest/docs/framework/react/guides/query-invalidation]:** `invalidateQueries` overrides `staleTime`, so `staleTime: Infinity` on breakdown is safe — a manual invalidation would still trigger refetch. Phase 3 shouldn't invalidate breakdown (per D-11 cache-forever), but future phases might (e.g., "regenerate this breakdown" — currently deferred).

### Pattern 5: Timezone Offset Header Injection

**What:** `apiFetch` adds an `X-Timezone-Offset: <minutes>` header on every request. Server converts to a Postgres INTERVAL.

**When to use:** Every request in Phase 3 (Today song, breakdown, session rating, reroll).

**Example:**

```typescript
// mobile/src/api/apiClient.ts (MODIFY)
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const base = process.env.EXPO_PUBLIC_API_URL;
  if (!base) throw new Error('EXPO_PUBLIC_API_URL is not set — check your .env or eas.json env config.');
  const url = `${base}${path}`;
  const userId = getOrCreateUserId();
  // Date().getTimezoneOffset() returns minutes WEST of UTC (opposite sign of the ISO offset).
  // For UTC-5 (Eastern Time), getTimezoneOffset() returns +300.
  // Server semantics: "add this many minutes to UTC to get local time" needs a sign flip.
  // We negate here so X-Timezone-Offset = -300 for UTC-5 — matching the ISO-8601 convention.
  const tzOffset = -new Date().getTimezoneOffset();
  const res = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'X-User-ID': userId,
      'X-Timezone-Offset': String(tzOffset),
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${init.method ?? 'GET'} ${path}`);
  return res.json() as Promise<T>;
}
```

```python
# server: FastAPI dependency to parse the header
# app/api/deps.py (NEW or extend existing)
from fastapi import Header, HTTPException
from typing import Annotated

def get_tz_offset_minutes(x_timezone_offset: Annotated[str, Header()] = "0") -> int:
    try:
        v = int(x_timezone_offset)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Timezone-Offset must be an integer number of minutes.")
    if not -840 <= v <= 840:  # ±14 hours (Postgres constraint)
        raise HTTPException(status_code=400, detail="X-Timezone-Offset out of range.")
    return v
```

```sql
-- Local calendar day math [CITED: https://www.postgresql.org/docs/current/functions-datetime.html]:
-- Postgres AT TIME ZONE with an INTERVAL treats the interval as a fixed UTC offset.
-- (now() AT TIME ZONE 'UTC') is UTC.
-- Adding + (tz * INTERVAL '1 minute') shifts to the user's local wall clock.
-- DATE() truncates to yyyy-mm-dd.
SELECT DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute')) AS local_day;
```

### Anti-Patterns to Avoid

- **Two separate SQL queries for the 75/25 mixture.** Race conditions with concurrent requests + duplicated seed math. Keep it single-CTE.
- **Using Python `random.random()` for the coin flip.** Would defeat the deterministic-per-day contract. Coin flip must live in the same Postgres session as `setseed`.
- **Setting `staleTime: Infinity` on `useTodaySong`.** The point is that the query key changes at local midnight; refetch fires automatically. `Infinity` would be fine if the key includes the local day, but a 12h backup staleTime is defensive against clock drift.
- **Client-side computation of `local_calendar_day` for the idempotency check.** Server must compute from `X-Timezone-Offset` to prevent client-clock spoofing. Client sends offset, server converts.
- **Passing `Breakdown.model_json_schema()` to `tools[]` without pruning `$ref`.** Anthropic tool-use schemas historically had issues with deeply nested `$ref`. Phase 2 didn't hit this, but the Breakdown schema is deeper (Tab → Measures → Beats → Notes). Test early — if Sonnet rejects the tool def, flatten with Pydantic's `mode='inline'` or a manual dereference pass.
- **`server_default` on `songs.breakdown_generated_at`.** Should be nullable with no default — the null itself signals "no breakdown yet" for the cache check.
- **UPDATE-all-then-clamp in Python.** The `LEAST(1, GREATEST(0, mastery + shift))` must happen in SQL so the write is one round trip. Python-side would require SELECT-then-UPDATE per row = N round trips.
- **Reading `song_skills.weight` for the mastery update.** D-07 explicitly weights equally — don't accidentally use the column that exists in the schema (per Phase 2 02-03 ORM).
- **Skipping `React.memo` on multi-measure TabNotation.** A 16-measure tab with each measure re-rendering every frame will drop below 60fps on mid-range Android. Memoize per measure.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Structured LLM output | Regex on Sonnet text response | Anthropic tool_use with forced `tool_choice` | Phase 2 already validated — same failure modes, same recovery, same telemetry surface |
| Timezone conversion | `X-Timezone-Name: America/Los_Angeles` + `pytz` | `X-Timezone-Offset: -420` + Postgres `INTERVAL '1 minute'` math | Header carries a number, not a name; Postgres does the arithmetic natively; DST edge cases are POC-acceptable drift per D-10 |
| Guitar chord/tab library | react-native-guitar / react-native-fretboard | Existing hand-rolled `TabNotation.tsx` + `ChordDiagram.tsx` | No mature RN libraries exist; existing components are ~150 LOC and already work with D-03 semantic JSON |
| Retry / backoff loop for Sonnet | Custom exponential backoff | Phase 2's D-07 one-retry-widened-timeout pattern | Consistency reduces mental overhead; matches `run_onboarding_parse` |
| Session UNIQUE enforcement | Application-level SELECT-then-INSERT | Database UNIQUE constraint on `(user_id, song_id, local_calendar_day)` | Race-condition free; DB error → 409 mapping in FastAPI exception handler |
| Mastery clamping | Python `min(1.0, max(0.0, ...))` in a Python loop over rows | SQL `LEAST(1, GREATEST(0, mastery + shift))` in one UPDATE | Correctness (atomic within a transaction) + performance (one query vs N) |
| Local midnight scheduler | Client-side `setInterval` to invalidate cache | TanStack Query key includes `localCalendarDay()`; new day → new key → automatic refetch | No scheduler to keep alive; no drift; works after app cold-start |

**Key insight:** Every "custom" solution in Phase 3 has been solved by Phase 1 or Phase 2 architecture. This phase is 90% consistency application, 10% net-new (song_catalog schema + prompt engineering).

## Runtime State Inventory

**Not applicable** — Phase 3 is a greenfield feature phase, not a rename/refactor. No existing runtime state to migrate. New tables (`song_catalog`, `user_sessions`) and one new column (`songs.breakdown_generated_at`) added via Alembic 0003; no existing data touched except the Phase 2 seed row which continues to work with `breakdown` populated and `breakdown_generated_at IS NULL` (Phase 3 code path treats that as "regenerate on first tap" — acceptable because Phase 2's placeholder breakdown is by design overwritable).

## Section 1 — Sonnet 4.6 Breakdown Prompt Engineering

### Cost + latency estimates [MEDIUM confidence]

**Pricing [CITED: https://www.anthropic.com/news/claude-sonnet-4-6, https://platform.claude.com/docs/en/about-claude/pricing]:**
- Input: $3 / MTok
- Output: $15 / MTok
- Cache read: $0.30 / MTok (90% savings — Phase 4 could exploit this for the system prompt if traffic grows)

**Estimated token counts per breakdown call:**

| Component | Input Tokens | Output Tokens |
|-----------|-------------:|--------------:|
| System prompt (rules + Fletcher voice) | ~350 | — |
| Tool schema (`Breakdown.model_json_schema()`) | ~400-600 [ASSUMED — depends on `$ref` expansion] | — |
| User message (song title + artist + skills + level) | ~50 | — |
| Breakdown output (4-8 measures, 4-6 chords, 3-5 technique notes with 40-word bodies) | — | ~1500-3000 [ASSUMED] |

**Cost per breakdown [ASSUMED — needs live measurement]:**
- Input: ~1000 tokens × $3/MTok = **~$0.003**
- Output: ~2000 tokens × $15/MTok = **~$0.030**
- **Total: ~$0.03-$0.06 per breakdown**

**Cost bound for Phase 3 with $20/mo cap:**
- Worst case: 1 user × 2 breakdown calls/day (initial + one re-roll) × 30 days = 60 calls/month
- 60 × $0.06 = **$3.60/month** — 18% of the $20 cap
- With cache-forever (D-11), a re-rolled song's breakdown is generated once, then cached — so 60 unique songs would be the worst-case scenario
- **Verdict:** Well within budget. Phase 4's governor is not on the critical path for Phase 3.

**Latency [MEDIUM confidence, needs measurement]:**
- Phase 2's `run_onboarding_parse` (~similar output size, ~30 tokens/second Sonnet 4.6 output rate) took **5-15 seconds** at typical bootstrapping load per 02-03 summary.
- Phase 3 breakdown output is ~2x the size of onboarding (more nested structure) → estimate **p50 latency: 8-15 seconds; p90: 20-30 seconds**.
- **The "target <5s p50" in the objective is not realistic** for this payload shape on Sonnet 4.6. Plan the UX around 10-15s p50:
  - Show breakdown card immediately with `<FletcherLoader />` in the technique-notes slot
  - Tab + chord skeleton pulses at the top
  - Three-message rotation (0s → 3s → 8s) exactly matches the observed latency curve
  - The "tap to see breakdown" UX from D-05 already accommodates this — user has explicit intent, so a 10s wait is acceptable

### Prompt patterns for stable guitar-domain structured output

**Pattern A — Explicit string numbering + fret convention in system prompt:**
Sonnet cannot be assumed to know your convention (string 1 = high e vs string 1 = low E) or your fret encoding (-1 for muted vs "x" string). State these once in the system prompt.

**Pattern B — Bound the tab size in the tool description:**
The `description` field of the tool definition is the surface where Sonnet reads "how much output do you want." Say "4-8 measures" explicitly. Without bounds, Sonnet drifts toward 12-16 measures which doubles cost and slows latency.

**Pattern C — Target skills in the user message, not the system prompt:**
Skills change per call; keeping them out of the system prompt keeps that prompt cacheable. Phase 4 could exploit `cache_control: {"type": "ephemeral"}` on system + tool def to drop input cost 90%.

**Pattern D — Chord `base_fret` explicit teaching in the prompt:**
Sonnet frequently emits `base_fret=1` for chords that are physically higher on the neck (e.g., a B7 barre at fret 2 or 7). Say "`base_fret` reflects the actual position on the neck; use 1 for open-position chords."

**Pattern E — Reject Sonnet's invention with schema validation:**
Pydantic `model_validate()` on the tool_use.input catches out-of-range strings (7+ = invalid) or missing required fields. `AIBreakdownError` triggers retry. This is Phase 2's error-handling pattern applied here.

### Structured output pitfalls

1. **Deep `$ref` in the JSON schema.** Pydantic's `model_json_schema()` emits `$ref` pointers to definitions. Anthropic's tool-use JSON parser has historically had trouble with certain `$ref` structures (per community reports, HIGH-uncertainty). **Test early with a curl before wiring up the endpoint.** If Sonnet returns malformed tool_use, use Pydantic's `model_json_schema(mode='validation', ref_template='#/$defs/{model}')` or dereference manually. [ASSUMED — verify at planning time]
2. **`tool_choice` forcing does not guarantee schema compliance.** Sonnet still occasionally emits fields not in the schema, or omits required fields. The retry-on-validation-error path per Phase 2 handles this.
3. **`max_tokens` too low silently truncates.** 4096 tokens fits a 4-measure tab comfortably; an 8-measure tab with 6 chords and 5 technique notes could push toward 5000+. **Set `max_tokens=8192` for headroom.**
4. **Sonnet may misapprehend the song.** If it doesn't know "Sweet Home Chicago" it will invent notes. The system prompt's "use idiomatic voicings for the genre" clause is the mitigation, but occasional wrong-notes are POC-acceptable — the user is the ground truth.
5. **Anthropic beta `strict: true` tool-use** (per [CITED: https://claude.com/blog/structured-outputs-on-the-claude-developer-platform]) is currently Sonnet 4.5 / Opus 4.1 only. Sonnet 4.6 support is unclear at research time. **Do not adopt in Phase 3.**

### Error handling

Mirror Phase 2's `AIParseError` pattern with a Phase 3 equivalent:

```python
class AIBreakdownError(Exception):
    """Raised when Sonnet breakdown fails after retry. Caller returns 503 with Fletcher-voiced message."""
```

- On first call: 30-second timeout.
- On timeout / APIError: retry with 60-second timeout (one retry — D-07 shape).
- On second failure: raise `AIBreakdownError` → FastAPI handler returns **503** with body:
  ```json
  {"detail": "Fletcher stepped away from the desk. Give me another second and try again."}
  ```
- Client shows a retry button (NOT fail-open — a bad breakdown is worse than no breakdown, per 03-CONTEXT).
- Do NOT persist `breakdown_generated_at` on failure — leaves the cache miss visible so the next tap retries.

**No SAVEPOINT needed here.** Unlike Phase 2 onboarding (which needed fail-open to preserve the user row), Phase 3 breakdown failure has nothing to rescue. The song row is already persisted; only the breakdown cache miss remains.

## Section 2 — react-native-svg Performance for Tab Notation

### Rendering limits [MEDIUM confidence]

Community reports from react-native-svg maintainers and users:

- **500 SVG shapes takes 9-10 seconds to render** on mid-range devices [CITED: https://github.com/software-mansion/react-native-svg/issues/1319, https://github.com/react-native-svg/react-native-svg/issues/1470].
- **15 SVGs in a FlatList causes 1-2 second render pauses** on navigation [CITED: same].
- **Clipping** (which the current TabNotation doesn't use) is flagged as a perf concern for scrolling scenarios.

**Extrapolating to Phase 3 payload sizes:**

| Measures | Beats/measure | Notes/beat | Total SVG nodes for tab | Estimated render time |
|----------|--------------:|-----------:|------------------------:|----------------------:|
| 1 (Phase 1) | 4 | 1.5 avg | ~15 (6 strings + 6 labels + ~12 note+rect pairs) = ~40 | <100ms ✓ |
| 4 | 4 | 1.5 avg | ~40 base + 4 × ~24 = ~136 | ~200ms |
| 8 | 4 | 1.5 avg | ~40 base + 8 × ~24 = ~232 | ~400ms |
| 16 | 4 | 1.5 avg | ~40 base + 16 × ~24 = ~424 | ~800ms — approaching the 500-node danger zone |

**Verdict:** 4-8 measures = comfortable. 16 measures = needs refactor. **Sonnet system prompt should bound to 8 measures max** (see §1 Pattern B).

### Memoization patterns

Existing `TabNotation.tsx` re-renders every SVG child element on every parent re-render. This is fine at 1 measure; at 8-16 measures it's expensive.

**Refactor recommendations:**

1. **Extract per-measure component with `React.memo`:**
   ```typescript
   const Measure = React.memo(function Measure({ measure, offsetX }: { measure: MeasureT; offsetX: number }) {
     // ... existing per-measure rendering
   });
   ```
   Because `measure` and `offsetX` are stable across parent re-renders (measures array is immutable from server), this eliminates re-renders during scroll.

2. **Move beat-rendering into pure functional helper:**
   Current code inlines a `.map` inside a `.map`. Extracting `renderBeat(beat, x, y)` allows better tree-shaking.

3. **Consider `useMemo` for the SVG's overall `staffWidth` / `svgWidth` calculation:**
   Cheap, but prevents recompute during unrelated re-renders (loader message tick, etc.).

### Scroll strategies

**Recommendation: horizontal `ScrollView` per tab, one measure per screen-width slice.**

```typescript
<ScrollView horizontal showsHorizontalScrollIndicator={false}>
  <Svg width={totalWidth} height={staffHeight}>
    {measures.map((m, i) => <Measure key={i} measure={m} offsetX={i * MEASURE_WIDTH} />)}
  </Svg>
</ScrollView>
```

**Why not `FlatList`?**
- `FlatList` per measure means N SVGs stitched together = broken staff lines at boundaries.
- Single wide SVG in a horizontal ScrollView keeps the staff continuous.

**Why not vertical stack?**
- Guitarists read tab left-to-right. Vertical stacking loses the temporal reading order.

**Performance ceiling:** A single wide SVG with 8 measures × 24 SVG-node-cost = ~200 nodes is well within react-native-svg's comfort zone. The horizontal scroll clips off-screen content at the native level (RN's UIScrollView / ScrollView is native), so nothing extra to memoize for that path.

### Refactor recommendations

**Existing `TabNotation.tsx` known stubs (from Phase 1 SUMMARY):**
- Renders `measures[0]` only.
- Duration labels omitted.

**Phase 3 changes:**
1. Loop over `measures` instead of `measures[0]`.
2. Add `MEASURE_WIDTH` constant + per-measure offset math.
3. Wrap in `<ScrollView horizontal>`.
4. Add `React.memo` around per-measure block.
5. Add measure-number labels (small text at the top of each measure).
6. **Duration labels remain optional** for Phase 3 — POC user (Hernan) reads tab, doesn't need duration ticks for the target skill breakdown UX.

**Existing `ChordDiagram.tsx` — no changes required.** Already accepts a single Chord and renders correctly.

**Wrapping chord row:** Phase 1's `<ScrollView horizontal>` around the chord row is fine for 4-6 chords. If a Sonnet breakdown returns 10+ chords, add horizontal snap-to-position and consider `FlatList horizontal` for windowing.

## Section 3 — song_catalog Seed Authorship

### Recommended approach

**Hand-curate 10 seed songs. Reject Sonnet-generated seed.**

**Rationale:**
1. **Quality control at small scale is faster than debugging bad seed data.** 10 songs × 2 minutes each = 20 minutes of curation. Sonnet-generating seed + review + correction cycles = 60+ minutes.
2. **The seed catalog is data the deployed app lives with.** A wrong `primary_skill_root` on a seed song will surface as "why did Fletcher hand me a Blues song when I'm working on Fingerstyle?" — a real user-facing bug.
3. **Seeds are ergonomic anchors.** Curator (Hernan) knows why each song is on the list. Sonnet doesn't.

### The 10 songs (per 03-CONTEXT `<specifics>`)

Copying the pre-agreed list from CONTEXT — this is a locked-in curation, not research proposal:

| Song | Artist | Genre | primary_skill_root | difficulty (approx) |
|------|--------|-------|--------------------|-----:|
| Sweet Home Chicago | Robert Johnson / Blues Brothers | Blues | Rhythm | 0.30 |
| Little Wing | Jimi Hendrix | Rock | Chord Voicings | 0.65 |
| Blackbird | The Beatles | Folk/Fingerstyle | Fingerstyle | 0.50 |
| Wonderwall | Oasis | Rock | Rhythm | 0.20 |
| Comfortably Numb | Pink Floyd | Rock | Lead | 0.70 |
| Purple Haze | Jimi Hendrix | Rock | Chord Voicings | 0.65 |
| Hotel California | Eagles | Rock | Chord Voicings | 0.55 |
| Wish You Were Here | Pink Floyd | Rock | Fingerstyle | 0.40 |
| Eruption | Van Halen | Rock | Lead | 0.90 |
| Thunderstruck | AC/DC | Rock | Rhythm | 0.60 |

**Difficulty scale anchors [MEDIUM confidence — Ultimate Guitar convention, adapted]:**
- 0.0-0.35 = Beginner (open chords, minimal syncopation)
- 0.35-0.65 = Intermediate (barre chords, arpeggios, hammer-ons, palm mutes, pinch harmonics)
- 0.65-1.0 = Advanced (tapping, sweep picking, complex time signatures)

Reference: Ultimate Guitar's public difficulty definitions [CITED: https://help.ultimate-guitar.com/en/articles/6749024-website-how-to-choose-the-right-tab-difficulty]. Songsterr does not publish its algorithm [CITED: Quora reference from search].

### Sizing (30-50 vs 10)

**03-CONTEXT proposed 30-50, but recommend 10 for POC:**
- Bank filter is `±0.15 around player_level` — with 10 songs and a difficulty-spread from 0.20 to 0.90, every user has 3-5 catalog songs eligible at any level. Sufficient signal.
- 25% override fires ~7-8 times/month × 1 user = 30 draws → cycling through 10 songs = each catalog song surfaced 2-3x in the first month. Enough for the discovery-surface intent.
- Phase 5 (Library) is when catalog size becomes a UX concern (browsing). Phase 3 doesn't need browsing.

### Difficulty rating references

**Songsterr's algorithm is closed [CITED: https://www.quora.com/How-does-songsterr-com-determine-track-difficulty-for-its-tabs].** They use 5 levels (Beginner / Intermediate / Intermediate+ / Advanced / Master). Their `Hardest Tabs` page [CITED: https://www.songsterr.com/a/wsa/hardest-tabs-a56030] contains anchors we can cross-reference qualitatively (e.g., Van Halen's "Eruption" is a Songsterr top-tier — mapping to 0.90 on our scale).

**Ultimate Guitar's public conventions [CITED: https://help.ultimate-guitar.com/en/articles/6749024-website-what-is-tab-difficulty]:**
- Novice: 3-4 open chords, simple strumming
- Intermediate: full riffs, arpeggios, hammer-ons, palm muting, pinch harmonics
- Advanced: "highly skilled players"

**Recommendation:** hand-anchor Hernan-familiar songs against these definitions using the numeric transformation 0.2 / 0.5 / 0.8 as tier centers with ±0.15 subjective fine-tuning. Document the rationale in a comment above the Alembic seed INSERT block so future curators can extend consistently.

### Authorship cost

- **Curation time:** ~30 minutes for 10 songs (including a quick verification listen).
- **Migration authorship time:** ~30 minutes.
- **Total:** 1 hour. **Zero LLM cost.**
- **Sonnet-generated alternative:** ~$0.05-0.10 for a single generation call producing 30 seed rows + ~90 minutes review time = strictly worse.

## Section 4 — Selector SQL Patterns

See **Architecture Patterns → Pattern 2** above for the full CTE.

### Additional patterns and edge cases

**Player level computation:**
```sql
-- Inline compute per D-05 Claude's Discretion. Cheap for POC (single user, ~50 leaf nodes).
SELECT COALESCE(AVG(mastery), 0.5)::numeric(4,3) AS player_level
FROM skill_nodes
WHERE user_id = :user_id AND level = 'leaf';
```
- `COALESCE(..., 0.5)` gives brand-new users (zero mastery data) a "middle of the road" player_level so the bank filter isn't empty.

**Bank empty case:**
If `bank_pick` returns no row AND `working_on_pick` returns no row (extremely unusual — user has zero songs across all categories and the ±0.15 filter is empty), the CTE's `SELECT` returns NULL for `today_song_id`. Server handler catches this and returns 404 with a Fletcher-voiced empty-state message (per 03-CONTEXT `<specifics>` "Empty working_on state on Today").

**Re-roll:**
Same CTE, different seed:
```sql
SELECT setseed(hashtext(:user_id::text || :local_calendar_day::text || 'reroll') / 2147483647.0);
```
The `'reroll'` suffix ensures the re-roll produces a different sequence than the original draw. Persist a `today_song_choices` row (or a JSONB slot on `users.preferences`) tracking `{"last_reroll_day": "2026-07-29"}` to enforce the "one re-roll per day" cap.

**Persistence of today's choice:**
The selector is deterministic-per-day per user — same query, same day, same result. But if the user re-rolls, we've now used a different seed and picked a different song. **The re-roll must be persisted server-side** (JSONB column or a small `today_song_choices` table with `(user_id, local_calendar_day, song_id, is_rerolled)`) so subsequent GETs return the re-rolled song. Otherwise the deterministic selector would re-compute the pre-reroll pick.

Simplest implementation: `users.preferences.today_song_choice = {"day": "2026-07-29", "song_id": 42, "rerolled": true}`. Server checks this before running the CTE.

## Section 5 — Rating Write Transaction

See **Architecture Patterns → Pattern 3** above for the full transaction.

### Idempotency guard details

**Two layers:**
1. **Application-level SELECT-before-INSERT** for legibility (matches Phase 2's `bootstrap_user` pattern of COUNTing before writing).
2. **Database UNIQUE constraint** on `(user_id, song_id, local_calendar_day)` for correctness under concurrency.

**Alembic 0003 migration for the constraint:**
```python
op.create_table(
    "user_sessions",
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("song_id", sa.Integer, sa.ForeignKey("songs.id"), nullable=False),
    sa.Column("rating", sa.Enum("not_my_tempo", "getting_closer", "thats_what_im_looking_for",
                                 name="rating_tier"), nullable=False),
    sa.Column("local_calendar_day", sa.Date, nullable=False),
    sa.Column("tz_offset_minutes", sa.Integer, nullable=False),
    sa.Column("rated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.UniqueConstraint("user_id", "song_id", "local_calendar_day", name="uq_user_session_daily"),
)
```

### Lock avoidance

- The UPDATE targets skill_nodes belonging to a single user (`WHERE user_id = X`). No lock contention across users.
- Within a user, the transaction is short (~10-100ms) — no long-held locks.
- No explicit `FOR UPDATE` needed because the transaction is a single COMMIT with all writes as one batch. Postgres MVCC handles the isolation.

### Partial-write prevention

The `async with db.begin():` block ensures both the INSERT and UPDATE succeed or roll back together. `AsyncSession` commits at the end of the block; any exception (including the UNIQUE constraint violation from a race) rolls back.

### FastAPI exception handler for UNIQUE violation

```python
@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    if "uq_user_session_daily" in str(exc):
        return JSONResponse(status_code=409, content={"detail": "Already rated this song today."})
    raise exc
```

## Section 6 — Timezone Handling

See **Architecture Patterns → Pattern 5** above for the full injection.

### Design principle

**Client sends offset (minutes); server computes calendar day.** Never the other way around. Client-computed calendar day would be spoofable and would drift under timezone changes (user travels).

### Postgres calendar-day math

The chosen approach: `DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute'))`.

**Why not `AT TIME ZONE 'America/Los_Angeles'`?**
- Requires the client to send a timezone NAME instead of an offset.
- More parsing surface; more spoofing surface.
- Naming is not stable across the year for DST-observing zones (per D-10 accepting timezone-change drift as POC-acceptable).

**Why not `AT TIME ZONE INTERVAL '-07:00'`?**
- Postgres accepts interval-form AT TIME ZONE, but the syntax with a bind parameter is awkward.
- `(now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute')` is cleaner and works identically for the calendar-day computation.

**DST caveat [ASSUMED, per D-10 accepted drift]:**
On a DST-transition day, the offset changes by 60 minutes mid-day. The client always sends the current offset. So if the user practices Sunday at 1am (before spring-forward) then again at 3am (after spring-forward), the offset differs by 60 minutes but the calendar day is the same. This is fine for our use case because we're computing day boundaries, not durations.

## Section 7 — TanStack Query Patterns

See **Architecture Patterns → Pattern 4** above for the full hooks.

### Additional considerations

**MMKV persistence:**
The existing `queryClient.ts` MMKV persister writes through every successful response. `useTodaySong` and `useBreakdown` are automatically persisted — PLAT-04 offline reads are covered without additional code.

**Mutation → invalidation chain:**
When `useSubmitRating` succeeds:
1. Invalidate `['skill-graph', userId]` — mastery changed, next Today song computation reads fresh state.
2. **Do NOT invalidate `['today-song', ...]`** — today's song stays locked (D-05).
3. **Do NOT invalidate `['breakdown', songId]`** — breakdown is cache-forever (D-11).

When `useReroll` succeeds:
1. `setQueryData(['today-song', userId, day], fresh)` — hard-replace today's song.
2. Do not invalidate anything else — the newly-selected song's breakdown will be fetched fresh on tap.

**Loader message rotation reuse:**
Phase 2's `uiStore.loaderMessageIndex` slice is generic — extract into `FletcherLoader` component:
```typescript
export function FletcherLoader({ messages, thresholds }: {
  messages: [string, string, string];
  thresholds: [number, number]; // ms — e.g., [3000, 8000]
}) {
  const { loaderMessageIndex, setLoaderMessageIndex, resetLoader } = useUIStore();
  useEffect(() => {
    const t1 = setTimeout(() => setLoaderMessageIndex(1), thresholds[0]);
    const t2 = setTimeout(() => setLoaderMessageIndex(2), thresholds[1]);
    return () => { clearTimeout(t1); clearTimeout(t2); resetLoader(); };
  }, []);
  return <Text>{messages[loaderMessageIndex]}</Text>;
}
```
Phase 3 breakdown loader uses:
```typescript
<FletcherLoader
  messages={["Fletcher is listening...", "Working out the fingering...", "Almost there..."]}
  thresholds={[3000, 8000]}
/>
```

**Query key stability:**
`localCalendarDay()` returns a string like `"2026-07-29"` derived from `Date.now()` + local offset. On device timezone change or DST transition, the key changes, forcing a refetch. This is the intended behavior — a genuine timezone change (user travels) should re-fetch Today.

## Section 8 — Landmines

Things that would silently break Phase 3 and are non-obvious from the CONTEXT alone.

1. **Sonnet emits `chord.base_fret=1` for chords that are actually barred higher.** Common failure mode. Chord diagram would render at the wrong position. **Mitigation:** system prompt Pattern D explicit teaching; add a post-parse validation that flags chords where `min(pos.fret for pos in positions if pos.fret > 0) - base_fret > FRETS_SHOWN (=4)`.

2. **`setseed()` is session-scoped, but SQLAlchemy async pool reuses connections.** A previous query's `setseed` state can bleed into a subsequent query on the same connection. **Mitigation:** always call `setseed` in the CTE that reads its effect — never rely on a preceding statement's seed. Wrap the whole selector in a single-transaction `AsyncSession.execute()` call.

3. **`hashtext(user_id::text || local_day::text)` can produce identical seeds for different (user_id, day) pairs due to string concatenation ambiguity.** `hashtext('abc123' || '2026-07-29')` == `hashtext('abc' || '1232026-07-29')`. Never a real collision at POC scale, but pedantically: separate with a delimiter — `hashtext(user_id::text || '|' || local_day::text)`.

4. **`local_calendar_day` in `user_sessions` UNIQUE constraint means the rating-per-song-per-day rule is enforced per calendar day per timezone.** If the user re-rated in a different timezone (traveled), the constraint would NOT catch a second rating. Acceptable per D-10 POC drift, but flag for the planner.

5. **Breakdown JSONB write happens OUTSIDE the ratings transaction.** The breakdown write is on `GET /api/v1/songs/{id}/breakdown`. The rating write is on `POST /api/v1/sessions`. These are two independent endpoints. If the breakdown call fails mid-flight, the songs row remains with `breakdown_generated_at IS NULL` — user retries on next tap. Not a landmine per se, but the planner must not couple these.

6. **Phase 2's system-user seed song has `breakdown` populated with placeholder JSONB and `breakdown_generated_at IS NULL`.** Phase 3's cache-check logic (`IF breakdown_generated_at IS NULL then regenerate`) will trigger a Sonnet call the first time this song is served to a user. This is intended per Phase 2 02-03 SUMMARY ("Phase 3 song AI call will overwrite with real technique breakdown"). But: **the placeholder song's `title = "Sweet Home Chicago"` overlaps with the seed catalog `song_catalog` recommendation.** The planner must decide: (a) delete the Phase 1/2 seed song and rely on `song_catalog`; or (b) keep the seed song and dedupe. Recommend (a) — the seed song was a Phase 1 scaffolding artifact.

7. **`skill_nodes.updated_at` has `onupdate=func.now()`.** The tiebreaker in D-01 relies on this — but `updated_at` also fires on non-mastery updates (if any). Currently no other updates exist. If Phase 4 adds fields (e.g., a decay timestamp), the tiebreaker semantics need re-inspection.

8. **`X-Timezone-Offset` header injection in `apiFetch` is a global change.** Every existing Phase 2 endpoint will start receiving it. Server must not fail on the header being present for endpoints that don't consume it. FastAPI ignores unknown headers, so this is safe — but the CORS whitelist (if any) must permit the header.

9. **`react-native-svg` version 15.15.4 API compatibility.** Existing components use `<Rect>`, `<Line>`, `<Circle>`, `<Text>`, `<G>`. All stable. But if the multi-measure refactor adds `<ClipPath>` (per one webresult, some devs use it for scroll optimization), that path has known perf issues on Windows [CITED: https://github.com/software-mansion/react-native-svg/issues/2660]. Windows isn't a target, but flagging that ClipPath isn't a silver bullet.

10. **TanStack Query `staleTime: Infinity` on `useBreakdown` prevents refetch but does NOT prevent MMKV persistence.** The breakdown will be written to MMKV on first fetch, then served from MMKV on cold-start. Correct behavior — but if a breakdown gets corrupted in MMKV (unlikely, but possible on OS-level storage errors), there's no automatic recovery. Recommend adding a `useBreakdown` invalidation path in Settings (deferred: not needed for POC).

11. **Mastery UPDATE clamp using `LEAST(1, GREATEST(0, mastery + shift))` — the literals must be typed as `numeric` not `float`.** SQLAlchemy sometimes serializes `Decimal("1.0")` as `1.0::float`. Test the actual generated SQL. Safer: `text("LEAST(1.0::numeric, GREATEST(0.0::numeric, mastery + :shift::numeric))")`.

12. **`song_catalog` seed data (Alembic migration INSERT) must run BEFORE any migration that could reference it.** No such downstream reference exists in Phase 3, but Phase 4 or 5 might. Standard Alembic ordering handles this.

13. **The re-roll persistence in `users.preferences.today_song_choice` is a lightly-typed JSONB write.** No FK enforcement on `song_id`. If a Phase 5 delete-song flow removes a song that's currently the re-rolled Today song, the next GET could 404. Acceptable POC drift; flag for the planner.

## Section 9 — Open Questions for the Planner

Things this research couldn't fully close.

1. **Does Sonnet 4.6 handle deeply nested `$ref` in tool_use `input_schema`?**
   - **What we know:** Phase 2 `SonnetOnboardingOutput.model_json_schema()` worked (03-03 SUMMARY confirms Sonnet ran clean). But `Breakdown` is deeper (Tab → Measures → Beats → Notes) plus nested Chord → ChordPosition + TechniqueNote arrays.
   - **What's unclear:** Whether Pydantic's default `$ref` output causes Sonnet to reject the tool definition or emit malformed tool_use.
   - **Recommendation:** First task in Phase 3 planning should be a **spike/probe** — a 10-minute test calling Sonnet with the actual `Breakdown.model_json_schema()` and validating the output. If it fails, add a dereference step before publishing the tool_def. This is a cheap test.

2. **What's the acceptable p50 latency for the breakdown fetch UX?**
   - **What we know:** Sonnet output at ~2000 tokens = 8-15s p50 on Sonnet 4.6. The objective's target of "<5s p50" is not achievable with this payload.
   - **What's unclear:** Whether the user (Hernan) is fine with 10s of loader time, or whether we need to shrink the breakdown output (fewer measures, shorter technique_notes) to hit a tighter target.
   - **Recommendation:** Plan for 10-15s p50. Loader rotation at 0s/3s/8s already accommodates this. If it feels bad in real use, tighten the `max_tokens` and system prompt bounds in a follow-up.

3. **Should the `song_catalog` genre be an enum, free-form string, or derived from `primary_skill_root`?**
   - **What we know:** 03-CONTEXT specifies `genre text` as free-form.
   - **What's unclear:** Whether the Library tab in Phase 5 will filter by genre — if so, an enum would be safer.
   - **Recommendation:** Free-form string in Phase 3 (per CONTEXT); Phase 5 can migrate to enum when it defines the Library filter UX.

4. **How does the reroll-persistence interact with cold-start of the app on a new device?**
   - **What we know:** `users.preferences.today_song_choice` is server-side; cold-start on a new device would preserve the reroll state (device UUID differs, so it's a different user — new device = fresh Today song).
   - **What's unclear:** Whether the same-device-different-network case (user has offline stale cache) causes stale reroll flag to show.
   - **Recommendation:** MMKV write-through covers this — the stale `TodaySongResponse.rerolled=true` shows correctly offline; server is source of truth online. Not a landmine, but worth explicit test.

5. **Does the mastery UPDATE need to bump `skill_nodes.updated_at` explicitly, or does `onupdate=func.now()` fire on the SQL UPDATE?**
   - **What we know:** `onupdate` in SQLAlchemy fires on ORM-level updates. It does NOT fire on `update(SkillNode).values(...)` bulk-update statements automatically — only if the column is included in the SET clause.
   - **What's unclear:** Whether SQLAlchemy 2.0 async's `update()` includes `onupdate`-declared columns by default.
   - **Recommendation:** Explicitly include `updated_at=func.now()` in the UPDATE .values() to guarantee the tiebreaker in D-01 works. Test with a manual SQL trace.

6. **Should the "From the bank" tag (visible signal for 25% override / empty-working_on) also fire on the initial 100% bank-random path (D-04)?**
   - **What we know:** 03-CONTEXT `<specifics>` says "The bank-random path should be visibly SIGNALED to the user via subtle Fletcher-voiced copy."
   - **What's unclear:** Whether an empty-working_on user (freshly re-onboarded) sees this tag or a different empty-state message.
   - **Recommendation:** Planner decides. Suggestion: same tag ("From the bank"), no separate copy — signals to the user that the pick came from the shared catalog, which is honest.

7. **Are there prompt-injection concerns for the Sonnet breakdown call?**
   - **What we know:** Phase 2 `run_onboarding_parse` mitigates via static-label wrapping of user text (`_format_user_message`).
   - **What's unclear:** For Phase 3, the user text is `song_title`, `song_artist`, and skill names — all controlled by the server (song_title/artist from database, skill names from `skill_nodes`). No direct user text goes into the prompt.
   - **Recommendation:** Lower injection surface than Phase 2. Still wrap fields with static labels per Phase 2 pattern for defense in depth. No additional threat model needed.

## Environment Availability

Not applicable — no new external dependencies. All packages (anthropic, SQLAlchemy, Alembic, @tanstack/react-query, react-native-svg, MMKV, Zustand) already installed and verified via Phase 1 and Phase 2. Anthropic API key already configured on Railway (Phase 2 03 verification).

## Security Domain

Config: `security_enforcement: true`, `security_asvs_level: 1`, `security_block_on: high`.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | No auth in POC (single-user). `X-User-ID` header is device-UUID identity, not authentication. |
| V3 Session Management | no | Same — no server-side sessions in POC. |
| V4 Access Control | yes | Rating write must UPDATE only the target user's skill_nodes (`WHERE user_id = X-User-ID`). Server MUST NOT trust `body.song_id` to be a song the user has access to — validate via `SELECT ... WHERE user_id = X-User-ID AND id = body.song_id`. |
| V5 Input Validation | yes | Pydantic validates rating enum, song_id type. `X-Timezone-Offset` range-checked (±840 minutes). Sonnet tool_use output revalidated via `Breakdown.model_validate()`. |
| V6 Cryptography | no | No new crypto in this phase. |

### Known Threat Patterns for FastAPI + Sonnet + Postgres

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Rating write for another user | Elevation of Privilege | UPDATE constrained by `WHERE user_id = X-User-ID`; Access Control test in mocked pytest |
| Song access for another user | Information Disclosure | GET breakdown validates `songs.user_id = X-User-ID` OR song is in `song_catalog` (public) |
| Prompt injection via song_title / artist | Tampering | Wrap fields with static labels per Phase 2 `_format_user_message` pattern. Server-owned data (not user text) so surface is low but pattern applies. |
| SQL injection via `X-Timezone-Offset` | Tampering | FastAPI's `Header()` typed to `str` then `int(x)` conversion — no raw string interpolation. Bind param `:tz` in the CTE. |
| DoS via repeat re-roll | Denial of Service | UNIQUE constraint on `(user_id, local_calendar_day)` in the re-roll persistence path; one re-roll per day cap enforced at DB level. |
| Anthropic API key leak | Information Disclosure | Client never sees the key; all calls proxied via `server/app/ai/`. Phase 2 pattern already enforced. |
| Cache poisoning via malformed Sonnet response | Tampering | `Breakdown.model_validate()` on tool_use.input; validation errors trigger retry, then 503. Never persist an invalid breakdown. |
| Mastery drift via non-atomic write | Tampering | `async with db.begin():` wraps INSERT + UPDATE; single transaction; UNIQUE constraint on session prevents double-write. |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Sonnet 4.6 breakdown output is ~1500-3000 tokens at typical size | §1 cost estimate | Cost per breakdown could be 2x higher if output is longer. Still well within $20/mo cap. Bounded by `max_tokens=8192`. |
| A2 | Sonnet 4.6 p50 latency is 8-15s for ~2000-token structured output | §1 latency | UX loader rotation timings (3s / 8s) would need adjustment if actual latency differs significantly. Easy to tune. |
| A3 | Pydantic `Breakdown.model_json_schema()` `$ref` output is accepted by Sonnet 4.6 tool_use | §1 Pitfall 1 | If rejected, need dereference step. See Open Question 1 — cheap probe test recommended. |
| A4 | react-native-svg at ~200 SVG nodes for an 8-measure tab renders in <500ms on mid-range devices | §2 rendering limits | If slower, need to further reduce SVG node count (e.g., drop the Rect-behind-Text pattern). |
| A5 | Ultimate Guitar's Beginner/Intermediate/Advanced maps cleanly to 0.2/0.5/0.8 numeric anchors | §3 difficulty | Subjective mapping. Fine-tuning at curation time by the human curator. |
| A6 | Anthropic beta `strict: true` tool-use is Sonnet 4.5 / Opus 4.1 only (not Sonnet 4.6) as of research date | Standard Stack Alternatives | Even if available, we're recommending against for consistency with Phase 2. Non-blocking. |
| A7 | SQLAlchemy 2.0 async's `update(...).values(...)` does NOT auto-fire `onupdate` on unincluded columns | Open Question 5 | If wrong (i.e., it does auto-fire), including `updated_at=func.now()` is redundant but harmless. |
| A8 | Postgres `setseed` scope is per-connection and does not persist across pool checkouts | §8 Landmine 2 | Documented Postgres behavior [CITED database.guide]. But the safe pattern (always setseed in the CTE that reads it) makes this a non-issue. |
| A9 | 10 seed songs is sufficient for the ±0.15 bank filter to yield non-empty results for any player_level | §3 sizing | If empty at extreme player_levels (0.0 or 1.0), planner can widen the filter to ±0.25 as fallback. |
| A10 | The device-UUID identity model (Phase 2 D-04) is sufficient for Phase 3 access control (no separate authorization layer) | Security V4 | Confirmed per Phase 2 03-CONTEXT. No auth in POC per PROJECT constraints. |

## Sources

### Primary (HIGH confidence)

- Phase 2 canonical patterns: `.planning/phases/02-onboarding-skill-graph/02-03-SUMMARY.md`, `.planning/phases/02-onboarding-skill-graph/02-04-SUMMARY.md`
- Phase 1 canonical patterns: `.planning/phases/01-foundation-empty-loop/01-CONTEXT.md`, `.planning/phases/01-foundation-empty-loop/01-02-SUMMARY.md`
- Existing code: `server/app/ai/onboarding.py`, `server/app/ai/client.py`, `server/app/models/song.py`, `server/app/models/db.py`, `mobile/src/api/apiClient.ts`, `mobile/src/api/queryClient.ts`, `mobile/src/components/TabNotation.tsx`, `mobile/src/components/ChordDiagram.tsx`, `mobile/src/store/uiStore.ts`
- Phase 3 context: `.planning/phases/03-ai-teacher-song-of-the-day/03-CONTEXT.md`
- Anthropic Claude Sonnet 4.6 pricing (official): https://www.anthropic.com/news/claude-sonnet-4-6
- Anthropic Structured Outputs docs (official): https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- PostgreSQL Date/Time Functions docs (official): https://www.postgresql.org/docs/current/functions-datetime.html
- TanStack Query invalidation docs (official): https://tanstack.com/query/latest/docs/framework/react/guides/query-invalidation

### Secondary (MEDIUM confidence)

- Postgres setseed / deterministic random: https://database.guide/how-setseed-works-in-postgresql/, https://www.cybertec-postgresql.com/en/making-random-deterministic/
- react-native-svg performance (issues + community): https://github.com/software-mansion/react-native-svg/issues/1319, https://github.com/react-native-svg/react-native-svg/issues/1470, https://geekyants.com/blog/optimizing-svg-rendering-in-react-native-from-react-native-svg-to-expo-image
- Ultimate Guitar tab difficulty conventions: https://help.ultimate-guitar.com/en/articles/6749024-website-how-to-choose-the-right-tab-difficulty, https://help.ultimate-guitar.com/en/articles/6749024-website-what-is-tab-difficulty
- Anthropic structured outputs blog: https://claude.com/blog/structured-outputs-on-the-claude-developer-platform

### Tertiary (LOW confidence)

- Songsterr closed-algorithm difficulty rating: https://www.quora.com/How-does-songsterr-com-determine-track-difficulty-for-its-tabs, https://www.songsterr.com/a/wsa/hardest-tabs-a56030
- Anthropic pricing analyses (multiple sources agreeing): https://openrouter.ai/anthropic/claude-sonnet-4.6, https://apidog.com/blog/claude-sonnet-4-6-pricing/

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — 100% code-verified reuse from Phase 1/2
- Architecture (selector CTE, atomic transaction, timezone math): HIGH — patterns are standard Postgres + FastAPI
- Sonnet cost + latency: MEDIUM — pricing verified from official Anthropic docs; specific per-breakdown token estimates are extrapolations from Phase 2 experience and general Sonnet 4.6 output characteristics
- react-native-svg limits at 4-8 measures: MEDIUM — extrapolated from community-reported node-count limits; needs on-device verification during Phase 3 execution
- song_catalog difficulty mapping: LOW-MEDIUM — Ultimate Guitar convention documented but subjective; Songsterr algorithm closed
- Structured output pitfalls (deep $ref, strict tool_use availability): MEDIUM — documented in Anthropic docs + community reports; needs spike test in Phase 3

**Research date:** 2026-07-29
**Valid until:** 2026-08-28 (30 days — Sonnet model behavior and pricing are stable at monthly cadence; react-native-svg patterns are stable at quarterly cadence)

## RESEARCH COMPLETE
