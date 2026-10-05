# server/app/verified_tabs.py
# FLE-110 — serve the FLE-103 hand-verified tab instead of generating one.
#
# WHY THIS MODULE EXISTS
# ----------------------
# FLE-103 hand-verified ten songs and committed them to
# server/data/verified_tabs/pilot_verified_set.json. That alone changes nothing a
# pilot user sees, for the reason its writeup §1a states: there is no stored tab
# in the catalog to mark verified. `song_catalog.breakdown` is NULL on all 64 prod
# rows; tab exists only in `songs` rows minted per user per day by the generator.
# So verifying a tab verifies one user's instance from one day, and the next
# request regenerates it. FLE-102 measured that generated corpus at 0 of 10 fit
# for a stranger to practise from.
#
# This module is the half that makes the verification load-bearing: a place to put
# the committed tab (`song_catalog.verified_tab`, migration 0016), the shaping that
# turns a verified-set entry into the Breakdown the API already serves, and the
# lookups the selector and the breakdown endpoint use.
#
# THE PILOT FLAG
# --------------
# Everything here is inert unless FLETCHER_PILOT_VERIFIED_ONLY is on. With the flag
# off the selector, the breakdown endpoint and song-of-day behave exactly as they
# did before this module existed — the column is written, and nothing reads it.
# That is deliberate: the restriction is a pilot posture, not a product decision,
# and Hernan owns when it switches on (see the FLE-88 readiness bar).
#
# WHAT THE COLUMN HOLDS
# ---------------------
# A Breakdown-shaped object — `{tab, chords, technique_notes}` — NOT a bare `Tab`.
# The issue offers the bare Tab ("the JSON is already in Tab schema so it can be
# loaded as-is") and that would work, but it would push the wrapping into the serve
# path, which means two call sites fabricating `chords`/`technique_notes` per
# request and a third shape for consumers to learn. Storing the Breakdown means
# `is_renderable_breakdown` and `_load_cached_breakdown` read it unchanged, and the
# endpoint's verified branch is a pure read.
#
# `chords` and `technique_notes` are DERIVED MECHANICALLY from the verified entry,
# never invented:
#   - each chord diagram is read back off the verified voicing itself (the first
#     beat of the first measure that declares that chord name), so a diagram cannot
#     contradict the tab beside it;
#   - the technique notes are the set's own `key`/`time_signature`/`feel`/`bpm`
#     metadata and Fret's `passage` prose, plus the bar-by-bar chord sequence.
# The set's internal fields — `why_in_set`, `mode_flip_guard`, `cannot_verify` —
# are NOT surfaced. They are written for the team ("a G# here is the T1 defect"),
# not for a player, and the `cannot_verify` caveats are a curation record rather
# than copy anyone has reviewed for the app's voice.
#
# Drills are out of scope (issue, "Not in scope"): the verified set is tab only, so
# the stored Breakdown carries no `drills` key and the drill path is unchanged.
import hashlib
import json
import logging
import os
import pathlib
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.song import (
    Breakdown,
    Chord,
    ChordPosition,
    Measure,
    Tab,
    TechniqueNote,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The pilot flag
# ---------------------------------------------------------------------------
PILOT_VERIFIED_ONLY_ENV = "FLETCHER_PILOT_VERIFIED_ONLY"

_TRUTHY = {"1", "true", "yes", "on", "y"}
_FALSEY = {"", "0", "false", "no", "off", "n"}


def pilot_verified_only() -> bool:
    """True when the pilot is restricted to songs with a hand-verified tab.

    Read per call rather than at import, matching `app.ai.governor.effective_cap`:
    this is a deploy-time env setting on the Railway `fletcher` service, not a
    code change. An unparseable value logs and reads as OFF — a typo must not
    silently narrow the catalog to ten songs.
    """
    raw = os.environ.get(PILOT_VERIFIED_ONLY_ENV, "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSEY:
        return False
    logger.warning(
        "Ignoring unparseable %s=%r; pilot verified-only mode stays OFF.",
        PILOT_VERIFIED_ONLY_ENV, raw,
    )
    return False


# ---------------------------------------------------------------------------
# The player_level cap (issue scope item 4)
# ---------------------------------------------------------------------------
# The selector's catalog window is ABS(difficulty - player_level) <= 0.15. With
# only the ten verified songs in play, FLE-103 §5 measured the coverage:
#
#     THIN  player_level 0.75-0.79: only 'Hotel California'  — no reroll variety
#     THIN  player_level 0.80-1.00: only 'Giant Steps'       — no reroll variety
#
# A user above ~0.75 therefore sees one song and a reroll that cannot move — the
# FLE-49 collapse in miniature. 0.70 is the highest cap that keeps two songs in
# every reachable window (0.70 ± 0.15 holds Blue Bossa 0.58, Ipanema 0.60 and
# Hotel California 0.65).
#
# A NOTE ON "SEEDED". The issue says "cap seeded player_level at 0.70", and read
# literally that is a no-op today: `_ONBOARDING_SEED_MASTERY` in api/v1/users.py
# tops out at 0.50 for can_play, player_level is AVG(mastery) over leaves, so no
# newly-onboarded user can start above 0.50. The level that actually reaches 0.75
# is one EARNED during the pilot — sessions/mastery.py shifts nodes up on a
# "that's what I'm looking for". So the cap is applied to the level the SELECTOR
# uses, not to the seed, which is the only placement that closes the window the
# issue is worried about.
#
# It is applied in the selector ONLY. player_level also feeds the Sonnet teaching
# prompt (breakdowns.py) and session planning (sessions/plan.py), where capping it
# would mean teaching a 0.85 player as a 0.70 player for no reason connected to
# catalog coverage.
PILOT_PLAYER_LEVEL_CAP = Decimal("0.70")

# The no-op cap. player_level is NUMERIC(4,3) in [0,1], so LEAST(level, 1) is the
# identity — binding this when the flag is off keeps the SQL one shape rather than
# two, which is what stops the capped and uncapped paths drifting apart.
NO_PLAYER_LEVEL_CAP = Decimal("1.0")


def effective_player_level_cap() -> Decimal:
    """The ceiling the selector applies to player_level, honouring the pilot flag."""
    return PILOT_PLAYER_LEVEL_CAP if pilot_verified_only() else NO_PLAYER_LEVEL_CAP


# ---------------------------------------------------------------------------
# The committed set
# ---------------------------------------------------------------------------
PILOT_SET_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "data"
    / "verified_tabs"
    / "pilot_verified_set.json"
)

# Key the loader stamps into the stored Breakdown so an operator can tell which
# revision of the file a row came from without re-reading the file. Leading
# underscore because it is not part of the Breakdown contract: Pydantic v2 ignores
# unknown keys by default, so `Breakdown.model_validate` and
# `is_renderable_breakdown` both read the row exactly as if it were absent.
PROVENANCE_KEY = "_verified_tab_provenance"

# The chord-diagram window the mobile renderer draws (ChordDiagram.tsx:19,
# FRETS_SHOWN). A voicing wider than this has dots the renderer silently drops
# (ChordDiagram.tsx:139, "Out-of-window fret — skip"), so a diagram for it would
# be a partial lie printed next to a correct tab. Those chords get no diagram.
CHORD_DIAGRAM_FRETS_SHOWN = 4


def load_pilot_set(path: Optional[pathlib.Path] = None) -> dict:
    """Read the committed verified set. Raises on a missing or malformed file."""
    return json.loads((path or PILOT_SET_PATH).read_text())


def song_fingerprint(entry: dict) -> str:
    """Stable sha256 over one verified-set entry.

    The loader stores this and compares it on the next run, so "the file was
    regenerated by fle103_build_pilot_set.py" is detectable per song rather than
    by diffing JSONB. `sort_keys` so a key-order change in the builder is not read
    as a content change.
    """
    return hashlib.sha256(
        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Shaping: verified-set entry -> Breakdown
# ---------------------------------------------------------------------------

def _chord_diagrams(tab: Tab, chord_sequence: list[str]) -> list[Chord]:
    """Read one diagram per distinct chord name back off the verified voicing.

    `chord_sequence` is one chord name per measure (FLE-103 commits all ten songs
    that way, 8 names for 8 measures), and within a measure every beat restates the
    same voicing. So the first beat of the first measure that declares a name IS
    the verified shape for that name, and reading it back is arithmetic rather than
    a second act of transcription — which is the point: a hand-drawn diagram could
    disagree with the tab printed beside it, and this one cannot.

    Strings absent from the voicing are emitted as `fret: -1` ("not played" in a
    diagram, §7.1.3) rather than omitted, so the renderer draws the × it draws for
    every other muted string.

    `finger` stays None throughout. The verified set records no fingering and
    inventing one would be exactly the kind of confident unchecked judgement
    FLE-103 §3 caught itself making. `barre_fret` stays None for the same reason —
    a barre is inferable from a repeated fret, but "inferable" is not "verified".
    """
    first_measure_for: dict[str, Measure] = {}
    for name, measure in zip(chord_sequence, tab.measures):
        if name not in first_measure_for:
            first_measure_for[name] = measure

    chords: list[Chord] = []
    for name, measure in first_measure_for.items():
        if not measure.beats or not measure.beats[0].notes:
            continue
        sounded = {n.string: n.fret for n in measure.beats[0].notes}
        fretted = [f for f in sounded.values() if f > 0]
        if fretted:
            lowest, highest = min(fretted), max(fretted)
            base_fret = 1 if highest <= CHORD_DIAGRAM_FRETS_SHOWN else lowest
            if highest - base_fret >= CHORD_DIAGRAM_FRETS_SHOWN:
                logger.warning(
                    "No chord diagram for %r: frets %d-%d do not fit the renderer's "
                    "%d-fret window (FLE-110).",
                    name, lowest, highest, CHORD_DIAGRAM_FRETS_SHOWN,
                )
                continue
        else:
            base_fret = 1
        chords.append(
            Chord(
                name=name,
                positions=[
                    ChordPosition(string=s, fret=sounded.get(s, -1))
                    for s in range(1, 7)
                ],
                base_fret=base_fret,
            )
        )
    return chords


def _technique_notes(entry: dict) -> list[TechniqueNote]:
    """The two player-facing notes a verified entry can honestly support.

    Everything here is the verified entry's own content. Nothing is written for
    this function; see the module header for why `why_in_set`, `mode_flip_guard`
    and `cannot_verify` are deliberately left out.
    """
    notes: list[TechniqueNote] = []

    setup = ", ".join(
        part for part in (
            entry.get("key"),
            entry.get("time_signature"),
            entry.get("feel"),
            f"around {entry['bpm']} BPM" if entry.get("bpm") else None,
        ) if part
    )
    passage = (entry.get("passage") or "").strip()
    body = f"{setup}. {passage}".strip() if setup else passage
    if body:
        notes.append(TechniqueNote(heading="The passage", body=body))

    sequence = entry.get("chord_sequence") or []
    if sequence:
        notes.append(
            TechniqueNote(
                heading="Bar by bar",
                body=" · ".join(
                    f"{i}. {name}" for i, name in enumerate(sequence, 1)
                ),
            )
        )
    return notes


def breakdown_from_verified_song(entry: dict) -> Breakdown:
    """Shape one verified-set entry into the Breakdown the API serves.

    Raises pydantic.ValidationError if the committed tab does not satisfy the real
    schema — which is the behaviour the loader wants. A verified set that cannot be
    parsed by the same model the endpoint parses is not ground truth, and failing
    the load is better than discovering it on a pilot user's screen.
    """
    tab = Tab.model_validate(entry["tab"])
    return Breakdown(
        tab=tab,
        chords=_chord_diagrams(tab, entry.get("chord_sequence") or []),
        technique_notes=_technique_notes(entry),
    )


def stored_value_for(entry: dict, source: str, schema_version: object) -> dict:
    """The JSONB payload for one `song_catalog.verified_tab` row."""
    payload = breakdown_from_verified_song(entry).model_dump()
    # `drills` is dropped, not emitted empty: Breakdown.drills has min_length=2
    # which fires on EXPLICIT input, so a stored `"drills": []` would make
    # `Breakdown.model_validate` fail on read-back. breakdowns.py works around
    # that with `_load_cached_breakdown`; not writing the key in the first place
    # means the verified row does not need the workaround.
    payload.pop("drills", None)
    payload[PROVENANCE_KEY] = {
        "issue": "FLE-110",
        "verified_by": "FLE-103",
        "source": source,
        "set_schema_version": schema_version,
        "sha256": song_fingerprint(entry),
    }
    return payload


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
# Both match the catalog on (lower(title), lower(artist)) — the same key
# `_ensure_catalog_song_as_user_song` and `songs_user_title_artist_uidx` already
# use to relate a songs row to its catalog entry, and the key covered by
# `song_catalog_title_artist_uidx`. There is no FK from songs to song_catalog to
# use instead; adding one is a bigger change than this issue, and the title+artist
# join is the relation the rest of the request path already trusts.

_VERIFIED_TAB_SELECT = text(
    "SELECT verified_tab FROM song_catalog "
    "WHERE verified_tab IS NOT NULL "
    "  AND lower(title) = lower(:title) "
    "  AND lower(artist) = lower(:artist) "
    "LIMIT 1"
)

_VERIFIED_TAB_EXISTS = text(
    "SELECT EXISTS ("
    "  SELECT 1 FROM song_catalog "
    "  WHERE verified_tab IS NOT NULL "
    "    AND lower(title) = lower(:title) "
    "    AND lower(artist) = lower(:artist)"
    ")"
)


async def verified_breakdown_for(
    db: AsyncSession, title: str, artist: Optional[str]
) -> Optional[dict]:
    """The stored verified Breakdown for a song, or None.

    Returns None for a NULL/blank artist rather than matching loosely: the catalog
    key is title+artist and a title-only match would serve one song's verified tab
    under another song's name.
    """
    if not title or not (artist or "").strip():
        return None
    return await db.scalar(_VERIFIED_TAB_SELECT, {"title": title, "artist": artist})


async def has_verified_tab(
    db: AsyncSession, title: str, artist: Optional[str]
) -> bool:
    """Existence-only form of `verified_breakdown_for` — no JSONB over the wire."""
    if not title or not (artist or "").strip():
        return False
    return bool(
        await db.scalar(_VERIFIED_TAB_EXISTS, {"title": title, "artist": artist})
    )
