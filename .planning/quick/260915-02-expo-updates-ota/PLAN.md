---
slug: 260915-02-expo-updates-ota
date: 2026-09-15
issue: FLE-27
mode: quick
---

# FLE-27 — Pilot update story: adopt `expo-updates` (Option A)

**Decision: Option A.** `expo-updates` is installed and configured; the pilot
build must be cut from this config so the binary that reaches testers is
updates-capable.

The deciding argument is not convenience, it is the EAS build quota. OTA updates
consume **zero** EAS builds, and `.planning` memory records the quota as the
scarce resource that mobile work is batched around. Option B spends a build on
every mid-pilot fix *and* spends tester goodwill on every reinstall.

## What changed

| File | Change |
|---|---|
| `mobile/package.json` | `+ expo-updates ~57.0.22` (SDK 57 compatible, via `npx expo install`) |
| `mobile/app.json` | `+ expo.updates.url`, `+ expo.runtimeVersion.policy: "fingerprint"` |
| `mobile/eas.json` | `+ channel` on all three build profiles |

No native-directory edits. `mobile/ios` is gitignored (`mobile/.gitignore:42`),
so the project is on Continuous Native Generation — EAS prebuilds and autolinks
`expo-updates` from `package.json` alone.

## D1 — The FLE-10 question, answered honestly

FLE-10's constraint is **"No analytics SDKs in a pilot build."** `expo-updates`
does not violate it, but it is not silent either, and the difference matters
enough to write down rather than wave through.

Verified by reading the installed source, not from memory
(`node_modules/expo-updates/ios/EXUpdates/AppLoader/FileDownloader.swift:436-470`,
mirrored in `android/.../loader/FileDownloader.kt:784,900`). Every update check
sends these headers to `u.expo.dev`:

- `Expo-Platform`, `Expo-Runtime-Version`, `Expo-Protocol-Version`,
  `Expo-API-Version`, `Expo-Updates-Environment`, `expo-channel-name`,
  `expo-current-update-id` — all update-routing data. This is the server
  deciding *which bundle you get*. It is not behavioural telemetry.
- **`EAS-Client-ID`** — a random UUID generated on first launch and persisted
  per install. It is not an advertising ID, not a device fingerprint, and not
  joined to any user identity, but it *is* a stable per-install identifier sent
  to Expo on every launch. Calling that "no telemetry" would be false.
- **`Expo-Fatal-Error`** — if the previous launch crashed, the error string
  (truncated to 1024 chars) is sent on the next update request. This is the
  anti-bricking mechanism: it is how the server can stop serving an update that
  is killing clients. It is also, unavoidably, crash data leaving the device.

**Call:** this is update-delivery plumbing, not an analytics SDK. There is no
event pipeline, no screen tracking, no session recording, no third-party
collector, and nothing that produces a product-analytics dataset. It clears
FLE-10. The `Expo-Fatal-Error` behaviour should be stated to pilot testers in
the same breath as anything else the app sends, because it is the one item a
reasonable person would want disclosed.

If that is judged too much for the pilot, `expo.updates.enabled: false` in
`app.json` turns the whole mechanism off without removing the dependency — but
that forfeits Option A entirely and lands you back in Option B, so it should be
a deliberate reversal of this decision, not a quiet toggle.

## D2 — `runtimeVersion` policy is `fingerprint`, not `appVersion`

`fingerprint` hashes everything that can affect the native runtime — autolinked
modules, config plugins, `eas.json`, the resolved Expo config — and refuses to
serve an update whose fingerprint does not match the installed binary.

The failure mode this prevents is the one that actually matters for a pilot:
pushing an OTA whose JS calls a native module the installed binary does not
have. That crashes on launch, and on a tester's device there is no Metro, no
logs, and no recovery — the exact manual-reinstall event Option A exists to
avoid, except now it is an emergency instead of a plan.

`appVersion` would happily serve that update. The documented cost of
`fingerprint` is "you need to build more often," but that cost is nil here: a
native change requires a new build under *any* policy. `fingerprint` only makes
the requirement explicit instead of discovering it on a tester's phone.

