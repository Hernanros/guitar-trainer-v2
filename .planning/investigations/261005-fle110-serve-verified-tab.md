# FLE-110 — serving the FLE-103 verified tab instead of generating one

Date: 2026-10-05 · Agent: Ripley · Parent: FLE-103 → FLE-89

FLE-103 hand-verified ten songs and committed them. That changed nothing a pilot
user sees, because **there was no stored tab in the catalog to mark verified**:
`song_catalog.breakdown` is NULL on all 64 prod rows, and tab exists only in
`songs` rows minted per user per day by the generator. This issue is the half
that makes the verification load-bearing.

## What landed

| Scope item | Where |
|---|---|
| 1. Somewhere to put a verified tab | `server/alembic/versions/0016_song_catalog_verified_tab.py` — `song_catalog.verified_tab` JSONB |
| 2. A re-runnable loader | `server/scripts/fle110_load_verified_tabs.py` |
| 3. Restrict + short-circuit under a pilot flag | `server/app/verified_tabs.py`, `app/selectors/today_song.py`, `app/api/v1/breakdowns.py`, `app/api/v1/song_of_day.py` |
| 4. Cap `player_level` at 0.70 | `verified_tabs.PILOT_PLAYER_LEVEL_CAP`, applied in the selector CTE |
| Check | `server/tests/test_fle110_verified_tab_serving.py` (12 tests) |

## Five decisions worth stating

**A column, not a join table.** One verified tab per catalog song, not versioned
in the DB (the committed file is the version history, under git), not per-user.
A join table would buy multiple verified tabs per song, which nothing asks for
and which the serve path would then have to choose between.

**The column stores a Breakdown, not a bare Tab.** The issue offered the bare
`Tab`. Storing `{tab, chords, technique_notes}` means `is_renderable_breakdown`
and `_load_cached_breakdown` read it unchanged and the endpoint's verified branch
is a pure read, instead of two call sites fabricating `chords`/`technique_notes`
per request. `chords` and `technique_notes` are derived **mechanically** from the
verified entry — each diagram is read back off the verified voicing itself, so a
diagram cannot contradict the tab beside it — never invented. `why_in_set`,
`mode_flip_guard` and `cannot_verify` are not surfaced: they are written for the
team, not for a player.

**The restriction goes on all three selector branches, not just the catalog
one.** This is a deliberate widening of scope item 3, and it is stated rather
than buried. The catalog branch is the *least* likely to fire — `working_on_pick`
takes 75% of calls for a user with any working_on song, and `user_bench_pick`
shadows the catalog entirely whenever the user owns any song at all. Restricting
the catalog alone would leave a pilot user served generator tab on most days,
which is the state FLE-102 measured at 0 of 10 fit to practise from. **The cost:
under the flag, a pilot user does not see their own working_on songs unless those
songs are in the verified ten.** That is real, and it is what the gate asks for.

**The 0.70 cap lands on the selector, not the onboarding seed.** Read literally,
"cap seeded player_level" is a no-op today: `_ONBOARDING_SEED_MASTERY` tops out
at 0.50, so no newly-onboarded user can start above it. The level that actually
reaches 0.75 is one *earned* during the pilot. So the cap applies to the level
the selector windows on — the only placement that closes the window the issue is
worried about — and only there, since `player_level` also feeds the teaching
prompt and session planning, where capping it would mean teaching a 0.85 player
as a 0.70 player for no reason connected to catalog coverage.

**Verified wins over cache, and is still charged a view.** The verified branch
sits *before* the cache-hit branch: a `songs` row for one of the ten may already
carry a generated breakdown, and returning it because it happens to be cached
would serve generator output under a song we told the pilot was hand-checked. It
sits *after* the cap check, so the view still counts — exempting verified songs
from the 5/day cap is defensible (serving a stored tab spends nothing) but that
cap is Hernan's decision from 2026-10-04 and silently changing what it counts is
not this issue's call. **Flagged for him, not taken.**

## Everything is inert until the flag is set

`FLETCHER_PILOT_VERIFIED_ONLY` is read per call, not at import, so it is a
deploy-time env setting on the Railway `fletcher` service rather than a code
change. With it off, the selector, the breakdown endpoint and song-of-day behave
exactly as they did before — the column is written and nothing reads it. An
unparseable value logs and reads as OFF: a typo must not silently narrow the
catalog to ten songs. The `player_level` cap is `LEAST(level, 1.0)` when off —
the identity on a `NUMERIC(4,3)` in `[0,1]` — so the capped and uncapped paths
are one shape of SQL rather than two that can drift.

## Results on the dev DB

```
validated  10/10 parse as Breakdown
wrote      10          (re-run: wrote 0, unchanged 10 — idempotent)
pruned     0
verified in catalog: 10   0.190 → 0.950
coverage over player_level [0.20, 0.70]: no dead or thin windows — every level has 2+ songs
```

`fle103_verify_pilot_tabs.py`: **80/80 measures, 10 songs, 0 warnings, all checks
pass** — and the same eight checks run again in the test suite against rows read
back *out of* the column, 80/80, so a shaping bug or a lossy JSONB round-trip
cannot pass as verified.

The issue's check, at each level it names: a pilot user at 0.20 / 0.45 / 0.70
gets a verified song and a reroll that moves to a different verified song. A user
earned to 0.95 windows on 0.70 and is served a verified song rather than the
single-song dead-reroll window.

## Two things I fixed on the way through

**The loader's `--dry-run` was a guaranteed false alarm.** It reported coverage
from a `SELECT ... WHERE verified_tab IS NOT NULL` issued *after* the skipped
write, so on a fresh database every window read DEAD and the exit code was 1 on a
load that was in fact healthy — the one mode whose job is "is this file safe to
load" always said no. Coverage is now projected from what the run decided.

**`breakdown_available` had to learn the second yes-path.** A hand-verified song
is served from `song_catalog.verified_tab` and never sets
`breakdown_generated_at` on the songs row, so the FLE-67 cache signal alone reads
False for exactly the ten songs the pilot is restricted to — and the client
disables the CTA on False. Extracted to `_breakdown_available` rather than
inlined a third time; this field already drifted between its two call sites once.

## Not done here

- **Drills** — out of scope per the issue; the verified set is tab only.
- **Prod rollout** — migration 0016 and the loader have NOT been run against
  prod, and the flag is NOT set anywhere. Both are deploy actions behind the
  closed beta gate (FLE-88 / FLE-89), and only Hernan calls that gate.
- **The FLE-103 §1c schema drift** (prod stamped `alembic_version = 0015` with
  0015's `rights_status` column absent) is untouched. Migration 0016 is
  `ADD COLUMN IF NOT EXISTS` so it is safe to land on a hand-patched database,
  but that is a posture for the drift, not a diagnosis of it.
