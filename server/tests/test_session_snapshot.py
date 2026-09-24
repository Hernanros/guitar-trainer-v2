"""Snapshot loader tests — the DB read that feeds the generator (FLE-9 Task 5).

`build_plan` is a pure function and 235 tests already pin it against literals. That
leaves exactly one untested seam in the session engine: the translation from a real
Postgres into a `GeneratorInputs`. These tests are that seam, and they run against
the real database rather than a mock because every bug this module can have is a
bug in SQL semantics — a join that drops a row, an absence that arrives as a zero,
an ordering that is not stable — and a mocked `execute()` would agree with whatever
the code already does.

What is asserted here is chosen by the same standard as test_alembic_0009.py: the
failures that produce a PLAUSIBLE session rather than an error. A crash gets fixed
the day it ships. These do not:

- `days_since_last = 0` for a user with no history reads as "practised yesterday"
  and silently suppresses the re-entry path on a first-ever session.
- A root with no leaves arriving as mastery 0.0 makes it the weakest root, and §4
  rule 5 then hijacks every session for a skill the user never mentioned.
- `completion_3 = 0.0` for a user with no terminal sessions is indistinguishable
  from a user who bails, and pushes the generator into a recovery mode on day one.
- An INNER JOIN on drill_progress would drop every never-practised drill — which in
  week 1 is the entire bank, so the user gets nothing precisely when the bank is
  newest.
- Counting non-terminal sessions in the history windows lets an abandoned-but-open
  row from this morning masquerade as yesterday's practice.

The last test is the issue's own acceptance criterion: same user, same day, same
preference -> byte-identical plan.

Runs against the real Postgres test database (DATABASE_URL env var or default).
Requires `alembic upgrade head` to have been applied before running.
"""
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.sessions.assemble import build_plan
from app.sessions.snapshot import (
    DEFAULT_TARGET_MINUTES,
    SnapshotError,
    load_snapshot,
    local_day,
)


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

DAY = date(2026, 3, 14)
NOW = datetime(2026, 3, 14, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
async def db():
    """Per-test connection, synchronous teardown.

    Identical construction to test_alembic_0009.py's, and for the same reasons
    documented at length there: a per-test NullPool engine keeps an asyncpg
    connection from being reused across event loops, and terminating the transport
    in a synchronous finalizer avoids awaiting on a loop that is already closing.

    Nothing below commits, so terminate-without-commit IS this module's isolation.
    """
    engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)
    conn = await engine.connect()

    yield conn

    conn.sync_connection.connection.dbapi_connection.driver_connection.terminate()
    engine.sync_engine.dispose(close=False)


# ---------------------------------------------------------------------------
# Row builders. Every INSERT is rolled back by the fixture's terminate().
# ---------------------------------------------------------------------------

async def _make_user(db, preferences: str = "{}") -> uuid.UUID:
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:id, CAST(:p AS jsonb))"),
        {"id": uid, "p": preferences},
    )
    return uid


async def _make_node(
    db,
    user_id: uuid.UUID,
    *,
    name: str,
    level: str,
    parent_id: uuid.UUID | None = None,
    mastery: float = 0.0,
) -> uuid.UUID:
    nid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO skill_nodes (id, user_id, name, level, parent_id, mastery) "
            "VALUES (:id, :uid, :name, CAST(:lvl AS skill_level), :pid, :m)"
        ),
        {
            "id": nid,
            "uid": user_id,
            "name": name,
            "lvl": level,
            "pid": parent_id,
            "m": mastery,
        },
    )
    return nid


async def _make_root_chain(
    db, user_id: uuid.UUID, root_name: str, *leaf_masteries: float
) -> list[uuid.UUID]:
    """root -> sub -> N leaves. Returns the leaf ids.

    The two-hop shape is the point: mastery lives on leaves, §4 asks about roots,
    and `_ROOT_MASTERY_SQL` only finds a leaf if BOTH hops resolve.
    """
    root = await _make_node(db, user_id, name=root_name, level="root")
    sub = await _make_node(
        db, user_id, name=f"{root_name} sub", level="sub", parent_id=root
    )
    return [
        await _make_node(
            db, user_id, name=f"{root_name} leaf {i}", level="leaf",
            parent_id=sub, mastery=m,
        )
        for i, m in enumerate(leaf_masteries)
    ]


