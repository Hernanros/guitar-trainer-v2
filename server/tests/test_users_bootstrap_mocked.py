"""Mocked-Sonnet integration test for POST /api/v1/users.

Runs WITHOUT a real ANTHROPIC_API_KEY — patches run_onboarding_parse at the
endpoint's import site (app.api.v1.users.run_onboarding_parse) so the live
Anthropic API is never called. Exercises the full HTTP → DB path including:

  - Full bootstrap (mode='full'): asserts 30 skill_nodes (6 root + 12 sub + 12 leaf),
    3 songs with correct categories, and song_skills junction rows.
  - Fail-open (mode='bootstrap'): monkeypatched AIParseError triggers SAVEPOINT rollback;
    asserts user row survives with onboarded_at set + exactly 6 root nodes.
  - Idempotency (mode='existing'): re-POST with same UUID returns existing graph without
    calling Sonnet a second time.

Per Revision F requirements: all 3 tests pass with pytest -x without ANTHROPIC_API_KEY.
"""
import os
import pytest
import pytest_asyncio
import asyncio
from typing import List
from uuid import uuid4

# Remove ANTHROPIC_API_KEY from env before any imports that might try to init the client.
os.environ.pop("ANTHROPIC_API_KEY", None)

from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func as sqlfunc, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.main import app
from app.models.skill_node import SonnetOnboardingOutput, SonnetSkillNodeProposal, SonnetSongProposal
from app.ai.onboarding import AIParseError, FIXED_ROOTS
from app.models.db import Base, SkillNode, Song, SongSkill, User


# ---------------------------------------------------------------------------
# Test database setup
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
    """Create a fresh AsyncSession against the test DB.

    Uses a fresh engine per call to avoid cross-test event loop reuse issues
    (asyncpg connection pools are bound to the event loop they were created in;
    pytest-asyncio 0.24 uses per-function event loops by default).
    """
    engine = create_async_engine(
        _make_test_db_url(),
        echo=False,
        pool_size=1,
        max_overflow=0,
    )
    return async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )()


# ---------------------------------------------------------------------------
# Canned Sonnet output
# ---------------------------------------------------------------------------

def _canned_output() -> SonnetOnboardingOutput:
    """Build a canned SonnetOnboardingOutput: 6 roots + 12 subs (2/root) + 12 leaves (1/sub).

    Every song references 2 real temp_ids from skill_graph. Cardinality:
      - skill_nodes: 30 total (6 root + 12 sub + 12 leaf)
      - songs: 3 (one per category)
      - song_skills: at least 2 per song = 6 minimum junction rows
    """
    skill_graph: List[SonnetSkillNodeProposal] = []

    for i, root_name in enumerate(FIXED_ROOTS):
        root_id = f"root-{i}"
        skill_graph.append(SonnetSkillNodeProposal(
            temp_id=root_id,
            name=root_name,
            level="root",
            parent_temp_id=None,
        ))
        for j in range(2):
            sub_id = f"sub-{i}-{j}"
            skill_graph.append(SonnetSkillNodeProposal(
                temp_id=sub_id,
                name=f"{root_name} Sub {j+1}",
                level="sub",
                parent_temp_id=root_id,
            ))
            leaf_id = f"leaf-{i}-{j}"
            skill_graph.append(SonnetSkillNodeProposal(
                temp_id=leaf_id,
                name=f"{root_name} Leaf {j+1}",
                level="leaf",
                parent_temp_id=sub_id,
                tempo_bin_low=80 + j * 10,
                tempo_bin_high=85 + j * 10,
            ))

    songs = [
        SonnetSongProposal(
            title="Sweet Home Chicago",
            artist="Robert Johnson",
            category="can_play",
            skill_temp_ids=["leaf-0-0", "leaf-1-0"],
            genre="Blues",
            difficulty="intermediate",
            bpm=112,
            key="E",
        ),
        SonnetSongProposal(
            title="Little Wing",
            artist="Jimi Hendrix",
            category="working_on",
            skill_temp_ids=["leaf-2-0", "leaf-3-0"],
            genre="Rock",
            difficulty="intermediate",
            bpm=70,
            key="Em",
        ),
        SonnetSongProposal(
            title="Eruption",
            artist="Van Halen",
            category="aspirational",
            skill_temp_ids=["leaf-4-0", "leaf-5-0"],
            genre="Rock",
            difficulty="advanced",
            bpm=150,
            key="E",
        ),
    ]

    return SonnetOnboardingOutput(songs=songs, skill_graph=skill_graph)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_sonnet_success(monkeypatch):
    """Monkeypatch run_onboarding_parse at the endpoint's import site to return canned output."""
    calls = {"count": 0}

    async def _fake(*args, **kwargs):
        calls["count"] += 1
        return _canned_output()

    monkeypatch.setattr("app.api.v1.users.run_onboarding_parse", _fake)
    return calls


