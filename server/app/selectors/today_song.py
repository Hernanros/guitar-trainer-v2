# server/app/selectors/today_song.py
# Deterministic per-day song selector.
#
# Design decisions (03-CONTEXT.md D-01 through D-04, 03-RESEARCH.md §4):
#
# D-01: 75/25 mixture — 75% argmin(mastery) from working_on; 25% bank-random.
# D-02: Bank = user's own songs (all categories) UNION song_catalog seed.
# D-03: Bank filtered by player_level ±0.15 (player_level = AVG(mastery) over leaf nodes).
# D-04: Empty working_on = 100% bank-random (same code path, no extra branch).
# D-05: One reroll per day — enforced by partial-unique index on user_sessions.
#
# The CTE is one SQL statement so setseed + random() share the same Postgres session.
# Splitting them into two statements would break determinism across async pool connections.
# (RESEARCH §8 landmine 2 — keep setseed in the same statement as random().)
#
# Landmine 3 (RESEARCH §8): seed key uses '|' delimiter to prevent concatenation-ambiguity
# collisions between user_ids and local_calendar_days.
#
# Landmine 8 (RESEARCH §8): tz_offset_minutes default is 0 (UTC), handled by
# get_tz_offset_minutes dep in deps.py.
#
# bank_source discriminator: the bank CTE is split into user_bench and catalog sub-CTEs
# so we can tell which branch produced the winning row (Revision B).
#
# _ensure_catalog_song_as_user_song: when the bank picks a song_catalog row, we must
# UPSERT it into songs (category='aspirational') because TodaySongResponse.song is always
# a songs row. Uses songs_user_title_artist_uidx (Revision F, added in migration 0003)
# for safe ON CONFLICT DO NOTHING under concurrent-reroll races.
import logging
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

BankSource = Literal["user_bench", "seed_catalog"]

# ---------------------------------------------------------------------------
# The 75/25 deterministic CTE (RESEARCH §4 verbatim + refinements)
# ---------------------------------------------------------------------------
#
# Bind parameters:
#   :user_id          — UUID of the requesting user (str cast in Postgres)
#   :local_calendar_day — DATE string for today (server-computed from tz offset)
#   :player_level     — NUMERIC(4,3) — mean mastery over leaf skill_nodes (fallback 0.5)
#   :reroll_suffix    — '' for normal fetch; ':reroll' for reroll call (landmine 3 delimiter)
#
# Returns one row: (today_song_id, from_bank, bank_source)
# today_song_id is NULL if BOTH working_on AND bank are empty.

