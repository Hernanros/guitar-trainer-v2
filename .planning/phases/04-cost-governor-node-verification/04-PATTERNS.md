# Phase 4: Cost Governor & Node Verification - Pattern Map

**Mapped:** 2026-07-30
**Files analyzed:** 15 (8 new, 7 modified)
**Analogs found:** 14 / 15 (admin HTML page has no prior analog)

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `server/app/ai/governor.py` | middleware/utility | request-response | `server/app/ai/client.py` (adjacent singleton) + `server/app/ai/onboarding.py` (error pattern) | role-match |
| `server/app/ai/skill_verifier.py` | service | request-response | `server/app/ai/breakdown.py` | exact |
| `server/app/ai/skill_dedupe.py` | utility | transform | `server/app/ai/onboarding.py::FIXED_ROOTS` (taxonomy constant pattern) | partial-match |
| `server/app/api/v1/admin.py` | controller | request-response | `server/app/api/v1/song_of_day.py` (router pattern) | role-match |
| `server/app/scheduler.py` | utility | event-driven | `server/app/main.py::on_startup` (startup hook pattern) | partial-match |
| `server/alembic/versions/0004_governor_and_node_verification.py` | migration | batch | `server/alembic/versions/0003_song_catalog_user_sessions_breakdown_generated_at.py` | exact |
| `server/app/ai/onboarding.py` *(modify)* | service | request-response | self (existing file) | exact |
| `server/app/ai/breakdown.py` *(modify)* | service | request-response | self (existing file) | exact |
| `server/app/api/v1/song_of_day.py` *(modify)* | controller | request-response | self (existing file) | exact |
| `server/app/models/db.py` *(modify)* | model | CRUD | self (existing file) + `UserSession` ORM class pattern | exact |
| `server/app/main.py` *(modify)* | config | event-driven | self (existing file) | exact |
| `server/requirements.txt` *(modify)* | config | — | self (existing file) | exact |
| `mobile/src/api/todaySong.ts` *(modify)* | service | request-response | self (existing file) | exact |
| `mobile/src/components/SongOfDayCard.tsx` *(modify)* | component | request-response | self (existing file) + `BreakdownErrorCard.tsx` (disabled-state pattern) | exact |
| `mobile/src/components/BreakdownErrorCard.tsx` *(modify)* | component | request-response | self (existing file) | exact |

---

## Pattern Assignments

### `server/app/ai/governor.py` (new — middleware/utility, request-response)

**Analogs:** `server/app/ai/client.py` (module structure, singleton avoidance) + `server/app/ai/onboarding.py` (error class, get_client() call pattern)

**Module header + imports pattern** (`server/app/ai/client.py` lines 1-16, `server/app/ai/onboarding.py` lines 1-16):
```python
"""Cost governor — @governed decorator that wraps every Sonnet call.

Sits adjacent to client.py (not inside it). Do not instantiate a second
AsyncAnthropic — always call get_client() from app.ai.client.
Phase 4 interception surface: pre-cap-check → count_tokens → dispatch → record usage.
"""
import logging
from functools import wraps

from anthropic import APIError

from app.ai.client import get_client, SONNET_MODEL

logger = logging.getLogger(__name__)
```

**Error class pattern** (`server/app/ai/onboarding.py` lines 19-26):
```python
class AIParseError(Exception):
    """Raised when Sonnet call fails after retry, or when structured output validation fails.

    Caught by bootstrap_user to trigger the D-07 SAVEPOINT-based fail-open path.
    The parent transaction (user row + preferences) remains alive when this is caught
    because it is raised after a SAVEPOINT rollback cleans up any partial skill_nodes/songs.
    """
```
Mirror for governor:
```python
class BudgetExceededError(Exception):
    """Raised by @governed when the per-user weekly cap is hit.

    feature='breakdown': cap=3 per 7-day rolling window (D-01/D-02).
    Caught by the breakdown endpoint to return HTTP 429 with BREAKDOWN_CAPPED body.
    """
    def __init__(self, feature: str, resets_at: str) -> None:
        self.feature = feature
        self.resets_at = resets_at
        super().__init__(f"Budget exceeded for feature={feature}. Resets at {resets_at}.")
```

**get_client() call-site pattern** (`server/app/ai/onboarding.py` lines 84-90):
```python
async def _call(timeout: float) -> SonnetOnboardingOutput:
    # get_client() may raise RuntimeError if ANTHROPIC_API_KEY is unset.
    # Calling it inside _call (which is inside the outer try/except below)
    # means the RuntimeError gets wrapped as AIParseError and triggers the
    # D-07 fail-open path — instead of a raw 500 to the client.
    client = get_client()
    resp = await asyncio.wait_for(
        client.messages.create(...),
        timeout=timeout,
    )
```
Governor MUST call `get_client()` inside the wrapped async call, not at decoration time, for the same reason.

