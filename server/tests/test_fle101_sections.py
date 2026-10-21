"""FLE-101 — sections, the drill → passage link, and the unlock state machine.

Design: FLE-100's `design` document, revision 3, approved 2026-10-21.

The thing being protected here is a product claim, not a schema: finishing every
drill that prepares a passage UNLOCKS that passage, and a player who has not done
that does NOT get it. Two ways to break that claim, and the tests are organised
around both:

  * GRANTING IT FOR FREE — a section that reads cleared when its drills are
    untouched. The LEFT JOIN in `_SECTION_TALLY_SQL` is the load-bearing line (a
    drill the player has never attempted has no `drill_progress` row at all), and
    `test_untouched_section_is_locked_not_auto_cleared` is the test that would
    catch an inner join turning every reward in the app into a freebie.

  * TAKING IT BACK — a cleared section that re-locks. §7.5's maintenance shelving
    is the mechanism that would do it, on a clock, for doing nothing wrong.

Everything else is fail-soft: a model that mislabels a section must cost the
labelling, never the breakdown, and never the 10 breakdowns already cached in prod.

The DB half requires the Postgres dev DB migrated to 0020, same as
test_backfill_drills.py — the predicate is mostly join and NULL behaviour, which a
mock harness would get right while the real query got it wrong.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.breakdown import SYSTEM_PROMPT
from app.models.song import MAX_SECTIONS, Breakdown, Section, tab_beat_count
from app.sessions.sections import (
    OWNED_STATES,
    section_beat_window,
    section_for_drill,
    section_states,
    unlock_for_mastered_drill,
)

# No module-level `pytest.mark.asyncio`: pytest.ini runs asyncio_mode=auto, and
# marking the module would also mark the dozen SYNC schema tests below, which
# pytest-asyncio then warns about one by one.


# ---------------------------------------------------------------------------
# Fixtures for the pure-schema half
# ---------------------------------------------------------------------------

def _beat(fret: int) -> dict:
    return {"notes": [{"string": 1, "fret": fret, "duration": "quarter"}]}


def _measure() -> dict:
    """One 4/4 measure of four quarter notes — four beat COLUMNS."""
    return {"beats": [_beat(i) for i in range(4)], "time_signature": "4/4"}


def _tab(measures: int = 2) -> dict:
    return {"measures": [_measure() for _ in range(measures)]}


def _drill(name: str, **overrides) -> dict:
    base = {
        "name": name,
        "target_skill_temp_id": "skill-1",
        "song_specific": False,
        "what": f"Isolate {name}, the move the intro turns on.",
        "tab_snippet": _tab(1),
        "start_bpm": 60,
        "target_bpm": 70,
        "repetitions": 8,
        "success_criterion": "Clean at target tempo.",
    }
    base.update(overrides)
    return base


def _raw(**overrides) -> dict:
    base = {"tab": _tab(2), "chords": [], "technique_notes": []}
    base.update(overrides)
    return base


# N = 8 for a 2-measure tab of quarter notes.
_INTRO = {"label": "Intro", "start_beat": 0, "end_beat": 3}
_RIFF = {"label": "Riff", "start_beat": 4, "end_beat": 7}


# ---------------------------------------------------------------------------
# §1 — beat addressing
# ---------------------------------------------------------------------------

def test_beats_are_columns_not_quarter_notes():
    """The addressing unit is the COLUMN, so a bar of eighths is 8, not 4.

    Worth pinning because 'beat' reads as a quarter note to a musician, and a
    renderer slicing `measures[*].beats[*]` by quarter-note position would draw the
    wrong half of every passage while every range still looked plausible.
    """
    eighths = {
        "measures": [
            {
                "beats": [
                    {"notes": [{"string": 1, "fret": 0, "duration": "eighth"}]}
                    for _ in range(8)
                ],
                "time_signature": "4/4",
            }
        ]
    }
    parsed = Breakdown.model_validate(_raw(tab=eighths))
    assert tab_beat_count(parsed.tab) == 8


# ---------------------------------------------------------------------------
# §5 — backward compatibility. The 10 breakdowns cached in prod.
# ---------------------------------------------------------------------------

def test_a_breakdown_with_no_sections_key_still_parses():
    """The prod case: every cached row predates `sections` and must read back."""
    parsed = Breakdown.model_validate(_raw(drills=[_drill("d1"), _drill("d2")]))
    assert parsed.sections == []
    assert all(d.prepares_section is None for d in parsed.drills)
    assert all(d.section_start_beat is None for d in parsed.drills)


def test_an_unsectioned_drill_belongs_to_the_implicit_section():
    """§5 — no link means the already-cleared whole-song section, not a broken one."""
    assert section_for_drill(None, None, None) is None


def test_a_half_written_link_is_treated_as_no_link():
    """A label with no beat range cannot place a passage, so it places nothing."""
    assert section_for_drill("Intro", None, None) is None
    assert section_for_drill("Intro", 0, None) is None


# ---------------------------------------------------------------------------
# §1 — the coverage rule. Sound partition, or no sections at all.
# ---------------------------------------------------------------------------

def test_a_sound_partition_survives_with_its_drill_links():
    parsed = Breakdown.model_validate(
        _raw(
            sections=[_INTRO, _RIFF],
            drills=[
                _drill("d1", prepares_section="Intro", section_start_beat=0, section_end_beat=2),
                _drill("d2", prepares_section="Riff", section_start_beat=4, section_end_beat=7),
            ],
        )
    )
    assert [(s.label, s.start_beat, s.end_beat) for s in parsed.sections] == [
        ("Intro", 0, 3),
        ("Riff", 4, 7),
    ]
    assert [d.prepares_section for d in parsed.drills] == ["Intro", "Riff"]


def test_a_single_section_covering_everything_is_valid():
    """§1 — a 6-measure riff with no internal break is ONE section, not a failure."""
    parsed = Breakdown.model_validate(
        _raw(sections=[{"label": "Riff", "start_beat": 0, "end_beat": 7}])
    )
    assert len(parsed.sections) == 1


@pytest.mark.parametrize(
    "sections,why",
    [
        ([{"label": "Intro", "start_beat": 0, "end_beat": 2},
          {"label": "Riff", "start_beat": 4, "end_beat": 7}], "gap at beat 3"),
        ([{"label": "Intro", "start_beat": 0, "end_beat": 4},
          {"label": "Riff", "start_beat": 4, "end_beat": 7}], "beat 4 double-booked"),
        ([{"label": "Intro", "start_beat": 1, "end_beat": 7}], "does not start at 0"),
        ([{"label": "Intro", "start_beat": 0, "end_beat": 6}], "does not reach N-1"),
        ([_RIFF, _INTRO], "out of start_beat order"),
        ([{"label": "Intro", "start_beat": 5, "end_beat": 1}], "inverted range"),
        ([{"label": "Intro", "start_beat": -1, "end_beat": 7}], "negative beat"),
        ([{"start_beat": 0, "end_beat": 7}], "no label"),
    ],
)
def test_an_unsound_sections_list_is_dropped_whole_never_raised(sections, why):
    """§1/§5 — degrade the song to unsectioned; never fail the breakdown over it.

    All-or-nothing on purpose. §3 gates a reward on every drill resolving to exactly
    one section, so a half-trusted partition would gate a reward on a range nobody
    checked. Dropping lands the song on the path all 10 cached rows are already on.
    """
    parsed = Breakdown.model_validate(_raw(sections=sections))
    assert parsed.sections == [], f"should have dropped: {why}"


def test_too_many_sections_are_dropped_rather_than_truncated():
    """Choosing which passages to discard is not a repair anyone can make honestly."""
    parsed = Breakdown.model_validate(
        _raw(
            tab=_tab(5),
            sections=[
                {"label": f"Riff {i}", "start_beat": i * 4, "end_beat": i * 4 + 3}
                for i in range(MAX_SECTIONS + 1)
            ],
        )
    )
    assert parsed.sections == []


def test_dropping_sections_also_clears_every_drill_link():
    """A link to a section that no longer exists must not survive the drop."""
    parsed = Breakdown.model_validate(
        _raw(
            sections=[{"label": "Intro", "start_beat": 0, "end_beat": 2}],  # unsound
            drills=[
                _drill("d1", prepares_section="Intro", section_start_beat=0, section_end_beat=2),
                _drill("d2", prepares_section="Intro", section_start_beat=1, section_end_beat=2),
            ],
        )
    )
    assert parsed.sections == []
    assert all(d.prepares_section is None for d in parsed.drills)


# ---------------------------------------------------------------------------
# §2 — the drill link repairs PER DRILL, not per breakdown
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad,why",
    [
        ({"prepares_section": "Nope", "section_start_beat": 0, "section_end_beat": 2},
         "label matches no section"),
        ({"prepares_section": "Intro", "section_start_beat": 2, "section_end_beat": 6},
         "range runs past the section's end"),
        ({"prepares_section": "Riff", "section_start_beat": 0, "section_end_beat": 2},
         "range sits in a different section"),
        ({"prepares_section": "Intro", "section_start_beat": 3, "section_end_beat": 1},
         "inverted range"),
        ({"prepares_section": "Intro"},
         "no beat range at all"),
    ],
)
def test_one_bad_link_costs_that_drill_only(bad, why):
    parsed = Breakdown.model_validate(
        _raw(
            sections=[_INTRO, _RIFF],
            drills=[
                _drill("broken", **bad),
                _drill("fine", prepares_section="Riff", section_start_beat=4, section_end_beat=7),
            ],
        )
    )
    assert len(parsed.sections) == 2, f"a drill's {why} must not cost the sections"
    assert parsed.drills[0].prepares_section is None
    assert parsed.drills[0].section_start_beat is None
    assert parsed.drills[0].section_end_beat is None
    assert parsed.drills[1].prepares_section == "Riff", "the good drill keeps its link"


def test_several_drills_may_prepare_the_same_section():
    parsed = Breakdown.model_validate(
        _raw(
            sections=[_INTRO, _RIFF],
            drills=[
                _drill("a", prepares_section="Intro", section_start_beat=0, section_end_beat=1),
                _drill("b", prepares_section="Intro", section_start_beat=2, section_end_beat=3),
            ],
        )
    )
    assert [d.prepares_section for d in parsed.drills] == ["Intro", "Intro"]


def test_section_beat_window_clamps_to_the_tab():
    parsed = Breakdown.model_validate(_raw(sections=[_INTRO, _RIFF]))
    assert section_beat_window(parsed, "Riff") == (4, 7)
    assert section_beat_window(parsed, "Nothing") is None


# ---------------------------------------------------------------------------
# The generator prompt
# ---------------------------------------------------------------------------

def test_prompt_teaches_the_partition_rule_and_the_cap():
    assert "SECTIONS" in SYSTEM_PROMPT
    assert "PARTITION THE TAB EXACTLY" in SYSTEM_PROMPT
    assert "No gaps, no overlaps" in SYSTEM_PROMPT
    assert "Four is the hard cap" in SYSTEM_PROMPT


def test_prompt_teaches_beat_columns_not_quarter_notes():
    """The one addressing mistake that produces plausible, wrong ranges."""
    assert "BEAT COLUMNS, not quarter notes" in SYSTEM_PROMPT


def test_prompt_forbids_splitting_on_a_chord_change():
    """Big Love / Chan Chan / Kashmir each fragment into 3-4 under a chord rule."""
    assert "NEVER ON A CHORD CHANGE ALONE" in SYSTEM_PROMPT


def test_prompt_names_every_section_label_the_schema_documents():
    for label in ("Intro", "Riff", "Verse", "Pre-Chorus", "Chorus",
                  "Bridge", "Solo", "Turnaround", "Outro", "Interlude"):
        assert label in SYSTEM_PROMPT, f"prompt never offers {label!r}"


def test_prompt_asks_for_all_three_drill_link_fields():
    for field in ("prepares_section", "section_start_beat", "section_end_beat"):
        assert field in SYSTEM_PROMPT, f"prompt never asks for {field}"


def test_the_tool_schema_carries_the_new_fields():
    """The tool definition is generated from the model, so this is the wire check."""
    schema = Breakdown.model_json_schema()
    assert "sections" in schema["properties"]
    drill = schema["$defs"]["Drill"]["properties"]
    for field in ("prepares_section", "section_start_beat", "section_end_beat"):
        assert field in drill


# ---------------------------------------------------------------------------
# §3 — the graduation predicate, against the real database
# ---------------------------------------------------------------------------

def _db_url() -> str:
    raw = os.environ.get(
        "DATABASE_URL", "postgresql://gt:devpass@localhost:5433/guitar_trainer"
    )
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    return raw


engine = create_async_engine(_db_url(), echo=False, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db():
    """Function-scoped session; every test seeds and rolls back."""
    async with SessionFactory() as session:
        yield session
        await session.rollback()


async def _seed_user(db: AsyncSession) -> str:
    user_id = str(uuid.uuid4())
    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:uid, '{}'::jsonb)"),
        {"uid": user_id},
    )
    await db.flush()
    return user_id


async def _seed_song(db: AsyncSession, user_id: str, breakdown: dict) -> int:
    await db.execute(
        text(
            "INSERT INTO songs (title, artist, breakdown, user_id, category, "
            "                   breakdown_generated_at) "
            "VALUES ('S', 'A', CAST(:bd AS jsonb), :uid, 'working_on', now())"
        ),
        {"bd": json.dumps(breakdown), "uid": user_id},
    )
    await db.flush()
    return (
        await db.execute(
            text("SELECT id FROM songs WHERE user_id = :uid ORDER BY id DESC LIMIT 1"),
            {"uid": user_id},
        )
    ).scalar_one()


async def _bank_drill(
    db: AsyncSession,
    user_id: str,
    song_id: int,
    name: str,
    *,
    label: str | None,
    start: int | None = 0,
    end: int | None = 1,
    status: str = "active",
) -> str:
    """One row in the drill bank, with or without a §2 section link."""
    skill_id = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO skill_nodes (id, user_id, name, level, mastery) "
            "VALUES (:sid, :uid, :nm, 'leaf', 0.3)"
        ),
        {"sid": skill_id, "uid": user_id, "nm": f"Leaf {name}"},
    )
    drill_id = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO drills (id, user_id, name, name_normalized, skill_node_id, "
            "    song_specific, what, tab_snippet, start_bpm, target_bpm, repetitions, "
            "    success_criterion, origin_song_id, origin_drill_index, status, "
            "    prepares_section, section_start_beat, section_end_beat) "
            "VALUES (:id, :uid, :nm, :nm, :sid, false, 'w', '{}'::jsonb, 60, 70, 8, "
            "    'sc', :song, 0, CAST(:st AS drill_status), :lbl, :sb, :eb)"
        ),
        {
            "id": drill_id, "uid": user_id, "nm": name, "sid": skill_id,
            "song": song_id, "st": status,
            "lbl": label,
            "sb": start if label is not None else None,
            "eb": end if label is not None else None,
        },
    )
    await db.flush()
    return drill_id


async def _set_state(db: AsyncSession, user_id: str, drill_id: str, state: str) -> None:
    await db.execute(
        text(
            "INSERT INTO drill_progress (id, user_id, drill_id, rung_bpm, state, "
            "                            mastered_at) "
            "VALUES (:id, :uid, :did, 70, CAST(:st AS drill_progress_state), "
            "        CASE WHEN :st = 'mastered' THEN now() ELSE NULL END)"
        ),
        {"id": str(uuid.uuid4()), "uid": user_id, "did": drill_id, "st": state},
    )
    await db.flush()


_TWO_SECTION_BREAKDOWN = _raw(sections=[_INTRO, _RIFF])


async def test_an_unsectioned_song_reports_no_sections(db: AsyncSession):
    """§5 — the prod case. No cards, nothing gated, exactly today's behaviour."""
    user_id = await _seed_user(db)
    song_id = await _seed_song(db, user_id, _raw())
    states = await section_states(
        db, user_id=uuid.UUID(user_id), song_id=song_id,
        breakdown=Breakdown.model_validate(_raw()),
    )
    assert states == []


