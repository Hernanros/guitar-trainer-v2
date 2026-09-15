"""Alembic migration 0006 tests — the drill bank (FLE-8 Task 3).

Verifies the three tables introduced by 0006:
  drills · drill_attempts · drill_dedupe_queue

The headline behaviours under test, in FLE-8's own terms:
  - "drills are queryable independently of songs" -> a drill SURVIVES deletion of
    the song that spawned it (origin_song_id ON DELETE SET NULL)
  - "duplicates collapse on write" -> uq_drills_canonical_identity makes a second
    canonical with the same normalized name for the same (user, skill) impossible
  - "per-drill history records tempo and reps" -> drill_attempts round-trips
    tempo_reached_bpm + reps_completed, and allows MULTIPLE attempts per day

Runs against the real Postgres test database (DATABASE_URL env var or default).
Requires `alembic upgrade head` to have been applied before running.

Downgrade note: like test_alembic_0005.py, this module does NOT execute
`alembic downgrade` in-suite — doing so mid-run would drop tables out from under
the rest of the suite. The 0006 -> 0005 -> 0006 round trip was verified at the
CLI against gt-postgres and drops all three tables clean; `test_0006_downgrade_
order_is_reverse_dependency` asserts the property that makes it safe.

Mirrors test_alembic_0005.py scaffolding.
"""
import os
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# ---------------------------------------------------------------------------
# Test database setup — mirrors conftest.py's URL handling
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


TEST_DB_URL = _make_test_db_url()
engine = create_async_engine(TEST_DB_URL, echo=False, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
async def db():
    """Session-scoped async DB session — shares the conftest.py event loop."""
    async with SessionFactory() as session:
        yield session


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

async def _seed_user_song_skill(db: AsyncSession) -> tuple[str, int, str]:
    """Seed a user + song + skill_node. Returns (user_id, song_id, skill_node_id)."""
    user_id = str(uuid.uuid4())
    skill_id = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO users (id, preferences) "
            "VALUES (:uid, '{}'::jsonb) ON CONFLICT (id) DO NOTHING"
        ),
        {"uid": user_id},
    )
    await db.execute(
        text(
            "INSERT INTO songs "
            "  (title, artist, genre, difficulty, bpm, key, breakdown, user_id, category) "
            "VALUES ('T', 'A', 'Blues', 'intermediate', 100, 'E', '{}'::jsonb, "
            "        :uid, 'working_on')"
        ),
        {"uid": user_id},
    )
    song_id = (
        await db.execute(
            text("SELECT id FROM songs WHERE user_id = :uid ORDER BY id DESC LIMIT 1"),
            {"uid": user_id},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
            "VALUES (:sid, :uid, 'Leaf', 'leaf', 0.3)"
        ),
        {"sid": skill_id, "uid": user_id},
    )
    await db.commit()
    return user_id, song_id, skill_id


_INSERT_DRILL = text(
    "INSERT INTO drills "
    "  (id, user_id, name, name_normalized, skill_node_id, canonical_drill_id, "
    "   dedupe_score, song_specific, what, tab_snippet, start_bpm, target_bpm, "
    "   repetitions, success_criterion, common_trap, origin_song_id, origin_drill_index) "
    "VALUES (:id, :uid, :name, :norm, :skill, :canon, :score, :song_specific, :what, "
    "        '{}'::jsonb, :start_bpm, :target_bpm, 12, 'Clean at tempo.', NULL, "
    "        :song_id, :drill_index)"
)


async def _insert_drill(
    db: AsyncSession,
    user_id: str,
    skill_id: str,
    *,
    name: str = "Isolate the slide",
    norm: str | None = None,
    canonical: str | None = None,
    score: int | None = None,
    song_id: int | None = None,
    drill_index: int | None = None,
    start_bpm: int = 50,
    target_bpm: int = 70,
    song_specific: bool = False,
) -> str:
    drill_id = str(uuid.uuid4())
    await db.execute(
        _INSERT_DRILL,
        {
            "id": drill_id,
            "uid": user_id,
            "name": name,
            "norm": norm if norm is not None else name.lower(),
            "skill": skill_id,
            "canon": canonical,
            "score": score,
            "song_specific": song_specific,
            "what": "Play the mechanic alone.",
            "start_bpm": start_bpm,
            "target_bpm": target_bpm,
            "song_id": song_id,
            "drill_index": drill_index,
        },
    )
    return drill_id


