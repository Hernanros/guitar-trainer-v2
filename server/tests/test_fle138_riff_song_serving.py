"""FLE-138 — a verified song whose bars are a FIGURE must serve.

The FLE-110 serving path was only ever exercised by chord-progression songs,
because until the FLE-103 widening (commit `7cc2e81`) the verified set held
nothing else. The widening added two boogie songs whose every bar is a riff,
and `chord_sequence` for such a bar is None. That produced two defects, both
pinned here:

1. `_chord_diagrams` zipped the sequence with the measures and built
   `Chord(name=None)`, which is a pydantic ValidationError — so 2 of the 15
   verified songs could not be served at all, and the loader could not write
   them.

2. Fixing (1) alone would have left `_technique_notes` formatting the same
   Nones into the player-facing "Bar by bar" note, which read
   "1. None · 2. None · ..." on a practising user's screen. A crash became
   visible nonsense, which is why the two ship together.

These tests read the COMMITTED file and shape it in-process. There is
deliberately no database here: `test_fle110_verified_tab_serving.py` is gated on
a loaded Postgres and skips without one, and this is the regression that must
not be skippable — a `None`-shaped bar is a property of the file and the shaping
code, and neither needs a row to demonstrate.

    server/.venv/bin/python -m pytest tests/test_fle138_riff_song_serving.py

NO DB, NO LLM, NO SPEND.
"""
import json

import pytest

from app.models.song import Breakdown, is_renderable_breakdown
from app.verified_tabs import (
    breakdown_from_verified_song,
    load_pilot_set,
    stored_value_for,
)

# The two songs the widening added whose every bar is a figure. Named rather
# than discovered so that a future edit which quietly gives them chord names
# (and thereby stops exercising this path) fails the first test below instead of
# making the whole module vacuous.
FIGURE_SONGS = ("Sweet Home Chicago", "Pride and Joy")


def _songs() -> dict[str, dict]:
    return {s["title"]: s for s in load_pilot_set()["songs"]}


# ---------------------------------------------------------------------------
# The premise: these songs really do have unnamed bars
# ---------------------------------------------------------------------------

def test_the_figure_songs_still_have_no_named_bars():
    """Guard the premise of every other test in this module.

    If this fails, the committed set changed shape: either these songs were
    given chord names (in which case the rest of this module no longer tests
    the figure path and something else must), or they were removed.
    """
    songs = _songs()
    for title in FIGURE_SONGS:
        assert title in songs, f"{title} is no longer in the verified set"
        sequence = songs[title].get("chord_sequence") or []
        assert sequence, f"{title}: no chord_sequence at all"
        assert all(name is None for name in sequence), (
            f"{title}: expected every bar to be a figure (chord_sequence all "
            f"None), got {sequence!r} — this module no longer pins the figure path"
        )


# ---------------------------------------------------------------------------
# Defect 1 — the shaping must not raise
# ---------------------------------------------------------------------------

def test_every_verified_song_shapes_into_a_breakdown():
    """The issue's own reproduce, as an assertion.

    `breakdown_from_verified_song` is what `scripts/fle110_load_verified_tabs.py`
    calls per row, so a song that raises here is a song that cannot be loaded —
    which is how this was found.
    """
    failures = []
    for title, entry in _songs().items():
        try:
            breakdown_from_verified_song(entry)
        except Exception as exc:  # noqa: BLE001 — the point is which ones raise
            failures.append(f"{title}: {type(exc).__name__}: {exc}")
    assert not failures, "verified songs that cannot be served:\n" + "\n".join(failures)


@pytest.mark.parametrize("title", FIGURE_SONGS)
def test_a_figure_song_serves_with_no_chord_diagrams(title):
    """A figure bar yields no diagram, and that is the correct answer.

    Not an error and not an invented chord: see V10 in
    `scripts/fle103_verify_pilot_tabs.py` for why naming an E boogie bar is a
    lie in either direction. The tab itself is what the player reads here, and
    it is present and unaffected.
    """
    breakdown = breakdown_from_verified_song(_songs()[title])

    assert breakdown.chords == [], (
        f"{title}: expected no chord diagrams for an all-figure song, got "
        f"{[c.name for c in breakdown.chords]}"
    )
    assert breakdown.tab.measures, f"{title}: the tab itself must still be served"


