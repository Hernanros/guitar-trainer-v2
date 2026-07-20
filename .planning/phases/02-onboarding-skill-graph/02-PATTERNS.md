# Phase 2: Onboarding & Initial Skill Graph — Pattern Map

**Mapped:** 2026-07-20
**Files analyzed:** 24 new/modified files
**Analogs found:** 20 / 24 (4 flagged as new-territory)

---

## Reader notes (must-read before planning)

- **Mobile CLAUDE.md pointer** — `mobile/CLAUDE.md` re-exports `mobile/AGENTS.md`, which says: **"Expo HAS CHANGED — read the exact versioned docs at https://docs.expo.dev/versions/v57.0.0/ before writing any code."** The planner MUST include a doc-check step for every new Expo Router route, splash/onboarding pattern, and any Expo module usage. This is a project-level enforcement rule.
- **Fletcher voice contract** — All user-facing copy in Phase 2 is governed by `.planning/design/fletcher-identity.md`. Pattern to follow: **sharp diagnosis → specific next step → confidence signal.** Never sharp diagnosis alone; never empty praise. No emojis in Fletcher's voice. No exclamation points except at peak moments.
- **Deterministic writes principle** — Sonnet writes STRUCTURE (nodes + edges), never mastery numbers. All mastery starts at `0.0` after onboarding (D-11). Any migration/ORM/response schema that includes `mastery` must default to `0.0` and NOT be Sonnet-populated.
- **Multi-user seams live now** — Phase 1 shipped a single-row songs table. Phase 2 introduces `users` + `user_id` scoping. The sentinel system user UUID is `00000000-0000-0000-0000-000000000000` (per <specifics>).
- **First LLM call in codebase** — `server/app/ai/` has no analog. Flag as new-territory; Phase 4's cost governor will wrap this boundary later, so keep the module cleanly interceptable (single entry point per Sonnet call).

---

## File Classification

### Server-side files

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `server/app/models/db.py` (MOD — add User, SkillNode, Song upgraded, SongSkill) | model (ORM) | CRUD | `server/app/models/db.py` (existing `Song` class) | exact |
| `server/app/models/user.py` (NEW) | model (Pydantic) | request-response | `server/app/models/song.py` | exact |
| `server/app/models/skill_node.py` (NEW) | model (Pydantic) | request-response | `server/app/models/song.py` | exact |
| `server/app/models/song.py` (MOD — add `category` enum, `user_id`) | model (Pydantic) | request-response | itself (extend in place) | exact |
| `server/app/models/song_skill.py` (NEW) | model (Pydantic) | request-response | `server/app/models/song.py` | exact |
| `server/app/api/v1/users.py` (NEW — POST bootstrap, GET current, POST re-run) | controller (FastAPI router) | request-response + LLM call | `server/app/api/v1/song_of_day.py` | role-match |
| `server/app/api/v1/song_of_day.py` (MOD per D-14) | controller (FastAPI router) | request-response | itself (extend or leave) | exact |
| `server/alembic/versions/0002_users_songs_categorization_skill_graph.py` (NEW) | migration | schema-mutation + backfill | `server/alembic/versions/0001_initial_songs_table.py` | role-match (backfill is new) |
| `server/app/ai/__init__.py` (NEW) | AI module scaffold | LLM call | **NO ANALOG** | new-territory |
| `server/app/ai/client.py` (NEW — Sonnet client wrapper) | AI service | LLM call | **NO ANALOG** | new-territory |
| `server/app/ai/onboarding.py` (NEW — song-parse + skill-graph prompt) | AI service | LLM call w/ tool-use | **NO ANALOG** | new-territory |
| `server/app/main.py` (MOD — register users router) | app entry | wiring | itself (existing `include_router` call) | exact |
| `server/requirements.txt` (MOD — add `anthropic` SDK) | config | dependency list | itself | exact |

### Mobile-side files

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `mobile/src/app/_layout.tsx` (MOD — add onboarded-check redirect) | route (root layout) | routing | itself (existing PersistQueryClientProvider wrap) | exact |
| `mobile/src/app/onboarding/_layout.tsx` (NEW — wizard shell) | route (layout) | routing | `mobile/src/app/(tabs)/_layout.tsx` | role-match |
| `mobile/src/app/onboarding/index.tsx` (NEW — Welcome/Fletcher joke landing) | route (screen) | UI | `mobile/src/app/(tabs)/library.tsx` (placeholder pattern) | partial |
| `mobile/src/app/onboarding/play.tsx` (NEW) | route (screen) | UI + local state | `mobile/src/app/(tabs)/library.tsx` | partial |
| `mobile/src/app/onboarding/working-on.tsx` (NEW) | route (screen) | UI + local state | `mobile/src/app/(tabs)/library.tsx` | partial |
| `mobile/src/app/onboarding/aspire.tsx` (NEW) | route (screen) | UI + local state | `mobile/src/app/(tabs)/library.tsx` | partial |
| `mobile/src/app/onboarding/preferences.tsx` (NEW) | route (screen) | UI + local state + mutation trigger | `mobile/src/app/(tabs)/index.tsx` (loading/error states) | partial |
| `mobile/src/app/settings.tsx` (NEW — placement TBD by planner) | route (screen) | UI + mutation | `mobile/src/app/(tabs)/toolkit.tsx` (placeholder) | partial |
| `mobile/src/components/FletcherIntroCard.tsx` (NEW — reusable across 5 sections) | component | UI | **NO DIRECT ANALOG** (consult `fletcher-identity.md`) | new-territory |
| `mobile/src/components/SongInputArea.tsx` (NEW — multi-line free text) | component | UI + controlled state | `mobile/src/components/themed-text.tsx` + `themed-view.tsx` (primitives) | partial |
| `mobile/src/components/SessionLengthChips.tsx` (NEW — 15/30/45/60 chips) | component | UI + controlled state | `mobile/src/components/themed-text.tsx` (primitive) | partial |
| `mobile/src/components/RetentionFormatRadio.tsx` (NEW — 3 radio options) | component | UI + controlled state | `mobile/src/components/themed-text.tsx` (primitive) | partial |
| `mobile/src/api/mmkv.ts` (NEW — shared MMKV instance + user_id helpers) | utility | storage | `mobile/src/api/queryClient.ts` (MMKV createMMKV pattern) | role-match |
| `mobile/src/api/apiClient.ts` (NEW — fetch wrapper w/ X-User-ID header) | utility | request-response | `mobile/src/api/songOfDay.ts` (fetch pattern) | role-match |
| `mobile/src/api/users.ts` (NEW — useUser, useUserBootstrap, useSkillGraph hooks) | service (TanStack Query hooks) | request-response + mutation | `mobile/src/api/songOfDay.ts` | exact |
| `mobile/src/store/uiStore.ts` (MOD — wizard state slice) | store (Zustand) | UI-local state | itself (stub) + Zustand docs | partial |

