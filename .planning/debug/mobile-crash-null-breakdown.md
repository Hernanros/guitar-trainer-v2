---
status: resolved
trigger: "iOS app crashes to home screen when user clicks anywhere on Today tab post-115fc05 deploy. Root cause: 33d78ac coerces placeholder breakdown to None server-side; mobile breakdown screen at [songId].tsx:162 accesses song.breakdown.technique_notes.map(...) without null guard → TypeError → RN unhandled exception → iOS kills app."
created: 2026-08-17
updated: 2026-08-17
phase: 04
milestone: v1.0
---

# Debug Session: mobile-crash-null-breakdown

## Observed facts

**User report (2026-08-17):** After 115fc05 deploy, user opens Today tab, sees the song card. Clicking anywhere on the screen (title, "See the breakdown" CTA, reroll) crashes the app back to the iOS home screen.

**Server logs (`/tmp/railway-click.txt`):**
```
GET /healthz                       → 200 OK
GET /api/v1/song-of-day            → 200 OK
GET /api/v1/users/601f2ca5-...     → 200 OK  (fresh install, new UUID)
POST /api/v1/today-song/reroll     → 200 OK
```
Every API call succeeds. Crash is 100% client-side, after successful data fetch.

**Mobile code (`mobile/src/app/breakdown/[songId].tsx:162, 174, 187`):**
```tsx
{song.breakdown.technique_notes.map((note: TechniqueNote, i: number) => (
  ...
))}
<TabNotation tab={song.breakdown.tab} />
{song.breakdown.chords.map((chord: Chord) => (
  ...
))}
```

Direct property access on `song.breakdown`. No null guard. Also renders `{song.genre} · {song.bpm} BPM · Key of {song.key}` (would display "undefined · undefined BPM ..." but not crash).

**Server change (commit 33d78ac):**
```python
# server/app/models/song.py — added:
breakdown: Optional[Breakdown] = None

@field_validator("breakdown", mode="before")
@classmethod
def coerce_placeholder_breakdown(cls, v):
    """Coerce placeholder JSONB dicts to None so Breakdown validation is skipped."""
    if isinstance(v, dict) and set(v.keys()) == {"placeholder"}:
        return None
    return v
```

Commit body warned: "Callers should consult TodaySongResponse.breakdown_available (server-authoritative flag) rather than probe SongResponse.breakdown for None." — but the current EAS build has no such guard. Contract change went out server-only.

## Root cause (evidence-backed)

Server-side hotfix 33d78ac broke the mobile client's implicit assumption that `song.breakdown` is always a valid `Breakdown` object. Placeholder songs (any user-onboarded song before Phase 3 fills the real breakdown) now return `breakdown: null` from the API. Mobile client dereferences `.technique_notes/.tab/.chords` on null → TypeError → React Native unhandled → iOS kills app.

Every path that navigates to `/breakdown/{songId}` triggers this:
- `SongOfDayCard` title Pressable (`onTitlePress={() => router.push(...)}`)
- "See the breakdown" CTA (`onSeekBreakdown={ratedLabel ? undefined : () => router.push(...)}`)

The rerroll click also crashes because it triggers a query invalidation → refetch → new song → re-render → same crash on the new song's null breakdown.

## Fix scope (user approved: server hotfix now + mobile follow-up)

### Server hotfix — replace None with empty-but-valid Breakdown

In `server/app/models/song.py`, change `coerce_placeholder_breakdown` to return an empty Breakdown dict instead of None:

```python
@field_validator("breakdown", mode="before")
@classmethod
def coerce_placeholder_breakdown(cls, v):
    """Coerce placeholder JSONB dicts to an empty-but-valid Breakdown shape.

    Rationale: mobile clients from the current EAS build (2026-08-16) access
    song.breakdown.tab / .chords / .technique_notes without null guards.
    Returning an empty Breakdown instead of None keeps them from crashing
    (empty .map() iterations render nothing). Once mobile ships graceful
    degradation on breakdown_available, the coerce target can become None.
    """
    if isinstance(v, dict) and "placeholder" in v and "tab" not in v:
        return {
            "tab": {"measures": [], "tuning": ["E", "A", "D", "G", "B", "e"]},
            "chords": [],
            "technique_notes": [],
        }
    return v
```

Keep `breakdown: Optional[Breakdown] = None` in the schema (allows future clients to receive true None once the mobile guard ships; doesn't break current clients since we're returning the empty dict).

### Regression tests — update existing test_song_of_day_nullable.py

The 33d78ac test suite has `test_song_of_day_placeholder_breakdown_returns_none` (or similar) — need to update to assert empty Breakdown shape instead. Add a new test that specifically asserts the mobile-safe shape (`tab.measures == [] AND chords == [] AND technique_notes == []`).

### Mobile follow-up (deferred to next EAS batch)

- `mobile/src/app/breakdown/[songId].tsx` — guard on `today.breakdown_available` at the top of the render. If false, show a "Fletcher is preparing this breakdown…" placeholder state instead of empty tab/chords sections.
- `mobile/src/app/(tabs)/index.tsx` — disable the `onTitlePress` navigation when `today.breakdown_available === false`, or route to a "not ready" screen.
- Update `mobile/src/api/generated/schema.d.ts` via `npm run codegen:local` so mobile TypeScript reflects Optional breakdown.

### Recovery for the affected user