**count_tokens call pattern** (D-03 — no existing analog; use same client):
```python
# Pre-dispatch token estimate (D-03). Same client instance, no second AsyncAnthropic.
client = get_client()
token_estimate = await client.messages.count_tokens(
    model=model,
    messages=messages,
)
```

**Decorator + DB insert pattern** (D-04 shape — no prior analog; extrapolate from onboarding error wrapping):
```python
def governed(feature: str, cap: int | None = None, window: str = "7d"):
    """Decorator factory. Applied at run_* function definition sites.

    Usage:
        @governed(feature='breakdown', cap=3, window='7d')
        async def run_technique_breakdown(..., db: AsyncSession) -> Breakdown:
            ...
    """
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, db: AsyncSession, **kwargs):
            user_id = kwargs.get("user_id") or _extract_user_id(args)
            if cap is not None:
                await _check_cap(db, user_id, feature, cap)   # raises BudgetExceededError
            call_id = await _insert_governor_call(db, user_id, feature, ...)
            try:
                result = await fn(*args, db=db, **kwargs)
                await _update_governor_call(db, call_id, resp.usage)
                return result
            except Exception as e:
                await _mark_governor_call_error(db, call_id, type(e).__name__)
                raise
        return wrapper
    return decorator
```

---

### `server/app/ai/skill_verifier.py` (new — service, request-response)

**Analog:** `server/app/ai/breakdown.py` — exact structural mirror

**Imports pattern** (`server/app/ai/breakdown.py` lines 1-18):
```python
"""Skill node verifier — single Sonnet call that validates a proposed skill node
against the canonical taxonomy and existing nodes.

Phase 4 interception point: wrapped in @governed(feature='skill_verify', cap=None).
Only called when rapidfuzz score < 70 (new proposal, not a near-duplicate).
"""
import asyncio
import logging
from typing import Any

from anthropic import APIError, APITimeoutError

from app.ai.client import SONNET_MODEL, get_client

logger = logging.getLogger(__name__)
```

**Error class pattern** (`server/app/ai/breakdown.py` lines 21-28):
```python
class AIBreakdownError(Exception):
    """Raised when the Sonnet breakdown call fails after retry, or when structured
    output validation fails.

    Caught by the breakdowns endpoint to return HTTP 503 with Fletcher-voiced detail.
    breakdown_generated_at is NOT set when this error is raised — next tap retries.
    """
```
Mirror:
```python
class AISkillVerifierError(Exception):
    """Raised when the Sonnet skill-verify call fails after retry.

    Caught by the verifier pipeline in run_onboarding_parse to trigger graceful drop
    of the proposal (D-14: onboarding still succeeds; proposal is queued as 'uncertain').
    """
```

**Tool definition pattern** (`server/app/ai/breakdown.py` lines 59-74):
```python
_TOOL_NAME = "emit_breakdown"
_TOOL_DEF: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "Emit a full technique breakdown for the requested song. ..."
    ),
    "input_schema": Breakdown.model_json_schema(),
}
```
Mirror for verifier:
```python
_TOOL_NAME = "emit_skill_verify"
_TOOL_DEF: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": "Emit a verdict on whether the proposed skill node belongs in the canonical graph.",
    "input_schema": SkillNodeVerifyOutput.model_json_schema(),
}
```

**Core Sonnet call + retry pattern** (`server/app/ai/breakdown.py` lines 111-182 — the primary template):
```python
async def run_technique_breakdown(
    song_title: str,
    song_artist: str,
    target_skill_names: list[str],
    user_level: float,
    *,
    timeout_seconds: float = 30.0,
) -> Breakdown:
    user_content = _format_user_message(...)

    async def _call(timeout: float) -> Breakdown:
        client = get_client()                                      # inside _call — hotfix pattern
        resp = await asyncio.wait_for(
            client.messages.create(
                model=SONNET_MODEL,
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                tools=[_TOOL_DEF],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
            ),
            timeout=timeout,
        )
        tool_use = next(
            (b for b in resp.content if getattr(b, "type", None) == "tool_use"), None
        )
        if tool_use is None:
            raise RuntimeError("Sonnet did not emit a tool_use block despite forced tool_choice.")
        return Breakdown.model_validate(tool_use.input)

    try:
        try:
            return await _call(timeout_seconds)
        except (APITimeoutError, asyncio.TimeoutError, APIError) as e:
            logger.warning(
                "Sonnet breakdown call failed once (%s). Retrying with widened timeout.",
                type(e).__name__,
            )
            return await _call(timeout_seconds * 2)
    except Exception as e:
        raise AIBreakdownError(
            f"Sonnet breakdown failed: {type(e).__name__}: {e}"
        ) from e
```
`run_skill_node_verify` copies this structure verbatim, replacing `Breakdown` with `SkillNodeVerifyOutput`, error class with `AISkillVerifierError`, and max_tokens=1024 (verdicts are short).