---

## Pattern Assignments

### `server/app/models/db.py` (ORM — additions for User, SkillNode, SongSkill, Song upgrade)

**Analog:** existing `Song` class in the same file (`server/app/models/db.py` lines 1-29).

**Imports pattern to extend** (lines 4-6):
```python
from sqlalchemy import Integer, String, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
```

For Phase 2 additions, also import:
```python
from sqlalchemy import ForeignKey, Numeric, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
import uuid
```

**Core ORM pattern to copy** (existing `Song`, lines 13-29):
```python
class Song(Base):
    __tablename__ = "songs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # ...
    breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
```

**Pattern extensions required for Phase 2:**
- UUID primary keys for new tables: `id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)`
- FK columns: `user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)`
- Enum columns for `songs.category` and `skill_nodes.level`: `sa.Enum(...)` with a Python `enum.Enum` — declare enum classes at top of file.
- JSONB for `users.preferences` and `users.raw_onboarding_text` — same pattern as `songs.breakdown` (line 23).
- `updated_at` on `skill_nodes` — pair with `created_at`, use `onupdate=func.now()`.
- Nullable `Numeric` for `mastery` (default `0.0`) and `tempo_bin_low`/`tempo_bin_high` (int, nullable — only leaves carry them per D-09).
- Self-referential `parent_id` FK for `skill_nodes.parent_id` → `skill_nodes.id`, nullable (root nodes).

**Existing Song class needs upgrade (D-14):** add `user_id` (nullable to preserve seed row initially, then backfilled), `category` (enum: `can_play` | `working_on` | `aspirational`, nullable to accommodate the system-user seed).

---

### `server/app/models/user.py` (NEW — Pydantic)

**Analog:** `server/app/models/song.py`.

**Imports pattern** (`server/app/models/song.py` lines 5-6):
```python
from typing import Optional
from pydantic import BaseModel
```

Add for UUID + enum support:
```python
from typing import Optional, Literal
from uuid import UUID
from pydantic import BaseModel
```

**Nested-model pattern to copy** (`server/app/models/song.py` lines 40-46 for `Chord`, lines 54-61 for `Breakdown`):
```python
class Chord(BaseModel):
    """A chord diagram with all string positions."""
    name: str
    positions: list[ChordPosition]
    barre_fret: Optional[int] = None
    base_fret: int = 1
```

**`from_attributes` pattern for ORM→response conversion** (`server/app/models/song.py` lines 64-75):
```python
class SongResponse(BaseModel):
    """Top-level API response for GET /api/v1/song-of-day."""
    id: int
    title: str
    # ...
    model_config = {"from_attributes": True}
```

**Concrete shape for Phase 2 (from D-14 <specifics> + D-12/D-13):**
```python
class UserPreferences(BaseModel):
    session_length_min: int  # 15 | 30 | 45 | 60
    retention_format: Literal["streak", "weekly_digest", "monthly_milestone"] = "streak"

class UserBootstrapRequest(BaseModel):
    """POST /api/v1/users body (see D-14 <specifics>)."""
    user_id: UUID
    songs: dict  # {can_play: [str], working_on: [str], aspirational: [str]}
    preferences: UserPreferences
    raw_input: dict  # verbatim text from wizard, kept for fail-open (D-07)

class UserResponse(BaseModel):
    id: UUID
    preferences: UserPreferences
    onboarded_at: Optional[str]  # ISO timestamp; None until bootstrap completes
    model_config = {"from_attributes": True}
```

---

### `server/app/models/skill_node.py` (NEW — Pydantic)

**Analog:** `server/app/models/song.py`.

**Pattern to copy:** the same nested-BaseModel + `from_attributes=True` pattern shown above.

