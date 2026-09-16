# server/app/selectors/player_level.py
# The single definition of "how skilled is this player", shared by every consumer.
#
# FLE-49 (2026-09-16): this module exists because the same expression was written
# twice — inline in the today_song CTE and again in breakdowns.py — and both copies
# carried the same bug. Prod measurement at the time: 16 users, 250 leaf skill_nodes,
# 3 nodes with mastery > 0, max mastery 0.050, and therefore *every* user resolving
# to player_level 0.00.
#
# Two things go wrong at 0.00:
#   1. The selector's catalog window ABS(difficulty - player_level) <= 0.15 contains
#      exactly one of the 64 catalog rows (Knockin' on Heaven's Door, difficulty 0.15).
#      Every bank draw, and every reroll, returns that same song.
#   2. breakdowns.py feeds player_level into the Sonnet teaching prompt, where the
#      scale is documented as "0.0 = beginner". Every user was being taught as an
#      absolute beginner regardless of what they told us at onboarding.
#
# Why COALESCE(AVG(mastery), 0.5) did not prevent this
# ----------------------------------------------------
# The COALESCE was written to give an unknown player a mid-catalog level, but it only
# fires when AVG returns NULL — i.e. when the user has no leaf nodes at all. Onboarding
# creates the leaves and seeds them, so AVG returns 0.0, not NULL, and the fallback is
# live code that never once executed for a real user. Right intent, wrong trigger.
#
# The pedagogy call (Nadia, FLE-49)
# ---------------------------------
# Mastery 0 is a *knowledge* state, not a *skill* state: it means "we have never
# observed this person play", which is not the claim "this person can play nothing".
# So there are two distinct fixes here and both are needed:
#
#   - PLAYER_LEVEL_FLOOR (this module) — never let absence of evidence read as
#     evidence of absence. A backstop that holds no matter what mastery does.
#   - Onboarding seeding (api/v1/users.py, _initial_leaf_mastery) — use the
#     can_play / working_on signal we already collect, so an intermediate player
#     starts where they actually are. That is the root-cause fix.
#
# The floor stays even with seeding in place, because mastery decays: scheduler.py
# multiplies untouched nodes by 0.95 nightly, so a seeded 0.30 erodes back under 0.05
# in about six idle weeks. Seeding sets the starting point; the floor is what makes
# the collapse unreachable.
from decimal import Decimal

# Level assigned when a user has no leaf skill_nodes at all (AVG returns NULL) —
# the genuinely-unknown player, placed mid-catalog.
UNKNOWN_PLAYER_LEVEL = Decimal("0.5")

# Hard lower bound on player_level.
#
# 0.20 is the catalog's beginner anchor (0007 tagging contract: 0.20 beginner /
# 0.50 intermediate / 0.80 advanced), so flooring here means the bottom of the
# range lands on a real tier rather than an arithmetic edge. At 0.20 the +/-0.15
# window holds 13 of the 64 catalog rows; at 0.00 it held 1.
#
# Deliberately NOT fixed by adding catalog entries below 0.15: Knockin' on Heaven's
# Door is four open chords, there is no honest tier of real songs beneath it, and
# inventing filler to satisfy an arithmetic window would be tagging the catalog to
# fit a bug (FLE-49, "Explicitly not recommended").
PLAYER_LEVEL_FLOOR = Decimal("0.20")

# SQL fragment computing player_level over a set of leaf skill_nodes rows.
#
# Interpolated (not bound) because today_song.py needs it inside a single-statement
# CTE where setseed() and random() must share one Postgres session, and because both
# operands are module constants — there is no user input on this path.
PLAYER_LEVEL_SQL = (
    f"GREATEST(COALESCE(AVG(mastery), {UNKNOWN_PLAYER_LEVEL}), {PLAYER_LEVEL_FLOOR})"
)


def floor_player_level(value: object | None) -> Decimal:
    """Apply the same floor/fallback to a player_level computed in Python.

    Mirrors PLAYER_LEVEL_SQL for callers that already have the AVG in hand (e.g.
    breakdowns.py, which composes the average through the SQLAlchemy Core API rather
    than raw SQL). None means "no leaf nodes" -> UNKNOWN_PLAYER_LEVEL.
    """
    if value is None:
        return UNKNOWN_PLAYER_LEVEL
    level = value if isinstance(value, Decimal) else Decimal(str(value))
    return max(level, PLAYER_LEVEL_FLOOR)