**Pydantic output model** (new — no prior analog; extend `server/app/models/skill_node.py` or define inline):
```python
from typing import Literal
from pydantic import BaseModel

class SkillNodeVerifyOutput(BaseModel):
    """Structured output from run_skill_node_verify."""
    verdict: Literal["yes", "no", "uncertain"]
    root: Literal["Rhythm", "Lead", "Chord Voicings", "Fingerstyle", "Music Theory", "Timing"] | None
    reason: str
```

---

### `server/app/ai/skill_dedupe.py` (new — utility, transform)

**Analog:** `server/app/ai/onboarding.py::FIXED_ROOTS` (taxonomy constant) — partial match; no prior fuzzy-match utility in codebase.

**Module structure pattern** (`server/app/ai/onboarding.py` lines 28-29):
```python
# Fixed root taxonomy per D-08. Sonnet MUST use exactly these names verbatim.
FIXED_ROOTS = ["Rhythm", "Lead", "Chord Voicings", "Fingerstyle", "Music Theory", "Timing"]
```
Reuse `FIXED_ROOTS` import from `onboarding.py` rather than redefining. If it moves, import from `app.ai.onboarding`.

**Threshold constants + normalize pattern** (D-09 — no prior analog; define at module top):
```python
from rapidfuzz import fuzz

# D-09 thresholds (token_set_ratio scale 0–100)
SCORE_AUTO_DEDUPE = 85   # >= 85: reuse existing canonical
SCORE_CURATOR_QUEUE = 70  # 70–84: uncertain → curator queue
# < 70: new proposal → run Sonnet verifier

def normalize(name: str) -> str:
    """Lowercase + strip punctuation for token_set_ratio input."""
    import re
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()

def dedupe_score(proposed: str, existing: str) -> int:
    """Return rapidfuzz.fuzz.token_set_ratio score (0–100)."""
    return fuzz.token_set_ratio(normalize(proposed), normalize(existing))
```

---

### `server/app/api/v1/admin.py` (new — controller, request-response)

**Analog:** `server/app/api/v1/song_of_day.py` — router pattern with Depends injection

**Imports + router pattern** (`server/app/api/v1/song_of_day.py` lines 15-28):
```python
from uuid import UUID

import sqlalchemy.exc
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tz_offset_minutes, get_user_id
from app.db.session import get_db
from app.models.db import Song
from app.models.song import SongResponse, TodayRatingInfo, TodaySongResponse
from app.selectors.today_song import select_today_song

router = APIRouter()
```
Mirror for admin:
```python
import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_token   # new dep (see Shared Patterns)
from app.db.session import get_db

router = APIRouter()
```

**Dependency injection pattern** (`server/app/api/v1/song_of_day.py` lines 31-36):
```python
@router.get("/song-of-day", response_model=TodaySongResponse)
async def get_song_of_day(
    user_id: UUID = Depends(get_user_id),
    tz_offset_minutes: int = Depends(get_tz_offset_minutes),
    db: AsyncSession = Depends(get_db),
) -> TodaySongResponse:
```
Mirror for admin:
```python
@router.get("/admin/curator", response_class=HTMLResponse)
async def curator_queue(
    _token: None = Depends(get_admin_token),   # auth gate; returns None on success
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
```

**HTTP 409 / IntegrityError handling pattern** (`server/app/api/v1/song_of_day.py` lines 200-206):
```python
    except sqlalchemy.exc.IntegrityError:
        # Race condition: concurrent second POST hit the partial-unique index first
        await db.rollback()
        raise HTTPException(status_code=409, detail="Already used today's reroll.")
```
Admin POST /action uses HTTPException for invalid action values; no IntegrityError expected but follow the same try/except shape.

**No analog for HTML response body** — use Python f-string templating (`return HTMLResponse(content=f"<html>...")`) since the curator page is a minimal internal tool, not a polished UI. No Jinja2 dependency needed at POC scale.

---

### `server/app/scheduler.py` (new — utility, event-driven)

**Analog:** `server/app/main.py` startup event hook — partial match (same event-driven lifecycle)

**Startup hook pattern** (`server/app/main.py` lines 49-55):
```python
@app.on_event("startup")
async def on_startup() -> None:
    """Seed the songs table on startup if it is empty."""
    logger.info("Startup: seeding songs table if empty.")
    async with AsyncSessionLocal() as db:
        await seed_songs(db)
    logger.info("Startup: seed check complete.")
```

