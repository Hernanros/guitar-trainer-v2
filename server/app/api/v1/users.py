# server/app/api/v1/users.py
# User bootstrap + skill-graph endpoints (Phase 2, upgraded in 02-03).
# Pattern: follows server/app/api/v1/song_of_day.py — AsyncSession dep injection,
# Pydantic response models, HTTPException error handling.
# Phase 4 (04-03): verifier pipeline (_run_verifier_pipeline) added to _persist_bootstrap.
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Literal, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy import func as sqlfunc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal

from app.db.session import get_db
from app.models.db import SkillLevel, SkillNode, SkillNodeProposal, SkillNodeRejection, Song, SongSkill, User
from app.models.skill_node import (
    SkillNodeResponse,
    SonnetOnboardingOutput,
    SonnetSkillNodeProposal,
    SonnetSongProposal,
)
from app.models.user import SkillGraphResponse, UserBootstrapRequest, UserResponse
from app.ai.onboarding import AIParseError, FIXED_ROOTS, run_onboarding_parse
from app.ai.skill_dedupe import best_match, SCORE_AUTO_DEDUPE, SCORE_CURATOR_QUEUE
from app.ai.skill_verifier import AISkillVerifierError, run_skill_node_verify

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Soft cap on raw_input size — DoS mitigation (T-02-03-DOS).
_RAW_INPUT_MAX_CHARS_PER_CATEGORY = 10_000

# Fan-out cap: max Sonnet verifier calls per onboarding run (T-04-03-11).
# Proposals beyond this limit are queued as 'deferred_overflow' for curator triage.
_VERIFIER_FANOUT_CAP = 10

# Concurrency bound: max concurrent Sonnet verifier calls (T-04-03-11 semaphore).
_VERIFIER_SEMAPHORE_LIMIT = 5


def _check_raw_input_size(raw_input: dict) -> None:
    """Reject requests where any raw_input category exceeds the per-category char cap."""
    for key in ("can_play", "working_on", "aspirational"):
        v = raw_input.get(key, "") or ""
        if isinstance(v, str) and len(v) > _RAW_INPUT_MAX_CHARS_PER_CATEGORY:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"raw_input.{key} exceeds {_RAW_INPUT_MAX_CHARS_PER_CATEGORY}-char cap "
                    f"({len(v)} chars)."
                ),
            )


async def _run_onboarding_parse_bounded(
    raw_input: dict,
    user_id: UUID,
) -> SonnetOnboardingOutput:
    """Run run_onboarding_parse against a FRESH AsyncSession for governor bookkeeping.

    IMPORTANT: run_onboarding_parse is @governed(feature='onboarding', cap=None) — the
    decorator calls _insert_governor_call(db, ...) which commits on the passed session.
    Using the caller's SAVEPOINT-nested session would commit the SAVEPOINT prematurely
    and any subsequent db.execute() on that session raises
    "Can't operate on closed transaction inside context manager." (prod P0 2026-08-13).

    Mirrors the _bounded_verify precedent (Slice C fix for the same class of bug on
    run_skill_node_verify). Only the governor_calls INSERT+COMMIT runs on this fresh
    session; the returned SonnetOnboardingOutput is a plain Pydantic model that the
    caller then persists via its own SAVEPOINT-scoped session.

    NOTE: The caller MUST ensure the users row for user_id is durably committed BEFORE
    invoking this helper — otherwise the fresh session cannot satisfy the
    governor_calls.user_id FK to users.id (different connection, cannot see the
    caller's uncommitted outer transaction).
    """
    async with AsyncSessionLocal() as onboarding_gov_db:
        return await run_onboarding_parse(
            raw_input,
            db=onboarding_gov_db,
            user_id=user_id,
        )


# Category precedence for duplicate-song coalescing (see _coalesce_song_proposals).
# working_on = currently practicing (most actionable);
# aspirational = stretch goal; can_play = baseline knowledge.
# If a user says both working_on and aspirational for the same song, they are
# actively practicing it → working_on wins.
_CATEGORY_PRIORITY: dict[str, int] = {
    "working_on": 2,
    "aspirational": 1,
    "can_play": 0,
}


