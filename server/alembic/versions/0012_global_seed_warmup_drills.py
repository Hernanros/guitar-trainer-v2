"""Global seed warm-up drills — FLE-4 §12.1 (FLE-61).

Makes `drills.user_id` nullable so the three hand-authored warm-up drills of §12.1
can exist, and inserts them. Until this migration, §5.1 rule (d) was unreachable
code: `select_warmup()` fell through to `fallback_slot1`, which served slot 1's own
drill as the warm-up. On 2026-10-04 that reached hardware — the device walk on FLE-10
rendered item 0 and item 1 as the same drill (`Hammer & Pull in the Bb Minor Box`,
one at 6 reps and one at 10), which reads as a broken product rather than as a
documented deviation.

THE DESIGN QUESTION THIS MIGRATION ANSWERS
------------------------------------------
`skill_node_id` was NOT NULL and FKs to `skill_nodes`, which is a PER-USER table.
A global drill therefore has no honest node to point at, and §13's data contract did
not say what it should point at. The three options were a sentinel node row, a
per-user copy of each seed, or a nullable column. This migration takes the third:

    user_id IS NULL  <=>  skill_node_id IS NULL        (ck_drills_global_has_no_skill_node)

A sentinel node would have to belong to SOME user — there is no global `skill_nodes`
row and no place to put one — and per-user copies would multiply the three rows by
the user table and reintroduce exactly the dedup problem §12.1 exempts them from.

The NULL here is SEMANTIC, not unknown: `drills.skill_node_id` means "whose weakness
this drill serves" (see the model docstring), and a seed warm-up serves nobody's
particular weakness — it is the universal ramp. The §8 axis a seed drill DOES carry
is `family`, which is what selection actually reads, and `ck_drills_global_is_seed_shaped`
below makes family/tier mandatory for a global row precisely so that axis is never
absent. The CHECK is what stops the NULL from drifting into meaning "unknown".

WHAT THE NULL BREAKS, AND WHAT IS DONE ABOUT IT
-----------------------------------------------
1. `uq_drills_canonical_identity (user_id, skill_node_id, name_normalized)`.
   Postgres indexes are NULLS DISTINCT by default, so with two NULLs in the key the
   index goes INERT for global rows: three inserts of "Chromatic spider, one string"
   would all succeed. It is recreated below with NULLS NOT DISTINCT, which restores
   the intended rule in the global case (a global drill's identity IS its normalized
   name) and changes nothing for per-user rows, whose two leading columns the CHECK
   keeps NOT NULL. Requires PG15+; local test DB is 16, prod is 18 (both verified).

2. §10 coverage counts. §12.1 excludes these rows from grading, and
   `app/sessions/coverage.py` graded the whole bank with `user_id IS NULL` meaning
   "all users". Both of its queries gained `AND user_id IS NOT NULL` in the same
   commit as this migration. Without that the §10.3 denominator would shift under a
   gate that is already being read as a pilot go/no-go.

3. Dedup. `app/ai/drill_dedupe.build_candidates` filters on an equality against a
   real `skill_node_id`, so a NULL-node row can never be scored against and can
   never be collapsed — §12.1's "never dedup-suppressed" holds by construction, not
   by a new branch. `ck_drills_global_is_seed_shaped` pins the other half
   (`canonical_drill_id IS NULL`) so no future write path can suppress one either.

4. `scripts/backfill_drill_taxonomy.py` INNER JOINs `skill_nodes` on
   `d.skill_node_id`, so it skips global rows. That is correct and deliberate: these
   three ship pre-classified and must not be reclassified by a rubric pass.

THE TIER COLUMN DEVIATES FROM §12.1, ON PURPOSE
-----------------------------------------------
§12.1's table says D1 for all three. The stored tier is D2 for all three, because
`tier` is defined by §9 as "computed by compute_tier() and STORED", and that is the
invariant the code relies on — `select.py::_tier_of()` recomputes an absent tier with
the same function, so a hand-overridden label would be a silent disagreement waiting
for the first backfill. What compute_tier() actually returns here:

    Chromatic spider   L1  raw  5  -> D2 unclamped, no clamp needed
    Open-chord cycle   C1  raw 12  -> D4 unclamped, D2 after the C1 range clamp
    Four subdivisions  M1  raw  7  -> D2, no clamp needed

(Raw scores above are post-FLE-75: that issue rescaled the §9.1 ladder_stretch
bucket after the FLE-72 span cap, which moved all three raw scores up — none
crossed a tier band, so only `tier_raw_score` needed a matching edit here, not
`tier`. Editing this file does not reach rows a prior `alembic upgrade head`
already inserted; FLE-75 shipped an explicit UPDATE for those alongside the
backfill run over the per-user bank, since `backfill_drill_taxonomy.py` INNER
JOINs `skill_nodes` and skips these global rows by design — see point 4 above.)

Two of the three are not reachable at D1 at all. §8 gives L1 ("Alternate picking &
speed") a D2 FLOOR, so (L1, D1) is not a viable cell — `coverage.viable_cells()` does
not contain it. And a six-string open chord scores simultaneity 4 on its own, which
puts any honest C1 cycle at raw >= 7, i.e. D2. So §12.1's D1 column predates §9.1's
rubric and §8's ranges rather than contradicting them on purpose. D2 is also the
better answer for the pilot, whose band is D2-D4.

`test_alembic_0012.py` asserts the stored (tier, tier_raw_score) still equals
compute_tier() over the stored snippet, so this claim cannot rot.

ORDER OF RULE (d) IS LOAD-BEARING AND THEREFORE FIXED
-----------------------------------------------------
§5.1 rule (d) picks `sorted(seed_drills, key=drill_id)[0]`, so the ids below are not
arbitrary: `5eed0001...` sorts first and the Chromatic spider is the default warm-up
for a player whose own bank cannot supply one. That is the most universal of the
three (one string, picking hand and fretting hand, no chord shapes assumed). A
family-aware tie-break in rule (d) would preload slot 1's mechanic better, but that
is a change to §5.1's written ladder and belongs to Miagi, not to this migration.

CONTENT PROVENANCE
------------------
FLE-36 (done) authored five warm-up drills; §12.1 specifies three. Reconciliation:
  - "Spider Walk (1-3-2-4)" + "Chromatic 1-2-3-4"  -> Chromatic spider, one string
  - "Single-String Pick Accuracy"                  -> One string, four subdivisions
  - "Open-Hand Reset"       dropped: off the guitar, no metronome, no tab. It cannot
                            be a `drills` row (tab_snippet, start_bpm and target_bpm
                            are all NOT NULL and target_bpm > start_bpm).
  - "Slow Major Scale, Named Out Loud"  dropped: T1/L4 material, not one of §12.1's
                            three. A candidate for the generated bank, not a global.
  - Open-chord cycle, clean ring: FLE-36 authored no chord drill, so §12.1's third
                            row had no prose to inherit. Written here.

Revision ID: 0012
Revises: 0011
"""
import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


