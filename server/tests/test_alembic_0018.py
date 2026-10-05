"""Alembic migration 0018 tests — the rights_status drift repair.

FLE-129. Prod `song_catalog` was stamped `alembic_version = 0015` without having
the `rights_status` column that 0015 adds, so `alembic upgrade head` ran 0016
onward and skipped 0015 permanently. 0018 is the forward repair.

Why this file is source-level and not database-level
----------------------------------------------------
The end state 0018 produces is already asserted, thoroughly, by
test_alembic_0015.py — same column, same constraint name, same 64-row tagging
contract, run against whatever the live database holds. Re-asserting it here
would add nothing: on any database these tests can reach, 0015 and 0018 are
indistinguishable by their results. That is the entire design goal.

What no existing test can see is the *drift scenario*, because reproducing it
needs a database stamped 0015 with the column absent — a throwaway DB, a
stamp-without-DDL, and a full 18-migration chain. That was verified by hand
against three freshly built databases on 2026-10-05 and the transcript is on
FLE-129; it is not cheap enough to live in the suite.

So this file guards the one thing that is both cheap to check and genuinely
fragile: 0018 carries a **frozen copy** of 0015's 64-title map rather than
importing it (see 0018's docstring for why). A copy can rot. The failure mode is
specific and silent — a curator revises a title's rights_status in 0015, 0018
still says the old value, and the next environment to hit the repair path gets
the stale answer with every test passing. test_the_two_maps_never_disagree is
the assertion that matters here; the others pin the properties that make the
repair safe to run twice.
"""
import ast
import pathlib

import pytest

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
_P_0015 = _VERSIONS / "0015_song_catalog_rights_status.py"
_P_0018 = _VERSIONS / "0018_repair_rights_status_missing.py"

# AST rather than import, for the reason spelled out in test_alembic_0007.py:
# conftest.py puts `server/` on sys.path[0], where the local `server/alembic/`
# package shadows the installed alembic distribution, so exec_module() on a
# migration raises ImportError under pytest.
_SRC_0018 = _P_0018.read_text()


def _consts(path: pathlib.Path, wanted: tuple[str, ...]) -> dict:
    tree = ast.parse(path.read_text())
    found = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in wanted
    }
    missing = set(wanted) - set(found)
    assert not missing, f"{path.name} no longer defines {missing} as module-level literals"
    return found


_WANTED = ("ALLOWED_RIGHTS_STATUSES", "_RIGHTS_STATUS", "revision", "down_revision")
C_0015 = _consts(_P_0015, _WANTED)
C_0018 = _consts(_P_0018, _WANTED)


# ---------------------------------------------------------------------------
# 1. The map copy must not rot — the load-bearing test
# ---------------------------------------------------------------------------

def test_the_two_maps_never_disagree() -> None:
    """Every title 0018 names must carry 0015's value for it.

    0018 may name FEWER titles than 0015: 0015's map is the project's live
    register of curated rows and grows as the catalog does (FLE-130/FLE-131
    added five), while 0018 is frozen at the 64 rows prod actually had on
    2026-10-05. That asymmetry is deliberate and is not a failure.

    The two maps disagreeing on a title that both name *is* a failure, and a
    silent one: 0015 is what the healthy path writes, 0018 is what the repair
    path writes, and nothing downstream can tell which path a given database
    took. One stale value here means two environments quietly hold different
    answers to "may a user post their clip of this song?".
    """
    m15, m18 = C_0015["_RIGHTS_STATUS"], C_0018["_RIGHTS_STATUS"]
    disagreements = sorted(
        (title, m18[title], m15[title]) for title in m18 if m15.get(title) != m18[title]
    )
    assert not disagreements, (
        "0018's frozen copy of the rights_status map disagrees with 0015 "
        f"(title, in_0018, in_0015): {disagreements}. 0015 is authoritative — "
        "update 0018 to match, and do not delete the title from 0018 to silence "
        "this: prod's repair path needs every row it actually has."
    )


def test_0018_names_no_title_0015_does_not() -> None:
    """The other direction: 0018 must not invent rows 0015 never curated.

    0018 re-applies 0015's tagging; a title present only in 0018 would mean the
    repair path writes a value no curator ever signed off on.
    """
    orphans = sorted(set(C_0018["_RIGHTS_STATUS"]) - set(C_0015["_RIGHTS_STATUS"]))
    assert not orphans, (
        f"0018 tags {len(orphans)} title(s) absent from 0015's map: {orphans}. "
        "Add them to 0015 (the curator record) or drop them from 0018."
    )