def _coalesce_song_proposals(
    songs: list[SonnetSongProposal],
) -> list[SonnetSongProposal]:
    """Dedupe Sonnet song proposals by (lower(title), lower(artist)).

    Prod bug 2026-08-16 (debug/onboarding-dup-song.md): Sonnet faithfully returned
    two proposals when the user mentioned the same song in both `working_on` and
    `aspirational` wizard sections. The songs INSERT loop then hit
    `songs_user_title_artist_uidx` (unique on `(user_id, lower(title), lower(artist))`,
    category NOT in the key) mid-transaction → full rollback → user got 0 songs and
    0 skill_nodes.

    Fix — model-layer coalesce (Sonnet output shape allows duplicates, DB says one
    row per user per title+artist; we normalize between them):

      - Group by (title.lower().strip(), artist.lower().strip()).
      - Pick the highest-priority category per _CATEGORY_PRIORITY
        (working_on > aspirational > can_play).
      - UNION every merged proposal's `skill_temp_ids` (preserve first-seen order,
        drop dupes) — no skill mapping is lost.
      - Preserve insertion order across distinct songs (first-mentioned wins position).

    The Sonnet prompt (server/app/ai/onboarding.py) carries a matching instruction
    as belt-and-suspenders, but this function is authoritative: even a compliant
    prompt-follower can drift under retry pressure or between prompt revisions.

    Returns a new list; does not mutate the input.
    """
    merged: dict[tuple[str, str], SonnetSongProposal] = {}
    merged_skill_ids: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []

    for prop in songs:
        key = (prop.title.lower().strip(), prop.artist.lower().strip())
        if key not in merged:
            # First sighting — keep title/artist casing from this proposal.
            # Metadata (genre/difficulty/bpm/key) also comes from the first-seen
            # proposal — Sonnet emits the same metadata across duplicates for a
            # given (title, artist), so the choice is equivalent. If a future
            # Sonnet drift returns divergent metadata across duplicates, first-seen
            # is a stable well-defined tiebreak.
            merged[key] = SonnetSongProposal(
                title=prop.title,
                artist=prop.artist,
                category=prop.category,
                skill_temp_ids=list(prop.skill_temp_ids),
                genre=prop.genre,
                difficulty=prop.difficulty,
                bpm=prop.bpm,
                key=prop.key,
            )
            merged_skill_ids[key] = list(prop.skill_temp_ids)
            order.append(key)
            continue

        existing = merged[key]
        # Category precedence: promote if incoming has higher priority.
        if _CATEGORY_PRIORITY.get(prop.category, -1) > _CATEGORY_PRIORITY.get(
            existing.category, -1
        ):
            logger.info(
                "Coalescing duplicate song '%s' by '%s': promoting category '%s' → '%s'.",
                existing.title, existing.artist, existing.category, prop.category,
            )
            existing.category = prop.category
        else:
            logger.info(
                "Coalescing duplicate song '%s' by '%s': keeping category '%s' (incoming '%s').",
                existing.title, existing.artist, existing.category, prop.category,
            )

        # UNION skill_temp_ids, preserve first-seen order, drop dupes.
        seen = set(merged_skill_ids[key])
        for tid in prop.skill_temp_ids:
            if tid not in seen:
                merged_skill_ids[key].append(tid)
                seen.add(tid)

    # Rebuild in insertion order with unioned skill_temp_ids.
    result: list[SonnetSongProposal] = []
    for key in order:
        song = merged[key]
        song.skill_temp_ids = merged_skill_ids[key]
        result.append(song)

    if len(result) < len(songs):
        logger.info(
            "Coalesced %d Sonnet song proposals down to %d unique (title, artist) rows.",
            len(songs), len(result),
        )
    return result


# ---------------------------------------------------------------------------
# Verifier pipeline (Phase 4 — D-09, D-10, D-13, D-14, T-04-03-11)
# ---------------------------------------------------------------------------

# Pipeline result markers stored on proposals as extra attrs (not Pydantic fields)
_REUSE_CANONICAL_ID_ATTR = "_reuse_canonical_id"  # str UUID of existing canonical to reuse
_DROPPED_ATTR = "_dropped"                          # True = drop this proposal (verdict='no')
_REJECTION_INFO_ATTR = "_rejection_info"            # dict for skill_node_rejections insert
_PROPOSAL_INFO_ATTR = "_proposal_info"             # dict for skill_node_proposals insert