_SELECTOR_CTE = text(
    """
WITH
  -- 1. Seed the RNG deterministically per (user, day, optional reroll).
  --    hashtext() returns int4; setseed expects -1..1 double.
  --    '|' delimiter prevents (user_id='AB' | day='CD') == (user_id='A' | day='BCD').
  --    Note: CAST() used instead of ::type because asyncpg cannot parse :param::type syntax
  --    (named-param colon is ambiguous with Postgres cast double-colon at the lexer level).
  seed AS (
    SELECT setseed(
      hashtext(CAST(:user_id AS text) || '|' || CAST(:local_calendar_day AS text) || CAST(:reroll_suffix AS text))
      / 2147483647.0
    )
  ),
  -- 2. Player level: AVG(mastery) over leaf skill_nodes. Fallback 0.5 for new users.
  player_level_cte AS (
    SELECT COALESCE(AVG(mastery), 0.5)::numeric(4,3) AS player_level
    FROM skill_nodes
    WHERE user_id = CAST(:user_id AS uuid)
      AND level = 'leaf'
  ),
  -- 3. Deterministic pick: argmin(mastery) over working_on songs for this user.
  --    song_id cast to text for CASE expression compatibility with bank_pick (also text).
  working_on_pick AS (
    SELECT s.id::text AS song_id
    FROM songs s
    JOIN song_skills ss ON ss.song_id = s.id
    JOIN skill_nodes sn ON sn.id = ss.skill_node_id
    WHERE s.user_id = CAST(:user_id AS uuid)
      AND s.category = 'working_on'
    ORDER BY sn.mastery ASC, sn.updated_at ASC
    LIMIT 1
  ),
  -- 4. Bank branch A: user's own songs (all three categories), drawn randomly.
  --    song_id cast to text so the UNION with catalog_pick (uuid) is type-compatible.
  --    The Python caller detects uuid-shaped strings to identify catalog IDs.
  user_bench_pick AS (
    SELECT s.id::text AS song_id, 'user_bench'::text AS src
    FROM songs s
    WHERE s.user_id = CAST(:user_id AS uuid)
    ORDER BY random()
    LIMIT 1
  ),
  -- 5. Bank branch B: seed catalog filtered by player_level ±0.15.
  --    song_id cast to text for UNION compatibility with user_bench_pick.
  catalog_pick AS (
    SELECT sc.id::text AS song_id, 'seed_catalog'::text AS src
    FROM song_catalog sc, player_level_cte pl
    WHERE ABS(sc.difficulty - pl.player_level) <= 0.15
    ORDER BY random()
    LIMIT 1
  ),
  -- 6. Unified bank pick: prefer user_bench; fall through to catalog.
  bank_pick AS (
    SELECT song_id, src
    FROM user_bench_pick
    UNION ALL
    SELECT song_id, src
    FROM catalog_pick
    WHERE NOT EXISTS (SELECT 1 FROM user_bench_pick)
    LIMIT 1
  ),
  -- 7. Coin flip for 75/25 (called after seed is set; same session).
  coin AS (
    SELECT random() AS flip
  )
-- 8. Final selection.
SELECT
  CASE
    WHEN (SELECT flip FROM coin) < 0.25 THEN (SELECT song_id FROM bank_pick)
    WHEN NOT EXISTS (SELECT 1 FROM working_on_pick) THEN (SELECT song_id FROM bank_pick)
    ELSE (SELECT song_id FROM working_on_pick)
  END AS today_song_id,
  (
    (SELECT flip FROM coin) < 0.25
    OR NOT EXISTS (SELECT 1 FROM working_on_pick)
  ) AS from_bank,
  CASE
    WHEN (
      (SELECT flip FROM coin) < 0.25
      OR NOT EXISTS (SELECT 1 FROM working_on_pick)
    ) THEN (SELECT src FROM bank_pick)
    ELSE NULL
  END AS bank_source
FROM seed  -- trigger the setseed side effect
"""
)


async def _ensure_catalog_song_as_user_song(
    db: AsyncSession,
    user_id: UUID,
    catalog_song_id: UUID,
) -> int:
    """UPSERT a song_catalog entry into the user's songs table (category='aspirational').

    Uses ON CONFLICT on songs_user_title_artist_uidx (Revision F: added in migration 0003)
    to make concurrent-reroll races safe. Returns the songs.id integer.

    The upsert is necessary because TodaySongResponse.song is always a songs row
    (songs.id is an integer PK; song_catalog.id is a UUID). Serving a catalog song
    directly would break the response type contract.
    """
    # Look up the catalog row first (we need title/artist/genre/difficulty).
    cat_row = await db.execute(
        text(
            "SELECT title, artist, genre, primary_skill_root, difficulty "
            "FROM song_catalog WHERE id = :id"
        ),
        {"id": str(catalog_song_id)},
    )
    cat = cat_row.mappings().one_or_none()
    if cat is None:
        raise RuntimeError(f"song_catalog row {catalog_song_id} not found — migration data missing?")

    # Check if this song already exists for the user (by title + artist, case-insensitive).
    existing = await db.execute(
        text(
            "SELECT id FROM songs "
            "WHERE user_id = :user_id "
            "  AND lower(title) = lower(:title) "
            "  AND lower(artist) = lower(:artist)"
        ),
        {"user_id": str(user_id), "title": cat["title"], "artist": cat["artist"]},
    )
    existing_row = existing.mappings().one_or_none()
    if existing_row:
        return int(existing_row["id"])

    # Insert the catalog song into the user's songs table as aspirational.
    # ON CONFLICT DO NOTHING backs the unique index; RETURNING gives us the new id.
    insert_result = await db.execute(
        text(
            """
            INSERT INTO songs
              (title, artist, genre, difficulty, breakdown, user_id, category)
            VALUES
              (:title, :artist, :genre, :difficulty, CAST(:breakdown AS jsonb), CAST(:user_id AS uuid), 'aspirational')
            ON CONFLICT (user_id, lower(title), lower(artist)) DO NOTHING
            RETURNING id
            """
        ),
        {
            "title": cat["title"],
            "artist": cat["artist"],
            "genre": cat["genre"],
            "difficulty": cat["difficulty"],
            "breakdown": '{"tab":{"measures":[],"tuning":["E","A","D","G","B","e"]},"chords":[],"technique_notes":[]}',
            "user_id": str(user_id),
        },
    )
    await db.commit()

    # If ON CONFLICT DO NOTHING fired, we need to re-query.
    inserted = insert_result.mappings().one_or_none()
    if inserted:
        return int(inserted["id"])

    # Concurrent insert raced us — fetch the existing row.
    retry = await db.execute(
        text(
            "SELECT id FROM songs "
            "WHERE user_id = :user_id "
            "  AND lower(title) = lower(:title) "
            "  AND lower(artist) = lower(:artist)"
        ),
        {"user_id": str(user_id), "title": cat["title"], "artist": cat["artist"]},
    )
    retry_row = retry.mappings().one_or_none()
    if retry_row is None:
        raise RuntimeError(
            f"Failed to upsert catalog song '{cat['title']}' by '{cat['artist']}' for user {user_id}"
        )
    return int(retry_row["id"])


