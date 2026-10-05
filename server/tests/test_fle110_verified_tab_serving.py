"""FLE-110 — the verified tab is what actually gets served.

Two things need proving, and they are different things.

1. WHAT THE COLUMN HOLDS IS STILL GROUND TRUTH. `fle103_verify_pilot_tabs.py`
   checks the committed FILE. This module reads the rows back OUT of
   `song_catalog.verified_tab` and runs the same eight checks on what the
   database actually contains, so a shaping bug in `app/verified_tabs.py` or a
   lossy JSONB round-trip cannot pass as verified. The migration docstring
   promises exactly this, which is why it is here and not left to the script.

2. A PILOT USER REACHES IT. The issue's check, verbatim: "a pilot user at
   player_level 0.20 / 0.45 / 0.70 gets a verified song with at least one
   alternative on reroll."

Both run against a REAL Postgres, for the reason test_today_song_selector.py
gives: the restriction is a SQL clause inside a CTE and mocking it would
validate nothing. They also require the loader to have run — a database with
migration 0016 applied but no rows loaded skips rather than fails, because
"nobody has loaded the set here" is a setup state and not a defect in the code
under test.

    server/.venv/bin/python -m pytest tests/test_fle110_verified_tab_serving.py

NO LLM, NO SPEND: every test reads stored rows or runs the selector CTE.
"""
import json
import os
import pathlib
import sys
import uuid
from decimal import Decimal
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.song import Breakdown, is_renderable_breakdown
from app.selectors.today_song import select_today_song
from app.verified_tabs import (
    PILOT_PLAYER_LEVEL_CAP,
    PILOT_VERIFIED_ONLY_ENV,
    PROVENANCE_KEY,
    effective_player_level_cap,
    has_verified_tab,
    pilot_verified_only,
    verified_breakdown_for,
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))


# ---------------------------------------------------------------------------
# Test DB helpers — same shape as tests/test_today_song_selector.py
# ---------------------------------------------------------------------------

def _make_test_db_url() -> str:
    raw = os.environ.get(
        "DATABASE_URL", "postgresql://gt:devpass@localhost:5433/guitar_trainer"
    )
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return raw if prefix.endswith("asyncpg://") else raw.replace(
                prefix, "postgresql+asyncpg://", 1
            )
    return raw


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(_make_test_db_url(), echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def verified_rows(db: AsyncSession) -> list[dict]:
    """Every loaded verified row, or skip.

    Skips rather than fails when the column is missing or empty: that is
    "the loader has not run on this database", a setup state. It does NOT skip
    when rows exist, so a shaping regression still fails loudly.
    """
    column = await db.scalar(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'song_catalog' AND column_name = 'verified_tab'"
        )
    )
    if not column:
        pytest.skip("song_catalog.verified_tab absent — run `alembic upgrade head`")
    rows = (
        await db.execute(
            text(
                "SELECT title, artist, difficulty, verified_tab FROM song_catalog "
                "WHERE verified_tab IS NOT NULL ORDER BY difficulty"
            )
        )
    ).mappings().all()
    if not rows:
        pytest.skip(
            "no verified_tab rows — run scripts/fle110_load_verified_tabs.py first"
        )
    return [dict(r) for r in rows]


@pytest_asyncio.fixture
async def pilot_flag_on(monkeypatch) -> None:
    monkeypatch.setenv(PILOT_VERIFIED_ONLY_ENV, "1")


async def _make_user(db: AsyncSession, player_level: Decimal) -> uuid.UUID:
    """A user with NO songs and one leaf node whose mastery IS the player level.

    player_level is AVG(mastery) over leaf skill_nodes (selectors/player_level.py),
    so a single leaf at the target value sets it exactly. No songs, so
    working_on_pick and user_bench_pick are both empty and the catalog branch —
    the branch the issue's check is about — is the one that fires.
    """
    user_id = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:id, '{}'::jsonb)"),
        {"id": str(user_id)},
    )
    await db.execute(
        text(
            "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
            "VALUES (:id, :uid, 'FLE-110 probe', 'leaf', :m)"
        ),
        {"id": str(uuid.uuid4()), "uid": str(user_id), "m": player_level},
    )
    await db.commit()
    return user_id