@pytest_asyncio.fixture
async def seeded_canonicals():
    """Pre-seed canonical skill_nodes matching every sub/leaf name in _canned_output().

    Phase 4 (D-09/D-13/D-14, bd60c90) added a verifier pipeline with a fan-out cap of 10:
    any sub/leaf proposal with no matching canonical falls to the verifier, and only the
    first 10 verifier-eligible proposals per bootstrap run get processed — the rest are
    dropped to a curator queue rather than inserted as skill_nodes. _canned_output() has
    24 non-root proposals (12 sub + 12 leaf), so on a bare DB with no canonicals yet, 14
    of them would be dropped by that cap, which is dedicated behavior already covered by
    test_onboarding_verifier_pipeline.py::test_pipeline_caps_verifier_fanout_at_ten.
    Seeding an exact-name canonical for each one drives every proposal through the
    score>=85 auto-dedupe path instead, so this file stays focused on its own concern:
    the full HTTP -> DB persistence path for a request with realistic (already-dedup'd)
    proposals, the way it behaves once the shared taxonomy is no longer empty.
    """
    seeded: list[tuple[str, str]] = []
    async with _make_session() as db:
        for i, root_name in enumerate(FIXED_ROOTS):
            for j in range(2):
                canonical_user_id = str(uuid4())
                await db.execute(text(
                    "INSERT INTO users (id, preferences) VALUES (:uid, '{}'::jsonb) "
                    "ON CONFLICT DO NOTHING"
                ), {"uid": canonical_user_id})
                for level, label in (("sub", "Sub"), ("leaf", "Leaf")):
                    node_id = str(uuid4())
                    await db.execute(text(
                        "INSERT INTO skill_nodes (id, user_id, name, level, canonical_node_id, mastery) "
                        "VALUES (:id, :uid, :name, :level, :id, 0.0)"
                    ), {
                        "id": node_id,
                        "uid": canonical_user_id,
                        "name": f"{root_name} {label} {j+1}",
                        "level": level,
                    })
                    seeded.append((canonical_user_id, node_id))
        await db.commit()

    yield

    async with _make_session() as db:
        for canonical_user_id, node_id in seeded:
            await db.execute(text("DELETE FROM skill_nodes WHERE id = :id"), {"id": node_id})
        for canonical_user_id in {u for u, _ in seeded}:
            await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": canonical_user_id})
        await db.commit()


@pytest.fixture
def mock_sonnet_fail(monkeypatch):
    """Monkeypatch run_onboarding_parse to raise AIParseError (simulates Sonnet failure)."""
    async def _raise(*args, **kwargs):
        raise AIParseError("simulated Sonnet failure for testing")

    monkeypatch.setattr("app.api.v1.users.run_onboarding_parse", _raise)


