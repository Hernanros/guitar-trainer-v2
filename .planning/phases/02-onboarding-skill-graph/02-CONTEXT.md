# Phase 2: Onboarding & Initial Skill Graph - Context

**Gathered:** 2026-07-20
**Status:** Ready for planning

<domain>
## Phase Boundary

A new user completes a ~5–8 minute onboarding once (songs they can play / working on / aspire to, style tags, session preferences). The server persists a 3-level DAG skill graph with 5-bpm tempo bins seeded from those answers. Onboarding is re-runnable from Settings.

**In scope:**
- Onboarding router at the app root (`if !onboarded → /onboarding else → /(tabs)`)
- 5-section wizard: Welcome / Play / Working on / Aspire / Preferences (each Fletcher-intro'd, standard UI captures data)
- Free-text song input per song category, MMKV-buffered
- Batch Sonnet call on Complete tap: parses song text + emits initial skill graph in a single API call
- Persistent user identity via device-generated UUID + MMKV `onboarded_at` marker
- Postgres schema: `users`, `songs` (with `category` enum), `song_skills` junction, `skill_nodes` (per-user, `parent_id` tree with `level` enum root|sub|leaf, `tempo_bin_low/high`, `mastery`)
- Alembic migrations for the new tables + a schema shift on the existing single-song `songs` (see D-14 discussion below)
- Settings screen with "Re-run onboarding" action (wipes user's `songs` + `skill_nodes`, keeps `user_id`, re-seeds)
- Fletcher voice throughout — copy governed by [[fletcher-identity]] (`.planning/design/fletcher-identity.md`)

**Out of scope for this phase:**
- AI teacher breakdown / Song-of-Day per-user selector (Phase 3 — Sonnet call for song technique)
- Session ratings + deterministic mastery writes (Phase 3)
- Cost governor (Phase 4)
- Cross-user skill node dedup + verifier + curator queue (Phase 4)
- Nightly 5% decay (Phase 4)
- Library search / add / remove UI (Phase 5)
- Push notifications for streak reminders (deferred — needs OS permission flow, not in Phase 2 scope)

</domain>

<decisions>
## Implementation Decisions

### Onboarding UX shape

- **D-01:** **5 sections — Welcome / Play / Working on / Aspire / Preferences.** Each song category gets its own section (giving Aspire the emotional weight it deserves as the retention hook). Welcome is the Fletcher joke-landing moment. Estimated 6–8 min total.
- **D-02:** **Sectioned wizard — Fletcher voice on section intros, efficient UI for input capture.** Not conversational (chat-style abandons at 40%+), not pure form (weakest voice landing). Each section opens with a full-screen Fletcher intro card (voice + optional character portrait per [[fletcher-identity]]) then transitions to efficient input UI. Progress indicator visible (5 dots).
- **D-03:** **MMKV-only wizard state; server touch happens once on Complete tap.** Wizard state (current section, typed-so-far text per section) lives entirely in MMKV. Nothing hits Postgres until the user taps Complete. On re-open mid-flow: resume from last completed section with a Fletcher line ("Welcome back. You left off at [section]."). No partial-user state on the server, no cleanup for abandoned onboardings.
- **D-04:** **Device UUID as user identity; MMKV `onboarded_at` timestamp as the onboarded marker.** First launch generates a v4 UUID, stores it in MMKV as `user_id`. All API requests send `X-User-ID: <uuid>` header. On Complete, POST `/api/v1/users` bootstraps the user row + skill graph. Root layout redirect: `onboarded_at == null` → `/onboarding`, else → `/(tabs)`. Settings "re-run" nulls the timestamp and clears wizard state. Reinstall = new UUID = re-onboard (acceptable POC behavior). **This decision resolves the multi-user seams gray area deferred from Phase 1** — `users` table introduced, no auth, UUID identity, sharing-ready.

### Song input UX

- **D-05:** **Free-text per section, single Sonnet batch parse on Complete.** Each song category (Play / Working / Aspire) has a single multi-line text area. User types naturally: `"Sweet Home Chicago, Wonderwall, working on Little Wing"`. On Complete, ONE Sonnet 4.6 call processes all three text lists + emits the initial skill graph. Estimated cost: ~$0.02–$0.05 per onboarding. Simplest orchestration, single failure surface, cheapest total tokens.
- **D-06:** **Sonnet call outputs both songs (canonicalized) + full skill graph in a single response.** Response shape (contract defined in Pydantic per D-02 from Phase 1): `{ songs: [{title, artist, category, skills: [skill_node_ref]}], skill_graph: {nodes: [...], edges: [...]} }`. One call = songs identified + song_skills junction populated + skill_nodes proposed under the fixed root taxonomy.
- **D-07:** **Failure handling — one silent retry with widened timeout, then fail-open with a bootstrap graph.** If Sonnet fails twice, save raw text input to `users.raw_onboarding_text` (JSONB column), mark onboarding complete with a minimal bootstrap graph (root domains only, no sub-domains or leaves — populate later via user session activity). Fletcher copy on the Complete screen: `"Got what you said. I'll fill in the details as we go."` User enters the app immediately.

### Skill graph seeding

- **D-08:** **Fixed root domains; Sonnet writes sub-domains and leaves within them.** Product locks the root taxonomy (initial proposal — refine in Phase 2 UI-phase / planning): **Rhythm, Lead, Chord Voicings, Fingerstyle, Music Theory, Timing**. Sonnet never invents new root domains; sub-domains and leaf skills are Sonnet-generated within these roots. Aligns with Phase 4's dedup/verifier work (canonical roots reduce collision surface).
- **D-09:** **Normalized per-user tree — `skill_nodes` table with `parent_id`.** Schema: `skill_nodes (id UUID PK, user_id FK, name text, level enum('root'|'sub'|'leaf'), parent_id FK nullable, tempo_bin_low int nullable, tempo_bin_high int nullable, mastery numeric default 0.0, created_at, updated_at)`. Root nodes have `parent_id = NULL`. Leaves carry tempo bin range (5-bpm width). Simplifies from true DAG to tree (leaf has one parent) — defers multi-parent DAG + canonical shared nodes to Phase 4 when dedup work also introduces `canonical_node_id` FK.
- **D-10:** **Songs table with `category` enum + `song_skills` junction.** Schema: `songs (id UUID PK, user_id FK, title text, artist text, category enum('can_play'|'working_on'|'aspirational'), created_at)`. Junction: `song_skills (song_id FK, skill_node_id FK, weight numeric default 1.0)`. Aspirations live at the song level, not the skill level — enables the retention hook query ("skills where song.category='aspirational' and mastery<0.5"). Also becomes the foundation for the Library tab in Phase 5.
- **D-11:** **All mastery starts at 0 after onboarding — deterministic writes principle preserved.** Sonnet writes STRUCTURE (nodes + edges) but NOT mastery numbers. Mastery is EARNED via Phase 3 session ratings. Category on the song is the metadata Fletcher uses for recommendation ("prove it again" for can_play, "still working" for working_on, "getting closer" for aspirational) — not a number in the skill graph. Matches PROJECT.md's "deterministic writes to skill graph from self-report only" principle.

### Session preferences

- **D-12:** **Session length — preset chips (15 / 30 / 45 / 60 min), single-select, no default.** Force intentional choice. Fletcher intro: `"How long do you have most days?"`. Stored in `users.preferences JSONB` as `{session_length_min: int}`.
- **D-13:** **Retention format — single-select radio (streak / weekly digest / monthly milestone), Fletcher-voiced descriptions.** Options: `"Streak: I show up daily, count me."` / `"Weekly digest: show me what I did on Sunday."` / `"Monthly milestone: mark the big wins."` If user picks nothing, default to `streak`. Stored in `users.preferences JSONB` as `{retention_format: 'streak'|'weekly_digest'|'monthly_milestone'}`. Setting is changeable in Settings later.

### Data-model migration considerations

- **D-14:** **Existing `songs` table needs a user scope shift.** Wave 1 shipped a single-row `songs` table (Sweet Home Chicago) serving everyone. Phase 2 makes songs user-scoped. Recommended migration approach for the planner:
  1. Add `user_id` FK to existing `songs` (nullable to preserve existing row).
  2. Introduce a bootstrap "system" user in the initial Phase 2 migration + attach the Sweet Home Chicago row to it.
  3. New Alembic revision (0002) handles: add users table, add user_id to songs, add category enum + column to songs, add song_skills junction, add skill_nodes table, seed system user, backfill Sweet Home Chicago's user_id to the system user.
  4. The Song-of-Day endpoint (currently `SELECT * FROM songs LIMIT 1`) either: (a) stays pointed at the system user for Phase 2 (Phase 3 refactors to per-user selector), OR (b) refactors now to accept X-User-ID header + fall back to system user if none provided. Planner decides.

### Claude's Discretion

- **Root domain vocabulary** — proposed: **Rhythm, Lead, Chord Voicings, Fingerstyle, Music Theory, Timing.** Iterate during Phase 2 UI-phase / planning if these don't feel right for the guitarist archetype. Consider adding: Rhythm-and-Blues Feel, Alternate Tunings — hold off until we see how Sonnet actually populates sub-domains on real inputs.
- **Onboarding copy** — every screen's Fletcher voice lands in [[fletcher-identity]]'s territory. Phase 2 UI-phase produces UI-SPEC.md with concrete copy per section. Character portrait usage per section is a UI-phase concern.
- **Session length units** — minutes. Metric prompt shown as "min" not "minutes" (space-constrained on chips).
- **Tempo bin representation** — columns on `skill_nodes` (`tempo_bin_low`, `tempo_bin_high` int columns). Simpler than a separate `tempo_bins` table. Phase 3 rating writes update mastery on the node directly.
- **Settings re-run behavior** — wipes user's `songs` + `song_skills` + `skill_nodes`, keeps `user_id` + `users.preferences`. Preserves preferences across re-runs since they're not song-derived. Confirm with a Fletcher-voiced dialog ("Start over? You'll keep your session preferences. Songs and skills reset.").
- **Empty section handling** — no minimums, no gates. If a section is empty, Sonnet parses gracefully ("You didn't list songs you can play yet — we'll build from what you're working on."). Only true blocker: all three song sections empty → nudge (soft warning "Fletcher needs at least one song to build from"). Not a hard block; if user insists, save raw text + bootstrap graph.
- **X-User-ID header validation** — server accepts any well-formed UUID for POC. No verification. Phase 4+ cost governor may attach rate limits per user_id at that point.
- **User bootstrap endpoint** — `POST /api/v1/users` with body `{user_id: UUID, songs: {can_play: [...], working_on: [...], aspirational: [...]}, preferences: {session_length_min, retention_format}, raw_input: {...}}`. Server: creates user row, calls Sonnet, persists everything atomically. Returns the bootstrapped skill graph shape for the client to cache.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Roadmap, requirements, and product principles
- `.planning/ROADMAP.md` — Phase 2 section (goal, mode, dependencies, requirements list, success criteria)
- `.planning/REQUIREMENTS.md` — ONB-01 through ONB-04 (onboarding), SKILL-01 (3-level DAG), SKILL-02 (5-bpm tempo bins)
- `.planning/PROJECT.md` — Constraints (single-user POC + multi-user seams), Key Decisions incl. "deterministic writes to skill graph from self-report only" and the Fletcher naming decision
- `RECOVERED-DESIGN.md` — onboarding copy notes ("add a few songs" guidance vs threshold; aspirational hook "you're 3 skills away from Eruption")

### Voice + copy contract
- `.planning/design/fletcher-identity.md` — **REQUIRED** reading for any user-facing copy in Phase 2. Governs every wizard-intro, empty-state, error, and completion copy line. Follow the "diagnosis → next step → confidence signal" pattern.

### Phase 1 decisions that constrain Phase 2
- `.planning/phases/01-foundation-empty-loop/01-CONTEXT.md` — D-01 (full payload shape), D-02 (Pydantic → OpenAPI → generated TS types), D-03 (semantic music JSON), D-04 (DB-backed endpoint pattern) — these patterns extend to all new Phase 2 API + DB work
- `.planning/phases/01-foundation-empty-loop/01-SKELETON.md` — Walking Skeleton architecture reference
- `.planning/phases/01-foundation-empty-loop/01-01-SUMMARY.md` — server + mobile scaffold outcomes (what already exists)
- `.planning/phases/01-foundation-empty-loop/01-04-SUMMARY.md` — Ship-to-devices deviations (Railway builder is Railpack; DATABASE_URL scheme is `postgresql://`; healthcheck cold-start realities; `.npmrc legacy-peer-deps` for EAS)

### Downstream phase awareness (do not implement, but avoid painting into corners)
- `.planning/ROADMAP.md` Phase 3 (AI teacher + real Song-of-Day selector) — the songs table + skill_nodes must be shaped for Phase 3's per-user selector to work naturally
- `.planning/ROADMAP.md` Phase 4 (cost governor + node verification + decay) — schema needs to be dedup-ready (D-08 fixed roots, D-09 normalized), and the Sonnet call in D-05 needs to be structured for governor interception

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

**Server (`server/`):**
- `server/app/db/session.py` — `AsyncSessionLocal`, `get_db` dependency, DATABASE_URL scheme rewrite (handles both `postgres://` and `postgresql://`). Reuse as-is for new endpoints.
- `server/app/models/song.py` — Pydantic model pattern (nested response models, `from_attributes=True` for ORM). Mirror this pattern for new `UserResponse`, `SkillNodeResponse`, `SongResponse` (upgraded from Phase 1's stub).
- `server/app/models/db.py` — SQLAlchemy ORM `Song` model with JSONB column. Same pattern for new `User`, `SkillNode`, `SongSkill` ORM classes.
- `server/app/api/v1/song_of_day.py` — endpoint pattern (`AsyncSession` dep injection, Pydantic response model). Copy structure for `/api/v1/users` (POST bootstrap), `/api/v1/users/{user_id}` (GET current state), `/api/v1/users/{user_id}/skill-graph` (GET graph).
- `server/alembic/versions/0001_initial_songs_table.py` — Alembic revision pattern with JSONB, `postgresql://` scheme handling in env.py. Follow same pattern for revision `0002_users_songs_categorization_skill_graph.py`.

**Mobile (`mobile/`):**
- `mobile/src/api/queryClient.ts` — QueryClient + MMKV persister. Reuse; onboarding wizard state can use direct MMKV (not TanStack) since it's UI-local, not server data.
- `mobile/src/api/songOfDay.ts` — TanStack Query hook pattern. Copy for new hooks: `useUser`, `useSkillGraph`, `useUserBootstrap` (mutation).
- `mobile/src/store/uiStore.ts` — Zustand stub. Onboarding wizard state (current section, per-section text) can live here.
- `mobile/src/app/_layout.tsx` — root layout with `PersistQueryClientProvider`. Add onboarded-check here: read `onboarded_at` from MMKV, redirect accordingly.
- `mobile/src/app/(tabs)/_layout.tsx` — tab layout unchanged. Phase 2 adds `/onboarding/` sibling route group.
- `mobile/src/components/themed-text.tsx`, `themed-view.tsx` — theming primitives. Onboarding uses these for consistency.

### Established Patterns

- **Async SQLAlchemy 2.0 + Alembic** — every DB touchpoint uses `AsyncSession`; migrations use sync Alembic with the scheme rewrite pattern.
- **Pydantic v2 + `from_attributes=True`** — ORM → response conversion; JSONB columns as nested Pydantic models.
- **`postgres://` / `postgresql://` scheme rewrite** — session.py + alembic/env.py both handle both schemes (per D-14 in Phase 1's Wave 3 lesson).
- **Expo Router file-based routing** — `src/app/(tabs)/` for tabs, add `src/app/onboarding/` for the wizard route group.
- **MMKV write-through via TanStack Query persister** — reuse for server-data cache; use raw MMKV writes for onboarding-local wizard state.
- **X-User-ID header** — new pattern introduced in Phase 2, will govern all future Phase 3+ endpoints.

### Integration Points

- **Root layout onboarded-check** — `mobile/src/app/_layout.tsx` gains a redirect based on MMKV `onboarded_at`. Before this Phase, root was pure — after this Phase, it routes.
- **Existing `songs` table shift** — the Wave 1 hardcoded single-row table needs user scoping. Migration `0002` handles it (see D-14). Song-of-Day endpoint may need refactoring depending on planner's choice.
- **API client header injection** — mobile client fetch needs to send `X-User-ID` on every request. Add a fetch wrapper in `mobile/src/api/` that reads `user_id` from MMKV and injects the header.
- **Onboarding router lives at `mobile/src/app/onboarding/`** — new route group. `_layout.tsx` for the wizard shell, `index.tsx` = Welcome, `play.tsx`, `working-on.tsx`, `aspire.tsx`, `preferences.tsx`. Or a single `[step].tsx` dynamic route with the section as a param — planner decides.
- **Settings route** — needs a new tab or nested route (`/(tabs)/toolkit/settings` or `/settings` outside tabs). Currently no Settings screen exists — Phase 2 introduces it. Placement is a UI-phase decision.

### Not reusable (must build new)

- User identity system (UUID generation, MMKV storage, header injection)
- Onboarding wizard shell + section navigation + progress dots
- Fletcher intro card component (image slot + copy + Next button)
- Free-text song input component (multi-line, no autocomplete, placeholder-guided)
- Session length chip selector + retention format radio group
- Sonnet API client on server (new pattern — first LLM call in the codebase)
- Sonnet prompt engineering for song-parse + skill-graph output
- User bootstrap endpoint + response typing

</code_context>

<specifics>
## Specific Ideas

- **The Sonnet call is Phase 2's first LLM touchpoint.** Wire it through a thin `server/app/ai/` module. Keep the module structure open enough for Phase 4's cost governor to wrap it (governor intercepts every LLM call — the module boundary is the interception point).
- **Sonnet prompt design** should be output-typed: request structured JSON matching the Pydantic response shape. Use OpenAI-compatible tool-use or Anthropic's tool-use for structured output. Prompt should include (a) the fixed root taxonomy, (b) constraints on sub-domain/leaf granularity, (c) example output for one song, (d) instruction that mastery values are always 0.
- **Onboarding welcome screen is the Fletcher joke-landing moment** per [[fletcher-identity]]. Copy direction: `"Meet Fletcher. He's the teacher Fletcher should have been."` + Continue button. Character portrait (deferred to UI-phase) sits above.
- **The Fletcher intro card component** is reusable across all 5 sections. Slots: image (optional), heading, body copy, primary CTA. Same component, different content per section.
- **Fletcher voice on the "Fletcher is thinking" loader** matters. Not a generic spinner. Copy suggestion: `"Fletcher is listening..."` for 0–3s, `"Working on your first lesson plan..."` for 3–8s, `"Almost there..."` for 8+ s. Vary to reduce perceived latency.
- **Aspire section deserves the most weight** per RECOVERED-DESIGN. Consider a larger text area, more evocative placeholder text (`"What are you chasing? Eruption? Purple Haze? Everything by Julian Lage? Type anything — Fletcher understands."`), and a slightly longer intro copy setup.
- **Session length chip labels** should show as `15 min` / `30 min` / `45 min` / `60 min` — the "min" abbreviation matters on mobile chip widths.
- **The bootstrap "system" user for Wave 1's Sweet Home Chicago row** — use a well-known sentinel UUID like `00000000-0000-0000-0000-000000000000` so any query can filter to "user or system content".

</specifics>

<deferred>
## Deferred Ideas

- **Autocomplete-against-song-DB (MusicBrainz/Spotify) song input** — noted as v1.1+ upgrade if free-text parse quality proves insufficient. Adds infrastructure but improves data quality.
- **Hybrid input (user confirms Sonnet's proposed matches)** — v1.1+ if we want tighter data control.
- **Cross-device onboarding continuity** — server-persisted partial-user state so user can start on phone, continue on tablet. Deferred until multi-device is a real user need.
- **Push notifications for streak reminders** — requires iOS/Android permission flows + backend job queue + Expo Push tokens. Deferred to a dedicated Notifications phase or to Phase 4/5 depending on user pull.
- **Skill graph re-run "diff" mode** — instead of wiping, show user what would change if they re-run onboarding. Nice UX polish, not POC critical.
- **Onboarding-time mastery self-rating** — the "rate each song 1–5" section discussed in Q4 of skill graph seeding. Deferred: violates deterministic-writes principle; ratings from Phase 3 sessions are the authoritative mastery source.
- **Multi-parent DAG (canonical shared nodes)** — Phase 4 introduces `canonical_node_id` FK + cross-user dedup. Phase 2 ships single-parent tree.
- **Character portrait per Fletcher intro card** — deferred to Phase 2 UI-phase. The intent is captured in [[fletcher-identity]]; asset acquisition + placement is a UI-phase decision.
- **Full stub JWT infrastructure** — X-User-ID header is enough for POC. Real auth (JWT + refresh + password/OAuth) is a dedicated Auth phase, not Phase 2's problem.
- **Fletcher-conversational onboarding UI** — full chat-style. Deferred to a v1.1+ experiment if the sectioned-wizard doesn't land well enough.

</deferred>

---

*Phase: 2-Onboarding & Initial Skill Graph*
*Context gathered: 2026-07-20*
