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

Follow-up hotfix 2026-08-17 (mobile-crash-null-breakdown), since REVERTED:
The 33d78ac coerce-to-None behavior crashed the EAS iOS build 690bc876 (built
2026-08-16 09:21 UTC, BEFORE 33d78ac shipped) because
`mobile/src/app/breakdown/[songId].tsx` accessed song.breakdown.tab / .chords /
.technique_notes without null guards, so the validator was changed to coerce
placeholder JSONB to an empty-but-valid Breakdown shape. Grace-B (d42ae4c) and
3c67c77 shipped the mobile null guards on 2026-09-08 and Grace-E moved the coerce
target back to None — the load-bearing client contract that justified the empty
shape is gone, so the three tests asserting it were stale red until FLE-67
realigned them.

Second prod P0 2026-09-24 (FLE-67) — same endpoint, same validator, new shape:
Song 73 carried a bare `breakdown = '{}'`. It has no `placeholder` key, so the
`"placeholder" in v and "tab" not in v` guard did not fire and the empty dict
reached Breakdown — a 3-error ValidationError (tab, chords, technique_notes) and
an HTTP 500. FLE-54 (d5fdb79) had just made the day's song pick persistent, which
turned what used to be a self-healing blip (next GET recomputed a different song)
into a hard 500 pinned for that user for the whole local day, on the main screen.

Fix: the coercer's test is now POSITIVE — a dict is a Breakdown only if it carries
every required key, derived from the model via `_REQUIRED_BREAKDOWN_KEYS`. Every
partial or empty snapshot degrades to "not generated yet" instead of 500ing.

Test coverage:
- Happy-path regression: user songs with full metadata serialize + serve correctly.
- Backstop path: songs with NULL genre/difficulty/bpm/key serialize (do not 500).
- Backstop path: songs with placeholder `{"placeholder": ...}` breakdown serialize;
  the placeholder coerces to None; breakdown_available=False.
- FLE-67 endpoint regression: a song whose breakdown is `{}` serves 200 + null
  breakdown rather than 500.
- FLE-67 shape matrix: every incomplete snapshot flavor coerces to None, and a
  complete one (drills key absent — pre-4.1 rows) survives untouched.
- Contract regression: SonnetSongProposal now REQUIRES the four metadata fields —
  any test/mock that omits them fails Pydantic validation at construction time,
  which is exactly the guarantee we want (prevents future drift).

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
# FLE-67 (2026-09-24): the shape prod song 73 actually carried. No `placeholder`
# key, so the pre-fix `"placeholder" in v` coercer let it through to Breakdown and
# 500'd with three missing-field errors.
_EMPTY_BREAKDOWN_JSON = "{}"


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
      - breakdown Optional + placeholder coercion → None (Grace-E, 2026-09-08). The
        2026-08-17 hotfix coerced to an empty-but-valid Breakdown instead, as a
        bandaid for EAS build 690bc876's missing null guards; Grace-B (d42ae4c) and
        3c67c77 shipped those guards mobile-side, so the target moved back to None
        and the Optional[Breakdown] contract matches server truth again.
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
        # Placeholder breakdown coerces to None (Grace-E) — the mobile null guards
        # that the 2026-08-17 empty-shape bandaid existed for have shipped. Callers
        # consult breakdown_available for "is a real breakdown ready?".
        assert song["breakdown"] is None, (
            f"Placeholder breakdown JSONB must coerce to None so the "
            f"Optional[Breakdown] contract matches server truth. Got: "
            f"{song['breakdown']}"
        )
        # breakdown_available reflects breakdown_generated_at — still False.
        assert body["breakdown_available"] is False
    finally:
        await _cleanup_user(user_id)


# ---------------------------------------------------------------------------
# Test 2b — FLE-67: bare `{}` breakdown snapshot must serve 200, not 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_song_of_day_serves_song_with_empty_dict_breakdown():
    """Regression for the prod P0 of 2026-09-24 (FLE-67).

    Prod song 73 ("Cause We've Ended as Lovers") carried `breakdown = '{}'` — a
    bare empty JSONB with no `placeholder` key. The old coercer only nulled dicts
    that HAD a `placeholder` key, so `{}` fell through to Breakdown and 500'd
    GET /api/v1/song-of-day with exactly three missing-field errors (tab, chords,
    technique_notes).

    What made it a P0 rather than a blip: before FLE-54 the day's song was
    recomputed on every GET, so a corrupt pick self-healed on the next request.
    FLE-54 (d5fdb79) persists the pick, which froze the 500 in place for that
    user for the whole local day — on the app's main screen.

    The coercer now tests positively (does the dict carry every required
    Breakdown key?), so `{}` and any other partial snapshot degrade to
    "not generated yet" instead of failing the request.
    """
    user_id = await _seed_user_with_song(
        genre="Blues",
        difficulty="advanced",
        bpm=63,
        key="Dm",
        breakdown_json=_EMPTY_BREAKDOWN_JSON,
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
            f"A song whose persisted breakdown snapshot is a bare '{{}}' must serve "
            f"200 with breakdown=null, not 500. Before the FLE-67 fix this was a "
            f"ValidationError on breakdown.tab / .chords / .technique_notes. "
            f"Got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["song"]["breakdown"] is None, (
            f"An empty snapshot carries no renderable breakdown — it must surface "
            f"as null, the same state the client already renders for a song whose "
            f"breakdown is pending. Got: {body['song']['breakdown']}"
        )
        assert body["breakdown_available"] is False
        # Metadata is unaffected — only the breakdown field degrades.
        assert body["song"]["genre"] == "Blues"
        assert body["song"]["bpm"] == 63
    finally:
        await _cleanup_user(user_id)