async def _make_drill(
    db,
    user_id: uuid.UUID,
    skill_node_id: uuid.UUID,
    *,
    start_bpm: int = 60,
    target_bpm: int = 100,
    repetitions: int = 12,
    song_specific: bool = False,
    canonical_drill_id: uuid.UUID | None = None,
    family: str | None = None,
    tier: str | None = None,
) -> uuid.UUID:
    did = uuid.uuid4()
    # status is DERIVED from canonical_drill_id rather than passed in. FLE-13's
    # ck_drills_status_matches_canonical makes "status = 'duplicate'" and
    # "canonical_drill_id IS NOT NULL" the same statement, so a helper that let the
    # two be set independently could only ever build rows the database rejects.
    status = "duplicate" if canonical_drill_id is not None else "active"
    # tier and tier_raw_score are paired by ck_drills_tier_pairs_raw_score; the raw
    # score is a fixed in-range filler because no assertion here reads it.
    await db.execute(
        text(
            "INSERT INTO drills (id, user_id, name, name_normalized, skill_node_id, "
            "canonical_drill_id, song_specific, what, tab_snippet, start_bpm, "
            "target_bpm, repetitions, success_criterion, status, family, tier, "
            "tier_raw_score) "
            "VALUES (:id, :uid, :name, :norm, :node, :canon, :ss, 'what', "
            "'{}'::jsonb, :start, :target, :reps, 'clean', "
            "CAST(:status AS drill_status), CAST(:family AS technique_family), "
            "CAST(:tier AS drill_tier), CASE WHEN :tier IS NULL THEN NULL ELSE 8 END)"
        ),
        {
            "id": did,
            "uid": user_id,
            "name": f"drill-{did.hex[:8]}",
            "norm": f"drill{did.hex[:8]}",
            "node": skill_node_id,
            "canon": canonical_drill_id,
            "ss": song_specific,
            "start": start_bpm,
            "target": target_bpm,
            "reps": repetitions,
            "status": status,
            "family": family,
            "tier": tier,
        },
    )
    return did


async def _make_progress(
    db,
    user_id: uuid.UUID,
    drill_id: uuid.UUID,
    *,
    rung_bpm: int = 75,
    state: str = "active",
    consecutive_clears: int = 0,
    attempts: int = 0,
    last_practiced_on: date | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO drill_progress (id, user_id, drill_id, rung_bpm, state, "
            "consecutive_clears, attempts, last_practiced_on, mastered_at) "
            "VALUES (:id, :uid, :did, :rung, CAST(:st AS drill_progress_state), "
            ":clears, :attempts, :last, :mastered)"
        ),
        {
            "id": uuid.uuid4(),
            "uid": user_id,
            "did": drill_id,
            "rung": rung_bpm,
            "st": state,
            "clears": consecutive_clears,
            "attempts": attempts,
            "last": last_practiced_on,
            # ck_drill_progress_mastered_at_pairs_state: present iff state='mastered'.
            "mastered": NOW if state == "mastered" else None,
        },
    )


async def _make_attempt(
    db, user_id: uuid.UUID, drill_id: uuid.UUID, *, outcome: str
) -> None:
    await db.execute(
        text(
            "INSERT INTO drill_attempts (id, drill_id, user_id, local_calendar_day, "
            "outcome) VALUES (:id, :did, :uid, :day, "
            "CAST(:o AS drill_attempt_outcome))"
        ),
        {
            "id": uuid.uuid4(),
            "did": drill_id,
            "uid": user_id,
            "day": DAY,
            "o": outcome,
        },
    )


