# FLE-32 (Task 3b) — outcome: the offline quality filter

> Written to the repo rather than to the issue thread: FLE-32 is held by a queued
> heartbeat run, so this run could not comment on it. The same account is on FLE-48
> as a comment, and the production-write incident is disclosed there in full.
>
> Commits: `27b9a99` (grade_cached_drills), `262abac` (--quality-filter),
> `9b12f47` (the transaction-contract fix).

**Option 2 taken: the quality filter exists, is measured against the real rows, and ships as a flag. The bank write itself is the only thing left, and it belongs to FLE-48.**

This issue offered two unblock paths: pay ~$0.50 to re-run the eval and regenerate, or agree a filter that is mechanically checkable offline. I took the second. No LLM, no spend, and it can be re-run after every breakdown a user generates.

### What shipped

- `27b9a99` — `scripts/grade_cached_drills.py`. Reuses `scripts.grade_eval.grade` unmodified against `songs.breakdown->'drills'`, then attributes each song-level gate failure to the drill that caused it. A song-level verdict is useless as a filter: you cannot exclude "song 71's L5".
- `262abac` — `backfill_drills.py --quality-filter` (plus `--l1-cover-min`). One decision function, `grade_cached_drills.exclusions`, backs both the report and the filter, so the two cannot disagree about which drill is held.
- `9b12f47` — the transaction-contract fix described below.

### Two gates turned out to be wrong as exclusions, and the production data is what showed it

**A1 is 0-for-4.** It fires when `what` contains a polyphony word and the snippet is monophonic, which cannot tell "promises a chord" from "mentions a chord". Song 76 D1 says *"Hold an open E chord shape and pick only the two bass strings… No treble strings at all"* — it explicitly disclaims polyphony, and A1 flags it anyway. As a filter it would hold back correct fingerstyle drills. Reported, never excluded by default.

**L1's fixed 3-beat run conflates two defects,** because an absolute threshold means different things at different snippet lengths: on a 4-beat snippet a 3-run is 75% of the drill; on an 8-beat snippet it is 38%. So L1 exclusions are qualified by a coverage ratio. Song 71 D1 is one bar of the shuffle verbatim (100%); song 73 D1 shares three notes only because it and the song walk the same Bb minor box (38%) — and L7 *requires* that shared position.

The naive L1+A1 filter banks 6 of 16 and empties one song entirely, which would leave the session generator as degenerate as the empty bank does. L1-with-ratio banks 12 and holds exactly the four that are the song played back.

### Collapse rate — the other deliverable

**0%.** 16 seen, 16 inserted, 0 reused, 0 queued.

FLE-8 sized this at "~180 rows that are mostly six ideas wearing different names", but that estimate assumed 60 songs with breakdowns. Production has **5**, and their drills target distinct skills with distinct names, so there is nothing for the dedup pass to collapse yet. The machinery is exercised and correct; it has no duplicates to find at this volume. The estimate is not wrong, it is just not yet due — worth re-measuring once the corpus grows.

### Holding the four costs no coverage

| | all 16 | filtered 12 |
|---|---|---|
| classify | 16 / 16 | 12 / 12 |
| distinct (family, tier) cells | 4 | **4** |
| pilot-band cells at stock >= 2 | 2 of 45 | **2 of 45** |

The only cell thinned is R3/D2, 7 -> 3, still over `MIN_CELL_STOCK`. So banking the extra four buys zero cells and hands a user four drills that are the song again.

### One thing went wrong, disclosed in full on FLE-48

Measuring that table required chaining `backfill_drills` and `backfill_drill_taxonomy` in one transaction. `backfill_drill_taxonomy.backfill()` committed internally despite documenting the opposite contract, so my rolled-back preview **wrote 16 drills to production**. I deleted them within about a minute; nothing had referenced them, and prod is back to 0 rows. Root cause fixed in `9b12f47` with three tests pinning the contract. Full account on FLE-48.

### Disposition — CLOSED 2026-09-24

The write landed under FLE-48, so the one thing this issue was holding for is done. Verified live against production read-only rather than inferred from FLE-48's report:

| "Done when" clause | Evidence from prod |
|---|---|
| Existing drills are in the bank | `drills` = **12** rows |
| Duplicates collapsed on write | 12 canonical, 0 merged, 12 distinct `name_normalized`, `drill_dedupe_queue` = 0 |
| Collapse rate reported | **0%** — 16 seen, 16 inserted, 0 reused (see above for why it is not yet due) |
| Drills are ones we'd hand a user | the 4 song-playback drills are absent: song 71 kept D1/D3 of 4, song 76 kept D1 of 3 |

Provenance is complete — every row carries a non-null `origin_song_id` and `origin_drill_index`, and every row resolved a `skill_node_id`. Taxonomy classified 12/12 with no nulls: `L4/D2 x7, R3/D2 x3, L3/D1 x1, C1/D2 x1`.

**Nothing was left unread.** 42 songs hold a `breakdown` object but only 5 carry a `drills` key; the other 37 are 34 `{"placeholder": "Phase 3 will populate breakdown"}` seeds and 3 real breakdowns that predate the drills field. So the 16-drill read was the whole available corpus, not a partial sweep — worth recording because "42 breakdowns, 12 drills" reads like an under-read and is not one.

Two things this issue does **not** close, both already owned elsewhere: the bank is per-user and splits 9/3 (FLE-61), and §10.3 pilot-band coverage stays at 2/45 for structural reasons (FLE-82). The 0% collapse rate is worth re-measuring once the corpus grows past 5 breakdowns — FLE-8's ~180-row estimate assumed 60 songs and is not disproven, just not yet due.
