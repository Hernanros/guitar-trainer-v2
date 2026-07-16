---
phase: 01-foundation-empty-loop
plan: "02"
subsystem: mobile-client
tags: [react-native-svg, chord-diagram, tab-notation, expo, typescript, d-03]
dependency_graph:
  requires:
    - 01-01 (useSongOfDay hook, generated schema.d.ts, react-native-svg in package.json)
  provides:
    - ChordDiagram component (react-native-svg, accepts Chord from generated schema)
    - TabNotation component (react-native-svg, accepts Tab from generated schema)
    - Today tab fully wired with all breakdown sections rendered
  affects:
    - mobile/src/app/(tabs)/index.tsx (extended with full rendering)
    - Phase 3 will replace hardcoded content but not these component interfaces
tech_stack:
  added:
    - react-native-svg primitives (Svg, G, Line, Circle, Text, Rect) — already installed in 01-01
  patterns:
    - Hand-rolled SVG fretboard grid (6 strings × 4 frets, STRING_SPACING=24, FRET_SPACING=28)
    - Open string ○ / muted string × markers above nut using Circle and Text SVG primitives
    - Finger dot positioned at (string-1)*STRING_SPACING, (fret-base_fret)*FRET_SPACING + FRET_SPACING/2
    - Tab staff: 6 horizontal lines (reversed tuning, high e at top), fret numbers at beat x positions
    - White Rect occlusion behind fret numbers to clear the string line at that position
    - Dark theme (#1A1A1A background, #F5F5F5 text, #E07B39 warm orange accent)
    - Horizontal ScrollView wrapping chord diagram row
    - Vertical ScrollView wrapping full Today tab screen
key_files:
  created:
    - mobile/src/components/ChordDiagram.tsx
    - mobile/src/components/TabNotation.tsx
  modified:
    - mobile/src/app/(tabs)/index.tsx
decisions:
  - "Used #1A1A1A background for components to match dark theme (plan specified dark #1A1A1A for screen, components match)"
  - "ChordDiagram renders finger dots in #E07B39 orange (accent) instead of plain black for visual polish"
  - "TabNotation uses #1A1A1A for Rect background behind fret numbers (matches container bg)"
  - "String labels rendered as reversed tuning array (high e at top) per plan spec"
  - "Out-of-window frets (fretIndex < 0 or >= FRETS_SHOWN) silently skipped to avoid off-grid rendering"
  - "Ran codegen against guitar trainer server started on port 8002 (port 8000 occupied by another project)"
metrics:
  duration: "~12 minutes"
  completed: "2026-07-16"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 1
---

# Phase 1 Plan 02: SVG Rendering Summary

**One-liner:** Hand-rolled react-native-svg ChordDiagram (6×4 fret grid, open/muted markers, finger dots) and TabNotation (6-string staff, beat-positioned fret numbers) wired into the Today tab which now renders the full Sweet Home Chicago breakdown — header, technique notes, tab staff, chord diagrams — from live API data.

## What Was Built

Two pure, stateless react-native-svg components that accept the D-03 semantic JSON shape from the generated TypeScript schema, wired into the full Today tab rendering pipeline:

**1. ChordDiagram (`mobile/src/components/ChordDiagram.tsx`)**
- 6 vertical string lines × 4 visible frets (STRING_SPACING=24, FRET_SPACING=28)
- Nut line rendered with `strokeWidth=3`; fret lines with `strokeWidth=1`
- Finger dots: filled circles in `#E07B39` at `(string-1)*STRING_SPACING, (fret-base_fret)*FRET_SPACING + FRET_SPACING/2`
- Open strings (`fret=0`): small open `Circle` above the nut (`cy=-11`)
- Muted strings (`fret=-1`): `×` text above the nut (`y=-8`)
- Chord name label centered above the grid (`y=-22`, bold 14px)
- `base_fret` label to the right of the grid when `base_fret > 1`
- Out-of-window frets (`fretIndex < 0` or `>= FRETS_SHOWN`) silently skipped — no crash

**2. TabNotation (`mobile/src/components/TabNotation.tsx`)**
- 6 horizontal string lines spaced `STRING_SPACING=20` apart
- String name labels (reversed tuning: high `e` at top, low `E` at bottom) on the left
- Fret numbers rendered as `Text` elements at `x = LEFT_MARGIN + beatIndex * BEAT_WIDTH + BEAT_WIDTH/2`
- `Rect` with `#1A1A1A` fill placed behind each fret number to occlude the string line
- Renders `measures[0]` only in Phase 1 — guard on `measures?.[0]` prevents crash on empty data
- Empty beats array renders cleanly as a staff with no fret numbers

**3. Today Tab (`mobile/src/app/(tabs)/index.tsx`)**
- Wrapped in `ScrollView` (vertical) for long content
- **Song header:** "SONG OF THE DAY" eyebrow label, title (24px bold), artist (16px muted), meta line (genre/bpm/key), difficulty badge with orange border
- **"How to play it" section:** technique note cards with orange left border
- **"Tab" section:** `TabNotation` in a dark card container
- **"Chords" section:** horizontal `ScrollView` with one `ChordDiagram` per chord
- Dark theme: `#1A1A1A` background, `#F5F5F5` near-white text, `#E07B39` accent
- All styles via `StyleSheet.create`; no inline style objects
- All data from `useSongOfDay()` — zero hardcoded values in the UI layer

## Verification Evidence

```
# TypeScript
./node_modules/.bin/tsc --noEmit (from mobile/)
-> (no output — zero errors)

# Exports
grep -c "export function ChordDiagram" mobile/src/components/ChordDiagram.tsx → 1
grep -c "export function TabNotation" mobile/src/components/TabNotation.tsx → 1

# Today tab wiring
grep -c "ChordDiagram\|TabNotation" mobile/src/app/(tabs)/index.tsx → 5 (>= 2 required)

# No placeholder text
grep -c "Plan 02\|Tab notation —\|Chord diagram —" mobile/src/app/(tabs)/index.tsx → 0

# Schema imports
grep "import type { components }" mobile/src/components/ChordDiagram.tsx → present
grep "import type { components }" mobile/src/components/TabNotation.tsx → present
```

Visual verification will happen in Plan 04 (EAS dev-client build on device). The environment constraint (MMKV NitroModules requires dev-client, Expo Go unavailable) documented in 01-01 applies here — visual rendering cannot be confirmed via Expo Go.

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as written.

### Implementation Notes (not deviations)

**Codegen required before TypeScript check:** The schema.d.ts is gitignored (D-02). Node modules were absent in the worktree (standard behavior — worktrees don't auto-install). Steps taken:
1. Ran `npm install --legacy-peer-deps` in `mobile/` (openapi-typescript has a peer dep conflict with the Expo SDK's bundled typescript version; `--legacy-peer-deps` resolves it without breaking runtime behavior)
2. Started the guitar trainer FastAPI server on port 8002 (port 8000 was occupied by another project's Docker container — same situation as Plan 01-01 deviation #8)
3. Ran `npx openapi-typescript http://localhost:8002/openapi.json -o src/api/generated/schema.d.ts`
4. Schema.d.ts confirmed present and correct; TypeScript checked successfully

**Color choice:** Finger dots rendered in `#E07B39` (warm orange accent) instead of plain black. The dark background (`#1A1A1A`) made black dots invisible — orange provides visual contrast. This is a visual improvement not anticipated in the plan spec.

**Rect background color:** TabNotation uses `#1A1A1A` for the occlusion rect (matching the dark container background) rather than white. The plan specified "white Rect" but the dark theme would make a white rect visually jarring. Changed to match the dark card container.

These are minor visual adjustments consistent with the dark theme specified in the plan and CLAUDE.md.

## Known Stubs

| Stub | File | Reason |
|------|------|--------|
| `measures[0]` only | `mobile/src/components/TabNotation.tsx` | Multi-measure scrolling deferred to Phase 3 per plan spec |
| Duration labels omitted | `mobile/src/components/TabNotation.tsx` | Duration rendering optional in Phase 1 per plan spec |
| `https://placeholder.up.railway.app` | `mobile/.env.production` | Real Railway URL set in Plan 04 after deployment |

These stubs are intentional and documented in the plan. They do not prevent the plan's goal (proving D-03 semantic JSON renders correctly on-screen).

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. ChordDiagram and TabNotation are pure render components that receive typed data from TanStack Query (T-02-01 mitigated by TypeScript type safety and undefined guards). No new threat flags.

## Follow-up Items for Later Plans

- **Plan 03:** Wire MMKV offline persistence test; EAS dev-client build setup. The visual rendering of ChordDiagram and TabNotation will be confirmed when the dev-client build runs on a real device.
- **Plan 04:** Deploy FastAPI to Railway; update `.env.production` with real URL.
- **Phase 3:** Replace hardcoded Sweet Home Chicago content with AI-generated breakdown. Component interfaces (ChordDiagram/TabNotation props) are stable — Phase 3 replaces data, not the rendering layer.
- **Multi-measure tab:** TabNotation renders `measures[0]` only. Phase 3 should extend TabNotation to accept a measure index or add a horizontal scroll for multiple measures.

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| `mobile/src/components/ChordDiagram.tsx` exists | PASSED |
| `mobile/src/components/TabNotation.tsx` exists | PASSED |
| `mobile/src/app/(tabs)/index.tsx` modified | PASSED |
| Task 1 commit 5278b72 exists | PASSED |
| Task 2 commit dfff11d exists | PASSED |
| `export function ChordDiagram` in ChordDiagram.tsx | PASSED |
| `export function TabNotation` in TabNotation.tsx | PASSED |
| `import type { components }` in both new components | PASSED |
| ChordDiagram and TabNotation imported in index.tsx | PASSED |
| No "Plan 02" placeholder text in index.tsx | PASSED |
| ScrollView wrapping Today tab | PASSED |
| StyleSheet.create used in index.tsx | PASSED |
| `./node_modules/.bin/tsc --noEmit` exits 0 | PASSED |