Verified computable — `npx expo-updates fingerprint:generate --platform ios`
resolves to `aa1b4ad8d5b06c0438dbab888af197cd538fca8b` and correctly lists
`expo-updates 57.0.22` among the autolinked iOS modules.

## D3 — Defaults left alone, deliberately

`updates` carries only `url`. The SDK 57 defaults are already the right ones for
a pilot, and spelling them out invites drift:

- `checkAutomatically: ON_LOAD` — checks on every cold start.
- `fallbackToCacheTimeout: 0` — **launch never blocks on the network.** A tester
  on bad wifi gets the app instantly; a downloaded update applies on the *next*
  cold start. See "what testers experience" below — this is an operational fact,
  not a detail.
- `useEmbeddedUpdate: true` — the binary always retains a working bundle.
- `disableAntiBrickingMeasures: false` — keep the safety net.

## D4 — Channels on every profile

`development` / `preview` / `production`. Without a channel an EAS build has no
branch mapping and `eas update` has nothing to publish to. The pilot ships from
`preview` (it is the profile that already produces an internal-distribution APK
and ad hoc IPA), so pilot updates go to the `preview` channel.

## Pilot operating procedure

**Cutting the pilot build** — must be built from this commit or later, otherwise
the binary has no updates client and nothing below works:
```
cd mobile && eas build --profile preview --platform all
```

**Shipping a JS-only fix mid-pilot** — no build, no reinstall, no quota:
```
cd mobile && eas update --branch preview --message "<what you fixed>"
```

**What testers experience:** nothing. No prompt, no reinstall. The update
downloads in the background on the next cold start and is live on the one after
that. Practically: a fix shipped tonight reaches a tester who opens the app
twice tomorrow.

**When a rebuild is still unavoidable:** any native change — adding a native
dependency, changing a config plugin, bumping the Expo SDK. The fingerprint will
have moved and `eas update` will simply refuse to serve those clients, which is
the designed behaviour, not a failure.

## Limits this does NOT remove

1. **The initial install still needs a build artifact link, and those expire
   after 14 days** (current pair: 2026-09-29). A pilot running longer than two
   weeks still needs re-issued links for *new* testers. OTA only helps devices
   that already have the app.
2. **Split-version ambiguity is reduced, not eliminated.** A tester who does not
   reopen the app stays on the old bundle, silently. Option A shrinks the window
   from "forever, unless they reinstall" to "until their next two cold starts" —
   which is a large improvement and not a guarantee.
3. Because of (2), **the version-visibility discipline Option B needed is still
   worth having.** `Updates.updateId` and `Updates.runtimeVersion` are readable
   at runtime; surfacing them where a tester can report them closes the gap. Not
   done here — new user-facing copy is contract (FLE-10: strings verbatim from
   `UI-SPEC.md`), so it belongs to whoever owns mobile UI. Filed as a follow-up.

## Verification

- `npx expo config --type public` resolves `updates.url` and
  `runtimeVersion.policy: fingerprint`. ✅
- `npx expo-updates fingerprint:generate --platform ios` computes a fingerprint
  and lists `expo-updates 57.0.22` as autolinked. ✅
- `mobile/package-lock.json` diff is **purely additive** (83 insertions, 0
  deletions, no version change to any existing package) — the install pulled in
  `expo-updates` and its transitive deps (`expo-eas-client`, `expo-manifests`,
  `expo-json-utils`, `expo-structured-headers`, `expo-updates-interface`) and
  bumped nothing else. ✅
- `npx jest` — 64/65 pass, 3 suites failing. **Pre-existing and unrelated:**
  re-running with `app.json`/`eas.json` reverted produces byte-identical
  results (3 failed suites, 1 failed test). The failures are an `expo-router`
  transform gap in two suites and a `TabNotation` multi-measure logic test; none
  touch updates. Not fixed here — out of scope for FLE-27.

**Not verified:** no EAS build was run. Whether the updates-capable binary
actually installs and checks in is only provable by consuming a build, which is
exactly the scarce resource under discussion. Fold that check into the pilot
build itself rather than spending a build to confirm config that
`fingerprint:generate` already resolved.