**APScheduler pattern** (no prior analog; D-Claude-discretion recommendation):
```python
"""APScheduler in-process — nightly 5% mastery decay job.

Single-worker safe (uvicorn --workers 1 per server/railway.toml).
If workers ever increase, replace with postgres advisory lock or Railway cron plugin.
"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None

def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler

async def decay_all_nodes() -> None:
    """Nightly 5% decay on nodes untouched > 7 days (SKILL-05)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text(
                "UPDATE skill_nodes "
                "SET mastery = GREATEST(mastery * 0.95, 0), "
                "    last_decayed_at = now() "
                "WHERE updated_at < now() - interval '7 days' AND mastery > 0 "
                "RETURNING id"
            )
        )
        ...
```

**Registration in main.py** mirrors the `on_startup` pattern:
```python
@app.on_event("startup")
async def on_startup() -> None:
    # ... existing seed_songs call ...
    scheduler = get_scheduler()
    scheduler.add_job(decay_all_nodes, trigger="cron", hour=3, minute=0)
    scheduler.start()
    logger.info("Startup: APScheduler started. decay_all_nodes scheduled at 03:00 UTC.")
```

---

### `server/alembic/versions/0004_governor_and_node_verification.py` (new — migration, batch)

**Analog:** `server/alembic/versions/0003_song_catalog_user_sessions_breakdown_generated_at.py` — exact structural mirror

**Module header pattern** (`0003` lines 1-27):
```python
"""Song catalog, user sessions, breakdown_generated_at column

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-29

Ordered steps:
1. Create primary_skill_root enum ...
...

downgrade() reverses in strict reverse order.
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None
```
Mirror:
```python
"""Governor calls, skill_node_proposals, skill_node_rejections, decay_runs;
canonical_node_id + last_decayed_at on skill_nodes.

Revision ID: 0004
Revises: 0003
...
"""
revision = "0004"
down_revision = "0003"
```

**Raw SQL enum creation** (`0003` lines 36-48):
```python
op.execute(
    "CREATE TYPE primary_skill_root AS ENUM "
    "('rhythm', 'lead', 'chord_voicings', 'fingerstyle', 'music_theory', 'timing')"
)
```
0004 adds no new enum types — governor_calls.feature and proposal.status are plain TEXT columns to avoid ALTER TYPE complexity.

**op.add_column pattern** (`0003` lines 158-162):
```python
op.add_column(
    "songs",
    sa.Column("breakdown_generated_at", sa.DateTime(timezone=True), nullable=True),
)
```
Mirror for `canonical_node_id` and `last_decayed_at` on `skill_nodes`:
```python
op.add_column(
    "skill_nodes",
    sa.Column("canonical_node_id", sa.UUID(), nullable=True),
)
op.add_column(
    "skill_nodes",
    sa.Column("last_decayed_at", sa.DateTime(timezone=True), nullable=True),
)
op.execute(
    "ALTER TABLE skill_nodes ADD CONSTRAINT fk_skill_nodes_canonical "
    "FOREIGN KEY (canonical_node_id) REFERENCES skill_nodes(id)"
)
```

**Index creation + downgrade** (`0003` lines 136-139, 178-200):
```python
op.execute(
    "CREATE UNIQUE INDEX uq_user_sessions_daily_reroll "
    "ON user_sessions (user_id, local_calendar_day) "
    "WHERE is_reroll_marker = true"
)
# downgrade: op.execute("DROP INDEX IF EXISTS uq_user_sessions_daily_reroll")
```
Mirror for governor_calls index:
```python
op.execute(
    "CREATE INDEX ix_governor_calls_user_feature_created "
    "ON governor_calls (user_id, feature, created_at DESC)"
)
```

**Raw CREATE TABLE** (`0003` lines 55-66, 115-128):
```python
op.execute("""
    CREATE TABLE song_catalog (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title TEXT NOT NULL,
        ...
    )
""")
```
Four new tables in 0004 follow same raw-SQL-string pattern. Downgrade reverses in strict reverse order: drop indexes, drop tables, drop FK constraints, drop added columns.

---

### `server/app/ai/onboarding.py` (modify — apply @governed + verifier pipeline hook)

**Existing decorator application site** — add `@governed` just above `async def run_onboarding_parse`:
```python
@governed(feature="onboarding", cap=None)
async def run_onboarding_parse(
    raw_input: dict,
    *,
    db: AsyncSession,           # <-- governor needs db for cap-check + insert
    user_id: UUID,              # <-- governor needs user_id for row attribution
    timeout_seconds: float = 30.0,
) -> SonnetOnboardingOutput:
```
`db` and `user_id` become required kwargs (the decorator extracts them before forwarding to the underlying function).