**Concrete shape (from D-06 + D-09):**
```python
from typing import Optional, Literal
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel

class SkillNodeResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    level: Literal["root", "sub", "leaf"]
    parent_id: Optional[UUID] = None
    tempo_bin_low: Optional[int] = None      # leaves only
    tempo_bin_high: Optional[int] = None     # leaves only
    mastery: Decimal = Decimal("0.0")        # deterministic-writes: always 0 at bootstrap (D-11)
    model_config = {"from_attributes": True}

class SkillGraphResponse(BaseModel):
    """GET /api/v1/users/{user_id}/skill-graph — full user graph."""
    nodes: list[SkillNodeResponse]
    # edges are implicit via parent_id; no separate edges list needed (tree not DAG per D-09)
```

**Sonnet-output shape (D-06) — this is the OUTPUT of the LLM tool-use call, INPUT to the endpoint's persist step:**
```python
class SonnetSkillNodeProposal(BaseModel):
    """What Sonnet returns per node — NO mastery field (per D-11)."""
    temp_id: str  # Sonnet-local id for edge references; server assigns real UUIDs
    name: str
    level: Literal["root", "sub", "leaf"]
    parent_temp_id: Optional[str] = None
    tempo_bin_low: Optional[int] = None
    tempo_bin_high: Optional[int] = None

class SonnetSongProposal(BaseModel):
    title: str
    artist: str
    category: Literal["can_play", "working_on", "aspirational"]
    skill_temp_ids: list[str]  # references SonnetSkillNodeProposal.temp_id

class SonnetOnboardingOutput(BaseModel):
    """Structured tool-use output from Sonnet — the contract for the AI module."""
    songs: list[SonnetSongProposal]
    skill_graph: list[SonnetSkillNodeProposal]
```

---

### `server/app/models/song.py` (MOD — extend)

**Extensions:**
- Add `category: Optional[Literal["can_play", "working_on", "aspirational"]] = None` to `SongResponse` (keep Optional for the system-user seed row).
- Add `user_id: Optional[UUID] = None` to `SongResponse` (nullable per D-14 step 1 during migration; can tighten to required in Phase 3).
- Consider a separate `SongCreate` model for the bootstrap flow to keep the response shape narrow.

**Preserve the `from_attributes=True` pattern (line 75).**

---

### `server/app/api/v1/users.py` (NEW — controller)

**Analog:** `server/app/api/v1/song_of_day.py` (the entire file).

**Imports pattern** (`song_of_day.py` lines 5-12):
```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.seed import seed_songs
from app.db.session import get_db
from app.models.db import Song
from app.models.song import SongResponse

router = APIRouter()
```

For Phase 2, add:
```python
from fastapi import APIRouter, Depends, HTTPException, Header
from uuid import UUID
from app.models.db import User, SkillNode, Song, SongSkill
from app.models.user import UserBootstrapRequest, UserResponse
from app.models.skill_node import SkillGraphResponse
from app.ai.onboarding import run_onboarding_parse  # new-territory module
```

**Endpoint pattern to copy** (`song_of_day.py` lines 17-36):
```python
@router.get("/song-of-day", response_model=SongResponse)
async def get_song_of_day(db: AsyncSession = Depends(get_db)) -> SongResponse:
    result = await db.execute(select(Song).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        # ... fallback
    if row is None:
        raise HTTPException(status_code=404, detail="No song found.")
    return SongResponse.model_validate(row)
```

**Three endpoints for Phase 2:**

1. **`POST /api/v1/users` — bootstrap** (D-04 + D-07 + <specifics>):
   - Body: `UserBootstrapRequest`.
   - Steps: create `User` row (idempotent on `user_id`), call `run_onboarding_parse(raw_input)` from the `ai/` module, persist songs + song_skills + skill_nodes atomically (single transaction — the `AsyncSession` from `get_db` gives you this).
   - Failure handling (D-07): one retry with widened timeout is handled inside the `ai/` module; if it re-raises, catch here, save `raw_input` to `users.raw_onboarding_text`, insert bootstrap graph (root domains only per D-08), set `onboarded_at = now()`, return `SkillGraphResponse` with the minimal graph.
   - Response: `SkillGraphResponse` (mirror what the client will cache).

2. **`GET /api/v1/users/{user_id}` — current state**:
   - Path param: `user_id: UUID`.
   - Return `UserResponse` (`from_attributes=True` handles the ORM→Pydantic conversion, same as `song_of_day.py` line 36).

3. **`POST /api/v1/users/{user_id}/re-run` — re-onboard** (from `## Claude's Discretion` "Settings re-run behavior"):
   - Wipes user's `songs` + `song_skills` + `skill_nodes` in one transaction.
   - Preserves `users.preferences` and `users.id`.
   - Nulls `users.onboarded_at`.
   - Same body shape as bootstrap; re-runs the Sonnet call.

**X-User-ID header pattern (new for Phase 2 per D-04):**
```python
async def get_user_id(x_user_id: UUID = Header(..., alias="X-User-ID")) -> UUID:
    """FastAPI dependency — reads UUID from X-User-ID header. Any well-formed UUID accepted (no verification per <Claude's Discretion>)."""
    return x_user_id
```
Register this as a reusable dependency (put it in `server/app/api/deps.py` — new file, or inline in `users.py` for now). The planner should decide placement; if inline, extract in Phase 3 when Song-of-Day also needs it.

**Error-handling pattern to copy** (`song_of_day.py` line 34): `raise HTTPException(status_code=..., detail=...)`. No global error middleware exists yet; keep with per-endpoint HTTPException.

---

### `server/app/api/v1/song_of_day.py` (MOD — per D-14 option choice)

**Planner decision required:** stays pointed at the system user (00000000-...-000) for Phase 2, OR refactors now to accept `X-User-ID` and fall back to system user.