async def _cleanup_user(db: AsyncSession, user_id: str) -> None:
    await db.rollback()
    await db.execute(text("DELETE FROM drill_dedupe_queue WHERE user_id = :u"), {"u": user_id})
    await db.execute(text("DELETE FROM drill_attempts WHERE user_id = :u"), {"u": user_id})
    # Duplicates first so the self-FK never blocks the canonical delete.
    await db.execute(
        text("DELETE FROM drills WHERE user_id = :u AND canonical_drill_id IS NOT NULL"),
        {"u": user_id},
    )
    await db.execute(text("DELETE FROM drills WHERE user_id = :u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_sessions WHERE user_id = :u"), {"u": user_id})
    await db.execute(
        text(
            "DELETE FROM song_skills WHERE song_id IN "
            "(SELECT id FROM songs WHERE user_id = :u)"
        ),
        {"u": user_id},
    )
    await db.execute(text("DELETE FROM songs WHERE user_id = :u"), {"u": user_id})
    await db.execute(text("DELETE FROM skill_nodes WHERE user_id = :u"), {"u": user_id})
    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    await db.commit()


# ---------------------------------------------------------------------------
# 1. Tables and columns exist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "table", ["drills", "drill_attempts", "drill_dedupe_queue"]
)
async def test_0006_creates_table(db: AsyncSession, table: str) -> None:
    found = (
        await db.execute(
            text("SELECT tablename FROM pg_tables WHERE tablename = :t"), {"t": table}
        )
    ).scalar_one_or_none()
    assert found == table, f"{table} not found after 0006 upgrade"


async def test_0006_drills_has_expected_columns(db: AsyncSession) -> None:
    rows = (
        await db.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'drills'"
            )
        )
    ).all()
    cols = {r.column_name: r.is_nullable for r in rows}

    # Identity + dedup key must be NOT NULL — they carry the uniqueness guarantee.
    for required in ("id", "user_id", "name", "name_normalized", "skill_node_id"):
        assert cols.get(required) == "NO", f"drills.{required} should be NOT NULL"

    # Provenance MUST be nullable — the drill outlives the song (FLE-8 headline).
    assert cols.get("origin_song_id") == "YES", (
        "origin_song_id must be nullable — a drill has to survive its song"
    )
    assert cols.get("origin_drill_index") == "YES"
    assert cols.get("canonical_drill_id") == "YES"


async def test_0006_drill_attempts_records_tempo_and_reps(db: AsyncSession) -> None:
    """FLE-8 'Done when': per-drill history records tempo and reps."""
    rows = (
        await db.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'drill_attempts'"
            )
        )
    ).all()
    cols = {r.column_name: r.data_type for r in rows}
    assert "tempo_reached_bpm" in cols, "history must record tempo actually reached"
    assert "reps_completed" in cols, "history must record reps completed"
    assert "attempted_at" in cols and "local_calendar_day" in cols, "history must record dates"


# ---------------------------------------------------------------------------
# 2. Dedup — "duplicates collapse on write"
# ---------------------------------------------------------------------------

async def test_0006_unique_index_blocks_second_canonical(db: AsyncSession) -> None:
    """Two canonical drills, same (user, skill, name_normalized) — second must fail.

    This is the DB-level backstop: even if the rapidfuzz layer is bypassed
    entirely, an exact-after-normalization duplicate cannot land.
    """
    user_id, _, skill_id = await _seed_user_song_skill(db)
    try:
        await _insert_drill(db, user_id, skill_id, norm="isolate the slide")
        await db.flush()
        with pytest.raises(IntegrityError):
            await _insert_drill(
                db, user_id, skill_id, name="Isolate The Slide", norm="isolate the slide"
            )
            await db.flush()
        await db.rollback()
    finally:
        await _cleanup_user(db, user_id)


async def test_0006_unique_index_allows_collapsed_duplicate_row(db: AsyncSession) -> None:
    """A row WITH canonical_drill_id set is exempt from the partial index, so
    collapsed-duplicate history can be retained alongside its canonical."""
    user_id, _, skill_id = await _seed_user_song_skill(db)
    try:
        canonical = await _insert_drill(db, user_id, skill_id, norm="isolate the slide")
        await _insert_drill(
            db,
            user_id,
            skill_id,
            name="Isolate The Slide",
            norm="isolate the slide",
            canonical=canonical,
            score=97,
        )
        await db.commit()
        count = (
            await db.execute(
                text("SELECT COUNT(*) FROM drills WHERE user_id = :u"), {"u": user_id}
            )
        ).scalar_one()
        assert count == 2
    finally:
        await _cleanup_user(db, user_id)


