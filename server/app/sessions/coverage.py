# server/app/sessions/coverage.py
#
# PART IV of the FLE-4 spec — §10, how the bank is GRADED.
#
# This module exists to answer one question that FLE-13 made answerable for the first
# time: is the drill bank good enough to put in front of a pilot user? §10.3 states
# that as a hard gate — every one of the PILOT_BAND_CELLS cells must hold at least
# MIN_CELL_STOCK active drills — and a cell is a (family, tier) pair, so before
# migration 0011 added `drills.family` there were no cells and the gate could not be
# evaluated at all. Not failing: unmeasurable. That distinction is the point of this
# module, and it is why `unclassified` is reported as loudly as the gate verdict.
#
# WHAT COUNTS TOWARD A CELL
# -------------------------
# status = 'active' only. Not duplicates (collapsed onto a canonical — counting both
# would double-count one drill), not retired, not pending_review. This is the same
# predicate app/sessions/select.py filters candidates on, deliberately: §10 has to
# grade what is SELECTABLE, or the gate passes on drills the session engine will
# never serve.
#
# A drill with a NULL family or a NULL tier stocks NO cell. It is not evidence of
# coverage anywhere, and it is reported separately as `unclassified` so a thin gate
# result can always be read correctly — "45 cells short" and "45 cells short because
# 300 drills are unclassified" call for opposite actions, and a bare cell count
# cannot tell them apart.
#
# The queries are raw SQL against `drills` rather than ORM aggregates because the
# whole module is one GROUP BY behind ix_drills_coverage, and snapshot.py's
# established style for read-only session queries is text() SQL.

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.sessions.taxonomy import (
    FAMILY_SPECS,
    PILOT_BAND_CELLS,
    PILOT_BAND_TIERS,
    VIABLE_CELLS,
    TechniqueFamily,
    Tier,
)

__all__ = [
    "MIN_CELL_STOCK",
    "Cell",
    "CoverageReport",
    "pilot_band_cells",
    "viable_cells",
    "load_coverage",
    "grade",
]

# §10.3 — a cell is STOCKED at two or more active drills. Two, not one, because a
# single drill in a cell means the selector has no choice there: the same drill comes
# back every session the band lands on that cell, and "varied practice" collapses to
# a loop the moment the user's mastery sits still.
MIN_CELL_STOCK = 2

Cell = tuple[TechniqueFamily, Tier]


def viable_cells() -> tuple[Cell, ...]:
    """Every (family, tier) the §8 grid says is MUSICALLY REAL, in enum order.

    Not the 90-cell rectangle — each family's tier_low/tier_high clamps it, because
    "extended jazz voicings at D1" is a mislabelled drill rather than a beginner one.
    Length is pinned to taxonomy.VIABLE_CELLS by a test.
    """
    return tuple(
        (f, t)
        for f in TechniqueFamily
        for t in Tier
        if FAMILY_SPECS[f].tier_low <= t <= FAMILY_SPECS[f].tier_high
    )


def pilot_band_cells() -> tuple[Cell, ...]:
    """The §10.3 gate's cells — viable cells restricted to the D2-D4 pilot band.

    The pilot cohort is intermediate-to-advanced, so D1 and D5 are out of band: the
    gate does not ask the bank to be complete, it asks it to be complete WHERE THE
    PILOT WILL ACTUALLY PLAY. Length is pinned to taxonomy.PILOT_BAND_CELLS.
    """
    return tuple((f, t) for (f, t) in viable_cells() if t in PILOT_BAND_TIERS)


@dataclass(frozen=True)
class CoverageReport:
    """§10 — what the bank stocks, and whether that clears the §10.3 gate.

    `stock` holds only NON-EMPTY cells; use `stock_of()` so an absent cell reads as 0
    rather than raising. That asymmetry is deliberate — an empty bank should produce
    an empty mapping, not 60 zero entries.
    """

    stock: Mapping[Cell, int]
    unclassified_family: int
    unclassified_tier: int
    total_active: int
    scope_user_id: Optional[UUID] = None

    def stock_of(self, family: TechniqueFamily, tier: Tier) -> int:
        return self.stock.get((family, tier), 0)

    @property
    def classified_active(self) -> int:
        """Active drills that actually stock a cell."""
        return sum(self.stock.values())

    def thin_cells(self, cells: tuple[Cell, ...]) -> tuple[Cell, ...]:
        """Cells below MIN_CELL_STOCK — what §10.4's gap-directed generation targets.

        Returned in grid order so two runs over the same bank produce the same
        worklist; a generation queue that reshuffles every read is not a queue.
        """
        return tuple(c for c in cells if self.stock_of(*c) < MIN_CELL_STOCK)

    def empty_cells(self, cells: tuple[Cell, ...]) -> tuple[Cell, ...]:
        """Cells with nothing in them at all. A subset of thin_cells."""
        return tuple(c for c in cells if self.stock_of(*c) == 0)

    @property
    def pilot_gate_passes(self) -> bool:
        """§10.3 — the HARD GATE. Every pilot-band cell at MIN_CELL_STOCK or better.

        Note what this does NOT do: it never returns True because there was nothing
        to check. An empty bank has 45 thin cells and fails, which is correct — the
        gate is a claim about stock, and no stock is not enough stock.
        """
        return not self.thin_cells(pilot_band_cells())

    def summary(self) -> str:
        """One line for a script's report or an issue comment."""
        band = pilot_band_cells()
        stocked = len(band) - len(self.thin_cells(band))
        verdict = "PASS" if self.pilot_gate_passes else "FAIL"
        return (
            f"§10.3 pilot gate: {verdict} — {stocked}/{len(band)} pilot-band cells at "
            f">={MIN_CELL_STOCK}; {self.classified_active}/{self.total_active} active "
            f"drills classified ({self.unclassified_family} missing family, "
            f"{self.unclassified_tier} missing tier)"
        )


