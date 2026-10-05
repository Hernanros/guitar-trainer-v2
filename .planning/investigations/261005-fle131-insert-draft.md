# FLE-131 — SUPERSEDED, see migration 0019

This file was a fill-in-the-blanks draft written 2026-10-05 while FLE-131 waited
on Junie's FLE-130 roster decision. Junie answered the same hour and accepted
all six rows with every column filled, so the draft was superseded before it was
an hour old. Keeping its "blocked, awaiting inputs" framing around would just be
a stale premise for the next reader.

**The real thing:** `server/alembic/versions/0019_insert_fle130_catalog_rows.py`
— six rows, `down_revision = "0018"`, verified against dev.

The two findings from the draft that are still worth knowing, both now recorded
where they belong rather than here:

- Inserting catalog rows is **two edits, not one**. The five new titles also had
  to be added to migration 0015's `_RIGHTS_STATUS` map, because
  `test_alembic_0015.py::test_no_row_left_on_the_default` asserts the map covers
  the live catalog and its sibling asserts the reverse. Written up in 0019's
  docstring.
- The map's "all titles are distinct, so a title key is safe" justification
  **stopped being true** with two *Big Love* rows. It still works only because
  both rows share one `rights_status`. Recorded as an `AMENDED` note directly
  above that map in 0015, including what has to change if a future curator needs
  two same-title rows to carry different values.

One thing the draft got wrong by assuming: it predicted `genre` and
`primary_skill_root` would have to be chased down separately, since FLE-130 did
not ask for them. Junie had already supplied both from the 0007 tagging contract
while deciding difficulty. The question was worth asking — the columns are
`NOT NULL` with no usable default — but it was already answered.

## Verification transcript — 2026-10-05

Kept here because reproducing it needs a throwaway database and a 19-migration
chain, which is too expensive to live in the suite. `test_alembic_0019.py`
guards the source-level properties; this is the behaviour.

**Schema facts, read off the live database rather than the ORM.** `genre` is
`text NOT NULL` with no default and `primary_skill_root` is a `NOT NULL` enum
with no default — so both really are required, confirming why the insert could
not be written from FLE-130's four fields. `difficulty` is `NUMERIC(4,3)
NOT NULL DEFAULT 0.0` under `CHECK (difficulty >= 0 AND difficulty <= 1)`. The
omitted columns are all safe: `created_at` defaults to `now()`, `breakdown` and
`verified_tab` are nullable. The conflict target exists and is exactly
`song_catalog_title_artist_uidx ON (lower(title), lower(artist))`.

**The prod path.** A scratch DB was rebuilt to prod's exact drift — `upgrade
0014`, then `stamp 0015` without running it — reproducing 64 rows, stamp `0015`,
`rights_status` absent. Then the single `alembic upgrade head` the Railway
pre-deploy step runs:

```
0015 -> 0016  song_catalog: add verified_tab
0016 -> 0017  songs.tuning: backfill from song_catalog
0017 -> 0018  song_catalog: repair missing rights_status column on prod
0018 -> 0019  song_catalog: insert the six FLE-130 rows Junie accepted

version=0019  rows=70  distinct_titles=69  rows_on_difficulty_default=0
rights_status: copyrighted 66 / public_domain 2 / traditional 1 / unknown 1
```

The single `unknown` is Black Mountain Side, which 0015 tags that way
deliberately. All six rows landed with Junie's exact values, apostrophes in
*Cause We've Ended as Lovers*, *Sweet Child O' Mine* and *Guns N' Roses*
escaped correctly.

**Round-trip and re-entry.** `downgrade 0018` removed exactly the six rows
(70 -> 64) and `upgrade head` restored them (64 -> 70). Separately, stamping back
to 0018 *without* deleting the rows and re-running `upgrade head` — the retried
deploy path — was a clean no-op at 70 rows with both *Big Love* rows' difficulty
and skill root unchanged. That is the `ON CONFLICT (lower(title),
lower(artist))` target resolving to the expression index, which a bare
`ON CONFLICT DO NOTHING` would not do.

**Tests.** `test_alembic_0019.py` is 10 tests, 9 source-level plus one
end-state check. Each load-bearing assertion was mutation-checked: re-parenting
`down_revision` to 0017, flipping one *Big Love* row's `rights_status`, zeroing
a difficulty, nudging Layla 0.700 -> 0.750, dropping `rights_status` or
`difficulty` from the column list, weakening `ON CONFLICT` to bare, typing a
bad skill root or tuning, and duplicating a `(title, artist)` pair all fail the
suite. The first version of the rejected-workaround test did **not** fail that
mutation — `"rights_status" in <source>` matches the docstring, which explains
the column at length — so it was rescoped to parse the INSERT's column list.
Full server suite: 1316 passed, 2 failed, both pre-existing and in other lanes
(`test_alembic_0012::test_stored_tier_matches_compute_tier`, a drills tier-rubric
drift, and `test_governor::test_day_boundary_follows_the_users_timezone`);
neither file references `song_catalog`.

**Not applied to prod.** 0019 is committed only. Prod is undeployable until
`main` is pushed (FLE-133), and the push plus `upgrade head` is FLE-124's.
