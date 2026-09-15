---
slug: 260915-01-drill-bank-schema
date: 2026-09-15
issue: FLE-8
mode: quick
---

# FLE-8 Task 3 — Drill bank: schema, dedup, history

Migration 0006 makes drills first-class. Today a drill's identity is
`(song_id, drill_index)` into one song's cached breakdown JSONB — a drill you
nailed on Little Wing cannot be handed to you tomorrow when the song is Kashmir.

## Scope of THIS task

In:
- `drills` table — stable UUID identity, drill survives its origin song
- `drill_attempts` table — per-drill history (tempo reached, reps, dates)
- `drill_dedupe_queue` table — the 70–84 uncertain band (curator queue)
- `app/ai/drill_dedupe.py` — rapidfuzz classification over `skill_dedupe.py`
- ORM models, reversible migration, tests

**Out — deliberately, and this is the headline:** the **backfill**.
FLE-8 states it is "Blocked on Task 1's disposition — if the eval says REVISE,
this would be a bank of bad drills." `04.1-05-EVAL-RESULTS.md` records
**DISPOSITION: REVISE** (25/30 gates; L5 ordering fails 3/5 songs, L1 isolation
fails on the barre drill). The prompt patch `04.1-06-PROMPT-PATCH.md` is written
but marked **NOT APPLIED**. Backfilling now banks exactly the bad drills the
issue warned about. Schema is quality-independent; backfill is not.

## Design decisions

**D1 — Identity.** `drills.id` UUID PK. `(origin_song_id, origin_drill_index)`
is retained as *provenance only*, nullable, `ON DELETE SET NULL` on the song FK.
The drill outliving its song is the entire point of the table.

No both-or-neither CHECK on the origin pair. `ON DELETE SET NULL` nulls
`origin_song_id` but cannot null `origin_drill_index` (not an FK column), so
such a CHECK would make song deletion fail — a constraint fighting the feature's
purpose. After a song delete, `origin_drill_index` is retained and is meaningless
without its song; documented, not enforced.

**D2 — Dedup scope is per `(user_id, skill_node_id)`.** Two drills with similar
names targeting different skills are not duplicates. Candidate lists are always
filtered to the same skill node before scoring.

**D3 — No LLM in the write path** (FLE-8 constraint). This conflicts with the
literal reading of "extend the existing machinery (rapidfuzz 85/70 + Sonnet
verifier + curator queue)". Resolution: the write path runs **rapidfuzz only**.
The Sonnet verifier is *not* called inline; the 70–84 band writes a
`drill_dedupe_queue` row and returns, which is the async escape hatch the
curator queue already exists to be. Thresholds and `normalize()` are imported
from `skill_dedupe.py`, not re-declared.

- `>= 85` → reuse existing canonical drill, no new row
- `70–84` → `drill_dedupe_queue` row, status `pending`, no drill row
- `< 70` → insert new canonical drill

**D4 — DB-level dedup backstop.** Partial-unique index
`uq_drills_canonical_identity` on `(user_id, skill_node_id, name_normalized)`
`WHERE canonical_drill_id IS NULL`. Exact-after-normalization collisions cannot
land even if the application layer is bypassed. rapidfuzz covers the fuzzy band
above that floor.

**D5 — `drill_attempts` is append-only, no one-per-day unique.** Contrast with
`user_sessions`' `uq_user_sessions_daily_rating`: that table records the daily
*verdict* (one per slot per day). This table records *what happened* — three
attempts at three tempos in one sitting is real data, and v2 feedback needs it.

## Verification

- `alembic upgrade head` then `downgrade` then `upgrade` against `gt-postgres:5433`
- `tests/test_alembic_0006.py` — columns, constraints, index behaviour, downgrade
- `tests/test_drill_dedupe.py` — threshold banding, per-skill scoping, normalization

## Constraint: publish schema before merging

FLE-8 requires the schema go to the Mobile Engineer before merge. See
`DRILL-BANK-SCHEMA.md` in this directory.
