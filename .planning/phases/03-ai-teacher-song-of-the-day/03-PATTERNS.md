# Phase 3: AI Teacher & Song of the Day — Pattern Map

**Mapped:** 2026-07-29
**Files analyzed:** 24 new/modified (11 server, 13 mobile)
**Analogs found:** 22 / 24 with strong or role-match analog; 2 no-analog (new territory)

**Read-only mandate:** This document does not modify any source code. Downstream planner uses the excerpts below to write PLAN.md action steps.

**Mobile Expo v57 mandate:** `mobile/CLAUDE.md` (which imports `mobile/AGENTS.md`) requires consulting `https://docs.expo.dev/versions/v57.0.0/` before writing any Expo API code. Phase 3 introduces no new native modules (per user constraint — "No new native modules"), but every mobile plan MUST cite the Expo v57 docs URL in the plan header before referencing any `expo-*` API.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| **SERVER — MIGRATIONS** | | | | |
| `server/alembic/versions/0003_song_catalog_user_sessions_breakdown_generated_at.py` | migration | schema+seed | `server/alembic/versions/0002_users_songs_categorization_skill_graph.py` | exact (raw-SQL enum, DEFERRABLE FK, system-user seed, IF EXISTS downgrade) |
| **SERVER — ORM** | | | | |
| `server/app/models/db.py` — add `SongCatalog` class | model | CRUD | `server/app/models/db.py::SkillNode` | exact (Numeric mastery/difficulty dual-default pattern, PGUUID PK, SAEnum with create_type=False) |
| `server/app/models/db.py` — add `UserSession` class | model | CRUD | `server/app/models/db.py::SongSkill` | role-match (FK-heavy composite; but Uses UNIQUE index instead of composite PK) |
| `server/app/models/db.py` — add `RatingLevel` Python enum | enum | — | `server/app/models/db.py::SkillLevel` | exact |
| `server/app/models/db.py` — add `PrimarySkillRoot` Python enum | enum | — | `server/app/models/db.py::SongCategory` | exact |
| `server/app/models/db.py` — extend `Song` with `breakdown_generated_at` | model | CRUD | `server/app/models/db.py::Song` (existing columns) | exact |
| **SERVER — PYDANTIC** | | | | |
| `server/app/models/session.py` (new) — `SessionCreate`, `SessionResponse`, `RatingLiteral` | schema | request/response | `server/app/models/user.py::UserBootstrapRequest+UserResponse` | exact (Literal enum, `from_attributes=True`, minimal fields) |
| `server/app/models/song.py` — add `TodaySongResponse`, `BreakdownResponse` | schema | request/response | `server/app/models/song.py::SongResponse` | exact (composition of existing SongResponse + booleans) |
| `server/app/models/song_catalog.py` (new) — `SongCatalogRow` if needed | schema | CRUD | `server/app/models/song.py::SongResponse` | role-match (same BaseModel + Literal pattern) |
| **SERVER — AI** | | | | |
| `server/app/ai/breakdown.py` (new) — `run_technique_breakdown`, `AIBreakdownError`, `SYSTEM_PROMPT`, `_TOOL_DEF` | service | request-response | `server/app/ai/onboarding.py::run_onboarding_parse` | exact (this is the canonical mirror per D-Discretion) |
| **SERVER — SELECTORS** | | | | |
| `server/app/selectors/today_song.py` (new) — `select_today_song` CTE executor | service | CRUD (read) | none — new territory | **NO ANALOG** (see §"No Analog Found") |
| **SERVER — API** | | | | |
| `server/app/api/v1/song_of_day.py` (refactor) — replace stub with per-user selector + reroll | controller | request-response | `server/app/api/v1/users.py::get_skill_graph` (read pattern) + `bootstrap_user` (transactional write pattern) | role-match (AsyncSession, HTTPException, header dep injection) |
| `server/app/api/v1/breakdowns.py` (new) — `GET /api/v1/songs/{id}/breakdown` | controller | request-response | `server/app/api/v1/users.py::bootstrap_user` (SAVEPOINT + AI-call structure) | role-match (but per RESEARCH §5: NO SAVEPOINT here — use plain `async with db.begin():`) |
| `server/app/api/v1/sessions.py` (new) — `POST /api/v1/sessions` rating write | controller | CRUD (write) | `server/app/api/v1/users.py::bootstrap_user` idempotency guard | role-match (COUNT-before-write, IntegrityError→409) |
| `server/app/api/v1/song_of_day.py` — add `POST /api/v1/today-song/reroll` | controller | request-response | `server/app/api/v1/users.py::re_run_onboarding` | role-match (idempotency guard + system-UUID protection pattern) |
| `server/app/api/deps.py` — add `get_tz_offset_minutes` dependency | middleware | request | `server/app/api/deps.py::get_user_id` | exact |
| `server/app/main.py` — register new routers | config | — | `server/app/main.py::include_router` block | exact |
| **MOBILE — API** | | | | |
| `mobile/src/api/apiClient.ts` (modify) — inject `X-Timezone-Offset` header | middleware | request | `mobile/src/api/apiClient.ts::apiFetch` X-User-ID injection | exact (same injection site) |
| `mobile/src/api/todaySong.ts` (new) — `useTodaySong`, `useBreakdown`, `useReroll` | service | request-response | `mobile/src/api/users.ts::useUser+useSkillGraph+useUserReonboard` | exact (TanStack Query pattern already documented in RESEARCH §4) |
| `mobile/src/api/sessions.ts` (new) — `useSubmitRating` | service | CRUD (write) | `mobile/src/api/users.ts::useUserBootstrap` | exact (useMutation + invalidateQueries) |
| **MOBILE — COMPONENTS** | | | | |
| `mobile/src/components/TabNotation.tsx` (modify) — multi-measure + memoized Measure + horizontal scroll | component | data-render | `mobile/src/components/TabNotation.tsx` (existing) | exact (refactor its own body — same constants, add wrapper) |
| `mobile/src/components/SongOfDayCard.tsx` (new) | component | data-render | `mobile/src/app/(tabs)/index.tsx` header block (lines 46-57) | role-match (existing "header" View pattern extracted into a card) |
| `mobile/src/components/RatingPills.tsx` (new) — 3-tier horizontal Pressables | component | user-input | `mobile/src/components/RetentionFormatRadio.tsx` | exact (Pressable + border-left-active pattern per UI-SPEC §5) |
| `mobile/src/components/FletcherLoader.tsx` (new) — extract loader rotation into shared component | component | data-render | `mobile/src/app/onboarding/preferences.tsx` loader block (lines 179-186) + `LOADER_MESSAGES` (lines 52-56) | exact (verbatim extraction — see §Shared Patterns) |
| `mobile/src/components/FromTheBankTag.tsx` (new) | component | data-render | `mobile/src/app/(tabs)/index.tsx::difficultyBadge` styles (lines 158-173) | role-match (chip styling with border variant per UI-SPEC §4) |
| `mobile/src/components/BreakdownErrorCard.tsx` (new) | component | data-render | `mobile/src/app/onboarding/preferences.tsx::errorBox` (lines 239-250) | role-match (dark-red-tint pattern; combine with `FletcherIntroCard` shell for CTA) |
| `mobile/src/components/AlreadyRatedCard.tsx` (new) | component | data-render | `mobile/src/app/onboarding/preferences.tsx` failOpen block (lines 167-176) | role-match (full-screen post-completion state) |
| **MOBILE — SCREENS** | | | | |
| `mobile/src/app/(tabs)/index.tsx` (modify) — wire `useTodaySong` + tap-to-breakdown flow | screen | data-render+navigation | existing file body | exact (surgical modification) |
| `mobile/src/app/breakdown/[songId].tsx` (new — likely) OR overlay in `(tabs)/index.tsx` | screen | data-render | `mobile/src/app/(tabs)/index.tsx` breakdown render sections (lines 60-91) | role-match (breakdown stack already exists; new file only needed if planner chooses route-based over inline) |

