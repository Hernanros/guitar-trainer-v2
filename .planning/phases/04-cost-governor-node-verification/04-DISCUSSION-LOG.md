# Phase 4: Cost Governor & Node Verification - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-30
**Phase:** 04-cost-governor-node-verification
**Areas discussed:** Governor cap + failure semantics, Quota UI + Console cap wiring, Skill-node verification pipeline
**Area skipped (Claude's discretion):** Decay scheduler + audit

---

## Governor cap + failure semantics

### Q1 — Cap window arithmetic

| Option | Description | Selected |
|--------|-------------|----------|
| Rolling 7-day window | COUNT calls in last 7 days from now(); no cliff, no tz plumbing | ✓ |
| Calendar week user local tz | Uses X-Timezone-Offset; user sees "resets Monday"; tz complexity on server | |
| Calendar week UTC | date_trunc('week' AT TIME ZONE 'UTC'); simplest server logic, but Sunday-night reset in local time | |

**User's choice:** Rolling 7-day window.
**Notes:** Locked as D-01. "3 left this week" copy is approximate — means "since 7d ago".

### Q2 — 4th-attempt UX

| Option | Description | Selected |
|--------|-------------|----------|
| Hard block + Fletcher-voice error card | Server 429; BreakdownErrorCard shows Fletcher copy; no retry button | ✓ |
| Preemptive disable at 0 remaining | Client uses quota to disable CTA; zero wasted dispatch | |
| Soft warn at N=2, hard block at N=3 | Warning banner on 3rd; hard block on 4th; two-step gate | |

**User's choice:** Hard block + Fletcher card.
**Notes:** Locked as D-02. Fletcher copy: `"Not my tempo. You've had 3 breakdowns this week. Come back in {N} days."` Response body: `{code:'BREAKDOWN_CAPPED', message, resets_at}`.

### Q3 — Token estimation strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Anthropic count_tokens API | Accurate; matches billing 1:1; +50-100ms/call | ✓ |
| Heuristic char-count / 4 | Zero latency; off by 10-30% in edge cases | |
| Static per-call constant | Simplest; useless for spotting prompt regressions | |

**User's choice:** count_tokens API.
**Notes:** Locked as D-03. Also records real usage from `response.usage` post-dispatch — enables prompt-regression detection in logs.

### Q4 — Governor module shape

| Option | Description | Selected |
|--------|-------------|----------|
| Decorator per feature | @governed(feature, cap, window) on each run_* function; explicit at decoration site | ✓ |
| Wrap get_client() proxy | Interception at HTTP layer; scatters feature logic across call sites | |
| FastAPI middleware on AI routes | Simple to add; breaks invariant that every LLM call is governed | |

**User's choice:** Decorator per feature.
**Notes:** Locked as D-04. New module `server/app/ai/governor.py`. Feature caps live in decorator kwargs.

---

## Quota UI + Console cap wiring

### Q1 — Where does quota surface on mobile?

| Option | Description | Selected |
|--------|-------------|----------|
| Inline chip on breakdown CTA | On SongOfDayCard; highest contextual relevance | ✓ |
| Settings row + inline chip only when ≤1 | Persistent Settings row; chip only at low counts | |
| Both always: Settings row + inline chip | Most transparent; adds Settings work (Phase 2 territory) | |

**User's choice:** Inline chip on breakdown CTA.
**Notes:** Locked as D-05. Copy: `"{N} left this week"` when remaining ≥ 1; `"Come back in {N} days"` when 0 (CTA disabled).

### Q2 — How does client learn its quota?

| Option | Description | Selected |
|--------|-------------|----------|
| Embed breakdown_quota in TodaySongResponse | Zero extra requests; server-authoritative; fresh with Today fetch | ✓ |
| Dedicated GET /api/v1/quota | Decouples quota from Today song; useful if more places need it later | |
| Response headers on every AI-adjacent call | Cheap on server; complicates apiFetch + client state | |

**User's choice:** Embed in TodaySongResponse.
**Notes:** Locked as D-06. Shape: `breakdown_quota: {remaining: int, cap: int, resets_at: string}`.

### Q3 — Console $20 cap wiring

| Option | Description | Selected |
|--------|-------------|----------|
| Manual + RUNBOOK + startup log reminder | One-time Console setup; documented; no automation | ✓ |
| Verified by startup probe | Anthropic Admin API called on startup; fails if not $20 | |
| Automated via Admin API on first deploy | Zero-config; overkill for solo POC | |

**User's choice:** Manual RUNBOOK.
**Notes:** Locked as D-07. Steps in new `.planning/RUNBOOK.md`. Alerts 50%/80%/100% → hernan.rosenblum89@gmail.com.

### Q4 — UX when Anthropic itself returns 429/quota-hit

| Option | Description | Selected |
|--------|-------------|----------|
| Structured 503 with distinct Fletcher copy | Server 503 with `code:'FLETCHER_OUT'`; distinct card copy so developer can tell org-level from user-level | ✓ |
| Same 429 as per-user cap | Simpler client; hides org-level failure mode | |
| Explicit 402 Payment Required | Semantically closer; uncommon status code; infra layer weirdness | |

**User's choice:** Structured 503 FLETCHER_OUT.
**Notes:** Locked as D-08. Copy: `"Fletcher's on a break. Try again in an hour."` logger.critical on catch (Railway logs / future Sentry).

---

## Skill-node verification pipeline

### Q1 — Embedding provider for dedup

| Option | Description | Selected |
|--------|-------------|----------|
| No embeddings — normalize + rapidfuzz token_set_ratio | Zero cost, zero vendor, sub-ms latency; thresholds 85/70 | ✓ |
| Voyage AI voyage-3-lite | Anthropic-recommended embeddings; $0.02/1M tokens; adds vendor + secret | |
| OpenAI text-embedding-3-small | Same tradeoff as Voyage | |
| Local sentence-transformers | Zero API cost; +90MB image + torch dep | |

**User's choice:** rapidfuzz fuzzy match.
**Notes:** Locked as D-09. Escape hatch: swap `dedupe_score()` for embedding call behind same signature if fuzzy loses precision later.

### Q2 — Curator queue UX

| Option | Description | Selected |
|--------|-------------|----------|
| Plain FastAPI /admin/curator HTML behind X-Admin-Token | Zero mobile work; browser-only; env-var auth | ✓ |
| psql-only for POC | Cheapest; ugliest UX; fine at ~1 proposal/month | |
| In-app Settings > Curator queue screen | Consistent product UX; overkill for solo curator role | |
| Daily email digest with signed action links | Async-friendly; requires email infra | |

**User's choice:** /admin/curator HTML page.
**Notes:** Locked as D-11. Auth via `FLETCHER_ADMIN_TOKEN` env var. Rejected-tab exposes retroactive review.

### Q3 — canonical_node_id introduction

| Option | Description | Selected |
|--------|-------------|----------|
| Add nullable canonical_node_id FK on skill_nodes; new proposals only | Non-breaking; existing rows stay per-user; incremental | ✓ |
| Separate canonical_skill_nodes table | Cleaner separation; requires data migration or dual state | |
| Defer canonical entirely — verifier only | Simplest; loses cross-user convergence (moot for single-user POC anyway) | |

**User's choice:** Nullable FK on skill_nodes.
**Notes:** Locked as D-12. Alembic 0004: `ALTER TABLE ... ADD COLUMN canonical_node_id UUID NULL REFERENCES skill_nodes(id)`. Backfill deferred.

### Q4 — Sonnet verifier role

| Option | Description | Selected |
|--------|-------------|----------|
| Semantic validity check only — fires when fuzzy score < 70 | Fuzzy handles similarity; verifier only checks "is this a real guitar skill under one of the 6 roots" | ✓ |
| Always run verifier on every new proposal | Redundant when fuzzy match found something; extra cost per onboarding | |
| Skip verifier for POC — fuzzy-only | Zero LLM calls in verification path; can't catch nonsense proposals | |

**User's choice:** Verifier fires only when fuzzy < 70.
**Notes:** Locked as D-10 (+ related D-13 D-14). Structured output `{verdict, root, reason}`. `yes` → insert. `no` → drop + log to skill_node_rejections. `uncertain` → curator queue.

---

## Claude's Discretion

- **Decay scheduler + audit (SKILL-05).** User skipped this area — delegated to Claude. Recommended: APScheduler in-process, nightly at 03:00 UTC (matches Railway single-worker posture; fits Hernan's EST/PST practice hours). Audit via dedicated `decay_runs` table (row per run: started_at, finished_at, nodes_affected, error?) plus per-node `last_decayed_at` for forensics. See CONTEXT.md `### Claude's Discretion` block.
- **429 response body shape** — Fletcher-voiced copy; `resets_at` ISO datetime; client re-derives "come back in {N} days" each render.
- **`governor_calls` table schema** — see CONTEXT.md for column list; index on `(user_id, feature, created_at DESC)` for the cap-check query.
- **Admin token generation** — `openssl rand -hex 32` → `FLETCHER_ADMIN_TOKEN`, documented in RUNBOOK.md.

## Deferred Ideas

- User-side "propose a new skill" UI (Phase 5+ Library-tab or later)
- Cross-user canonical-graph backfill (future phase, non-urgent for solo POC)
- Embeddings-based dedup escape hatch (swap `dedupe_score()` under the same signature)
- Curator queue notifications (email/SMS/push)
- Per-user dollar-cost dashboard (observability phase, not here)
- Multi-worker/HA decay locking (only needed if uvicorn --workers > 1)
- Sonnet verifier retry-with-different-root
