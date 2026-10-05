# server/tests/test_fle121_key_frame.py
# FLE-121 — songs.key names the SHAPES, and the sounding key is derived.
#
# The cases below are the four prod rows whose setup makes the two frames
# differ (all Eb standard, measured read-only 2026-10-05), plus the two
# tunings in TUNING_NOTE_MAP that no single interval describes, plus the capo
# case the schema already had a convention for. The point of the file is that
# "we could not work it out" and "the frames coincide" are different answers
# and never collapse into a bare None.
import pytest

from app.ai.breakdown import TUNING_NOTE_MAP
from app.models.practice_session import EmbeddedSong
from app.models.song import (
    SongResponse,
    sounding_key,
    sounding_key_from_breakdown,
    uniform_tuning_offset,
)

STANDARD = ["E", "A", "D", "G", "B", "e"]
EFLAT = ["Eb", "Ab", "Db", "Gb", "Bb", "Eb"]
DSTANDARD = ["D", "G", "C", "F", "A", "D"]
DADGAD = ["D", "A", "D", "G", "A", "D"]
DROP_D = ["D", "A", "D", "G", "B", "E"]


# --- uniform_tuning_offset -------------------------------------------------

@pytest.mark.parametrize(
    "tuning,expected",
    [
        (STANDARD, 0),
        (["E", "A", "D", "G", "B", "E"], 0),      # high string spelled upper
        (EFLAT, -1),
        (["D#", "G#", "C#", "F#", "A#", "D#"], -1),  # sharp spelling, same pitch
        (DSTANDARD, -2),
        (["E♭", "A♭", "D♭", "G♭", "B♭", "E♭"], -1),  # unicode flats
    ],
)
def test_uniform_retunes_have_an_offset(tuning, expected):
    assert uniform_tuning_offset(tuning) == expected


@pytest.mark.parametrize("tuning", [DADGAD, DROP_D, ["D", "G", "D", "G", "B", "D"]])
def test_non_uniform_retunes_have_no_offset(tuning):
    """Open G, DADGAD and drop D move strings by different amounts.

    There is no interval that maps a standard shape onto what it sounds, so
    the answer must be "no" rather than a plausible-looking number.
    """
    assert uniform_tuning_offset(tuning) is None


@pytest.mark.parametrize(
    "tuning", [None, [], ["E", "A", "D"], ["E", "A", "D", "G", "B", "H"]]
)
def test_missing_or_unparseable_tuning_has_no_offset(tuning):
    assert uniform_tuning_offset(tuning) is None


def test_every_tuning_in_the_prompt_vocabulary_is_classified():
    """Each TUNING_NOTE_MAP slug is either a transpose or explicitly not one.

    This is the guard that matters when someone adds a tuning: a new slug shows
    up here and has to be given an answer, instead of silently landing in
    whichever branch its note array happens to hit.
    """
    expected = {
        "standard": 0,
        "eb_standard": -1,
        "drop_d": None,
        "drop_c": None,
        "open_d": None,
        "open_e": None,
        "open_g": None,
        "dadgad": None,
    }
    assert set(expected) == set(TUNING_NOTE_MAP), (
        "TUNING_NOTE_MAP changed — classify the new slug here and in song.py §7.3"
    )
    for slug, offset in expected.items():
        assert uniform_tuning_offset(TUNING_NOTE_MAP[slug]["notes"]) == offset, slug


# --- sounding_key ----------------------------------------------------------

@pytest.mark.parametrize(
    "shape_key,tuning,capo,expected",
    [
        # The three prod rows that are right in the shape frame (ids 60/71/106).
        ("Em", EFLAT, 0, "Ebm"),       # Little Wing
        ("E", EFLAT, 0, "Eb"),         # Lenny, Pride and Joy
        ("E minor", EFLAT, 0, "Eb minor"),   # suffix carried, not reinterpreted
        ("A minor", EFLAT, 0, "Ab minor"),   # Eruption, were its key right
        # §7.2's own worked example, now applied at song level.
        ("Em", STANDARD, 2, "F#m"),    # Wonderwall: Em shapes sound F#m
        ("Em", EFLAT, 2, "Fm"),        # capo and retune compose, -1 + 2
        ("Bm", DSTANDARD, 0, "Am"),    # D standard is a uniform -2
    ],
)
def test_sounding_key_derives_the_absolute_key(shape_key, tuning, capo, expected):
    got, reason = sounding_key(shape_key, tuning, capo)
    assert (got, reason) == (expected, "ok")