# Fixed ids: rule (d) sorts on drill_id (see the header), and a stable id lets the
# rows be recognised across environments without matching on name.
SEED_IDS = {
    "spider": uuid.UUID("5eed0001-0000-4000-8000-000000000001"),
    "cycle": uuid.UUID("5eed0002-0000-4000-8000-000000000002"),
    "subs": uuid.UUID("5eed0003-0000-4000-8000-000000000003"),
}

STANDARD_TUNING = ["E", "A", "D", "G", "B", "e"]


def _note(string: int, fret: int, duration: str) -> dict:
    return {"string": string, "fret": fret, "duration": duration}


# One note per BEAT SLOT, rhythm carried by `duration`. That is the house idiom for
# tab_snippet — verified against the prod bank, e.g. "Hammer & Pull in the Bb Minor
# Box" is 8 eighth-note slots in one 4/4 measure. `notes` holds SIMULTANEOUS notes
# only, which is also why §9.1 reads its length as the simultaneity feature.
_SPIDER_TAB = {
    "tuning": STANDARD_TUNING,
    "measures": [
        {
            "time_signature": "4/4",
            # 1-3-2-4 fingering on frets 1-4, low E only, twice.
            "beats": [{"notes": [_note(6, f, "eighth")]} for f in (1, 3, 2, 4, 1, 3, 2, 4)],
        }
    ],
}

