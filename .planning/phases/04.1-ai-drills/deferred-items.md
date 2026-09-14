# Phase 4.1 — Deferred Items

Log of out-of-scope discoveries surfaced during 04.1 execution. Do NOT fix
these inside 04.1 plans — surface them for the owner to schedule.

## Pre-existing test failures (discovered during 04.1-01 regression check)

Confirmed unrelated to drills work by isolating each failing test on the
pre-04.1 baseline (fa5ba31). All fail there too. Root causes are prior sessions'
API contract drift or test-isolation bugs — not caused by Plan 04.1-01.

| Test | Category | Symptom |
|------|----------|---------|
| `tests/test_users_bootstrap_mocked.py::test_full_bootstrap_persists_correctly` | API contract drift | POST body sends `songs.can_play` as `"Sweet Home Chicago, Blackbird"` (string) but Pydantic model now requires `list[str]` — 422. |
| `tests/test_users_bootstrap_mocked.py::test_fail_open_savepoint_preserves_user_row` | Same as above | Same 422 body shape mismatch. |
| `tests/test_users_bootstrap_mocked.py::test_idempotent_re_post_returns_existing` | Same as above | Same. |
| `tests/test_today_song_selector.py::test_get_tz_offset_dep_valid_range` | Test bug | `RuntimeWarning: coroutine 'get_tz_offset_minutes' was never awaited`. Async dep not awaited in test. |
| `tests/test_today_song_selector.py::test_get_tz_offset_dep_rejects_out_of_range` | Same as above | Same. |
| `tests/test_today_song_selector.py::test_get_tz_offset_dep_rejects_non_integer` | Same as above | Same. |
| Cross-file cascade failures in test_song_of_day_quota / test_today_song_selector when run in FULL suite | Test isolation | Pass individually. Only fail in `pytest` full-suite runs because of shared session-scoped event loop + DB fixture state. |

All 33 drills tests (11 schema + 5 soft-fail + 17 breakdowns_mocked) added by
Plan 04.1-01 pass cleanly both in isolation and when run alongside the rest of
the suite — none of the failures above involve the drills code path.
