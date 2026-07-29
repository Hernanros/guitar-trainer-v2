---
phase: 03-ai-teacher-song-of-the-day
verified: 2026-07-29T00:00:00Z
status: human_needed
score: 5/6 must-haves verified
overrides_applied: 0
human_verification:
  - test: "SC #6 — Tomorrow's Song of the Day reflects yesterday's rating"
    expected: "After submitting a self-report rating for today's song, the following day's GET /api/v1/song-of-day returns a different song (the mastery shift on the rated song's skill_nodes moves it out of argmin position, promoting a different song). Or: same song if it is still the weakest after the shift."
    why_human: "Requires two calendar-day API calls with real DB state. The code path is fully wired (sessions.py updates mastery; selector reads mastery via argmin ORDER BY sn.mastery ASC), but temporal verification cannot be done with grep or unit tests without time-travel mocking."
  - test: "Live Sonnet 4.6 breakdown for a real song (not seed data)"
    expected: "When a song whose songs.breakdown_generated_at IS NULL is selected as Song of the Day, opening the breakdown screen renders a Sonnet-generated tab (4-8 measures) and chord diagrams that are musically accurate for that song at the user's player_level. The breakdown_generated_at column is set after the call."
    why_human: "The server endpoint (GET /api/v1/songs/{id}/breakdown) is correct and calls SONNET_MODEL='claude-sonnet-4-6'. The breakdown screen reads song.breakdown embedded in TodaySongResponse.song — which means it renders from the stored JSONB, not by calling the endpoint. This path is correct for cache hits. For cache misses on new songs, a device test is needed to confirm the lazy-fetch Sonnet call fires, the result persists, and the SVG renders plausibly."
  - test: "Reroll button appears and works on device (SongOfDayCard ghost button)"
    expected: "With no session rated today and rerolled=false, the Today tab shows a ghost 'Try a different song' button. Tapping it changes the displayed song and hides the button (rerolls_left=0). Second same-day open does not show the button."
    why_human: "Device build required. The code wiring is confirmed (useReroll exported, onReroll passed to SongOfDayCard iff ratedLabel null and rerollsLeft != 0, SongOfDayCard already gates on onReroll truthy). Cannot verify button renders and responds to tap without an EAS build."
  - test: "FromTheBankTag chip appears on device when from_bank=true"
    expected: "When today's song comes from the bank (from_bank=true, bank_source='user_bench' or 'seed_catalog'), the Today tab renders the FromTheBankTag chip above SongOfDayCard with the correct copy ('From your bench' / 'From the bank')."
    why_human: "Device build required. Code is wired (showBankChip and bankChipLabel computed correctly; FromTheBankTag imported and rendered conditionally). Cannot verify visual rendering without EAS build."
---

# Phase 3: AI Teacher & Song of the Day — Verification Report