_OPEN_CHORDS = (
    ((6, 0), (5, 2), (4, 2), (3, 0), (2, 0), (1, 0)),  # Em
    ((5, 0), (4, 2), (3, 2), (2, 1), (1, 0)),          # Am
    ((5, 3), (4, 2), (3, 0), (2, 1), (1, 0)),          # C
    ((6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 3)),  # G
)
_CYCLE_TAB = {
    "tuning": STANDARD_TUNING,
    "measures": [
        {
            "time_signature": "4/4",
            # One chord per click: Em - Am - C - G.
            "beats": [
                {"notes": [_note(s, f, "quarter") for (s, f) in shape]}
                for shape in _OPEN_CHORDS
            ],
        }
    ],
}

_SUBS_TAB = {
    "tuning": STANDARD_TUNING,
    "measures": [
        # One bar each of halves, quarters, eighths, sixteenths — the four
        # subdivisions the name promises, all on A string fret 5 so the only
        # variable is the click.
        {"time_signature": "4/4", "beats": [{"notes": [_note(5, 5, d)]} for _ in range(c)]}
        for d, c in (("half", 2), ("quarter", 4), ("eighth", 8), ("sixteenth", 16))
    ],
}


# (tier, tier_raw_score) are the values compute_tier() returns for the snippet and
# bpms on the same row — NOT hand-picked labels. See the header, and
# test_alembic_0012.py::test_stored_tier_matches_compute_tier.
SEED_DRILLS = (
    {
        "id": SEED_IDS["spider"],
        "name": "Chromatic spider, one string",
        "name_normalized": "chromatic spider one string",
        "family": "L1",
        "tier": "D2",
        "tier_raw_score": 5,
        "start_bpm": 50,
        "target_bpm": 80,
        "repetitions": 8,
        "tab_snippet": _SPIDER_TAB,
        "what": (
            "Low E string only. Play frets 1-2-3-4 with fingers 1-2-3-4, but in the "
            "order 1-3-2-4, strictly alternating down-up. Keep each finger pressed "
            "until you actually need to lift it."
        ),
        "success_criterion": (
            "Eight clean eighth notes in a row at the click, no buzz, no finger "
            "lifting early."
        ),
        "common_trap": (
            "Racing the click to get the 3rd and 4th fingers out of the way. If a "
            "note buzzes or you fall behind, drop 5 BPM and repeat — this is a tempo "
            "floor test, not a speed test."
        ),
    },
    {
        "id": SEED_IDS["cycle"],
        "name": "Open-chord cycle, clean ring",
        "name_normalized": "openchord cycle clean ring",
        "family": "C1",
        "tier": "D2",
        "tier_raw_score": 12,
        "start_bpm": 50,
        "target_bpm": 75,
        "repetitions": 8,
        "tab_snippet": _CYCLE_TAB,
        "what": (
            "Em - Am - C - G, one chord per click, round and round. Strum once and "
            "let it ring for the full beat; the job is the change, not the strum."
        ),
        "success_criterion": (
            "Every string in every shape rings clean for the whole beat, and you "
            "arrive on the next chord on the click rather than after it."
        ),
        "common_trap": (
            "Landing the fingers one at a time. Set the whole shape in the air and "
            "drop it as one unit — if that costs you the tempo, drop 5 BPM."
        ),
    },
    {
        "id": SEED_IDS["subs"],
        "name": "One string, four subdivisions",
        "name_normalized": "one string four subdivisions",
        "family": "M1",
        "tier": "D2",
        "tier_raw_score": 7,
        "start_bpm": 60,
        "target_bpm": 90,
        "repetitions": 8,
        "tab_snippet": _SUBS_TAB,
        "what": (
            "A string, 5th fret, nothing else. One bar of half notes, one of "
            "quarters, one of eighths, one of sixteenths, then back down. Watch the "
            "pick, not the fretboard."
        ),
        "success_criterion": (
            "Each subdivision locks to the click and every note is equally loud — "
            "and the changeover between bars does not rush."
        ),
        "common_trap": (
            "The sixteenth bar getting louder and faster than the rest. The point is "
            "that all four bars feel like the same tempo."
        ),
    },
)