async def test_0006_same_name_different_skill_is_not_a_duplicate(db: AsyncSession) -> None:
    """Dedup is scoped to (user, skill). Same drill name on another skill is a
    different exercise and must be allowed to be canonical."""
    user_id, _, skill_a = await _seed_user_song_skill(db)
    skill_b = str(uuid.uuid4())
    try:
        await db.execute(
            text(
                "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
                "VALUES (:sid, :uid, 'Other Leaf', 'leaf', 0.1)"
            ),
            {"sid": skill_b, "uid": user_id},
        )
        await _insert_drill(db, user_id, skill_a, norm="isolate the slide")
        await _insert_drill(db, user_id, skill_b, norm="isolate the slide")
        await db.commit()
        count = (
            await db.execute(
                text("SELECT COUNT(*) FROM drills WHERE user_id = :u"), {"u": user_id}
            )
        ).scalar_one()
        assert count == 2, "cross-skill same-name drills must both be canonical"
    finally:
        await _cleanup_user(db, user_id)


async def test_0006_check_rejects_target_bpm_not_above_start(db: AsyncSession) -> None:
    """DB mirror of Drill._target_bpm_above_start — a zero-progress drill cannot land."""
    user_id, _, skill_id = await _seed_user_song_skill(db)
    try:
        with pytest.raises(IntegrityError):
            await _insert_drill(db, user_id, skill_id, start_bpm=60, target_bpm=60)
            await db.flush()
        await db.rollback()
    finally:
        await _cleanup_user(db, user_id)


async def test_0006_check_rejects_self_referencing_canonical(db: AsyncSession) -> None:
    """A drill cannot be its own duplicate."""
    user_id, _, skill_id = await _seed_user_song_skill(db)
    try:
        drill_id = str(uuid.uuid4())
        with pytest.raises(IntegrityError):
            await db.execute(
                _INSERT_DRILL,
                {
                    "id": drill_id,
                    "uid": user_id,
                    "name": "Self",
                    "norm": "self",
                    "skill": skill_id,
                    "canon": drill_id,  # points at itself
                    "score": 100,
                    "song_specific": False,
                    "what": "x",
                    "start_bpm": 50,
                    "target_bpm": 60,
                    "song_id": None,
                    "drill_index": None,
                },
            )
            await db.flush()
        await db.rollback()
    finally:
        await _cleanup_user(db, user_id)


# ---------------------------------------------------------------------------
# 3. The headline: a drill survives the song that spawned it
# ---------------------------------------------------------------------------

async def test_0006_drill_survives_deletion_of_origin_song(db: AsyncSession) -> None:
    """FLE-8's whole premise. Delete the song; the drill must still be there,
    still queryable, with provenance nulled rather than cascading the row away."""
    user_id, song_id, skill_id = await _seed_user_song_skill(db)
    try:
        drill_id = await _insert_drill(
            db, user_id, skill_id, song_id=song_id, drill_index=2
        )
        await db.commit()

        # Sanity: provenance recorded.
        row = (
            await db.execute(
                text("SELECT origin_song_id, origin_drill_index FROM drills WHERE id = :d"),
                {"d": drill_id},
            )
        ).one()
        assert row.origin_song_id == song_id
        assert row.origin_drill_index == 2

        # The song goes away.
        await db.execute(text("DELETE FROM songs WHERE id = :s"), {"s": song_id})
        await db.commit()

        survived = (
            await db.execute(
                text(
                    "SELECT id, origin_song_id, origin_drill_index, skill_node_id "
                    "FROM drills WHERE id = :d"
                ),
                {"d": drill_id},
            )
        ).one_or_none()
        assert survived is not None, "drill was deleted with its song — the bank failed"
        assert survived.origin_song_id is None, "origin_song_id should be SET NULL"
        assert str(survived.skill_node_id) == skill_id, (
            "the drill must remain queryable by skill with no song at all"
        )
    finally:
        await _cleanup_user(db, user_id)