@pytest.mark.parametrize("title", FIGURE_SONGS)
def test_a_figure_song_survives_the_round_trip_the_endpoint_does(title):
    """Shape it, store it, parse it back the way the breakdown endpoint does.

    An empty `chords` list is the new thing a consumer sees, so this asserts the
    two gates it has to clear: `Breakdown.model_validate` (the endpoint's cache
    branch) and `is_renderable_breakdown` (song_of_day's `_breakdown_available`).
    `chords` has no `min_length` and the renderability check tests key presence,
    so `[]` passes both — but it passes them by property, not by accident, and a
    future `min_length=1` on `chords` would break the pilot path right here.
    """
    stored = stored_value_for(_songs()[title], source="test", schema_version=2)
    # JSONB round-trip, as the column actually does it.
    stored = json.loads(json.dumps(stored))

    assert is_renderable_breakdown(stored), (
        f"{title}: stored row reads as unrenderable, so song_of_day's CTA would "
        "promise a breakdown the client drops to None (FLE-67)"
    )
    parsed = Breakdown.model_validate(stored)
    assert parsed.chords == []
    assert parsed.tab.measures


# ---------------------------------------------------------------------------
# Defect 2 — no player-facing note may say "None"
# ---------------------------------------------------------------------------

def test_no_technique_note_in_the_whole_set_contains_none():
    """The player-visible assertion, over every song rather than just the two.

    Phrased as a substring check on the rendered body because that is precisely
    what the defect looked like: valid JSON, parsed fine, and read
    "1. None · 2. None" to someone trying to practise.
    """
    offenders = []
    for title, entry in _songs().items():
        for note in breakdown_from_verified_song(entry).technique_notes:
            if "None" in note.body or "None" in note.heading:
                offenders.append(f"{title} [{note.heading}] {note.body}")
    assert not offenders, "player-facing notes containing None:\n" + "\n".join(offenders)


@pytest.mark.parametrize("title", FIGURE_SONGS)
def test_a_figure_song_omits_the_bar_by_bar_note_but_keeps_the_form(title):
    """Omitted, not emptied, and not renumbered.

    The form is the useful statement about a riff and it is already carried by
    "The passage" ("Bars 1-8 of the 12-bar form: I I I I IV IV I I"), so
    dropping "Bar by bar" costs the player nothing. Listing only the named bars
    renumbered 1..n was the alternative and is worse than it looks: it would
    relabel which bar of the song a chord falls on.
    """
    notes = {n.heading: n.body for n in breakdown_from_verified_song(_songs()[title]).technique_notes}

    assert "Bar by bar" not in notes, (
        f"{title}: an all-figure song must not carry a bar-by-bar chord note; got "
        f"{notes.get('Bar by bar')!r}"
    )
    assert "The passage" in notes, f"{title}: lost the one note that carries the form"
    assert "12-bar form" in notes["The passage"], (
        f"{title}: 'The passage' no longer states the form, so omitting "
        f"'Bar by bar' now does lose the player information: {notes['The passage']!r}"
    )


# ---------------------------------------------------------------------------
# The mixed case: some bars named, some not
# ---------------------------------------------------------------------------
# No committed song is part-chord/part-figure today, so this is the one shape
# here that is synthetic. It is worth pinning anyway: a part-figure song is the
# natural next verified entry (a blues with a turnaround figure over named
# changes), and the two plausible wrong answers — crashing, or renumbering the
# survivors — are both silent.

def test_a_part_figure_song_keeps_true_bar_numbers():
    entry = dict(_songs()["Sweet Home Chicago"])
    sequence = list(entry["chord_sequence"])
    assert len(sequence) >= 5, "fixture assumption: at least 5 bars"
    sequence[0] = "E7"
    sequence[4] = "A7"
    entry["chord_sequence"] = sequence

    breakdown = breakdown_from_verified_song(entry)

    notes = {n.heading: n.body for n in breakdown.technique_notes}
    assert "Bar by bar" in notes, "a partly-named song must still get the note"
    assert notes["Bar by bar"] == "1. E7 · 5. A7", (
        "the named bars must keep the bar numbers they actually fall on; "
        f"got {notes['Bar by bar']!r}"
    )
    assert "None" not in notes["Bar by bar"]
    assert [c.name for c in breakdown.chords] == ["E7", "A7"], (
        "expected a diagram for each named bar and none for the figure bars, got "
        f"{[c.name for c in breakdown.chords]}"
    )