async def _make_song(
    db,
    user_id: uuid.UUID | None,
    *,
    category: str | None = "can_play",
    bpm: int | None = 90,
) -> int:
    return await db.scalar(
        text(
            "INSERT INTO songs (title, artist, breakdown, user_id, category, bpm) "
            "VALUES (:t, 'artist', '{}'::jsonb, :uid, "
            "CAST(:cat AS song_category), :bpm) RETURNING id"
        ),
        {
            "t": f"song-{uuid.uuid4().hex[:8]}",
            "uid": user_id,
            "cat": category,
            "bpm": bpm,
        },
    )


async def _link_song_skill(db, song_id: int, node_id: uuid.UUID) -> None:
    await db.execute(
        text(
            "INSERT INTO song_skills (song_id, skill_node_id, weight) "
            "VALUES (:s, :n, 1.0)"
        ),
        {"s": song_id, "n": node_id},
    )


async def _rate_song(
    db, user_id: uuid.UUID, song_id: int, *, day: date
) -> None:
    """A user_sessions row — the only 'recently played' signal songs actually has."""
    await db.execute(
        text(
            "INSERT INTO user_sessions (id, user_id, song_id, local_calendar_day, "
            "tz_offset_minutes) VALUES (:id, :uid, :sid, :day, 0)"
        ),
        {"id": uuid.uuid4(), "uid": user_id, "sid": song_id, "day": day},
    )


async def _make_session(
    db,
    user_id: uuid.UUID,
    *,
    day: date,
    state: str = "completed",
    completion_ratio: float | None = None,
    generated_at: datetime | None = None,
) -> uuid.UUID:
    """A practice_sessions row obeying 0009's state/terminal_reason/ended_at pairings."""
    terminal = state in ("completed", "abandoned")
    sid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO practice_sessions "
            "(id, user_id, local_calendar_day, tz_offset_minutes, target_minutes, "
            " mode, allow_push, generator_version, seed, mode_rule, state, "
            " terminal_reason, ended_at, started_at, generated_at, item_count, "
            " completion_ratio) "
            "VALUES (:id, :uid, :day, 0, 30, 'BALANCED', true, 'session-gen/1.0.0', "
            " 'seed', 'default', CAST(:state AS session_state), "
            " CAST(:reason AS session_terminal_reason), :ended, :started, "
            " :generated, 4, :ratio)"
        ),
        {
            "id": sid,
            "uid": user_id,
            "day": day,
            "state": state,
            "reason": "user_completed" if terminal else None,
            "ended": NOW if terminal else None,
            "started": NOW if state != "planned" else None,
            "generated": generated_at or NOW,
            "ratio": completion_ratio,
        },
    )
    return sid


async def _make_item(
    db,
    session_id: uuid.UUID,
    *,
    index: int,
    drill_id: uuid.UUID | None = None,
    state: str = "completed",
) -> None:
    await db.execute(
        text(
            "INSERT INTO practice_session_items "
            "(id, session_id, item_index, block, kind, drill_id, planned_seconds, "
            " rated, skippable, click_enabled, state) "
            "VALUES (:id, :sid, :idx, 'technique', 'drill', :did, 120, "
            " true, true, true, CAST(:st AS session_item_state))"
        ),
        {
            "id": uuid.uuid4(),
            "sid": session_id,
            "idx": index,
            "did": drill_id,
            "st": state,
        },
    )


async def _snapshot(db, user_id, **kwargs):
    return await load_snapshot(db, user_id, tz_offset_minutes=0,
                               local_calendar_day=DAY, **kwargs)


# ---------------------------------------------------------------------------
# The user, the preference (D-12)
# ---------------------------------------------------------------------------

async def test_unknown_user_raises_snapshot_error(db):
    with pytest.raises(SnapshotError):
        await _snapshot(db, uuid.uuid4())


@pytest.mark.parametrize("stored,expected", [(15, 15), (30, 30), (45, 45), (60, 60)])
async def test_target_minutes_reads_the_onboarding_preference(db, stored, expected):
    """Task 5's headline: the value onboarding captured and nothing read."""
    uid = await _make_user(db, f'{{"session_length_min": {stored}}}')
    assert (await _snapshot(db, uid)).target_minutes == expected


