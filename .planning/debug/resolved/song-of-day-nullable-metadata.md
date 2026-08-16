---
status: resolved
trigger: "GET /api/v1/song-of-day returns HTTP 500 for user 28c6b9ea-... after successful onboarding on eacfcb9. Root cause: Song rows inserted during onboarding have NULL genre/difficulty/bpm/key + placeholder breakdown, but SongResponse Pydantic contract requires them all populated. Every user-onboarded song 500s until Phase 3 breakdown fills in the fields."
created: 2026-08-16
updated: 2026-08-16
phase: 04
milestone: v1.0
---

# Debug Session: song-of-day-nullable-metadata

## Observed facts (from Railway logs + code read)

**Stack trace signature** (from `/tmp/railway-sod.txt`):
```
File "/app/app/api/v1/song_of_day.py", line 157, in get_song_of_day
    song=SongResponse.model_validate(row),
pydantic_core._pydantic_core.ValidationError: 7 validation errors for SongResponse
  genre:                      expected str, got None
  difficulty:                 expected str, got None
  bpm:                        expected int, got None
  key:                        expected str, got None
  breakdown.tab:              field required (input: {'placeholder': 'Phase 3 will populate breakdown'})
  breakdown.chords:           field required
  breakdown.technique_notes:  field required
```

**Insert-time state** (from `server/app/api/v1/users.py:461-478` in `_persist_bootstrap`):
```python
song = Song(
    title=song_prop.title,
    artist=song_prop.artist,
    genre=None,       # ← always NULL on user-onboarded songs
    difficulty=None,  # ← always NULL
    bpm=None,         # ← always NULL
    key=None,         # ← always NULL
    breakdown={"placeholder": "Phase 3 will populate breakdown"},  # ← placeholder, not a valid Breakdown
    user_id=user_id,
    category=song_prop.category,
)
```

**API contract** (from `server/app/models/song.py:72-98`):
```python
class SongResponse(BaseModel):
    id: int
    title: str
    artist: str
    genre: str          # ← non-nullable
    difficulty: str     # ← non-nullable
    bpm: int            # ← non-nullable
    key: str            # ← non-nullable
    breakdown: Breakdown  # ← non-nullable, requires .tab, .chords, .technique_notes
    ...
```

**Mobile usage** (from `mobile/src/components/SongOfDayCard.tsx:45-48`):
```tsx
<Text>{song.genre} · {song.bpm} BPM · Key of {song.key}</Text>
<View style={styles.difficultyBadge}><Text>{song.difficulty}</Text></View>
```
Mobile has no fallback for missing metadata — direct interpolation.

**Design intent signal** (from `song.py:132-152`): `TodaySongResponse.breakdown_available: bool` already anticipates that breakdown may not exist. That signal was designed correctly; the SongResponse strictness contradicts it.

**Sonnet contract** (from `server/app/models/skill_node.py:71-76`): `SonnetSongProposal` currently emits only `title + artist + category + skill_temp_ids` — no metadata. Sonnet has this knowledge implicitly (it knows "little wing" is Hendrix in Em ~90bpm intermediate) but isn't asked for it.

## Root cause

Contract drift across Phase 1 → 2 → 3:
- **Phase 1** (walking skeleton): `SongResponse` designed against the hardcoded seed song, which was fully populated. Strictness was fine.
- **Phase 2** (onboarding + persistence): user-song insert path emits NULLs + placeholder breakdown because Sonnet's `SonnetSongProposal` schema doesn't carry metadata. Placeholder was explicitly labeled "Phase 3 will populate breakdown" — a known deferral.
- **Phase 3** (song-of-day selector): wired user songs into the selector. `breakdown_available` flag was added to `TodaySongResponse` to signal absence — but the underlying `SongResponse` was never loosened. `select_today_song` happily returns user-onboarded songs, then `.model_validate()` 500s before `breakdown_available` is even computed.

No test caught this because the Phase 3 selector tests seed songs with fully-populated fields (test fixtures don't reproduce the Phase 2 insert path's NULLs).

## Fix scope (user chose: Both — Sonnet populates + Optional for safety)

### Primary — populate metadata at onboarding via Sonnet

1. **Extend `SonnetSongProposal`** (`server/app/models/skill_node.py:71-76`) with:
   - `genre: str` — required
   - `difficulty: Literal["beginner", "intermediate", "advanced"]` — required (matches DB enum precedent though DB column is currently VARCHAR)
   - `bpm: int` — required
   - `key: str` — required (e.g. "Em", "C", "G#m")