---

## Pattern Assignments

### `server/alembic/versions/0003_song_catalog_user_sessions_breakdown_generated_at.py` (migration)

**Analog:** `server/alembic/versions/0002_users_songs_categorization_skill_graph.py`

**Header pattern** (0002 lines 1-32):
```python
"""Song catalog, user sessions, breakdown_generated_at column

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-29

Ordered steps:
1. Create song_catalog table with primary_skill_root enum + difficulty numeric(4,3)
2. Seed 10 hand-curated catalog songs
3. Create user_sessions table with UNIQUE(user_id, song_id, local_calendar_day)
4. Add songs.breakdown_generated_at nullable timestamptz
5. (Optional) index on skill_nodes(user_id, mastery, updated_at) for selector performance
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None
```

**Raw-SQL enum creation pattern** (0002 lines 58, 94 — CRITICAL to avoid SQLAlchemy re-emit bug):
```python
# Raw SQL avoids SQLAlchemy re-emitting CREATE TYPE when op.add_column
# processes an sa.Enum column that SQLAlchemy hasn't seen in this session.
op.execute("CREATE TYPE primary_skill_root AS ENUM ('rhythm', 'lead', 'chord_voicings', 'fingerstyle', 'music_theory', 'timing')")
op.execute("CREATE TYPE rating_level AS ENUM ('not_my_tempo', 'getting_closer', 'thats_what_im_looking_for')")
```

**DEFERRABLE FK pattern** (0002 lines 95-111) — apply to `user_sessions.song_id` if any circular refs; likely not needed for Phase 3 but noted for the planner:
```python
op.execute("""
    CREATE TABLE user_sessions (
        id UUID PRIMARY KEY NOT NULL,
        user_id UUID NOT NULL REFERENCES users(id),
        song_id INTEGER NOT NULL REFERENCES songs(id),
        rating rating_level NOT NULL,
        local_calendar_day DATE NOT NULL,
        tz_offset_minutes INTEGER NOT NULL,
        rated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_user_sessions_daily UNIQUE (user_id, song_id, local_calendar_day)
    )
""")
```

**Seed data pattern** (0002 lines 65-69 — system user, but the shape mirrors for hand-curated catalog):
```python
# Idempotent seed of catalog rows — ON CONFLICT DO NOTHING lets migration re-run safely.
op.execute("""
    INSERT INTO song_catalog (id, title, artist, genre, primary_skill_root, difficulty)
    VALUES
        (gen_random_uuid(), 'Sweet Home Chicago', 'Robert Johnson', 'Blues', 'rhythm', 0.20),
        (gen_random_uuid(), 'Little Wing', 'Jimi Hendrix', 'Rock', 'chord_voicings', 0.65),
        (gen_random_uuid(), 'Blackbird', 'The Beatles', 'Folk', 'fingerstyle', 0.50),
        (gen_random_uuid(), 'Wonderwall', 'Oasis', 'Rock', 'chord_voicings', 0.20),
        (gen_random_uuid(), 'Comfortably Numb', 'Pink Floyd', 'Rock', 'lead', 0.70),
        (gen_random_uuid(), 'Purple Haze', 'Jimi Hendrix', 'Rock', 'lead', 0.75),
        (gen_random_uuid(), 'Hotel California', 'Eagles', 'Rock', 'lead', 0.65),
        (gen_random_uuid(), 'Wish You Were Here', 'Pink Floyd', 'Rock', 'fingerstyle', 0.40),
        (gen_random_uuid(), 'Eruption', 'Van Halen', 'Rock', 'lead', 0.90),
        (gen_random_uuid(), 'Thunderstruck', 'AC/DC', 'Rock', 'rhythm', 0.55)
    ON CONFLICT DO NOTHING
""")
```

**Downgrade pattern** (0002 lines 136-157 — strict reverse order):
```python
def downgrade() -> None:
    op.drop_column("songs", "breakdown_generated_at")
    op.drop_table("user_sessions")
    op.execute("DROP TYPE IF EXISTS rating_level")
    op.drop_table("song_catalog")
    op.execute("DROP TYPE IF EXISTS primary_skill_root")
```

---

### `server/app/models/db.py` extensions — `SongCatalog`, `UserSession` ORM

**Analog:** `SkillNode` class (lines 99-149) — critical for the dual-default pattern on numeric fields.

**Dual-default pattern (LOAD-BEARING — Phase 2 hotfix)** — SkillNode.mastery (lines 135-143):
```python
mastery: Mapped[Decimal] = mapped_column(
    Numeric(4, 3),
    nullable=False,
    default=Decimal("0.0"),          # Python-side default so new rows have mastery=0.0
                                      # BEFORE flush/refresh — response serialization
                                      # needs it non-None on the in-memory object.
    server_default=text("0.0"),      # DB-side default backs it for direct-SQL inserts
                                      # (migrations, seeding, external tools).
)
```

**Apply this exact dual-default to:**
- `SongCatalog.difficulty` (Numeric(4,3)) — will be read for the ±0.15 filter; must not be None in-memory after INSERT
- Any new mastery/rating column that has a numeric server-side default

**SAEnum with `create_type=False` pattern** (SkillNode.level lines 119-127):
```python
level: Mapped[str] = mapped_column(
    SAEnum(
        SkillLevel,
        name="skill_level",
        values_callable=lambda x: [e.value for e in x],
        create_type=False,  # already created by migration 0002 raw SQL
    ),
    nullable=False,
)
```

**Apply verbatim to:**
- `SongCatalog.primary_skill_root` — `SAEnum(PrimarySkillRoot, name="primary_skill_root", ..., create_type=False)`
- `UserSession.rating` — `SAEnum(RatingLevel, name="rating_level", ..., create_type=False)`

**Composite / unique-constraint junction pattern** (SongSkill lines 156-173):
```python
class SongSkill(Base):
    __tablename__ = "song_skills"

    song_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("songs.id"), primary_key=True
    )
    skill_node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_nodes.id"), primary_key=True
    )
```
`UserSession` differs — it has its own UUID PK plus a UNIQUE constraint (enforced in migration), not a composite PK. Planner uses this shape for the ORM class body:
```python
class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    song_id: Mapped[int] = mapped_column(Integer, ForeignKey("songs.id"), nullable=False)
    # ... rating, local_calendar_day, tz_offset_minutes, rated_at
```

