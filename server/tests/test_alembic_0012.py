"""Alembic migration 0012 tests — FLE-4 §12.1's global seed warm-up drills (FLE-61).

0012 made `drills.user_id` and `drills.skill_node_id` nullable so a drill can belong
to no user, and inserted the three hand-authored warm-up rows. §5.1 rule (d) was
unreachable code before it; on 2026-10-04 that reached hardware as "the warm-up and
the first real drill are the same".

What is worth testing here, and why these:

- THE ROWS, AGAINST THE RUBRIC THAT CLASSIFIED THEM. The migration's header claims
  the stored (tier, tier_raw_score) is exactly what compute_tier() returns for the
  stored snippet and bpms, and that this is why the tier column says D2 where §12.1's
  table says D1. `test_stored_tier_matches_compute_tier` is what stops that claim
  from rotting into a hand-picked label nobody can reproduce.
- BOTH CHECK CONSTRAINTS, IN BOTH DIRECTIONS. Nullability is the easy half. The hard
  half is that a NULL now has to MEAN "global" and nothing else — a per-user row that
  loses its skill node would vanish from snapshot.py's candidate query silently,
  because that query INNER JOINs skill_nodes. The constraint is what makes it an
  error instead.
- NULLS NOT DISTINCT. With two NULLs in its leading key, the default-NULLS-DISTINCT
  index is INERT for exactly these rows: three identical "Chromatic spider" inserts
  would all succeed. This is the one thing in the migration that is easy to get
  wrong and impossible to notice.
- §10 EXCLUSION. §12.1 says the seeds are excluded from coverage counts, and the
  §10.3 gate is being read as a pilot go/no-go. A seed drill cannot be selected into
  a technique slot at all, so counting one as cell stock would claim coverage the
  selector can never serve.
- THE LOOP END TO END. `test_seed_drills_resolve_rule_d_rather_than_repeating_slot_one`
  is the issue's own done-when criterion: a generated warm-up never repeats the
  technique block's first drill for a player whose own bank cannot supply one.

Runs against the real Postgres test database. Requires `alembic upgrade head`.
"""
import json
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.sessions.coverage import load_coverage
from app.sessions.select import fill_technique_block, select_warmup
from app.sessions.snapshot import _load_seed_drills
from app.sessions.taxonomy import TechniqueFamily, Tier, compute_tier

# The three ids migration 0012 fixes. Hard-coded rather than imported: `server/alembic/`
# shadows the installed alembic distribution on the test sys.path (see
# test_alembic_0011's note), so the migration module cannot be imported here. Pinning
# the literals is also the point — rule (d) sorts on drill_id, so these values ARE the
# default-warm-up decision and a change to them should break a test.
SEED_IDS = (
    uuid.UUID("5eed0001-0000-4000-8000-000000000001"),
    uuid.UUID("5eed0002-0000-4000-8000-000000000002"),
    uuid.UUID("5eed0003-0000-4000-8000-000000000003"),
)
SPIDER, CYCLE, SUBS = SEED_IDS

EXPECTED = {
    SPIDER: ("Chromatic spider, one string", TechniqueFamily.L1, 50, 80),
    CYCLE: ("Open-chord cycle, clean ring", TechniqueFamily.C1, 50, 75),
    SUBS: ("One string, four subdivisions", TechniqueFamily.M1, 60, 90),
}


def _make_test_db_url() -> str:
    raw = os.environ.get(
        "DATABASE_URL", "postgresql://gt:devpass@localhost:5433/guitar_trainer"
    )
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if raw.startswith(prefix):
            if prefix == "postgresql+asyncpg://":
                return raw
            return raw.replace(prefix, "postgresql+asyncpg://", 1)
    return raw


TEST_DB_URL = _make_test_db_url()


@pytest.fixture
async def db():
    """Per-test connection on NullPool with a synchronous teardown. Nothing commits,
    so terminate-without-commit is this module's isolation — same fixture as
    test_alembic_0009/0011, load-bearing for the same reasons."""
    engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)
    conn = await engine.connect()

    yield conn

    conn.sync_connection.connection.dbapi_connection.driver_connection.terminate()
    engine.sync_engine.dispose(close=False)