# ---------------------------------------------------------------------------
# Test 3 — Backstop path: placeholder breakdown coercion works in isolation
# ---------------------------------------------------------------------------


def test_song_response_placeholder_breakdown_coerces_to_none():
    """Unit test — SongResponse's coercion validator maps a not-yet-generated JSONB
    snapshot to None so Optional[Breakdown] validation passes instead of 500ing.

    A real breakdown dict (with tab/chords/technique_notes) still parses as a full
    Breakdown. None passes through unchanged (Optional path).
    """
    # Placeholder → None (Grace-E, 2026-09-08).
    resp = SongResponse.model_validate({
        "id": 1, "title": "T", "artist": "A",
        "genre": None, "difficulty": None, "bpm": None, "key": None,
        "breakdown": {"placeholder": "Phase 3 will populate breakdown"},
    })
    assert resp.breakdown is None

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
# Test 3b — FLE-67: every incomplete snapshot shape degrades to None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "snapshot,label",
    [
        ({}, "bare empty dict — the exact prod song 73 shape"),
        ({"placeholder": "Phase 3 will populate breakdown"}, "onboarding marker"),
        ({"placeholder": "reset for tuning-awareness verify"}, "manual reset marker"),
        ({"tab": {"measures": [], "tuning": ["E"]}}, "tab only, no chords/notes"),
        ({"chords": [], "technique_notes": []}, "arrays only, no tab"),
        ({"drills": []}, "drills only — pre-4.1 partial"),
    ],
)
def test_incomplete_breakdown_snapshots_coerce_to_none(snapshot, label):
    """FLE-67 regression guard — the coercer tests POSITIVELY, not for a marker key.

    The bug was a coercer that only recognized one flavor of "not generated yet"
    (`"placeholder" in v`). Any other incomplete shape sailed past it into
    Breakdown and 500'd the endpoint. Enumerating the shapes here locks in the
    inverted test: a dict is a breakdown only if it carries EVERY required key.

    `{"drills": []}` is included deliberately — drills has a default_factory, so
    its presence alone must not make a snapshot look complete.
    """
    resp = SongResponse.model_validate({
        "id": 42, "title": "Partial Song", "artist": "Test Artist",
        "genre": None, "difficulty": None, "bpm": None, "key": None,
        "breakdown": snapshot,
    })
    assert resp.breakdown is None, (
        f"An incomplete breakdown snapshot ({label}) must coerce to None rather "
        f"than reach Breakdown validation and 500 the endpoint. Got: "
        f"{resp.breakdown}"
    )


def test_complete_breakdown_snapshot_survives_coercion():
    """The other half of the FLE-67 guard — tightening the coercer must not start
    nulling out real breakdowns.

    Pre-4.1 cached rows have no `drills` key at all; they are still complete,
    because Breakdown.drills carries a default_factory. Only tab/chords/
    technique_notes are load-bearing.
    """
    complete = {
        "tab": {"measures": [], "tuning": ["E", "A", "D", "G", "B", "e"]},
        "chords": [],
        "technique_notes": [],
    }
    resp = SongResponse.model_validate({
        "id": 43, "title": "Real Song", "artist": "Test Artist",
        "genre": "Rock", "difficulty": "intermediate", "bpm": 120, "key": "A",
        "breakdown": complete,
    })
    assert isinstance(resp.breakdown, Breakdown), (
        "A snapshot carrying tab + chords + technique_notes is a real breakdown "
        "and must survive coercion untouched, drills key or not."
    )
    assert resp.breakdown.drills == []
    assert resp.breakdown.tab.tuning == ["E", "A", "D", "G", "B", "e"]


def test_required_breakdown_keys_track_the_model():
    """Guard against the set desyncing from Breakdown.

    `_REQUIRED_BREAKDOWN_KEYS` is derived from the model rather than hardcoded, so
    adding a required field to Breakdown automatically tightens the completeness
    test. This asserts the derivation itself — that it picks up exactly the
    non-defaulted fields — so a future refactor to a literal set gets caught.
    """
    from app.models.song import _REQUIRED_BREAKDOWN_KEYS, is_renderable_breakdown

    assert _REQUIRED_BREAKDOWN_KEYS == {"tab", "chords", "technique_notes"}
    assert "drills" not in _REQUIRED_BREAKDOWN_KEYS, (
        "drills has a default_factory — requiring it would null out every "
        "pre-4.1 cached breakdown."
    )
    # The helper is the inverse of what the validator nulls out.
    assert is_renderable_breakdown(
        {"tab": {}, "chords": [], "technique_notes": []}
    ) is True
    assert is_renderable_breakdown({}) is False
    assert is_renderable_breakdown(None) is False


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
