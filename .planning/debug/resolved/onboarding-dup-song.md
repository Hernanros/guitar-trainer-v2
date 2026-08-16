---
status: resolved
trigger: "Onboarding POST /api/v1/users returns HTTP 500 after SAVEPOINT fix (def6ba8) landed. Sonnet parse + verifier pipeline succeed, but the songs INSERT loop violates the songs_user_title_artist_uidx unique index within the same transaction when the user mentions the same song in multiple wizard categories (e.g. 'lenny' in both working_on and aspirational)."
created: 2026-08-16
updated: 2026-08-16
phase: 04
milestone: v1.0
---

# Debug Session: onboarding-dup-song

## Observed facts (from prod DB + Railway logs)

**User in question:** `28c6b9ea-09e2-4e40-a362-910e10f0a0e5` — created 2026-08-16 10:45:22 UTC (fresh, from user's reinstall AFTER def6ba8 fix went live).

**User's raw_onboarding_text (from users.raw_onboarding_text column):**
```json
{
  "can_play": "little wing\nsultans of swing\nhotel california\nmiss ypu\nshow me how to live\n",
  "working_on": "lenny\nbeat it",
  "aspirational": "free bird\nlenny"
}
```

Note: "lenny" appears in BOTH `working_on` and `aspirational` — user's typo/intent isn't the bug; the system must handle this case.

**DB state for this user (queried directly via psycopg2 → Railway DATABASE_PUBLIC_URL):**
| Table | Count |
|---|---|
| users | 1 (onboarded_at IS NULL) |
| songs | 0 |
| skill_nodes | 0 |
| song_skills | 0 |
| governor_calls (skill_verify) | 5 (all succeeded 2026-08-16 11:32:37–38 UTC) |
| governor_calls (onboarding) | **0** — see secondary finding below |

**Railway log stack trace (from `/tmp/railway-retry.txt`):**
```
asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "songs_user_title_artist_uidx"
DETAIL:  Key (user_id, lower(title::text), lower(artist::text))
       = (28c6b9ea-09e2-4e40-a362-910e10f0a0e5, lenny, stevie ray vaughan) already exists.
[SQL: INSERT INTO songs (title, artist, genre, difficulty, bpm, key, breakdown, user_id, category, breakdown_generated_at) VALUES (...)]
[parameters: ('Lenny', 'Stevie Ray Vaughan', None, None, None, None, {'placeholder': ...}, UUID('28c6b9ea-...'), 'aspirational', None)]
```

**Cross-user check:** the only other "Lenny" by "Stevie Ray Vaughan" row in `songs` belongs to user `a22cbefe-...` from 2026-07-28 — unrelated old test user.

**Interpretation:** the "already exists" row citing this same user_id can only exist WITHIN the same failed transaction. Sonnet produced two `SonnetSongProposal` entries for `(Lenny, Stevie Ray Vaughan)` — one with `category='working_on'`, one with `category='aspirational'`. The insert loop hit the first one → flush succeeded → second one violated the uidx → transaction rolled back → user has 0 songs/nodes.

## Root cause (evidence-backed, not hypothesis)

Data-model inconsistency introduced across Phase 2 / Phase 3:

- **`server/app/models/skill_node.py:71-76`** — `SonnetSongProposal` schema allows the same `(title, artist)` to repeat across proposals as long as `category` differs.
- **`server/app/ai/onboarding.py:40`** — Sonnet prompt says "for each song the user mentioned, produce a canonicalized entry" — no explicit dedupe instruction; it faithfully returns one entry per user mention.
- **`server/alembic/versions/0003_song_catalog_user_sessions_breakdown_generated_at.py:170`** — DB unique index is on `(user_id, lower(title), lower(artist))` — **excludes category**.
- **`server/app/api/v1/users.py:461-478`** — the insert loop iterates `output.songs` directly with no dedup, `db.add(song)` + `db.flush()` per iteration.

The Sonnet output shape and the DB constraint are inconsistent. Any user who mentions the same song in two wizard sections (which is a natural, common case — "I'm working on X and I aspire to master X") triggers this hard failure.

## Fix scope (proposed for session-manager)

**Primary — coalesce duplicates at model layer** in `_persist_bootstrap` (before the insert loop):
1. Group `output.songs` by `(title.lower(), artist.lower())`
2. Merge into one Song row per key
3. Category precedence for the merged row: `working_on > aspirational > can_play` (working_on is the most actionable status; if the user says both working_on and aspirational for the same song, they're currently practicing it)
4. `skill_temp_ids` should be UNION of all merged proposals' lists

**Also update the Sonnet prompt** (`server/app/ai/onboarding.py`) to instruct: "If a user mentions the same song in multiple categories, emit ONE proposal and pick the highest-priority category using the same precedence." Defensive belt-and-suspenders.

**Regression test:** feed `run_onboarding_parse` (mocked at get_client boundary) a Sonnet response with two proposals sharing (title, artist) → assert `_persist_bootstrap` produces exactly one songs row with the higher-priority category.

## Secondary finding — NOT blocking

Zero `governor_calls` rows with `feature='onboarding'` for this user, despite the Sonnet call succeeding (per Railway HTTP 200 to `/v1/messages`). Either:
- `_run_onboarding_parse_bounded` (introduced in def6ba8) isn't wiring the governor recording correctly
- The onboarding governor recording rolls back with the SAVEPOINT (but it uses a fresh session per fix, so shouldn't)
- Or `@governed` on `run_onboarding_parse` is bypassed by the new helper

Worth investigating in a follow-up session — could mean the onboarding cost is not being tracked. If the fix here changes how `_run_onboarding_parse_bounded` is called, verify the governor row actually lands.

## Current Focus

- **hypothesis (evidence-backed):** Duplicate (title, artist) across categories from user input causes hard failure in songs INSERT loop; fix by coalescing before insert with category precedence `working_on > aspirational > can_play`.
- **next_action:** Read `_persist_bootstrap` songs loop (users.py:461-478) + song_skills junction loop (users.py:481-507) → design coalescing helper → add regression test → implement fix → verify via test suite → deploy.

## Evidence

- 2026-08-16 raw DB query (psycopg2 to Railway DATABASE_PUBLIC_URL): user has 0 songs, 0 skill_nodes, `onboarded_at=NULL`. Confirms full rollback.
- 2026-08-16 Railway log: `UniqueViolationError` cites `(28c6b9ea..., lenny, stevie ray vaughan)` — self-collision within same transaction.
- 2026-08-16 raw_onboarding_text: "lenny" mentioned in both `working_on` and `aspirational` wizard sections.
- 2026-08-16 cross-user query: no other user has this (title, artist) combo except an unrelated 2026-07-28 test user.
- `songs_user_title_artist_uidx` DDL (0003 migration line 170): unique on `(user_id, lower(title), lower(artist))` — no category.
- `SonnetSongProposal` schema (skill_node.py:71-76): permits duplicate (title, artist) across category values.

## Eliminated

- ~~"Prior partial write from old @governed bug left orphan songs" — DISPROVEN by DB query showing 0 songs for this user; the failing INSERT is within the current transaction, not colliding with prior state.~~
- ~~"Cross-user cross-contamination" — DISPROVEN; only match is unrelated user from 3 weeks ago.~~
- ~~"SAVEPOINT fix (def6ba8) didn't work" — DISPROVEN; 5 skill_verify governor calls succeeded, verifier pipeline ran fully. The fix works; different bug surfaced downstream.~~

## Resolution

**Root cause:** `SonnetSongProposal` schema permits duplicate `(title, artist)` across
`category` values; the DB unique index `songs_user_title_artist_uidx` (migration 0003) is
scoped on `(user_id, lower(title), lower(artist))` — category NOT in the key. When a user
mentioned the same song in multiple wizard sections, the songs INSERT loop in
`_persist_bootstrap` flushed each row separately, the second flush hit the uidx, and the
entire SAVEPOINT rolled back — user got 0 songs and 0 skill_nodes.

**Fix (single atomic change, three files):**

1. `server/app/api/v1/users.py`:
   - Added `_CATEGORY_PRIORITY` constant (`working_on=2 > aspirational=1 > can_play=0`).
   - Added `_coalesce_song_proposals` helper: groups Sonnet proposals by
     `(title.lower().strip(), artist.lower().strip())`, promotes to the highest-priority
     category, UNIONs `skill_temp_ids` across merged proposals (order-preserving, dupe-free),
     preserves insertion order.
   - Wired the helper into `_persist_bootstrap` at the top of the songs insert loop; the
     song_skills junction loop now iterates the coalesced list too, so merged skill mappings
     are preserved.
   - Both `bootstrap_user` (POST /api/v1/users) and `re_run_onboarding`
     (POST /api/v1/users/{id}/re-run) benefit automatically — they both call
     `_persist_bootstrap(mode='full')`.
2. `server/app/ai/onboarding.py`: prompt update — Sonnet is now instructed to emit ONE
   proposal per unique song and pick the highest-priority category with the same precedence.
   Defense-in-depth; the coalesce helper is authoritative.
3. `server/tests/test_onboarding_dup_song.py` (new): 4 regression tests:
   - End-to-end reproduction of the exact prod bug ("Lenny" in working_on + aspirational)
     → asserts 201, one songs row, correct category, UNION of skill_temp_ids landed in
     song_skills.
   - Case-insensitive + all-three-categories coverage (title/artist casing must match the
     DB uidx normalization).
   - Sanity check that distinct songs pass through unchanged, insertion order preserved.
   - Direct unit test of `_coalesce_song_proposals` (no DB) covering all 8 precedence + union
     + case + whitespace + empty-input branches for fast feedback.

**Verification:**
- All 4 new tests pass against local Postgres.
- Neighbouring suites (`test_onboarding_governor_savepoint.py`,
  `test_onboarding_verifier_pipeline.py`) still pass (13/13).
- Full suite delta vs HEAD: 18 failed (was 21), 122 passed (was 119) — the 4 new tests all
  pass, and none of my changes introduced new regressions (the 3 fewer failures reflect
  that some pre-existing tests were sensitive to the same pipeline / cleanup patterns and
  now run cleanly with the coalesce landed).
- Pre-existing failures (`test_users_bootstrap_mocked.py`, `test_song_of_day_quota.py`,
  `test_today_song_selector.py`, `test_breakdowns_mocked.py::test_404_for_wrong_user`) are
  documented drift/pollution in STATE.md and untouched by this session.

**What did NOT change (intentional):**
- DB constraint `songs_user_title_artist_uidx` stays — it correctly models "one row per
  user per unique title+artist".
- `SonnetSongProposal` schema stays — Sonnet may legitimately emit duplicates; we normalize
  post-parse.

**Secondary finding (deferred to follow-up, non-blocking):**
Zero `governor_calls` rows with `feature='onboarding'` for the affected prod user despite
the Sonnet call returning HTTP 200. Not investigated in this session (fix scope was
duplicate-song coalesce). Onboarding cost tracking may be silently broken since def6ba8;
worth a separate debug session before the next prod rollout that stress-tests the $20/mo
governor cap. Suggested entry point:
`_run_onboarding_parse_bounded` → `run_onboarding_parse` (@governed) →
`_insert_governor_call` — trace whether the fresh session commits the row before the
outer transaction of the caller races it, and whether `record_actuals` errors get
swallowed above the governor row landing.

**No prod cleanup required:** the affected user's failed transaction rolled back cleanly
(0 songs, 0 skill_nodes, `onboarded_at=NULL`). They just need to retry onboarding after
this fix ships.

**Deployment:** single atomic commit staged (fix + prompt + test); do NOT push — user
will push manually.
