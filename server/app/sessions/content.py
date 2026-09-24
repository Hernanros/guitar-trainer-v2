# server/app/sessions/content.py — the embed half of the session payload (FLE-63).
#
# `practice_session_items` stores REFERENCES: drill_id, song_id. That is correct for
# the table — telemetry keys should not carry a copy of the prose they point at, and
# FLE-21's readout joins on those ids. It is wrong for the wire, and the ruling on
# FLE-21 (2026-09-24) settled which way the HTTP payload goes: EMBED.
#
# The reason is not payload aesthetics. FLE-10 §5 requires a session to be runnable
# fully offline once opened, and a network stall between item 3 and item 4 is a stall
# inside someone's practice, in a room with bad wifi, mid-song. A reference payload
# makes every item boundary a potential fetch. So the whole session — every drill's
# prose and tab, every song's breakdown — ships in the one response that opens it.
#
# Two batch queries, never N+1. A session has ~6 items across at most a handful of
# drills and ONE song, so the id sets are tiny; the point of batching is not speed
# but that the number of round trips does not vary with the plan shape.
#
# SHAPE RULE (FLE-4 §12.1, restated on the FLE-21 ruling thread): the embedded drill
# payload carries NO `user_id`. §12.1's hand-authored warm-up drills are global rows
# (`user_id IS NULL`) and a warm-up item can resolve to one. If ownership leaked into
# this payload, the player would need a kind-of-drill branch on top of the
# kind-of-item branch it already has. It does not, and must not start.
from __future__ import annotations

from typing import Any, Iterable, Mapping
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# `song_specific` is included because it is the flag the player uses to decide whether
# the drill's `what` copy may name the song — a global seed drill's may not.
_DRILLS_SQL = text(
    """
    SELECT id, name, what, tab_snippet, start_bpm, target_bpm, repetitions,
           success_criterion, common_trap, song_specific
      FROM drills
     WHERE id = ANY(CAST(:ids AS uuid[]))
    """
)

# `breakdown` is the tab/chords/technique_notes envelope. It is embedded rather than
# left to the client's existing per-song `useBreakdown` cache because that cache is
# only warm if the user opened the Today card first — and a resumed session, or one
# opened straight from a notification, has no such guarantee. Measured at <=10KB.
_SONGS_SQL = text(
    """
    SELECT id, title, artist, genre, difficulty, bpm, key, breakdown
      FROM songs
     WHERE id = ANY(CAST(:ids AS int[]))
    """
)


async def load_item_content(
    db: AsyncSession, items: Iterable[Any]
) -> tuple[Mapping[UUID, Any], Mapping[int, Any]]:
    """Batch-load the drill and song rows the given items reference.

    Returns (drills_by_id, songs_by_id). Missing ids are simply absent from the maps
    rather than raising: a drill row deleted after the plan was written must degrade
    to an item the player can skip past, not a 500 that makes the whole session
    unopenable. `practice_session_items.drill_id` is ON DELETE SET NULL, so this is a
    real state and not a hypothetical one.
    """
    drill_ids = sorted({i.drill_id for i in items if i.drill_id is not None})
    song_ids = sorted({i.song_id for i in items if i.song_id is not None})

    drills: dict[UUID, Any] = {}
    songs: dict[int, Any] = {}

    if drill_ids:
        rows = (
            await db.execute(_DRILLS_SQL, {"ids": [str(d) for d in drill_ids]})
        ).all()
        drills = {r.id: r for r in rows}
    if song_ids:
        rows = (await db.execute(_SONGS_SQL, {"ids": song_ids})).all()
        songs = {r.id: r for r in rows}

    return drills, songs