# One GROUP BY behind ix_drills_coverage. The :user_id bind is nullable: passing NULL
# grades the WHOLE bank, which is the question §10.3 actually asks — the drill bank is
# graded globally even though its rows are per-user, because a cell that only one
# user's bank stocks is not coverage the pilot can rely on.
#
# The CAST is load-bearing, not decoration. A bare :user_id appearing only in `IS NULL`
# and an equality gives asyncpg nothing to infer a type from, and it refuses the
# statement with AmbiguousParameterError rather than guessing — so the scope bind has
# to name its own type.
_USER_SCOPE = "(CAST(:user_id AS uuid) IS NULL OR user_id = CAST(:user_id AS uuid))"

_STOCK_SQL = text(
    f"""
    SELECT family::text AS family, tier::text AS tier, count(*) AS stock
      FROM drills
     WHERE status = 'active'
       AND family IS NOT NULL
       AND tier IS NOT NULL
       AND {_USER_SCOPE}
     GROUP BY family, tier
    """
)

_TOTALS_SQL = text(
    f"""
    SELECT
        count(*)                                        AS total_active,
        count(*) FILTER (WHERE family IS NULL)          AS no_family,
        count(*) FILTER (WHERE tier IS NULL)            AS no_tier
      FROM drills
     WHERE status = 'active'
       AND {_USER_SCOPE}
    """
)


async def load_coverage(
    db: AsyncSession, *, user_id: Optional[UUID] = None
) -> CoverageReport:
    """Read §10 cell stock. `user_id=None` grades the whole bank (the §10.3 default).

    Two statements rather than one because they answer different questions over
    different row sets — stock is per-cell over CLASSIFIED rows, the totals are over
    all active rows including the unclassified ones the first query cannot see.
    """
    params = {"user_id": str(user_id) if user_id else None}

    stock: dict[Cell, int] = {}
    for row in (await db.execute(_STOCK_SQL, params)).all():
        try:
            cell = (TechniqueFamily(row.family), Tier(row.tier))
        except ValueError:
            # A family or tier the enums no longer know. Only reachable if the DB
            # type gained a value the code has not — skip rather than raise: a
            # coverage report that 500s is worse than one that undercounts by a row
            # it cannot name.
            continue
        stock[cell] = int(row.stock)

    totals = (await db.execute(_TOTALS_SQL, params)).one()
    return CoverageReport(
        stock=stock,
        unclassified_family=int(totals.no_family),
        unclassified_tier=int(totals.no_tier),
        total_active=int(totals.total_active),
        scope_user_id=user_id,
    )


def grade(report: CoverageReport) -> dict[str, object]:
    """The §10.3 verdict as a plain dict — for a script's JSON output or an API.

    `blocked_by_unclassified` is the field that matters when the gate fails: it says
    the failure may be a CLASSIFICATION gap rather than a CONTENT gap, and those have
    completely different fixes (run the backfill vs. generate more drills).
    """
    band = pilot_band_cells()
    thin = report.thin_cells(band)
    return {
        "pilot_gate_passes": report.pilot_gate_passes,
        "min_cell_stock": MIN_CELL_STOCK,
        "pilot_band_cells": len(band),
        "pilot_band_cells_stocked": len(band) - len(thin),
        "thin_cells": [f"{f.value}/{t.value}" for f, t in thin],
        "empty_cells": [f"{f.value}/{t.value}" for f, t in report.empty_cells(band)],
        "viable_cells": VIABLE_CELLS,
        "total_active_drills": report.total_active,
        "classified_active_drills": report.classified_active,
        "unclassified_family": report.unclassified_family,
        "unclassified_tier": report.unclassified_tier,
        "blocked_by_unclassified": bool(thin) and report.unclassified_family > 0,
        "summary": report.summary(),
    }


# Pinned here so a typo in taxonomy's FAMILY_SPECS surfaces as a failing import-time
# assertion in tests rather than as a coverage report that quietly grades against the
# wrong denominator.
assert len(viable_cells()) == VIABLE_CELLS
assert len(pilot_band_cells()) == PILOT_BAND_CELLS
