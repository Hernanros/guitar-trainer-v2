# server/app/sessions/snapshot.py — the DB read that feeds the generator (FLE-9).
#
# `build_plan` is a pure function of a `GeneratorInputs`. This module is the ONLY
# place that turns a database into one. Everything else in app/sessions/ can be
# tested against literals precisely because the I/O is quarantined here.
#
# Three rules this module holds, all of them load-bearing:
#
#   1. READ ONLY. Not one statement here writes. The generator must be safe to run
#      speculatively — a preview, a debug replay of yesterday, a test — and that is
#      only true if loading a snapshot has no side effects. The writer is a separate
#      concern (app/sessions/store.py).
#
#   2. NO SONG SELECTION. §5.3 says the session inherits the day's song and never
#      re-rolls. `song_id` is therefore a PARAMETER, decided upstream by
#      app/selectors/today_song.py, and this module only enriches it (bpm, readiness,
#      section node, skill node ids). Calling the selector from here would put a
#      coin-flip inside a function whose entire contract is reproducibility.
#
#   3. ABSENCE IS NOT ZERO. The three places this matters, because each one has a
#      plausible-looking wrong answer that changes the generated session:
#        - `days_since_last=None` (no prior session) is NOT a 0-day layoff. A 0 would
#          read as "practised yesterday" and suppress the re-entry path for a
#          first-ever session.
#        - a root with no leaf nodes is ABSENT from `root_mastery`, not 0.0. §4 rule 5
#          fires on the weakest root; an untouched root is unknown, not weak, and a
#          0.0 there would hijack every session for a skill the user never mentioned.
#        - `completion_3=None` (fewer than one terminal session) is NOT 0.0. Zero
#          completion is a user who bails; no history is a user who hasn't started.
#
# Day boundary: computed by Postgres from tz_offset_minutes (D-10), never from the
# server's clock or the client's. Same expression as today_song.py, so the session
# and the song it inherits can never land on different days.
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.selectors.player_level import floor_player_level
from app.sessions.assemble import ConsolidationSong, GeneratorInputs, SongMaterial
from app.sessions.select import DrillCandidate
from app.sessions.taxonomy import SkillRoot, TechniqueFamily, Tier

# §12.1's hand-authored warm-up drills are GLOBAL rows (`user_id IS NULL`) and
# `drills.user_id` is NOT NULL today, so no query can return them. The seam stays
# wired and empty rather than absent: when the rows ship, this is the one line that
# changes, and select_warmup's rule (d) starts firing without touching selection.
SEED_DRILLS: Sequence[DrillCandidate] = ()

# D-12. The value onboarding captured and — until this module — nothing read.
VALID_TARGET_MINUTES = (15, 30, 45, 60)
DEFAULT_TARGET_MINUTES = 30

# §4's mode inputs look at the recent past; §5.2's recency constraint looks at the
# last two. Both windows are counted in TERMINAL sessions, not calendar days, so a
# week off does not erase the history that shapes the return session.
COMPLETION_WINDOW = 3
RECENCY_WINDOW = 2

TERMINAL_STATES = ("completed", "abandoned")


class SnapshotError(Exception):
    """The user cannot be loaded at all — no such user row.

    Distinct from `NoMaterialError`, which means the user exists and has nothing to
    practise. That one is a legitimate product state with a real screen behind it;
    this one is a 404.
    """


def _root_key(name: str) -> Optional[str]:
    """'Chord Voicings' -> 'chord_voicings'. Unknown names return None.

    Onboarding pins the six root names verbatim (app.ai.onboarding.FIXED_ROOTS), so
    this is a total function in practice. It returns None rather than raising anyway:
    a stray root row from a hand-edited database should cost that root's contribution
    to §4 rule 5, not the user's whole session.
    """
    key = name.strip().lower().replace(" ", "_")
    return key if key in {r.value for r in SkillRoot} else None


def _target_minutes(preferences: Optional[Mapping[str, Any]]) -> int:
    """The onboarding preference, defended.

    A malformed value falls back to 30 rather than raising. The session-length
    preference is a comfort setting; a corrupt one should not be the reason a user
    gets no session at all.
    """
    if not preferences:
        return DEFAULT_TARGET_MINUTES
    raw = preferences.get("session_length_min")
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_TARGET_MINUTES
    return value if value in VALID_TARGET_MINUTES else DEFAULT_TARGET_MINUTES


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


async def local_day(db: AsyncSession, tz_offset_minutes: int) -> date:
    """The user's calendar day, computed by the DB (D-10).

    Byte-identical expression to today_song.py's. If these two ever diverge, a
    session generated at 23:59 could inherit a song chosen for the following day,
    and the resulting row would violate the open-session unique for a day the user
    never saw.
    """
    return await db.scalar(
        text("SELECT DATE((now() AT TIME ZONE 'UTC') + (:tz * INTERVAL '1 minute'))"),
        {"tz": tz_offset_minutes},
    )