_GLOBAL_INSERT = text(
    "INSERT INTO drills (id, user_id, skill_node_id, name, name_normalized, "
    "canonical_drill_id, song_specific, what, tab_snippet, start_bpm, target_bpm, "
    "repetitions, success_criterion, origin_song_id, family, tier, tier_raw_score, "
    "status) "
    "VALUES (:id, :uid, :node, :name, :norm, :canon, :song_specific, 'what', "
    "'{}'::jsonb, 60, 100, 12, 'clean', :origin, CAST(:family AS technique_family), "
    "CAST(:tier AS drill_tier), :raw, 'active')"
)


async def _insert(db, **over):
    """Insert one drill row, global by default. Rolled back by the fixture."""
    params = {
        "id": uuid.uuid4(),
        "uid": None,
        "node": None,
        "name": f"probe-{uuid.uuid4().hex[:8]}",
        "norm": f"probe{uuid.uuid4().hex[:8]}",
        "canon": None,
        "song_specific": False,
        "origin": None,
        "family": "L1",
        "tier": "D2",
        "raw": 5,
    }
    params.update(over)
    await db.execute(_GLOBAL_INSERT, params)
    return params["id"]


async def _make_user(db) -> uuid.UUID:
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:id, '{}'::jsonb)"), {"id": uid}
    )
    return uid


async def _make_skill_node(db, user_id: uuid.UUID) -> uuid.UUID:
    nid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
            "VALUES (:id, :uid, :name, 'leaf', 0.0)"
        ),
        {"id": nid, "uid": user_id, "name": f"node-{nid.hex[:8]}"},
    )
    return nid


# ---------------------------------------------------------------------------
# The rows themselves
# ---------------------------------------------------------------------------

async def test_the_three_seed_rows_exist_and_are_global(db):
    rows = (
        await db.execute(
            text(
                "SELECT id, name, user_id, skill_node_id, song_specific, "
                "canonical_drill_id, origin_song_id, status, family::text AS family "
                "FROM drills WHERE user_id IS NULL ORDER BY id"
            )
        )
    ).all()
    assert [r.id for r in rows] == list(SEED_IDS), (
        "§12.1 ships exactly three global rows and rule (d) sorts on drill_id — a "
        "fourth, or a different id, changes which warm-up a thin-bank player gets"
    )
    for r in rows:
        name, family, _, _ = EXPECTED[r.id]
        assert r.name == name
        assert r.family == family.value
        assert r.skill_node_id is None       # paired with user_id, see the CHECK below
        assert r.song_specific is False      # §12.1
        assert r.canonical_drill_id is None  # §12.1: never dedup-suppressed
        assert r.origin_song_id is None      # no song spawned a hand-authored drill
        assert r.status == "active"          # or select.py would not see it


async def test_stored_tier_matches_compute_tier(db):
    """§9 defines `tier` as compute_tier()'s output, STORED so §9.1 is retunable by
    one UPDATE. Migration 0012's header claims the stored D2s are that output and not
    hand-picked labels — and that is the whole argument for deviating from §12.1's D1
    column. This test is the argument's receipt.
    """
    rows = (
        await db.execute(
            text(
                "SELECT id, tab_snippet, start_bpm, target_bpm, tier::text AS tier, "
                "tier_raw_score FROM drills WHERE user_id IS NULL ORDER BY id"
            )
        )
    ).all()
    assert len(rows) == 3
    for r in rows:
        _, family, start, target = EXPECTED[r.id]
        assert (r.start_bpm, r.target_bpm) == (start, target), "§12.1's ladder"
        snippet = r.tab_snippet if isinstance(r.tab_snippet, dict) else json.loads(r.tab_snippet)
        tier, raw = compute_tier(
            snippet, start_bpm=r.start_bpm, target_bpm=r.target_bpm, family=family
        )
        assert (r.tier, r.tier_raw_score) == (tier.value, raw), (
            f"{EXPECTED[r.id][0]}: stored ({r.tier}, {r.tier_raw_score}) but the "
            f"rubric says ({tier.value}, {raw})"
        )
        # The pilot band is D2-D4. All three landing in it is a property worth
        # knowing, not an accident: D1 is out of band for the cohort.
        assert Tier(r.tier) is Tier.D2