@pytest.mark.parametrize(
    "preferences",
    [
        "{}",                                  # never onboarded a length
        '{"session_length_min": null}',        # explicit null
        '{"session_length_min": "forty"}',     # wrong type
        '{"session_length_min": 42}',          # not one of the four
        '{"session_length_min": -30}',
    ],
)
async def test_malformed_preference_falls_back_rather_than_raising(db, preferences):
    """A comfort setting must never be the reason a user gets no session at all."""
    uid = await _make_user(db, preferences)
    assert (await _snapshot(db, uid)).target_minutes == DEFAULT_TARGET_MINUTES


async def test_explicit_override_beats_the_stored_preference(db):
    uid = await _make_user(db, '{"session_length_min": 60}')
    assert (await _snapshot(db, uid, target_minutes=15)).target_minutes == 15


async def test_invalid_override_falls_back_to_the_stored_preference(db):
    """An override out of range must not clobber a preference that IS valid."""
    uid = await _make_user(db, '{"session_length_min": 45}')
    assert (await _snapshot(db, uid, target_minutes=999)).target_minutes == 45


# ---------------------------------------------------------------------------
# History (§4) — absence is not zero
# ---------------------------------------------------------------------------

async def test_no_history_is_none_not_zero(db):
    """The header's rule 3, all three columns at once.

    A 0 in any of these is a lie with a plausible shape: 0 days since last reads as
    'practised yesterday', 0.0 completion reads as 'bails every time'.
    """
    uid = await _make_user(db)
    snap = await _snapshot(db, uid)
    assert snap.sessions_count == 0
    assert snap.days_since_last is None
    assert snap.completion_3 is None


async def test_days_since_last_counts_local_calendar_days(db):
    uid = await _make_user(db)
    await _make_session(db, uid, day=DAY - timedelta(days=3))
    assert (await _snapshot(db, uid)).days_since_last == 3


async def test_history_ignores_non_terminal_sessions(db):
    """An open session from this morning is not a session the user completed.

    Counting it would both fake a 0-day layoff and inflate sessions_count, which
    together move the §4 mode decision off the user's actual history.
    """
    uid = await _make_user(db)
    await _make_session(db, uid, day=DAY - timedelta(days=5), state="completed")
    # Separate days, because 0009's `uq_practice_sessions_open_day` allows only one
    # OPEN session per user per day. Both are more recent than the completed one, so
    # counting either would drag days_since_last down to 1 or 0.
    await _make_session(db, uid, day=DAY - timedelta(days=1), state="in_progress")
    await _make_session(db, uid, day=DAY, state="planned")

    snap = await _snapshot(db, uid)
    assert snap.sessions_count == 1
    assert snap.days_since_last == 5


async def test_abandoned_counts_as_terminal(db):
    """'abandoned' is history: the user showed up. Only open sessions are excluded."""
    uid = await _make_user(db)
    await _make_session(db, uid, day=DAY - timedelta(days=1), state="abandoned")
    snap = await _snapshot(db, uid)
    assert snap.sessions_count == 1
    assert snap.days_since_last == 1


async def test_completion_3_averages_only_the_last_three(db):
    """The window is three TERMINAL sessions, most recent first.

    The 0.0 four days back must not drag the average: §4's recovery rules read this
    number, and a stale bad day would keep pushing a recovered user into recovery.
    """
    uid = await _make_user(db)
    for offset, ratio in ((4, 0.0), (3, 0.9), (2, 0.6), (1, 0.9)):
        await _make_session(
            db, uid, day=DAY - timedelta(days=offset), completion_ratio=ratio
        )
    snap = await _snapshot(db, uid)
    assert snap.sessions_count == 4
    assert snap.completion_3 == pytest.approx((0.9 + 0.6 + 0.9) / 3)


