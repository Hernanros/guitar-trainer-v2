"""drills bank: drills + drill_attempts + drill_dedupe_queue

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-15

FLE-8 Task 3 — make drills first-class so a drill survives the song that spawned it.

Before this migration a drill's identity is (song_id, drill_index) into one song's
cached breakdown JSONB (migration 0005 hangs those two columns off user_sessions).
A drill nailed on Little Wing could not be handed to the user tomorrow when the
song is Kashmir. Three tables fix that:

1. drills             — the bank. UUID identity, nullable song provenance.
2. drill_attempts     — per-drill history: tempo actually reached, reps completed,
                        dates. Append-only. Substrate for v2 feedback.
3. drill_dedupe_queue — the 70-84 uncertain band from the rapidfuzz dedup pass
                        (D-09 thresholds, same as skill_node_proposals).

Dedup design (see .planning/quick/260915-01-drill-bank-schema/PLAN.md):
  - Scope is (user_id, skill_node_id). Similar names on different skills are not dups.
  - >= 85 reuse canonical · 70-84 queue · < 70 insert new canonical.
  - uq_drills_canonical_identity is the DB-level backstop for exact-after-normalization
    collisions; rapidfuzz covers the fuzzy band above it.
  - NO LLM in the write path (FLE-8 constraint) — the Sonnet verifier is not called
    inline; the queue is the async escape hatch.

Origin-provenance nullability (deliberate, see PLAN.md D1):
  origin_song_id is ON DELETE SET NULL so a drill genuinely outlives its song.
  There is intentionally NO both-or-neither CHECK on (origin_song_id,
  origin_drill_index): SET NULL cannot null origin_drill_index (not an FK column),
  so such a CHECK would make song deletion fail — a constraint fighting the very
  purpose of the table. After a song delete origin_drill_index is retained as
  historical trivia and is meaningless without its song.

downgrade() drops in strict reverse dependency order:
  drill_dedupe_queue -> drill_attempts -> drills
(indexes and constraints drop with their tables).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


# rating_level was created by migration 0003's raw SQL. Reference it without
# re-creating it — same pattern as the ORM's create_type=False.
RATING_LEVEL = postgresql.ENUM(
    "not_my_tempo",
    "getting_closer",
    "thats_what_im_looking_for",
    name="rating_level",
    create_type=False,
)


def upgrade() -> None:
    # -----------------------------------------------------------------
    # Table 1: drills — the bank.
    # -----------------------------------------------------------------
    op.create_table(
        "drills",
        sa.Column(
            "id",
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # --- identity / dedup ---
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "name_normalized",
            sa.String(255),
            nullable=False,
            comment="skill_dedupe.normalize(name) — lowercased, punctuation stripped. "
            "Dedup key component and the DB-level collision backstop.",
        ),
        sa.Column(
            "skill_node_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("skill_nodes.id", ondelete="CASCADE"),
            nullable=False,
            comment="The single skill this drill exercises — resolved from the "
            "breakdown's target_skill_temp_id. Dedup is scoped to this.",
        ),
        sa.Column(
            "canonical_drill_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("drills.id", ondelete="CASCADE"),
            nullable=True,
            comment="NULL = this row IS canonical. Set = collapsed duplicate pointing "
            "at its canonical. Mirrors skill_nodes.canonical_node_id.",
        ),
        sa.Column(
            "dedupe_score",
            sa.Integer(),
            nullable=True,
            comment="rapidfuzz token_set_ratio vs the canonical at write time. "
            "NULL for rows that were new proposals (< 70, no match).",
        ),
        # --- drill content (mirrors app.models.song.Drill) ---
        sa.Column("song_specific", sa.Boolean(), nullable=False),
        sa.Column("what", sa.Text(), nullable=False),
        sa.Column("tab_snippet", JSONB(), nullable=False),
        sa.Column("start_bpm", sa.Integer(), nullable=False),
        sa.Column("target_bpm", sa.Integer(), nullable=False),
        sa.Column("repetitions", sa.Integer(), nullable=False),
        sa.Column("success_criterion", sa.Text(), nullable=False),
        sa.Column("common_trap", sa.Text(), nullable=True),
        # --- provenance (nullable by design — the drill outlives the song) ---
        sa.Column(
            "origin_song_id",
            sa.Integer(),
            sa.ForeignKey("songs.id", ondelete="SET NULL"),
            nullable=True,
            comment="The song whose breakdown spawned this drill. SET NULL on song "
            "delete — the drill survives. Advisory provenance, not identity.",
        ),
        sa.Column(
            "origin_drill_index",
            sa.Integer(),
            nullable=True,
            comment="0-based index into that breakdown's drills list. Pairs with "
            "origin_song_id and with user_sessions.drill_index. Retained but "
            "meaningless once origin_song_id is nulled by a song delete.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # target_bpm > start_bpm — the DB mirror of Drill._target_bpm_above_start.
    # Defense in depth, same rationale as 0005's ck_drill_index_pairs_skill_node.
    op.create_check_constraint(
        "ck_drills_target_bpm_above_start",
        "drills",
        "target_bpm > start_bpm",
    )
    # A drill cannot be its own duplicate.
    op.create_check_constraint(
        "ck_drills_canonical_not_self",
        "drills",
        "canonical_drill_id IS NULL OR canonical_drill_id <> id",
    )
    # dedupe_score is a rapidfuzz ratio.
    op.create_check_constraint(
        "ck_drills_dedupe_score_range",
        "drills",
        "dedupe_score IS NULL OR (dedupe_score >= 0 AND dedupe_score <= 100)",
    )

    # DB-level dedup backstop: at most ONE canonical drill per
    # (user, skill, normalized name). Duplicate rows (canonical_drill_id NOT NULL)
    # are exempt so collapsed history can be retained.
    op.execute(
        "CREATE UNIQUE INDEX uq_drills_canonical_identity "
        "ON drills (user_id, skill_node_id, name_normalized) "
        "WHERE canonical_drill_id IS NULL"
    )
    # The query the bank exists to serve: "give me this user's drills for this skill."
    op.create_index("ix_drills_user_skill", "drills", ["user_id", "skill_node_id"])
    op.create_index("ix_drills_canonical", "drills", ["canonical_drill_id"])
    op.create_index("ix_drills_origin_song", "drills", ["origin_song_id"])

    # -----------------------------------------------------------------
    # Table 2: drill_attempts — per-drill history.
    #
    # Append-only. Deliberately NO one-per-day unique index: user_sessions
    # records the daily VERDICT (one per slot per day, uq_user_sessions_daily_rating);
    # this records WHAT HAPPENED. Three attempts at three tempos in one sitting
    # is real data and v2 feedback needs it.
    # -----------------------------------------------------------------
    op.create_table(
        "drill_attempts",
        sa.Column(
            "id",
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "drill_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("drills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_session_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("user_sessions.id", ondelete="SET NULL"),
            nullable=True,
            comment="The rating row this attempt came from, when it came from the "
            "daily loop. NULL for attempts logged outside a rated session.",
        ),
        sa.Column(
            "local_calendar_day",
            sa.Date(),
            nullable=False,
            comment="Client-local day, same convention as user_sessions — day "
            "bucketing must not drift across timezones.",
        ),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "tempo_reached_bpm",
            sa.Integer(),
            nullable=True,
            comment="Tempo the user ACTUALLY reached — not the drill's target_bpm. "
            "The thing a 3-choice rating throws away.",
        ),
        sa.Column("reps_completed", sa.Integer(), nullable=True),
        sa.Column(
            "rating",
            RATING_LEVEL,
            nullable=True,
            comment="Optional 3-choice verdict, reusing the existing rating_level enum.",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_check_constraint(
        "ck_drill_attempts_tempo_range",
        "drill_attempts",
        "tempo_reached_bpm IS NULL OR (tempo_reached_bpm >= 20 AND tempo_reached_bpm <= 400)",
    )
    op.create_check_constraint(
        "ck_drill_attempts_reps_nonneg",
        "drill_attempts",
        "reps_completed IS NULL OR reps_completed >= 0",
    )

    # "History for this drill, newest first" — the per-drill history read.
    op.execute(
        "CREATE INDEX ix_drill_attempts_drill_time "
        "ON drill_attempts (drill_id, attempted_at DESC)"
    )
    op.create_index(
        "ix_drill_attempts_user_day", "drill_attempts", ["user_id", "local_calendar_day"]
    )

    # -----------------------------------------------------------------
    # Table 3: drill_dedupe_queue — the 70-84 uncertain band.
    # Mirrors skill_node_proposals. No LLM runs in the write path; a row here
    # means "a human or an async job decides", and no drill row was written.
    # -----------------------------------------------------------------
    op.create_table(
        "drill_dedupe_queue",
        sa.Column(
            "id",
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("proposed_name", sa.String(255), nullable=False),
        sa.Column(
            "skill_node_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("skill_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_drill_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("drills.id", ondelete="CASCADE"),
            nullable=False,
            comment="The existing canonical drill this proposal might duplicate.",
        ),
        sa.Column("fuzzy_score", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "payload",
            JSONB(),
            nullable=False,
            comment="The full proposed Drill, so approval can insert it without "
            "regenerating — no Sonnet call on the resolve path either.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_check_constraint(
        "ck_drill_dedupe_queue_score_range",
        "drill_dedupe_queue",
        "fuzzy_score >= 0 AND fuzzy_score <= 100",
    )
    op.create_check_constraint(
        "ck_drill_dedupe_queue_status",
        "drill_dedupe_queue",
        "status IN ('pending', 'approved', 'rejected', 'merged')",
    )
    op.create_index(
        "ix_drill_dedupe_queue_pending",
        "drill_dedupe_queue",
        ["status", "created_at"],
    )


def downgrade() -> None:
    # Strict reverse dependency order. Indexes and CHECK constraints drop with
    # their tables, so they are not dropped individually.
    op.drop_table("drill_dedupe_queue")
    op.drop_table("drill_attempts")
    op.drop_table("drills")