async def _drop_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    for stmt in (
        "DELETE FROM user_sessions WHERE user_id = :uid",
        "DELETE FROM song_skills WHERE song_id IN "
        "  (SELECT id FROM songs WHERE user_id = :uid)",
        "DELETE FROM songs WHERE user_id = :uid",
        "DELETE FROM skill_nodes WHERE user_id = :uid",
        "DELETE FROM users WHERE id = :uid",
    ):
        await db.execute(text(stmt), {"uid": str(user_id)})
    await db.commit()


async def _song_identity(db: AsyncSession, song_id: int) -> tuple[str, str]:
    row = (
        await db.execute(
            text("SELECT title, artist FROM songs WHERE id = :id"), {"id": song_id}
        )
    ).mappings().one()
    return row["title"], row["artist"]


# ---------------------------------------------------------------------------
# 1. What the column holds is still ground truth
# ---------------------------------------------------------------------------

def _chord_sequence_from_notes(breakdown: dict) -> list[str]:
    """Recover the bar-by-bar chord names out of the stored technique note.

    `verified_tabs._technique_notes` writes them as "1. Cm7 · 2. Fm7 · ...", so
    they are recoverable — and reading them back from the STORED note rather
    than from the file is the point. The verifier checks every measure's notes
    against the chord name for that bar, so if the stored note and the stored
    tab ever disagreed, this is the test that would catch it.
    """
    for note in breakdown.get("technique_notes") or []:
        if note.get("heading") == "Bar by bar":
            return [
                part.split(".", 1)[1].strip()
                for part in note["body"].split("·")
                if "." in part
            ]
    return []


def test_stored_rows_pass_the_fle103_checks(verified_rows, tmp_path):
    """Reconstruct a verified set FROM THE DATABASE and run the eight checks.

    `catalog_difficulty` comes from the `song_catalog` row rather than the file's
    copy of it, which is the stronger reading: the selector windows on the
    catalog column, so that is the number whose coverage matters.
    """
    from fle103_verify_pilot_tabs import main as verify

    doc = {
        "schema_version": 2,
        "songs": [
            {
                "title": row["title"],
                "artist": row["artist"],
                "catalog_difficulty": float(row["difficulty"]),
                "chord_sequence": _chord_sequence_from_notes(row["verified_tab"]),
                "tab": row["verified_tab"]["tab"],
            }
            for row in verified_rows
        ],
    }
    path = tmp_path / "from_database.json"
    path.write_text(json.dumps(doc))

    assert verify(str(path)) == 0, (
        "rows read back out of song_catalog.verified_tab FAIL the FLE-103 checks "
        "— the stored tab is not the ground truth that was verified"
    )


def test_every_stored_row_parses_as_breakdown(verified_rows):
    """The endpoint parses these with `Breakdown.model_validate`; so does this.

    Also asserts `is_renderable_breakdown`, because song_of_day's
    `_breakdown_available` and the breakdown endpoint's cache branch both gate on
    it — a stored row that parsed but read as unrenderable would make the CTA
    promise a breakdown the client then cannot draw (FLE-67).
    """
    for row in verified_rows:
        stored = row["verified_tab"]
        assert "drills" not in stored, (
            f"{row['title']}: stored Breakdown carries a `drills` key. Drills are "
            "out of FLE-110's scope, and an explicit empty list trips "
            "Breakdown.drills min_length=2 on read-back"
        )
        parsed = Breakdown.model_validate(stored)
        assert parsed.tab.measures, f"{row['title']}: no measures"
        assert is_renderable_breakdown(stored), (
            f"{row['title']}: parses but is_renderable_breakdown() is False — the "
            "CTA would promise a breakdown the client drops to None"
        )


def test_stored_rows_carry_loader_provenance(verified_rows):
    """Every row names the file revision it came from.

    The loader's idempotence is keyed on this sha256 (it is how a re-run reports
    `unchanged 10` instead of rewriting), so a row missing it would be rewritten
    on every run and, worse, would make a regenerated set undetectable.
    """
    for row in verified_rows:
        prov = row["verified_tab"].get(PROVENANCE_KEY)
        assert prov, f"{row['title']}: no {PROVENANCE_KEY}"
        assert prov.get("sha256"), f"{row['title']}: provenance has no sha256"
        assert prov.get("verified_by") == "FLE-103"