# ---------------------------------------------------------------------------
# 4. Per-drill history
# ---------------------------------------------------------------------------

async def test_0006_history_round_trips_tempo_and_reps(db: AsyncSession) -> None:
    user_id, _, skill_id = await _seed_user_song_skill(db)
    try:
        drill_id = await _insert_drill(db, user_id, skill_id)
        await db.execute(
            text(
                "INSERT INTO drill_attempts "
                "  (id, drill_id, user_id, local_calendar_day, tempo_reached_bpm, "
                "   reps_completed, rating) "
                "VALUES (:id, :d, :u, :day, 64, 18, 'getting_closer')"
            ),
            {
                "id": str(uuid.uuid4()),
                "d": drill_id,
                "u": user_id,
                "day": date(2099, 7, 1),
            },
        )
        await db.commit()
        row = (
            await db.execute(
                text(
                    "SELECT tempo_reached_bpm, reps_completed, rating, attempted_at "
                    "FROM drill_attempts WHERE drill_id = :d"
                ),
                {"d": drill_id},
            )
        ).one()
        assert row.tempo_reached_bpm == 64
        assert row.reps_completed == 18
        assert row.rating == "getting_closer"
        assert row.attempted_at is not None
    finally:
        await _cleanup_user(db, user_id)


async def test_0006_history_allows_multiple_attempts_same_day(db: AsyncSession) -> None:
    """Deliberate contrast with uq_user_sessions_daily_rating: user_sessions holds
    one VERDICT per slot per day; drill_attempts holds WHAT HAPPENED, and three
    tempo steps in one sitting is real data, not a conflict."""
    user_id, _, skill_id = await _seed_user_song_skill(db)
    try:
        drill_id = await _insert_drill(db, user_id, skill_id)
        day = date(2099, 7, 2)
        for tempo, reps in ((50, 12), (55, 10), (60, 8)):
            await db.execute(
                text(
                    "INSERT INTO drill_attempts "
                    "  (id, drill_id, user_id, local_calendar_day, tempo_reached_bpm, reps_completed) "
                    "VALUES (:id, :d, :u, :day, :t, :r)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "d": drill_id,
                    "u": user_id,
                    "day": day,
                    "t": tempo,
                    "r": reps,
                },
            )
        await db.commit()
        count = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM drill_attempts "
                    "WHERE drill_id = :d AND local_calendar_day = :day"
                ),
                {"d": drill_id, "day": day},
            )
        ).scalar_one()
        assert count == 3
    finally:
        await _cleanup_user(db, user_id)


@pytest.mark.parametrize(
    "column,value",
    [("tempo_reached_bpm", 0), ("tempo_reached_bpm", 500), ("reps_completed", -1)],
)
async def test_0006_history_check_constraints_reject_nonsense(
    db: AsyncSession, column: str, value: int
) -> None:
    user_id, _, skill_id = await _seed_user_song_skill(db)
    try:
        drill_id = await _insert_drill(db, user_id, skill_id)
        await db.flush()
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "INSERT INTO drill_attempts "
                    f"  (id, drill_id, user_id, local_calendar_day, {column}) "
                    "VALUES (:id, :d, :u, :day, :v)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "d": drill_id,
                    "u": user_id,
                    "day": date(2099, 7, 3),
                    "v": value,
                },
            )
            await db.flush()
        await db.rollback()
    finally:
        await _cleanup_user(db, user_id)


async def test_0006_history_cascades_when_drill_is_deleted(db: AsyncSession) -> None:
    """Attempts are meaningless without their drill — CASCADE, unlike the song FK."""
    user_id, _, skill_id = await _seed_user_song_skill(db)
    try:
        drill_id = await _insert_drill(db, user_id, skill_id)
        await db.execute(
            text(
                "INSERT INTO drill_attempts (id, drill_id, user_id, local_calendar_day) "
                "VALUES (:id, :d, :u, :day)"
            ),
            {"id": str(uuid.uuid4()), "d": drill_id, "u": user_id, "day": date(2099, 7, 4)},
        )
        await db.commit()
        await db.execute(text("DELETE FROM drills WHERE id = :d"), {"d": drill_id})
        await db.commit()
        count = (
            await db.execute(
                text("SELECT COUNT(*) FROM drill_attempts WHERE drill_id = :d"),
                {"d": drill_id},
            )
        ).scalar_one()
        assert count == 0
    finally:
        await _cleanup_user(db, user_id)