async def test_spec_12_1_tier_d1_is_unreachable_for_two_of_the_three(db):
    """Pins WHY the stored tier deviates, so the deviation is a finding and not drift.

    L1's §8 range floors at D2, so (L1, D1) is not a viable cell at all. And a
    six-string open chord scores 4 on §9.1's simultaneity feature by itself, which
    puts any honest C1 cycle above D1's raw<=3 cutoff.
    """
    assert TechniqueFamily.L1.tier_range[0] is Tier.D2
    from app.sessions.coverage import viable_cells

    assert (TechniqueFamily.L1, Tier.D1) not in viable_cells()

    row = (
        await db.execute(
            text(
                "SELECT tab_snippet, start_bpm, target_bpm FROM drills WHERE id = :id"
            ),
            {"id": CYCLE},
        )
    ).one()
    snippet = row.tab_snippet if isinstance(row.tab_snippet, dict) else json.loads(row.tab_snippet)
    _, raw = compute_tier(snippet, start_bpm=row.start_bpm, target_bpm=row.target_bpm)
    assert raw > 3, "D1 requires raw <= 3; an open-chord cycle cannot get there"


# ---------------------------------------------------------------------------
# ck_drills_global_has_no_skill_node — both directions
# ---------------------------------------------------------------------------

async def test_a_global_drill_may_not_carry_a_skill_node(db):
    """`skill_nodes` is per-user. A global row pointing at one would belong to that
    user through the back door."""
    user = await _make_user(db)
    node = await _make_skill_node(db, user)
    with pytest.raises(IntegrityError, match="ck_drills_global_has_no_skill_node"):
        await _insert(db, uid=None, node=node)


async def test_a_user_owned_drill_may_not_drop_its_skill_node(db):
    """The direction that matters more. snapshot.py's candidate query INNER JOINs
    skill_nodes, so a per-user row with a NULL node would not raise anywhere — it
    would just stop being selectable, silently."""
    user = await _make_user(db)
    with pytest.raises(IntegrityError, match="ck_drills_global_has_no_skill_node"):
        await _insert(db, uid=user, node=None)


# ---------------------------------------------------------------------------
# ck_drills_global_is_seed_shaped — §12.1's four properties
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "over,why",
    [
        ({"song_specific": True}, "§12.1: song_specific = false"),
        ({"family": None}, "family_key has no root or node to degrade to"),
        ({"tier": None, "raw": None}, "§12.1 names a tier for each seed"),
    ],
    ids=["song_specific", "no_family", "no_tier"],
)
async def test_a_global_drill_must_be_seed_shaped(db, over, why):
    with pytest.raises(IntegrityError, match="ck_drills_global_is_seed_shaped"):
        await _insert(db, **over)


async def test_a_global_drill_cannot_be_dedup_suppressed(db):
    """§12.1: "never dedup-suppressed". drill_dedupe already cannot reach one (it
    filters on an equality against a real skill_node_id), but the constraint is what
    stops a future write path from collapsing one anyway."""
    with pytest.raises(IntegrityError) as exc:
        await _insert(db, canon=SPIDER)
    # Either constraint is a correct rejection: 0011's status/canonical pairing fires
    # on the same row. Assert the rejection, not which guard caught it first.
    assert "ck_drills_" in str(exc.value)


async def test_the_seed_shape_constraint_does_not_touch_user_owned_drills(db):
    """A per-user drill is still allowed to be song_specific, unclassified and a
    collapsed duplicate — none of that is what 0012 is constraining."""
    user = await _make_user(db)
    node = await _make_skill_node(db, user)
    await _insert(
        db, uid=user, node=node, song_specific=True, family=None, tier=None, raw=None
    )  # no raise


# ---------------------------------------------------------------------------
# uq_drills_canonical_identity, NULLS NOT DISTINCT
# ---------------------------------------------------------------------------

async def test_two_global_canonicals_cannot_share_a_normalized_name(db):
    """The default NULLS DISTINCT index is INERT for a key of (NULL, NULL, name): PG
    treats every global row's key as unique, so three identical seeds would all
    insert. 0012 recreates the index NULLS NOT DISTINCT to close that."""
    with pytest.raises(IntegrityError, match="uq_drills_canonical_identity"):
        # Collides with SPIDER's row on name_normalized alone; (user_id, skill_node_id)
        # is (NULL, NULL) on both.
        await _insert(db, norm="chromatic spider one string")