def test_chord_diagrams_do_not_contradict_the_tab(verified_rows):
    """Each diagram is the voicing from the first bar that declares that chord.

    `_chord_diagrams` derives diagrams off the tab precisely so the two cannot
    disagree; this is the assertion that keeps that true. Strings absent from
    the voicing must be `fret: -1` (the diagram's "not played"), and a chord
    whose voicing does not fit the renderer's 4-fret window must have been
    dropped rather than drawn partially.
    """
    for row in verified_rows:
        stored = row["verified_tab"]
        sequence = _chord_sequence_from_notes(stored)
        measures = stored["tab"]["measures"]
        by_name = {c["name"]: c for c in stored.get("chords") or []}

        for name, measure in zip(sequence, measures):
            diagram = by_name.pop(name, None)
            if diagram is None:
                continue  # already checked, or dropped for the fret window
            sounded = {n["string"]: n["fret"] for n in measure["beats"][0]["notes"]}
            assert len(diagram["positions"]) == 6, f"{row['title']} {name}"
            for pos in diagram["positions"]:
                expected = sounded.get(pos["string"], -1)
                assert pos["fret"] == expected, (
                    f"{row['title']} {name}: diagram says string "
                    f"{pos['string']} fret {pos['fret']}, the tab beside it plays "
                    f"{expected}"
                )
        assert not by_name, (
            f"{row['title']}: diagrams for chords the tab never declares: "
            f"{sorted(by_name)}"
        )


# ---------------------------------------------------------------------------
# 2. The flag, and the cap
# ---------------------------------------------------------------------------

def test_flag_defaults_off_and_unparseable_reads_off(monkeypatch):
    """A typo must not silently narrow the catalog to ten songs."""
    monkeypatch.delenv(PILOT_VERIFIED_ONLY_ENV, raising=False)
    assert pilot_verified_only() is False
    assert effective_player_level_cap() == Decimal("1.0")

    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv(PILOT_VERIFIED_ONLY_ENV, truthy)
        assert pilot_verified_only() is True, truthy
        assert effective_player_level_cap() == PILOT_PLAYER_LEVEL_CAP

    for junk in ("maybe", "tru", "2", "off!"):
        monkeypatch.setenv(PILOT_VERIFIED_ONLY_ENV, junk)
        assert pilot_verified_only() is False, junk
        assert effective_player_level_cap() == Decimal("1.0"), junk


@pytest.mark.asyncio
async def test_lookups_refuse_a_blank_artist(db: AsyncSession, verified_rows):
    """Title-only matching would serve one song's tab under another's name."""
    row = verified_rows[0]
    assert await verified_breakdown_for(db, row["title"], row["artist"]) is not None
    assert await has_verified_tab(db, row["title"], row["artist"]) is True

    assert await verified_breakdown_for(db, row["title"], None) is None
    assert await verified_breakdown_for(db, row["title"], "   ") is None
    assert await has_verified_tab(db, row["title"], None) is False
    # Case-insensitive, matching song_catalog_title_artist_uidx.
    assert await has_verified_tab(db, row["title"].upper(), row["artist"].upper())