async def _run_verifier_pipeline(
    db: AsyncSession,
    user_id: UUID,
    proposals: list[SonnetSkillNodeProposal],
) -> None:
    """Run the dedup + verifier pipeline for each sub/leaf proposal in the onboarding output.

    Operates on proposals IN-PLACE by attaching pipeline result markers as extra attrs.
    Root proposals (level='root') bypass this pipeline entirely (D-08 fixed roots).

    Pipeline per proposal (D-09, D-10, D-13, D-14):
      1. Query existing canonical skill_nodes (canonical_node_id = self.id, user_id IS NULL or
         shared canonical marker). For POC: treat nodes where canonical_node_id IS NOT NULL
         AND canonical_node_id = id as canonical. Also treats nodes with canonical_node_id = id
         as their own canonical (self-referential).
      2. For sub/leaf proposals, run dedupe_score against candidates at the same level:
         - score >= 85: auto-dedupe → mark proposal._reuse_canonical_id = existing_id
         - 70 <= score < 85: curator queue → insert skill_node_proposals, still insert user-scoped node
         - score < 70 or no candidates: run Sonnet verifier
           - AISkillVerifierError: treat as 'uncertain' → queue for curator
           - verdict 'yes': insert as new canonical (canonical_node_id = self.id)
           - verdict 'no': drop + insert skill_node_rejections
           - verdict 'uncertain': insert user-scoped node + skill_node_proposals
      3. Fan-out cap: first 10 verifier-eligible proposals run the verifier concurrently
         with asyncio.Semaphore(5). Remaining proposals queue as 'deferred_overflow'.

    All DB inserts for proposals/rejections happen HERE via db.execute(text(...)).
    The actual skill_nodes inserts happen in _persist_bootstrap after pipeline completes.

    Args:
        db: Active AsyncSession (within the SAVEPOINT from bootstrap_user/re_run_onboarding).
        user_id: UUID of the user being onboarded.
        proposals: List of SonnetSkillNodeProposal objects from SonnetOnboardingOutput.skill_graph.
    """
    # (a) Query existing canonical nodes for dedup reference.
    # For POC: canonical nodes are rows where canonical_node_id IS NOT NULL AND equals their own id.
    # On fresh install with no prior users, this returns empty — all proposals fall through to verifier.
    canonical_rows_result = await db.execute(
        text(
            "SELECT id, name, level FROM skill_nodes "
            "WHERE canonical_node_id IS NOT NULL AND canonical_node_id = id"
        )
    )
    canonical_rows = canonical_rows_result.mappings().all()

    # Build level-keyed candidate map: level → [(id, name), ...]
    canonical_by_level: dict[str, list[dict]] = {}
    for row in canonical_rows:
        lvl = str(row["level"])
        canonical_by_level.setdefault(lvl, []).append(
            {"id": str(row["id"]), "name": row["name"]}
        )

    # (b) Separate verifier-eligible proposals (score < 70 or no candidates) for fan-out cap.
    verifier_eligible: list[SonnetSkillNodeProposal] = []

    for prop in proposals:
        if prop.level == "root":
            # Roots always insert as-is (D-08 fixed taxonomy, never verified)
            continue

        level_candidates = canonical_by_level.get(prop.level, [])
        candidate_names = [c["name"] for c in level_candidates]

        match = best_match(prop.name, candidate_names)

        if match is not None and match[1] >= SCORE_AUTO_DEDUPE:
            # Auto-dedupe: reuse existing canonical (mark for downstream insert)
            matched_name, matched_score = match
            matched_id = next(
                c["id"] for c in level_candidates if c["name"] == matched_name
            )
            setattr(prop, _REUSE_CANONICAL_ID_ATTR, matched_id)
            logger.info(
                "Verifier pipeline: auto-dedupe '%s' → existing canonical '%s' (score=%d)",
                prop.name, matched_name, matched_score,
            )

        elif match is not None and match[1] >= SCORE_CURATOR_QUEUE:
            # Curator queue: near-duplicate — queue for review, still insert user-scoped node
            matched_name, matched_score = match
            matched_id = next(
                c["id"] for c in level_candidates if c["name"] == matched_name
            )
            setattr(prop, _PROPOSAL_INFO_ATTR, {
                "user_id": str(user_id),
                "proposed_name": prop.name,
                "fuzzy_score": matched_score,
                "status": "pending",
                "canonical_id": matched_id,
                "verifier_verdict": None,
                "verifier_reason": f"near-duplicate fuzzy score {matched_score} (auto-queued, not verified)",
            })
            logger.info(
                "Verifier pipeline: curator queue '%s' (score=%d vs '%s')",
                prop.name, matched_score, matched_name,
            )

        else:
            # Verifier eligible: score < 70 or no candidates
            verifier_eligible.append(prop)

    # (c) Fan-out cap: first 10 go to verifier; overflow queues as deferred_overflow.
    first_batch = verifier_eligible[:_VERIFIER_FANOUT_CAP]
    overflow = verifier_eligible[_VERIFIER_FANOUT_CAP:]

    for prop in overflow:
        # Queue as deferred_overflow — no user-scoped skill_nodes insert for overflow
        setattr(prop, _DROPPED_ATTR, True)
        setattr(prop, _PROPOSAL_INFO_ATTR, {
            "user_id": str(user_id),
            "proposed_name": prop.name,
            "fuzzy_score": 0,
            "status": "pending",
            "canonical_id": None,
            "verifier_verdict": "deferred_overflow",
            "verifier_reason": "verifier fan-out cap reached; curator to triage",
        })
        logger.info(
            "Verifier pipeline: deferred_overflow '%s' (fan-out cap reached)", prop.name
        )

    # (d) Run verifier on the first batch with asyncio.Semaphore(5) concurrency bound.
    semaphore = asyncio.Semaphore(_VERIFIER_SEMAPHORE_LIMIT)

    async def _bounded_verify(prop: SonnetSkillNodeProposal) -> None:
        """Run run_skill_node_verify for a single proposal within the semaphore.

        IMPORTANT: run_skill_node_verify is @governed(cap=None) — the decorator
        calls _insert_governor_call(db, ...) which commits on the passed session.
        Using the main 'db' (which is inside a SAVEPOINT) would commit the SAVEPOINT
        prematurely. Instead, we use a FRESH AsyncSession so the governor_calls
        INSERT+COMMIT runs on its own independent transaction.
        """
        level_candidates = canonical_by_level.get(prop.level, [])
        candidate_names = [c["name"] for c in level_candidates]

        async with semaphore:
            async with AsyncSessionLocal() as verifier_db:
                try:
                    verdict = await run_skill_node_verify(
                        prop.name,
                        candidate_names,
                        db=verifier_db,
                        user_id=user_id,
                    )
                except AISkillVerifierError as ve:
                    # D-14: verifier failure degrades gracefully — queue as 'uncertain'
                    logger.warning(
                        "Verifier pipeline: skill_verify failed for '%s' (%s) — queuing as uncertain.",
                        prop.name, ve,
                    )
                    setattr(prop, _PROPOSAL_INFO_ATTR, {
                        "user_id": str(user_id),
                        "proposed_name": prop.name,
                        "fuzzy_score": 0,
                        "status": "pending",
                        "canonical_id": None,
                        "verifier_verdict": "uncertain",
                        "verifier_reason": "verifier call failed, queued for manual review",
                    })
                    return

                # Route verdict (outside the except block — only reached on success)
                if verdict.verdict == "yes":
                    # New canonical: skill_nodes insert with canonical_node_id = self.id
                    # (handled in _persist_bootstrap; no markers set here)
                    logger.info(
                        "Verifier pipeline: '%s' → verdict=yes (new canonical under %s)",
                        prop.name, verdict.root,
                    )

                elif verdict.verdict == "no":
                    # Drop the proposal + record rejection
                    setattr(prop, _DROPPED_ATTR, True)
                    setattr(prop, _REJECTION_INFO_ATTR, {
                        "proposed_name": prop.name,
                        "reason": verdict.reason,
                        "verifier_response": verdict.model_dump(),
                    })
                    logger.info(
                        "Verifier pipeline: '%s' → verdict=no ('%s') — dropped.",
                        prop.name, verdict.reason,
                    )

                elif verdict.verdict == "uncertain":
                    # Insert user-scoped node + queue proposal for curator
                    setattr(prop, _PROPOSAL_INFO_ATTR, {
                        "user_id": str(user_id),
                        "proposed_name": prop.name,
                        "fuzzy_score": 0,
                        "status": "pending",
                        "canonical_id": None,
                        "verifier_verdict": "uncertain",
                        "verifier_reason": verdict.reason,
                    })
                    logger.info(
                        "Verifier pipeline: '%s' → verdict=uncertain ('%s') — queued.",
                        prop.name, verdict.reason,
                    )

    # Run first batch concurrently with semaphore
    await asyncio.gather(*[_bounded_verify(p) for p in first_batch])

    # (e) Flush proposal/rejection rows to DB now (within the SAVEPOINT).
    for prop in proposals:
        if prop.level == "root":
            continue

        rejection_info = getattr(prop, _REJECTION_INFO_ATTR, None)
        if rejection_info:
            await db.execute(
                text(
                    "INSERT INTO skill_node_rejections "
                    "  (id, proposed_name, reason, verifier_response, created_at) "
                    "VALUES "
                    "  (gen_random_uuid(), :name, :reason, :response, now())"
                ),
                {
                    "name": rejection_info["proposed_name"],
                    "reason": rejection_info["reason"],
                    "response": None,  # JSONB stored as NULL for simplicity at POC scale
                },
            )

        proposal_info = getattr(prop, _PROPOSAL_INFO_ATTR, None)
        if proposal_info:
            await db.execute(
                text(
                    "INSERT INTO skill_node_proposals "
                    "  (id, user_id, proposed_name, fuzzy_score, status, "
                    "   canonical_id, verifier_verdict, verifier_reason, created_at) "
                    "VALUES "
                    "  (gen_random_uuid(), :uid, :name, :score, :status, "
                    "   :canonical_id, :verdict, :reason, now())"
                ),
                {
                    "uid": proposal_info["user_id"],
                    "name": proposal_info["proposed_name"],
                    "score": proposal_info["fuzzy_score"],
                    "status": proposal_info["status"],
                    "canonical_id": proposal_info["canonical_id"],
                    "verdict": proposal_info["verifier_verdict"],
                    "reason": proposal_info["verifier_reason"],
                },
            )