_INSERT_SQL = sa.text(
    """
    INSERT INTO drills (
        id, user_id, skill_node_id, name, name_normalized,
        canonical_drill_id, dedupe_score, song_specific,
        what, tab_snippet, start_bpm, target_bpm, repetitions,
        success_criterion, common_trap,
        origin_song_id, origin_drill_index,
        family, tier, tier_raw_score, status
    ) VALUES (
        CAST(:id AS uuid), NULL, NULL, :name, :name_normalized,
        NULL, NULL, false,
        :what, CAST(:tab_snippet AS jsonb), :start_bpm, :target_bpm, :repetitions,
        :success_criterion, :common_trap,
        NULL, NULL,
        CAST(:family AS technique_family), CAST(:tier AS drill_tier),
        :tier_raw_score, 'active'
    )
    ON CONFLICT (id) DO NOTHING
    """
)


def upgrade() -> None:
    # 1 — the two columns a global row cannot fill.
    op.alter_column("drills", "user_id", existing_type=sa.dialects.postgresql.UUID(), nullable=True)
    op.alter_column(
        "drills", "skill_node_id", existing_type=sa.dialects.postgresql.UUID(), nullable=True
    )

    # 2 — the NULL means "global", and only that. Pairing the two columns is what
    # stops a per-user row from losing its node, which would silently drop it out of
    # the candidate query (snapshot.py INNER JOINs skill_nodes) with no error.
    op.create_check_constraint(
        "ck_drills_global_has_no_skill_node",
        "drills",
        "(user_id IS NULL) = (skill_node_id IS NULL)",
    )
    # 3 — §12.1's four properties of a global row, as one constraint. `family`/`tier`
    # NOT NULL is the load-bearing half: DrillCandidate.family_key degrades to the
    # skill-node root and then to the node id, and a global row has neither, so a
    # family-less global would collapse to the literal key "node:None".
    op.create_check_constraint(
        "ck_drills_global_is_seed_shaped",
        "drills",
        "user_id IS NOT NULL OR ("
        "song_specific = false"
        " AND canonical_drill_id IS NULL"
        " AND origin_song_id IS NULL"
        " AND family IS NOT NULL"
        " AND tier IS NOT NULL)",
    )

    # 4 — NULLS NOT DISTINCT, or the index is inert for exactly the rows this
    # migration adds. See the header. No change for per-user rows: constraint (2)
    # keeps both leading columns NOT NULL whenever user_id is present.
    op.execute("DROP INDEX uq_drills_canonical_identity")
    op.execute(
        "CREATE UNIQUE INDEX uq_drills_canonical_identity "
        "ON drills (user_id, skill_node_id, name_normalized) NULLS NOT DISTINCT "
        "WHERE canonical_drill_id IS NULL"
    )

    # 5 — the rows themselves.
    for row in SEED_DRILLS:
        op.get_bind().execute(
            _INSERT_SQL,
            {**row, "id": str(row["id"]), "tab_snippet": json.dumps(row["tab_snippet"])},
        )


def downgrade() -> None:
    # Only the three ids this migration created. A different global row means someone
    # added one deliberately, and the NOT NULL restore below should fail loudly
    # rather than have this DELETE quietly take it out from under them.
    op.get_bind().execute(
        sa.text("DELETE FROM drills WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(i) for i in SEED_IDS.values()]},
    )

    op.execute("DROP INDEX uq_drills_canonical_identity")
    op.execute(
        "CREATE UNIQUE INDEX uq_drills_canonical_identity "
        "ON drills (user_id, skill_node_id, name_normalized) "
        "WHERE canonical_drill_id IS NULL"
    )

    op.drop_constraint("ck_drills_global_is_seed_shaped", "drills", type_="check")
    op.drop_constraint("ck_drills_global_has_no_skill_node", "drills", type_="check")

    op.alter_column(
        "drills", "skill_node_id", existing_type=sa.dialects.postgresql.UUID(), nullable=False
    )
    op.alter_column(
        "drills", "user_id", existing_type=sa.dialects.postgresql.UUID(), nullable=False
    )