async def test_untouched_section_is_locked_not_auto_cleared(db: AsyncSession):
    """THE test. A drill the player never attempted has NO drill_progress row.

    An inner join here would drop the requirement entirely, report '0 of 0', and
    auto-clear the section — handing out every reward in the app for free while
    every count still looked sane. The LEFT JOIN is what makes absent read as
    not-mastered.
    """
    user_id = await _seed_user(db)
    song_id = await _seed_song(db, user_id, _TWO_SECTION_BREAKDOWN)
    await _bank_drill(db, user_id, song_id, "intro-a", label="Intro")
    await _bank_drill(db, user_id, song_id, "intro-b", label="Intro")

    states = await section_states(
        db, user_id=uuid.UUID(user_id), song_id=song_id,
        breakdown=Breakdown.model_validate(_TWO_SECTION_BREAKDOWN),
    )
    intro = next(s for s in states if s.label == "Intro")
    assert intro.required_drills == 2
    assert intro.mastered_drills == 0
    assert intro.cleared is False
    assert intro.remaining == 2


async def test_a_section_clears_only_when_its_last_drill_is_mastered(db: AsyncSession):
    user_id = await _seed_user(db)
    song_id = await _seed_song(db, user_id, _TWO_SECTION_BREAKDOWN)
    first = await _bank_drill(db, user_id, song_id, "intro-a", label="Intro")
    last = await _bank_drill(db, user_id, song_id, "intro-b", label="Intro")

    async def intro():
        states = await section_states(
            db, user_id=uuid.UUID(user_id), song_id=song_id,
            breakdown=Breakdown.model_validate(_TWO_SECTION_BREAKDOWN),
        )
        return next(s for s in states if s.label == "Intro")

    await _set_state(db, user_id, first, "mastered")
    assert (await intro()).cleared is False, "one of two is not graduation"

    await _set_state(db, user_id, last, "mastered")
    cleared = await intro()
    assert cleared.cleared is True
    assert (cleared.mastered_drills, cleared.required_drills) == (2, 2)


