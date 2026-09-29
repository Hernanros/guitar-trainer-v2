# server/app/sessions/mastery.py
# D-08's mastery shift — the one place in the codebase where a rating moves
# `skill_nodes.mastery`.
#
# Extracted from app/api/v1/sessions.py by FLE-65 rather than copied. `deficit` is
# ~100 of the ~180 points in FLE-4 §11.2's selection score and it reads this column,
# so two hand-rolled copies of the clamp — one per rating surface — is how the Today
# card and the session player would come to disagree about what "+0.15" means. There
# is one shift table and one clamp expression; the surfaces differ only in WHICH rows
# they hand it.
#
# SKILL-03: deterministic writes, no LLM in this path.
import logging
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import Numeric, cast, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import SkillNode, SongSkill

logger = logging.getLogger(__name__)

# D-08: fixed additive mastery shifts per rating tier. The pilot's tuning knob —
# FLE-4 §1 is explicit that these constants are the ones to argue with afterwards,
# which is the whole reason they live in a dict and not inline in two routers.
RATING_SHIFTS: dict[str, Decimal] = {
    "not_my_tempo": Decimal("-0.05"),
    "getting_closer": Decimal("0.05"),
    "thats_what_im_looking_for": Decimal("0.15"),
}


def _clamped(shift: Decimal):
    """`LEAST(1.0, GREATEST(0.0, mastery + shift))` as a SQLAlchemy expression.

    The bounds are typed literals rather than `::numeric` text: asyncpg rejects
    Postgres cast syntax inside a parameterised query (03-RESEARCH §8 landmine 11),
    so `func.least`/`func.greatest`/`cast(literal(...))` is the pattern that actually
    executes. Clamping SQL-side and not in Python keeps it correct under a concurrent
    update — the read and the write are one statement.
    """
    return func.least(
        cast(literal(Decimal("1.0")), Numeric(4, 3)),
        func.greatest(
            cast(literal(Decimal("0.0")), Numeric(4, 3)),
            SkillNode.mastery + cast(literal(shift), Numeric(4, 3)),
        ),
    )


def shift_for(rating: Optional[str]) -> Optional[Decimal]:
    """The shift a rating earns, or None if the rating is not one D-08 knows.

    Unknown ratings move nothing, deliberately mirroring `ladder.classify` ("a verdict
    the ladder does not understand must never move it"). A `rating_level` value added
    to the enum without a shift being agreed must leave mastery alone rather than
    raise — the rating is already committed to the item row by the time we get here,
    and a KeyError would roll it back.
    """
    if rating is None:
        return None
    return RATING_SHIFTS.get(rating)


async def shift_node(
    db: AsyncSession,
    *,
    user_id: UUID,
    node_id: UUID,
    shift: Decimal,
) -> bool:
    """Move exactly ONE `skill_nodes` row. Returns whether a row actually moved.

    `SkillNode.user_id == user_id` is in the WHERE clause, not checked beforehand
    (T-04.1-05): a node id that belongs to another participant matches zero rows and
    mutates nothing, with no TOCTOU window between the check and the update. The
    caller is told `False` rather than raising, because on the player's path the id
    came from a server-written item row — a mismatch is corrupt data, and the user's
    rating and ladder move are already in this transaction.

    `updated_at` is set explicitly: a bulk UPDATE through `execute()` does not fire
    SQLAlchemy's `onupdate` hook (03-RESEARCH §9 Q5).
    """
    result = await db.execute(
        update(SkillNode)
        .where(SkillNode.id == node_id, SkillNode.user_id == user_id)
        .values(mastery=_clamped(shift), updated_at=func.now())
    )
    return bool(result.rowcount)


async def shift_song_leaves(
    db: AsyncSession,
    *,
    user_id: UUID,
    song_id: int,
    shift: Decimal,
) -> int:
    """Move every leaf in `song_skills` for this song, equally. Returns the rowcount.

    D-07 equal-weight: `song_skills.weight` exists in the schema and is NOT read here.
    The `SkillNode.user_id` filter is defence in depth — a crafted `song_id` cannot
    reach another user's nodes even though the caller has already proved song ownership.
    """
    return (
        await db.execute(
            update(SkillNode)
            .where(
                SkillNode.id.in_(
                    select(SongSkill.skill_node_id).where(SongSkill.song_id == song_id)
                ),
                SkillNode.user_id == user_id,
            )
            .values(mastery=_clamped(shift), updated_at=func.now())
        )
    ).rowcount