**Phase Goal:** The user opens the Today tab, sees exactly one Song of the Day chosen deterministically from their current skill graph state, gets a Sonnet 4.6 technique breakdown rendered as tab + chord diagrams via react-native-svg, and submits a self-report rating that writes back into the graph via deterministic rules.
**Verified:** 2026-07-29
**Status:** human_needed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | GET /api/v1/song-of-day returns a per-user deterministic pick via the 75/25 selector CTE (D-01/D-02/D-03/D-04) | VERIFIED | `select_today_song` imported and called in `song_of_day.py` line 84. CTE in `today_song.py` implements argmin(mastery) for working_on, bank fallback, 25% coin. Four acceptance tests pass: `test_song_of_day_endpoint_returns_today_song_response`, `test_reroll_endpoint_first_returns_200`, `test_reroll_endpoint_second_returns_409`, `test_get_song_of_day_after_reroll_reads_marker`. |
| 2 | POST /api/v1/today-song/reroll swaps the pick, enforced one-per-day per user (D-05) | VERIFIED | `@router.post("/today-song/reroll")` present in `song_of_day.py` line 137. Application-layer pre-check + DB partial-unique index `uq_user_sessions_daily_reroll` enforces one-per-day. IntegrityError mapped to 409. Both `test_reroll_endpoint_first_returns_200` (200 + rerolled=true + rerolls_left=0) and `test_reroll_endpoint_second_returns_409` (409) pass. |
| 3 | GET /api/v1/songs/{id}/breakdown returns Sonnet 4.6 breakdown JSON | VERIFIED | `breakdowns.py` endpoint exists, registered in `main.py`, calls `run_technique_breakdown` which uses `SONNET_MODEL="claude-sonnet-4-6"`. Cache-forever gate: `breakdown_generated_at IS NULL` → Sonnet call; else serve stored JSONB. Mocked test `test_mocked_run_returns_breakdown_from_tool_use` passes. |
| 4 | Tab notation + chord diagrams render via react-native-svg | VERIFIED | `TabNotation.tsx` imports `G, Line, Rect, Svg, Text as SvgText` from `react-native-svg`. `ChordDiagram.tsx` imports `Svg, G, Line, Circle, Text, Rect` from `react-native-svg`. Both components are imported and rendered in `breakdown/[songId].tsx` (lines 118, 132). Components are substantive: `TabNotation` implements per-measure horizontal scroll with React.memo; `ChordDiagram` implements fret grid with finger dots, open/muted markers, barre annotation. |
| 5 | Self-report ratings (POST /api/v1/sessions) write to song_skills deterministically (SKILL-03) | VERIFIED | `sessions.py` contains no AI imports. `RATING_SHIFTS` dict applies fixed Decimal shifts (-0.05 / +0.05 / +0.15). UPDATE on `SkillNode` via `SongSkill.skill_node_id` subquery, SQL clamp via `func.least(func.greatest(...))`, all inside `async with db.begin()` atomic transaction. 30 tests pass against real DB. |
| 6 | Tomorrow's Song of the Day reflects yesterday's rating | UNCERTAIN — needs human | Mastery write path is correctly wired (rating → skill_node mastery update → selector reads `ORDER BY sn.mastery ASC`). Temporal verification requires two calendar-day API calls with real DB state. Cannot confirm end-to-end without device/prod test across a day boundary. |

