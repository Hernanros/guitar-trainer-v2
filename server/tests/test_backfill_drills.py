"""Tests for scripts/backfill_drills.py — FLE-8 Task 3 backfill.

Requires the Postgres dev DB (same as test_alembic_0006.py) because the backfill's
correctness is mostly FK/constraint behaviour and cross-row dedup — a sqlite or mock
harness would pass while the real load failed.

Every test drives `backfill()` inside a session it ROLLS BACK, which is also the
production dry-run path: the function flushes but never commits, so the caller decides.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from scripts.backfill_drills import _parse_cutoff, backfill

pytestmark = pytest.mark.asyncio


def _make_test_db_url() -> str:
    raw = os.environ.get(
        "DATABASE_URL", "postgresql://gt:devpass@localhost:5433/guitar_trainer"
    )
    for prefix in ("postgresql+asyncpg://",):
        if raw.startswith(prefix):
            return raw
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    return raw


engine = create_async_engine(_make_test_db_url(), echo=False, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

PATCH_TIME = datetime(2026, 9, 16, 17, 43, 41, tzinfo=timezone.utc)
AFTER = PATCH_TIME + timedelta(hours=1)
BEFORE = PATCH_TIME - timedelta(days=30)


@pytest.fixture
async def db():
    """Function-scoped session. Each test seeds, asserts, then rolls everything back."""
    async with SessionFactory() as session:
        yield session
        await session.rollback()


def _drill(name: str, skill_id: str, **overrides) -> dict:
    """A well-formed cached-breakdown drill dict."""
    base = {
        "name": name,
        "target_skill_temp_id": skill_id,
        "song_specific": False,
        "what": f"Do the thing: {name}",
        "tab_snippet": {"strings": [], "tuning": "EADGBE"},
        "start_bpm": 60,
        "target_bpm": 90,
        "repetitions": 5,
        "success_criterion": "Clean at target tempo",
        "common_trap": "Rushing",
    }
    base.update(overrides)
    return base


async def _seed_user_and_skills(db: AsyncSession, n_skills: int = 1) -> tuple[str, list[str]]:
    user_id = str(uuid.uuid4())
    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:uid, '{}'::jsonb)"),
        {"uid": user_id},
    )
    skill_ids = []
    for i in range(n_skills):
        sid = str(uuid.uuid4())
        await db.execute(
            text(
                "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
                "VALUES (:sid, :uid, :nm, 'leaf', 0.3)"
            ),
            {"sid": sid, "uid": user_id, "nm": f"Leaf {i}"},
        )
        skill_ids.append(sid)
    await db.flush()
    return user_id, skill_ids


async def _seed_song(
    db: AsyncSession,
    user_id: str,
    drills: list[dict],
    *,
    generated_at: datetime | None = AFTER,
    title: str = "T",
) -> int:
    await db.execute(
        text(
            "INSERT INTO songs (title, artist, genre, difficulty, bpm, key, breakdown, "
            "                   user_id, category, breakdown_generated_at) "
            "VALUES (:t, 'A', 'Blues', 'intermediate', 100, 'E', "
            "        CAST(:bd AS jsonb), :uid, 'working_on', :gen)"
        ),
        {
            "t": title,
            "bd": __import__("json").dumps({"drills": drills}),
            "uid": user_id,
            "gen": generated_at,
        },
    )
    await db.flush()
    return (
        await db.execute(
            text("SELECT id FROM songs WHERE user_id = :uid ORDER BY id DESC LIMIT 1"),
            {"uid": user_id},
        )
    ).scalar_one()


async def _banked(db: AsyncSession, user_id: str) -> list[tuple]:
    rows = await db.execute(
        text(
            "SELECT name, skill_node_id, origin_song_id, origin_drill_index, dedupe_score "
            "FROM drills WHERE user_id = :uid ORDER BY origin_song_id, origin_drill_index"
        ),
        {"uid": user_id},
    )
    return list(rows.all())


# ---------------------------------------------------------------------------
# The quality gate — the reason this script needs a flag at all
# ---------------------------------------------------------------------------

async def test_cutoff_skips_pre_patch_breakdowns(db: AsyncSession):
    """Breakdowns generated before the prompt patch are NOT banked under a cutoff.

    This is the whole safety story: the Task 1 eval graded the pre-patch prompt as
    defective, so banking its output is banking known-bad drills.
    """
    user_id, (skill,) = await _seed_user_and_skills(db)
    await _seed_song(
        db, user_id, [_drill("Old bad drill", skill)], generated_at=BEFORE, title="Pre"
    )
    await _seed_song(
        db, user_id, [_drill("Alternate picking burst", skill)], generated_at=AFTER, title="Post"
    )

    counters = await backfill(db, user_id=uuid.UUID(user_id), cutoff=PATCH_TIME, apply=False, verbose=False)

    assert counters.songs_skipped_pre_patch == 1
    assert counters.inserted == 1
    banked = await _banked(db, user_id)
    assert [r[0] for r in banked] == ["Alternate picking burst"]


async def test_include_pre_patch_banks_everything(db: AsyncSession):
    """cutoff=None (the --include-pre-patch path) deliberately banks the old rows too."""
    user_id, (skill,) = await _seed_user_and_skills(db)
    await _seed_song(
        db, user_id, [_drill("Old bad drill", skill)], generated_at=BEFORE, title="Pre"
    )
    await _seed_song(
        db, user_id, [_drill("Alternate picking burst", skill)], generated_at=AFTER, title="Post"
    )

    counters = await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)

    assert counters.songs_skipped_pre_patch == 0
    assert counters.inserted == 2


def test_parse_cutoff_rejects_naive_timestamp():
    """A naive cutoff would blow up against timestamptz mid-run — reject it up front."""
    with pytest.raises(SystemExit) as exc:
        _parse_cutoff("2026-09-16T17:43:41")
    assert "timezone" in str(exc.value)

    parsed = _parse_cutoff("2026-09-16T17:43:41+03:00")
    assert parsed.tzinfo is not None


def test_parse_cutoff_rejects_garbage():
    with pytest.raises(SystemExit):
        _parse_cutoff("last tuesday")


# ---------------------------------------------------------------------------
# Dedup on write
# ---------------------------------------------------------------------------

async def test_duplicate_across_songs_collapses_within_one_run(db: AsyncSession):
    """A drill banked from song A is reused, not re-inserted, when song B repeats it.

    The in-run bank cache is what makes this work — dedup against only pre-existing
    rows would bank both copies.
    """
    user_id, (skill,) = await _seed_user_and_skills(db)
    await _seed_song(db, user_id, [_drill("Alternate picking burst", skill)], title="A")
    await _seed_song(
        db, user_id, [_drill("Alternate picking speed burst drill", skill)], title="B"
    )

    counters = await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)

    assert counters.inserted == 1
    assert counters.reused == 1
    assert len(await _banked(db, user_id)) == 1


async def test_distinct_drills_both_insert(db: AsyncSession):
    user_id, (skill,) = await _seed_user_and_skills(db)
    await _seed_song(
        db,
        user_id,
        [
            _drill("Alternate picking burst", skill),
            _drill("Fingerstyle thumb independence", skill),
        ],
    )

    counters = await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)

    assert counters.inserted == 2
    assert counters.reused == 0


async def test_same_name_different_skill_is_not_a_duplicate(db: AsyncSession):
    """Dedup is scoped to (user, skill): same name on two skills = two drills."""
    user_id, skills = await _seed_user_and_skills(db, n_skills=2)
    await _seed_song(
        db,
        user_id,
        [
            _drill("Isolate the slide", skills[0]),
            _drill("Isolate the slide", skills[1]),
        ],
    )

    counters = await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)

    assert counters.inserted == 2
    assert counters.reused == 0


async def test_uncertain_band_queues_and_writes_no_drill(db: AsyncSession):
    """70-84 writes a curator row and NO drill row — no LLM in the write path."""
    user_id, (skill,) = await _seed_user_and_skills(db)
    await _seed_song(db, user_id, [_drill("Isolate the slide and bend", skill)], title="A")
    await _seed_song(db, user_id, [_drill("Isolate the b3 to 3 slide", skill)], title="B")

    counters = await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)

    assert counters.inserted == 1
    assert counters.queued == 1
    assert len(await _banked(db, user_id)) == 1

    queued = (
        await db.execute(
            text(
                "SELECT proposed_name, fuzzy_score, status, payload "
                "FROM drill_dedupe_queue WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
    ).all()
    assert len(queued) == 1
    name, score, status, payload = queued[0]
    assert name == "Isolate the b3 to 3 slide"
    assert 70 <= score < 85
    assert status == "pending"
    # The payload carries provenance so approval can insert without regenerating.
    assert payload["origin_drill_index"] == 0
    assert payload["origin_song_id"] is not None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

async def test_second_run_banks_nothing(db: AsyncSession):
    """Re-running is a no-op: everything scores 100 against itself and reuses."""
    user_id, (skill,) = await _seed_user_and_skills(db)
    await _seed_song(
        db,
        user_id,
        [
            _drill("Alternate picking burst", skill),
            _drill("Fingerstyle thumb independence", skill),
        ],
    )

    first = await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)
    assert first.inserted == 2

    second = await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)
    assert second.inserted == 0
    assert second.reused == 2
    assert len(await _banked(db, user_id)) == 2


async def test_second_run_does_not_requeue_pending_proposal(db: AsyncSession):
    """A pending curator card is not duplicated by a re-run."""
    user_id, (skill,) = await _seed_user_and_skills(db)
    await _seed_song(db, user_id, [_drill("Isolate the slide and bend", skill)], title="A")
    await _seed_song(db, user_id, [_drill("Isolate the b3 to 3 slide", skill)], title="B")

    await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)
    second = await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)

    assert second.queued == 0
    assert second.queue_already_pending == 1
    count = (
        await db.execute(
            text("SELECT count(*) FROM drill_dedupe_queue WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# Bad input — the backfill must never abort the whole load over one bad row
# ---------------------------------------------------------------------------

async def test_hallucinated_skill_id_is_skipped_not_fatal(db: AsyncSession):
    """A skill id not owned by the user is skipped; the good drill still banks.

    The endpoint already drops these at generation time, but a node deleted since
    would leave a dangling id in the cache that the FK would reject mid-transaction.
    """
    user_id, (skill,) = await _seed_user_and_skills(db)
    await _seed_song(
        db,
        user_id,
        [
            _drill("Ghost skill drill", str(uuid.uuid4())),
            _drill("Alternate picking burst", skill),
        ],
    )

    counters = await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)

    assert counters.skipped_bad_skill_id == 1
    assert counters.inserted == 1


async def test_non_uuid_skill_id_is_skipped(db: AsyncSession):
    user_id, (skill,) = await _seed_user_and_skills(db)
    await _seed_song(db, user_id, [_drill("Legacy drill", "temp_skill_1")])

    counters = await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)

    assert counters.skipped_bad_skill_id == 1
    assert counters.inserted == 0


async def test_malformed_drills_are_skipped(db: AsyncSession):
    """Missing name or missing content fields — skipped, counted, not fatal."""
    user_id, (skill,) = await _seed_user_and_skills(db)
    no_start_bpm = _drill("Missing tempo", skill)
    del no_start_bpm["start_bpm"]
    await _seed_song(
        db,
        user_id,
        [
            {"target_skill_temp_id": skill},
            _drill("", skill),
            no_start_bpm,
            _drill("Alternate picking burst", skill),
        ],
    )

    counters = await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)

    assert counters.skipped_malformed == 3
    assert counters.inserted == 1


async def test_pre_41_breakdowns_without_drills_key_are_ignored(db: AsyncSession):
    """Old cached rows have no `drills` key, and soft-failed ones have `drills: []`."""
    user_id, _ = await _seed_user_and_skills(db)
    await db.execute(
        text(
            "INSERT INTO songs (title, artist, genre, difficulty, bpm, key, breakdown, "
            "                   user_id, category, breakdown_generated_at) "
            "VALUES ('Old', 'A', 'Blues', 'intermediate', 100, 'E', "
            "        '{\"tab\": {}}'::jsonb, :uid, 'working_on', :gen)"
        ),
        {"uid": user_id, "gen": AFTER},
    )
    await _seed_song(db, user_id, [], title="SoftFail")
    await db.flush()

    counters = await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)

    assert counters.songs_scanned == 0
    assert counters.drills_seen == 0
    assert counters.inserted == 0


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

async def test_banked_drill_records_origin_song_and_index(db: AsyncSession):
    """Provenance is advisory but must be recorded, and survives the song's deletion."""
    user_id, (skill,) = await _seed_user_and_skills(db)
    song_id = await _seed_song(
        db,
        user_id,
        [
            _drill("Alternate picking burst", skill),
            _drill("Fingerstyle thumb independence", skill),
        ],
    )

    await backfill(db, user_id=uuid.UUID(user_id), cutoff=None, apply=False, verbose=False)

    banked = await _banked(db, user_id)
    assert [(r[2], r[3]) for r in banked] == [(song_id, 0), (song_id, 1)]

    await db.execute(text("DELETE FROM songs WHERE id = :sid"), {"sid": song_id})
    await db.flush()
    survivors = await db.execute(
        text("SELECT origin_song_id FROM drills WHERE user_id = :uid"), {"uid": user_id}
    )
    assert [r[0] for r in survivors.all()] == [None, None]