# ---------------------------------------------------------------------------
# The queries
# ---------------------------------------------------------------------------

# Mode inputs (§4) over the user's TERMINAL sessions. One statement rather than
# three: sessions_count, days_since_last and completion_3 are all windows over the
# same ordered set, and splitting them risks the three reads landing either side of
# a concurrent write and describing different histories.
_HISTORY_SQL = text(
    """
    WITH terminal AS (
        SELECT local_calendar_day, completion_ratio
          FROM practice_sessions
         WHERE user_id = :user_id
           AND state = ANY(:terminal_states)
         ORDER BY local_calendar_day DESC, generated_at DESC
    ),
    recent AS (
        SELECT completion_ratio FROM terminal LIMIT :completion_window
    )
    SELECT
        (SELECT count(*) FROM terminal)                       AS sessions_count,
        (SELECT max(local_calendar_day) FROM terminal)        AS last_day,
        (SELECT avg(completion_ratio) FROM recent
          WHERE completion_ratio IS NOT NULL)                 AS completion_3
    """
)

# §4's root_mastery: root value -> mean mastery over its LEAF descendants.
#
# The join is leaf -> sub -> root, two hops, because mastery lives only on leaves and
# §4 asks about roots. Roots with no leaves produce no row and are therefore absent
# from the mapping, which is rule 3 of this module's header: an untouched root is
# unknown, not weak.
_ROOT_MASTERY_SQL = text(
    """
    SELECT root.name AS root_name, avg(leaf.mastery) AS mastery
      FROM skill_nodes leaf
      JOIN skill_nodes sub  ON sub.id  = leaf.parent_id
      JOIN skill_nodes root ON root.id = sub.parent_id
     WHERE leaf.user_id = :user_id
       AND leaf.level = 'leaf'
       AND root.level = 'root'
     GROUP BY root.name
    """
)

_PLAYER_LEVEL_SQL = text(
    """
    SELECT avg(mastery) FROM skill_nodes
     WHERE user_id = :user_id AND level = 'leaf'
    """
)

# The bank. One row per canonical drill, carrying this user's progress on it and the
# mastery + root of the skill it targets.
#
# LEFT JOIN on drill_progress is the important half: a drill the user has never
# touched MUST appear as a candidate with no history, not vanish. New drills are
# exactly what a thin bank is made of, and an INNER JOIN here would make week 1
# return nothing.
#
# lifetime_clears is a correlated subquery rather than another join because only
# §5.4's consolidation fallback reads it; joining drill_attempts into the main query
# would fan out every candidate row for a column no selection rule scores on.
_CANDIDATES_SQL = text(
    """
    SELECT
        d.id::text                AS drill_id,
        d.skill_node_id::text     AS skill_node_id,
        d.tab_snippet             AS tab_snippet,
        d.start_bpm               AS start_bpm,
        d.target_bpm              AS target_bpm,
        d.repetitions             AS repetitions,
        d.song_specific           AS song_specific,
        d.family::text            AS family,
        d.tier::text              AS tier,
        leaf.mastery              AS node_mastery,
        root.name                 AS root_name,
        p.state::text             AS progress_state,
        p.rung_bpm                AS rung_bpm,
        p.consecutive_clears      AS consecutive_clears,
        p.attempts                AS attempts,
        p.last_practiced_on       AS last_practiced_on,
        COALESCE((
            SELECT count(*) FROM drill_attempts a
             WHERE a.drill_id = d.id AND a.user_id = d.user_id AND a.outcome = 'CLEAR'
        ), 0)                     AS lifetime_clears
      FROM drills d
      JOIN skill_nodes leaf ON leaf.id = d.skill_node_id
      LEFT JOIN skill_nodes sub  ON sub.id  = leaf.parent_id
      LEFT JOIN skill_nodes root ON root.id = sub.parent_id AND root.level = 'root'
      LEFT JOIN drill_progress p ON p.drill_id = d.id AND p.user_id = d.user_id
     WHERE d.user_id = :user_id
       AND d.status = 'active'
     ORDER BY d.id
    """
)

# §5.3 — the song's skills, its readiness, and the section to work.
#
# readiness is the MEAN mastery over the song's leaf skills; section_skill_node_id is
# the LOWEST-mastery one. A song with no song_skills rows returns no rows at all,
# which the caller reads as readiness=None (header note (c) of assemble.py: that
# means tempo_factor 0.70, not 1.00 — an unknown song is approached slowly).
_SONG_SKILLS_SQL = text(
    """
    SELECT n.id::text AS skill_node_id, n.mastery AS mastery
      FROM song_skills ss
      JOIN skill_nodes n ON n.id = ss.skill_node_id
     WHERE ss.song_id = :song_id
       AND n.user_id = :user_id
     ORDER BY n.mastery ASC, n.id ASC
    """
)

