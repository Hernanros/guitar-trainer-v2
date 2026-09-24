# 260916-ktr — Mobile test suite green, and the screen suites can actually render

**Board issues:** FLE-40, FLE-47 · **Owner:** Kaya (Mobile Engineer) · **Base:** `3cd1187`
**Mode:** GSD quick (gsd-sdk CLI unavailable in this environment — workflow followed manually:
`.planning/quick/` artifacts, atomic commits, STATE.md quick-task row)

## Task

The suite was red and had been red for long enough to stop being a signal: 3 of 13 suites could not
run at all, and the one failing assertion was wrong about its own fixture. Doro found it during the
FLE-3 pre-flight and correctly left it alone — release/QA is not the owning lane.

Starting state, measured: **3 suites failing to run, 1 test failing, 177 passing.**
Finishing state, measured: **13 suites green, 202 passing, `tsc --noEmit` clean.**

## What was actually broken (four separate causes, not one)

### C1 — `standard-navigation` ships untranspiled ESM

`expo-router` imports it, so any suite touching a screen died at require time with
`SyntaxError: Cannot use import statement outside a module`. The existing
`transformIgnorePatterns` allow-list covers `expo*` and `@react-navigation/*`, but
`standard-navigation` is a **top-level package name** that matches neither.

Fix: add it to the allow-list. One word.

### C2 — `react-native-mmkv` v4 is a NitroModules package

`createMMKV()` reaches for a JSI binding that does not exist in a Node process, so importing it
throws. The Today screen pulls it in transitively (`todaySong` → `apiClient` → `mmkv`), which is why
that suite failed at require time rather than on an assertion.

Fix: `mobile/__mocks__/react-native-mmkv.ts`, auto-applied the same way the existing `expo-audio`
and `expo-keep-awake` manual mocks are.

**It is a real in-memory store, not a bag of `jest.fn()`s.** Everything worth testing around MMKV is
read-after-write — the user id is generated once and reused, the wizard resumes on the section it
last wrote, `clearOnboardedAt()` must make `getOnboardedAt()` return null. A stub whose `getString`
returns a constant passes all of those no matter what the code does. Stores are keyed by instance id
so two `createMMKV({ id })` calls with the same id share state, matching the real package — which is
the property that makes `mmkv.ts` and `queryClient.ts` safe to keep on separate ids.

### C3 — `TabNotation.test.tsx` asserted against its own fixture

The `should iterate over all measures not just measures[0]` test never imported TabNotation. It
built a local fixture, reduced over it, and asserted on the result — **so the regression in its own
name could not fail it.** It also failed outright: four `makeMeasure` calls each holding one beat
produce 4, against a hardcoded 16.

Fix: render the real component with 4 measures × 4 beats and frets 1–16, every label distinct.

**Mutation-verified, because a test that has never failed is not yet a test.** Patching
TabNotation's `tab.measures.map(...)` to `[tab.measures[0]].map(...)` fails the new test (1 failed,
10 passed) and passes the old one. Restored, 11/11 green.

Note for anyone adding SVG assertions: RNTL's `getByText` does **not** work here. react-native-svg
renders `<SvgText>` to an `RNSVGText`/`RNSVGTSpan` pair and puts the glyphs in a `content` *prop*,
not in children. The test reads `props.content` off the rendered tree. Also, RNTL 14 on React 19
makes `render()` **async** — `const { getByText } = render(...)` silently yields undefined queries
and `screen` reports "render function has not been called". `await` it.

### C4 — `(tabs)/index.test.tsx` was never executed by anything

Written before the repo had a runner, as module-load-time `throw`s under a header instructing the
reader to run `npx tsc --noEmit` to validate it. **That stopped being true when tsconfig.json began
excluding `**/*.test.tsx`** — which is how the file still imported `FletcherLineVariant` from
SongOfDayCard long after that type, and the `FLETCHER_LINE` map it guarded, were deleted from the
app. No runner, no typecheck, no failure possible. Under Jest it then failed a second way: no
`describe`/`it` meant "Your test suite must contain at least one test".

Rewritten as real tests, and three blocks were **deleted rather than converted**:

| Block | Why it went |
|---|---|
| `FLETCHER_LINE` copy contract | Compared a locally declared map against itself; the constant no longer exists in `src/` |
| `rerollsLeft` / bank-chip gating | Re-implemented TodayScreen's one-line conditionals locally and asserted on the copy |
| 409 handling | Asserted that a string literal it had just written contained `'HTTP 409'` |

What replaced them is bound to real source: `localCalendarDay` (real import, plus a new case pinning
*local* vs UTC day — the distinction that matters for anyone west of UTC late at night) and a real
render of the real `FromTheBankTag`.

`rerollsLeft` and the chip gate are left **explicitly uncovered**, with a note in the file saying so.
They are inline in TodayScreen and unexported; covering them honestly means rendering that screen
against a mocked `useTodaySong` + QueryClientProvider + router. That harness is follow-up work.
Asserting on a local copy, as this file did, is worse than an honest gap — it reads as coverage and
is none.

## Also removed

`src/components/app-tabs.tsx` and `app-tabs.web.tsx` — `create-expo-app` scaffolding for a Home /
Explore tab bar, **zero importers anywhere in the repo**. The web variant held the only
`tsc --noEmit` error (`"/explore"` is not a valid typed route — there is no such route; the real tab
bar is `(tabs)/_layout.tsx` with index / library / toolkit / settings). Doro's FLE-40 called it:
"either add the `/explore` route or drop the link." Dropped, as a dead pair rather than half a pair.
Recoverable from git history if ever wanted.

## Verified vs inferred

**Verified** (ran it): 13/13 suites, 202/202 tests, `tsc --noEmit` exit 0. Mutation check on the
multi-measure test, both directions.

**Inferred** (not run here): nothing in this task needs a device. The two screen suites now execute
in CI, but neither renders a full screen yet — C4's note is the honest boundary.

## Known, pre-existing, not fixed here

`A worker process has failed to exit gracefully` prints after every run, including before these
changes. A timer is outliving a suite somewhere. It does not fail the run and chasing it is its own
task — flagged, not silently absorbed.