async def test_mastering_the_last_drill_returns_the_unlock(db: AsyncSession):
    """The reward moment: the payload the celebration modal is built from."""
    user_id = await _seed_user(db)
    song_id = await _seed_song(db, user_id, _TWO_SECTION_BREAKDOWN)
    first = await _bank_drill(db, user_id, song_id, "intro-a", label="Intro")
    last = await _bank_drill(db, user_id, song_id, "intro-b", label="Intro")

    await _set_state(db, user_id, first, "mastered")
    assert await unlock_for_mastered_drill(
        db, user_id=uuid.UUID(user_id), drill_id=uuid.UUID(first)
    ) is None, "a sibling is still unmastered — no unlock"

    await _set_state(db, user_id, last, "mastered")
    unlock = await unlock_for_mastered_drill(
        db, user_id=uuid.UUID(user_id), drill_id=uuid.UUID(last)
    )
    assert unlock is not None
    assert unlock.label == "Intro"
    assert (unlock.start_beat, unlock.end_beat) == (0, 3)
    assert unlock.song_id == song_id


async def test_sections_unlock_independently(db: AsyncSession):
    """Decision 3 — clearing the Riff must not wait on the Intro.

    A dependency chain would let one bad model-generated drill soft-lock the rest
    of the song.
    """
    user_id = await _seed_user(db)
    song_id = await _seed_song(db, user_id, _TWO_SECTION_BREAKDOWN)
    await _bank_drill(db, user_id, song_id, "intro-a", label="Intro")
    riff = await _bank_drill(db, user_id, song_id, "riff-a", label="Riff", start=4, end=7)

    await _set_state(db, user_id, riff, "mastered")
    unlock = await unlock_for_mastered_drill(
        db, user_id=uuid.UUID(user_id), drill_id=uuid.UUID(riff)
    )
    assert unlock is not None and unlock.label == "Riff"

    states = {s.label: s for s in await section_states(
        db, user_id=uuid.UUID(user_id), song_id=song_id,
        breakdown=Breakdown.model_validate(_TWO_SECTION_BREAKDOWN),
    )}
    assert states["Riff"].cleared is True
    assert states["Intro"].cleared is False