async def test_nulls_not_distinct_is_actually_set_on_the_index(db):
    """Asserted on the catalog as well as behaviourally: `indnullsnotdistinct` is the
    single bit that makes the test above pass, and it silently reverts to false if the
    index is ever recreated by hand."""
    assert (
        await db.scalar(
            text(
                "SELECT indnullsnotdistinct FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'uq_drills_canonical_identity'"
            )
        )
        is True
    )


async def test_two_users_may_still_share_a_drill_name(db):
    """NULLS NOT DISTINCT must not have narrowed the per-user rule. Dedup is scoped to
    (user, skill); two users owning "Isolate the slide" was never a collision."""
    for _ in range(2):
        user = await _make_user(db)
        node = await _make_skill_node(db, user)
        await _insert(db, uid=user, node=node, norm="isolate the slide")
    await db.execute(text("SELECT 1"))  # no raise


# ---------------------------------------------------------------------------
# §10 — excluded from coverage counts
# ---------------------------------------------------------------------------

async def test_seed_drills_are_excluded_from_coverage_counts(db):
    """§12.1, explicitly. The §10.3 gate is read as a pilot go/no-go, and a seed drill
    cannot be selected into a technique slot at all — crediting one as cell stock
    would claim coverage the selector can never serve."""
    report = await load_coverage(db)  # user_id=None grades the whole bank
    assert report.total_active >= 0
    assert report.stock_of(TechniqueFamily.M1, Tier.D2) == 0, (
        "M1/D2 is stocked ONLY by the 'One string, four subdivisions' seed in a clean "
        "test database — if it counts, the exclusion predicate is missing"
    )

    # And the control: the same row is visible when queried without the exclusion, so
    # the assertion above is about the predicate and not about an empty table.
    assert (
        await db.scalar(
            text(
                "SELECT count(*) FROM drills WHERE user_id IS NULL AND status = 'active' "
                "AND family = 'M1' AND tier = 'D2'"
            )
        )
        == 1
    )


# ---------------------------------------------------------------------------
# The loop, end to end — the issue's done-when criterion
# ---------------------------------------------------------------------------

async def test_the_loader_returns_the_three_seeds_as_candidates(db):
    seeds = await _load_seed_drills(db)
    assert [s.drill_id for s in seeds] == [str(i) for i in SEED_IDS]
    for s in seeds:
        assert s.skill_node_id is None
        assert s.family is not None
        assert s.root is s.family.root, "a global row's root comes from §8, not a tree"
        assert s.family_key.startswith("family:")
        assert s.attempts == 0 and s.rung_bpm is None, (
            "the warm-up never touches the ladder (§6.2), so a seed drill has no "
            "per-user progress to load"
        )


async def test_seed_drills_resolve_rule_d_rather_than_repeating_slot_one(db):
    """FLE-61's done-when, against the real rows: a player whose own bank cannot
    supply a warm-up gets a seed drill, not slot 1 served twice.

    The §5.1 ladder fails through (a), (b) and (c) here the same way it did for
    Hernan's 9-drill bank on 2026-10-04 — no drill has been practised yet, and (c)
    excludes slot 1's own drill, so a single-family bank leaves it empty.
    """
    from tests.test_session_select import ctx, drill

    seeds = await _load_seed_drills(db)
    assert seeds, "migration 0012 has not run against this database"

    own = drill("own-only", family=TechniqueFamily.L3, attempts=0, last_practiced_on=None)
    fill = fill_technique_block([own], ctx(), technique_seconds=900)
    assert fill.picks, "the technique block must fill, or there is no slot 1 to repeat"

    pick = select_warmup(
        [own], ctx(), seconds=180, slot_one=fill.picks[0], seed_drills=seeds
    )
    assert pick is not None
    assert pick.rule == "d"
    assert pick.candidate.drill_id != fill.picks[0].candidate.drill_id, (
        "the warm-up and the first real drill are the same — the exact device "
        "observation FLE-61 exists to fix"
    )
    assert pick.candidate.drill_id == str(SPIDER)
