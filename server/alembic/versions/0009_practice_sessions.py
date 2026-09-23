"""session engine: practice_sessions + practice_session_items + drill_progress

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-23

FLE-9 Task 5 — the session object. Until now the app hands the user MATERIAL; it does
not run a SESSION. There is no row anywhere that says "this plan, these items, in this
order, and here is how far they got".

Three tables, plus an extension to drill_attempts:

1. practice_sessions       — one planned session. Lifecycle + telemetry.
2. practice_session_items  — the ordered plan. One row per item, written UP FRONT.
3. drill_progress          — the FLE-4 §7 tempo ladder, per (user, drill).
4. drill_attempts (+8 cols) — FLE-4 §13's full attempt record; 0006 shipped the stub.

The column set is the UNION of FLE-4 §13 (`sessions` / `session_items`) and the
FLE-21 "Pilot Session Telemetry" contract revision 2, which renamed them. Per FLE-21
R0 these are ONE pair of tables, not a pedagogy pair plus a telemetry pair. Renames
applied from FLE-4 §13: ordinal -> item_index, actual_seconds -> active_seconds,
item `pending` -> `not_reached`, session `complete` -> `completed`.

--- Three decisions worth reading before changing anything here ---

(a) NOT `user_sessions`. That table is the per-RATING log, and its partial-unique
    daily indexes (uq_user_sessions_daily_rating, uq_user_sessions_daily_reroll,
    uq_user_sessions_daily_pick) encode one-row-per-day semantics that are simply
    wrong for a session lifecycle. It stays exactly as it is and still serves the
    Today-card rating outside the player.

(b) EVERY item row is INSERTed at generation time with state = 'not_reached'.
    Rows are never created lazily on first view. This is the one expensive-to-retrofit
    decision in the whole feature (FLE-21 §2): FLE-13's readout asks both "where did
    they bail" and "which block do they skip", and those are only separable if an
    untouched item is on the record. If rows appeared only when touched, a skipped
    block and a never-reached block would both be absent and the second question
    could not be answered AT ALL, retroactively or otherwise. Cost: one extra INSERT
    per session. Benefit: 'skipped' means "the user actively passed on this" and
    'not_reached' means "the session ended before here".

(c) The open-session partial unique is
        (user_id, local_calendar_day) WHERE state IN ('planned', 'in_progress')
    and it DELIBERATELY differs from FLE-4 §13's `WHERE state != 'abandoned'`
    (FLE-21 §1). A COMPLETED session must not block a user who wants to practise
    twice in one day — a second deliberate session is a good day, not a telemetry
    anomaly. What the index does buy is that POST /api/v1/practice-sessions can be
    resolve-or-generate and a double-tap cannot produce two plans, enforced at the
    DB rather than in application code.

Not in this migration, named so it doesn't get lost: FLE-4 §8/§9 add `family`,
`tier`, `tier_raw_score` and `status` to `drills`. Migration 0006 shipped the bank
without them, so §11's band(f) filter cannot run yet. Those columns plus the §9.1
tier classifier and its backfill are their own increment — tier is a pure function
of columns `drills` already has, but `family` is a new 18-value taxonomy label and
needs a backfill decision, not a silent default.

downgrade() reverses in strict dependency order:
  drill_attempts columns -> practice_session_items -> practice_sessions
  -> drill_progress -> enum types
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


# Enum value lists, kept as module-level literals so tests can ast.literal_eval them
# without importing this module (see tests/test_alembic_0007.py for why importing a
# migration under pytest fails: server/alembic/ shadows the alembic distribution).
SESSION_MODE = ("BUILD", "BALANCED", "PERFORM")
SESSION_STATE = ("planned", "in_progress", "completed", "abandoned")
# NOTE: no 'superseded'. FLE-21 R7 deleted it — with resolve-or-generate plus the
# open-session partial unique, a second OPEN session for the same user and day can no
# longer be created, so there is nothing left to supersede.
SESSION_TERMINAL_REASON = ("user_completed", "day_rolled", "timeout")
SESSION_ITEM_BLOCK = ("warmup", "technique", "repertoire", "consolidation")
SESSION_ITEM_KIND = ("drill", "song_section", "song_play")
SESSION_ITEM_STATE = ("not_reached", "in_progress", "completed", "skipped")
ADVANCE_MODE = ("user_tap", "auto")
DRILL_PROGRESS_STATE = ("active", "maintenance", "mastered", "retired")
DRILL_ATTEMPT_OUTCOME = ("CLEAR", "HOLD", "MISS", "SKIP")

_NEW_ENUMS = {
    "session_mode": SESSION_MODE,
    "session_state": SESSION_STATE,
    "session_terminal_reason": SESSION_TERMINAL_REASON,
    "session_item_block": SESSION_ITEM_BLOCK,
    "session_item_kind": SESSION_ITEM_KIND,
    "session_item_state": SESSION_ITEM_STATE,
    "advance_mode": ADVANCE_MODE,
    "drill_progress_state": DRILL_PROGRESS_STATE,
    "drill_attempt_outcome": DRILL_ATTEMPT_OUTCOME,
}


def _enum(name: str) -> postgresql.ENUM:
    """Reference an enum created by the raw SQL below — never re-create it.

    Same pattern as migration 0006's RATING_LEVEL and the ORM's create_type=False.
    Letting create_table() emit CREATE TYPE would try twice for any enum used by two
    tables, and would leave the type behind on a failed partial upgrade.
    """
    return postgresql.ENUM(*_NEW_ENUMS[name], name=name, create_type=False)


# rating_level was created by migration 0003's raw SQL.
RATING_LEVEL = postgresql.ENUM(
    "not_my_tempo",
    "getting_closer",
    "thats_what_im_looking_for",
    name="rating_level",
    create_type=False,
)


def upgrade() -> None:
    # -----------------------------------------------------------------
    # Step 1: enum types. Created explicitly and idempotently so a re-run
    # after a partial failure does not trip over a type that already exists.
    # -----------------------------------------------------------------
    for name, values in _NEW_ENUMS.items():
        rendered = ", ".join(f"'{v}'" for v in values)
        op.execute(
            f"DO $$ BEGIN "
            f"CREATE TYPE {name} AS ENUM ({rendered}); "
            f"EXCEPTION WHEN duplicate_object THEN NULL; "
            f"END $$;"
        )

    # -----------------------------------------------------------------
    # Step 2: drill_progress — the FLE-4 §7 ladder, per (user, drill).
    #
    # Created BEFORE practice_session_items only for readability; there is no FK
    # between them. The generator reads this table to plan planned_bpm, and the
    # rating write path applies one transition per attempt (§7.2).
    #
    # §7.6 is a constraint on OTHER code, recorded here because this is where a
    # future reader looks: the nightly decay job (app/scheduler.py) moves
    # skill_nodes.mastery and must NOT touch rung_bpm. Mastery is the graph's
    # ESTIMATE of a skill and should get less confident with time; a rung is a
    # RECORD of a tempo the player actually held, twice. Decaying it would be the
    # app silently deciding you got worse at something it watched you do.
    # -----------------------------------------------------------------
    op.create_table(
        "drill_progress",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "drill_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("drills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rung_bpm",
            sa.Integer(),
            nullable=False,
            comment="Current working tempo. init = drill.start_bpm, always a multiple of 5.",
        ),
        sa.Column(
            "consecutive_clears", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "consecutive_misses", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_practiced_on", sa.Date(), nullable=True),
        sa.Column(
            "state",
            _enum("drill_progress_state"),
            nullable=False,
            server_default=sa.text("'active'::drill_progress_state"),
        ),
        sa.Column("mastered_at", sa.DateTime(timezone=True), nullable=True),
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
        # §7 — one ladder per (user, drill). This is the upsert target for the
        # rating write path; without it a lost race would fork a drill's ladder.
        sa.UniqueConstraint("user_id", "drill_id", name="uq_drill_progress_user_drill"),
        # §1 — every planned tempo is a multiple of 5, matching the skill graph's
        # tempo bins. A rung that isn't makes round_to_5() lossy on the next push.
        sa.CheckConstraint("rung_bpm % 5 = 0", name="ck_drill_progress_rung_multiple_of_5"),
        sa.CheckConstraint("rung_bpm BETWEEN 20 AND 400", name="ck_drill_progress_rung_range"),
        sa.CheckConstraint(
            "consecutive_clears >= 0 AND consecutive_misses >= 0 AND attempts >= 0",
            name="ck_drill_progress_counters_non_negative",
        ),
        # §7.5 — mastered_at is what arms the 14-day maintenance timer. A 'mastered'
        # row without it would never enter maintenance and the drill would be
        # excluded from selection forever.
        sa.CheckConstraint(
            "(state = 'mastered') = (mastered_at IS NOT NULL)",
            name="ck_drill_progress_mastered_at_pairs_state",
        ),
    )
    # §5.1(a) and §11.2's staleness term both scan a user's ladder by recency.
    op.create_index(
        "ix_drill_progress_user_last_practiced",
        "drill_progress",
        ["user_id", "last_practiced_on"],
    )
    # §5.2's candidate filter opens with `state in ('active','maintenance')`.
    op.create_index("ix_drill_progress_user_state", "drill_progress", ["user_id", "state"])

    # -----------------------------------------------------------------
    # Step 3: practice_sessions.
    #
    # generated_at and started_at are DELIBERATELY separate (FLE-21 §1). A session
    # generated and never opened is NOT an abandoned session — it is an ignored plan,
    # a different and also interesting number. Only started_at IS NOT NULL rows enter
    # the completion-rate denominator.
    # -----------------------------------------------------------------
    op.create_table(
        "practice_sessions",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "local_calendar_day",
            sa.Date(),
            nullable=False,
            comment="Server-computed from tz_offset_minutes per D-10. Never client-supplied.",
        ),
        sa.Column("tz_offset_minutes", sa.Integer(), nullable=False),
        # Inherited from the day's selector (FLE-4 §5.3) — the session does NOT run its
        # own song selection and does NOT re-roll. Nullable because a user with no
        # catalog match still gets a technique-only session rather than no session.
        sa.Column(
            "song_id", sa.Integer(), sa.ForeignKey("songs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "target_minutes",
            sa.Integer(),
            nullable=False,
            comment="From UserPreferences.session_length_min. FLE-4 §0: 15|30|45|60.",
        ),
        sa.Column("mode", _enum("session_mode"), nullable=False),
        sa.Column("allow_push", sa.Boolean(), nullable=False),
        # The two columns that make a session explicable after the fact. Any change to
        # a constant in app/sessions/plan.py is a generator_version bump — old sessions
        # have to stay readable against the rules that actually produced them.
        sa.Column("generator_version", sa.Text(), nullable=False),
        sa.Column("seed", sa.Text(), nullable=False),
        # mode_rule is not in either published field list. It is one short string and it
        # is the difference between "why did I get a PERFORM session" being answerable
        # from the row and requiring a replay of six ordered rules against history that
        # has since moved. Worth the 16 bytes.
        sa.Column(
            "mode_rule",
            sa.String(32),
            nullable=False,
            comment="FLE-4 §4 first-match-wins rule name: cold_start|layoff|bailing|"
            "song_nearly_ready|root_deficit|default.",
        ),
        sa.Column(
            "generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="First item entered. WRITE-ONCE (FLE-21 R5). Day-7 return is computed from this.",
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "state",
            _enum("session_state"),
            nullable=False,
            server_default=sa.text("'planned'::session_state"),
        ),
        sa.Column("terminal_reason", _enum("session_terminal_reason"), nullable=True),
        sa.Column("completion_ratio", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "elapsed_active_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Sum of item active_seconds. CLIENT-REPORTED — see FLE-21 §6.",
        ),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column(
            "last_item_index_reached",
            sa.Integer(),
            nullable=True,
            comment="Highest index entered. Monotonic: GREATEST(existing, index), never decreases.",
        ),
        sa.CheckConstraint(
            "target_minutes IN (15, 30, 45, 60)", name="ck_practice_sessions_target_minutes"
        ),
        sa.CheckConstraint("item_count > 0", name="ck_practice_sessions_item_count_positive"),
        sa.CheckConstraint(
            "completion_ratio IS NULL OR (completion_ratio >= 0 AND completion_ratio <= 1)",
            name="ck_practice_sessions_completion_ratio_range",
        ),
        sa.CheckConstraint(
            "elapsed_active_seconds >= 0", name="ck_practice_sessions_elapsed_non_negative"
        ),
        # A terminal state and its reason arrive together or not at all. Without this,
        # a row can be 'abandoned' with no reason, and FLE-13's "where did they bail"
        # loses the day_rolled/timeout split that tells a real abandonment from a
        # corrupt tz_offset_minutes (FLE-21 §3).
        sa.CheckConstraint(
            "(state IN ('completed', 'abandoned')) = (terminal_reason IS NOT NULL)",
            name="ck_practice_sessions_terminal_reason_pairs_state",
        ),
        sa.CheckConstraint(
            "(state IN ('completed', 'abandoned')) = (ended_at IS NOT NULL)",
            name="ck_practice_sessions_ended_at_pairs_state",
        ),
        # FLE-21 §5: POST does not set started_at; /start does. So 'planned' means
        # never opened, and every other state has been opened.
        sa.CheckConstraint(
            "(state = 'planned') OR (started_at IS NOT NULL) OR (state = 'abandoned')",
            name="ck_practice_sessions_started_at_when_active",
        ),
    )
    # The FLE-13 return-rate query: distinct local_calendar_day per user where started.
    op.create_index(
        "ix_practice_sessions_user_started", "practice_sessions", ["user_id", "started_at"]
    )
    # See decision (c) in the module docstring. This is what makes resolve-or-generate
    # safe against a double-tap at the DB level rather than in application code.
    op.execute(
        "CREATE UNIQUE INDEX uq_practice_sessions_open_day "
        "ON practice_sessions (user_id, local_calendar_day) "
        "WHERE state IN ('planned', 'in_progress')"
    )
    # The §3 hourly day-roll sweep scans exactly this set.
    op.execute(
        "CREATE INDEX ix_practice_sessions_in_progress "
        "ON practice_sessions (last_activity_at) "
        "WHERE state = 'in_progress'"
    )

    # -----------------------------------------------------------------
    # Step 4: practice_session_items — the plan, written up front (decision (b)).
    # -----------------------------------------------------------------
    op.create_table(
        "practice_session_items",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("practice_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_index", sa.Integer(), nullable=False, comment="0-based plan order."),
        sa.Column("block", _enum("session_item_block"), nullable=False),
        sa.Column("kind", _enum("session_item_kind"), nullable=False),
        # ON DELETE SET NULL, not CASCADE: deleting a drill from the bank must not
        # silently delete the telemetry proving the user practised it.
        sa.Column(
            "drill_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("drills.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "song_id", sa.Integer(), sa.ForeignKey("songs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "target_skill_node_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("skill_nodes.id", ondelete="SET NULL"),
            nullable=True,
            comment="Where a rating on this item writes mastery. Nadia's review finding #1: "
            "§13 had no mastery write path for bank drills.",
        ),
        sa.Column(
            "planned_seconds",
            sa.Integer(),
            nullable=False,
            comment="ADVISORY (FLE-4 §6). Never a hard cut — a drill cut mid-rep is worse "
            "than a session that runs four minutes long.",
        ),
        sa.Column("planned_bpm", sa.Integer(), nullable=True),
        sa.Column(
            "planned_reps",
            sa.Integer(),
            nullable=True,
            comment="The DENOMINATOR in FLE-4 §7.1's CLEAR test. Not drill.repetitions, "
            "which is only a ceiling.",
        ),
        sa.Column(
            "completed_reps",
            sa.Integer(),
            nullable=True,
            comment="Client-observed, unreconstructable after the fact. NEVER inferred from "
            "active_seconds — that inference is exactly the ladder inflation §7.1 "
            "exists to prevent. NULL on a rated drill classifies as HOLD, never CLEAR.",
        ),
        # Server-told per-item flags (FLE-10 R5). The client derives NONE of them: that
        # is what keeps FLE-4's versioning discipline alive after the trip to the device.
        # Re-deriving `skippable` on the client would also break FLE-21 §4.1 — the skip
        # readout must exclude unskippable items from its denominator, and it can only
        # do that because the flag is on the row.
        sa.Column("rated", sa.Boolean(), nullable=False),
        sa.Column("skippable", sa.Boolean(), nullable=False),
        sa.Column("click_enabled", sa.Boolean(), nullable=False),
        sa.Column("re_entry", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("repeat_ok", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "is_consolidation", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "state",
            _enum("session_item_state"),
            nullable=False,
            server_default=sa.text("'not_reached'::session_item_state"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "active_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Wall-clock while this is the active item. NOT paused on app-background "
            "or screen-lock (FLE-21 §6) — the product's core behaviour is a propped-up "
            "phone and both hands on a guitar. Clamped to 4x planned_seconds on write.",
        ),
        sa.Column("rating", RATING_LEVEL, nullable=True),
        sa.Column("advance_mode", _enum("advance_mode"), nullable=True),
        sa.UniqueConstraint("session_id", "item_index", name="uq_session_items_session_index"),
        sa.CheckConstraint("item_index >= 0", name="ck_session_items_index_non_negative"),
        sa.CheckConstraint("planned_seconds > 0", name="ck_session_items_planned_seconds_positive"),
        sa.CheckConstraint("active_seconds >= 0", name="ck_session_items_active_seconds"),
        sa.CheckConstraint(
            "planned_reps IS NULL OR planned_reps > 0", name="ck_session_items_planned_reps"
        ),
        sa.CheckConstraint(
            "completed_reps IS NULL OR completed_reps >= 0", name="ck_session_items_completed_reps"
        ),
        sa.CheckConstraint(
            "planned_bpm IS NULL OR planned_bpm BETWEEN 20 AND 400",
            name="ck_session_items_planned_bpm_range",
        ),
        # An item's referent is implied by its kind. Enforced here because a
        # song_section row with a NULL song_id renders as an empty card on the device
        # and there is nothing the player can do about it at runtime.
        #
        # Both are IS NOT NULL OR ... to survive the ON DELETE SET NULL above: once a
        # drill or song is deleted the referent legitimately goes NULL and the historical
        # row must stay valid. The CHECK therefore only constrains which COLUMN is used,
        # not that it is populated forever.
        sa.CheckConstraint(
            "(kind = 'drill' AND song_id IS NULL) OR "
            "(kind IN ('song_section', 'song_play') AND drill_id IS NULL)",
            name="ck_session_items_ref_matches_kind",
        ),
        # FLE-21 §5.1: a rating on an item with rated = false is a client bug, rejected
        # 422 at the API. This is the DB backstop — FLE-4 §5.4 is explicit that a rating
        # on the consolidation item "converts the win back into an assessment".
        sa.CheckConstraint(
            "rating IS NULL OR rated = true", name="ck_session_items_rating_requires_rated"
        ),
    )
    # The resume query: first non-terminal item of a session, in plan order.
    op.create_index(
        "ix_session_items_session_state",
        "practice_session_items",
        ["session_id", "state", "item_index"],
    )
    # FLE-13's "which block do they skip", grouped by block.
    op.execute(
        "CREATE INDEX ix_session_items_skipped_block "
        "ON practice_session_items (block) "
        "WHERE state = 'skipped'"
    )

    # -----------------------------------------------------------------
    # Step 5: drill_attempts gains the eight columns FLE-4 §13 specifies.
    #
    # 0006 shipped the stub (tempo_reached_bpm, reps_completed, rating). What was
    # missing is everything needed to AUDIT a ladder transition: the rung we asked
    # for, the reps we planned, whether it was a re-entry, and the outcome the
    # classifier actually assigned.
    #
    # All nullable — existing rows predate the session engine and there is no honest
    # value to backfill. A NULL outcome means "written before the classifier existed",
    # which is exactly what it should mean.
    # -----------------------------------------------------------------
    op.add_column(
        "drill_attempts",
        sa.Column(
            "session_item_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("practice_session_items.id", ondelete="SET NULL"),
            nullable=True,
            comment="The plan item this attempt came from. SET NULL so attempt history "
            "outlives the session, matching user_session_id's existing behaviour.",
        ),
    )
    op.add_column(
        "drill_attempts",
        sa.Column(
            "rung_bpm",
            sa.Integer(),
            nullable=True,
            comment="drill_progress.rung_bpm at attempt time — the tempo of RECORD.",
        ),
    )
    # Nadia's review ask, and it is one column for a real gap: on §7.3 re-entry
    # planned_bpm is rung_bpm - 5, so the two differ exactly when the player is coming
    # back from a layoff. The gap between the tempo we ASKED for and the tempo HELD is
    # what v2 stall detection reads, and it is unrecoverable if only one is stored.
    op.add_column(
        "drill_attempts",
        sa.Column(
            "planned_bpm",
            sa.Integer(),
            nullable=True,
            comment="The tempo the item actually asked for. Differs from rung_bpm on §7.3 re-entry.",
        ),
    )
    op.add_column("drill_attempts", sa.Column("planned_reps", sa.Integer(), nullable=True))
    op.add_column(
        "drill_attempts",
        sa.Column(
            "outcome",
            _enum("drill_attempt_outcome"),
            nullable=True,
            comment="§7.1, classified SERVER-side and stored so the classifier is auditable. "
            "Recomputing it later from (rating, reps) would silently rewrite history "
            "every time the rubric is retuned.",
        ),
    )
    op.add_column(
        "drill_attempts",
        sa.Column("re_entry", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("drill_attempts", sa.Column("duration_s", sa.Integer(), nullable=True))
    # Nadia's review finding #1: `deficit` is 100 of the ~180 points in the §11.2
    # selection score and it reads skill_nodes.mastery, but nothing in the spec moved
    # mastery when a BANK drill was rated. POST /api/v1/sessions cannot serve that path —
    # its drill branch is keyed on drill_index, a position in a SONG's breakdown, and a
    # bank drill has neither a song_id nor a positional index.
    op.add_column(
        "drill_attempts",
        sa.Column(
            "target_skill_node_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("skill_nodes.id", ondelete="SET NULL"),
            nullable=True,
            comment="The node this attempt's rating moves mastery on.",
        ),
    )
    op.create_check_constraint(
        "ck_drill_attempts_rung_bpm_range",
        "drill_attempts",
        "rung_bpm IS NULL OR rung_bpm BETWEEN 20 AND 400",
    )
    op.create_check_constraint(
        "ck_drill_attempts_planned_bpm_range",
        "drill_attempts",
        "planned_bpm IS NULL OR planned_bpm BETWEEN 20 AND 400",
    )
    op.create_check_constraint(
        "ck_drill_attempts_planned_reps",
        "drill_attempts",
        "planned_reps IS NULL OR planned_reps > 0",
    )


def downgrade() -> None:
    # Strict reverse order.

    # Step 5 reverse.
    op.drop_constraint("ck_drill_attempts_planned_reps", "drill_attempts", type_="check")
    op.drop_constraint("ck_drill_attempts_planned_bpm_range", "drill_attempts", type_="check")
    op.drop_constraint("ck_drill_attempts_rung_bpm_range", "drill_attempts", type_="check")
    for col in (
        "target_skill_node_id",
        "duration_s",
        "re_entry",
        "outcome",
        "planned_reps",
        "planned_bpm",
        "rung_bpm",
        "session_item_id",
    ):
        op.drop_column("drill_attempts", col)

    # Step 4 reverse (indexes drop with the table).
    op.drop_table("practice_session_items")

    # Step 3 reverse.
    op.drop_table("practice_sessions")

    # Step 2 reverse.
    op.drop_table("drill_progress")

    # Step 1 reverse. Types drop last — every column referencing them is gone by now.
    for name in _NEW_ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")