async def test_maintenance_does_not_take_a_reward_back(db: AsyncSession):
    """§7.5 shelves a mastered drill on a 14-day clock. The passage stays earned.

    Nothing writes 'maintenance' today, so this is the latent case — and the
    reason it is pinned now is that the shelving job landing later would otherwise
    re-lock every cleared section silently, for doing nothing wrong.
    """
    assert "maintenance" in OWNED_STATES
    user_id = await _seed_user(db)
    song_id = await _seed_song(db, user_id, _TWO_SECTION_BREAKDOWN)
    drill = await _bank_drill(db, user_id, song_id, "intro-a", label="Intro")
    await _set_state(db, user_id, drill, "maintenance")

    states = await section_states(
        db, user_id=uuid.UUID(user_id), song_id=song_id,
        breakdown=Breakdown.model_validate(_TWO_SECTION_BREAKDOWN),
    )
    assert next(s for s in states if s.label == "Intro").cleared is True


async def test_an_active_drill_does_not_clear_its_section(db: AsyncSession):
    """Started is not finished — only the top of the ladder, held twice, counts."""
    user_id = await _seed_user(db)
    song_id = await _seed_song(db, user_id, _TWO_SECTION_BREAKDOWN)
    drill = await _bank_drill(db, user_id, song_id, "intro-a", label="Intro")
    await _set_state(db, user_id, drill, "active")

    states = await section_states(
        db, user_id=uuid.UUID(user_id), song_id=song_id,
        breakdown=Breakdown.model_validate(_TWO_SECTION_BREAKDOWN),
    )
    assert next(s for s in states if s.label == "Intro").cleared is False


