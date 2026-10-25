"""Alembic migration 0019 tests — the six FLE-130 catalog rows.

FLE-131. Migration 0019 transcribes a roster decision (Junie, FLE-130 comment
2026-10-05): six accepted rows, each with an explicit genre, primary_skill_root,
difficulty, tuning and rights_status.

Why this file is mostly source-level
------------------------------------
0019 has no logic to test. It is a literal table and one INSERT, so there is no
branch to cover and no state machine to drive. What *can* go wrong is a value
drifting away from the decision it is supposed to record — and that is a
source-level property, invisible to any assertion about the end state.

The prod-path behaviour was verified by hand on 2026-10-05 against a throwaway
database rebuilt to prod's exact drift (`upgrade 0014`, `stamp 0015`, column
absent), then driven through the single `alembic upgrade head` the deploy runs:
0016 -> 0017 -> 0018 -> 0019, ending at 70 rows / 69 distinct titles, zero rows
on the difficulty default, and the only 'unknown' being Black Mountain Side's
deliberate one. Down-and-up round-trip returned 70 -> 64 -> 70, and re-running
the INSERT with the rows already present was a clean no-op. That needs an
18-migration chain on a scratch DB, so it lives on FLE-131, not in the suite.

What this file guards
---------------------
Four things, in descending order of how badly they fail:

1. **`down_revision == "0018"`.** This is the only thing stopping 0019 from
   running against prod's missing `rights_status` column. Prod is stamped 0015
   without having run it (FLE-129), so an INSERT naming `rights_status` raises
   UndefinedColumn until 0018's repair lands. Re-parent 0019 ahead of 0018 and
   the next deploy fails in pre-deploy. The chain enforces the ordering; this
   test enforces the chain.

2. **Both *Big Love* rows share one rights_status.** 0015's `_RIGHTS_STATUS` map
   is keyed on title alone, which was sound when all titles were distinct. 0019
   breaks that premise by inserting two *Big Love* rows, and the map survives
   only because both resolve to 'copyrighted' — there is nothing for the single
   key to lose. That is a property of Junie's decision, not of the schema, so it
   needs an assertion rather than a comment.

3. **No row carries difficulty 0.0.** The column's DEFAULT is 0.0, and the
   selector gate is `ABS(sc.difficulty - pl.player_level) <= 0.15`, so a row left
   on the default is not inert — it is served to players at level <= 0.15 and
   nobody else, i.e. to brand-new users on day one. Unsetting difficulty is a
   louder decision than it looks.

4. **Every inserted title is named in 0015's map.** 0019's rows are live rows the
   moment it runs, and test_alembic_0015.py asserts the map covers the live
   catalog in both directions. Adding a row here without adding the title there
   breaks a *different* file's test, which is a confusing way to find out.
"""
import ast
import decimal
import os
import pathlib
import re

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
_P_0015 = _VERSIONS / "0015_song_catalog_rights_status.py"
_P_0019 = _VERSIONS / "0019_insert_fle130_catalog_rows.py"

# AST rather than import, for the reason spelled out in test_alembic_0007.py:
# conftest.py puts `server/` on sys.path[0], where the local `server/alembic/`
# package shadows the installed alembic distribution, so exec_module() on a
# migration raises ImportError under pytest.
_SRC_0019 = _P_0019.read_text()


def _consts(path: pathlib.Path, wanted: tuple[str, ...]) -> dict:
    """Module-level literal constants, by name.

    Handles both `X = ...` and the annotated `X: T = ...` form — 0019's `_ROWS`
    carries a type annotation, which parses as ast.AnnAssign rather than
    ast.Assign and is invisible to test_alembic_0018.py's version of this helper.
    """
    found = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target] if node.value is not None else []
        else:
            continue
        for target in targets:
            if target.id in wanted:
                found[target.id] = ast.literal_eval(node.value)

    missing = set(wanted) - set(found)
    assert not missing, f"{path.name} no longer defines {missing} as module-level literals"
    return found