async def test_completion_3_skips_null_ratios(db):
    """A terminal session with no ratio is unmeasured, not a zero."""
    uid = await _make_user(db)
    await _make_session(db, uid, day=DAY - timedelta(days=2), completion_ratio=None)
    await _make_session(db, uid, day=DAY - timedelta(days=1), completion_ratio=0.8)
    assert (await _snapshot(db, uid)).completion_3 == pytest.approx(0.8)


async def test_history_is_scoped_to_the_user(db):
    other = await _make_user(db)
    await _make_session(db, other, day=DAY - timedelta(days=1), completion_ratio=1.0)
    uid = await _make_user(db)
    snap = await _snapshot(db, uid)
    assert snap.sessions_count == 0 and snap.days_since_last is None


# ---------------------------------------------------------------------------
# root_mastery (§4 rule 5)
# ---------------------------------------------------------------------------

async def test_root_mastery_averages_leaves_through_two_hops(db):
    uid = await _make_user(db)
    await _make_root_chain(db, uid, "Rhythm", 0.2, 0.4)
    await _make_root_chain(db, uid, "Lead", 0.9)

    roots = (await _snapshot(db, uid)).root_mastery
    assert roots["rhythm"] == pytest.approx(0.3)
    assert roots["lead"] == pytest.approx(0.9)


async def test_root_with_no_leaves_is_absent_not_zero(db):
    """The rule-5 hijack. An untouched root is unknown, not weak.

    A 0.0 here would make the empty root the weakest one in every session the user
    ever generates, for a skill they never mentioned at onboarding.
    """
    uid = await _make_user(db)
    await _make_root_chain(db, uid, "Rhythm", 0.5)
    await _make_node(db, uid, name="Fingerstyle", level="root")  # bare root

    snap = await _snapshot(db, uid)
    assert "fingerstyle" not in snap.root_mastery
    assert snap.weakest_root_mastery == pytest.approx(0.5)


async def test_unknown_root_name_is_dropped_not_crashed(db):
    """A hand-edited DB costs that root's contribution, not the whole session."""
    uid = await _make_user(db)
    await _make_root_chain(db, uid, "Rhythm", 0.5)
    await _make_root_chain(db, uid, "Underwater Basket Weaving", 0.1)

    roots = (await _snapshot(db, uid)).root_mastery
    assert set(roots) == {"rhythm"}


async def test_player_level_is_floored_for_a_new_user(db):
    """floor_player_level's UNKNOWN, reached through the loader rather than asserted
    on directly — a user with no leaves must not arrive as a literal 0.0."""
    uid = await _make_user(db)
    assert (await _snapshot(db, uid)).player_level > 0.0


async def test_player_mastery_raw_is_none_for_a_new_user(db):
    """FLE-59 — no leaves means choose_mode's rule 5 must not fire, so the raw
    field stays None rather than inheriting the catalog floor or falling back to
    UNKNOWN_PLAYER_LEVEL (0.5), either of which would fabricate a comparable value
    for a player who has never been observed."""
    uid = await _make_user(db)
    assert (await _snapshot(db, uid)).player_mastery_raw is None


async def test_player_mastery_raw_is_unfloored_below_the_catalog_floor(db):
    """FLE-59 — a true average under PLAYER_LEVEL_FLOOR (0.20) must reach
    choose_mode unfloored, or rule 5 compares a floored player_level against a raw
    weakest_root_mastery and manufactures a gap that isn't real."""
    uid = await _make_user(db)
    await _make_root_chain(db, uid, "Rhythm", 0.05, 0.05)

    snap = await _snapshot(db, uid)
    assert snap.player_level == pytest.approx(0.20)  # floored for catalog use
    assert snap.player_mastery_raw == pytest.approx(0.05)  # raw, for rule 5


# ---------------------------------------------------------------------------
# The bank
# ---------------------------------------------------------------------------