@pytest.mark.asyncio
async def test_cap_binds_the_window_a_high_level_user_sees(
    db: AsyncSession, verified_rows, pilot_flag_on
):
    """A user earned to 0.95 is windowed as 0.70, not as 0.95.

    Without the cap this user's window is 0.80-1.00, which holds exactly one
    verified song (Giant Steps, 0.95) and a reroll that cannot move — the FLE-49
    collapse in miniature, which is what scope item 4 is for. The assertion is
    that the level the CTE windows on is the capped one.
    """
    user_id = await _make_user(db, Decimal("0.95"))
    try:
        level = await db.scalar(
            text(
                "SELECT LEAST(COALESCE(AVG(mastery), 0.5), CAST(:cap AS numeric))"
                "::numeric(4,3) FROM skill_nodes "
                "WHERE user_id = CAST(:uid AS uuid) AND level = 'leaf'"
            ),
            {"uid": str(user_id), "cap": effective_player_level_cap()},
        )
        assert level == PILOT_PLAYER_LEVEL_CAP, (
            f"a 0.95 user windows on {level}, not the {PILOT_PLAYER_LEVEL_CAP} cap"
        )

        song_id, _, _ = await select_today_song(db, user_id, 0, force_reroll=False)
        assert song_id is not None, "capped user got no song at all"
        title, artist = await _song_identity(db, song_id)
        assert await has_verified_tab(db, title, artist), (
            f"a 0.95 pilot user was served {title!r}, which has no verified tab"
        )
    finally:
        await _drop_user(db, user_id)


# ---------------------------------------------------------------------------
# 3. The issue's check, verbatim
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("player_level", ["0.20", "0.45", "0.70"])
async def test_pilot_user_gets_a_verified_song_with_an_alternative_on_reroll(
    db: AsyncSession, verified_rows, pilot_flag_on, player_level
):
    """The issue's Check, at each of the three levels it names.

    Asserted in two parts because they can fail separately: the song served is
    verified (scope item 3), and a reroll can move off it (scope item 4). The
    reroll passes `exclude_song_id` because that is what the endpoint does
    (FLE-98) — without it a reroll landing on the same row proves nothing.
    """
    user_id = await _make_user(db, Decimal(player_level))
    try:
        first_id, _, _ = await select_today_song(db, user_id, 0, force_reroll=False)
        assert first_id is not None, (
            f"player_level {player_level}: no song at all — a DEAD window"
        )
        first = await _song_identity(db, first_id)
        assert await has_verified_tab(db, *first), (
            f"player_level {player_level}: served {first[0]!r}, not hand-verified"
        )

        second_id, _, _ = await select_today_song(
            db, user_id, 0, force_reroll=True, exclude_song_id=first_id
        )
        assert second_id is not None and second_id != first_id, (
            f"player_level {player_level}: reroll cannot move off {first[0]!r} "
            "— one song and a dead reroll, the FLE-49 collapse"
        )
        second = await _song_identity(db, second_id)
        assert await has_verified_tab(db, *second), (
            f"player_level {player_level}: reroll served {second[0]!r}, "
            "not hand-verified"
        )
    finally:
        await _drop_user(db, user_id)


@pytest.mark.asyncio
async def test_flag_off_does_not_restrict_the_catalog(
    db: AsyncSession, verified_rows, monkeypatch
):
    """With the flag off this is the pre-FLE-110 selector.

    Checked by counting eligible catalog rows under each flag value rather than
    by sampling the random pick: the selector is seeded per (user, day) so one
    call cannot distinguish "unrestricted" from "got lucky". 0.45's window holds
    unverified catalog songs, so the two counts must differ.
    """
    monkeypatch.delenv(PILOT_VERIFIED_ONLY_ENV, raising=False)
    eligible = text(
        "SELECT count(*) FROM song_catalog sc "
        "WHERE ABS(sc.difficulty - 0.45) <= 0.15 "
        "  AND (NOT CAST(:verified_only AS boolean) OR sc.verified_tab IS NOT NULL)"
    )
    unrestricted = await db.scalar(eligible, {"verified_only": False})
    restricted = await db.scalar(eligible, {"verified_only": True})

    assert restricted >= 2, (
        f"only {restricted} verified songs around 0.45 — a pilot user there has "
        "no reroll variety"
    )
    assert unrestricted > restricted, (
        "the verified filter changes nothing at 0.45, so this test cannot tell "
        "whether the flag is honoured"
    )

    user_id = await _make_user(db, Decimal("0.45"))
    try:
        song_id, _, _ = await select_today_song(db, user_id, 0, force_reroll=False)
        assert song_id is not None, "flag off: user got no song"
    finally:
        await _drop_user(db, user_id)


