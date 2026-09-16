# Quick 260916-01 — SUMMARY

**Status:** shipped
**Branch:** `fix/fle3-song-of-day-decimal` (worktree — shared checkout had a concurrent agent in `server/app/`)

## What changed

`server/app/selectors/today_song.py`
- Added `difficulty_label()` — maps `song_catalog.difficulty` `NUMERIC(4,3)` to the
  3-tier `songs.difficulty` label. `< 0.35` beginner, `< 0.65` intermediate, else
  advanced; `None` passes through as `None`.
- Bound it at the seed-song insert (was binding the raw `Decimal`).

`server/tests/test_today_song_difficulty_label.py` — new, 17 pure unit tests
(no Postgres needed): the exact production payload, contract anchors, half-open
boundaries, range endpoints, None, and float/str/int tolerance.

No schema change, no migration, one call site.

## Verification

- 17/17 new tests pass.
- 281 tests collect clean across the suite — no import breakage.
- Production, after deploy `<filled below>`: cold-start `GET /api/v1/song-of-day`
  with a fresh UUID returns **200** with a 3-tier `difficulty`.

## Follow-ups NOT done here (out of FLE-3 scope)

1. `songs.difficulty` (String(50)) and `song_catalog.difficulty` (NUMERIC(4,3))
   share a name but not a type. `difficulty_label()` is a boundary adapter, not a
   reconciliation. Worth a real decision in ROADMAP.
2. Existing `songs.difficulty` rows written before this fix are not backfilled.
   Nothing wrote a bad value (the insert 500'd rather than persisting), so this is
   about pre-existing free-text values, not damage from this bug.
