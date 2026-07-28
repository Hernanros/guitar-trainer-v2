# Phase 3: AI Teacher & Song of the Day - Context

**Gathered:** 2026-07-28
**Status:** Ready for planning

<domain>
## Phase Boundary

Every morning, the user opens the Today tab and sees exactly one song chosen deterministically from their skill graph and preferences. Tapping the song fetches a Sonnet 4.6 technique breakdown (rendered as tab + chord diagrams via `react-native-svg`). After practicing, the user submits a self-report rating in one tap; deterministic rules update mastery on all attached skill_nodes; tomorrow's Song of the Day reflects yesterday's rating.

**In scope:**
- Deterministic Song-of-Day selector: 75% weakest-skill-in-working_on + 25% random-from-bank
- Song "bank" — hybrid of user's own aggregate (`working_on ∪ can_play ∪ aspirational`) and a small curated seed catalog shipped with Fletcher
- Sonnet 4.6 `run_technique_breakdown` call — new function in `server/app/ai/`, mirrors 02-03's `run_onboarding_parse` pattern
- Full breakdown payload landing in the existing `songs.breakdown` JSONB column (Phase 1 D-01 schema already accommodates this)
- 3-tier Fletcher-voiced rating pills ("Not my tempo" / "Getting closer" / "That's what I'm looking for")
- Deterministic mastery update: fixed additive shifts per rating tier applied to every skill_node in the song's `song_skills` junction, clamped [0, 1]
- Daily rotation at local midnight (device timezone) with one re-roll allowed per day
- Empty-working_on fallback → bank-random selection (same code path as the 25% override)