async def _persist_bootstrap(
    db: AsyncSession,
    user_id: UUID,
    output: Optional[SonnetOnboardingOutput],
    *,
    mode: Literal["full", "bootstrap"],
) -> List[SkillNode]:
    """Persist Sonnet's structured output (mode='full') or the 6-root fallback (mode='bootstrap').

    parent_id FK is DEFERRABLE INITIALLY DEFERRED per migration 0002 — insertion order
    doesn't matter for FK correctness; PostgreSQL only checks the FK at COMMIT time.
    We still sort by level for readability, but this is not load-bearing.

    Phase 4 (Slice C): for mode='full', each sub/leaf proposal runs through the verifier
    pipeline BEFORE insert. The pipeline runs _run_verifier_pipeline which:
      - Marks proposals with _reuse_canonical_id (auto-dedupe) or _dropped (verdict='no')
        or _proposal_info (curator queue) as extra attrs.
    The insert loop below honors these markers.

    Returns the inserted SkillNode rows so the caller can build SkillGraphResponse.
    """
    if mode == "bootstrap":
        # 6-root fallback graph — D-07 fail-open path. No Sonnet output required.
        rows = [
            SkillNode(
                id=uuid4(),
                user_id=user_id,
                name=root,
                level="root",
                parent_id=None,
                tempo_bin_low=None,
                tempo_bin_high=None,
            )
            for root in FIXED_ROOTS
        ]
        for row in rows:
            db.add(row)
        await db.flush()
        return rows

    # mode="full" — persist Sonnet's structured DAG + songs + song_skills
    assert output is not None, "output required for mode='full'"

    # 1) Map Sonnet temp_ids to real UUIDs
    temp_to_uuid: dict[str, UUID] = {p.temp_id: uuid4() for p in output.skill_graph}

    # 2) Coerce invalid root names to closest known root (D-08 defense-in-depth).
    #    Recommendation from plan: coerce with warning (safer than rejection for POC).
    root_lookup = {r.lower(): r for r in FIXED_ROOTS}
    for p in output.skill_graph:
        if p.level == "root" and p.name not in FIXED_ROOTS:
            fallback = root_lookup.get(p.name.lower())
            if fallback:
                logger.warning(
                    "Sonnet returned non-canonical root '%s' — coerced to '%s'", p.name, fallback
                )
                p.name = fallback
            else:
                logger.warning(
                    "Sonnet returned unknown root '%s' — coerced to 'Music Theory'", p.name
                )
                p.name = "Music Theory"

    # 2.5) Phase 4 Slice C: run verifier pipeline on sub/leaf proposals.
    # This runs BEFORE the insert loop so pipeline markers are set before we decide
    # what to insert. Any AISkillVerifierError is caught inside _run_verifier_pipeline
    # per D-14 (graceful degradation — onboarding still succeeds).
    # AIParseError is NOT caught here — it propagates to the SAVEPOINT for fail-open.
    await _run_verifier_pipeline(db, user_id, output.skill_graph)

    # 2.6) Cascade-drop: any proposal whose parent was dropped by the verifier
    #      must ALSO be dropped, otherwise the skill_nodes insert loop below will
    #      create an orphan row (parent_id → a UUID that never gets inserted) and
    #      Postgres blows the DEFERRABLE FK fk_skill_nodes_parent at COMMIT time.
    #
    #      Prod bug 2026-08-17 (debug/onboarding-fk-orphan-leaves.md):
    #        Verifier dropped a sub ('leaf-open-chord-voicings-100' parent), but the
    #        sub's leaf children were still inserted with parent_id pointing to the
    #        dropped sub's temp_to_uuid entry. Orphan parent_id
    #        b564b549-47d8-4fb1-8c34-c5674dc1b4c4 tripped fk_skill_nodes_parent at
    #        commit → whole SAVEPOINT rolled back → re-run returned 500.
    #
    #      Fixed-point loop (while any pass adds new drops) so the cascade
    #      propagates through arbitrarily deep sub → sub → leaf trees. The current
    #      DAG is only 3 levels (root → sub → leaf) so one pass suffices, but the
    #      loop future-proofs against deeper trees without changing correctness.
    dropped_temp_ids: set[str] = {
        p.temp_id for p in output.skill_graph if getattr(p, _DROPPED_ATTR, False)
    }
    while True:
        newly_dropped: list[SonnetSkillNodeProposal] = []
        for prop in output.skill_graph:
            if getattr(prop, _DROPPED_ATTR, False):
                continue
            if prop.parent_temp_id and prop.parent_temp_id in dropped_temp_ids:
                setattr(prop, _DROPPED_ATTR, True)
                newly_dropped.append(prop)
                logger.warning(
                    "Cascade-dropping proposal '%s' (temp_id '%s') — "
                    "parent '%s' was dropped by verifier.",
                    prop.name, prop.temp_id, prop.parent_temp_id,
                )
        if not newly_dropped:
            break
        dropped_temp_ids.update(p.temp_id for p in newly_dropped)

    # 3) Insert skill_nodes. Sorted by level for readability; FK is deferrable so order
    #    doesn't matter for correctness (PostgreSQL checks FK only at COMMIT time per migration 0002).
    level_order = {"root": 0, "sub": 1, "leaf": 2}
    ordered = sorted(output.skill_graph, key=lambda p: level_order[p.level])

    for prop in ordered:
        # Honor pipeline drop marker (verdict='no' or deferred_overflow skips user-scoped insert)
        if getattr(prop, _DROPPED_ATTR, False):
            continue

        parent_uuid = temp_to_uuid.get(prop.parent_temp_id) if prop.parent_temp_id else None
        reuse_id = getattr(prop, _REUSE_CANONICAL_ID_ATTR, None)

        # Determine canonical_node_id:
        # - Reuse marker: use existing canonical's UUID
        # - Proposal with curator queue: canonical_node_id=NULL (user-scoped, not yet canonical)
        # - Verifier verdict='yes': canonical_node_id = self.id (this node IS the canonical)
        # - Root nodes: canonical_node_id=NULL (roots are per-user, not canonical in POC)
        if reuse_id:
            canonical_node_id = UUID(reuse_id)
        elif prop.level in ("sub", "leaf") and not getattr(prop, _PROPOSAL_INFO_ATTR, None):
            # Verifier said 'yes' (or no pipeline ran) → self-canonical
            canonical_node_id = temp_to_uuid[prop.temp_id]
        else:
            # Curator-queued, uncertain, or root → NULL
            canonical_node_id = None

        db.add(SkillNode(
            id=temp_to_uuid[prop.temp_id],
            user_id=user_id,
            name=prop.name,
            level=prop.level,
            parent_id=parent_uuid,
            tempo_bin_low=prop.tempo_bin_low,
            tempo_bin_high=prop.tempo_bin_high,
            canonical_node_id=canonical_node_id,
            # mastery defaults to 0.0 via server_default (D-11 deterministic-writes principle)
        ))

    await db.flush()   # make SkillNode IDs available for song_skills FK

    # 4) Insert songs
    #
    # Coalesce Sonnet duplicates before insert. `songs_user_title_artist_uidx`
    # (migration 0003) is UNIQUE on (user_id, lower(title), lower(artist)) — the
    # category column is NOT part of the key. A user who mentions the same song in
    # two wizard sections (e.g. working_on AND aspirational) would previously trigger
    # a UniqueViolationError mid-loop and roll back the entire SAVEPOINT (prod bug
    # 2026-08-16, see .planning/debug/onboarding-dup-song.md).
    coalesced_songs = _coalesce_song_proposals(output.songs)

    song_id_by_key: dict[tuple, int] = {}
    for song_prop in coalesced_songs:
        # Persist Sonnet-emitted metadata (Phase 4 hotfix 2026-08-16 —
        # see .planning/debug/song-of-day-nullable-metadata.md). Prior to this,
        # genre/difficulty/bpm/key were hardcoded None and GET /song-of-day 500'd
        # inside SongResponse.model_validate() because those API fields were required.
        # SonnetSongProposal now REQUIRES these fields (validated at tool_use time);
        # any onboarding output missing them raises AIParseError → D-07 fail-open.
        # SongResponse fields have also been loosened to Optional as a backstop for
        # non-Sonnet insert paths (admin tools, migrations, seed backfills).
        song = Song(
            title=song_prop.title,
            artist=song_prop.artist,
            genre=song_prop.genre,
            difficulty=song_prop.difficulty,
            bpm=song_prop.bpm,
            key=song_prop.key,
            # Placeholder breakdown intentional — Phase 3 breakdown selector fills this
            # on first user-requested breakdown. SongResponse.breakdown is now Optional,
            # so this row now serves via GET /song-of-day as breakdown_available=False.
            breakdown={"placeholder": "Phase 3 will populate breakdown"},
            user_id=user_id,
            category=song_prop.category,
        )
        db.add(song)
        await db.flush()
        song_id_by_key[
            (song_prop.title.lower().strip(), song_prop.artist.lower().strip())
        ] = song.id

    # 5) Insert song_skills junctions — iterate the coalesced list so a song that
    #    appeared in multiple wizard sections gets the UNION of all referenced
    #    skill_temp_ids (no skill mappings dropped by dedup).
    for song_prop in coalesced_songs:
        song_id = song_id_by_key.get(
            (song_prop.title.lower().strip(), song_prop.artist.lower().strip())
        )
        if song_id is None:
            continue
        for temp_id in song_prop.skill_temp_ids:
            # Skip song_skills for dropped proposals (their temp_id has no skill_nodes row)
            prop_for_temp = next(
                (p for p in output.skill_graph if p.temp_id == temp_id), None
            )
            if prop_for_temp and getattr(prop_for_temp, _DROPPED_ATTR, False):
                logger.warning(
                    "Skipping song_skill for dropped proposal temp_id '%s' in song '%s'.",
                    temp_id, song_prop.title,
                )
                continue

            skill_uuid = temp_to_uuid.get(temp_id)
            if skill_uuid is None:
                logger.warning(
                    "Sonnet referenced unknown temp_id '%s' in song '%s' — skipping.",
                    temp_id,
                    song_prop.title,
                )
                continue
            db.add(SongSkill(song_id=song_id, skill_node_id=skill_uuid))

    await db.flush()

    # Return the freshly-created skill_nodes for the response
    result = await db.execute(
        select(SkillNode).where(SkillNode.user_id == user_id)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/users", response_model=SkillGraphResponse, status_code=201)
async def bootstrap_user(
    body: UserBootstrapRequest,
    db: AsyncSession = Depends(get_db),
) -> SkillGraphResponse:
    """Bootstrap the user (D-04 + D-05 + D-06 + D-07):

    1. Idempotency guard (Revision D): if skill_nodes already exist for this user_id,
       short-circuit with mode='existing' — no Sonnet call, no writes.
    2. Upsert users row + preferences + raw_onboarding_text AND COMMIT — this must be
       durable before Step 3 because the @governed decorator on run_onboarding_parse
       (Step 3) opens a fresh AsyncSession for its governor_calls INSERT, which needs
       the users FK to be visible on another connection.
    3. Open SAVEPOINT (async with db.begin_nested()) around: Sonnet call +
       _persist_bootstrap(mode='full'). Sonnet call goes through
       _run_onboarding_parse_bounded (fresh session for @governed bookkeeping).
    4. On AIParseError: SAVEPOINT rolls back automatically; call
       _persist_bootstrap(mode='bootstrap') OUTSIDE the nested block to write 6-root fallback.
       Users row + preferences remain durably committed from Step 2 (fail-open preserved).
    5. Set onboarded_at on the user row.
    6. Single await db.commit() at end for graph writes.
    """
    _check_raw_input_size(body.raw_input)

    # ---- Idempotency guard (Revision D) ----
    existing_count: int = (await db.execute(
        select(sqlfunc.count()).select_from(SkillNode).where(
            SkillNode.user_id == body.user_id
        )
    )).scalar_one()

    if existing_count > 0:
        # User already bootstrapped. Do NOT call Sonnet. Return existing graph.
        user_row = (await db.execute(
            select(User).where(User.id == body.user_id)
        )).scalar_one_or_none()
        if user_row is None:
            # Extremely unusual: skill_nodes exist but user row is gone. Refuse.
            raise HTTPException(
                status_code=500,
                detail="Data inconsistency: skill_nodes exist without user row.",
            )
        rows = (await db.execute(
            select(SkillNode).where(SkillNode.user_id == body.user_id)
        )).scalars().all()
        return SkillGraphResponse(
            nodes=[SkillNodeResponse.model_validate(r) for r in rows],
            mode="existing",
        )

    # ---- Step 2: upsert user row AND COMMIT (parent transaction) ----
    # Why commit here (not deferred to Step 6): the @governed decorator on
    # run_onboarding_parse (Step 3) opens a FRESH AsyncSession for governor_calls
    # bookkeeping. That fresh session cannot see this session's uncommitted users
    # row, so the governor_calls.user_id FK would fail with ForeignKeyViolationError.
    # Committing here also preserves the fail-open contract: if Sonnet fails in
    # Step 3, the users row + preferences still survive.
    now = datetime.now(timezone.utc)
    upsert = pg_insert(User).values(
        id=body.user_id,
        preferences=body.preferences.model_dump(),
        raw_onboarding_text=body.raw_input,
        # onboarded_at set at Step 5 after graph writes succeed
    ).on_conflict_do_update(
        index_elements=["id"],
        set_={
            "preferences": body.preferences.model_dump(),
            "raw_onboarding_text": body.raw_input,
        },
    )
    await db.execute(upsert)
    await db.commit()

    # ---- Step 3 + 4: SAVEPOINT-guarded Sonnet call + full persist ----
    # run_onboarding_parse is invoked via _run_onboarding_parse_bounded which opens
    # a FRESH AsyncSession for the @governed decorator's governor_calls INSERT+COMMIT.
    # Passing the outer 'db' would commit the SAVEPOINT prematurely and make any
    # subsequent db.execute() raise "Can't operate on closed transaction"
    # (prod P0 2026-08-13). Only _persist_bootstrap uses the SAVEPOINT-scoped 'db'.
    mode: Literal["full", "bootstrap"] = "full"
    skill_rows: List[SkillNode]
    try:
        async with db.begin_nested():
            sonnet_output = await _run_onboarding_parse_bounded(
                body.raw_input,
                body.user_id,
            )
            skill_rows = await _persist_bootstrap(
                db, body.user_id, sonnet_output, mode="full"
            )
    except AIParseError as e:
        # SAVEPOINT auto-rolled-back. Users row + preferences already committed at Step 2.
        logger.warning(
            "Sonnet-backed bootstrap failed for user %s (%s) — falling back to 6-root graph.",
            body.user_id,
            e,
        )
        skill_rows = await _persist_bootstrap(db, body.user_id, None, mode="bootstrap")
        mode = "bootstrap"

    # ---- Step 5: mark onboarded_at ----
    await db.execute(
        pg_insert(User)
        .values(
            id=body.user_id,
            onboarded_at=now,
            preferences=body.preferences.model_dump(),
            raw_onboarding_text=body.raw_input,
        )
        .on_conflict_do_update(index_elements=["id"], set_={"onboarded_at": now})
    )

    # ---- Step 6: commit graph writes + onboarded_at ----
    await db.commit()

    return SkillGraphResponse(
        nodes=[SkillNodeResponse.model_validate(r) for r in skill_rows],
        mode=mode,
    )


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Return the user's preferences and onboarded_at timestamp.

    Returns 404 if the user has not been bootstrapped yet.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found.")
    return UserResponse(
        id=row.id,
        preferences=row.preferences,
        onboarded_at=row.onboarded_at.isoformat() if row.onboarded_at else None,
    )


@router.get("/users/{user_id}/skill-graph", response_model=SkillGraphResponse)
async def get_skill_graph(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> SkillGraphResponse:
    """Return the user's full skill graph (tree via parent_id, no separate edges list per D-09)."""
    user_row = (await db.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()
    if user_row is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

    rows = (await db.execute(
        select(SkillNode).where(SkillNode.user_id == user_id)
    )).scalars().all()
    return SkillGraphResponse(
        nodes=[SkillNodeResponse.model_validate(r) for r in rows],
        # get-endpoint always returns mode="full" — fail-open marker is set at bootstrap time only
        mode="full",
    )


@router.post("/users/{user_id}/re-run", response_model=SkillGraphResponse, status_code=200)
async def re_run_onboarding(
    user_id: UUID,
    body: UserBootstrapRequest,
    db: AsyncSession = Depends(get_db),
) -> SkillGraphResponse:
    """Settings re-run (per D-14 Claude's Discretion):

    Wipes user's songs + song_skills + skill_nodes; keeps users row + preferences
    (overwritten with new body); then re-runs the same SAVEPOINT fail-open bootstrap.

    Protection: refuses 403 if user_id is the system UUID (T-02-03-06 — seed data guard).
    """
    if body.user_id != user_id:
        raise HTTPException(
            status_code=400, detail="Body user_id must match path user_id."
        )

    if str(user_id) == "00000000-0000-0000-0000-000000000000":
        raise HTTPException(
            status_code=403,
            detail="Re-run against system user is forbidden (protects seed data).",
        )

    _check_raw_input_size(body.raw_input)

    # Verify user exists before wiping anything
    user_row = (await db.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()
    if user_row is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

    # Wipe order: song_skills first (FK to both songs + skill_nodes), then songs, then skill_nodes
    await db.execute(
        delete(SongSkill).where(
            SongSkill.song_id.in_(
                select(Song.id).where(Song.user_id == user_id)
            )
        )
    )
    await db.execute(delete(Song).where(Song.user_id == user_id))
    await db.execute(delete(SkillNode).where(SkillNode.user_id == user_id))

    # Update preferences + raw_input on the user row (parent tx)
    user_row.preferences = body.preferences.model_dump()
    user_row.raw_onboarding_text = body.raw_input

    # Commit the wipe + preference update BEFORE the SAVEPOINT for the same reason as
    # bootstrap_user Step 2: _run_onboarding_parse_bounded opens a fresh AsyncSession
    # for the @governed decorator, and the fresh session must see a durable users row.
    # (The users row already existed from a prior bootstrap so the FK is technically
    # satisfied even without this commit, but we still need to prevent the SAVEPOINT
    # from holding a lock across the AI call — committing here releases the lock.)
    await db.commit()

    # SAVEPOINT-guarded Sonnet + full persist (same pattern as bootstrap_user).
    # Same governor-vs-SAVEPOINT rule applies here: run_onboarding_parse must run
    # against a fresh AsyncSession via _run_onboarding_parse_bounded so the governor
    # decorator's commit doesn't close this SAVEPOINT.
    mode: Literal["full", "bootstrap"] = "full"
    skill_rows: List[SkillNode]
    try:
        async with db.begin_nested():
            sonnet_output = await _run_onboarding_parse_bounded(
                body.raw_input,
                user_id,
            )
            skill_rows = await _persist_bootstrap(
                db, user_id, sonnet_output, mode="full"
            )
    except AIParseError as e:
        logger.warning(
            "Sonnet-backed re-run failed for user %s (%s) — falling back to 6-root graph.",
            user_id,
            e,
        )
        skill_rows = await _persist_bootstrap(db, user_id, None, mode="bootstrap")
        mode = "bootstrap"

    user_row.onboarded_at = datetime.now(timezone.utc)
    await db.commit()

    return SkillGraphResponse(
        nodes=[SkillNodeResponse.model_validate(r) for r in skill_rows],
        mode=mode,
    )
