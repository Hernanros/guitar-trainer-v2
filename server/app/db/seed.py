# server/app/db/seed.py
# Hardcoded seed row for the songs table.
# This is idempotent — it only inserts if the table is empty.
# D-01: Full Phase 3-ready payload shape with realistic content.
# Song: "Sweet Home Chicago" (Robert Johnson) — 12-bar blues in E, intermediate.
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

HARDCODED_SONG = {
    "title": "Sweet Home Chicago",
    "artist": "Robert Johnson",
    "genre": "blues",
    "difficulty": "intermediate",
    "bpm": 112,
    "key": "E",
    "breakdown": {
        "tab": {
            "tuning": ["E", "A", "D", "G", "B", "e"],
            "measures": [
                {
                    "time_signature": "4/4",
                    "beats": [
                        {
                            "notes": [
                                {"string": 6, "fret": 0, "duration": "quarter"}
                            ]
                        },
                        {
                            "notes": [
                                {"string": 5, "fret": 2, "duration": "eighth"},
                                {"string": 4, "fret": 2, "duration": "eighth"},
                            ]
                        },
                        {
                            "notes": [
                                {"string": 6, "fret": 0, "duration": "quarter"}
                            ]
                        },
                        {
                            "notes": [
                                {"string": 5, "fret": 2, "duration": "quarter"}
                            ]
                        },
                    ],
                },
                {
                    "time_signature": "4/4",
                    "beats": [
                        {
                            "notes": [
                                {"string": 6, "fret": 0, "duration": "eighth"},
                                {"string": 5, "fret": 2, "duration": "eighth"},
                            ]
                        },
                        {
                            "notes": [
                                {"string": 4, "fret": 2, "duration": "quarter"}
                            ]
                        },
                        {
                            "notes": [
                                {"string": 3, "fret": 1, "duration": "eighth"},
                                {"string": 4, "fret": 2, "duration": "eighth"},
                            ]
                        },
                        {
                            "notes": [
                                {"string": 6, "fret": 0, "duration": "quarter"}
                            ]
                        },
                    ],
                },
            ],
        },
        "chords": [
            {
                "name": "E7",
                "base_fret": 1,
                "barre_fret": None,
                "positions": [
                    {"string": 6, "fret": 0, "finger": None},
                    {"string": 5, "fret": 2, "finger": 2},
                    {"string": 4, "fret": 0, "finger": None},
                    {"string": 3, "fret": 1, "finger": 1},
                    {"string": 2, "fret": 0, "finger": None},
                    {"string": 1, "fret": 0, "finger": None},
                ],
            },
            {
                "name": "A7",
                "base_fret": 1,
                "barre_fret": None,
                "positions": [
                    {"string": 6, "fret": -1, "finger": None},
                    {"string": 5, "fret": 0, "finger": None},
                    {"string": 4, "fret": 2, "finger": 2},
                    {"string": 3, "fret": 0, "finger": None},
                    {"string": 2, "fret": 2, "finger": 3},
                    {"string": 1, "fret": 0, "finger": None},
                ],
            },
        ],
        "technique_notes": [
            {
                "heading": "Shuffle Feel",
                "body": (
                    "This is a 12-bar blues in E with a shuffle rhythm. "
                    "Play the bass note on beats 1 and 3, and the chord on 2 and 4. "
                    "Swing the eighth notes — long-short, long-short."
                ),
            },
            {
                "heading": "Fretting Hand",
                "body": (
                    "Keep your thumb behind the neck on E7. For A7, let string 6 mute naturally "
                    "— your thumb can wrap over if comfortable. Focus on clean chord transitions "
                    "at the shuffle tempo before increasing speed."
                ),
            },
        ],
    },
}


import uuid as _uuid_module

# System user sentinel UUID (matches migration 0002 seed — per <specifics> in 02-CONTEXT.md)
SYSTEM_USER_ID = _uuid_module.UUID("00000000-0000-0000-0000-000000000000")


async def seed_songs(db: AsyncSession) -> None:
    """Insert the hardcoded seed song if the songs table is empty. Idempotent.

    Phase 2 (02-01): also ensures the system user row exists (idempotent ON CONFLICT)
    and assigns the seed song to the system user with category='can_play'.
    The system user is seeded by migration 0002 on fresh databases; this call
    handles the case where migration ran before seed (e.g., dev restarts).
    """
    # local imports to avoid circular dep at module level
    from app.models.db import Song as SongORM, User as UserORM

    result = await db.execute(text("SELECT COUNT(*) FROM songs"))
    count = result.scalar()
    if count and count > 0:
        logger.info("seed_songs: songs table already has %d row(s), skipping.", count)
        return

    logger.info("seed_songs: ensuring system user exists.")
    # Ensure system user exists (ON CONFLICT DO NOTHING — migration already seeds it
    # on fresh DBs, but dev may start the server without running migration first).
    await db.execute(
        text(
            "INSERT INTO users (id, preferences, onboarded_at, created_at) "
            "VALUES (:id, '{}', now(), now()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": str(SYSTEM_USER_ID)},
    )

    logger.info("seed_songs: inserting hardcoded Sweet Home Chicago row.")
    # asyncpg requires JSONB columns to be passed as dicts (not JSON strings).
    # Using SQLAlchemy ORM insert avoids raw SQL parameter quoting issues.
    song_row = SongORM(
        title=HARDCODED_SONG["title"],
        artist=HARDCODED_SONG["artist"],
        genre=HARDCODED_SONG["genre"],
        difficulty=HARDCODED_SONG["difficulty"],
        bpm=HARDCODED_SONG["bpm"],
        key=HARDCODED_SONG["key"],
        breakdown=HARDCODED_SONG["breakdown"],
        user_id=SYSTEM_USER_ID,
        category="can_play",
    )
    db.add(song_row)
    await db.flush()
    await db.commit()
    logger.info("seed_songs: seed row inserted with user_id=system and category=can_play.")