**SAVEPOINT + verifier pipeline hook** (`server/app/ai/onboarding.py` lines 84-120 — hook after line 106):

The existing pattern for calling within SAVEPOINT (from 02-CONTEXT.md D-07 reference and 03-CONTEXT.md L125-126):
```python
async with db.begin_nested():   # SAVEPOINT
    # ... existing Sonnet call ...
    result = SonnetOnboardingOutput.model_validate(tool_use.input)
    # Phase 4 hook: run verifier pipeline on each proposed skill node
    for node in result.skill_graph.nodes:
        await _verify_and_enqueue(db, user_id, node, existing_canonical_names)
    # insertions happen here — if verifier pipeline errors, SAVEPOINT rolls back
```

---

### `server/app/ai/breakdown.py` (modify — apply @governed)

**Decorator application** — same pattern as onboarding.py modification above:
```python
@governed(feature="breakdown", cap=3, window="7d")
async def run_technique_breakdown(
    song_title: str,
    song_artist: str,
    target_skill_names: list[str],
    user_level: float,
    *,
    db: AsyncSession,     # added for governor
    user_id: UUID,        # added for governor
    timeout_seconds: float = 30.0,
) -> Breakdown:
```
The governor raises `BudgetExceededError` before the Sonnet call if cap is hit. The endpoint catches it and returns 429.

---

### `server/app/api/v1/song_of_day.py` (modify — add breakdown_quota to TodaySongResponse construction)

**Existing response construction pattern** (`song_of_day.py` lines 126-134):
```python
return TodaySongResponse(
    song=SongResponse.model_validate(row),
    breakdown_available=(row.breakdown_generated_at is not None),
    from_bank=from_bank,
    bank_source=bank_source,
    rerolled=rerolled,
    rated=rated_info,
    rerolls_left=rerolls_left,
)
```
Extend with TWO extra queries before this block — a COUNT for `remaining`
AND a MIN(created_at) for accurate `resets_at`. Do NOT take the shortcut of
`resets_at = now() + 7d` — the mobile chip + BreakdownErrorCard use this value
to render "Come back in {N} days" and the pytest asserts it matches oldest-call
+ 7d within a 1s tolerance (test_breakdown_quota_resets_at_matches_oldest_plus_7d).
```python
# Phase 4: breakdown_quota — COUNT + MIN with indexed query (D-06)
from datetime import datetime, timedelta, timezone

quota_count = await db.scalar(
    text(
        "SELECT COUNT(*) FROM governor_calls "
        "WHERE user_id = :user_id AND feature = 'breakdown' "
        "AND created_at > now() - interval '7 days'"
    ),
    {"user_id": str(user_id)},
)
oldest_call = await db.scalar(
    text(
        "SELECT MIN(created_at) FROM governor_calls "
        "WHERE user_id = :user_id AND feature = 'breakdown' "
        "AND created_at > now() - interval '7 days'"
    ),
    {"user_id": str(user_id)},
)
if oldest_call is None:
    resets_at_dt = datetime.now(timezone.utc) + timedelta(days=7)
else:
    resets_at_dt = oldest_call + timedelta(days=7)
resets_at = resets_at_dt.isoformat()

breakdown_quota = BreakdownQuota(
    remaining=max(0, 3 - (quota_count or 0)),
    cap=3,
    resets_at=resets_at,
)

return TodaySongResponse(
    ...
    breakdown_quota=breakdown_quota,  # new field
)
```

---

### `server/app/models/db.py` (modify — add columns to SkillNode + four new ORM classes)

**Existing ORM class pattern** (`db.py` lines 123-173 — `SkillNode`):
```python
class SkillNode(Base):
    __tablename__ = "skill_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    ...
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```
Add to `SkillNode`:
```python
    # Phase 4 additions
    canonical_node_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("skill_nodes.id"),
        nullable=True,
    )
    last_decayed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

**New ORM class pattern** (`db.py` lines 241-281 — `UserSession` as template):
```python
class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    ...
    is_reroll_marker: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