No DB touch needed. After server hotfix deploys:
- Force-quit + relaunch → Today tab
- Song-of-day renders normally
- Clicking title/CTA navigates to breakdown screen → renders **empty** sections (ugly but stable)
- User can rate the song from that screen (RatingPills still work)
- Mobile follow-up will polish the empty-state UX in the next EAS batch

## Current Focus

- **hypothesis (evidence-backed):** Server-side 33d78ac coerce → None broke mobile assumption of non-null breakdown. Fix: coerce to empty Breakdown shape so mobile's `.map()` calls iterate empty arrays without crashing.
- **next_action:** DONE — validator + tests updated, verified, committed. Awaiting user push confirmation.

## Evidence

- Server logs `/tmp/railway-click.txt` — all API responses 200 OK, no server errors during the crash window.
- Mobile source `[songId].tsx:162,174,187` — 3 unconditional accesses to `song.breakdown.{technique_notes,tab,chords}`.
- Commit 33d78ac diff — explicitly returns `None` from `coerce_placeholder_breakdown` for placeholder JSONB.
- Commit 33d78ac message body — even acknowledges the mobile contract change ("Callers should consult TodaySongResponse.breakdown_available rather than probe SongResponse.breakdown for None") but the mobile update was deferred.
- Current EAS build (`690bc876`) was built 2026-08-16 09:21 UTC — BEFORE 33d78ac (which was committed 14:07 local == 11:07 UTC same day). Mobile TS types + code were snapshotted with the old contract.

## Eliminated

- ~~"Server-side crash"~~ — DISPROVEN. All requests return 200; no ERROR/Traceback in logs during click window.
- ~~"115fc05 cascade fix regressed"~~ — DISPROVEN. Cascade fix only touches insert path, doesn't affect song-of-day response shape.
- ~~"Song data corruption"~~ — DISPROVEN. song-of-day returned valid 200; the returned song just has `breakdown=None` because DB row has `{"placeholder": ...}` and 33d78ac coerces that to None.

## Resolution

**Root cause:** commit 33d78ac (2026-08-16 server hotfix) coerced placeholder breakdown JSONB to `None`, but the in-field EAS iOS build 690bc876 (built 2026-08-16 09:21 UTC, BEFORE 33d78ac shipped) accesses `song.breakdown.tab/.chords/.technique_notes` at `mobile/src/app/breakdown/[songId].tsx:162,174,187` without null guards. Null-deref → TypeError → RN unhandled → iOS kills app to home screen.

**Fix applied (server-side hotfix, single commit):**

1. `server/app/models/song.py` — `coerce_placeholder_breakdown` now returns an empty-but-valid Breakdown shape (`{"tab": {"measures": [], "tuning": ["E","A","D","G","B","e"]}, "chords": [], "technique_notes": []}`) instead of `None`. Placeholder detection unchanged (`isinstance(v, dict) and "placeholder" in v and "tab" not in v`). `breakdown: Optional[Breakdown] = None` schema kept for future post-guard clients.
2. `server/tests/test_song_of_day_nullable.py`:
   - `test_song_of_day_serves_song_with_null_metadata_and_placeholder_breakdown` (Test 2, DB-backed) — assertion updated from `song["breakdown"] is None` to `song["breakdown"] is not None` + empty-arrays shape check.
   - `test_song_response_placeholder_breakdown_coerces_to_none` renamed to `test_song_response_placeholder_breakdown_coerces_to_empty_breakdown` (Test 3, unit) — assertion updated to check empty Breakdown instance instead of None.
   - **New:** `test_placeholder_breakdown_returns_mobile_safe_empty_shape` — client-contract regression guard that explicitly asserts the exact mobile-safe empty shape with clear error messages tied to EAS build 690bc876 and the crashing mobile file paths.
   - Module docstring updated with 2026-08-17 follow-up context.

**Verification:**
- All 6 tests in `test_song_of_day_nullable.py` pass (both new + all existing).
- Full 7-file SongResponse-touching test set (test_breakdown_schema_spike, test_sessions, test_today_song_selector, test_song_of_day_nullable, test_breakdowns_mocked, test_governor, test_song_of_day_quota): 64 passed, 1 skipped, 3 pre-existing unrelated failures in `test_today_song_selector.py::test_get_tz_offset_dep_*` (coroutine-never-awaited fixture drift; part of the documented 18-28 baseline failures; fail in isolation without importing my modified module; not a new regression).
- Zero-new-regressions bar met.

**Deferred to next EAS batch (unchanged, still owed):**
- `mobile/src/app/breakdown/[songId].tsx` — guard on `today.breakdown_available` at render top; show "Fletcher is preparing this breakdown…" placeholder state when false.
- `mobile/src/app/(tabs)/index.tsx` — disable `onTitlePress` navigation when `today.breakdown_available === false`, or route to a "not ready" screen.
- Regenerate `mobile/src/api/generated/schema.d.ts` via `npm run codegen:local` so mobile TypeScript reflects Optional breakdown.
- Once shipped, `coerce_placeholder_breakdown` can be flipped back to returning `None` and `test_placeholder_breakdown_returns_mobile_safe_empty_shape` retired (or inverted).

**Recovery for affected user (unchanged):** No DB touch needed. After deploy, force-quit + relaunch. Song-of-day + breakdown screen render (empty sections on placeholder songs — ugly but stable). Rating still works.

**Status:** committed locally, not pushed — awaiting orchestrator push confirmation with user.