def _bootstrap_body(user_id: str) -> dict:
    return {
        "user_id": user_id,
        "songs": {
            "can_play": ["Sweet Home Chicago", "Blackbird"],
            "working_on": ["Little Wing"],
            "aspirational": ["Eruption"],
        },
        "preferences": {
            "session_length_min": 30,
            "retention_format": "streak",
        },
        "raw_input": {
            "can_play": "Sweet Home Chicago, Blackbird",
            "working_on": "Little Wing",
            "aspirational": "Eruption",
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_bootstrap_persists_correctly(mock_sonnet_success, seeded_canonicals):
    """Full bootstrap with canned output: asserts 30 skill_nodes + 3 songs + correct song_skills."""
    user_id = str(uuid4())
    body = _bootstrap_body(user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/v1/users", json=body)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["mode"] == "full", f"Expected mode='full', got: {data['mode']}"
    assert len(data["nodes"]) == 30, f"Expected 30 nodes, got: {len(data['nodes'])}"

    # Verify DB state
    async with _make_session() as db:
        # skill_nodes by level
        level_counts = {}
        for level in ("root", "sub", "leaf"):
            count = (await db.execute(
                select(sqlfunc.count()).select_from(SkillNode).where(
                    SkillNode.user_id == user_id,
                    SkillNode.level == level,
                )
            )).scalar_one()
            level_counts[level] = count

        assert level_counts == {"root": 6, "sub": 12, "leaf": 12}, \
            f"Unexpected level counts: {level_counts}"

        # songs: 3 rows with correct categories
        songs = (await db.execute(
            select(Song).where(Song.user_id == user_id)
        )).scalars().all()
        assert len(songs) == 3, f"Expected 3 songs, got: {len(songs)}"
        categories = {s.category for s in songs}
        assert categories == {"can_play", "working_on", "aspirational"}, \
            f"Unexpected categories: {categories}"

        # song_skills: 6 junction rows (2 per song × 3 songs)
        song_ids = [s.id for s in songs]
        skill_count = (await db.execute(
            select(sqlfunc.count()).select_from(SongSkill).where(
                SongSkill.song_id.in_(song_ids)
            )
        )).scalar_one()
        # canned output has 2 skill_temp_ids per song = 6 total
        assert skill_count == 6, f"Expected 6 song_skills, got: {skill_count}"

        # Mastery is 0.0 except for leaves referenced by can_play/working_on songs,
        # which FLE-49 seeds from the onboarding split (can_play=0.50, working_on=0.30,
        # aspirational unseeded) so the catalog opens up on first login. See
        # test_onboarding_mastery_seed.py for dedicated coverage of the seeding rule.
        _seeded_mastery = {
            "Rhythm Leaf 1": 0.50,       # can_play: Sweet Home Chicago -> leaf-0-0
            "Lead Leaf 1": 0.50,         # can_play: Sweet Home Chicago -> leaf-1-0
            "Chord Voicings Leaf 1": 0.30,  # working_on: Little Wing -> leaf-2-0
            "Fingerstyle Leaf 1": 0.30,     # working_on: Little Wing -> leaf-3-0
        }
        nodes_check = (await db.execute(
            select(SkillNode).where(SkillNode.user_id == user_id)
        )).scalars().all()
        for node in nodes_check:
            expected = _seeded_mastery.get(node.name, 0.0)
            assert float(node.mastery) == expected, \
                f"Node '{node.name}' has mastery {node.mastery}, expected {expected}"

        # user row has onboarded_at set
        user_row = (await db.execute(
            select(User).where(User.id == user_id)
        )).scalar_one()
        assert user_row.onboarded_at is not None, "onboarded_at should be set"
        assert user_row.raw_onboarding_text is not None, "raw_onboarding_text should be set"

    # mock called exactly once
    assert mock_sonnet_success["count"] == 1, \
        f"Expected run_onboarding_parse called once, was: {mock_sonnet_success['count']}"

    # Cleanup
    async with _make_session() as db:
        await db.execute(text(
            f"DELETE FROM song_skills WHERE song_id IN "
            f"(SELECT id FROM songs WHERE user_id='{user_id}')"
        ))
        await db.execute(text(f"DELETE FROM songs WHERE user_id='{user_id}'"))
        await db.execute(text(f"DELETE FROM skill_nodes WHERE user_id='{user_id}'"))
        await db.execute(text(f"DELETE FROM users WHERE id='{user_id}'"))
        await db.commit()


@pytest.mark.asyncio
async def test_fail_open_savepoint_preserves_user_row(mock_sonnet_fail):
    """AIParseError triggers SAVEPOINT rollback; user row + 6 root nodes survive; no partial state."""
    user_id = str(uuid4())
    body = _bootstrap_body(user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/v1/users", json=body)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["mode"] == "bootstrap", f"Expected mode='bootstrap', got: {data['mode']}"
    assert len(data["nodes"]) == 6, f"Expected 6 root nodes, got: {len(data['nodes'])}"

    # Verify DB state: user row intact
    async with _make_session() as db:
        user_row = (await db.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        assert user_row is not None, "User row should exist after fail-open"
        assert user_row.onboarded_at is not None, "onboarded_at should be set"
        assert user_row.raw_onboarding_text is not None, "raw_onboarding_text should be set"
        assert user_row.preferences is not None, "preferences should be persisted"

        # Exactly 6 root-level nodes
        root_count = (await db.execute(
            select(sqlfunc.count()).select_from(SkillNode).where(
                SkillNode.user_id == user_id,
                SkillNode.level == "root",
            )
        )).scalar_one()
        assert root_count == 6, f"Expected 6 root nodes, got: {root_count}"

        # Zero non-root nodes (SAVEPOINT rolled back full persist)
        non_root_count = (await db.execute(
            select(sqlfunc.count()).select_from(SkillNode).where(
                SkillNode.user_id == user_id,
                SkillNode.level != "root",
            )
        )).scalar_one()
        assert non_root_count == 0, f"Expected 0 non-root nodes, got: {non_root_count}"

        # Zero orphaned songs
        song_count = (await db.execute(
            select(sqlfunc.count()).select_from(Song).where(Song.user_id == user_id)
        )).scalar_one()
        assert song_count == 0, f"Expected 0 songs (fail-open), got: {song_count}"

    # Cleanup
    async with _make_session() as db:
        await db.execute(text(f"DELETE FROM skill_nodes WHERE user_id='{user_id}'"))
        await db.execute(text(f"DELETE FROM users WHERE id='{user_id}'"))
        await db.commit()


@pytest.mark.asyncio
async def test_idempotent_re_post_returns_existing(mock_sonnet_success, seeded_canonicals):
    """Re-POST with same UUID returns mode='existing'; skill_nodes unchanged; Sonnet called once."""
    user_id = str(uuid4())
    body = _bootstrap_body(user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # First POST — full bootstrap
        resp1 = await client.post("/api/v1/users", json=body)
        assert resp1.status_code == 201, resp1.text
        data1 = resp1.json()
        assert data1["mode"] == "full"
        first_node_ids = {n["id"] for n in data1["nodes"]}

        # Second POST — idempotency guard should fire
        resp2 = await client.post("/api/v1/users", json=body)
        assert resp2.status_code == 201, resp2.text
        data2 = resp2.json()
        assert data2["mode"] == "existing", \
            f"Expected mode='existing' on re-POST, got: {data2['mode']}"

        # Same node IDs returned
        second_node_ids = {n["id"] for n in data2["nodes"]}
        assert first_node_ids == second_node_ids, \
            "Re-POST should return the original node IDs unchanged"

    # Verify DB: skill_nodes count unchanged after second POST
    async with _make_session() as db:
        count = (await db.execute(
            select(sqlfunc.count()).select_from(SkillNode).where(
                SkillNode.user_id == user_id
            )
        )).scalar_one()
        assert count == 30, f"Expected 30 nodes after idempotent re-POST, got: {count}"

    # run_onboarding_parse was called exactly ONCE (second POST short-circuited)
    assert mock_sonnet_success["count"] == 1, \
        f"run_onboarding_parse should have been called exactly once; was: {mock_sonnet_success['count']}"

    # Cleanup
    async with _make_session() as db:
        await db.execute(text(
            f"DELETE FROM song_skills WHERE song_id IN "
            f"(SELECT id FROM songs WHERE user_id='{user_id}')"
        ))
        await db.execute(text(f"DELETE FROM songs WHERE user_id='{user_id}'"))
        await db.execute(text(f"DELETE FROM skill_nodes WHERE user_id='{user_id}'"))
        await db.execute(text(f"DELETE FROM users WHERE id='{user_id}'"))
        await db.commit()