```
Four new ORM classes follow this exact shape:
- `GovernorCall` — `(id, user_id FK, feature TEXT, model TEXT, prompt_tokens_estimated INT, prompt_tokens_actual INT NULL, output_tokens_actual INT NULL, dollars_estimated NUMERIC NULL, dollars_actual NUMERIC NULL, error_code TEXT NULL, created_at TIMESTAMPTZ)`
- `SkillNodeProposal` — `(id, user_id FK, proposed_name TEXT, fuzzy_score INT, status TEXT, canonical_id FK NULL, verifier_verdict TEXT NULL, verifier_reason TEXT NULL, created_at TIMESTAMPTZ)`
- `SkillNodeRejection` — `(id, proposed_name TEXT, reason TEXT, verifier_response JSONB NULL, created_at TIMESTAMPTZ)`
- `DecayRun` — `(id, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ NULL, nodes_affected INT NULL, error TEXT NULL)`

---

### `server/app/main.py` (modify — register admin router + scheduler startup)

**Existing router registration pattern** (`main.py` lines 10-13, 37-40):
```python
from app.api.v1.breakdowns import router as breakdowns_router
from app.api.v1.sessions import router as sessions_router
from app.api.v1.song_of_day import router as song_router
from app.api.v1.users import router as users_router
...
app.include_router(breakdowns_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")
app.include_router(song_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
```
Add:
```python
from app.api.v1.admin import router as admin_router
from app.scheduler import get_scheduler, decay_all_nodes
...
app.include_router(admin_router, prefix="/api/v1")
```

**Existing startup hook** (`main.py` lines 49-55):
```python
@app.on_event("startup")
async def on_startup() -> None:
    """Seed the songs table on startup if it is empty."""
    logger.info("Startup: seeding songs table if empty.")
    async with AsyncSessionLocal() as db:
        await seed_songs(db)
    logger.info("Startup: seed check complete.")
```
Extend: add scheduler start after `seed_songs`:
```python
    # Phase 4: APScheduler — nightly decay job at 03:00 UTC (Claude's discretion)
    scheduler = get_scheduler()
    scheduler.add_job(decay_all_nodes, trigger="cron", hour=3, minute=0, timezone="UTC")
    scheduler.start()
    logger.info(
        "Startup: APScheduler started. decay_all_nodes scheduled nightly at 03:00 UTC. "
        "REMINDER: verify $20/mo Anthropic Console cap is set at anthropic.com/console."
    )
```

---

### `server/requirements.txt` (modify)

**Existing pattern** (`requirements.txt` lines 1-10):
```
fastapi==0.128.8
uvicorn==0.39.0
sqlalchemy==2.0.51
...
anthropic==0.117.0
```
Append pinned versions (resolve latest compatible at implementation time):
```
rapidfuzz==3.9.7
apscheduler==3.10.4
```

---

### `mobile/src/api/todaySong.ts` (modify — add breakdown_quota to TodaySongResponse type)

**Existing type import pattern** (`todaySong.ts` lines 19-21):
```typescript
// Phase 3 response type — includes song + rated + rerolled + from_bank + bank_source.
export type TodaySongResponse = components['schemas']['TodaySongResponse'];
export type SongResponse = components['schemas']['SongResponse'];
```
After `TodaySongResponse` gains `breakdown_quota` in the OpenAPI schema (via server-side Pydantic model change), regenerate types. No manual type override needed — the generated schema handles it automatically. Add a convenience re-export:
```typescript
export type BreakdownQuota = components['schemas']['BreakdownQuota'];
```

No new query hook needed — `useTodaySong` already returns `TodaySongResponse`; `SongOfDayCard` reads `today.data?.breakdown_quota` directly (D-06).

---

### `mobile/src/components/SongOfDayCard.tsx` (modify — quota chip + disabled CTA)

**Existing CTA pattern** (`SongOfDayCard.tsx` lines 62-71):
```tsx
{onSeekBreakdown && (
  <Pressable
    style={styles.ctaButton}
    onPress={onSeekBreakdown}
    accessibilityRole="button"
    accessibilityLabel="See the breakdown"
  >
    <Text style={styles.ctaText}>See the breakdown</Text>
  </Pressable>
)}
```

**Disabled state pattern to copy** (from `BreakdownErrorCard.tsx` — no button disabled example exists; use Pressable `disabled` prop with opacity):
```tsx
// Quota chip (D-05): renders below CTA only in default (unrated) variant
{breakdown_quota && breakdown_quota.remaining > 0 && (
  <Text style={styles.quotaChip}>{breakdown_quota.remaining} left this week</Text>
)}

// CTA with cap-disabled state (D-02 + D-05)
{onSeekBreakdown && (
  <Pressable
    style={[styles.ctaButton, breakdown_quota?.remaining === 0 && styles.ctaDisabled]}
    onPress={breakdown_quota?.remaining === 0 ? undefined : onSeekBreakdown}
    accessibilityRole="button"
    accessibilityLabel={
      breakdown_quota?.remaining === 0
        ? `Come back in ${daysUntilReset(breakdown_quota.resets_at)} days`
        : "See the breakdown"
    }
    disabled={breakdown_quota?.remaining === 0}
  >
    <Text style={styles.ctaText}>
      {breakdown_quota?.remaining === 0
        ? `Come back in ${daysUntilReset(breakdown_quota.resets_at)} days`
        : "See the breakdown"}
    </Text>
  </Pressable>
)}
```

**StyleSheet pattern** (`SongOfDayCard.tsx` lines 89-166):
```typescript
const styles = StyleSheet.create({
  ...
  ctaButton: {
    backgroundColor: '#E07B39',
    borderRadius: 8,
    paddingVertical: 14,
    alignItems: 'center',
    marginBottom: 10,
  },
  // Phase 4 additions:
  ctaDisabled: {
    backgroundColor: '#555',    // muted, not orange
    opacity: 0.6,
  },
  quotaChip: {
    fontSize: 12,
    color: '#999',
    textAlign: 'center',
    marginTop: 6,
  },
});
```

**Props interface extension** (`SongOfDayCard.tsx` lines 16-22):
```typescript
interface SongOfDayCardProps {
  song: SongResponse;
  ratedLabel?: string | null;
  onTitlePress?: () => void;
  onSeekBreakdown?: () => void;
  onReroll?: () => void;
  breakdown_quota?: BreakdownQuota | null;   // Phase 4 addition
}
```

---

### `mobile/src/components/BreakdownErrorCard.tsx` (modify — extend to handle two new error codes)

**Existing single-variant pattern** (`BreakdownErrorCard.tsx` lines 17-49):
```tsx
interface BreakdownErrorCardProps {
  /** Called when the user taps "Try again" — should trigger refetch(). */
  onRetry: () => void;
  /** Called when the user taps "Back to today's song" — should trigger router.back(). */
  onBack: () => void;
}

export function BreakdownErrorCard({ onRetry, onBack }: BreakdownErrorCardProps) {
  return (
    <View style={styles.card}>
      <Text style={styles.heading}>Fletcher lost the thread.</Text>
      <Text style={styles.body}>
        The connection dropped mid-thought. Give it a minute — try again.
      </Text>
      <Pressable ... onPress={onRetry}>
        <Text style={styles.ctaText}>Try again</Text>
      </Pressable>
      <Pressable style={styles.backLink} onPress={onBack}>
        <Text style={styles.backLinkText}>Back to today's song</Text>
      </Pressable>
    </View>
  );
}
```
Extend with `code` prop + switch:
```tsx
interface BreakdownErrorCardProps {
  onRetry: () => void;
  onBack: () => void;
  /** Phase 4: structured error code from server. Defaults to generic 503 behavior. */
  code?: 'BREAKDOWN_CAPPED' | 'FLETCHER_OUT' | null;
  /** Phase 4: ISO timestamp — used to compute "Come back in N days" for BREAKDOWN_CAPPED. */
  resets_at?: string | null;
}

export function BreakdownErrorCard({ onRetry, onBack, code, resets_at }: BreakdownErrorCardProps) {
  // Variant copy (D-02, D-08 — Fletcher voice from fletcher-identity.md)
  const heading =
    code === 'BREAKDOWN_CAPPED' ? "Not my tempo." :
    code === 'FLETCHER_OUT'     ? "Fletcher's on a break." :
    "Fletcher lost the thread.";

  const body =
    code === 'BREAKDOWN_CAPPED'
      ? `You've had 3 breakdowns this week. Come back in ${daysUntilReset(resets_at)} days.`
    : code === 'FLETCHER_OUT'
      ? "Try again in an hour."
    : "The connection dropped mid-thought. Give it a minute — try again.";

  // BREAKDOWN_CAPPED: no retry button (cap is not retryable). FLETCHER_OUT: show retry.
  const showRetry = code !== 'BREAKDOWN_CAPPED';

  return (
    <View style={styles.card}>
      <Text style={styles.heading}>{heading}</Text>
      <Text style={styles.body}>{body}</Text>
      {showRetry && (
        <Pressable style={styles.cta} onPress={onRetry} accessibilityRole="button">
          <Text style={styles.ctaText}>Try again</Text>
        </Pressable>
      )}
      <Pressable style={styles.backLink} onPress={onBack} accessibilityRole="link">
        <Text style={styles.backLinkText}>Back to today's song</Text>
      </Pressable>
    </View>
  );
}
```
Existing `styles` object carries over unchanged (card, heading, body, cta, ctaPressed, ctaText, backLink, backLinkText).

---

## Shared Patterns

### Dependency injection — `get_admin_token`
**Source:** `server/app/api/deps.py` — add sibling alongside `get_user_id`
**Apply to:** `server/app/api/v1/admin.py`

```python
# Pattern from deps.py lines 14-21 (get_user_id):
async def get_user_id(x_user_id: UUID = Header(..., alias="X-User-ID")) -> UUID:
    return x_user_id

# New sibling for admin auth (D-11 — single env var check).
# SECURITY: constant-time comparison via hmac.compare_digest to defeat timing attacks (T-04-03-03).
import os
from hmac import compare_digest
from fastapi import Header, HTTPException

async def get_admin_token(
    x_admin_token: str = Header(..., alias="X-Admin-Token"),
) -> None:
    """Validate X-Admin-Token against FLETCHER_ADMIN_TOKEN env var.

    Returns None on success (used as a gate dep — caller only cares about the side-effect).
    Raises HTTP 503 if env var is unset (server misconfigured).
    Raises HTTP 401 if the token is wrong.
    Comparison uses hmac.compare_digest for constant-time semantics.
    """
    expected = os.environ.get("FLETCHER_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Admin token not configured on server.")
    if not compare_digest(x_admin_token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin token.")
```

### Error class definition
**Source:** `server/app/ai/onboarding.py` lines 19-26 (`AIParseError`)
**Apply to:** `server/app/ai/governor.py` (`BudgetExceededError`), `server/app/ai/skill_verifier.py` (`AISkillVerifierError`)

Pattern: subclass `Exception`, document which caller catches it, document what transaction state is expected when it surfaces.

### Singleton module variable
**Source:** `server/app/ai/client.py` lines 28-41 (`_client: Optional[AsyncAnthropic]`)
**Apply to:** `server/app/scheduler.py` (`_scheduler: Optional[AsyncIOScheduler]`)

```python
# Pattern from client.py lines 28-41:
_client: Optional[AsyncAnthropic] = None

def get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=_get_api_key())
    return _client
```

### Raw SQL in async session
**Source:** `server/app/api/v1/song_of_day.py` lines 46-63
**Apply to:** `server/app/ai/governor.py` (cap-check query), `server/app/scheduler.py` (decay UPDATE), `server/app/api/v1/admin.py` (curator queue SELECT)

```python
# Pattern: text() + named params + .mappings().one_or_none()
result = (
    await db.execute(
        text("SELECT ... FROM ... WHERE user_id = :user_id AND ..."),
        {"user_id": str(user_id), ...},
    )
).mappings().one_or_none()
```

### Fletcher voice for HTTP error responses
**Source:** `server/app/api/v1/song_of_day.py` line 175 + 03-CONTEXT.md (error body shape)
**Apply to:** `server/app/ai/governor.py` (BudgetExceededError body), endpoint that catches it

HTTP 429 body (D-02):
```python
raise HTTPException(
    status_code=429,
    detail={
        "code": "BREAKDOWN_CAPPED",
        "message": "Not my tempo. You've had 3 breakdowns this week. Come back in {N} days.",
        "resets_at": resets_at_iso,
    },
)
```
HTTP 503 body (D-08):
```python
raise HTTPException(
    status_code=503,
    detail={
        "code": "FLETCHER_OUT",
        "message": "Fletcher's on a break. Try again in an hour.",
        "retry_after_hint": "1h",
    },
)
```

### Pydantic response model extension
**Source:** `server/app/models/song.py` lines 117-136 (`TodaySongResponse`)
**Apply to:** `TodaySongResponse` (add `breakdown_quota` field) and new `BreakdownQuota` model

```python
class BreakdownQuota(BaseModel):
    """Phase 4 quota snapshot embedded in TodaySongResponse (D-06)."""
    remaining: int
    cap: int
    resets_at: str   # ISO datetime string (server-authoritative)

    model_config = {"from_attributes": False}

class TodaySongResponse(BaseModel):
    ...
    breakdown_quota: BreakdownQuota    # Phase 4 addition — always populated
```

### `daysUntilReset` utility (mobile)
**Source:** no prior analog — new helper
**Apply to:** `mobile/src/components/SongOfDayCard.tsx`, `mobile/src/components/BreakdownErrorCard.tsx`

```typescript
/** Returns integer days until the ISO timestamp, floored to 0. */
function daysUntilReset(resets_at: string | null | undefined): number {
  if (!resets_at) return 0;
  const ms = Date.parse(resets_at) - Date.now();
  return Math.max(0, Math.ceil(ms / (1000 * 60 * 60 * 24)));
}
```
Define once in a shared location (e.g., `mobile/src/utils/quota.ts`) so both components import it identically.

---

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `server/app/api/v1/admin.py` (HTML response body) | controller | request-response | No HTML-response FastAPI endpoint exists in the codebase. Use `HTMLResponse` with f-string templating; no Jinja2 dependency at POC scale. |

---

## Metadata

**Analog search scope:** `server/app/ai/`, `server/app/api/`, `server/app/models/`, `server/alembic/versions/`, `mobile/src/api/`, `mobile/src/components/`
**Files scanned:** 15 source files read in full
**Pattern extraction date:** 2026-07-30