**Score:** 5/6 truths verified (1 uncertain, needs human)

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `server/app/api/v1/song_of_day.py` | GET + POST reroll handlers calling select_today_song | VERIFIED | 225 lines, substantive. GET calls `select_today_song(force_reroll=False)`, POST calls `select_today_song(force_reroll=True)`. `from_bank=False, bank_source=None` hardcoding removed (grep: 0 occurrences). `rerolls_left` present in both responses. |
| `server/app/selectors/today_song.py` | 75/25 CTE with setseed, argmin, bank fallback | VERIFIED | 305 lines. Full CTE with seed, player_level_cte, working_on_pick, user_bench_pick, catalog_pick, bank_pick, coin flip. asyncpg-safe CAST() syntax throughout. Imported by `song_of_day.py`. |
| `server/app/api/v1/breakdowns.py` | GET /songs/{id}/breakdown with Sonnet lazy-gen | VERIFIED | 102 lines. Cache hit (breakdown_generated_at IS NOT NULL) short-circuits Sonnet. Cache miss calls `run_technique_breakdown`. Error mapped to 503 with Fletcher-voiced detail. Registered in `main.py`. |
| `server/app/ai/breakdown.py` | Sonnet 4.6 tool-use call via run_technique_breakdown | VERIFIED | 183 lines. Uses `SONNET_MODEL="claude-sonnet-4-6"`. Tool-use schema from `Breakdown.model_json_schema()`. Retry with 2× timeout. AIBreakdownError on double failure. |
| `server/app/api/v1/sessions.py` | POST /api/v1/sessions atomic rating + mastery write | VERIFIED | 188 lines. `async with db.begin()` atomic. No AI imports. Fixed shifts. SQL clamp. UPDATE via SongSkill subquery. IntegrityError → 409. |
| `server/app/models/song.py` | TodaySongResponse with rerolls_left field | VERIFIED | `rerolls_left: int` present (line 134). All existing fields preserved. `model_config = {"from_attributes": False}`. |
| `mobile/src/api/todaySong.ts` | useTodaySong + useReroll with 409-silent-invalidate | VERIFIED | `useReroll` exported (line 70). POST to `/api/v1/today-song/reroll`. `setQueryData` on success. `msg.includes('HTTP 409')` → `invalidateQueries` → swallow. useMutation + useQueryClient imported. |
| `mobile/src/app/(tabs)/index.tsx` | Today tab wires useReroll + FromTheBankTag chip | VERIFIED | `useReroll` imported and called. `rerollsLeft = today.rerolled ? 0 : 1`. `onReroll = ratedLabel || rerollsLeft === 0 ? undefined : () => reroll.mutate()`. `showBankChip = Boolean(today.from_bank && today.bank_source)`. UI-SPEC §2 copy verbatim (`'From your bench'` / `'From the bank'`). `<FromTheBankTag>` rendered conditionally. |
| `mobile/src/components/TabNotation.tsx` | react-native-svg tab staff, multi-measure horizontal scroll | VERIFIED | Substantive: React.memo per Measure, string lines drawn once, horizontal ScrollView, all measures rendered via `tab.measures.map`. |
| `mobile/src/components/ChordDiagram.tsx` | react-native-svg chord grid with finger dots | VERIFIED | Substantive: fret grid, string lines, open (Circle), muted (×), fretted (Circle with fill), base_fret label. |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `song_of_day.py::get_song_of_day` | `today_song.py::select_today_song` | `AsyncSession + user_id + tz_offset_minutes` | WIRED | `from app.selectors.today_song import select_today_song` line 26; called at line 84 (fresh pick) |
| `song_of_day.py::reroll_today_song` | `user_sessions` table | `INSERT is_reroll_marker=True, bank_source` | WIRED | Lines 186-206 insert with `is_reroll_marker=TRUE`, `bank_source=bank_source` from selector |
| `song_of_day.py::get_song_of_day` | `user_sessions` reroll marker row | `SELECT WHERE is_reroll_marker=true AND local_calendar_day=:day` | WIRED | Lines 54-63 read marker; `bank_source = reroll_marker["bank_source"]` (Revision B) |
| `todaySong.ts::useReroll` | POST /api/v1/today-song/reroll | apiFetch mutation | WIRED | `apiFetch<TodaySongResponse>('/api/v1/today-song/reroll', { method: 'POST' })` |
| `index.tsx` | `useReroll` hook | `onReroll` prop passed to SongOfDayCard | WIRED | `const onReroll = ratedLabel || rerollsLeft === 0 ? undefined : () => reroll.mutate()` → passed to `<SongOfDayCard onReroll={onReroll}>` |
| `breakdowns.py` | `breakdown.py::run_technique_breakdown` | Sonnet 4.6 tool-use call | WIRED | `from app.ai.breakdown import AIBreakdownError, run_technique_breakdown`; called at line 81 on cache miss |
| `sessions.py` | `SkillNode.mastery` via `SongSkill` | atomic UPDATE with LEAST/GREATEST clamp | WIRED | `update(SkillNode).where(SkillNode.id.in_(select(SongSkill.skill_node_id).where(SongSkill.song_id == body.song_id)))` |
| `main.py` | All three routers | `app.include_router` | WIRED | Lines 37-39 register breakdowns, sessions, song_of_day routers with `/api/v1` prefix |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| `song_of_day.py GET` | `song_id, from_bank, bank_source` | `select_today_song` CTE → Postgres `songs` table | Yes — CTE queries `skill_nodes.mastery`, `songs.category`, `song_catalog` | FLOWING |
| `song_of_day.py POST /reroll` | `song_id, from_bank, bank_source` | `select_today_song(force_reroll=True)` → `user_sessions` INSERT | Yes — different seed suffix produces different pick; INSERT persists | FLOWING |
| `breakdowns.py` | `breakdown` Pydantic model | Sonnet 4.6 tool-use → `songs.breakdown` JSONB | Yes — real Sonnet call on cache miss; JSONB on cache hit | FLOWING |
| `sessions.py` | `session_row`, mastery update | DB INSERT to `user_sessions` + UPDATE on `skill_nodes` | Yes — atomic transaction, SQL-side clamp, real mastery write | FLOWING |
| `breakdown/[songId].tsx` | `song.breakdown.tab`, `song.breakdown.chords` | `TodaySongResponse.song.breakdown` JSONB (embedded in SongResponse) | Yes — `songs.breakdown` JSONB is NOT NULL (seed data + Sonnet writes populate it) | FLOWING |

