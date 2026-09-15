# Phase 4 RUNBOOK — Manual Setup Steps

**Purpose:** Manual setup steps that Claude cannot automate for Phase 4 cost governance and admin auth. These steps are performed once by the developer (Hernan) and verified before Slice C ships.

---

## Section 1 — Anthropic Console $20/mo Hard Cap (COST-04, D-07)

The Anthropic Console hard cap is a per-organization monthly spending limit. It stops all API calls (across all app features) when the org exceeds $20 USD in a calendar month, regardless of what the app's per-user cap enforces.

### Steps

1. Open https://console.anthropic.com in a browser and log in with your Anthropic account.
2. Navigate to **Settings → Billing → Usage Limits** (or search for "Usage Limits" in Settings).
3. Set **Hard limit** = **$20.00** for the current calendar month.
4. Set three billing alert thresholds:
   - **50% ($10.00)** — early warning
   - **80% ($16.00)** — action required
   - **100% ($20.00)** — cap approaching
5. Set the alert email address to: **hernan.rosenblum89@gmail.com**
6. Save / confirm all changes.

### Notes

- This is a Console UI-only step. The Anthropic Admin API is beta and adds a credential surface for zero benefit at POC scale — do NOT automate.
- The cap is per-organization (the whole POC org). It is NOT per-user. The app enforces per-user caps (3 breakdowns/7d) separately.
- After Railway deploys, the server startup log prints a reminder line (see Section 3 below).

### Verification

Console displays **"$20 hard limit"** in the Usage Limits section with three alert thresholds listed.

---

## Section 2 — FLETCHER_ADMIN_TOKEN Generation (Slice C dep, D-11 + D-Claude admin-token)

The admin token gates the `/api/v1/admin/curator` endpoint so only the developer can view and action the skill-node curator queue.

### Generate a token

```
openssl rand -hex 32
```

This produces a 64-character hex string. Copy it immediately.

### Add to Railway environment

Option A — Railway CLI:
```
railway variables set FLETCHER_ADMIN_TOKEN=<generated-value>
```

Option B — Railway dashboard:
1. Open your Railway project → **Variables** tab.
2. Add `FLETCHER_ADMIN_TOKEN` = `<generated-value>`.

### Add to server .env.example (documentation reminder only)

The server's `.env.example` already contains (or will contain after Slice C):
```
FLETCHER_ADMIN_TOKEN=<generate-with-openssl-rand-hex-32>
```
Do NOT paste the actual token value there — `.env.example` is committed to git.

### Usage

The developer (Hernan) injects the `X-Admin-Token: <token>` header via a browser extension (e.g., ModHeader for Chrome/Firefox) when visiting `/api/v1/admin/curator` to review pending skill-node proposals.

---

## Section 3 — Verifying the Cap Post-Deploy

After Railway deploys, the server prints the following line on startup:

```
REMINDER: verify $20/mo Anthropic Console cap is set at anthropic.com/console (COST-04, D-07). See .planning/RUNBOOK.md.
```

Check Railway logs (Project → Deployments → latest → Logs) for this line to confirm the deploy is aware. If the line is missing, the server may not have booted correctly.

---

## Section 4 — Shipping a Mid-Pilot Fix Without a Rebuild (FLE-27)

The app uses `expo-updates`. JS-and-asset-only fixes ship over the air and
consume **no EAS build quota** and **no tester reinstall**.

```
cd mobile && eas update --branch preview --message "<what you fixed>"
```

Testers get it on their next cold start (downloads in background, applies on the
one after). No prompt, nothing for them to do.

**The pilot build must be cut from a commit that includes `expo-updates`** —
built before that, the binary has no updates client and `eas update` reaches
nobody:

```
cd mobile && eas build --profile preview --platform all
```

**A rebuild is still required** for any native change — new native dependency,
config plugin change, Expo SDK bump. The `fingerprint` runtime-version policy
detects this and refuses to serve the update rather than crashing testers.

Full decision record, including the FLE-10 telemetry assessment and the limits
this does not remove: `.planning/quick/260915-02-expo-updates-ota/PLAN.md`.

---

*Phase: 04-cost-governor-node-verification*
*Created: Slice B (04-02)*
*Section 4 added: FLE-27 (2026-09-15)*
