"""Ground-truth tuning resolution for a `songs` row (FLE-108).

WHY THIS MODULE EXISTS
----------------------
`song_catalog.tuning` (migration 0007) is the only ground truth we have for a
song's recorded tuning. Migration 0014 gave `songs` a column to carry it into,
and `_ensure_catalog_song_as_user_song` (selectors/today_song.py) writes it on
the catalog-pick path. **That path is not how songs are born.** Nearly every
`songs` row comes from the onboarding wizard (api/v1/users.py step 4), which
inserts a user-named title/artist and never looks at the catalog at all.

Measured read-only against prod on 2026-10-05, after 0014 had been deployed:

    SELECT count(*), count(tuning) FROM songs;       -->  48, 0

Zero of 48. And 39 of those 48 rows *do* match a catalog row on
`lower(title)` + `lower(artist)`, 17 of them a **non-standard** one — six
`eb_standard` titles (Lenny, Little Wing, Eruption, Pride and Joy) and Kashmir
in DADGAD. So the ground truth existed for 17 rows, in a column one join away,
and `run_technique_breakdown` was passed NULL for every one of them.

WHAT THAT COST, BEFORE AND AFTER FLE-128
----------------------------------------
Before FLE-128 the NULL meant "let Sonnet infer", and on the five non-standard
rows that have actually generated it happened to infer right (songs 60, 71, 106,
107, 108 all carry the catalog's tuning in `breakdown.tab.tuning`). That is the
real reason FLE-89 could report tuning agreement 8/8: it was agreement between
Sonnet's guess and the catalog, not a check that ever fired. The guess was
unvalidated on every single row.

After FLE-128 the NULL means something worse: `_format_user_message` now
*defaults to standard* on a catalog miss and tells Sonnet not to infer. That is
the right policy for a song with no catalog row (FLE-125 §5.1 — a wrong
non-standard claim makes a user retune their guitar away from the record, a
wrong standard claim leaves a playable tab a semitone sharp), but applied to a
song whose catalog row says DADGAD it converts a correct inference into a
confident wrong default. Kashmir would have come back in standard tuning.

The two halves are one fix: FLE-128 may only suppress inference because this
module supplies the ground truth it is suppressing inference in favour of.

WHAT A NULL MEANS AFTER THIS MODULE
-----------------------------------
Once every write path goes through here, `songs.tuning IS NULL` means what
migration 0014 says it means — *no catalog row exists for this song* — rather
than "nobody wrote the column". That is FLE-125 option 1 and the surviving half
of FLE-108's "fail loudly": the off-catalog set is a `WHERE tuning IS NULL`
count, `Breakdown.tuning_source` records the same fact per generation, and
`resolve_known_tuning` logs the miss. It is deliberately NOT fatal — hard-failing
an off-catalog song would block free-text entry, which is the common path for
real users (FLE-128 §1-2).
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _key(title: str, artist: str | None) -> tuple[str, str]:
    """The join key, normalized the same way `songs_user_title_artist_uidx` is.

    Exact lower(title) + lower(artist), and no fuzzier. FLE-108 originally asked
    for an alias column to catch a band-vs-member credit ("Big Love — Fleetwood
    Mac" against a Lindsey Buckingham row). Measured against prod, that case has
    **zero instances**: joining on title alone and keeping rows whose artists
    differ returns 0 rows, because there is no Big Love row under either artist.
    The risk is real in principle — *Layla* entered under "Derek and the
    Dominos" would be it — but a looser join is machinery against a problem the
    data does not have, and it can match the wrong song. Adding the five missing
    catalog rows is the fix for those; that is Junie's roster call
    (261005-fle125-tuning-ground-truth.md §6 has the ground truth for each).
    """
    return (title.lower().strip(), (artist or "").lower().strip())


async def catalog_tuning_map(db: AsyncSession) -> dict[tuple[str, str], str]:
    """Every catalog row's tuning, keyed by `_key`. One query, 64 rows today.

    Loading the whole catalog beats a per-song lookup for the onboarding path,
    which inserts a handful of songs in one transaction.

    A duplicate (title, artist) pair with two different tunings would be a
    catalog data bug, not something to resolve here — it is logged and the
    lowest `id` wins so the result is at least deterministic.
    """
    rows = await db.execute(
        text(
            "SELECT id, title, artist, tuning FROM song_catalog ORDER BY id"
        )
    )
    out: dict[tuple[str, str], str] = {}
    for row in rows.mappings():
        key = _key(row["title"], row["artist"])
        if key in out and out[key] != row["tuning"]:
            logger.warning(
                "song_catalog has conflicting tunings for %r by %r (%s vs %s) — "
                "keeping the lower id. Fix the catalog row.",
                row["title"], row["artist"], out[key], row["tuning"],
            )
            continue
        out.setdefault(key, row["tuning"])
    return out


async def catalog_tuning(db: AsyncSession, title: str, artist: str | None) -> str | None:
    """The catalog's tuning tag for one (title, artist), or None off catalog."""
    row = await db.execute(
        text(
            "SELECT tuning FROM song_catalog "
            "WHERE lower(title) = lower(:t) AND lower(trim(artist)) = lower(:a) "
            "ORDER BY id LIMIT 1"
        ),
        {"t": title.strip(), "a": (artist or "").strip()},
    )
    return row.scalar_one_or_none()


async def resolve_known_tuning(db: AsyncSession, song) -> str | None:
    """The `known_tuning` to hand `run_technique_breakdown`, with a lazy backfill.

    Returns `songs.tuning` when it is set; otherwise looks the song up in the
    catalog and, on a hit, assigns the tag onto the ORM instance so the caller's
    existing commit persists it.

    The lazy write is what reaches the 39 prod rows that predate every write
    path here — without it they would stay NULL until each one was re-onboarded,
    and migration 0017 only backfills the rows present when it runs. **No commit
    and no flush of its own:** if the generation call then fails, nothing is
    written and the next tap resolves it again. The column is a cache of catalog
    ground truth, so a lost write costs one extra SELECT, never correctness.

    A miss is logged at WARNING. This is FLE-108's "fail loudly", scoped to what
    FLE-125 §5.1 settled: visible and counted, never fatal.
    """
    if getattr(song, "tuning", None) is not None:
        return song.tuning

    tuning = await catalog_tuning(db, song.title, song.artist)
    if tuning is None:
        logger.warning(
            "Off-catalog breakdown: no song_catalog row for %r by %r (songs.id=%s) "
            "— no tuning ground truth. Defaulting to standard and stamping "
            "tuning_source='default' (FLE-108/FLE-128).",
            song.title, song.artist, getattr(song, "id", None),
        )
        return None

    logger.info(
        "Resolved catalog tuning %r for %r by %r (songs.id=%s) — backfilling "
        "songs.tuning (FLE-108).",
        tuning, song.title, song.artist, getattr(song, "id", None),
    )
    song.tuning = tuning
    return tuning