async def test_untouched_drill_appears_with_default_progress(db):
    """The LEFT JOIN. In week 1 this is the entire bank.

    An INNER JOIN would return nothing here, and the thin-bank ladder would fire for
    a user whose bank is actually full.
    """
    uid = await _make_user(db)
    (leaf,) = await _make_root_chain(db, uid, "Rhythm", 0.25)
    did = await _make_drill(db, uid, leaf, start_bpm=55)

    (c,) = (await _snapshot(db, uid)).candidates
    assert c.drill_id == str(did)
    assert c.rung_bpm is None
    assert c.planned_bpm == 55        # a drill with no progress is planned at its floor
    assert c.progress_state == "active"
    assert c.consecutive_clears == 0 and c.attempts == 0
    assert c.last_practiced_on is None
    assert c.node_mastery == pytest.approx(0.25)
    assert c.root is not None and c.root.value == "rhythm"


async def test_progress_row_overrides_the_defaults(db):
    uid = await _make_user(db)
    (leaf,) = await _make_root_chain(db, uid, "Lead", 0.5)
    did = await _make_drill(db, uid, leaf, start_bpm=60)
    await _make_progress(
        db, uid, did, rung_bpm=85, state="maintenance",
        consecutive_clears=2, attempts=7, last_practiced_on=DAY - timedelta(days=1),
    )

    (c,) = (await _snapshot(db, uid)).candidates
    assert c.rung_bpm == 85 and c.planned_bpm == 85
    assert c.progress_state == "maintenance"
    assert c.consecutive_clears == 2 and c.attempts == 7
    assert c.last_practiced_on == DAY - timedelta(days=1)


async def test_non_canonical_drills_are_excluded(db):
    """A deduped duplicate must not compete with the drill it was folded into."""
    uid = await _make_user(db)
    (leaf,) = await _make_root_chain(db, uid, "Rhythm", 0.5)
    canonical = await _make_drill(db, uid, leaf)
    await _make_drill(db, uid, leaf, canonical_drill_id=canonical)

    ids = {c.drill_id for c in (await _snapshot(db, uid)).candidates}
    assert ids == {str(canonical)}


async def test_lifetime_clears_counts_only_clears(db):
    """§5.4's consolidation fallback reads this. HOLD/MISS/SKIP are not wins."""
    uid = await _make_user(db)
    (leaf,) = await _make_root_chain(db, uid, "Timing", 0.5)
    did = await _make_drill(db, uid, leaf)
    for outcome in ("CLEAR", "CLEAR", "MISS", "HOLD", "SKIP"):
        await _make_attempt(db, uid, did, outcome=outcome)

    (c,) = (await _snapshot(db, uid)).candidates
    assert c.lifetime_clears == 2


async def test_candidates_are_scoped_to_the_user(db):
    other = await _make_user(db)
    (other_leaf,) = await _make_root_chain(db, other, "Rhythm", 0.5)
    await _make_drill(db, other, other_leaf)

    uid = await _make_user(db)
    assert (await _snapshot(db, uid)).candidates == ()


async def test_drill_on_an_orphan_leaf_still_appears(db):
    """A leaf with no sub/root must not vanish from the bank.

    The root joins are LEFT for exactly this: `root=None` costs the family-repeat
    penalty its axis, which `family_key` already degrades; dropping the row would
    cost the user a drill.
    """
    uid = await _make_user(db)
    orphan = await _make_node(db, uid, name="orphan leaf", level="leaf", mastery=0.3)
    did = await _make_drill(db, uid, orphan)

    (c,) = (await _snapshot(db, uid)).candidates
    assert c.drill_id == str(did) and c.root is None


# ---------------------------------------------------------------------------
# The song (§5.3) — inherited, never re-rolled
# ---------------------------------------------------------------------------

async def test_song_readiness_is_the_mean_and_section_is_the_weakest(db):
    uid = await _make_user(db)
    leaves = await _make_root_chain(db, uid, "Rhythm", 0.8, 0.2, 0.5)
    song_id = await _make_song(db, uid, category="working_on", bpm=120)
    for leaf in leaves:
        await _link_song_skill(db, song_id, leaf)

    song = (await _snapshot(db, uid, song_id=song_id)).song
    assert song.song_id == song_id and song.bpm == 120
    assert song.readiness == pytest.approx(0.5)
    assert song.section_skill_node_id == str(leaves[1])  # the 0.2
    assert song.skill_node_ids == frozenset(str(x) for x in leaves)


