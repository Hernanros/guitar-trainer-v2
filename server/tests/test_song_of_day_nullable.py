"""Regression tests for prod P0 2026-08-16 — GET /song-of-day 500 on NULL metadata.

Bug: User `28c6b9ea-09e2-4e40-a362-910e10f0a0e5` completed onboarding cleanly, then
every GET /api/v1/song-of-day returned HTTP 500 with a 7-error Pydantic ValidationError:

    File "/app/app/api/v1/song_of_day.py", line 157, in get_song_of_day
        song=SongResponse.model_validate(row),
    pydantic_core._pydantic_core.ValidationError: 7 validation errors for SongResponse
      genre:                      expected str, got None
      difficulty:                 expected str, got None
      bpm:                        expected int, got None
      key:                        expected str, got None
      breakdown.tab:              field required (input: {'placeholder': '...'})
      breakdown.chords:           field required
      breakdown.technique_notes:  field required

Root cause (contract drift Phase 1 → Phase 2 → Phase 3):
- Phase 1 wrote `SongResponse` against a fully-populated seed row; strict types were fine.
- Phase 2 `_persist_bootstrap` inserted user-onboarded rows with hardcoded NULL
  genre/difficulty/bpm/key and a `{"placeholder": ...}` breakdown JSONB.
- Phase 3 `select_today_song` correctly picked those user rows — then serialization
  500'd inside SongResponse.model_validate() before `breakdown_available` could even
  be computed.

Fix (both layers per user-approved plan):
1. PRIMARY — Sonnet emits metadata at onboarding: `SonnetSongProposal` extended with
   required genre/difficulty/bpm/key; onboarding prompt updated; `_persist_bootstrap`
   persists what Sonnet returns instead of hardcoded NULL.
2. BACKSTOP — `SongResponse` metadata + breakdown loosened to Optional. Any non-Sonnet
   insert path (admin tools, migrations, seed backfills) no longer 500s the endpoint.
   Callers consult `TodaySongResponse.breakdown_available` (server-authoritative signal)
   rather than probing SongResponse.breakdown for None.

Follow-up hotfix 2026-08-17 (mobile-crash-null-breakdown):
The 33d78ac coerce-to-None behavior crashed the EAS iOS build 690bc876 (built
2026-08-16 09:21 UTC, BEFORE 33d78ac shipped) because
`mobile/src/app/breakdown/[songId].tsx` accesses song.breakdown.tab / .chords /
.technique_notes without null guards. The validator was updated to coerce placeholder
JSONB to an empty-but-valid Breakdown shape instead. Client-contract regression guard
`test_placeholder_breakdown_returns_mobile_safe_empty_shape` locks that shape in.

Test coverage:
- Happy-path regression: user songs with full metadata serialize + serve correctly.
- Backstop path: songs with NULL genre/difficulty/bpm/key serialize (do not 500).
- Backstop path: songs with placeholder `{"placeholder": ...}` breakdown serialize;
  the placeholder coerces to an empty Breakdown via SongResponse validator;
  breakdown_available=False (the mobile-safe contract until EAS ships null guards).
- Contract regression: SonnetSongProposal now REQUIRES the four metadata fields —
  any test/mock that omits them fails Pydantic validation at construction time,
  which is exactly the guarantee we want (prevents future drift).
- Mobile-safe shape regression: explicit assertion of the empty-Breakdown fields the
  current EAS build depends on (empty .map()-friendly arrays + standard tuning).

These tests exercise SongResponse.model_validate directly (fast, no DB required) plus
end-to-end via GET /api/v1/song-of-day against a real Postgres (proves the endpoint
serialization path doesn't 500).

Test DB pattern mirrors test_today_song_selector.py — real Postgres, tear-down per test.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Remove ANTHROPIC_API_KEY so the app boots without live-API side-effects.
os.environ.pop("ANTHROPIC_API_KEY", None)

from app.main import app
from app.models.song import Breakdown, SongResponse
from app.models.skill_node import SonnetSongProposal
from app.api.v1.users import _coalesce_song_proposals


# ---------------------------------------------------------------------------
# Test DB helpers (mirror test_today_song_selector.py pattern)
# ---------------------------------------------------------------------------

def _make_test_db_url() -> str:
    raw = os.environ.get(
        "DATABASE_URL",
        "postgresql://gt:devpass@localhost:5433/guitar_trainer",
    )
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    return raw


def _make_session() -> AsyncSession:
    engine = create_async_engine(
        _make_test_db_url(), echo=False, pool_size=1, max_overflow=0
    )
    return async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )()


async def _cleanup_user(user_id: str) -> None:
    async with _make_session() as db:
        await db.execute(
            text("DELETE FROM user_sessions WHERE user_id = :uid"), {"uid": user_id}
        )
        await db.execute(
            text(
                "DELETE FROM song_skills WHERE song_id IN "
                "(SELECT id FROM songs WHERE user_id = :uid)"
            ),
            {"uid": user_id},
        )
        await db.execute(text("DELETE FROM songs WHERE user_id = :uid"), {"uid": user_id})
        await db.execute(text("DELETE FROM skill_nodes WHERE user_id = :uid"), {"uid": user_id})
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        await db.commit()


# ---------------------------------------------------------------------------
# Fixtures — user with a working_on song at three different metadata states
# ---------------------------------------------------------------------------

async def _seed_user_with_song(
    *,
    genre: str | None,
    difficulty: str | None,
    bpm: int | None,
    key: str | None,
    breakdown_json: str,
) -> str:
    """Create a user + a leaf skill_node + a working_on song with the specified
    metadata. Returns the user_id (str) for the test to use.

    Uses raw SQL so we exercise the exact insert shape used by _persist_bootstrap
    (NULL columns via parameterized query) — not the ORM defaults.

    The song is `working_on` so the today-song selector deterministically picks it
    (no bank fallback / no seed catalog dependency).
    """
    user_id = str(uuid.uuid4())
    async with _make_session() as db:
        await db.execute(
            text(
                "INSERT INTO users (id, preferences) VALUES (:uid, '{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"uid": user_id},
        )
        # Leaf skill_node so the selector has a player_level to filter against.
        skill_id = str(uuid.uuid4())
        await db.execute(
            text(
                "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
                "VALUES (:sid, :uid, 'Test Leaf', 'leaf', 0.2)"
            ),
            {"sid": skill_id, "uid": user_id},
        )
        # Song row — insert with the requested metadata state (possibly NULL).
        result = await db.execute(
            text(
                "INSERT INTO songs "
                "  (title, artist, genre, difficulty, bpm, key, breakdown, user_id, category) "
                "VALUES "
                "  (:title, :artist, :genre, :difficulty, :bpm, :key, "
                "   CAST(:breakdown AS JSONB), :uid, 'working_on') "
                "RETURNING id"
            ),
            {
                "title": "Regression Song",
                "artist": "Test Artist",
                "genre": genre,
                "difficulty": difficulty,
                "bpm": bpm,
                "key": key,
                "breakdown": breakdown_json,
                "uid": user_id,
            },
        )
        song_id = result.scalar_one()
        await db.execute(
            text(
                "INSERT INTO song_skills (song_id, skill_node_id) VALUES (:sid, :skid)"
            ),
            {"sid": song_id, "skid": skill_id},
        )
        await db.commit()
    return user_id


_FULL_BREAKDOWN_JSON = (
    '{"tab":{"measures":[],"tuning":["E","A","D","G","B","e"]},'
    '"chords":[],"technique_notes":[]}'
)
_PLACEHOLDER_BREAKDOWN_JSON = '{"placeholder":"Phase 3 will populate breakdown"}'


# ---------------------------------------------------------------------------
# Test 1 — Happy path: full metadata serializes + serves 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_song_of_day_serves_song_with_full_metadata():
    """Happy-path regression for the exact prod bug.

    Pre-fix: even fully-populated songs traveled through SongResponse.model_validate
    which passed; but Sonnet-inserted rows had NULL columns → 500. This test now
    exercises the shape a NEW onboarding-via-Sonnet run would produce (all metadata
    populated). Endpoint must return 200 with the expected metadata echoed back.
    """
    user_id = await _seed_user_with_song(
        genre="Rock",
        difficulty="intermediate",
        bpm=70,
        key="Em",
        breakdown_json=_FULL_BREAKDOWN_JSON,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/v1/song-of-day",
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp.status_code == 200, (
            f"Fully-populated user song must serialize successfully. "
            f"Got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        song = body["song"]
        assert song["genre"] == "Rock"
        assert song["difficulty"] == "intermediate"
        assert song["bpm"] == 70
        assert song["key"] == "Em"
        # Full-breakdown row has tab/chords/technique_notes → serves as a real Breakdown.
        assert song["breakdown"] is not None
        assert "tab" in song["breakdown"]
        # breakdown_available reflects songs.breakdown_generated_at — the seed helper
        # doesn't set it, so this is expected to be False (real breakdown pipeline
        # sets breakdown_generated_at as a separate write).
        assert body["breakdown_available"] is False
    finally:
        await _cleanup_user(user_id)


# ---------------------------------------------------------------------------
# Test 2 — Backstop path: all metadata NULL, breakdown placeholder → still 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_song_of_day_serves_song_with_null_metadata_and_placeholder_breakdown():
    """The exact prod bug reproduction — before the SongResponse fix, this 500'd
    with a 7-error ValidationError. Now it must serve 200 with the missing fields
    surfaced as null in the JSON payload.

    This exercises both parts of the SongResponse loosening:
      - genre/difficulty/bpm/key Optional → null in JSON when the column is NULL
      - breakdown Optional + placeholder coercion → empty-but-valid Breakdown shape
        (mobile-safe contract per 2026-08-17 follow-up hotfix; EAS build 690bc876
        crashes on null breakdown). Once mobile ships graceful degradation on
        breakdown_available, the coerce target can move back to None.
    """
    user_id = await _seed_user_with_song(
        genre=None,
        difficulty=None,
        bpm=None,
        key=None,
        breakdown_json=_PLACEHOLDER_BREAKDOWN_JSON,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/v1/song-of-day",
                headers={"X-User-ID": user_id, "X-Timezone-Offset": "0"},
            )
        assert resp.status_code == 200, (
            f"User song with NULL metadata + placeholder breakdown must serialize "
            f"successfully (backstop path). Before the fix this was a 500 with a "
            f"7-error Pydantic ValidationError. Got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        song = body["song"]
        # Invariants — always present.
        assert song["title"] == "Regression Song"
        assert song["artist"] == "Test Artist"
        # Loosened fields — null in payload because DB column is NULL.
        assert song["genre"] is None
        assert song["difficulty"] is None
        assert song["bpm"] is None
        assert song["key"] is None
        # Placeholder breakdown coerces to the mobile-safe empty Breakdown shape
        # (not None) so EAS build 690bc876 can .map() over the arrays without
        # crashing. Callers still consult breakdown_available for "is a real
        # breakdown ready?" — see test_placeholder_breakdown_returns_mobile_safe_empty_shape
        # for the exact shape guarantee.
        assert song["breakdown"] is not None, (
            f"Placeholder breakdown JSONB must coerce to an empty-but-valid Breakdown "
            f"(not None) so mobile clients from EAS build 690bc876 don't crash on "
            f"song.breakdown.tab / .chords / .technique_notes access. Got: "
            f"{song['breakdown']}"
        )
        assert song["breakdown"]["tab"]["measures"] == []
        assert song["breakdown"]["chords"] == []
        assert song["breakdown"]["technique_notes"] == []
        # breakdown_available reflects breakdown_generated_at — still False.
        assert body["breakdown_available"] is False
    finally:
        await _cleanup_user(user_id)


# ---------------------------------------------------------------------------
# Test 3 — Backstop path: placeholder breakdown coercion works in isolation
# ---------------------------------------------------------------------------


def test_song_response_placeholder_breakdown_coerces_to_empty_breakdown():
    """Unit test — SongResponse's placeholder-coercion validator maps the JSONB
    marker `{"placeholder": ...}` to an empty-but-valid Breakdown so
    Optional[Breakdown] validation passes AND mobile clients from EAS build
    690bc876 can safely iterate the (empty) tab.measures / chords /
    technique_notes arrays without null-deref crashes.

    A real breakdown dict (with tab/chords/technique_notes) still parses as a full
    Breakdown. None passes through unchanged (Optional path).
    """
    # Placeholder → empty Breakdown (mobile-safe shape).
    resp = SongResponse.model_validate({
        "id": 1, "title": "T", "artist": "A",
        "genre": None, "difficulty": None, "bpm": None, "key": None,
        "breakdown": {"placeholder": "Phase 3 will populate breakdown"},
    })
    assert isinstance(resp.breakdown, Breakdown)
    assert resp.breakdown.tab.measures == []
    assert resp.breakdown.tab.tuning == ["E", "A", "D", "G", "B", "e"]
    assert resp.breakdown.chords == []
    assert resp.breakdown.technique_notes == []

    # None → None (Optional path, unchanged).
    resp = SongResponse.model_validate({
        "id": 2, "title": "T", "artist": "A",
        "genre": None, "difficulty": None, "bpm": None, "key": None,
        "breakdown": None,
    })
    assert resp.breakdown is None

    # Real breakdown → full Breakdown parses.
    resp = SongResponse.model_validate({
        "id": 3, "title": "T", "artist": "A",
        "genre": "Rock", "difficulty": "intermediate", "bpm": 100, "key": "E",
        "breakdown": {
            "tab": {"measures": [], "tuning": ["E", "A", "D", "G", "B", "e"]},
            "chords": [],
            "technique_notes": [],
        },
    })
    assert isinstance(resp.breakdown, Breakdown)
    assert resp.breakdown.tab.tuning == ["E", "A", "D", "G", "B", "e"]


# ---------------------------------------------------------------------------
# Test 3b — Client-contract regression: mobile-safe empty Breakdown shape
# ---------------------------------------------------------------------------


def test_placeholder_breakdown_returns_mobile_safe_empty_shape():
    """Client-contract regression guard for mobile-crash-null-breakdown (2026-08-17).

    The current EAS iOS build 690bc876 (built 2026-08-16 09:21 UTC, before 33d78ac's
    coerce-to-None change shipped) accesses song.breakdown.tab / .chords /
    .technique_notes without null guards in
    `mobile/src/app/breakdown/[songId].tsx:162,174,187`. Returning None from the
    placeholder coercer crashes the app to the iOS home screen the moment the
    breakdown screen mounts.

    This test locks the mobile-safe empty-Breakdown shape in place so any future
    edit that regresses the coercer back to None (or drops a required key from
    the empty shape) fails loudly with a clear message. Once mobile ships graceful
    degradation on TodaySongResponse.breakdown_available, this test can be flipped
    (or the coercer target moved back to None + the assertion relaxed) — but until
    that EAS build ships, this shape is a load-bearing client contract.
    """
    resp = SongResponse.model_validate({
        "id": 42, "title": "Placeholder Song", "artist": "Test Artist",
        "genre": None, "difficulty": None, "bpm": None, "key": None,
        "breakdown": {"placeholder": "Phase 3 will populate breakdown"},
    })

    assert resp.breakdown is not None, (
        "Placeholder coercion must NOT return None while EAS build 690bc876 is in "
        "the field — mobile crashes on song.breakdown.tab access. See "
        ".planning/debug/mobile-crash-null-breakdown.md."
    )
    assert isinstance(resp.breakdown, Breakdown)

    # tab must be a fully-valid Tab with an iterable (possibly empty) .measures list
    # so mobile's <TabNotation tab={song.breakdown.tab} /> doesn't null-deref.
    assert resp.breakdown.tab.measures == [], (
        "tab.measures must be an empty list (not None) — mobile TabNotation "
        "iterates measures without null guards."
    )
    assert resp.breakdown.tab.tuning == ["E", "A", "D", "G", "B", "e"], (
        "tab.tuning must be standard 6-string tuning so mobile's tab renderer "
        "has a valid string count."
    )

    # chords must be an empty list so .map() renders nothing without crashing.
    assert resp.breakdown.chords == [], (
        "chords must be an empty list (not None) — mobile's "
        "song.breakdown.chords.map((chord) => ...) crashes on null."
    )

    # technique_notes must be an empty list so .map() renders nothing without crashing.
    assert resp.breakdown.technique_notes == [], (
        "technique_notes must be an empty list (not None) — mobile's "
        "song.breakdown.technique_notes.map((note, i) => ...) crashes on null."
    )


# ---------------------------------------------------------------------------
# Test 4 — Contract regression: SonnetSongProposal now REQUIRES metadata fields
# ---------------------------------------------------------------------------


def test_sonnet_song_proposal_requires_metadata_fields():
    """Regression guard — any future test/mock that constructs SonnetSongProposal
    without the new metadata fields fails Pydantic validation immediately.

    This is the "loud fail" side of the fix: if someone regresses the Sonnet output
    schema back to (title, artist, category, skill_temp_ids) only, the onboarding
    tool_use will silently miss the metadata fields → we want ValidationError at
    construction, not silent NULL persistence.
    """
    from pydantic import ValidationError

    # Missing metadata → ValidationError with all 4 fields flagged.
    with pytest.raises(ValidationError) as exc_info:
        SonnetSongProposal(
            title="Little Wing",
            artist="Jimi Hendrix",
            category="working_on",
            skill_temp_ids=[],
            # NO genre/difficulty/bpm/key — must fail.
        )

    errors = exc_info.value.errors()
    missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
    assert missing_fields == {"genre", "difficulty", "bpm", "key"}, (
        f"Removing any of genre/difficulty/bpm/key from SonnetSongProposal must be "
        f"a Pydantic-level required-field failure. Missing set was: {missing_fields}"
    )

    # Difficulty is Literal — invalid values also raise.
    with pytest.raises(ValidationError):
        SonnetSongProposal(
            title="Song",
            artist="Artist",
            category="working_on",
            skill_temp_ids=[],
            genre="Rock",
            difficulty="pro",  # not in {"beginner","intermediate","advanced"}
            bpm=100,
            key="C",
        )

    # Fully-valid construction succeeds.
    prop = SonnetSongProposal(
        title="Little Wing",
        artist="Jimi Hendrix",
        category="working_on",
        skill_temp_ids=["leaf-1"],
        genre="Rock",
        difficulty="intermediate",
        bpm=70,
        key="Em",
    )
    assert prop.genre == "Rock"
    assert prop.difficulty == "intermediate"
    assert prop.bpm == 70
    assert prop.key == "Em"


# ---------------------------------------------------------------------------
# Test 5 — Regression: _coalesce_song_proposals preserves metadata (eacfcb9)
# ---------------------------------------------------------------------------


def test_coalesce_song_proposals_preserves_metadata_across_duplicates():
    """Regression guard for eacfcb9 (duplicate-song coalesce, prod bug 2026-08-16).

    The coalesce path was updated in this hotfix to propagate genre/difficulty/bpm/key
    into the merged SonnetSongProposal. Before this test existed, someone regressing
    the coalesce to drop metadata would silently reintroduce the P0 (merged proposal
    would fail SonnetSongProposal validation because required fields are absent).

    First-seen wins for metadata; category still follows _CATEGORY_PRIORITY.
    """
    props = [
        SonnetSongProposal(
            title="Lenny",
            artist="Stevie Ray Vaughan",
            category="aspirational",
            skill_temp_ids=["a"],
            genre="Blues",
            difficulty="advanced",
            bpm=85,
            key="Eb",
        ),
        SonnetSongProposal(
            title="Lenny",
            artist="Stevie Ray Vaughan",
            category="working_on",
            skill_temp_ids=["b"],
            genre="Blues",
            difficulty="advanced",
            bpm=85,
            key="Eb",
        ),
    ]
    out = _coalesce_song_proposals(props)
    assert len(out) == 1
    merged = out[0]
    # Category precedence still wins.
    assert merged.category == "working_on"
    # skill_temp_ids UNION preserved.
    assert merged.skill_temp_ids == ["a", "b"]
    # Metadata preserved (first-seen).
    assert merged.genre == "Blues"
    assert merged.difficulty == "advanced"
    assert merged.bpm == 85
    assert merged.key == "Eb"