**Out of scope for this phase:**
- Node dedup + Sonnet verifier + curator queue (Phase 4 — SKILL-04)
- Nightly 5% decay job (Phase 4 — SKILL-05)
- Cost governor / per-user rate caps / Console cap (Phase 4)
- Library search / browse / add / remove UI (Phase 5)
- Metronome + tuner (Phase 5)
- Weighted `song_skills.weight` per skill (deferred — Sonnet doesn't emit weights yet at onboarding; would require reopening Phase 2)
- Streak bonus math on mastery updates (deferred — retention format is a display concern per Phase 2 D-13, not a mastery-write concern)
- Any onboarding wizard changes (Phase 2 is closed)

</domain>

<decisions>
## Implementation Decisions

### Song-of-Day selector

- **D-01: 75/25 mixture.** The primary rule is a deterministic pick: `argmin(mastery)` over `skill_nodes` joined to songs where `songs.category = 'working_on'` for this user. Tiebreaker on the leaf: `updated_at ASC` (longest-untouched wins). Pick the song attached to that leaf via `song_skills`. On top of that, roll a `random()` — 25% of the time, override the deterministic pick with a bank-random selection (see D-02/D-03).
- **D-02: Bank = hybrid.** The "bank" is the union of the user's own songs (all three categories) AND a small curated seed catalog Fletcher ships with. The seed catalog gives new users a discovery surface AND provides songs when `working_on` is thin. Planner: propose ~30–50 seed songs across the fixed root taxonomy (Rhythm/Lead/Chord Voicings/Fingerstyle/Music Theory/Timing) for POC scope.
- **D-03: Bank filtered by player level.** `player_level = mean(mastery) over user's leaf skill_nodes`. Bank narrowed to songs whose primary-skill difficulty is within ±0.15 of that level. This means the bank grows as the user levels up. New tables/columns implied: `song_catalog` (seed songs global to all users) + a difficulty/primary_skill tag per catalog row. Planner: propose the exact schema; `song_catalog.primary_skill_root` might be an enum matching the fixed root taxonomy, and `song_catalog.difficulty` might be a numeric [0, 1] on the same scale as mastery so comparison is direct.
- **D-04: Empty working_on = same bank-random.** If the user's `working_on` list is empty (bootstrap-mode user, freshly re-onboarded with no `working_on` input, etc.), the selector short-circuits to a 100% bank-random pick. Same code path as the 25% override — no separate fallback branch.
- **D-05: One re-roll per day.** After Today renders, the user can swap once. Then the selection is locked until the daily rotation trigger fires. Preserves Fletcher's authority (not a Spotify "skip" button), but gives an escape hatch for genuinely-wrong picks (e.g., a song the user just outgrew). Cost bound: at most 2 breakdown calls per user per day.

### Rating UX + mastery math

- **D-06: 3-tier Fletcher-voiced pills.** After a song's breakdown renders, a rating row sits below the tab/chords/technique-notes stack. Three big taps, side by side: **"Not my tempo"** / **"Getting closer"** / **"That's what I'm looking for"** — signature Fletcher-vocabulary reused as coaching (per `.planning/design/fletcher-identity.md`). One tap submits; no confirm step. Rating stored per session on `user_sessions` (new table — planner to design).
- **D-07: Rating writes to all attached skill_nodes.** The rating shifts mastery on **every** leaf skill in `song_skills` for this song, weighted equally. Matches how a real practice session builds multiple skills at once. Downside accepted: fine-grained "this song is 70% shuffle, 30% chord voicing" attribution is deferred until Sonnet emits weights at onboarding (Phase 2 doesn't).
- **D-08: Fixed additive mastery math.** `Not my tempo` → mastery += -0.05. `Getting closer` → mastery += +0.05. `That's what I'm looking for` → mastery += +0.15. Clamped to [0, 1]. Deterministic, predictable, no session-history required to compute — matches PROJECT.md's "deterministic writes to skill graph from self-report only" principle. Ratings are one-shot; no "undo".

### Daily rotation trigger

- **D-09: Local midnight, unconditional.** Today's song rolls over at midnight in the user's local timezone. Fresh song every calendar day regardless of whether yesterday's got rated. Simplest semantic; missed-day = missed graph update, that's the trade. Streak accounting (D-13 retention format from Phase 2) reads the same `user_sessions` table so a missed rating naturally breaks a streak.
- **D-10: Device timezone at first launch each day.** Client sends its TZ offset as a query param on `GET /api/v1/today-song` (or as an `X-Timezone-Offset` header — planner's choice). Server uses that offset to determine whether "today" has already been selected for this user. No user-config surface, no wizard change. Timezone-change (user travels) is acceptable POC drift.

### Claude's Discretion

- **Sonnet breakdown timing = lazy on first tap, cache-forever per song.** Not user-decided but the natural default: don't pre-generate breakdowns at Song-of-Day selection time (adds background cost + latency for a call the user might never make). Fetch on tap; cache in `songs.breakdown` JSONB once written; never regenerate for the same song. Planner: expose a `breakdown_generated_at` column on `songs` so the caching signal is legible; if `breakdown` JSONB is populated for that song, serve it without a Sonnet call.
- **Sonnet call structure mirrors 02-03.** New function `run_technique_breakdown(song_title, song_artist, target_skill_names, user_level)` at `server/app/ai/breakdown.py`. Returns a structured tool-use response matching the Pydantic `Breakdown` schema Phase 1 already defined (tab measures, chord positions, technique notes). One retry with widened timeout; on second failure, return HTTP 503 with a Fletcher-voiced error (client shows a retry button, not a fail-open — a bad breakdown is worse than no breakdown here).
- **Fletcher loader copy during Sonnet breakdown.** "Fletcher is listening..." → "Working out the fingering..." (@3s) → "Almost there..." (@8s). Same three-message rotation pattern from 02-04's `preferences.tsx` — planner can factor it out into a shared component.
- **Rating write endpoint = `POST /api/v1/sessions`.** Body: `{song_id, rating: "not_my_tempo" | "getting_closer" | "thats_what_im_looking_for"}`. Server: creates a `user_sessions` row + updates `skill_nodes.mastery` for every skill in `song_skills` where `song_id = body.song_id AND user_id = X-User-ID`. Single transaction — no partial writes. Idempotency: check if a session for this user + today's date already exists; if so, return 409 (one rating per song per day).
- **Player level cached in `users.preferences` JSONB.** Recomputed on Song-of-Day request (cheap SQL: `AVG(mastery)`). Caching decision deferred to planner if `AVG` on skill_nodes gets slow; for POC (single user, hundreds of nodes max), inline compute is fine.
- **`GET /api/v1/today-song` payload shape** — planner defines. Suggest `{song_id, song, breakdown_available: bool, rerolled: bool}` where `song` is the current existing `SongResponse` and `breakdown_available` signals whether the client should show "Tap to see breakdown" vs the breakdown itself (breakdown fetched by a separate `GET /api/v1/songs/{id}/breakdown`).
- **Re-roll endpoint** — `POST /api/v1/today-song/reroll` with idempotent daily-flag check server-side. If already re-rolled today, 409.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Roadmap, requirements, and product principles
- `.planning/ROADMAP.md` — Phase 3 section (goal, mode, dependencies, requirements, success criteria)
- `.planning/REQUIREMENTS.md` — SOTD-01 through SOTD-05, SKILL-03 exact texts
- `.planning/PROJECT.md` — Constraints (deterministic writes principle, $20/mo cap), Key Decisions (Sonnet 4.6 for teaching content, mobile-first)

### Voice + copy contract (MANDATORY for any user-facing string)
- `.planning/design/fletcher-identity.md` — governs rating pill labels, loader rotation, error messages, re-roll copy, empty-state copy. Diagnosis → next step → confidence signal pattern applies.

### Phase 1 decisions that constrain Phase 3
- `.planning/phases/01-foundation-empty-loop/01-CONTEXT.md` — D-01 (full breakdown payload shape already defined for Phase 3), D-02 (Pydantic → OpenAPI → generated TS types), D-03 (semantic music JSON contract for tab + chords)
- `.planning/phases/01-foundation-empty-loop/01-02-SUMMARY.md` — `TabNotation.tsx` + `ChordDiagram.tsx` implementation (reuse as-is)
- `.planning/phases/01-foundation-empty-loop/01-01-SUMMARY.md` — server + mobile scaffold

### Phase 2 decisions that constrain Phase 3
- `.planning/phases/02-onboarding-skill-graph/02-CONTEXT.md` — D-08 (fixed root taxonomy), D-09 (per-user `skill_nodes` tree schema), D-10 (`songs` + `song_skills` junction), D-11 (all mastery starts at 0 / deterministic writes principle)
- `.planning/phases/02-onboarding-skill-graph/02-03-SUMMARY.md` — `server/app/ai/` module structure + SAVEPOINT + idempotency guard pattern (mirror for Phase 3's breakdown call)
- `.planning/phases/02-onboarding-skill-graph/02-04-SUMMARY.md` — Fletcher loader rotation + fail-open card patterns (mirror for breakdown-loading UX)

### Downstream phase awareness (do not implement, but avoid painting into corners)
- `.planning/ROADMAP.md` Phase 4 (cost governor + node verification + decay) — the Sonnet breakdown call must go through `server/app/ai/` module boundary so Phase 4 can wrap it. Session ratings + mastery writes should NOT couple to a decay timestamp yet; Phase 4 adds that.
- `.planning/ROADMAP.md` Phase 5 (Library + Toolkit) — Library tab consumes `songs` and the new `song_catalog`; keep both schemas queryable by category/genre/skill so Phase 5 can filter without further migration.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

**Server (`server/`):**
- `server/app/api/v1/song_of_day.py` — Phase 1 stub returning `SELECT * FROM songs LIMIT 1`. Phase 3 refactors this endpoint into the deterministic per-user selector. Keep the response shape (`SongResponse`) unchanged for backwards compat with the Today tab.
- `server/app/ai/onboarding.py` — `run_onboarding_parse` pattern (Sonnet tool-use + SAVEPOINT-guarded caller). Mirror it for `run_technique_breakdown` in `server/app/ai/breakdown.py`.
- `server/app/ai/client.py` — Shared `AsyncAnthropic` singleton via `get_client()`. Reuse — do NOT instantiate a second client.
- `server/app/models/db.py` — `Song`, `SkillNode`, `SongSkill`, `User` ORM. `SkillNode.mastery` has both `default=Decimal("0.0")` and `server_default=text("0.0")` per hotfix. Reuse; add `song_catalog` + `user_sessions` tables in migration 0003.
- `server/app/models/song.py` — `SongResponse` Pydantic + `Breakdown` schema (tab + chords + technique notes). Reuse. Add `BreakdownResponse` if Phase 3 wants to split the breakdown fetch from the song metadata fetch.
- `server/alembic/versions/0002_...` — Alembic pattern with raw-SQL enum creation + DEFERRABLE FK + system-user backfill. Mirror for migration 0003.

**Mobile (`mobile/`):**
- `mobile/src/app/(tabs)/index.tsx` — Today tab already renders full `SongResponse` including `TabNotation` + `ChordDiagram`. Phase 3 wires this to `useTodaySong` (new) + adds the rating pills below the existing breakdown stack.
- `mobile/src/components/TabNotation.tsx` — Reuse as-is.
- `mobile/src/components/ChordDiagram.tsx` — Reuse as-is.
- `mobile/src/components/FletcherIntroCard.tsx` — Reuse for the empty-state / error card if the bank-random path returns nothing (edge case).
- `mobile/src/api/apiClient.ts` — `apiFetch` wrapper already injects `X-User-ID`. Add `X-Timezone-Offset` here (single interception point, no per-hook change).
- `mobile/src/api/users.ts` — `useSkillGraph` hook already exists (from 02-04). Phase 3 adds `useTodaySong`, `useBreakdown(songId)`, `useSubmitRating`, `useReroll` following the same TanStack Query pattern.
- `mobile/src/store/uiStore.ts` — `loaderMessageIndex` slice already exists (from 02-04). Reuse for the breakdown loader rotation.

### Established Patterns

- **Async SQLAlchemy 2.0 + Alembic** — every DB touchpoint uses `AsyncSession`.
- **Pydantic v2 + `from_attributes=True`** — ORM → response with the `SkillNodeResponse.coerce_level_enum` field_validator pattern for enum-heavy tables.
- **`postgres://` / `postgresql://` scheme rewrite** — session.py + alembic/env.py both handle it.
- **X-User-ID header + apiFetch centralization** — every API request goes through `apiFetch`. Do NOT add a second fetch path.
- **Sonnet call in `server/app/ai/` module** — Phase 4 governor interception point. Any new LLM call MUST live here.
- **SAVEPOINT-guarded LLM calls** — Phase 2's `run_onboarding_parse` uses `async with db.begin_nested():` around the AI call; Phase 3's breakdown call is idempotent per-song (see D-11 caching) so SAVEPOINT is less critical, but keep the pattern for consistency.
- **Idempotency guard on writes** — Phase 2's `bootstrap_user` COUNTs before writing to short-circuit re-POSTs. Mirror for `POST /api/v1/sessions` (one rating per user per song per day → 409 on repeat).

### Integration Points

- **`GET /api/v1/song-of-day` (existing)** — refactored from Phase 1's stub into the per-user selector. Response shape unchanged: `SongResponse`.
- **`GET /api/v1/songs/{song_id}/breakdown` (new)** — separate endpoint so the Today tab can show song metadata immediately and stream the breakdown on user tap.
- **`POST /api/v1/sessions` (new)** — write rating + update mastery in one transaction.
- **`POST /api/v1/today-song/reroll` (new)** — one re-roll per day guard; returns a new `SongResponse`.
- **`song_catalog` (new table)** — global seed songs. Schema: `id UUID PK, title text, artist text, genre text, primary_skill_root enum('rhythm'|'lead'|'chord_voicings'|'fingerstyle'|'music_theory'|'timing'), difficulty numeric(4,3) CHECK (difficulty BETWEEN 0 AND 1), breakdown JSONB nullable, created_at`. Seeded via Alembic 0003.
- **`user_sessions` (new table)** — session rating log. Schema: `id UUID PK, user_id FK, song_id FK, rating enum('not_my_tempo'|'getting_closer'|'thats_what_im_looking_for'), rated_at timestamptz, tz_offset_minutes int`. `UNIQUE (user_id, song_id, DATE(rated_at AT TIME ZONE 'UTC' + tz_offset_minutes * interval '1 minute'))` OR simpler: `UNIQUE (user_id, song_id, local_calendar_day text)` where `local_calendar_day` is computed client-side. Planner decides.

### Not reusable (must build new)

- Deterministic Song-of-Day selector SQL (75/25 mixture, argmin over working_on, hybrid bank filtered by player level)
- `run_technique_breakdown` Sonnet call + prompt engineering
- 3-tier rating pill component
- `user_sessions` write endpoint + mastery-update transaction
- `song_catalog` seed data (30–50 songs with primary_skill + difficulty tags)
- Re-roll endpoint + daily-flag check
- Timezone offset injection in `apiFetch`

</code_context>

<specifics>
## Specific Ideas

- **Selector as one SQL statement** if possible. The 75/25 mixture can be expressed as `SELECT * FROM (working_on_argmin UNION ALL bank_random) ORDER BY random() > 0.75 LIMIT 1` — planner may find an even cleaner expression. Deterministic within the day means the random seed should be `(user_id, local_calendar_day)` so repeated fetches on the same day return the same song.
- **Rating pill styling** — reuse `RetentionFormatRadio.tsx` visual pattern (highlighted-selection card) from Phase 2 but adapt for horizontal 3-way selection with big tap targets. Fletcher orange for the confirmed selection.
- **Breakdown loader on tap** — 3-message rotation, then the tab + chord stack renders. Cache the response client-side in TanStack Query with `staleTime: Infinity` since D-11 says breakdown is never regenerated.
- **The bank-random path** should be visibly SIGNALED to the user via subtle Fletcher-voiced copy — e.g., the Today card shows "From the bank" as a small tag when the 25% override fires. Users deserve to know why today isn't what they expected.
- **`song_catalog` seed authorship** — Phase 3 planner should not hand-curate 50 songs manually. Options: (a) one-shot Sonnet call at migration time producing the seed JSON; (b) hand-pick 10 canonical Blues/Rock/Fingerstyle songs and defer the rest. Recommend (b) for POC scope — Sweet Home Chicago + Little Wing + Blackbird + Wonderwall + Comfortably Numb + Purple Haze + Hotel California + Wish You Were Here + Eruption + Thunderstruck. Hand-picked keeps quality control tight.
- **Re-roll button placement** — bottom-right of the Today card, ghost button style, single line: `Not this one? Give me another. (1 left today)` — decrements to `(0 left today)` grayed out after use.
- **Empty working_on state on Today** — no separate screen. The 100% bank-random selection just happens silently. If the bank comes back empty too (extremely unusual — user has zero songs across all categories AND the seed catalog is somehow empty), THEN show a Fletcher card pointing at Settings re-run.
- **Timezone offset header** — always sent, `X-Timezone-Offset: -240` (minutes). Server truncates to UTC calendar day + offset for the "same-day" comparison. Cleaner than fighting with `DATE()` and `AT TIME ZONE` in SQL.

</specifics>

<deferred>
## Deferred Ideas

- **Weighted `song_skills.weight`** — Sonnet emits per-skill weight per song, mastery shifts proportionally rather than equal-weighted. Requires a Phase 2 wizard change and a Sonnet output schema extension. Deferred to a follow-up phase after we have real-user data on whether the equal-weighted model produces obvious mis-tracking.
- **Streak bonus on mastery** — +0.02 per consecutive day of ratings, on top of the base additive. Deferred — mastery math is the wrong place for retention motivation; streak display lives in Phase 2's retention_format preference (D-13) and can be surfaced without touching the write path.
- **Multiplier mastery math** — swap fixed additive for `×0.95/×1.05/×1.15`. Faster low-mastery climbs, natural ceiling saturation. Deferred until D-08 shows misfit — additive is easier to reason about and debug.
- **Slider or 5-tier rating UI** — deferred; the 3-tier Fletcher-voiced pill is the "signature" version. Revisit if we see users straining to express nuance.
- **Sonnet breakdown pre-generation at midnight** — pre-warm today's breakdown at rotation time so the tap is instant. Deferred — lazy-on-tap is cheaper and matches the actual usage curve (some users never tap the breakdown).
- **Regeneration of an existing breakdown** — user requests a different explanation. Deferred; per D-11 caching, breakdown is never regenerated. If users complain about a bad breakdown, we revisit as its own decision.
- **Multi-song days** — user gets to practice 2 songs per day. Deferred — the discipline of "one song, focus, rate" is the whole product thesis.
- **Skip-without-rating penalty** — skipping today's song penalizes mastery on its attached skills. Deferred; feels punitive and could game the system.
- **Cross-user song_catalog contributions** — users upload songs to the shared catalog. Deferred to a shareability phase post-PMF.
- **Song lyrics or chord charts beyond the semantic Breakdown JSON** — deferred; Fletcher's job is technique, not sing-along.
- **Practice timer / session length enforcement** — using `users.preferences.session_length_min` to display a countdown during practice. Deferred; the preference is retention-facing (Fletcher tone of "let's do 30 min") not enforcement.
- **Regenerating today's song after re-roll is used** — once re-roll is spent, that's it. Deferred if the "1 re-roll per day" feels too restrictive in real usage.

</deferred>

---

*Phase: 3-AI Teacher & Song of the Day*
*Context gathered: 2026-07-28*