async def test_a_section_no_drill_prepares_is_auto_cleared(db: AsyncSession):
    """Design §3 — a reward cannot be gated on drills that do not exist.

    This is also §5's whole backward-compat mechanism, so it is not an edge case
    bolted on: it is the rule the cached rows already run on.
    """
    user_id = await _seed_user(db)
    song_id = await _seed_song(db, user_id, _TWO_SECTION_BREAKDOWN)
    await _bank_drill(db, user_id, song_id, "riff-a", label="Riff", start=4, end=7)

    states = {s.label: s for s in await section_states(
        db, user_id=uuid.UUID(user_id), song_id=song_id,
        breakdown=Breakdown.model_validate(_TWO_SECTION_BREAKDOWN),
    )}
    assert states["Intro"].required_drills == 0
    assert states["Intro"].cleared is True
    assert states["Intro"].zero_required is True


async def test_a_retired_drill_cannot_block_a_section(db: AsyncSession):
    """§13 — a drill the player can never be served must not gate a reward."""
    user_id = await _seed_user(db)
    song_id = await _seed_song(db, user_id, _TWO_SECTION_BREAKDOWN)
    await _bank_drill(db, user_id, song_id, "intro-x", label="Intro", status="retired")

    states = await section_states(
        db, user_id=uuid.UUID(user_id), song_id=song_id,
        breakdown=Breakdown.model_validate(_TWO_SECTION_BREAKDOWN),
    )
    intro = next(s for s in states if s.label == "Intro")
    assert intro.required_drills == 0 and intro.cleared is True