async def test_song_with_no_skills_has_unknown_readiness(db):
    """None, not 0.0 — assemble.py reads None as tempo_factor 0.70.

    A 0.0 would also mean 'slowly', but it would claim the song is KNOWN to be
    beyond the user rather than simply unmapped.
    """
    uid = await _make_user(db)
    song_id = await _make_song(db, uid, category="working_on")
    song = (await _snapshot(db, uid, song_id=song_id)).song
    assert song is not None and song.readiness is None
    assert song.section_skill_node_id is None


async def test_song_belonging_to_another_user_is_not_loaded(db):
    """Deleted or foreign song -> no repertoire block, not a 500."""
    other = await _make_user(db)
    song_id = await _make_song(db, other, category="working_on")
    uid = await _make_user(db)
    assert (await _snapshot(db, uid, song_id=song_id)).song is None


async def test_song_without_bpm_is_not_loaded(db):
    """Every repertoire item is planned at a tempo; a song with no bpm has none."""
    uid = await _make_user(db)
    song_id = await _make_song(db, uid, category="working_on", bpm=None)
    assert (await _snapshot(db, uid, song_id=song_id)).song is None


async def test_no_song_id_loads_no_song(db):
    """Rule 2 of the header: this module never selects a song."""
    uid = await _make_user(db)
    await _make_song(db, uid, category="working_on")
    assert (await _snapshot(db, uid)).song is None


# ---------------------------------------------------------------------------
# Consolidation songs (§5.4) — end on a win
# ---------------------------------------------------------------------------

async def test_can_play_songs_order_most_recently_played_first(db):
    uid = await _make_user(db)
    old = await _make_song(db, uid)
    recent = await _make_song(db, uid)
    never = await _make_song(db, uid)
    await _rate_song(db, uid, old, day=DAY - timedelta(days=30))
    await _rate_song(db, uid, recent, day=DAY - timedelta(days=2))

    ordered = [s.song_id for s in (await _snapshot(db, uid)).can_play_songs]
    assert ordered.index(recent) < ordered.index(old) < ordered.index(never)


async def test_can_play_excludes_other_categories_and_missing_bpm(db):
    uid = await _make_user(db)
    winner = await _make_song(db, uid, category="can_play", bpm=80)
    await _make_song(db, uid, category="working_on", bpm=80)
    await _make_song(db, uid, category="aspirational", bpm=80)
    await _make_song(db, uid, category="can_play", bpm=None)

    songs = (await _snapshot(db, uid)).can_play_songs
    assert [s.song_id for s in songs] == [winner]
    assert songs[0].bpm == 80


# ---------------------------------------------------------------------------
# Recency (§5.2)
# ---------------------------------------------------------------------------

async def test_recent_drills_come_from_the_last_two_terminal_sessions(db):
    uid = await _make_user(db)
    (leaf,) = await _make_root_chain(db, uid, "Rhythm", 0.5)
    d_old, d_mid, d_new = [await _make_drill(db, uid, leaf) for _ in range(3)]

    for offset, did in ((3, d_old), (2, d_mid), (1, d_new)):
        sid = await _make_session(db, uid, day=DAY - timedelta(days=offset))
        await _make_item(db, sid, index=0, drill_id=did)

    recent = (await _snapshot(db, uid)).recent_drill_ids
    assert recent == {str(d_mid), str(d_new)}