async def select_today_song(
    db: AsyncSession,
    user_id: UUID,
    tz_offset_minutes: int,
    *,
    force_reroll: bool = False,
) -> tuple[int | None, bool, BankSource | None]:
    """Execute the 75/25 CTE and return the selected song.

    Returns:
        (song_id, from_bank, bank_source)

        song_id: integer id from the songs table (None if bank is totally empty).
        from_bank: True when the bank branch fired (25% coin, empty working_on, or reroll).
        bank_source: 'user_bench' | 'seed_catalog' | None (None when from_bank=False).

    When force_reroll=True, a different seed suffix is used so the reroll produces
    a different sequence of random() calls (RESEARCH §4 landmine 3 anti-collision).

    When the selected song comes from song_catalog, _ensure_catalog_song_as_user_song
    UPSERTs it into songs (aspirational category) and returns the resulting integer songs.id.
    """
    reroll_suffix = ":reroll" if force_reroll else ""

    # Compute local calendar day from the tz offset (server-side; never trust client clock).
    today: date = await db.scalar(
        text(
            "SELECT DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute'))"
        ),
        {"tz": tz_offset_minutes},
    )

    result = await db.execute(
        _SELECTOR_CTE,
        {
            "user_id": str(user_id),
            "local_calendar_day": str(today),
            "player_level": 0.5,  # will be overridden by inline AVG in CTE
            "reroll_suffix": reroll_suffix,
        },
    )
    row = result.mappings().one()

    raw_song_id = row["today_song_id"]
    from_bank: bool = bool(row["from_bank"])
    bank_source_raw: str | None = row["bank_source"]

    if raw_song_id is None:
        return None, from_bank, None

    # Determine if the song_id is a UUID (song_catalog) or int (songs).
    # The CTE returns a text column; if it looks like a UUID, it's from the catalog.
    song_id_str = str(raw_song_id)
    is_catalog_id = len(song_id_str) == 36 and "-" in song_id_str

    if is_catalog_id:
        # UPSERT catalog song into songs table and return the integer songs.id.
        import uuid as _uuid
        catalog_uuid = _uuid.UUID(song_id_str)
        songs_int_id = await _ensure_catalog_song_as_user_song(db, user_id, catalog_uuid)
        bank_source: BankSource | None = "seed_catalog"
        return songs_int_id, True, bank_source

    # Integer songs.id — either working_on pick or user_bench pick.
    songs_int_id = int(raw_song_id)

    # Determine actual bank_source from what the CTE reported.
    if bank_source_raw == "seed_catalog":
        bank_source = "seed_catalog"
    elif bank_source_raw == "user_bench":
        bank_source = "user_bench"
    else:
        bank_source = None

    return songs_int_id, from_bank, bank_source