**If refactoring now:** add the same `get_user_id` dependency from `users.py`, add a `.where(Song.user_id == user_id)` clause to the `select(Song)` on line 24, and if none found, fall back to `.where(Song.user_id == SYSTEM_USER_UUID)`.

**If leaving unchanged:** the trivial `SELECT LIMIT 1` still works because the seed row belongs to the system user and is the only row until users are created. Add a comment explaining Phase 3 will replace this.

---

### `server/alembic/versions/0002_users_songs_categorization_skill_graph.py` (NEW)

**Analog:** `server/alembic/versions/0001_initial_songs_table.py` (entire file).

**Imports + revision header pattern to copy** (`0001` lines 11-18):
```python
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
```

For `0002`:
```python
revision = "0002"
down_revision = "0001"
```

**`create_table` pattern to copy** (`0001` lines 21-38):
```python
def upgrade() -> None:
    op.create_table(
        "songs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(255), nullable=False),
        # ...
        sa.Column("breakdown", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
```

**Ordered steps for `0002`** (must be in this order to satisfy D-14):

1. **Create `users` table** (UUID PK, `preferences JSONB`, `raw_onboarding_text JSONB nullable`, `onboarded_at timestamptz nullable`, `created_at`).
2. **Create Postgres enums** for `songs.category` (`'can_play', 'working_on', 'aspirational'`) and `skill_nodes.level` (`'root', 'sub', 'leaf'`) — use `sa.Enum(..., name='song_category', create_type=True)` inside `op.create_table` or `op.execute("CREATE TYPE ...")` first. Alembic auto-emits the CREATE TYPE when the Enum column is added; verify by running `alembic upgrade head` locally.
3. **Add `user_id` (nullable) + `category` (nullable) columns to existing `songs` table** via `op.add_column`.
4. **Seed the system user** with `INSERT INTO users (id, preferences, onboarded_at) VALUES ('00000000-0000-0000-0000-000000000000', '{}', now())` — use `op.execute(...)`. Idempotent: `ON CONFLICT (id) DO NOTHING`.
5. **Backfill existing Sweet Home Chicago row**: `op.execute("UPDATE songs SET user_id = '00000000-...-000', category = 'can_play' WHERE user_id IS NULL")`. Note: `category` for the seed is a Claude-discretion call — `'can_play'` is a reasonable default; the planner may prefer another value.
6. **Create `song_skills` junction** (`song_id FK → songs.id`, `skill_node_id FK → skill_nodes.id`, `weight NUMERIC DEFAULT 1.0`, composite PK `(song_id, skill_node_id)`).
7. **Create `skill_nodes` table** with self-referential `parent_id` FK, `tempo_bin_low int nullable`, `tempo_bin_high int nullable`, `mastery numeric default 0.0`, `created_at`, `updated_at`.

**`downgrade()` pattern to copy** (`0001` lines 41-42): reverse everything (drop tables in reverse dependency order, drop enums with `op.execute("DROP TYPE ...")`, remove columns from `songs`).

**Idempotent seeding pattern reference** — see `server/app/db/seed.py` line 131 (`SELECT COUNT(*)` guard). For Alembic, prefer `ON CONFLICT` since migrations run under a transaction.

---

### `server/app/ai/__init__.py`, `server/app/ai/client.py`, `server/app/ai/onboarding.py` (NEW — first LLM call, NO ANALOG)

**Flag:** new-territory. No existing pattern in codebase. Planner should:

1. **Read** `.planning/phases/01-foundation-empty-loop/01-CONTEXT.md` (D-01, D-02, D-03) to understand the Pydantic-as-contract discipline — this extends into how the AI module returns typed data (Sonnet's tool-use output validates into `SonnetOnboardingOutput` from `skill_node.py`).
2. **Consult** the Anthropic Python SDK v0.x docs for `client.messages.create(...)` with `tools=[...]` for structured tool-use output. The SDK is not yet in `requirements.txt` — add `anthropic==<latest>` there.

**Module boundary discipline (from <specifics> — Phase 4 cost governor hook):**
Keep a single top-level function per user-facing operation:
```python
# server/app/ai/onboarding.py
async def run_onboarding_parse(
    raw_input: dict,
    *,
    timeout_seconds: float = 30.0,
) -> SonnetOnboardingOutput:
    """
    Parse raw onboarding text -> canonicalized songs + initial skill graph.
    Called from POST /api/v1/users.

    Failure behavior (D-07): one retry with widened timeout (60s), then re-raise.
    Caller catches and applies fail-open (raw_input stored + bootstrap graph).

    Phase 4 hook: this function is the SINGLE interception point for the cost governor.
    Do not sprinkle client.messages.create() calls elsewhere in the codebase.
    """
    ...
```

**Configuration pattern (mirror `session.py`'s env-var reading — lines 19-35):**
```python
# server/app/ai/client.py
import os
from anthropic import AsyncAnthropic

def _get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. "
            "Set it before starting the server (Railway variable or local .env)."
        )
    return key

client = AsyncAnthropic(api_key=_get_api_key())
SONNET_MODEL = "claude-sonnet-4-6-20260101"  # planner verifies the exact model string
```

**Prompt-shape guidance (from <specifics>):**
- Include the fixed root taxonomy (D-08: Rhythm, Lead, Chord Voicings, Fingerstyle, Music Theory, Timing) inline in the system prompt as a hard constraint.
- Sonnet must never invent root domains; sub-domains + leaves only.
- Include example output for one song (few-shot).
- Explicit instruction: mastery values are always 0 (redundant with the tool-use schema which omits `mastery`).
- Use Anthropic `tools=[{name, description, input_schema}]` with `input_schema` derived from `SonnetOnboardingOutput.model_json_schema()`. Force tool use with `tool_choice={"type": "tool", "name": "..."}`.

**No test file analog exists** (Phase 1 has no tests). Planner may choose to add a basic mocked-response test at `server/tests/ai/test_onboarding.py` — flag as discretion.

---

### `server/app/main.py` (MOD — register users router)

**Analog:** itself (`server/app/main.py` line 34): `app.include_router(song_router, prefix="/api/v1")`.

**Pattern to copy** — add one line under the existing include:
```python
from app.api.v1.users import router as users_router
app.include_router(users_router, prefix="/api/v1")
```

**CORS note (line 30):** currently `allow_methods=["GET"]`. Phase 2 needs POST for `/api/v1/users` and re-run endpoints. Update to `allow_methods=["GET", "POST"]` (or `["*"]` — planner decides, but the current file explicitly restricts to GET).

---

### `server/requirements.txt` (MOD)

Add:
```
anthropic==<latest-verify-during-plan>
```

Existing pattern (from `requirements.txt`): pinned versions. Do not use `^` or `~` — the file uses `==` exclusively.

---

## Mobile-side pattern assignments

### `mobile/src/app/_layout.tsx` (MOD — add onboarded-check redirect)

**Analog:** itself (entire file, 19 lines).

**Existing structure to preserve:**
```typescript
import { Stack } from 'expo-router';
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client';
import { queryClient, mmkvPersister } from '../api/queryClient';

export default function RootLayout() {
  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{ persister: mmkvPersister }}
    >
      <Stack>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      </Stack>
    </PersistQueryClientProvider>
  );
}
```

**Modifications for Phase 2:**
- Import the shared MMKV instance from the new `mobile/src/api/mmkv.ts` and read `onboarded_at`.
- If null → render `<Redirect href="/onboarding" />` (Expo Router primitive).
- Register the new route group: add `<Stack.Screen name="onboarding" options={{ headerShown: false }} />`.
- If a `settings` route is added at root level (not inside `(tabs)`), register it too.

**Expo Router redirect pattern (planner must verify against Expo v57 docs per `mobile/AGENTS.md`):**
Recommended: use `expo-router`'s `<Redirect>` inside the layout render tree, or a `useEffect` + `router.replace('/onboarding')`. The AGENTS.md pointer requires verifying which API v57 currently ships.

---

### `mobile/src/app/onboarding/_layout.tsx` (NEW — wizard shell)

**Analog:** `mobile/src/app/(tabs)/_layout.tsx` (routing pattern) — but the wizard is a **Stack**, not `Tabs`.

**Structural pattern to adapt (from `(tabs)/_layout.tsx`):**
```typescript
import { Tabs } from 'expo-router';
// ...
export default function TabLayout() {
  return (
    <Tabs screenOptions={{ tabBarActiveTintColor: '#E07B39' }}>
      <Tabs.Screen name="index" options={{ title: 'Today', ... }} />
      <Tabs.Screen name="library" options={{ title: 'Library', ... }} />
      <Tabs.Screen name="toolkit" options={{ title: 'Toolkit', ... }} />
    </Tabs>
  );
}
```

For onboarding, use `Stack` (not `Tabs`) and hide the header — the FletcherIntroCard is the header:
```typescript
import { Stack } from 'expo-router';
export default function OnboardingLayout() {
  return (
    <Stack screenOptions={{ headerShown: false, gestureEnabled: false }}>
      <Stack.Screen name="index" />       {/* Welcome */}
      <Stack.Screen name="play" />
      <Stack.Screen name="working-on" />
      <Stack.Screen name="aspire" />
      <Stack.Screen name="preferences" />
    </Stack>
  );
}
```

**Alternative to consider (from <code_context> Integration Points):** single `[step].tsx` dynamic route. Planner picks; the 5-file explicit version is closer to the existing `(tabs)/` convention and easier to reason about.

**Color reference from existing code:** `#E07B39` (existing `(tabs)/_layout.tsx` line 10) is the app's accent. Reuse for progress dots.

---

### `mobile/src/app/onboarding/index.tsx` (Welcome — Fletcher joke landing)

**Analog for the shell:** `mobile/src/app/(tabs)/library.tsx` (simple centered-View pattern, 34 lines).

**Structural pattern to copy:**
```typescript
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

export default function LibraryScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.text}>Library — Coming soon</Text>
      <Text style={styles.subtext}>Browse and manage your tracked songs.</Text>
    </View>
  );
}
```

**For Welcome specifically:** render the new `FletcherIntroCard` component. Copy from `fletcher-identity.md` lines 87-93:
> "Meet Fletcher. He's the teacher Fletcher should have been." + Continue button. Character portrait deferred to UI-phase.

**Navigation to next step:** use `router.push('/onboarding/play')` (from `expo-router`).

---

### `mobile/src/app/onboarding/{play,working-on,aspire,preferences}.tsx` (NEW — section screens)

**Pattern per section:**
1. Render `<FletcherIntroCard heading="..." body="..." />` (see below).
2. Render the section-specific input component (`<SongInputArea />`, `<SessionLengthChips />`, `<RetentionFormatRadio />`).
3. "Next" button — writes current-section text/state to MMKV (via `mobile/src/api/mmkv.ts`) and calls `router.push('/onboarding/{next-section}')`.
4. On `preferences.tsx` "Complete" tap: call `useUserBootstrap.mutate({ ... })` (from `mobile/src/api/users.ts`), show Fletcher-voiced loader (see <specifics>: "Fletcher is listening..." → "Working on your first lesson plan..." → "Almost there..."), then `router.replace('/(tabs)')`.

**Loading-state pattern to copy** — from `mobile/src/app/(tabs)/index.tsx` lines 24-31:
```typescript
if (isLoading) {
  return (
    <View style={styles.center}>
      <ActivityIndicator size="large" color="#E07B39" />
      <Text style={styles.loadingText}>Loading...</Text>
    </View>
  );
}
```

**Error-state pattern to copy** — from `(tabs)/index.tsx` lines 33-42. Adapt copy to Fletcher voice: `"Fletcher lost the thread. Try that again."` + retry button.

**MMKV resume behavior (D-03):** On mount, each section reads its own MMKV slot; if populated, prefills the input. Welcome copy on re-open per D-03: `"Welcome back. You left off at [section]."`

---

### `mobile/src/app/settings.tsx` (NEW — placement TBD)

**Analog:** `mobile/src/app/(tabs)/toolkit.tsx` (placeholder pattern, 34 lines).

**Structural pattern to copy** (see toolkit.tsx above).

**Additions:**
- Session-preferences display (read from `useUser`), each preference editable (mutations not scoped to Phase 2 unless planner adds — currently only re-run onboarding is in scope per phase boundary).
- "Re-run onboarding" button — Fletcher-voiced confirm dialog: `"Start over? You'll keep your session preferences. Songs and skills reset."` (from D-14 Claude's Discretion). On confirm: call re-run mutation, then `router.replace('/onboarding')`.

**Placement decision (planner):** either `/(tabs)/toolkit/settings` (nested), `/(tabs)/settings` (fourth tab), or top-level `/settings` outside tabs (opened via a gear icon somewhere). No existing pattern to copy — UI-phase decision.

---

### `mobile/src/components/FletcherIntroCard.tsx` (NEW — NO DIRECT ANALOG)

**Flag:** new-territory. No existing component matches.

**Consult:** `.planning/design/fletcher-identity.md` (entire file — voice + character reference direction). Specifically:
- Lines 27-79 (voice & tone rules — "sharp diagnosis → specific next step").
- Line 89-91 (Welcome copy example).
- Lines 129-160+ (where the joke lands vs where warmth lands).

**Component shape (from <specifics>):**
Reusable across all 5 sections. Slots: image (optional; deferred to UI-phase), heading, body copy, primary CTA (default: "Continue"). Same component, different content per section.

**Primitives to build on:** `mobile/src/components/themed-text.tsx` and `themed-view.tsx` for theming consistency (though those pull from `Colors.light` / `Colors.dark` which currently use black/white — the existing Today screen bypasses this and hardcodes the `#1A1A1A` dark theme. Planner should decide: honor the theme system, or match the Today screen's hardcoded dark palette). The `mobile/src/app/(tabs)/index.tsx` file (lines 96-221) uses hardcoded dark theme (`#1A1A1A` bg, `#F5F5F5` text, `#E07B39` accent) — this is the current-shipping look. Recommended: match this palette for visual continuity between onboarding and post-onboarding Today tab.

**Suggested API:**
```typescript
export interface FletcherIntroCardProps {
  heading: string;                     // Section title, e.g. "What can you play?"
  body: string;                        // Fletcher-voiced intro copy
  cta?: string;                        // default "Continue"
  onNext: () => void;
  progress?: { current: number; total: number };  // 1..5 dots
  showPortrait?: boolean;              // portrait asset deferred to UI-phase
}
```

---

### `mobile/src/components/SongInputArea.tsx` (NEW — multi-line free text)

**Primitives:** React Native `TextInput` (multiline). No existing text-input component in the codebase.

**Style pattern to copy** — from `mobile/src/app/(tabs)/index.tsx` lines 185-192 (`techniqueCard` style has the "dark card with orange accent" pattern that fits the app aesthetic):
```typescript
techniqueCard: {
  backgroundColor: '#242424',
  borderRadius: 10,
  padding: 14,
  marginBottom: 10,
  borderLeftWidth: 3,
  borderLeftColor: '#E07B39',
},
```

**Behavior (from D-05 + <specifics>):**
- `multiline`, no autocomplete, `autoCapitalize="none"` for the song list.
- Placeholder-guided per category:
  - Play: `"Songs you can play. Type them out — Fletcher understands."`
  - Working on: `"What you're working on. Anything counts."`
  - Aspire: `"What are you chasing? Eruption? Purple Haze? Everything by Julian Lage? Type anything — Fletcher understands."` (from <specifics>)
- On change: debounced write-through to MMKV via `mobile/src/api/mmkv.ts` (D-03 buffered wizard state).

---

### `mobile/src/components/SessionLengthChips.tsx` (NEW)

**No analog.** Build with `TouchableOpacity` + `View` primitives. Style to match the existing `difficultyBadge` (from `(tabs)/index.tsx` lines 158-166) — small pill with orange border.

**Behavior (D-12):** single-select, no default (force intentional choice per D-12). Labels: `15 min` / `30 min` / `45 min` / `60 min` (space matters on chip widths per <specifics>).

---

### `mobile/src/components/RetentionFormatRadio.tsx` (NEW)

**No analog.** Radio group with Fletcher-voiced descriptions per D-13:
- `"Streak: I show up daily, count me."`
- `"Weekly digest: show me what I did on Sunday."`
- `"Monthly milestone: mark the big wins."`

Default to `streak` if user picks nothing (D-13).

---

### `mobile/src/api/mmkv.ts` (NEW — shared MMKV instance)

**Analog:** `mobile/src/api/queryClient.ts` lines 14-19 (MMKV instantiation pattern).

**Pattern to copy:**
```typescript
import { createMMKV } from 'react-native-mmkv';

// MMKV v4 API: createMMKV() replaces `new MMKV()` from v3.
const mmkv = createMMKV({ id: 'query-cache' });
```

**For Phase 2 add a second, separate MMKV instance for user identity + wizard state** (keeping it separate from the query-cache instance means the query-cache can be wiped without losing user identity):
```typescript
// mobile/src/api/mmkv.ts
import { createMMKV } from 'react-native-mmkv';
import * as Crypto from 'expo-crypto';  // v57 preferred over uuid npm package

// User + wizard MMKV (kept separate from query-cache).
export const userMmkv = createMMKV({ id: 'user-store' });

const USER_ID_KEY = 'user_id';
const ONBOARDED_AT_KEY = 'onboarded_at';

export function getOrCreateUserId(): string {
  const existing = userMmkv.getString(USER_ID_KEY);
  if (existing) return existing;
  const fresh = Crypto.randomUUID();
  userMmkv.set(USER_ID_KEY, fresh);
  return fresh;
}

export function getOnboardedAt(): string | null {
  return userMmkv.getString(ONBOARDED_AT_KEY) ?? null;
}

export function setOnboardedAt(iso: string): void {
  userMmkv.set(ONBOARDED_AT_KEY, iso);
}

export function clearOnboardedAt(): void {
  userMmkv.remove(ONBOARDED_AT_KEY);  // MMKV v4: remove() replaces delete()
}
```

**Note on `expo-crypto`:** already available in the Expo SDK 57 core; the planner should verify `Crypto.randomUUID()` is in v57 per `mobile/AGENTS.md`.

---

### `mobile/src/api/apiClient.ts` (NEW — fetch wrapper w/ X-User-ID)

**Analog:** `mobile/src/api/songOfDay.ts` lines 14-21 (fetch pattern).

**Existing pattern:**
```typescript
async function fetchSongOfDay(): Promise<SongResponse> {
  const url = `${process.env.EXPO_PUBLIC_API_URL}/api/v1/song-of-day`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} fetching song-of-day`);
  }
  return res.json() as Promise<SongResponse>;
}
```

**For Phase 2:**
```typescript
import { getOrCreateUserId } from './mmkv';

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const url = `${process.env.EXPO_PUBLIC_API_URL}${path}`;
  const userId = getOrCreateUserId();
  const res = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'X-User-ID': userId,
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} ${init.method ?? 'GET'} ${path}`);
  }
  return res.json() as Promise<T>;
}
```

