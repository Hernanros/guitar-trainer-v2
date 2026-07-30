# Phase 4: Cost Governor & Node Verification - Context

**Gathered:** 2026-07-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Stand up a single-module server-side cost governor that intercepts every Anthropic API call, enforces per-user weekly caps on the breakdown feature, surfaces remaining quota to the mobile client, wires the Anthropic Console-level $20/month hard cap, and adds a skill-node dedup + Sonnet verifier + curator queue flow so new skill_nodes proposed by onboarding-Sonnet pass a quality gate before joining the canonical graph. Nightly 5% decay on nodes untouched > 7 days runs on the server.

**Ships:** COST-01, COST-02, COST-03, COST-04, SKILL-04, SKILL-05.

**Does NOT ship:** Payment/paywall (there is no paid tier), multi-user quota policies (single-user POC), Anthropic Admin API automation, cross-user canonical-graph backfill (existing per-user skill_nodes stay per-user), user-side "propose a new skill" UI (only onboarding-Sonnet-emitted proposals go through the verifier), observability dashboards (Railway logs only), Library-tab / Toolkit / metronome / tuner (Phase 5).

</domain>

<decisions>
## Implementation Decisions

### Governor cap + failure semantics
- **D-01: Rolling 7-day window for the breakdown cap.** Cap arithmetic: `COUNT(governor_calls WHERE user_id = X AND feature = 'breakdown' AND created_at > now() - interval '7 days') >= 3`. No timezone plumbing on the server, no Sunday-night cliff, and someone who breaks down 3 songs in one day recovers one call at a time as older calls age out. UX copy that says "3 left this week" is approximate — it means "since 7 days ago" — accepted as adequate for the domain.
- **D-02: Hard block + Fletcher-voice error card on the 4th attempt.** Server returns HTTP 429 with structured body `{code: 'BREAKDOWN_CAPPED', message: "Not my tempo. You've had 3 breakdowns this week. Come back in {N} days.", resets_at: '<iso timestamp>'}`. Mobile detects `code === 'BREAKDOWN_CAPPED'` and shows the BreakdownErrorCard with Fletcher copy (no retry button). Preserves cost integrity and Fletcher's authority (matches the "reinforcing but demanding" personality in `.planning/design/fletcher-identity.md`).
- **D-03: Anthropic `count_tokens` API for pre-dispatch estimation.** Governor calls `client.messages.count_tokens(model=..., messages=[...])` immediately before every real dispatch, records the estimate, then records real usage from `response.usage` post-dispatch. Adds ~50–100 ms latency per call — acceptable at both call sites (onboarding is already ~2 s user-facing; breakdown is already 10–30 s). Enables prompt-regression detection (if estimate doubles between deploys, we'll see it in logs).
- **D-04: `@governed(feature, cap, window)` decorator per `run_*` function.** New module `server/app/ai/governor.py` exposes the decorator. Applied at `run_technique_breakdown` (feature='breakdown', cap=3, window='7d') and `run_onboarding_parse` (feature='onboarding', cap=None — uncapped for POC because onboarding runs once per user lifetime). Feature name and cap live at the decoration site — extensible: adding a new AI call adds one line. Decorator handles: pre-cap-check → BudgetExceededError on hit → count_tokens → dispatch → record real usage.

### Quota UI + Console cap wiring
- **D-05: Inline chip on the breakdown CTA (SongOfDayCard).** Chip renders below the "See the breakdown" button with copy `"{N} left this week"` when `remaining >= 1`, and the CTA is disabled with copy `"Come back in {N} days"` when `remaining === 0`. No Settings row this phase (Settings is Phase 2 territory and the chip covers the "user always knows what's left" requirement contextually). Zero-noise placement: user sees it precisely when spending the call.
- **D-06: `breakdown_quota` field embedded in `TodaySongResponse`.** Extend the existing response with `breakdown_quota: {remaining: int, cap: int, resets_at: string}`. Zero extra requests — Today tab already fetches this on load. Chip renders directly from `today.breakdown_quota.remaining`. Server-authoritative — no MMKV cache of quota state needed.
- **D-07: Manual Anthropic Console cap + RUNBOOK entry.** Console $20/mo hard cap is configured once by the developer via `anthropic.com/console → Settings → Billing`. Alert thresholds 50%/80%/100% wired to `hernan.rosenblum89@gmail.com`. Steps documented in a new `.planning/RUNBOOK.md`. App startup emits an INFO log line reminding the reader to verify the cap is set (no automated verification — Admin API is beta and adds a credential surface for zero benefit on a solo POC).
- **D-08: Structured HTTP 503 `FLETCHER_OUT` on Anthropic-side quota hits.** Governor catches Anthropic's 429/quota-exceeded errors, logs at `logger.critical` (surfaces in Railway logs, and later Sentry if wired), returns HTTP 503 with body `{code: 'FLETCHER_OUT', message: "Fletcher's on a break. Try again in an hour.", retry_after_hint: '1h'}`. Mobile detects `code === 'FLETCHER_OUT'` and shows a distinct BreakdownErrorCard so the developer can tell from the app that this is an org-level cap-hit, not a per-user cap-hit.

### Skill-node verification pipeline
- **D-09: `rapidfuzz.token_set_ratio` for dedup — no external embeddings vendor.** Skill names are short (2–6 words) and the taxonomy is anchored to 6 fixed roots. Fuzzy match on normalized (lowercased, punctuation-stripped, token-sorted) strings. Thresholds: **score ≥ 85 → auto-dedupe** (reuse existing canonical), **70 ≤ score < 85 → curator queue** (uncertain similarity), **score < 70 → treat as brand-new proposal → run Sonnet verifier**. Adds `rapidfuzz` to `server/requirements.txt`. Zero API cost, sub-ms latency, zero new secrets. Escape hatch: if the taxonomy grows and fuzzy loses precision, swap `dedupe_score()` for a Voyage/OpenAI embedding call behind the same signature — the rest of the pipeline is provider-agnostic.
- **D-10: Sonnet verifier runs only when fuzzy score < 70 (i.e., on wholly new proposals).** New function `run_skill_node_verify(proposed_name, existing_canonical_names, fixed_roots)` at `server/app/ai/skill_verifier.py`. Structured output: `{verdict: 'yes' | 'no' | 'uncertain', root: PrimarySkillRoot | None, reason: str}`. `verdict === 'yes'` → insert as new canonical under `root`. `verdict === 'no'` → drop the proposal (log with reason). `verdict === 'uncertain'` → curator queue. Also wrapped in `@governed(feature='skill_verify', cap=None)` — uncapped because it fires only on new-node proposals which are naturally rare after onboarding stabilizes.
- **D-11: Curator queue = plain FastAPI `/admin/curator` HTML page behind `X-Admin-Token` header check.** New endpoints: `GET /admin/curator` (server-rendered HTML listing pending proposals with score + reason), `POST /admin/curator/action` with body `{proposal_id, action: 'approve' | 'reject' | 'merge_with', canonical_id?: UUID}`. Auth: single env var `FLETCHER_ADMIN_TOKEN` checked against `X-Admin-Token` header. No mobile-app work. Curator (Hernan) bookmarks the URL and a browser extension injects the header. Rejected-tab exposes retroactive review.
- **D-12: Add nullable `canonical_node_id UUID` FK on `skill_nodes` in Alembic 0004; new proposals only.** Schema change: `ALTER TABLE skill_nodes ADD COLUMN canonical_node_id UUID NULL REFERENCES skill_nodes(id)`; `CREATE INDEX ix_skill_nodes_canonical ON skill_nodes(canonical_node_id)`. Existing rows stay `canonical_node_id = NULL` — they remain per-user (Phase 2's shipping behavior). New proposals from the verifier flow set `canonical_node_id` at insert time (either the matched canonical's id, or `self.id` for a newly-verified canonical). Non-breaking: Phase 2 selector queries still work; Phase 3 selector CTE (`select_today_song`) doesn't join on canonical, so it stays working too. Backfill of existing rows is explicitly deferred.
- **D-13: Proposal entry point for POC = onboarding-Sonnet-emitted nodes only.** The verifier pipeline hooks into `run_onboarding_parse` — after Sonnet emits its proposed skill graph, each node runs through `dedupe_score → verifier` before insertion. No user-side "propose a new skill" UI this phase (Phase 5+ if ever). This means the curator queue will grow slowly (~0 items for the single POC user once their initial graph stabilizes). That's fine — the mechanism must exist for correctness; queue volume is not the success metric.
- **D-14: Rejected proposals log + drop gracefully; onboarding still succeeds.** When Sonnet verifier returns `'no'` for a node, drop it silently (do not insert), write a `skill_node_rejections` row with `{proposed_name, reason, verifier_response, created_at}`. Onboarding parse still succeeds as a whole (the fail-open bootstrap graph from Phase 2 D-07 is not triggered — a single rejected node is not a failure of the whole parse). Rejected rows visible via the `/admin/curator?tab=rejected` page.

### Claude's Discretion
- **Decay scheduler (SKILL-05)** — user did not select this area for discussion, delegated to Claude's judgment. **Recommendation: APScheduler in-process, nightly at 03:00 UTC.** Fits Railway's `uvicorn --workers 1` single-worker posture (no need for multi-worker coordination locking). Adds `apscheduler` to `server/requirements.txt`. New module `server/app/scheduler.py` registers a `decay_all_nodes()` job on app startup. Job semantics: `UPDATE skill_nodes SET mastery = mastery * 0.95 WHERE updated_at < now() - interval '7 days' AND mastery > 0`. **Audit surface: dedicated `decay_runs` table with one row per run** (`{id, started_at, finished_at, nodes_affected, error?}`) plus a `last_decayed_at` timestamp column on `skill_nodes` for per-node forensics. The dedicated table gives you fast "was decay running this week?" answers without scanning all skill_nodes; the per-node column gives you "when did this specific node last decay?" for debugging.
- **429 response body shape** — copy is Fletcher-voiced; server passes `resets_at` as ISO. Mobile derives the "come back in {N} days" copy client-side from `Date.parse(resets_at) - Date.now()` so the message stays live if the user leaves the app open.
- **`governor_calls` table schema** — `{id UUID PK, user_id UUID FK, feature TEXT, model TEXT, prompt_tokens_estimated INT, prompt_tokens_actual INT NULL, output_tokens_actual INT NULL, dollars_estimated NUMERIC NULL, dollars_actual NUMERIC NULL, error_code TEXT NULL, created_at TIMESTAMPTZ}` with index on `(user_id, feature, created_at DESC)` for the cap-check query. `prompt_tokens_actual/output_tokens_actual` populate post-dispatch; NULL means the call errored before returning.
- **Admin token generation** — `openssl rand -hex 32` → `FLETCHER_ADMIN_TOKEN` in Railway env. Documented in RUNBOOK.md alongside the Console cap setup.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level requirements + design
- `.planning/PROJECT.md` — Constraints ($20/mo hard cap, deterministic writes principle, single-user POC with multi-user seams), Key Decisions (Sonnet 4.6 for teaching content, Haiku 4.5 for routing which stays unused this phase, Opus 4.7 fallback)
- `.planning/REQUIREMENTS.md` — COST-01…COST-04, SKILL-04, SKILL-05 exact texts
- `.planning/ROADMAP.md` §Phase 4 — Goal statement + 6 success criteria + mode:mvp designation
- `.planning/design/fletcher-identity.md` — Fletcher voice vocabulary ("Not my tempo", "Rushing", "Dragging") — required for error card copy

### Prior-phase locked decisions
- `.planning/phases/02-onboarding-skill-graph/02-CONTEXT.md` — D-05 (single Sonnet batch parse on Complete — the governor's onboarding call site), D-08 (fixed root taxonomy: Rhythm/Lead/Chord Voicings/Fingerstyle/Music Theory/Timing — canonical for verifier's `root` field), D-09 (`skill_nodes` normalized per-user tree — the table 0004 alters), L104 (Phase 4 depends on schema being dedup-ready)
- `.planning/phases/03-ai-teacher-song-of-the-day/03-CONTEXT.md` — L104 (`server/app/ai/client.py::get_client()` is the shared singleton and the governor's interception surface), L124 (all LLM calls MUST live in `server/app/ai/`), L91 (Phase 4 wraps at the module boundary — this is the direct instruction)

### Live source (Phase 4 modifies or reads)
- `server/app/ai/client.py` — Shared `AsyncAnthropic` singleton; the module boundary being wrapped. **Do not modify** (contract is stable); the decorator lives adjacent, not inside.
- `server/app/ai/onboarding.py` — `run_onboarding_parse` — apply `@governed(feature='onboarding', cap=None)` here. Also hook the verifier pipeline: after `SonnetOnboardingOutput` is parsed, feed each proposed skill_node through `dedupe_score → verifier` before insert.
- `server/app/ai/breakdown.py` — `run_technique_breakdown` — apply `@governed(feature='breakdown', cap=3, window='7d')` here.
- `server/app/api/v1/song_of_day.py` — Extend `TodaySongResponse` construction with `breakdown_quota` (query `governor_calls` for user's `breakdown` feature count in last 7 days).
- `server/app/models/db.py` — Add `canonical_node_id` to `SkillNode`; add `last_decayed_at` to `SkillNode`; add new `GovernorCall`, `SkillNodeProposal`, `SkillNodeRejection`, `DecayRun` ORM classes matching the migration.
- `server/app/main.py` — Register admin router + APScheduler startup hook.
- `server/alembic/versions/` — New `0004_governor_and_node_verification.py` migration.
- `server/requirements.txt` — Add `rapidfuzz`, `apscheduler`.
- `mobile/src/api/todaySong.ts` — Add `breakdown_quota` to `TodaySongResponse` type; expose via `useTodaySong`.
- `mobile/src/components/SongOfDayCard.tsx` — Add inline chip below "See the breakdown" CTA; disable CTA when `remaining === 0`.
- `mobile/src/components/BreakdownErrorCard.tsx` (existing from Phase 3) — Extend to handle two new error codes: `BREAKDOWN_CAPPED` (429) and `FLETCHER_OUT` (503).

### External docs (referenced during discussion)
- Anthropic `count_tokens` API — https://docs.anthropic.com/en/api/messages-count-tokens (contract for D-03 pre-dispatch estimation)
- `rapidfuzz` docs — `fuzz.token_set_ratio` semantics (D-09 threshold calibration)
- APScheduler docs — `AsyncIOScheduler` + `add_job(trigger='cron')` (D-Claude's-discretion decay)
- Anthropic Admin API — https://docs.anthropic.com/en/api/organization-usage (mentioned in RUNBOOK for cap verification, NOT called by app)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `server/app/ai/client.py::get_client()` — the singleton the governor sits behind. Do not instantiate a second `AsyncAnthropic`.
- `server/app/ai/onboarding.py::AIParseError` + SAVEPOINT-guarded caller pattern — mirror this for `AISkillVerifierError` if verifier fails after retry.
- `server/app/api/deps.py::get_user_id` — the X-User-ID dep. Add a sibling `get_admin_token` dep for `/admin/curator` (checks `X-Admin-Token == env['FLETCHER_ADMIN_TOKEN']`).
- `mobile/src/components/BreakdownErrorCard.tsx` — Phase 3 component already renders a Fletcher-voice error state. Extend the `code` prop switch instead of building a new component.
- Alembic pattern in `0003_...py` — atomic column-add + index-add + preDeploy hook. Copy for 0004.
- `mobile/src/api/todaySong.ts::apiFetch` — existing HTTP wrapper. The chip's data flow is purely from `TodaySongResponse` — no new query hook needed.

### Established Patterns
- **Single AI module boundary** (Phase 3 D-124): every LLM call lives in `server/app/ai/`. Phase 4 preserves this — governor and verifier both go in `server/app/ai/`, not in `api/v1/`.
- **Structured Sonnet output via Pydantic** (Phase 2 D-06, Phase 3 breakdown): verifier follows the same pattern — Pydantic `SkillNodeVerifyOutput` model, Anthropic tool-use with tool_choice='required' or JSON schema.
- **Fletcher voice for user-facing errors** (Phase 3 breakdown 503): both cap-hit copy variants (BREAKDOWN_CAPPED, FLETCHER_OUT) MUST use Fletcher vocabulary. No generic "Rate limit exceeded" strings.
- **X-Timezone-Offset header** (Phase 3) — NOT needed by Phase 4 governor (rolling 7-day is tz-agnostic), but decay scheduler chooses UTC to avoid a per-user tz decision.
- **Alembic preDeploy on Railway** (`server/railway.toml`) — 0004 runs automatically on next deploy; no manual step required at deploy time.
- **`uvicorn --workers 1`** — APScheduler in-process is safe. If we ever move to multi-worker, decay job needs a distributed lock (postgres advisory lock or move to Railway cron plugin).

### Integration Points
- **Onboarding parse flow** — the verifier pipeline hooks between Sonnet response and DB insert. Existing `run_onboarding_parse` flow: `text → Sonnet → SonnetOnboardingOutput → insert songs + skill_nodes`. New flow: `text → Sonnet → SonnetOnboardingOutput → dedupe_score(each skill_node) → verifier(if score<70) → insert (with canonical_node_id set)`. All within the existing SAVEPOINT so verifier failures don't leave partial state.
- **Song of Day fetch** — server side: `song_of_day.py::get_song_of_day` now also computes `breakdown_quota` (one extra COUNT query per fetch — cheap with the index on `governor_calls(user_id, feature, created_at DESC)`).
- **Governor decorator wrap** — applied at `server/app/ai/{onboarding,breakdown,skill_verifier}.py` function definitions. Zero call-site changes at `api/v1/*` routes.

</code_context>

<specifics>
## Specific Ideas

- Fletcher voice for cap-hit copy: "Not my tempo." leads (per `.planning/design/fletcher-identity.md`). Full string: `"Not my tempo. You've had 3 breakdowns this week. Come back in {N} days."` — {N} computed server-side from `resets_at - now()` and re-derived client-side each render so it stays live.
- Fletcher voice for org-level FLETCHER_OUT: `"Fletcher's on a break. Try again in an hour."` — different first-line ("Fletcher's on a break" vs "Not my tempo") gives the developer a from-the-app tell that this is org-level, not user-level.
- `X-Admin-Token` header value: single 64-hex-char token from `openssl rand -hex 32`, stored in Railway env `FLETCHER_ADMIN_TOKEN`, added to `.env.example` (server) as `FLETCHER_ADMIN_TOKEN=<generate-with-openssl-rand-hex-32>`.
- Decay run time: 03:00 UTC nightly. Avoids US-daytime user activity (Hernan is EST/PST, so 03:00 UTC = 10–11 PM local — post-practice, pre-sleep, low read load).

</specifics>

<deferred>
## Deferred Ideas

- **User-side "propose a new skill" UI** — a mobile flow where the user types a skill they don't see in their tree. Belongs in Phase 5 (Library-tab) or later, when we have a Library-tab surface to attach it to. For now, verifier only sees onboarding-Sonnet-emitted proposals.
- **Cross-user canonical-graph backfill** — Phase 4 introduces `canonical_node_id` as a nullable FK that stays NULL for pre-existing rows. A future phase can backfill by running the verifier over all existing rows and reconciling near-duplicates. Not urgent for single-user POC.
- **Embeddings-based dedup escape hatch** — swap `rapidfuzz.token_set_ratio` for a Voyage/OpenAI embedding call behind the same `dedupe_score(name_a, name_b) → int` signature. Not needed now; noted so a future phase can flip it without changing pipeline shape.
- **Curator queue notifications** — email/SMS/push when queue depth exceeds a threshold. Not needed for solo POC (Hernan checks the URL manually). Belongs in a future multi-user phase.
- **Dollar-cost accounting per user** — governor logs `dollars_actual` per call. A future dashboard summing per-user monthly cost belongs in an observability phase, not here.
- **Multi-worker/HA decay locking** — postgres advisory lock or move decay to a Railway cron plugin service. Only needed if we ever bump `--workers` above 1.
- **Sonnet verifier retry-with-different-root** — currently: verifier picks a root and either accepts or rejects. If it rejects but the proposed name might fit under a different root, we don't retry. Could iterate. Not needed at POC scale.

</deferred>

---

*Phase: 04-cost-governor-node-verification*
*Context gathered: 2026-07-30*