def test_allowed_statuses_are_identical() -> None:
    """Both migrations must build the CHECK from the same four values.

    0018 adds the constraint only when one of that name does not already exist,
    so a widened tuple here would not replace 0015's narrower constraint — it
    would simply never be applied, and the difference would surface as a write
    rejected on one environment and accepted on another.
    """
    assert tuple(C_0018["ALLOWED_RIGHTS_STATUSES"]) == tuple(
        C_0015["ALLOWED_RIGHTS_STATUSES"]
    ), (
        f"ALLOWED_RIGHTS_STATUSES diverged: 0018={C_0018['ALLOWED_RIGHTS_STATUSES']} "
        f"vs 0015={C_0015['ALLOWED_RIGHTS_STATUSES']}"
    )


# ---------------------------------------------------------------------------
# 2. The properties that make the repair safe to run anywhere
# ---------------------------------------------------------------------------

def test_add_column_is_conditional() -> None:
    """The ADD COLUMN must be `IF NOT EXISTS`.

    This is the whole reason 0018 exists rather than a stamp-back-and-re-run of
    0015: a bare ADD COLUMN fails with 'column already exists' on every
    environment where 0015 ran cleanly. Without this clause the repair fixes
    prod and breaks dev.
    """
    assert "ADD COLUMN IF NOT EXISTS rights_status" in _SRC_0018, (
        "0018's ALTER TABLE lost its IF NOT EXISTS — it will fail on any "
        "database where 0015 already added the column."
    )


def test_constraint_add_is_guarded() -> None:
    """The CHECK must be added only if one of that name is absent.

    Postgres has no ADD CONSTRAINT IF NOT EXISTS, so the guard is an explicit
    pg_constraint lookup. Dropping it makes a second apply fail.
    """
    assert "pg_constraint" in _SRC_0018 and "IF NOT EXISTS (" in _SRC_0018, (
        "0018's ADD CONSTRAINT lost its pg_constraint existence guard"
    )


def test_constraint_name_matches_0015() -> None:
    """Both migrations must use the same constraint name.

    0015's downgrade drops it by name and
    test_alembic_0015.py::test_check_constraint_is_named asserts it. If 0018
    created a differently-named constraint, a repaired database would end up
    carrying two CHECKs, and 0015's downgrade would leave one behind.
    """
    name = "song_catalog_rights_status_check"
    assert name in _SRC_0018, f"0018 no longer uses the constraint name {name!r}"
    assert name in _P_0015.read_text(), (
        f"0015 no longer uses {name!r} — the two migrations must agree"
    )


def test_downgrade_is_deliberately_empty() -> None:
    """0018's downgrade must stay a no-op.

    Not an oversight — see 0018's DOWNGRADE docstring. On dev the column and
    constraint belong to 0015, so dropping them here would destroy state 0018
    never created and leave 0015 stamped over a table missing its own column:
    the exact drift being repaired, manufactured in reverse. Removing rights_status
    is 0015's downgrade's job.

    This test exists because an empty function body looks like unfinished work
    to the next reader, and the cost of "tidying" it is a second drift incident.
    """
    tree = ast.parse(_SRC_0018)
    fn = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade"),
        None,
    )
    assert fn is not None, "0018 has no downgrade()"
    body = [n for n in fn.body if not isinstance(n, ast.Expr) or not isinstance(n.value, ast.Constant)]
    assert all(isinstance(n, ast.Pass) for n in body), (
        "0018.downgrade() now does something. If that is intentional, read the "
        "DOWNGRADE section of the migration docstring first — dropping the column "
        "here re-creates the FLE-129 drift on every environment where 0015 ran cleanly."
    )


# ---------------------------------------------------------------------------
# 3. Chain position
# ---------------------------------------------------------------------------

def test_0018_follows_0017() -> None:
    """0018 must sit after 0017, so `upgrade head` from prod's 0015 reaches it."""
    assert C_0018["revision"] == "0018"
    assert C_0018["down_revision"] == "0017", (
        f"0018.down_revision is {C_0018['down_revision']!r}, expected '0017' — "
        "a gap here means prod never reaches the repair."
    )


def test_0015_still_precedes_the_repair() -> None:
    """Sanity: 0015 is still the migration whose effect 0018 repairs.

    If 0015 were ever renumbered or its down_revision moved, the repair's
    premise — that prod is stamped 0015 with the column missing — stops
    describing anything real, and this file should be re-read rather than
    trusted.
    """
    assert C_0015["revision"] == "0015", (
        f"0015's revision id is now {C_0015['revision']!r}; FLE-129's premise was "
        "that prod is stamped exactly '0015'."
    )
