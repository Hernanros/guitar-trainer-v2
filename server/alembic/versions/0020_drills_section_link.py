"""drills: the FLE-101 §2 section link — prepares_section, section_start_beat, section_end_beat

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-21

FLE-101, the engineering half of the drill → section graduation loop (design:
FLE-100 `design` doc rev. 3, approved 2026-10-21).

WHAT THIS FIXES
---------------
FLE-89's confirmed root cause: nothing in the schema records WHICH passage of a
song a drill prepares. The generator prompt has claimed since FLE-99 that every
drill names its passage, and the NECK REGION rule has the model reasoning about
that passage's fret span — but the answer was never emitted and never stored.
Finishing a drill moved a hidden mastery float and nothing unlocked. These three
columns are where the answer lands.

NULLABLE, AND NULL IS THE NORMAL CASE
-------------------------------------
Every drill in the bank today predates this (prod: 12 banked rows, 0 with a
link). Design §5 defines a drill with no `prepares_section` as belonging to one
implicit, already-cleared whole-song section, so a NULL link is not a hole — it
is the pre-FLE-101 behaviour, preserved exactly. No backfill runs here and none
is scheduled: the link is a property of the breakdown that spawned the drill, and
the 10 cached breakdowns have no sections to link to. They acquire both together
when a breakdown is next regenerated under the new prompt.

That is also why there is no DEFAULT. A default would put an invented passage
name on every existing row, and §3 gates a reward on these values.

THE CHECK CONSTRAINT
--------------------
ck_drills_section_link_complete — all three columns present, or all three NULL.
A half-written link is the one state §3 cannot answer: `prepares_section` alone
says a section is required for graduation while the beat range that would let the
player SEE the passage is missing, so the reward unlocks onto nothing. The
Pydantic layer already clears all three together
(`Breakdown._link_drills_to_sections`); this is the DB-level backstop for the
other writer, scripts/backfill_drills.py.

Deliberately NOT enforced here: that `section_end_beat >= section_start_beat`,
and that the range sits inside the named section. The first is cheap but the
second needs the origin song's breakdown JSONB, so splitting them would put half
a rule in the schema and imply the other half was checked too. Both live together
in `Breakdown._link_drills_to_sections`, which repairs rather than rejects.

String(80) on prepares_section: §1's controlled vocabulary tops out around
"Pre-Chorus" plus a disambiguator ("Solo (bars 5-8)"). 80 is room to spare for a
display-only label, and bounded because it is a key the clear predicate groups on.

INDEX
-----
ix_drills_section_link is the §3 predicate's query — "every active drill of this
origin song that prepares section X" — and is partial on the link being present
because the NULL rows are exactly the ones that predicate never looks at.

downgrade() drops the index, the constraint, then the columns.
"""

from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "drills",
        sa.Column(
            "prepares_section",
            sa.String(length=80),
            nullable=True,
            comment=(
                "FLE-101 §2 — Section.label from the ORIGIN song's breakdown. "
                "Provenance, copied at promotion like origin_song_id; not a claim "
                "that travels with a deduped canonical row."
            ),
        ),
    )
    op.add_column(
        "drills",
        sa.Column(
            "section_start_beat",
            sa.Integer(),
            nullable=True,
            comment="First beat column of the passage this drill isolates, inclusive.",
        ),
    )
    op.add_column(
        "drills",
        sa.Column(
            "section_end_beat",
            sa.Integer(),
            nullable=True,
            comment="Last beat column of that passage, inclusive.",
        ),
    )
    op.create_check_constraint(
        "ck_drills_section_link_complete",
        "drills",
        "(prepares_section IS NULL AND section_start_beat IS NULL "
        " AND section_end_beat IS NULL) "
        "OR (prepares_section IS NOT NULL AND section_start_beat IS NOT NULL "
        " AND section_end_beat IS NOT NULL)",
    )
    op.create_index(
        "ix_drills_section_link",
        "drills",
        ["origin_song_id", "prepares_section"],
        postgresql_where=sa.text("prepares_section IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_drills_section_link", table_name="drills")
    op.drop_constraint("ck_drills_section_link_complete", "drills", type_="check")
    op.drop_column("drills", "section_end_beat")
    op.drop_column("drills", "section_start_beat")
    op.drop_column("drills", "prepares_section")