_SONG_SQL = text("SELECT id, bpm FROM songs WHERE id = :song_id AND user_id = :user_id")

# §5.4 — end on a win. `can_play` songs, most recently PLAYED first.
#
# "Recently played" is the last rating in user_sessions, not songs.created_at: the
# songs table has no updated_at, and when-it-was-added says nothing about whether
# the user still owns it. A song rated last week is a surer win than one added six
# months ago and never touched. Never-rated songs sort last (NULLS LAST) rather than
# being excluded — a can_play song the user declared at onboarding is still a win.
_CAN_PLAY_SQL = text(
    """
    SELECT s.id, s.bpm
      FROM songs s
      LEFT JOIN (
          SELECT song_id, max(local_calendar_day) AS last_played
            FROM user_sessions
           WHERE user_id = :user_id AND song_id IS NOT NULL
           GROUP BY song_id
      ) r ON r.song_id = s.id
     WHERE s.user_id = :user_id AND s.category = 'can_play' AND s.bpm IS NOT NULL
     ORDER BY r.last_played DESC NULLS LAST, s.id ASC
     LIMIT 20
    """
)

# §5.2 — drills practised in the last two TERMINAL sessions.
#
# "Practised" is item state completed or in_progress, NOT merely planned: a drill the
# user never reached is not a drill they just did, and excluding it would suppress
# material for two sessions over an item that never appeared on screen.
_RECENT_DRILLS_SQL = text(
    """
    WITH last_sessions AS (
        SELECT id FROM practice_sessions
         WHERE user_id = :user_id
           AND state = ANY(:terminal_states)
         ORDER BY local_calendar_day DESC, generated_at DESC
         LIMIT :recency_window
    )
    SELECT DISTINCT i.drill_id::text AS drill_id
      FROM practice_session_items i
      JOIN last_sessions s ON s.id = i.session_id
     WHERE i.drill_id IS NOT NULL
       AND i.state IN ('completed', 'in_progress')
    """
)


async def _load_song(
    db: AsyncSession, user_id: UUID, song_id: int
) -> Optional[SongMaterial]:
    """Enrich an already-decided song_id. Returns None if the song is not the user's.

    Not an error: a song can be deleted between the selector's choice and this read,
    and the honest response to that is a session with no repertoire block (§12's
    thin-bank ladder handles it) rather than a 500.
    """
    row = (await db.execute(_SONG_SQL, {"song_id": song_id, "user_id": user_id})).first()
    if row is None or row.bpm is None:
        return None

    skills = (
        await db.execute(_SONG_SKILLS_SQL, {"song_id": song_id, "user_id": user_id})
    ).all()

    readiness: Optional[float] = None
    section_node: Optional[str] = None
    if skills:
        masteries = [_as_float(s.mastery) or 0.0 for s in skills]
        readiness = sum(masteries) / len(masteries)
        # Ordered mastery ASC, id ASC — the first row IS the lowest-mastery node, and
        # the id tie-break makes that choice stable across two runs on the same day.
        section_node = skills[0].skill_node_id

    return SongMaterial(
        song_id=int(row.id),
        bpm=int(row.bpm),
        readiness=readiness,
        section_skill_node_id=section_node,
        skill_node_ids=frozenset(s.skill_node_id for s in skills),
    )


async def _load_candidates(db: AsyncSession, user_id: UUID) -> list[DrillCandidate]:
    rows = (await db.execute(_CANDIDATES_SQL, {"user_id": user_id})).all()
    candidates: list[DrillCandidate] = []
    for r in rows:
        root_value = _root_key(r.root_name) if r.root_name else None
        candidates.append(
            DrillCandidate(
                drill_id=r.drill_id,
                skill_node_id=r.skill_node_id,
                tab_snippet=r.tab_snippet or {},
                start_bpm=int(r.start_bpm),
                target_bpm=int(r.target_bpm),
                repetitions=int(r.repetitions),
                song_specific=bool(r.song_specific),
                is_canonical=True,  # the WHERE clause already guarantees it
                # §8/§9 from the FLE-13 columns. Both stay Optional: the columns are
                # nullable and NULL means the backfill has not reached this row (or
                # declined to classify it). family_key degrades family -> root -> node
                # on its own and _tier_of() recomputes an absent tier, so a NULL here
                # costs precision, never correctness.
                family=TechniqueFamily(r.family) if r.family else None,
                tier=Tier(r.tier) if r.tier else None,
                root=SkillRoot(root_value) if root_value else None,
                node_mastery=_as_float(r.node_mastery) or 0.0,
                # A drill with no drill_progress row is 'active' at its start_bpm with
                # no history — which is exactly the dataclass's own defaults, restated
                # here because the LEFT JOIN hands us NULLs rather than absences.
                progress_state=r.progress_state or "active",
                rung_bpm=int(r.rung_bpm) if r.rung_bpm is not None else None,
                consecutive_clears=int(r.consecutive_clears or 0),
                attempts=int(r.attempts or 0),
                last_practiced_on=r.last_practiced_on,
                lifetime_clears=int(r.lifetime_clears or 0),
            )
        )
    return candidates