# ---------------------------------------------------------------------------
# 4. The endpoint actually serves it — over the wire, over a cached generation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_endpoint_serves_the_verified_tab_over_a_cached_generated_one(
    db: AsyncSession, verified_rows, pilot_flag_on, monkeypatch
):
    """The whole point of the issue, asserted through HTTP.

    The seeded song is one of the verified ten AND already carries a cached
    generated breakdown with `breakdown_generated_at` set. Before FLE-110 that
    row is a cache hit and the generated tab is what ships. The assertion is
    that the response carries the HAND-VERIFIED measures instead — serving the
    cached one here would be exactly the generator output FLE-102 measured at 0
    of 10 fit to practise from, under a song the pilot was told was checked.

    The generator is patched to raise, so a quiet fall-through to generation
    fails loudly rather than spending (NO LLM, NO SPEND).
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async def _never(*args, **kwargs):
        raise RuntimeError("generator called for a hand-verified song (FLE-110)")

    monkeypatch.setattr("app.api.v1.breakdowns.run_technique_breakdown", _never)

    row = verified_rows[0]
    verified_measures = row["verified_tab"]["tab"]["measures"]
    # A renderable but DIFFERENT cached breakdown: one measure, so a response
    # built from it is unmistakable.
    cached = {
        "tab": {
            "tuning": row["verified_tab"]["tab"]["tuning"],
            "measures": [verified_measures[0]],
        },
        "chords": [],
        "technique_notes": [{"heading": "Generated", "body": "the cached one"}],
    }

    user_id = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:id, '{}'::jsonb)"),
        {"id": str(user_id)},
    )
    await db.execute(
        text(
            "INSERT INTO songs (title, artist, genre, difficulty, bpm, key, "
            "  breakdown, breakdown_generated_at, user_id, category) "
            "VALUES (:t, :a, 'Rock', 'intermediate', 90, 'E', "
            "  CAST(:bd AS jsonb), now(), CAST(:uid AS uuid), 'working_on')"
        ),
        {
            "t": row["title"],
            "a": row["artist"],
            "bd": json.dumps(cached),
            "uid": str(user_id),
        },
    )
    song_id = await db.scalar(
        text("SELECT id FROM songs WHERE user_id = CAST(:uid AS uuid)"),
        {"uid": str(user_id)},
    )
    await db.commit()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/songs/{song_id}/breakdown",
                headers={"X-User-ID": str(user_id)},
            )
        assert resp.status_code == 200, resp.text
        served = resp.json()["breakdown"]

        assert len(served["tab"]["measures"]) == len(verified_measures), (
            f"served {len(served['tab']['measures'])} measures; the verified tab "
            f"has {len(verified_measures)} and the cached generated one has 1 — "
            "the cache won, which is the bug FLE-110 exists to fix"
        )
        headings = {n["heading"] for n in served["technique_notes"]}
        assert "Generated" not in headings, (
            "served the cached generated technique notes, not the verified ones"
        )

        # The free view is still charged against the cap — serving a stored tab
        # spends nothing, but the cap counts VIEWS (see breakdowns.py step 2b).
        views = await db.scalar(
            text(
                "SELECT count(*) FROM governor_calls "
                "WHERE user_id = CAST(:uid AS uuid) AND feature = 'breakdown'"
            ),
            {"uid": str(user_id)},
        )
        assert views == 1, (
            f"{views} governor_calls rows for one verified view — a free path that "
            "does not record is an uncapped free breakdown (the FLE-39 hole)"
        )
    finally:
        for stmt in (
            "DELETE FROM governor_calls WHERE user_id = CAST(:uid AS uuid)",
            "DELETE FROM user_sessions WHERE user_id = CAST(:uid AS uuid)",
            "DELETE FROM song_skills WHERE song_id IN "
            "  (SELECT id FROM songs WHERE user_id = CAST(:uid AS uuid))",
            "DELETE FROM songs WHERE user_id = CAST(:uid AS uuid)",
            "DELETE FROM skill_nodes WHERE user_id = CAST(:uid AS uuid)",
            "DELETE FROM users WHERE id = CAST(:uid AS uuid)",
        ):
            await db.execute(text(stmt), {"uid": str(user_id)})
        await db.commit()