async def test_unreached_items_are_not_recent(db):
    """A drill the user never got to is not a drill they just did.

    Suppressing it would cost the user that material for two sessions over an item
    that never appeared on screen.
    """
    uid = await _make_user(db)
    (leaf,) = await _make_root_chain(db, uid, "Rhythm", 0.5)
    done, skipped, unreached, started = [
        await _make_drill(db, uid, leaf) for _ in range(4)
    ]

    sid = await _make_session(db, uid, day=DAY - timedelta(days=1))
    await _make_item(db, sid, index=0, drill_id=done, state="completed")
    await _make_item(db, sid, index=1, drill_id=started, state="in_progress")
    await _make_item(db, sid, index=2, drill_id=skipped, state="skipped")
    await _make_item(db, sid, index=3, drill_id=unreached, state="not_reached")

    assert (await _snapshot(db, uid)).recent_drill_ids == {str(done), str(started)}


# ---------------------------------------------------------------------------
# The module's two standing promises
# ---------------------------------------------------------------------------

async def test_local_day_is_computed_from_the_offset(db):
    """D-10. Two offsets either side of UTC midnight must land on different days.

    UTC+14 and UTC-12 are 26 hours apart, so the two local dates are ALWAYS 1 or 2
    days apart, never 0 — which of the two depends on where the UTC time-of-day
    sits. The old assertion accepted 0 and rejected 2, so it went red on any run
    whose UTC clock was inside the ~2-hour window where both sides wrap
    (e.g. 10:20 UTC: ahead = the 25th, behind = the 23rd).
    """
    ahead = await local_day(db, 840)    # UTC+14
    behind = await local_day(db, -720)  # UTC-12
    assert (ahead - behind).days in (1, 2)
    assert ahead > behind


async def test_loading_a_snapshot_writes_nothing(db):
    """Rule 1. The generator must be safe to run speculatively — preview, replay, test."""
    uid = await _make_user(db, '{"session_length_min": 45}')
    (leaf,) = await _make_root_chain(db, uid, "Rhythm", 0.4)
    await _make_drill(db, uid, leaf)
    await _make_session(db, uid, day=DAY - timedelta(days=1), completion_ratio=0.7)

    counts = lambda: db.execute(text(  # noqa: E731
        "SELECT (SELECT count(*) FROM practice_sessions) a, "
        "       (SELECT count(*) FROM practice_session_items) b, "
        "       (SELECT count(*) FROM drill_progress) c, "
        "       (SELECT count(*) FROM drill_attempts) d, "
        "       (SELECT count(*) FROM user_sessions) e"
    ))
    before = (await counts()).one()
    await _snapshot(db, uid)
    assert (await counts()).one() == before


async def test_same_user_same_day_same_plan(db):
    """The issue's acceptance criterion, end to end.

    Two independent loads of the same user must produce the same snapshot AND the
    same ordered plan — which is the whole argument for keeping an LLM out of the
    write path. This runs the real loader into the real assembler; nothing here is
    a literal.
    """
    uid = await _make_user(db, '{"session_length_min": 45}')
    rhythm = await _make_root_chain(db, uid, "Rhythm", 0.2, 0.35)
    lead = await _make_root_chain(db, uid, "Lead", 0.6, 0.55)
    for leaf in rhythm + lead:
        for _ in range(2):
            await _make_drill(db, uid, leaf)

    song_id = await _make_song(db, uid, category="working_on", bpm=110)
    for leaf in rhythm:
        await _link_song_skill(db, song_id, leaf)

    win = await _make_song(db, uid, category="can_play", bpm=85)
    await _rate_song(db, uid, win, day=DAY - timedelta(days=4))
    await _make_session(db, uid, day=DAY - timedelta(days=1), completion_ratio=0.8)

    first = await _snapshot(db, uid, song_id=song_id)
    second = await _snapshot(db, uid, song_id=song_id)
    assert first == second

    plan_a, plan_b = build_plan(first), build_plan(second)
    assert plan_a == plan_b

    # And the preference actually reached the plan, rather than the 30-minute default
    # surviving because nothing read it — the exact bug Task 5 exists to fix.
    assert first.target_minutes == 45
    assert plan_a.target_minutes == 45
    assert plan_a.items, "a stocked bank must produce items"