_C_0019 = _consts(_P_0019, ("_ROWS", "revision", "down_revision"))
_ROWS = _C_0019["_ROWS"]
_RIGHTS_STATUS_0015 = _consts(_P_0015, ("_RIGHTS_STATUS",))["_RIGHTS_STATUS"]

# Junie's FLE-130 decision table, retyped here from the issue comment rather than
# derived from _ROWS — a test that reads its expectation out of the code under
# test would pass through any edit. Order is the comment's order.
_DECIDED = [
    ("Cause We've Ended as Lovers", "Jeff Beck", "Jazz Fusion", "lead", "0.800", "standard", "copyrighted"),
    ("Big Love", "Fleetwood Mac", "Pop Rock", "chord_voicings", "0.550", "standard", "copyrighted"),
    ("Big Love", "Lindsey Buckingham", "Pop Rock", "fingerstyle", "0.880", "standard", "copyrighted"),
    ("Layla", "Eric Clapton", "Blues Rock", "lead", "0.700", "standard", "copyrighted"),
    ("Beat It", "Michael Jackson", "Pop Rock", "rhythm", "0.480", "standard", "copyrighted"),
    ("Sweet Child O' Mine", "Guns N' Roses", "Hard Rock", "lead", "0.580", "eb_standard", "copyrighted"),
]

# song_catalog's live CHECK / enum domains, as of 0007 + 0014 + 0015.
_ALLOWED_TUNINGS = (
    "standard", "eb_standard", "drop_d", "drop_c", "open_d", "open_e", "open_g", "dadgad",
)
_ALLOWED_SKILL_ROOTS = (
    "rhythm", "lead", "chord_voicings", "fingerstyle", "music_theory", "timing",
)
_ALLOWED_RIGHTS = ("public_domain", "traditional", "copyrighted", "unknown")


# Test database setup — mirrors test_alembic_0015.py
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
    """Session-scoped async DB session — shares the conftest.py event loop.

    Cleanup is done by the autouse _release_transaction fixture below, NOT here —
    see test_alembic_0007.py's copy of this fixture for the full explanation.
    """
    async with SessionFactory() as session:
        yield session


@pytest.fixture(autouse=True)
async def _release_transaction(db):
    """Roll back after every test so no connection is checked out at session teardown.

    Same reasoning as test_alembic_0015.py: a session-scoped finalizer runs on a
    different event loop than the one asyncpg bound the connection to, so closing
    there raises 'Future attached to a different loop'.
    """
    yield
    await db.rollback()


# ---------------------------------------------------------------------------


def test_0019_follows_0018() -> None:
    """The ordering constraint that makes the INSERT safe on prod.

    Not style: prod is stamped 0015 without the `rights_status` column 0015 adds,
    so naming that column in an INSERT raises UndefinedColumn until 0018 repairs
    it. `down_revision = "0018"` is what guarantees the repair runs first inside
    the one `upgrade head` the deploy performs.
    """
    assert _C_0019["revision"] == "0019"
    assert _C_0019["down_revision"] == "0018", (
        "0019 INSERTs into song_catalog.rights_status, which prod does not have "
        "until 0018 adds it. Re-parenting 0019 off 0018 makes the next deploy "
        "fail in pre-deploy with UndefinedColumn."
    )


def test_rows_are_exactly_junies_decision() -> None:
    """Every field of every row matches the FLE-130 table, retyped independently.

    A migration that records a decision has exactly one failure mode: recording a
    different decision. Difficulty and primary_skill_root are the dangerous two —
    both are curation calls that look like free parameters to anyone editing the
    SQL, and both change who the song is served to.
    """
    assert _ROWS == _DECIDED, (
        "0019's _ROWS no longer matches Junie's accepted table on FLE-130. If the "
        "roster decision genuinely changed, change it on the issue first and "
        "update both sides — do not edit the migration alone."
    )