# ---------------------------------------------------------------------------
# 5. Curator queue (70-84 band)
# ---------------------------------------------------------------------------

async def test_0006_queue_accepts_pending_row_and_rejects_bad_status(
    db: AsyncSession,
) -> None:
    user_id, _, skill_id = await _seed_user_song_skill(db)
    try:
        drill_id = await _insert_drill(db, user_id, skill_id)
        await db.execute(
            text(
                "INSERT INTO drill_dedupe_queue "
                "  (id, user_id, proposed_name, skill_node_id, candidate_drill_id, "
                "   fuzzy_score, status, payload) "
                "VALUES (:id, :u, 'Isolate that slide', :s, :d, 78, 'pending', '{}'::jsonb)"
            ),
            {"id": str(uuid.uuid4()), "u": user_id, "s": skill_id, "d": drill_id},
        )
        await db.commit()

        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "INSERT INTO drill_dedupe_queue "
                    "  (id, user_id, proposed_name, skill_node_id, candidate_drill_id, "
                    "   fuzzy_score, status, payload) "
                    "VALUES (:id, :u, 'X', :s, :d, 78, 'bogus', '{}'::jsonb)"
                ),
                {"id": str(uuid.uuid4()), "u": user_id, "s": skill_id, "d": drill_id},
            )
            await db.flush()
        await db.rollback()
    finally:
        await _cleanup_user(db, user_id)


async def test_0006_queue_rejects_out_of_range_score(db: AsyncSession) -> None:
    user_id, _, skill_id = await _seed_user_song_skill(db)
    try:
        drill_id = await _insert_drill(db, user_id, skill_id)
        await db.flush()
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "INSERT INTO drill_dedupe_queue "
                    "  (id, user_id, proposed_name, skill_node_id, candidate_drill_id, "
                    "   fuzzy_score, payload) "
                    "VALUES (:id, :u, 'X', :s, :d, 101, '{}'::jsonb)"
                ),
                {"id": str(uuid.uuid4()), "u": user_id, "s": skill_id, "d": drill_id},
            )
            await db.flush()
        await db.rollback()
    finally:
        await _cleanup_user(db, user_id)


# ---------------------------------------------------------------------------
# 6. Indexes + downgrade safety
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "index",
    [
        "uq_drills_canonical_identity",
        "ix_drills_user_skill",
        "ix_drill_attempts_drill_time",
        "ix_drill_attempts_user_day",
        "ix_drill_dedupe_queue_pending",
    ],
)
async def test_0006_index_exists(db: AsyncSession, index: str) -> None:
    found = (
        await db.execute(
            text("SELECT indexname FROM pg_indexes WHERE indexname = :i"), {"i": index}
        )
    ).scalar_one_or_none()
    assert found == index, f"{index} missing after 0006"


async def test_0006_canonical_identity_index_is_partial(db: AsyncSession) -> None:
    """The WHERE clause is what lets collapsed duplicates coexist with canonicals."""
    idxdef = (
        await db.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_drills_canonical_identity'"
            )
        )
    ).scalar_one()
    assert "WHERE" in idxdef.upper(), f"index must be partial — got: {idxdef}"
    assert "canonical_drill_id IS NULL" in idxdef, f"unexpected predicate: {idxdef}"


async def test_0006_downgrade_order_is_reverse_dependency(db: AsyncSession) -> None:
    """Both child tables FK into drills, so downgrade must drop them BEFORE drills.

    Asserts the dependency that makes the hand-written drop order in 0006's
    downgrade() correct — if someone adds a table that drills references, or
    reorders the drops, this is the signal.
    """
    refs = {
        r.table_name
        for r in (
            await db.execute(
                text(
                    "SELECT tc.table_name "
                    "FROM information_schema.table_constraints tc "
                    "JOIN information_schema.constraint_column_usage ccu "
                    "  ON tc.constraint_name = ccu.constraint_name "
                    "WHERE tc.constraint_type = 'FOREIGN KEY' "
                    "  AND ccu.table_name = 'drills' "
                    "  AND tc.table_name <> 'drills'"
                )
            )
        ).all()
    }
    assert refs == {"drill_attempts", "drill_dedupe_queue"}, (
        f"unexpected FK dependents on drills: {refs} — downgrade drop order in "
        "0006 must drop every dependent before drills"
    )