def test_concert_pitch_reports_same_frame_not_a_duplicate():
    """§7.2's rule, at song level: a redundant copy is where a contradiction lives."""
    assert sounding_key("Bm", STANDARD, 0) == (None, "same_frame")


def test_a_capo_that_cancels_the_retune_is_the_same_frame():
    """Eb standard with a capo at 1 sounds at concert pitch again."""
    assert sounding_key("E", EFLAT, 1) == (None, "same_frame")


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_null_key_is_reported_as_no_key(missing):
    """19 of 48 prod rows. The caller has to be able to tell this from a failure."""
    assert sounding_key(missing, EFLAT, 0) == (None, "no_key")


def test_open_tuning_refuses_rather_than_guesses():
    """Kashmir is DADGAD; naming a sounding key for it would be invention."""
    assert sounding_key("D", DADGAD, 0) == (None, "non_uniform_setup")


def test_no_tuning_is_distinguished_from_a_tuning_we_cannot_use():
    assert sounding_key("E", None, 0) == (None, "no_setup")


def test_a_key_that_is_not_a_note_name_is_reported_not_swallowed():
    got, reason = sounding_key("modal", EFLAT, 0)
    assert (got, reason) == (None, "unparseable_key")


# --- reading the setup off a breakdown -------------------------------------

def _bd(tuning, capo=0):
    return {"tab": {"measures": [], "tuning": tuning, "capo": capo},
            "chords": [], "technique_notes": []}


def test_setup_comes_from_the_breakdown_not_songs_tuning():
    """songs.tuning is NULL on all 48 prod rows; tab.tuning is populated."""
    assert sounding_key_from_breakdown("Em", _bd(EFLAT)) == ("Ebm", "ok")


def test_breakdown_without_a_tab_reports_no_setup():
    assert sounding_key_from_breakdown("Em", {"chords": []}) == (None, "no_setup")
    assert sounding_key_from_breakdown("Em", None) == (None, "no_setup")


def test_no_key_beats_no_setup_when_both_are_missing():
    """The row has nothing to transpose, which is the more useful of the two."""
    assert sounding_key_from_breakdown(None, None) == (None, "no_key")


# --- the API surface -------------------------------------------------------

def test_song_response_serves_the_derived_sounding_key():
    song = SongResponse(
        id=106, title="Little Wing", artist="Jimi Hendrix", key="Em",
        breakdown=_bd(EFLAT),
    )
    assert song.key == "Em"            # the shapes, untouched
    assert song.key_sounding == "Ebm"  # what the record sounds


def test_song_response_leaves_sounding_null_at_concert_pitch():
    song = SongResponse(
        id=74, title="Comfortably Numb", artist="Pink Floyd", key="Bm",
        breakdown=_bd(STANDARD),
    )
    assert song.key_sounding is None


def test_song_response_ignores_a_supplied_sounding_key():
    """Derived means derived. A caller cannot post a frame contradiction."""
    song = SongResponse(
        id=106, title="Little Wing", artist="Jimi Hendrix", key="Em",
        key_sounding="G#m", breakdown=_bd(EFLAT),
    )
    assert song.key_sounding == "Ebm"


def test_song_response_without_a_breakdown_has_no_sounding_key():
    """Rows land with a placeholder breakdown until first request; no setup, no frame."""
    song = SongResponse(id=55, title="Big Love", artist="Fleetwood Mac", key="E")
    assert song.key_sounding is None


def test_embedded_song_derives_from_the_raw_envelope():
    """EmbeddedSong.breakdown is Any — the persisted dict, not a parsed model."""
    embedded = EmbeddedSong(
        song_id=71, title="Pride and Joy", artist="Stevie Ray Vaughan",
        key="E", breakdown=_bd(EFLAT),
    )
    assert (embedded.key, embedded.key_sounding) == ("E", "Eb")


def test_embedded_song_with_a_null_key_stays_null():
    """Chan Chan and Kashmir, the two served rows with no key at all."""
    embedded = EmbeddedSong(
        song_id=108, title="Kashmir", artist="Led Zeppelin",
        key=None, breakdown=_bd(DADGAD),
    )
    assert embedded.key_sounding is None