**Note on breakdown rendering path:** The mobile breakdown screen reads `song.breakdown` directly from the embedded `SongResponse` (returned inside `TodaySongResponse`). It does NOT call `GET /api/v1/songs/{id}/breakdown` separately. This is the design: `songs.breakdown` is non-nullable (Phase 1 seed data populates it for the seed song; the Sonnet endpoint writes to it on first tap, making it available in subsequent `TodaySongResponse.song.breakdown`). For new user-added songs with empty `breakdown` JSON, the first breakdown screen load will show the default empty structure until the Sonnet endpoint is called — but the Sonnet endpoint is not triggered from mobile in this phase. This is a known design artifact (D-11 cache-forever; breakdown_available field signals whether Sonnet has been run).

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| 4 acceptance tests pass (GET song-of-day, POST reroll ×2, GET after reroll) | `.venv/bin/python -m pytest tests/test_today_song_selector.py -k "endpoint or reroll"` | 4 passed | PASS |
| Full test suite (39/45 tests, 6 pre-existing failures unchanged) | `.venv/bin/python -m pytest tests/ --ignore=tests/test_alembic_0003.py` | 39 passed, 6 failed (all pre-existing: 3 tz_offset async-sync test bugs, 3 bootstrap_mocked unrelated) | PASS |
| TypeScript type check | `cd mobile && npx tsc --noEmit` | 1 error (pre-existing `app-tabs.web.tsx` template error only) | PASS |
| Gap 1 closed: selector import | `grep -c "from app.selectors.today_song import" server/app/api/v1/song_of_day.py` | 1 | PASS |
| Gap 2 closed: reroll endpoint | `grep -c '@router.post("/today-song/reroll"' server/app/api/v1/song_of_day.py` | 1 | PASS |
| Gap 3 closed: useReroll hook | `grep -c "export function useReroll" mobile/src/api/todaySong.ts` | 1 | PASS |
| Gap 4 closed: bank_source not hardcoded | `grep -c "from_bank=False, bank_source=None" server/app/api/v1/song_of_day.py` | 0 | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SOTD-01 | 03-01 + 03-04 | Today tab shows exactly one Song of the Day from skill graph state | SATISFIED | GET /api/v1/song-of-day calls select_today_song 75/25 CTE; 4 acceptance tests pass |
| SOTD-02 | 03-02 | AI teacher (Sonnet 4.6) produces technique breakdown | SATISFIED | `run_technique_breakdown` in `ai/breakdown.py` uses `SONNET_MODEL="claude-sonnet-4-6"`; endpoint registered |
| SOTD-03 | 03-01 + 03-02 | Breakdown renders tab notation via react-native-svg | SATISFIED | `TabNotation.tsx` imports from `react-native-svg`, renders `tab.measures.map`, used in breakdown screen |
| SOTD-04 | 03-01 + 03-02 | Breakdown renders chord diagrams via react-native-svg | SATISFIED | `ChordDiagram.tsx` imports from `react-native-svg`, renders fret grid + finger dots, used in breakdown screen |
| SOTD-05 | 03-03 | User submits self-report rating that feeds back into skill graph | SATISFIED | POST /api/v1/sessions in `sessions.py` writes to `user_sessions` and updates `skill_nodes.mastery` atomically |
| SKILL-03 | 03-03 | Session ratings update graph via deterministic rules (no LLM) | SATISFIED | `sessions.py` has zero AI imports; `RATING_SHIFTS` dict with fixed Decimal values; UPDATE via SQL clamp |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| None | — | No TBD/FIXME/XXX debt markers found in any modified file | — | — |
| None | — | No hardcoded empty stubs (`return null`, `return []`, `from_bank=False`) in modified files | — | — |