2. **Update Sonnet system prompt** in `server/app/ai/onboarding.py` (~line 40, PART 1 section): add explicit instruction to emit genre + difficulty + bpm + key for each song. Give a short example. Explicitly say "if you're unsure, make a reasonable inference — do not omit or leave blank".
3. **Update `_persist_bootstrap`** (`users.py:461-478`) to persist these from `song_prop.genre/difficulty/bpm/key` instead of hardcoded None.
4. **Regenerate schema.d.ts** on mobile side for TypeScript type accuracy (though mobile won't see changes until next EAS build — that's fine, this data is only READ by mobile, not written).

### Backstop — make SongResponse fields Optional

1. **`server/app/models/song.py:72-98`** — change `genre: str → Optional[str]`, `difficulty: str → Optional[str]`, `bpm: int → Optional[int]`, `key: str → Optional[str]`, `breakdown: Breakdown → Optional[Breakdown]`.
2. Keep `title/artist/id` required (invariants — always present).
3. This means ANY song-insert path that skips Sonnet (future admin tools, migrations, seed catalog additions) won't 500 the API.
4. Regenerate OpenAPI schema. Mobile clients will get `T | undefined` on next codegen — but current EAS build already works because Sonnet populates them.

### Recovery for the affected user

User `28c6b9ea-...` has 2 songs in DB (post-onboarding) with NULL metadata. Options:
- **A.** Trigger re-run onboarding from device Settings — wipes + re-seeds via new Sonnet path with metadata. Cleanest.
- **B.** One-off UPDATE: run Sonnet-style fill for their existing songs (not practical without new tooling).
- **C.** Delete the 2 songs + set `onboarded_at = NULL` → app routes them back to onboarding. Server-side one-liner.
- **Recommend A** — the "Re-run onboarding" action exists in the mobile app per Phase 2 design (D-04). User just triggers it from Settings after deploy.

### Deferred to next EAS batch (mobile side)

Mobile `SongOfDayCard` graceful degradation for missing metadata (`{song.genre ?? '—'} · {song.bpm ?? '?'} BPM ...`). NOT needed for THIS user's happy path because Sonnet will populate the fields, but should be added defensively in the next mobile build. Capture as a follow-up.

## Also worth doing — regression test surface

- Test that Sonnet's structured-output tool schema now REQUIRES the new fields (fail loudly if a mock omits them).
- Test that `_persist_bootstrap` persists all metadata fields correctly.
- Test that `SongResponse` accepts a fully-nullable song (backstop path).
- Test that GET /api/v1/song-of-day returns a valid response for a user whose songs have complete metadata (regression for the specific prod bug).

Existing tests to check for breakage:
- Anything that constructs a `SonnetSongProposal` directly — mocks will fail without the new fields → need updating.
- Any `SongResponse` test that currently asserts strict presence.

## Current Focus

- **hypothesis (evidence-backed):** Contract drift between Song DB (nullable) and SongResponse API (strict); user-onboarded songs 500 before Phase 3 fills them. Fix by having Sonnet emit metadata at onboarding + loosening the API contract as backstop.
- **next_action:** Extend SonnetSongProposal + Sonnet prompt → update _persist_bootstrap to persist metadata → make SongResponse fields Optional → regen OpenAPI + mobile types → write regression tests → apply → verify test suite → deploy.

## Evidence

- Railway `/tmp/railway-sod.txt` — 7-error ValidationError from Pydantic on `SongResponse.model_validate(row)` at `song_of_day.py:157`.
- Direct psycopg2 query on prod DB confirmed songs table row for user `28c6b9ea-...` has `genre=NULL, difficulty=NULL, bpm=NULL, key=NULL, breakdown={"placeholder": "..."}`.
- `_persist_bootstrap` insert code (`users.py:461-478`) proves NULLs are hardcoded at insert time.
- `SonnetSongProposal` schema (`skill_node.py:71-76`) confirms metadata fields absent.
- `TodaySongResponse.breakdown_available` (`song.py:145`) proves design intent was for breakdown to be optional-in-practice.

## Eliminated

- ~~"Decimal-vs-str seed bug from earlier verification"~~ — Different bug, unrelated. This user has real songs; the seed-insert path isn't triggered.
- ~~"Cross-user data corruption"~~ — Songs verified as user-owned via `WHERE Song.user_id == user_id` filter in the SELECT.
- ~~"Sonnet output missing songs entirely"~~ — DB shows 2 songs after onboarding, both with valid title/artist. Only metadata + breakdown are NULL/placeholder.

## Resolution

**Fix applied:** Both layers per user-approved plan.