---

### `server/app/models/session.py` (new — Pydantic session models)

**Analog:** `server/app/models/user.py`

**Literal enum pattern** (user.py lines 19-20):
```python
retention_format: Literal["streak", "weekly_digest", "monthly_milestone"] = "streak"
```
Apply to:
```python
# server/app/models/session.py
RatingLiteral = Literal["not_my_tempo", "getting_closer", "thats_what_im_looking_for"]

class SessionCreate(BaseModel):
    song_id: int
    rating: RatingLiteral

class SessionResponse(BaseModel):
    id: UUID
    user_id: UUID
    song_id: int
    rating: RatingLiteral
    local_calendar_day: str          # ISO date; matches Phase 2 onboarded_at pattern
    rated_at: str                    # ISO timestamp
    model_config = {"from_attributes": True}
```

**`from_attributes=True` boundary pattern** (user.py line 40):
```python
class UserResponse(BaseModel):
    ...
    model_config = {"from_attributes": True}
```

**CRITICAL merge-boundary rule** (from `<additional_context>`): "Bare `dict` in Pydantic → OpenAPI codegen produces `Record<string, never>` in TypeScript. Any new response model with `dict` must be typed with explicit key/value types." So do NOT use bare `dict` in `TodaySongResponse` or `SessionResponse`.

---

### `server/app/models/song.py` extension — `TodaySongResponse`, `BreakdownResponse`

**Analog:** `SongResponse` (lines 70-84).