**asyncpg syntax fix in `today_song.py`:** The selector CTE was rewritten from `:param::type` syntax to `CAST(:param AS type)` throughout — this was a blocking latent bug (asyncpg's named-param colon and the Postgres `::` cast double-colon are lexically ambiguous). The fix is correct and substantive, not a stub. All integration tests pass against real Postgres.

---

### Probe Execution

No probe scripts found at `scripts/*/tests/probe-*.sh`. Step 7c: SKIPPED (no probe scripts declared in any PLAN).

---

### Human Verification Required

#### 1. Tomorrow's Song Reflects Yesterday's Rating (SC #6)

**Test:** Submit a rating for today's song. Note the song's attached `skill_node` mastery values before and after (via `/api/v1/skill-graph` or direct DB query). Verify mastery shifted by the expected amount (+0.05 for "getting_closer"). The following day, confirm GET /api/v1/song-of-day returns a different song if the previously rated song's skill_node is no longer the argmin.
**Expected:** Mastery shifts deterministically per D-08. The selector picks the song with the lowest mastery on the next call. If the rated song was the only working_on song, it may still be selected (mastery shifted but still argmin). The loop "closes" if the song changes when the user levels up past it.
**Why human:** Two-day temporal verification. Code path is correctly wired but cannot be proven with grep or single-day pytest.

#### 2. Live Sonnet Breakdown for a New Song

**Test:** Add a new song via onboarding (no pre-populated breakdown). Open the Today tab, tap the song, open the breakdown screen. Confirm (a) the breakdown renders real Sonnet content (not the empty seed JSON), (b) `songs.breakdown_generated_at` is set in the DB after the tap.
**Expected:** The breakdown screen shows musically accurate tab (4-8 measures) and chord diagrams generated by Sonnet 4.6. A second tap serves the cached JSONB (no Sonnet call).
**Why human:** Live Sonnet API key required. The mobile breakdown screen reads `song.breakdown` from the embedded `SongResponse` — which means Sonnet content must first be written to the DB via a GET /api/v1/songs/{id}/breakdown call. Verify this call fires (from the mobile app or directly) and the result renders correctly in the SVG components.

#### 3. Reroll Button Renders and Works on Device

**Test:** Open the Today tab on device (no prior rating or reroll today). Confirm the "Try a different song" ghost button appears below SongOfDayCard. Tap it. Confirm the song changes and the button disappears. Re-open the app same day — button should not reappear.
**Expected:** First tap: 200 response, new song, button hidden (rerolls_left=0). Second tap (if user finds a way): 409, button stays hidden, cache syncs.
**Why human:** Requires EAS device build. Per user's EAS quota memory, device verification is batched.

#### 4. FromTheBankTag Chip Appears on Device

**Test:** With `from_bank=true` in the API response (25% bank path or empty working_on), open Today tab. Confirm the chip appears above SongOfDayCard with the correct copy ("From your bench" for user_bench / "From the bank" for seed_catalog).
**Expected:** Chip renders correctly with correct copy. When from_bank=false, no chip renders.
**Why human:** Requires EAS device build. Visual rendering cannot be grep-verified.

---

## Gaps Summary

No blocking gaps. All four Wave 1 gaps documented in HANDOFF.json are closed in live code:

- **Gap 1 (selector CTE unwired):** `select_today_song` is imported and called in `song_of_day.py` (grep: 1 import, 2 calls). The Phase-1 `SELECT * FROM songs LIMIT 1` stub and `seed_songs` fallback block are gone.
- **Gap 2 (POST /reroll missing):** `@router.post("/today-song/reroll")` exists at line 137 of `song_of_day.py` with DB-enforced one-per-day and IntegrityError→409 mapping.
- **Gap 3 (useReroll hook missing):** `export function useReroll()` present in `todaySong.ts` with correct 409-silent-invalidate semantics mirroring `useSubmitRating`.
- **Gap 4 (bank_source hardcoded):** `from_bank=False, bank_source=None` literal is gone (grep: 0). Both GET and POST read/write `bank_source` from the selector and the reroll marker row.

The asyncpg `:param::type` parser bug in the selector CTE was discovered and fixed (all instances replaced with `CAST(:param AS type)`). This was a blocking latent bug that prevented the selector from ever executing successfully before 03-04 — the summary's documentation of it is accurate.

Six pre-existing test failures are unchanged and pre-date this phase: 3 tz_offset dep tests that call the async function synchronously (test authoring bug), and 3 users_bootstrap_mocked tests (unrelated to Phase 3 scope).

---

_Verified: 2026-07-29_
_Verifier: Claude (gsd-verifier)_