async def test_songs_without_a_user_are_ignored(db: AsyncSession):
    """Seed/catalog rows have user_id NULL; a drill needs an owner, so they're skipped.

    The run below is genuinely unscoped (user_id=None), which walks every eligible
    song in the table — including whatever real rows other users/tests have already
    committed to the shared dev DB. So this asserts scoped outcomes only (nothing
    banked against the orphan song, or against the skill node this test owns), not
    counters.inserted == 0 globally: that global count is not this test's to own, and
    asserting it flakes on any unrelated committed data (see FLE-71).
    """
    user_id, (skill,) = await _seed_user_and_skills(db)
    await db.execute(
        text(
            "INSERT INTO songs (title, artist, genre, difficulty, bpm, key, breakdown, "
            "                   category, breakdown_generated_at) "
            "VALUES ('Seed', 'A', 'Blues', 'intermediate', 100, 'E', "
            "        CAST(:bd AS jsonb), 'working_on', :gen)"
        ),
        {"bd": __import__("json").dumps({"drills": [_drill("Orphan drill", skill)]}), "gen": AFTER},
    )
    await db.flush()
    orphan_id = (
        await db.execute(
            text("SELECT id FROM songs WHERE user_id IS NULL ORDER BY id DESC LIMIT 1")
        )
    ).scalar_one()

    # Unscoped run — the NULL-user song is in range and still must not be banked.
    await backfill(db, user_id=None, cutoff=None, apply=False, verbose=False)

    orphaned = (
        await db.execute(
            text("SELECT count(*) FROM drills WHERE origin_song_id = :sid"),
            {"sid": orphan_id},
        )
    ).scalar_one()
    assert orphaned == 0

    # skill is a fresh UUID minted by _seed_user_and_skills for this test alone, so a
    # non-empty result here can only be the orphan drill slipping through.
    banked_for_skill = (
        await db.execute(
            text("SELECT count(*) FROM drills WHERE skill_node_id = :sid"),
            {"sid": skill},
        )
    ).scalar_one()
    assert banked_for_skill == 0

    banked = await _banked(db, user_id)
    assert banked == []
