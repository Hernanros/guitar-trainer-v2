# Quick 260916-01 — Fix cold-start 500 on GET /api/v1/song-of-day

**Raised by:** FLE-3 (Task 2 — push, deploy, build, verify on device)
**Type:** bug / release blocker
**Server:** https://guitar-trainer-v2-production.up.railway.app

## Symptom

`GET /api/v1/song-of-day` returns **HTTP 500** for any user who does not yet have a
song assigned for today (the cold-start / seed-song insert path).

Reproduced against production on 2026-09-16, deploy `30ab662`:

```
curl -H "X-User-ID: 3d5554bd-84fb-4f2e-8e50-24aadaa77e99" \
  https://guitar-trainer-v2-production.up.railway.app/api/v1/song-of-day
-> HTTP 500, 0.49s
```

Railway log:

```
asyncpg.exceptions.DataError: invalid input for query argument $4:
  Decimal('0.550') (expected str, got Decimal)
[parameters: ('Thunderstruck', 'AC/DC', 'Rock', Decimal('0.550'),
  '{"tab":{...},"chords":[],"technique_notes":[]}',
  '3d5554bd-84fb-4f2e-8e50-24aadaa77e99')]
  File "/app/app/api/v1/song_of_day.py", line 92, in get_song_of_day
```

This is the bug recorded in MEMORY.md `project_song_of_day_decimal_bug.md`
(first surfaced 2026-08-16 during Phase 4 Railway verification, never fixed,
never entered ROADMAP.md). The song title in the trace has changed (`Blackbird`
-> `Thunderstruck`) only because migration 0007 expanded the catalog; the
failure is the same one.

## Root cause

Two columns named `difficulty` carry different types, and the seed-song upsert
copies one straight into the other.

| Column | Type | Declared |
|---|---|---|
| `song_catalog.difficulty` | `NUMERIC(4,3)` in [0,1] | migration 0003 line 62 |
| `songs.difficulty` | `String(50)` free text | migration 0001 line 28 |

`server/app/selectors/today_song.py:200` binds the catalog value (asyncpg
decodes `NUMERIC` as `decimal.Decimal`) directly into the `songs` insert, whose
`$4` is a string column. asyncpg refuses the implicit conversion.

Why it was never caught earlier: the path only runs for a user with **no** song
row for today. Existing users short-circuit at the `existing_row` check on
`today_song.py:180`, and cached/seeded local fixtures never exercised it.

## Why `str(...)` alone is the WRONG fix

`songs.difficulty` is not a stringified number — it is a **3-tier label**:

- `server/app/models/song.py:245` — `difficulty: Optional[str]  # "beginner" | "intermediate" | "advanced"`
- `server/app/models/skill_node.py:98` — `Literal["beginner", "intermediate", "advanced"]`
- `server/app/db/seed.py:17` — `"difficulty": "intermediate"`
- `mobile/src/components/SongOfDayCard.tsx:58` renders it **verbatim** into the
  difficulty badge.

A bare `str(Decimal('0.550'))` would stop the 500 but write `"0.550"` into the
badge on the Song of the Day card — a visible UI defect, and it would corrupt
FLE-3 device-verification Item 4 (which reads that card).

## Fix

Map the catalog's numeric difficulty onto the 3-tier label at the boundary,
using the anchors the catalog tagging contract already defines
(`0007_song_catalog_tuning_and_expansion.py`, "Tagging contract"):

> Anchors: 0.20 beginner, 0.50 intermediate, 0.80 advanced.

Nearest-anchor midpoints give the thresholds:

| catalog difficulty | label |
|---|---|
| `< 0.35` | `beginner` |
| `< 0.65` | `intermediate` |
| `>= 0.65` | `advanced` |

Add `difficulty_label()` to `server/app/selectors/today_song.py` and bind its
result at the insert. Single call site; no schema change, no migration.

Deliberately NOT doing here (out of FLE-3 scope, flag for ROADMAP):
- reconciling the two `difficulty` columns onto one type
- backfilling existing `songs.difficulty` rows

## Verification

1. Unit: `difficulty_label()` at/around each threshold + both anchors.
2. Regression: the exact production payload — `Decimal('0.550')` -> `"intermediate"`.
3. Prod: re-run the failing curl with a fresh UUID, expect 200 and a
   `difficulty` of `beginner|intermediate|advanced`.