def test_both_big_love_rows_share_one_rights_status() -> None:
    """The premise that keeps 0015's title-keyed map sound.

    0015 keys `_RIGHTS_STATUS` on title alone and justifies it with "all 64 titles
    are distinct". 0019 ends that: two *Big Love* rows, same composition, two
    arrangements. A single key is still correct only because both rows carry the
    same rights_status. If that ever stops being true, the map, 0015 step 2's
    UPDATE, 0018's frozen copy, and the AST readers in test_alembic_0015.py all
    have to move to a (title, artist) key together.
    """
    by_title: dict[str, set[str]] = {}
    for title, _artist, _genre, _skill, _diff, _tuning, rights in _ROWS:
        by_title.setdefault(title, set()).add(rights)

    split = {t: v for t, v in by_title.items() if len(v) > 1}
    assert not split, (
        f"{split} — two rows share a title but disagree on rights_status, so "
        "0015's title-keyed map can only hold one of the two values and the "
        "other is silently lost. Re-key the map on (title, artist) across 0015, "
        "0018 and test_alembic_0015.py before landing this."
    )


def test_no_row_relies_on_the_difficulty_default() -> None:
    """Explicit difficulty on every row, strictly inside (0, 1].

    The column DEFAULT is 0.0 and the song-of-day gate is
    `ABS(difficulty - player_level) <= 0.15`, so 0.0 does not park a row out of
    reach — it targets it at players below level 0.15, which is every brand-new
    user. *Sweet Child O' Mine* as a day-one pick is the concrete version.
    """
    for title, artist, _genre, _skill, diff, _tuning, _rights in _ROWS:
        value = decimal.Decimal(diff)
        assert value > 0, f"{title} / {artist} is on the 0.0 default"
        assert value <= 1, f"{title} / {artist} difficulty {diff} violates the CHECK [0,1]"
        assert -value.as_tuple().exponent <= 3, (
            f"{title} / {artist} difficulty {diff} has more precision than "
            "NUMERIC(4,3) keeps, so the stored value will not equal this literal"
        )


def test_every_row_value_is_in_its_domain() -> None:
    """tuning, primary_skill_root and rights_status against the live CHECK/enum.

    These fail loudly at migration time rather than silently, but they fail
    during a deploy's pre-deploy step — which is a much worse place to find a
    typo than here.
    """
    for title, artist, genre, skill, _diff, tuning, rights in _ROWS:
        where = f"{title} / {artist}"
        assert tuning in _ALLOWED_TUNINGS, f"{where}: tuning {tuning!r}"
        assert skill in _ALLOWED_SKILL_ROOTS, f"{where}: primary_skill_root {skill!r}"
        assert rights in _ALLOWED_RIGHTS, f"{where}: rights_status {rights!r}"
        # genre is free text by 0007's contract, but it is NOT NULL with no
        # default, so an empty string is the one value that passes the schema and
        # means nothing.
        assert genre.strip(), f"{where}: genre is blank"


def test_no_duplicate_title_artist_pairs() -> None:
    """The INSERT would conflict with itself, which ON CONFLICT does not cover.

    `ON CONFLICT DO NOTHING` resolves a new row against rows already in the
    table; two identical keys inside one VALUES list are a cardinality violation
    instead. The two *Big Love* rows are the near miss — they are legal only
    because the unique index is on (lower(title), lower(artist)) and the artists
    differ.
    """
    keys = [(t.lower(), a.lower()) for t, a, *_rest in _ROWS]
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, (
        f"{dupes} appears twice in _ROWS. ON CONFLICT cannot absorb a duplicate "
        "within a single VALUES list — the INSERT raises "
        "'ON CONFLICT DO UPDATE command cannot affect row a second time'."
    )


def test_every_inserted_title_is_named_in_0015s_map() -> None:
    """0019's rows become live rows, and 0015's map must cover the live catalog.

    test_alembic_0015.py asserts the map names every live title and that the map
    names nothing the catalog lacks. Both directions break if a row lands here
    without a map entry, so catch it in the file that caused it.
    """
    missing = sorted({t for t, *_rest in _ROWS if t not in _RIGHTS_STATUS_0015})
    assert not missing, (
        f"{missing} inserted by 0019 but absent from 0015's _RIGHTS_STATUS map. "
        "Add them there in the same change — test_alembic_0015.py::"
        "test_no_row_left_on_the_default will otherwise fail against the live DB."
    )