**Server code changes:**
- `server/app/models/skill_node.py` — `SonnetSongProposal` extended with required `genre: str`, `difficulty: Literal["beginner","intermediate","advanced"]`, `bpm: int`, `key: str`. Any missing metadata now raises Pydantic ValidationError → AIParseError → D-07 fail-open path.
- `server/app/ai/onboarding.py` — Sonnet system prompt updated (PART 1) to require all four metadata fields per song with explicit "make a reasonable inference — do not omit" instruction and two concrete examples ("Little Wing" and "Sweet Home Chicago").
- `server/app/api/v1/users.py` — `_persist_bootstrap` now persists `genre=song_prop.genre, difficulty=song_prop.difficulty, bpm=song_prop.bpm, key=song_prop.key` (was hardcoded `None`). Also updated `_coalesce_song_proposals` to propagate the 4 metadata fields into merged proposals (first-seen wins) so eacfcb9's coalesce path doesn't drop them.
- `server/app/models/song.py` — `SongResponse.genre/difficulty/bpm/key/breakdown` all loosened to `Optional[...]`. Added a `coerce_placeholder_breakdown` @field_validator that converts `{"placeholder": ...}` JSONB rows to `None` so Optional[Breakdown] validation passes; real breakdowns (with `tab` key) pass through unchanged. Callers should consult `TodaySongResponse.breakdown_available` (server-authoritative flag) instead of probing `SongResponse.breakdown` for None.

**Test coverage (new file `server/tests/test_song_of_day_nullable.py`, 5 tests, all pass):**
1. `test_song_of_day_serves_song_with_full_metadata` — happy-path regression (full metadata → 200 with echoed fields)
2. `test_song_of_day_serves_song_with_null_metadata_and_placeholder_breakdown` — exact prod bug repro; before this fix was a 500 with 7-error ValidationError
3. `test_song_response_placeholder_breakdown_coerces_to_none` — unit test for the placeholder coercion validator
4. `test_sonnet_song_proposal_requires_metadata_fields` — contract regression; loud-fails if anyone reintroduces the schema drift
5. `test_coalesce_song_proposals_preserves_metadata_across_duplicates` — guard for the eacfcb9 (dup-song) coalesce path

**Existing test constructor updates:**
- `server/tests/test_users_bootstrap_mocked.py` — 3 SonnetSongProposal constructions extended with plausible metadata (Sweet Home Chicago / Little Wing / Eruption).
- `server/tests/test_onboarding_dup_song.py` — 3 dict-form Sonnet outputs (Lenny x2, Beat It x3, distinct 3-song set) and 7 direct SonnetSongProposal constructor blocks extended with metadata. All 4 eacfcb9 regression tests still pass.

**Test suite verification:**
- Baseline before fix: 18 failed, 122 passed, 1 skipped, 10 errors (141 tests).
- After fix: 18 failed, 127 passed (+5), 1 skipped, 10 errors (146 tests — +5 new).
- Exact same set of 28 baseline failures/errors — zero new regressions. All pre-existing failures are documented Phase-2 schema drift + pool-teardown noise unrelated to this fix.

**Root cause (recap):** Contract drift Phase 1 → 2 → 3. `SongResponse` was designed against the fully-populated seed row (Phase 1). Phase 2 user-onboarded rows landed with NULL metadata + placeholder breakdown because Sonnet's tool_use schema didn't carry those fields. Phase 3 selector correctly picked user rows → `SongResponse.model_validate()` blew up on the NULLs before `breakdown_available` was even computed. Fixed by carrying metadata through the Sonnet contract (primary correctness) + accepting NULL/placeholder at the API boundary (backstop for any non-Sonnet insert path).

**Recovery for the affected user (`28c6b9ea-09e2-4e40-a362-910e10f0a0e5`):**
Not applied server-side per user request. Recommended: after deploy, user opens Settings → "Re-run onboarding" → wipes their 2 NULL-metadata songs and re-seeds through the new Sonnet path with full metadata. Alternative one-liner if UI action is inaccessible: `DELETE FROM songs WHERE user_id = '28c6b9ea-...'; UPDATE users SET onboarded_at = NULL WHERE id = '28c6b9ea-...';` — the app then routes them back through onboarding on next launch.

**Follow-ups (not in this commit):**
- Regenerate `mobile/src/api/generated/schema.d.ts` via `cd mobile && npm run codegen:local` (requires local server running) so TypeScript sees Optional fields. Not blocking — current EAS build works because Sonnet populates metadata on the happy path.
- Mobile `SongOfDayCard` graceful degradation for missing metadata (`{song.genre ?? '—'}` etc.) — deferred to next EAS batch per `.claude/projects/.../memory/project_eas_batch_phase3_and_4.md`.