async def test_a_drill_with_no_link_never_fires_an_unlock(db: AsyncSession):
    """Every drill banked before FLE-101. The implicit section was already cleared."""
    user_id = await _seed_user(db)
    song_id = await _seed_song(db, user_id, _raw())
    drill = await _bank_drill(db, user_id, song_id, "legacy", label=None)
    await _set_state(db, user_id, drill, "mastered")

    assert await unlock_for_mastered_drill(
        db, user_id=uuid.UUID(user_id), drill_id=uuid.UUID(drill)
    ) is None


async def test_a_link_to_a_label_the_breakdown_no_longer_emits_unlocks_nothing(
    db: AsyncSession,
):
    """Drills outlive the breakdown that spawned them (origin_song_id is SET NULL).

    A regenerated breakdown can segment the song differently, orphaning the old
    labels. Those drills gate nothing — they must not open a passage that is not
    in the tab the player is looking at.
    """
    user_id = await _seed_user(db)
    song_id = await _seed_song(db, user_id, _TWO_SECTION_BREAKDOWN)
    drill = await _bank_drill(db, user_id, song_id, "old", label="Chorus", start=0, end=1)
    await _set_state(db, user_id, drill, "mastered")

    assert await unlock_for_mastered_drill(
        db, user_id=uuid.UUID(user_id), drill_id=uuid.UUID(drill)
    ) is None


async def test_one_players_progress_does_not_unlock_anothers_section(db: AsyncSession):
    """The tally is per-user. Shared `drills` rows, separate `drill_progress`."""
    owner = await _seed_user(db)
    other = await _seed_user(db)
    song_id = await _seed_song(db, owner, _TWO_SECTION_BREAKDOWN)
    drill = await _bank_drill(db, owner, song_id, "intro-a", label="Intro")
    await _set_state(db, owner, drill, "mastered")

    states = {s.label: s for s in await section_states(
        db, user_id=uuid.UUID(other), song_id=song_id,
        breakdown=Breakdown.model_validate(_TWO_SECTION_BREAKDOWN),
    )}
    assert states["Intro"].cleared is False
    assert states["Intro"].mastered_drills == 0