def test_map_entries_agree_with_the_inserted_values() -> None:
    """0015's map and 0019's INSERT must not state different rights for one title."""
    conflicts = [
        (title, rights, _RIGHTS_STATUS_0015[title])
        for title, _a, _g, _s, _d, _tn, rights in _ROWS
        if title in _RIGHTS_STATUS_0015 and _RIGHTS_STATUS_0015[title] != rights
    ]
    assert not conflicts, (
        "0015's map and 0019's INSERT disagree (title, inserted, mapped): "
        f"{conflicts}. test_alembic_0015.py::test_every_live_value_matches_the_map "
        "will fail against the live database."
    )


def test_insert_names_rights_status_explicitly() -> None:
    """The rejected workaround must stay rejected.

    Omitting `rights_status` from the column list makes 0019 run on prod's broken
    schema today — and leaves six rows on the 'unknown' DEFAULT the moment the
    column appears, which is precisely the failure 0015's contract exists to
    catch. The fix for the ordering problem is 0018, not a narrower INSERT.
    """
    # Scoped to the INSERT's column list on purpose. An `in _SRC_0019` check
    # passes on the docstring alone — it mentions rights_status a dozen times
    # explaining why the column must be named — so it can never fail and proves
    # nothing. Verified by mutation: deleting rights_status from the column list
    # left that version of this test green.
    columns = re.search(
        r"INSERT INTO song_catalog\s*\(([^)]*)\)", _SRC_0019, re.IGNORECASE
    )
    assert columns, "0019's INSERT INTO song_catalog (...) column list is unparseable"
    named = {c.strip() for c in columns.group(1).split(",")}
    assert "rights_status" in named, (
        f"0019's INSERT names {sorted(named)} and omits rights_status. If this was "
        "done to make the migration run against prod's missing column, revert it "
        "— 0018 is the fix. Omitting it leaves six rows on the 'unknown' DEFAULT."
    )
    assert "difficulty" in named, (
        f"0019's INSERT names {sorted(named)} and omits difficulty, so every row "
        "inherits the 0.0 DEFAULT and is served to day-one users only."
    )
    assert "ON CONFLICT (lower(title), lower(artist))" in _SRC_0019, (
        "0019's ON CONFLICT no longer targets song_catalog_title_artist_uidx. A "
        "bare DO NOTHING matches only the primary key, which is gen_random_uuid() "
        "and never conflicts — so the INSERT stops being idempotent (0003's bug)."
    )


async def test_decided_rows_are_live_with_their_decided_values(db: AsyncSession) -> None:
    """End-state check against whatever database the suite is pointed at.

    This is the one database-level assertion worth keeping: it catches a hand-edit
    in prod that drifted a tier away from the roster decision, and it catches an
    INSERT whose ON CONFLICT silently matched an existing row with different
    values.
    """
    # Whole table, filtered in Python: the match key is (lower(title),
    # lower(artist)) and binding a text[] for it needs a CAST(... AS text[])
    # dance, because `:param::text[]` parses the cast's `::` as another bind.
    # 70 rows is not worth that.
    result = await db.execute(
        text(
            "SELECT title, artist, genre, primary_skill_root::text, "
            "difficulty, tuning, rights_status FROM song_catalog"
        )
    )
    live = {(r[0].lower(), r[1].lower()): tuple(r)[2:] for r in result}

    absent = [(t, a) for t, a, *_rest in _DECIDED if (t.lower(), a.lower()) not in live]
    assert not absent, f"0019's rows are not in song_catalog: {absent}"

    drifted = [
        {
            "row": f"{t} / {a}",
            "in_db": live[(t.lower(), a.lower())],
            "decided": (g, s, decimal.Decimal(d), tn, r),
        }
        for t, a, g, s, d, tn, r in _DECIDED
        if live[(t.lower(), a.lower())] != (g, s, decimal.Decimal(d), tn, r)
    ]
    assert not drifted, f"song_catalog disagrees with FLE-130's decision: {drifted}"
