# Drill bank schema — published for the Mobile Engineer

**Issue:** FLE-8 Task 3 · **Migration:** `0006_drills_bank` · **Date:** 2026-09-15
**Status:** schema landed on `main`, **no API endpoints yet** — nothing to integrate
against today. This document exists to satisfy FLE-8's "publish the schema before
merging" constraint and to let mobile plan against stable field names.

## What changed conceptually

A drill used to be a field inside one song's cached breakdown, identified by
`(song_id, drill_index)`. It is now a row with its own UUID. A drill you nailed on
Little Wing can be handed to you tomorrow when the song is Kashmir.

**Nothing mobile currently calls has changed.** `BreakdownEnvelope`,
`Breakdown.drills`, `SessionCreate.drill_index` and `drill_rated_today_indices` are
all untouched. The existing `(song_id, drill_index)` path still works exactly as it
did — the bank sits alongside it, and wiring the two together is a later task.

## `drills` — the bank

| column | type | null | notes |
|---|---|---|---|
| `id` | uuid | no | **the new stable identity** |
| `user_id` | uuid | no | FK users, CASCADE |
| `name` | varchar(255) | no | imperative drill name |
| `name_normalized` | varchar(255) | no | dedup key; internal, not for display |
| `skill_node_id` | uuid | no | FK skill_nodes — the one skill drilled |
| `canonical_drill_id` | uuid | **yes** | NULL = canonical. Set = collapsed duplicate |
| `dedupe_score` | int | yes | rapidfuzz score vs canonical at write time |
| `song_specific` | bool | no | same meaning as today |
| `what` | text | no | |
| `tab_snippet` | jsonb | no | same `Tab` shape mobile already renders |
| `start_bpm` / `target_bpm` | int | no | CHECK `target_bpm > start_bpm` |
| `repetitions` | int | no | |
| `success_criterion` | text | no | |
| `common_trap` | text | yes | |
| `origin_song_id` | int | **yes** | FK songs, **ON DELETE SET NULL** |
| `origin_drill_index` | int | **yes** | pairs with `origin_song_id` |
| `created_at` / `updated_at` | timestamptz | no | |

**Two things worth mobile's attention:**

1. **`origin_song_id` is nullable and will become NULL.** It is provenance, not
   identity. If a song is deleted the drill survives with `origin_song_id = NULL`.
   Do not build UI that assumes a banked drill can always name its source song, and
   do not key anything off `(origin_song_id, origin_drill_index)`. Key off `id`.

2. **Filter `canonical_drill_id IS NULL` when listing.** Rows with it set are
   collapsed duplicates retained for history; showing them would surface the exact
   near-duplicates the bank exists to remove. Any future list endpoint will do this
   server-side, but the field is visible in the schema so the reason is stated here.

## `drill_attempts` — per-drill history

| column | type | null | notes |
|---|---|---|---|
| `id` | uuid | no | |
| `drill_id` | uuid | no | FK drills, **CASCADE** |
| `user_id` | uuid | no | FK users, CASCADE |
| `user_session_id` | uuid | yes | FK user_sessions, SET NULL |
| `local_calendar_day` | date | no | client-local day, same convention as `user_sessions` |
| `attempted_at` | timestamptz | no | |
| `tempo_reached_bpm` | int | yes | CHECK 20–400. **Tempo actually reached** |
| `reps_completed` | int | yes | CHECK >= 0 |
| `rating` | rating_level | yes | reuses the existing 3-choice enum |
| `notes` | text | yes | |

**The one behavioural difference mobile should plan for:** this table permits
**multiple attempts per drill per day**. `user_sessions` enforces one rating per
slot per day (`uq_user_sessions_daily_rating`) and a second POST 409s. Attempts do
not work that way — three tempo steps in one sitting is three rows, by design. When
an attempt-logging endpoint exists it will **not** 409 on a repeat.

`tempo_reached_bpm` and `reps_completed` are both nullable: an attempt can be
recorded with neither. Treat them as optional in any client model.

## `drill_dedupe_queue` — internal

Curator queue for the 70–84 fuzzy band. **No mobile surface.** Listed only so the
third table in the migration is not a surprise.

## Dedup rules (relevant to why duplicate-looking drills won't appear)

Scoped to `(user_id, skill_node_id)` — similar names on *different* skills are not
duplicates and both stay canonical. Within a scope: `>= 85` reuse · `70–84` queue ·
`< 70` new. No LLM runs in the write path.

## Open questions for mobile — answer whenever, not blocking

1. Does the drill player have a **tempo the user actually reached** to report, or
   only the drill's configured `target_bpm`? `tempo_reached_bpm` is the column
   that makes this history worth having, and if the client can't source it the
   field will be NULL forever.
2. Same for `reps_completed` — is that counted, or would it be self-reported?

Reply on FLE-8 and I'll fold the answers into the attempt-logging endpoint design.
