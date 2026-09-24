# server/app/models/practice_session.py
# Pydantic v2 request/response models for the practice-session lifecycle (FLE-21 §5).
#
# NAMING IS LOAD-BEARING (FLE-21 R10, FLE-10 R1). These are `PracticeSessionResponse`
# and `PracticeSessionItemResponse`, NOT `SessionResponse`. `SessionResponse` already
# exists in app/models/session.py for the Phase-3 ratings endpoint and is bound on the
# client at mobile/src/api/sessions.ts:26. Redefining it would collide in the generated
# `components['schemas']` and break `useSubmitRating` at the next `npm run codegen` —
# in a file nobody touched, which is the worst kind of break to diagnose.
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.session import RatingLiteral

SessionStateLiteral = Literal["planned", "in_progress", "completed", "abandoned"]
TerminalReasonLiteral = Literal["user_completed", "day_rolled", "timeout"]
ItemStateLiteral = Literal["not_reached", "in_progress", "completed", "skipped"]
AdvanceModeLiteral = Literal["user_tap", "auto"]


class PracticeSessionItemResponse(BaseModel):
    """One item of the plan, with whatever progress has been recorded against it.

    Every boolean here is SERVER-TOLD and the client derives none of them (FLE-10 R5).
    `skippable` in particular is stored rather than re-derived because FLE-21 §4.1
    needs it at readout time: `repertoire.item1` is unskippable BY CONSTRUCTION
    (FLE-4 §5.3), so its zero skip count is a property of the generator and must be
    excluded from the skip denominator rather than read as a participant preference.
    """

    item_index: int
    block: Literal["warmup", "technique", "repertoire", "consolidation"]
    kind: Literal["drill", "song_section", "song_play"]
    state: ItemStateLiteral

    drill_id: Optional[UUID] = None
    song_id: Optional[int] = None
    target_skill_node_id: Optional[UUID] = None

    planned_seconds: int
    planned_bpm: Optional[int] = None
    planned_reps: Optional[int] = None
    completed_reps: Optional[int] = None

    rated: bool
    skippable: bool
    click_enabled: bool
    re_entry: bool
    repeat_ok: bool
    is_consolidation: bool

    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    active_seconds: int = 0
    rating: Optional[RatingLiteral] = None
    advance_mode: Optional[AdvanceModeLiteral] = None


class PracticeSessionResponse(BaseModel):
    """The session header plus its ordered items.

    `generated_at` and `started_at` are both present and are NOT the same thing
    (FLE-21 §1): a session generated and never opened is an ignored plan, not an
    abandoned session, and only rows with `started_at` set enter the completion-rate
    denominator. A client that treats `generated_at` as "when practice began" would
    reintroduce exactly the conflation this record exists to prevent.
    """

    id: UUID
    user_id: UUID
    local_calendar_day: str
    tz_offset_minutes: int
    song_id: Optional[int] = None
    target_minutes: int
    mode: Literal["BUILD", "BALANCED", "PERFORM"]
    state: SessionStateLiteral
    terminal_reason: Optional[TerminalReasonLiteral] = None

    generated_at: str
    started_at: Optional[str] = None
    last_activity_at: str
    ended_at: Optional[str] = None

    completion_ratio: Optional[float] = None
    elapsed_active_seconds: int
    item_count: int
    last_item_index_reached: Optional[int] = None

    items: list[PracticeSessionItemResponse] = Field(default_factory=list)


class ItemCompleteRequest(BaseModel):
    """Body for `POST …/items/{index}/complete` (FLE-21 §5.1).

    `rating` is nullable and a null rating still lands `state = 'completed'` (R1).
    `active_seconds` is required — it is the honest half of FLE-13's "did the
    generated duration match the time they actually had", and a client that omits it
    turns that question back into self-report.
    """

    rating: Optional[RatingLiteral] = None
    active_seconds: int = Field(ge=0)
    completed_reps: Optional[int] = Field(default=None, ge=0)
    advance_mode: Optional[AdvanceModeLiteral] = None


class ItemSkipRequest(BaseModel):
    """Body for `POST …/items/{index}/skip`.

    No `rating` field, by design — a skip is the user declining the item, and a
    verdict on something they did not do is not a thing the readout can use.
    """

    active_seconds: int = Field(default=0, ge=0)
    completed_reps: Optional[int] = Field(default=None, ge=0)


class ItemEventResponse(BaseModel):
    """What an item transition returns: the item's new state and the session header.

    `applied` is false when FLE-21 §5.3's lattice rejected a backwards move — a late
    `enter` landing on an item the user already finished. The call still answers 200
    because the write is idempotent rather than failed, and the player's outbox must
    be able to tell "delivered" from "retry me" without parsing prose.
    """

    item_index: int
    state: ItemStateLiteral
    applied: bool
    active_seconds: int
    clamped: bool = Field(
        default=False,
        description=(
            "True when active_seconds was clamped to 4x planned_seconds (FLE-21 §6). "
            "Clamped items are excluded from the readout's duration comparison."
        ),
    )
    session: PracticeSessionResponse


class SessionCompleteResponse(BaseModel):
    """Body for `POST …/{id}/complete` (FLE-10 R8).

    All four numbers are returned because the summary screen needs them and because
    the readout should never have to re-derive a ratio from counts it did not see.
    `done_items + skipped_items` does NOT necessarily equal `planned_items`: the
    remainder is `not_reached`, and keeping that gap visible is the whole point of
    FLE-21 §2 writing every item row at generation time.
    """

    session: PracticeSessionResponse
    completion_ratio: Optional[float] = None
    done_items: int
    skipped_items: int
    planned_items: int