**Refactor note:** `mobile/src/api/songOfDay.ts` (fetchSongOfDay) should be updated to route through `apiFetch` — even if the server doesn't require the header yet, sending it on every request is the D-04 contract. This keeps a single fetch entry point that Phase 4's cost governor client-side hooks can wrap.

---

### `mobile/src/api/users.ts` (NEW — TanStack Query hooks)

**Analog:** `mobile/src/api/songOfDay.ts` (entire file, 30 lines).

**Query hook pattern to copy** (`songOfDay.ts` lines 23-29):
```typescript
export function useSongOfDay() {
  return useQuery({
    queryKey: ['song-of-day'],
    queryFn: fetchSongOfDay,
  });
}
```

**Codegen-typed import pattern to copy** (`songOfDay.ts` lines 8, 12):
```typescript
import type { components } from './generated/schema';
export type SongResponse = components['schemas']['SongResponse'];
```

**For Phase 2:**
```typescript
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from './generated/schema';
import { apiFetch } from './apiClient';
import { getOrCreateUserId, setOnboardedAt } from './mmkv';

export type UserResponse = components['schemas']['UserResponse'];
export type SkillGraphResponse = components['schemas']['SkillGraphResponse'];
export type UserBootstrapRequest = components['schemas']['UserBootstrapRequest'];

export function useUser() {
  const userId = getOrCreateUserId();
  return useQuery({
    queryKey: ['user', userId],
    queryFn: () => apiFetch<UserResponse>(`/api/v1/users/${userId}`),
  });
}

export function useSkillGraph() {
  const userId = getOrCreateUserId();
  return useQuery({
    queryKey: ['skill-graph', userId],
    queryFn: () => apiFetch<SkillGraphResponse>(`/api/v1/users/${userId}/skill-graph`),
  });
}

export function useUserBootstrap() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UserBootstrapRequest) =>
      apiFetch<SkillGraphResponse>('/api/v1/users', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (graph) => {
      setOnboardedAt(new Date().toISOString());
      qc.setQueryData(['skill-graph', body.user_id], graph);
    },
  });
}
```