async def load_snapshot(
    db: AsyncSession,
    user_id: UUID,
    *,
    tz_offset_minutes: int,
    song_id: Optional[int] = None,
    local_calendar_day: Optional[date] = None,
    target_minutes: Optional[int] = None,
) -> GeneratorInputs:
    """One frozen read of everything `build_plan` depends on.

    Args:
        song_id: today's song, ALREADY CHOSEN by app/selectors/today_song.py. None is
            a legitimate input (empty bank) and produces a session with no repertoire
            block via §12's ladder.
        local_calendar_day: pass the day the caller already computed, so the session
            and the song-of-day read cannot straddle a midnight boundary. Computed
            here when omitted.
        target_minutes: override for the onboarding preference. Exists for the
            "practise for 15 minutes today" case the UI will eventually offer; when
            None the stored preference wins, which is the whole point of Task 5.

    Raises:
        SnapshotError: no such user.
    """
    prefs_row = (
        await db.execute(
            text("SELECT preferences FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
    ).first()
    if prefs_row is None:
        raise SnapshotError(f"no user {user_id}")

    day = local_calendar_day or await local_day(db, tz_offset_minutes)

    resolved_minutes = (
        target_minutes
        if target_minutes in VALID_TARGET_MINUTES
        else _target_minutes(prefs_row.preferences)
    )

    history = (
        await db.execute(
            _HISTORY_SQL,
            {
                "user_id": user_id,
                "terminal_states": list(TERMINAL_STATES),
                "completion_window": COMPLETION_WINDOW,
            },
        )
    ).one()

    # None, not 0 — see rule 3. `days_since_last` is in the user's local calendar
    # days, which is why both operands are dates and neither is a timestamp: a
    # session at 23:00 and one at 01:00 the next morning are one day apart, and
    # subtracting timestamps would call that two hours.
    days_since_last = (day - history.last_day).days if history.last_day else None

    root_rows = (await db.execute(_ROOT_MASTERY_SQL, {"user_id": user_id})).all()
    root_mastery: dict[str, float] = {}
    for r in root_rows:
        key = _root_key(r.root_name)
        if key is not None:
            root_mastery[key] = _as_float(r.mastery) or 0.0

    # Raw AVG(mastery), pre-floor. `player_level` below applies the FLE-49 catalog
    # floor to this same scalar; `player_mastery_raw` keeps the unfloored value for
    # rule 5's self-relative comparison in choose_mode (FLE-59) — floor and raw
    # answer different questions and must not collapse into one field.
    raw_avg_mastery = await db.scalar(_PLAYER_LEVEL_SQL, {"user_id": user_id})
    player_level = float(floor_player_level(raw_avg_mastery))
    player_mastery_raw = _as_float(raw_avg_mastery)

    song = await _load_song(db, user_id, song_id) if song_id is not None else None
    candidates = await _load_candidates(db, user_id)

    can_play_rows = (await db.execute(_CAN_PLAY_SQL, {"user_id": user_id})).all()
    can_play = tuple(
        ConsolidationSong(song_id=int(r.id), bpm=int(r.bpm)) for r in can_play_rows
    )

    recent_rows = (
        await db.execute(
            _RECENT_DRILLS_SQL,
            {
                "user_id": user_id,
                "terminal_states": list(TERMINAL_STATES),
                "recency_window": RECENCY_WINDOW,
            },
        )
    ).all()

    return GeneratorInputs(
        user_id=str(user_id),
        local_calendar_day=day,
        tz_offset_minutes=tz_offset_minutes,
        target_minutes=resolved_minutes,
        sessions_count=int(history.sessions_count or 0),
        days_since_last=days_since_last,
        completion_3=_as_float(history.completion_3),
        player_level=player_level,
        player_mastery_raw=player_mastery_raw,
        root_mastery=root_mastery,
        song=song,
        candidates=tuple(candidates),
        seed_drills=SEED_DRILLS,
        can_play_songs=can_play,
        recent_drill_ids=frozenset(r.drill_id for r in recent_rows),
    )