**Composition pattern** (mirror SongResponse's `breakdown: Breakdown` field pattern for TodaySongResponse):
```python
class TodaySongResponse(BaseModel):
    """GET /api/v1/song-of-day response. Composes existing SongResponse plus rotation-state booleans."""
    song: SongResponse
    breakdown_available: bool         # True if songs.breakdown_generated_at IS NOT NULL
    from_bank: bool                   # True if 25% override or empty-working_on path fired
    rerolled: bool                    # True if the user has already used today's reroll
    model_config = {"from_attributes": False}  # constructed by hand from selector + song row
```

**Breakdown response** — reuse existing `Breakdown` class as-is; no wrapper needed unless the planner wants a `BreakdownResponse` with metadata (generated_at). Recommend re-exporting `Breakdown` directly for `GET /songs/{id}/breakdown`.

---

### `server/app/ai/breakdown.py` (new — the canonical Sonnet mirror)

**Analog:** `server/app/ai/onboarding.py` (verbatim mirror — this is the strongest analog in the entire phase).

**Full pattern to copy** (onboarding.py, complete file body):

Imports (lines 1-16):
```python
"""Technique breakdown parse — single Sonnet call that turns song title + artist + target skills
into a structured Breakdown (tab + chords + technique_notes).

Phase 4 cost-governor interception point: this is the ONLY function that calls
Sonnet during breakdown fetch. Do not sprinkle client.messages.create() calls anywhere else.
"""
import asyncio
import logging
from typing import Any

from anthropic import APITimeoutError, APIError

from app.ai.client import get_client, SONNET_MODEL
from app.models.song import Breakdown

logger = logging.getLogger(__name__)


class AIBreakdownError(Exception):
    """Raised when Sonnet breakdown fails after retry. Caller returns 503 with Fletcher-voiced message."""
```

**Tool definition pattern** (onboarding.py lines 55-60):
```python
_TOOL_NAME = "emit_breakdown"
_TOOL_DEF: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "Emit a full technique breakdown for the requested song. "
        "Include 4-8 measures of tab (standard tuning unless the song calls for otherwise), "
        "chord diagrams for every chord referenced in the tab, and 3-5 technique notes "
        "focused on the target skills the user is working on."
    ),
    "input_schema": Breakdown.model_json_schema(),
}
```

**Call + retry pattern (CRITICAL — line 89 `get_client()` inside try/except)** (onboarding.py lines 82-120):
```python
async def run_technique_breakdown(
    song_title: str,
    song_artist: str,
    target_skill_names: list[str],
    user_level: float,
    *,
    timeout_seconds: float = 30.0,
) -> Breakdown:
    user_content = _format_user_message(song_title, song_artist, target_skill_names, user_level)

    async def _call(timeout: float) -> Breakdown:
        # get_client() may raise RuntimeError if ANTHROPIC_API_KEY is unset.
        # Calling it inside _call (which is inside the outer try/except below)
        # means the RuntimeError gets wrapped as AIBreakdownError instead of raw 500.
        client = get_client()
        resp = await asyncio.wait_for(
            client.messages.create(
                model=SONNET_MODEL,
                max_tokens=8192,               # RESEARCH §1: raise from 4096 for headroom
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
            return await _call(timeout_seconds * 2)      # D-07 widened retry
    except Exception as e:
        raise AIBreakdownError(f"Sonnet breakdown failed: {type(e).__name__}: {e}") from e
```

**System prompt goes above `run_technique_breakdown`** — RESEARCH §1 already drafted it (lines 267-285 of 03-RESEARCH.md). The planner should copy that verbatim.

---

### `server/app/api/v1/song_of_day.py` refactor — selector wiring

**Analog:** `server/app/api/v1/users.py::get_skill_graph` (lines 297-316) for the read pattern, plus `bootstrap_user` header/dep injection.

**Imports pattern** (users.py lines 1-22):
```python
import logging
from datetime import datetime, timezone
from typing import ...
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.api.deps import get_user_id, get_tz_offset_minutes    # NEW: tz dep
from app.models.db import Song, SongCatalog, UserSession, SkillNode, SongSkill
from app.models.song import SongResponse, TodaySongResponse

logger = logging.getLogger(__name__)
router = APIRouter()
```

**Endpoint shape** (adapted from `bootstrap_user` signature style):
```python
@router.get("/song-of-day", response_model=TodaySongResponse)
async def get_song_of_day(
    user_id: UUID = Depends(get_user_id),
    tz_offset_minutes: int = Depends(get_tz_offset_minutes),
    db: AsyncSession = Depends(get_db),
) -> TodaySongResponse:
    # ... run selector CTE via app.selectors.today_song.select_today_song(db, user_id, tz_offset_minutes)
    # ... check UserSession for rerolled flag
    # ... return composed TodaySongResponse
```

**No SAVEPOINT needed** in the read path — selector is a single deterministic query.

---

### `server/app/api/v1/breakdowns.py` (new — lazy-fetch + cache-forever)

**Analog:** `server/app/api/v1/users.py::bootstrap_user` (lines 175-274) — for the AI-call structure but WITHOUT the SAVEPOINT (per RESEARCH §5).

**Cache-check-before-call pattern** (new — from D-Discretion "lazy on first tap, cache-forever"):
```python
@router.get("/songs/{song_id}/breakdown", response_model=Breakdown)
async def get_breakdown(
    song_id: int,
    user_id: UUID = Depends(get_user_id),
    db: AsyncSession = Depends(get_db),
) -> Breakdown:
    # 1. Load song row + check cache
    song = (await db.execute(select(Song).where(Song.id == song_id))).scalar_one_or_none()
    if song is None:
        raise HTTPException(status_code=404, detail="Song not found.")

    if song.breakdown_generated_at is not None:
        # Cache hit — return existing breakdown
        return Breakdown.model_validate(song.breakdown)

    # 2. Cache miss — resolve target skills for this song + user's level
    target_skill_names = [...]  # SELECT skill_nodes.name JOIN song_skills WHERE song_id
    user_level = ...            # AVG(mastery) inline compute per D-Discretion

    # 3. Sonnet call — NO SAVEPOINT per RESEARCH §5 (no rescue path; failure = no cache write)
    try:
        breakdown = await run_technique_breakdown(
            song.title, song.artist, target_skill_names, user_level
        )
    except AIBreakdownError as e:
        logger.warning("Breakdown call failed for song %s user %s: %s", song_id, user_id, e)
        raise HTTPException(
            status_code=503,
            detail="Fletcher stepped away from the desk. Give me another second and try again.",
        )

    # 4. Persist (single transaction — no SAVEPOINT wrapper needed)
    async with db.begin():
        song.breakdown = breakdown.model_dump()
        song.breakdown_generated_at = datetime.now(timezone.utc)

    return breakdown
```

**Key deviation from `bootstrap_user`:** Do NOT wrap the Sonnet call in `async with db.begin_nested():`. Per RESEARCH §5: "No SAVEPOINT needed here. Unlike Phase 2 onboarding (which needed fail-open to preserve the user row), Phase 3 breakdown failure has nothing to rescue. The song row is already persisted; only the breakdown cache miss remains."

---

### `server/app/api/v1/sessions.py` (new — rating write, atomic)

**Analog:** `server/app/api/v1/users.py::bootstrap_user` (idempotency guard lines 194-218).

**Idempotency guard pattern (verbatim structure, different keys)** (users.py lines 194-218):
```python
existing_count: int = (await db.execute(
    select(sqlfunc.count()).select_from(SkillNode).where(
        SkillNode.user_id == body.user_id
    )
)).scalar_one()

if existing_count > 0:
    # ... return existing shape
```

**Adapt to `sessions.py`:**
```python
already = await db.scalar(
    select(func.count(UserSession.id)).where(
        UserSession.user_id == user_id,
        UserSession.song_id == body.song_id,
        UserSession.local_calendar_day == local_day,
    )
)
if already:
    raise HTTPException(status_code=409, detail="Already rated this song today.")
```

**Full atomic write pattern** — see RESEARCH §5 (03-RESEARCH.md lines 415-486). That block is the exact template; planner copies verbatim including `RATING_SHIFTS` dict, `LEAST/GREATEST` SQL clamp, and `async with db.begin():` single transaction.

**Do NOT reference `song_skills.weight` in the UPDATE.** Per RESEARCH anti-patterns: "D-07 explicitly weights equally — don't accidentally use the column that exists in the schema (per Phase 2 02-03 ORM)."

---

### `server/app/api/v1/song_of_day.py` reroll endpoint

**Analog:** `server/app/api/v1/users.py::re_run_onboarding` (lines 319-391) for the idempotency guard + system-UUID protection pattern.

**System-UUID protection pattern** (users.py lines 337-341):
```python
if str(user_id) == "00000000-0000-0000-0000-000000000000":
    raise HTTPException(
        status_code=403,
        detail="Re-run against system user is forbidden (protects seed data).",
    )
```

Phase 3 reroll doesn't need this exact guard (no destructive seed), but note the pattern for future consistency. What Phase 3 DOES need: the idempotency guard adapted to check if `UserSession` has any reroll flag or if a `today_song_rerolls` counter is present.

**Recommended shape** — extend `user_sessions` OR create a small `daily_reroll` marker table. Planner decides; either way, the guard structure mirrors:
```python
already_rerolled = await db.scalar(
    select(func.count(...)).where(..., local_calendar_day == local_day)
)
if already_rerolled > 0:
    raise HTTPException(status_code=409, detail="Already used your reroll today.")
```

---

### `server/app/api/deps.py` — add `get_tz_offset_minutes`

**Analog:** `server/app/api/deps.py::get_user_id` (full file, 20 lines).

**Exact template** (deps.py lines 12-19):
```python
async def get_user_id(x_user_id: UUID = Header(..., alias="X-User-ID")) -> UUID:
    return x_user_id
```

**New function** (per RESEARCH §6 lines 614-622):
```python
def get_tz_offset_minutes(x_timezone_offset: Annotated[str, Header()] = "0") -> int:
    try:
        v = int(x_timezone_offset)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Timezone-Offset must be an integer number of minutes.")
    if not -840 <= v <= 840:  # ±14 hours (Postgres constraint)
        raise HTTPException(status_code=400, detail="X-Timezone-Offset out of range.")
    return v
```

---

### `server/app/main.py` — register new routers

**Analog:** `main.py` lines 10-36 (existing router registration block).

**Pattern to extend** (main.py lines 10-11, 35-36):
```python
from app.api.v1.song_of_day import router as song_router
from app.api.v1.users import router as users_router
# NEW:
from app.api.v1.breakdowns import router as breakdowns_router
from app.api.v1.sessions import router as sessions_router

app.include_router(song_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
# NEW:
app.include_router(breakdowns_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")
```

Also: extend `allow_methods=["GET", "POST"]` — already includes POST, so no change needed.

---

### `mobile/src/api/apiClient.ts` — add `X-Timezone-Offset` injection

**Analog:** `mobile/src/api/apiClient.ts::apiFetch` (existing file, lines 23-43).

**Existing injection pattern** (apiClient.ts lines 29-38):
```typescript
const userId = getOrCreateUserId();
const res = await fetch(url, {
  ...init,
  headers: {
    'Content-Type': 'application/json',
    'X-User-ID': userId,
    ...(init.headers ?? {}),
  },
});
```

**Modification per RESEARCH §6 (03-RESEARCH.md lines 583-605):**
```typescript
const userId = getOrCreateUserId();
// Date().getTimezoneOffset() returns minutes WEST of UTC (opposite sign of the ISO offset).
// For UTC-5 (Eastern Time), getTimezoneOffset() returns +300.
// We negate so X-Timezone-Offset = -300 for UTC-5 — matching ISO-8601 convention.
const tzOffset = -new Date().getTimezoneOffset();
const res = await fetch(url, {
  ...init,
  headers: {
    'Content-Type': 'application/json',
    'X-User-ID': userId,
    'X-Timezone-Offset': String(tzOffset),
    ...(init.headers ?? {}),
  },
});
```

**Critical:** Single injection point — no per-hook change. Do NOT add a second fetch path.

---

### `mobile/src/api/todaySong.ts` (new — TanStack hooks)

**Analog:** `mobile/src/api/users.ts` (whole file — verbatim template).

**Import + type-export pattern** (users.ts lines 7-17):
```typescript
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from './generated/schema';
import { apiFetch } from './apiClient';
import { getOrCreateUserId } from './mmkv';

export type TodaySongResponse = components['schemas']['TodaySongResponse'];
export type Breakdown = components['schemas']['Breakdown'];
```

**Query hook pattern** (users.ts::useSkillGraph lines 62-71):
```typescript
export function useSkillGraph() {
  const userId = getOrCreateUserId();
  return useQuery({
    queryKey: ['skill-graph', userId],
    queryFn: () => apiFetch<SkillGraphResponse>(`/api/v1/users/${userId}/skill-graph`),
    staleTime: 1000 * 60 * 60, // 1 hour
    refetchOnWindowFocus: false,
  });
}
```

**Adapt for `useTodaySong`** — but include local-day key per RESEARCH §4 (03-RESEARCH.md lines 511-528):
```typescript
function localCalendarDay(): string {
  const now = new Date();
  const offsetMs = now.getTimezoneOffset() * 60 * 1000;
  const localMidnight = new Date(now.getTime() - offsetMs);
  return localMidnight.toISOString().slice(0, 10);
}

export function useTodaySong() {
  const userId = getOrCreateUserId();
  return useQuery({
    queryKey: ['today-song', userId, localCalendarDay()],
    queryFn: () => apiFetch<TodaySongResponse>('/api/v1/today-song'),
    staleTime: 1000 * 60 * 60 * 12,
    refetchOnWindowFocus: false,
  });
}
```

**Mutation + invalidation pattern** (users.ts::useUserBootstrap lines 41-55):
```typescript
export function useUserBootstrap() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UserBootstrapRequest) =>
      apiFetch<SkillGraphResponse>('/api/v1/users', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (graph, variables) => {
      setOnboardedAt(new Date().toISOString());
      qc.setQueryData(['skill-graph', variables.user_id], graph);
    },
  });
}
```

**Adapt for `useReroll`** (per RESEARCH §4 lines 559-569):
```typescript
export function useReroll() {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();
  return useMutation({
    mutationFn: () => apiFetch<TodaySongResponse>('/api/v1/today-song/reroll', { method: 'POST' }),
    onSuccess: (fresh) => {
      qc.setQueryData(['today-song', userId, localCalendarDay()], fresh);
    },
  });
}
```

**`useBreakdown(songId)` — cache-forever staleTime** (per RESEARCH §4 lines 530-543):
```typescript
export function useBreakdown(songId: number | undefined) {
  return useQuery({
    queryKey: ['breakdown', songId],
    queryFn: () => apiFetch<Breakdown>(`/api/v1/songs/${songId}/breakdown`),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60 * 24 * 30,
    enabled: songId !== undefined,
    refetchOnWindowFocus: false,
  });
}
```

---

### `mobile/src/api/sessions.ts` (new — rating mutation)

**Analog:** `mobile/src/api/users.ts::useUserReonboard` (lines 87-103).

**Full pattern to copy** (users.ts lines 87-103):
```typescript
export function useUserReonboard() {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();
  return useMutation({
    mutationFn: (body: UserBootstrapRequest) =>
      apiFetch<SkillGraphResponse>(`/api/v1/users/${userId}/re-run`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (graph) => {
      setOnboardedAt(new Date().toISOString());
      clearWizardState();
      qc.setQueryData(['skill-graph', userId], graph);
      qc.invalidateQueries({ queryKey: ['user', userId] });
    },
  });
}
```

**Adapt for `useSubmitRating`** (per RESEARCH §4 lines 545-557):
```typescript
export function useSubmitRating() {
  const qc = useQueryClient();
  const userId = getOrCreateUserId();
  return useMutation({
    mutationFn: (body: { song_id: number; rating: RatingLiteral }) =>
      apiFetch<SessionResponse>('/api/v1/sessions', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      // Skill graph mastery changed → invalidate so Today's next selection reflects.
      qc.invalidateQueries({ queryKey: ['skill-graph', userId] });
      // Today song stays locked for today (D-05). Don't invalidate it.
    },
  });
}
```

---

### `mobile/src/components/TabNotation.tsx` (modify — multi-measure + memoize)

**Analog:** `mobile/src/components/TabNotation.tsx` (existing file — its own current body is the analog).

**Constants to preserve verbatim** (lines 14-18):
```typescript
const STRING_SPACING = 20;
const BEAT_WIDTH = 48;
const LEFT_MARGIN = 30;
const TOP_PADDING = 20;
const STRINGS = 6;
```

**Current single-measure inner render** (lines 66-98) becomes the `Measure` component body:
```typescript
const Measure = React.memo(function Measure({
  measure,
  offsetX,
  tuning,
}: { measure: MeasureT; offsetX: number; tuning: string[] }) {
  const beats = measure.beats ?? [];
  // ... existing per-beat map with beatX computed as offsetX + LEFT_MARGIN + beatIndex * BEAT_WIDTH + BEAT_WIDTH / 2
  // ... existing Rect+Text occlusion pattern (lines 76-95)
});
```

**Horizontal scroll wrapper pattern** (per RESEARCH §2 lines 791-796 and UI-SPEC §7):
```typescript
<ScrollView horizontal showsHorizontalScrollIndicator={true}>
  <Svg width={totalWidth} height={staffHeight}>
    {measures.map((m, i) => <Measure key={i} measure={m} offsetX={i * MEASURE_WIDTH} tuning={tuning} />)}
  </Svg>
</ScrollView>
```

**UI-SPEC note (line 383):** `showsHorizontalScrollIndicator={true}` on tab (unlike chords). Users need to know they can scroll.

---

### `mobile/src/components/SongOfDayCard.tsx` (new)

**Analog:** `mobile/src/app/(tabs)/index.tsx` header block (lines 46-57) + its styles (129-173).

**Pattern to extract** (index.tsx lines 46-57):
```typescript
<View style={styles.header}>
  <Text style={styles.label}>SONG OF THE DAY</Text>
  <Text style={styles.title}>{song.title}</Text>
  <Text style={styles.artist}>{song.artist}</Text>
  <Text style={styles.meta}>
    {song.genre} · {song.bpm} BPM · Key of {song.key}
  </Text>
  <View style={styles.difficultyBadge}>
    <Text style={styles.difficultyText}>{song.difficulty}</Text>
  </View>
</View>
```

**Style pattern to preserve** (index.tsx lines 129-173):
```typescript
header: { marginBottom: 28, paddingBottom: 20, borderBottomWidth: 1, borderBottomColor: '#333' },
label: { fontSize: 11, fontWeight: '700', letterSpacing: 1.5, color: '#E07B39', marginBottom: 6 },
title: { fontSize: 24, fontWeight: '800', color: '#F5F5F5', marginBottom: 4 },
artist: { fontSize: 16, color: '#999', marginBottom: 6 },
meta: { fontSize: 13, color: '#666', marginBottom: 10 },
difficultyBadge: {
  alignSelf: 'flex-start',
  backgroundColor: '#2A2A2A',
  borderRadius: 6,
  paddingHorizontal: 10,
  paddingVertical: 4,
  borderWidth: 1,
  borderColor: '#E07B39',
},
```

**Extend to card shell** per UI-SPEC §1 — Fletcher line (muted, above title), primary CTA button (48px tall), re-roll ghost button (bottom-right). CTA styling analog is `FletcherIntroCard::cta` (lines 120-124):
```typescript
cta: {
  backgroundColor: '#E07B39',
  paddingVertical: 14,
  borderRadius: 8,
  alignItems: 'center',
},
```

---

### `mobile/src/components/RatingPills.tsx` (new — strongest visual analog match)

**Analog:** `mobile/src/components/RetentionFormatRadio.tsx` (whole file — per UI-SPEC line 37 "Visual pattern reused").

**Pressable + active-border pattern** (RetentionFormatRadio.tsx lines 37-52):
```typescript
<Pressable
  key={opt.id}
  style={[styles.row, active && styles.rowActive]}
  onPress={() => onChange(opt.id)}
  accessibilityRole="radio"
  accessibilityState={{ selected: active }}
  accessibilityLabel={opt.label}
>
  ...
  <Text style={styles.label}>{opt.label}</Text>
</Pressable>
```

**Active-state style pattern** (RetentionFormatRadio.tsx lines 62-73):
```typescript
row: {
  flexDirection: 'row',
  alignItems: 'center',
  padding: 12,
  borderRadius: 8,
  backgroundColor: '#242424',
},
rowActive: {
  // Border-left accent mirrors techniqueCard pattern from Today tab.
  borderLeftWidth: 3,
  borderLeftColor: '#E07B39',
  paddingLeft: 9,
},
```

**Adaptation per UI-SPEC §5:**
- Change layout from vertical stack (`gap: 12, marginBottom: 24`) to horizontal row (`flexDirection: 'row'`, `gap: 12`)
- Each pill 48px tall (UI-SPEC 2xl token) — width flex 1
- No radio dot — remove `styles.radio`/`radioActive`/`radioDot` (14 lines)
- Label centered — `textAlign: 'center'`, `label` style becomes `fontSize: 14, fontWeight: '600', color: '#F5F5F5'`
- accessibilityRole="button" (not "radio" — per UI-SPEC "they're independent tap targets, not options in a form")

**Copy — LOCKED per UI-SPEC §5 (do NOT edit):**
- Left: `Not my tempo`
- Center: `Getting closer`
- Right: `That's what I'm looking for`

---

### `mobile/src/components/FletcherLoader.tsx` (new — extract shared loader)

**Analog:** `mobile/src/app/onboarding/preferences.tsx` loader block (lines 179-186) + `LOADER_MESSAGES` (lines 52-56) + rotation `useEffect` (lines 91-102) + styles (lines 212-223).

**Loader messages pattern (adapt copy per UI-SPEC §3)** (preferences.tsx lines 50-56):
```typescript
const LOADER_MESSAGES = [
  'Fletcher is listening...',
  'Working on your first lesson plan...',   // ← replace with 'Working out the fingering...' for Phase 3
  'Almost there...',
] as const;
```

**Phase 3 copy per UI-SPEC §3:**
```typescript
const BREAKDOWN_LOADER_MESSAGES = [
  'Fletcher is listening...',
  'Working out the fingering...',
  'Almost there...',
] as const;
```

**Rotation timer useEffect pattern (verbatim)** (preferences.tsx lines 91-102):
```typescript
useEffect(() => {
  if (!active.isPending) {
    resetLoader();
    return;
  }
  const t1 = setTimeout(() => setLoaderMessageIndex(1), 3000);
  const t2 = setTimeout(() => setLoaderMessageIndex(2), 8000);
  return () => {
    clearTimeout(t1);
    clearTimeout(t2);
  };
}, [active.isPending, setLoaderMessageIndex, resetLoader]);
```

**Render pattern (verbatim)** (preferences.tsx lines 179-186):
```typescript
return (
  <View style={styles.loader}>
    <ActivityIndicator size="large" color="#E07B39" />
    <Text style={styles.loaderText}>{LOADER_MESSAGES[loaderMessageIndex]}</Text>
  </View>
);
```

**Zustand slice reused** — uiStore.ts `loaderMessageIndex`/`setLoaderMessageIndex`/`resetLoader` (lines 17-27) already exist; no new state.

**Component API** (planner's discretion):
```typescript
export function FletcherLoader({ messages, isPending }: { messages: readonly string[]; isPending: boolean }) {
  // ... hosts the useEffect + Zustand + render pattern above
}
```

---

### `mobile/src/components/FromTheBankTag.tsx` (new)

**Analog:** `mobile/src/app/(tabs)/index.tsx::difficultyBadge` (lines 158-173) + technique-note border pattern (lines 185-192).

**Chip pattern to adapt** (index.tsx lines 158-173):
```typescript
difficultyBadge: {
  alignSelf: 'flex-start',
  backgroundColor: '#2A2A2A',
  borderRadius: 6,
  paddingHorizontal: 10,
  paddingVertical: 4,
  borderWidth: 1,
  borderColor: '#E07B39',
},
```

**Per UI-SPEC §4 — replace `borderWidth: 1` with `borderLeftWidth: 3` (matches technique-note pattern from index.tsx line 190):**
```typescript
tag: {
  alignSelf: 'flex-start',
  backgroundColor: '#242424',
  borderLeftWidth: 3,
  borderLeftColor: '#E07B39',
  paddingHorizontal: 6,
  paddingVertical: 4,
},
label: {
  fontSize: 14,
  fontWeight: '600',
  color: '#F5F5F5',
},
```

**Copy per UI-SPEC §2:**
- User's-own-songs bank path: `From your bench`
- Seed-catalog bank path: `From the bank`

---

### `mobile/src/components/BreakdownErrorCard.tsx` (new)

**Analog:** `mobile/src/app/onboarding/preferences.tsx::errorBox` + `errorText` styles (lines 239-250) — dark-red-tint pattern.

**Error surface pattern** (preferences.tsx lines 239-250):
```typescript
errorBox: {
  backgroundColor: '#3A1F1F',
  padding: 12,
  borderRadius: 6,
  marginBottom: 12,
  borderLeftWidth: 3,
  borderLeftColor: '#ff6b6b',
},
errorText: {
  color: '#ffb0b0',
  fontSize: 14,
},
```

**Adapt per UI-SPEC §7** — background `#3A1F1F` (locked), primary text `#F5F5F5` (not `#ffb0b0`), body `#A0A0A0`, one primary CTA button, one secondary link.

**Composition note per UI-SPEC line 49:** "wraps `FletcherIntroCard` or is a bespoke card." Recommend bespoke — `FletcherIntroCard`'s wizard-progress prop doesn't apply here.

**Copy — LOCKED per UI-SPEC §7:**
- Heading: `Fletcher lost the thread.`
- Body: `The connection dropped mid-thought. Give it a minute — try again.`
- Primary CTA: `Try again`
- Secondary link: `Back to today's song`

---

### `mobile/src/components/AlreadyRatedCard.tsx` (new)

**Analog:** `mobile/src/app/onboarding/preferences.tsx` failOpen block (lines 167-176) + styles (lines 224-231).

**Full-screen post-completion state pattern** (preferences.tsx lines 167-176):
```typescript
if (showFailOpenCard) {
  return (
    <View style={styles.loader}>
      <ActivityIndicator size="large" color="#E07B39" />
      <Text style={styles.failOpenText}>
        Got what you said. I'll fill in the details as we go.
      </Text>
    </View>
  );
}
```

**Style pattern** (preferences.tsx lines 224-231):
```typescript
failOpenText: {
  color: '#F5F5F5',
  marginTop: 16,
  fontSize: 15,
  textAlign: 'center',
  maxWidth: 300,
  lineHeight: 22,
},
```

**Auto-navigate timer pattern (T-02-04-03 mitigation)** (preferences.tsx lines 108-119):
```typescript
useEffect(() => {
  if (!active.data) return;
  if (active.data.mode === 'bootstrap' && !showFailOpenCard) {
    setShowFailOpenCard(true);
    const t = setTimeout(() => {
      if (isReRun) setReRunPending(false);
      router.replace('/(tabs)');
    }, 2000);
    return () => clearTimeout(t);
  }
  ...
}, [active.data, showFailOpenCard, isReRun]);
```

**Copy — LOCKED per UI-SPEC §6 (per-rating variants):**
- `Not my tempo` → `Slow the whole thing 10 bpm and rebuild tomorrow. You'll be back.`
- `Getting closer` → `Getting closer. Same routine tomorrow — the next pass gets cleaner.`
- `That's what I'm looking for` → `That's the tempo. Push it 5 bpm next session.`
- Secondary line: `See you tomorrow.`

---

### `mobile/src/app/(tabs)/index.tsx` (modify — wire useTodaySong)

**Analog:** the file itself (current body).

**Current Phase 1 wiring to REPLACE** (index.tsx lines 22, 44-92):
```typescript
const { data: song, isLoading, isError, error } = useSongOfDay();
// ... existing scroll of breakdown sections
```

**Replacement pattern:**
```typescript
const { data: today, isLoading, isError, error } = useTodaySong();
const submitRating = useSubmitRating();
const reroll = useReroll();
// ... render <SongOfDayCard song={today.song} onTapBreakdown={...} onReroll={...} />
// ... render <FromTheBankTag /> conditional on today.from_bank
// ... breakdown+RatingPills only after user taps into breakdown route (or after inline expansion)
```

**Loading state UPGRADE per UI-SPEC §2 line 334:** "Loading text (Phase 1's plain `Loading...` on Today tab) is REPLACED across Phase 3 by the `FletcherLoader`." So the existing `<ActivityIndicator size="large" color="#E07B39"><Text>Loading...</Text>` block (lines 24-30) becomes `<FletcherLoader messages={...} isPending={true} />`.

**Preserve verbatim (do NOT rewrite):** the existing breakdown section rendering (index.tsx lines 60-91) — technique notes, tab, chord scroll — is still valid. Only its trigger changes (from immediate render on `useSongOfDay` to conditional render on breakdown fetch state).

---

## Shared Patterns

### Async SQLAlchemy transaction

**Source:** `server/app/api/v1/users.py::bootstrap_user` line 241 (`async with db.begin_nested()`) and line 269 (`await db.commit()`).

**Phase 3 usage rule:**
- **Sonnet call in breakdowns.py:** plain `async with db.begin():` — NO SAVEPOINT (per RESEARCH §5)
- **Rating write in sessions.py:** plain `async with db.begin():` (per RESEARCH §5 template lines 457-482)
- **Reroll write in song_of_day.py:** plain `async with db.begin():`

**Applied to:** `server/app/api/v1/breakdowns.py`, `sessions.py`, extension of `song_of_day.py`.

### Sonnet client boundary (Phase 4 governor interception point)

**Source:** `server/app/ai/onboarding.py` docstring lines 1-5.

**Rule:** Every `client.messages.create()` call MUST live in `server/app/ai/*.py`. Verified via grep in Phase 2: `grep -rn "messages.create" server/app | grep -v "server/app/ai/"` returns empty.

**Applied to:** `server/app/ai/breakdown.py` (the only new AI-call site in Phase 3).

**`get_client()` inside try/except (Phase 2 hotfix)** — the `RuntimeError` for missing `ANTHROPIC_API_KEY` must be raised INSIDE the wrappable try block. See `onboarding.py::run_onboarding_parse` lines 85-89 comment: "Calling it inside _call (which is inside the outer try/except below) means the RuntimeError gets wrapped as AIParseError."

**Applied to:** `run_technique_breakdown` MUST call `get_client()` inside the inner `_call()` closure, not at function entry.

### Idempotency guard on writes

**Source:** `server/app/api/v1/users.py::bootstrap_user` lines 194-218 (COUNT-before-write pattern).

**Applied to:**
- `POST /api/v1/sessions` — one rating per user per song per local calendar day → 409 on repeat
- `POST /api/v1/today-song/reroll` — one reroll per user per local calendar day → 409 on repeat

**Belt-and-suspenders DB constraint:** the migration MUST add `UNIQUE (user_id, song_id, local_calendar_day)` on `user_sessions` so concurrent double-tap fails at DB level with `IntegrityError` → FastAPI 409.

### apiFetch single entry point

**Source:** `mobile/src/api/apiClient.ts::apiFetch` — the only fetch call site in the mobile codebase.

**Rule per `<additional_context>`:** "apiFetch must remain the single fetch entry point — do not add a second fetch path."

**Applied to:** every new Phase 3 hook in `todaySong.ts` and `sessions.ts` routes through `apiFetch<T>(path, init)`.

### TanStack Query key + invalidation contract

**Source:** `mobile/src/api/users.ts::useUserReonboard::onSuccess` lines 96-101.

**Query keys used in Phase 3:**
- `['today-song', userId, localCalendarDay()]` — rotates at local midnight via key change
- `['breakdown', songId]` — `staleTime: Infinity` per D-Discretion "cache-forever"
- `['skill-graph', userId]` — invalidated by `useSubmitRating.onSuccess` so tomorrow's selection reflects mastery shift

**Rule:** rating submission INVALIDATES `['skill-graph', userId]` but does NOT invalidate `['today-song', ...]` (D-05: today's song stays locked once rated).

### Merge-boundary typing (Pydantic → OpenAPI → TS)

**Source:** `<additional_context>` — "Bare `dict` in Pydantic → OpenAPI codegen produces `Record<string, never>` in TypeScript."

**Applied to:**
- `SessionResponse` — no bare `dict` fields; all shapes are `Literal`/`str`/`int`/nested BaseModel
- `TodaySongResponse` — nests `SongResponse` (already correctly typed), NOT a raw `dict`
- `SongCatalogRow` (if planner exposes it via API) — must use explicit types

**Regeneration step required:** after any Pydantic model change, run `npm run codegen:local` from `mobile/` per RESEARCH §Standard Stack line 127.

### Dual-default for numeric columns with server_default

**Source:** `server/app/models/db.py::SkillNode.mastery` lines 135-143.

**Rule (Phase 2 hotfix):** any `Numeric` column with `server_default` MUST also have Python-side `default=Decimal("0.0")` — otherwise in-memory row after INSERT + flush has `mastery=None` and the API response serialization crashes with 500.

**Applied to:**
- `SongCatalog.difficulty` — `Numeric(4,3)`, has server_default, MUST have Python default
- Any new mastery-shift column (none planned in Phase 3, but noted for reroll counter if that route is chosen)

### Raw-SQL enum creation in Alembic

**Source:** `server/alembic/versions/0002_...py` lines 58, 94 with docstring lines 82-92.

**Rule:** `op.execute("CREATE TYPE ... AS ENUM (...)")` instead of `sa.Enum()` inside `op.create_table()` — SQLAlchemy re-emits `CREATE TYPE` even with `create_type=False` under some migration-session conditions.

**Applied to:** `primary_skill_root` and `rating_level` enums in migration 0003.

### Fletcher voice contract (mandatory)

**Source:** `.planning/design/fletcher-identity.md` (per CONTEXT.md, RESEARCH.md, UI-SPEC.md all cite this).

**Rule:** every user-facing string in Phase 3 mobile code MUST match the voice contract. UI-SPEC.md sections §1–§10 lock every string. Do NOT paraphrase.

**Applied to:**
- `RatingPills.tsx` labels (UI-SPEC §5)
- `FletcherLoader.tsx` message rotation (UI-SPEC §3)
- `BreakdownErrorCard.tsx` heading + body + CTAs (UI-SPEC §7)
- `AlreadyRatedCard.tsx` per-rating acknowledgement (UI-SPEC §6)
- `SongOfDayCard.tsx` Fletcher line variants + re-roll button copy (UI-SPEC §1)
- `FromTheBankTag.tsx` chip labels (UI-SPEC §2)

**Zero-emoji rule:** grep-verified in Phase 2 across 6 modified/created files. Same check applies to Phase 3.

### Timer-race mitigation (T-02-04-03)

**Source:** `mobile/src/app/onboarding/preferences.tsx` line 118 (`return () => clearTimeout(t);`).

**Rule:** every `setTimeout` inside a `useEffect` MUST return a cleanup that calls `clearTimeout(t)` — prevents state updates after unmount.

**Applied to:**
- `FletcherLoader.tsx` — the extracted rotation timers (both 3000ms and 8000ms)
- `AlreadyRatedCard.tsx` — 2-second auto-navigate timer
- Rating pill "confirmed visual" 150ms scale animation (UI-SPEC §5) — if timer-based

### Expo v57 mandate (mobile/AGENTS.md)

**Rule:** consult `https://docs.expo.dev/versions/v57.0.0/` before writing any Expo API.

**Phase 3 note:** no new native modules planned (per `<additional_context>` — "Phase 2 dropped expo-crypto to save an EAS build. Phase 3 continues that discipline"). But any plan that touches `expo-router`, `expo-crypto`, `expo-status-bar`, etc. MUST include the v57 docs URL in its plan header.

---

## No Analog Found

Files with no close match in the codebase — planner MUST use RESEARCH.md patterns instead of a codebase analog:

| File | Role | Data Flow | Reason | RESEARCH.md reference |
|------|------|-----------|--------|-----------------------|
| `server/app/selectors/today_song.py` | selector (SQL executor) | CRUD (read, deterministic-per-day) | No existing per-user deterministic selector in the codebase — Phase 1 was `SELECT LIMIT 1`. The 75/25 CTE with `setseed(hashtext(user_id\|\|local_day))` is net-new. | RESEARCH §4 Pattern 2 lines 338-403 (full CTE draft) |
| Song catalog seed authorship (10 hand-curated songs, embedded in migration 0003) | seed data | schema | Phase 2's system-user backfill was 1 row; Phase 3 seeds 10 songs with difficulty tags. Curation is content, not code — no existing analog. | RESEARCH §Summary "hand-curate 10 canonical songs" list + `<specifics>` line 156 |

**Both are covered in detail in RESEARCH.md** and RESEARCH.md is the authoritative source. The planner should copy the CTE draft verbatim into `server/app/selectors/today_song.py` and the seed list verbatim into the migration's `INSERT ... VALUES` block.

---

## Metadata

**Analog search scope:**
- `server/app/api/v1/*.py`
- `server/app/models/*.py`
- `server/app/ai/*.py`
- `server/alembic/versions/*.py`
- `mobile/src/api/*.ts`
- `mobile/src/components/*.tsx`
- `mobile/src/app/(tabs)/*.tsx`
- `mobile/src/app/onboarding/*.tsx`
- `mobile/src/store/*.ts`

**Files read for pattern extraction (11):**
- `server/app/ai/onboarding.py` (137 lines, whole file)
- `server/app/ai/client.py` (42 lines, whole file)
- `server/app/api/v1/users.py` (391 lines, whole file — the master analog)
- `server/app/api/v1/song_of_day.py` (37 lines, whole file)
- `server/app/api/deps.py` (20 lines, whole file)
- `server/app/main.py` (52 lines, whole file)
- `server/app/models/db.py` (174 lines, whole file)
- `server/app/models/song.py` (85 lines, whole file)
- `server/app/models/user.py` (61 lines, whole file)
- `server/app/models/skill_node.py` (88 lines, whole file)
- `server/alembic/versions/0002_users_songs_categorization_skill_graph.py` (158 lines, whole file)
- `mobile/src/api/apiClient.ts` (44 lines, whole file)
- `mobile/src/api/users.ts` (104 lines, whole file)
- `mobile/src/api/songOfDay.ts` (30 lines, whole file)
- `mobile/src/app/(tabs)/index.tsx` (222 lines, whole file)
- `mobile/src/app/onboarding/preferences.tsx` (252 lines, whole file — the loader/timer/fail-open analog)
- `mobile/src/components/TabNotation.tsx` (110 lines, whole file)
- `mobile/src/components/RetentionFormatRadio.tsx` (101 lines, whole file — the rating-pill visual analog)
- `mobile/src/components/FletcherIntroCard.tsx` (138 lines, whole file)
- `mobile/src/store/uiStore.ts` (28 lines, whole file)

**Phase-summary context files read (3):**
- `.planning/phases/02-onboarding-skill-graph/02-01-SUMMARY.md`
- `.planning/phases/02-onboarding-skill-graph/02-03-SUMMARY.md`
- `.planning/phases/02-onboarding-skill-graph/02-04-SUMMARY.md`

**Planning-artifact source files read (3):**
- `.planning/phases/03-ai-teacher-song-of-the-day/03-CONTEXT.md`
- `.planning/phases/03-ai-teacher-song-of-the-day/03-RESEARCH.md`
- `.planning/phases/03-ai-teacher-song-of-the-day/03-UI-SPEC.md`

**Pattern extraction date:** 2026-07-29

*Phase: 3 — AI Teacher & Song of the Day*
*Downstream consumer: gsd-planner (writes per-plan action steps referencing the analog file + excerpt above)*