**Codegen dependency:** the types `UserResponse`, `SkillGraphResponse`, `UserBootstrapRequest` come from OpenAPI codegen — `npm run codegen:local` must be re-run after the server ships the new endpoints. Sequencing constraint for the planner: server endpoints ship first, codegen runs, then mobile hooks type-check.

---

### `mobile/src/store/uiStore.ts` (MOD — wizard state slice)

**Analog:** itself (14-line stub).

**Existing shape:**
```typescript
import { create } from 'zustand';

interface UIState {
  // Placeholder — no UI state needed in Phase 1.
}

export const useUIStore = create<UIState>()(() => ({}));
```

**Design decision (from <code_context>):** the Zustand comment says "wizard state can live here" but D-03 says wizard state is MMKV-only. Resolution: use Zustand ONLY for ephemeral in-memory state that shouldn't survive an app reload (e.g., "currently-showing Fletcher loader message index"). Persistent wizard state (per-section typed text, current section) is MMKV via `mobile/src/api/mmkv.ts`.

**Extension pattern:**
```typescript
interface UIState {
  loaderMessageIndex: number;                  // rotates "Fletcher is listening..." variants
  setLoaderMessageIndex: (n: number) => void;
}
```

Keep the store minimal in Phase 2 — most real state lives in TanStack Query cache or MMKV.

