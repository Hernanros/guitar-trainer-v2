"""FLE-54 Fix 2 — the pre-4.1 drills backfill.

Covers `app.services.breakdown_backfill`: which rows it claims as candidates, and
the guardrails on the regeneration path. Sonnet is patched out throughout — this
suite must never spend, so `run_technique_breakdown` is replaced at the module
boundary rather than the Anthropic client being faked.

The candidate query is the part most worth pinning. `jsonb_array_length` raises on
anything that is not a jsonb array, *including* the SQL NULL a missing key yields,
and a pre-4.1 breakdown has no `drills` key at all — which is precisely the row the
backfill exists to find. An earlier revision guarded that with COALESCE to a `[]`
literal and died on every run with "cannot get array length of a scalar". So the
three shapes below are not padding; they are the bug.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# No live key may leak into this suite.
os.environ.pop("ANTHROPIC_API_KEY", None)

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.breakdown import AIBreakdownError
from app.services import breakdown_backfill as bf


def _test_db_url() -> str:
    raw = os.environ.get(
        "DATABASE_URL", "postgresql://gt:devpass@localhost:5433/guitar_trainer"
    )
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return (
                raw
                if prefix == "postgresql+asyncpg://"
                else raw.replace(prefix, "postgresql+asyncpg://", 1)
            )
    return raw


def _make_session() -> AsyncSession:
    engine = create_async_engine(
        _test_db_url(), echo=False, pool_size=1, max_overflow=0
    )
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)()


PRE_DRILLS = bf.DRILLS_SHIPPED_AT - timedelta(days=2)
POST_DRILLS = bf.DRILLS_SHIPPED_AT + timedelta(days=2)


def _tab() -> dict:
    return {
        "measures": [
            {
                "beats": [{"notes": [{"string": 1, "fret": 0, "duration": "quarter"}]}],
                "time_signature": "4/4",
            }
        ],
        "tuning": ["E", "A", "D", "G", "B", "e"],
    }


def _breakdown(drills: object = "__omit__") -> dict:
    """A stored breakdown. `drills` omitted entirely reproduces the pre-4.1 shape."""
    out = {"tab": _tab(), "chords": [], "technique_notes": []}
    if drills != "__omit__":
        out["drills"] = drills
    return out


class _Fixture:
    """One user, their songs, and a skill mapped to each song, torn down after."""

    def __init__(self) -> None:
        self.user_id = uuid.uuid4()
        self.skill_id = uuid.uuid4()
        self.song_ids: list[int] = []

    async def add_song(
        self, title: str, breakdown: dict, generated_at: datetime | None, *, skills=True
    ) -> int:
        async with _make_session() as db:
            song_id = await db.scalar(
                text(
                    "INSERT INTO songs (title, artist, breakdown, created_at, user_id, "
                    "breakdown_generated_at) "
                    "VALUES (:t, 'Test Artist', CAST(:b AS jsonb), now(), :u, :g) "
                    "RETURNING id"
                ),
                {
                    "t": title,
                    "b": __import__("json").dumps(breakdown),
                    "u": self.user_id,
                    "g": generated_at,
                },
            )
            if skills:
                await db.execute(
                    text(
                        "INSERT INTO song_skills (song_id, skill_node_id, weight) "
                        "VALUES (:s, :k, 1.0)"
                    ),
                    {"s": song_id, "k": self.skill_id},
                )
            await db.commit()
        self.song_ids.append(song_id)
        return song_id

    async def setup(self) -> None:
        async with _make_session() as db:
            await db.execute(
                text(
                    "INSERT INTO users (id, preferences) VALUES (:u, '{}'::jsonb) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"u": self.user_id},
            )
            await db.execute(
                text(
                    "INSERT INTO skill_nodes (id, user_id, name, level, mastery, "
                    "created_at, updated_at) "
                    "VALUES (:k, :u, 'Blues Shuffle', 'leaf', 0.4, now(), now())"
                ),
                {"k": self.skill_id, "u": self.user_id},
            )
            await db.commit()

    async def teardown(self) -> None:
        async with _make_session() as db:
            for table, col in (
                ("song_skills", "song_id"),
                ("songs", "id"),
            ):
                await db.execute(
                    text(f"DELETE FROM {table} WHERE {col} = ANY(:ids)"),
                    {"ids": self.song_ids},
                )
            await db.execute(
                text("DELETE FROM governor_calls WHERE user_id = :u"), {"u": self.user_id}
            )
            await db.execute(
                text("DELETE FROM skill_nodes WHERE user_id = :u"), {"u": self.user_id}
            )
            await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": self.user_id})
            await db.commit()


@pytest.fixture
async def fx():
    f = _Fixture()
    await f.setup()
    try:
        yield f
    finally:
        await f.teardown()


# ---------------------------------------------------------------------------
# find_candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_drills_key_is_a_candidate_and_does_not_raise(fx):
    """The pre-4.1 shape: no `drills` key at all.

    This is the row the whole backfill exists for, and the one the naive
    `jsonb_array_length(coalesce(...))` predicate crashed on.
    """
    song_id = await fx.add_song("Lenny", _breakdown(), PRE_DRILLS)

    async with _make_session() as db:
        found = {c.song_id: c for c in await bf.find_candidates(db)}

    assert song_id in found
    assert found[song_id].reason == "pre_drills_schema"


@pytest.mark.asyncio
async def test_non_array_drills_value_does_not_raise(fx):
    """A malformed write — `drills` holding a scalar — is counted, not crashed on."""
    song_id = await fx.add_song("Malformed", _breakdown(drills="nope"), PRE_DRILLS)

    async with _make_session() as db:
        found = {c.song_id for c in await bf.find_candidates(db)}

    assert song_id in found


@pytest.mark.asyncio
async def test_row_with_drills_is_left_alone(fx):
    song_id = await fx.add_song(
        "Has Drills", _breakdown(drills=[{"name": "x"}]), PRE_DRILLS
    )

    async with _make_session() as db:
        found = {c.song_id for c in await bf.find_candidates(db)}

    assert song_id not in found


@pytest.mark.asyncio
async def test_post_drills_soft_fail_excluded_by_default_included_on_request(fx):
    """A post-4.1 empty `drills` is a generation outcome, not a schema gap."""
    song_id = await fx.add_song("Soft Fail", _breakdown(drills=[]), POST_DRILLS)

    async with _make_session() as db:
        default = {c.song_id for c in await bf.find_candidates(db)}
        opted_in = {
            c.song_id: c for c in await bf.find_candidates(db, include_soft_fail=True)
        }

    assert song_id not in default
    assert opted_in[song_id].reason == "soft_fail_empty"


@pytest.mark.asyncio
async def test_force_song_id_returns_a_row_that_already_has_drills(fx):
    """The single-song escape hatch ignores the drills state."""
    song_id = await fx.add_song(
        "Force Me", _breakdown(drills=[{"name": "x"}]), POST_DRILLS
    )

    async with _make_session() as db:
        forced = await bf.find_candidates(db, force_song_id=song_id)

    assert [c.song_id for c in forced] == [song_id]
    assert forced[0].reason == "forced"


@pytest.mark.asyncio
async def test_force_song_id_skips_a_song_with_no_cached_breakdown(fx):
    """Nothing cached means the normal cache-miss path already covers it."""
    song_id = await fx.add_song("Never Generated", _breakdown(), None)

    async with _make_session() as db:
        assert await bf.find_candidates(db, force_song_id=song_id) == []
        assert await bf.find_candidates(db, force_song_id=-1) == []


# ---------------------------------------------------------------------------
# regenerate_one
# ---------------------------------------------------------------------------


class _FakeDrill:
    def __init__(self, target_skill_temp_id: str, name: str = "Isolate the slide"):
        self.target_skill_temp_id = target_skill_temp_id
        self.name = name


class _FakeBreakdown:
    def __init__(self, drills: list[_FakeDrill]):
        self.drills = drills

    def model_dump(self) -> dict:
        return {
            "tab": _tab(),
            "chords": [],
            "technique_notes": [],
            "drills": [{"name": d.name} for d in self.drills],
        }


async def _stored_drills(song_id: int) -> object:
    async with _make_session() as db:
        return await db.scalar(
            text("SELECT breakdown -> 'drills' FROM songs WHERE id = :s"), {"s": song_id}
        )


@pytest.mark.asyncio
async def test_regenerate_writes_drills_and_clears_the_candidate(fx, monkeypatch):
    song_id = await fx.add_song("Lenny", _breakdown(), PRE_DRILLS)

    async def fake_run(title, artist, target_skills, user_level, *, db, user_id):
        return _FakeBreakdown([_FakeDrill(target_skills[0]["id"])])

    monkeypatch.setattr(bf, "run_technique_breakdown", fake_run)

    async with _make_session() as db:
        candidate = next(
            c for c in await bf.find_candidates(db) if c.song_id == song_id
        )
        result = await bf.regenerate_one(db, candidate)

    assert result.ok and result.drills_written == 1
    assert await _stored_drills(song_id) is not None

    async with _make_session() as db:
        assert song_id not in {c.song_id for c in await bf.find_candidates(db)}


@pytest.mark.asyncio
async def test_hallucinated_skill_ids_are_dropped_and_the_row_is_left_as_is(fx, monkeypatch):
    """Every drill pointing at a skill the song does not have leaves nothing to write.

    A drill-less rewrite is no better than the drill-less row already there, and
    rewriting would reset `breakdown_generated_at` and cost the user their place in
    the candidate list. So the old row stands.
    """
    song_id = await fx.add_song("Lenny", _breakdown(), PRE_DRILLS)
    before = await _stored_drills(song_id)

    async def fake_run(title, artist, target_skills, user_level, *, db, user_id):
        return _FakeBreakdown([_FakeDrill(str(uuid.uuid4()))])

    monkeypatch.setattr(bf, "run_technique_breakdown", fake_run)

    async with _make_session() as db:
        candidate = next(
            c for c in await bf.find_candidates(db) if c.song_id == song_id
        )
        result = await bf.regenerate_one(db, candidate)

    assert not result.ok
    assert "no drills" in result.detail
    assert await _stored_drills(song_id) == before


@pytest.mark.asyncio
async def test_song_with_no_target_skills_is_skipped_without_calling_sonnet(fx, monkeypatch):
    """Without target skills every drill would be dropped — so do not pay for the call."""
    song_id = await fx.add_song("Unmapped", _breakdown(), PRE_DRILLS, skills=False)

    called = False

    async def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Sonnet must not be called for an unmapped song")

    monkeypatch.setattr(bf, "run_technique_breakdown", fake_run)

    async with _make_session() as db:
        candidate = next(
            c for c in await bf.find_candidates(db) if c.song_id == song_id
        )
        result = await bf.regenerate_one(db, candidate)

    assert not called
    assert not result.ok
    assert "no target skills" in result.detail


@pytest.mark.asyncio
async def test_sonnet_failure_is_reported_not_raised(fx, monkeypatch):
    """One bad row must not abort a multi-row run."""
    song_id = await fx.add_song("Lenny", _breakdown(), PRE_DRILLS)

    async def fake_run(*args, **kwargs):
        raise AIBreakdownError("truncated")

    monkeypatch.setattr(bf, "run_technique_breakdown", fake_run)

    async with _make_session() as db:
        candidate = next(
            c for c in await bf.find_candidates(db) if c.song_id == song_id
        )
        result = await bf.regenerate_one(db, candidate)

    assert not result.ok
    assert "sonnet failed" in result.detail


# ---------------------------------------------------------------------------
# cost estimate
# ---------------------------------------------------------------------------


def test_estimate_scales_with_row_count():
    one = bf.estimate_cost_usd(1)
    assert one == pytest.approx(0.0858, abs=0.005)
    assert bf.estimate_cost_usd(10) == pytest.approx(one * 10)
    assert bf.estimate_cost_usd(0) == 0.0