---

## Shared Patterns

### Async SQLAlchemy + get_db dependency
**Source:** `server/app/db/session.py` lines 53-56
**Apply to:** every new endpoint in `server/app/api/v1/users.py`
```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
```
Injection at endpoint: `db: AsyncSession = Depends(get_db)` (see `song_of_day.py` line 18).

### DATABASE_URL scheme rewrite
**Source:** `server/app/db/session.py` lines 19-35, `server/alembic/env.py` lines 25-38
**Apply to:** any new module that reads DATABASE_URL. Do NOT duplicate the rewrite; import from `session.py` if the AI module or a new script needs it.
Both `postgres://` and `postgresql://` schemes must be handled — Railway now injects the latter, but the earlier one still appears in some contexts.

### Pydantic v2 with `from_attributes=True`
**Source:** `server/app/models/song.py` line 75
**Apply to:** every new response model in `user.py`, `skill_node.py`, `song_skill.py`
```python
model_config = {"from_attributes": True}
```

### JSONB columns for structured data
**Source:** `server/app/models/db.py` line 23 + Alembic `0001` line 31
**Apply to:** `users.preferences`, `users.raw_onboarding_text`
```python
# ORM
from sqlalchemy.dialects.postgresql import JSONB
preferences: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default='{}')

# Alembic
from sqlalchemy.dialects.postgresql import JSONB
sa.Column("preferences", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
```

### FastAPI HTTPException error handling
**Source:** `server/app/api/v1/song_of_day.py` line 34
**Apply to:** every new endpoint. No global error middleware exists; raise `HTTPException(status_code=..., detail=...)` per case.

### TanStack Query hook + codegen types
**Source:** `mobile/src/api/songOfDay.ts` (entire file)
**Apply to:** all new hooks in `mobile/src/api/users.ts`
- Import types from `./generated/schema`.
- Read `process.env.EXPO_PUBLIC_API_URL` for the base URL.
- Throw on `!res.ok`.

### MMKV v4 API
**Source:** `mobile/src/api/queryClient.ts` lines 16, 32
**Apply to:** `mobile/src/api/mmkv.ts`
- `createMMKV()`, not `new MMKV()`.
- `.remove(key)`, not `.delete(key)`.

### Expo Router file-based routing
**Source:** `mobile/src/app/(tabs)/_layout.tsx`, `mobile/src/app/_layout.tsx`
**Apply to:** all new routes under `mobile/src/app/onboarding/`
- `_layout.tsx` per route group (Stack for onboarding, Tabs for existing tabs group).
- File name = URL segment.
- Verify against Expo Router v57 docs per `mobile/AGENTS.md` — the specific Redirect / router.replace() API surface may have shifted.

### Fletcher voice (all user-facing copy)
**Source:** `.planning/design/fletcher-identity.md` (entire file)
**Apply to:** `FletcherIntroCard` content, loader messages, error messages, settings confirm dialog, any Toast/Alert copy
**Signature pattern:** diagnosis → specific next step → confidence signal. No emojis. No generic praise. Terse and warm.

---

## No Analog Found

Files with no close match in the codebase (planner should treat these as new-territory + consult external references):

| File | Role | Data Flow | Reason | Reference to consult |
|---|---|---|---|---|
| `server/app/ai/client.py` | AI service | LLM call | First LLM touchpoint in the codebase | Anthropic Python SDK docs |
| `server/app/ai/onboarding.py` | AI service | LLM call with tool-use | First structured-output LLM call | Anthropic SDK `tools=` parameter + `SonnetOnboardingOutput` Pydantic schema |
| `mobile/src/components/FletcherIntroCard.tsx` | component | UI | First branded-voice component | `.planning/design/fletcher-identity.md` |
| `mobile/src/app/settings.tsx` | route | UI | No Settings screen exists yet | Placement decision + `(tabs)/toolkit.tsx` as skeleton |

---

## Metadata

**Analog search scope:** `server/app/`, `server/alembic/`, `mobile/src/`
**Files scanned:** 22 source files fully read; directory listings of `server/app/`, `server/alembic/versions/`, `mobile/src/app/`, `mobile/src/components/`, `mobile/src/api/`, `mobile/src/store/`, `mobile/src/hooks/`, `mobile/src/constants/`
**Design + planning docs consulted:** `02-CONTEXT.md`, `01-CONTEXT.md`, `01-01-SUMMARY.md`, `01-04-SUMMARY.md`, `PROJECT.md`, `fletcher-identity.md` (partial)
**Pattern extraction date:** 2026-07-20
