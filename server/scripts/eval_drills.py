"""eval_drills.py — Live-Anthropic drill-quality diagnostic.

Phase 4.1 Plan 04.1-05 Task 1: Standalone async script that calls run_technique_breakdown
against 5 hand-picked songs (spanning tuning + genre diversity) and prints reviewer-friendly
drill JSON for manual landmine gate-checking.

USAGE (from the server/ directory):
    cd server
    ANTHROPIC_EVAL_RUN=1 \\
    ANTHROPIC_API_KEY=sk-ant-... \\
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/guitar_trainer \\
    python -m scripts.eval_drills

Or via the pytest wrapper (skip-by-default, requires ANTHROPIC_EVAL_RUN=1):
    ANTHROPIC_EVAL_RUN=1 ANTHROPIC_API_KEY=... DATABASE_URL=... \\
    python -m pytest tests/test_drill_eval_live.py -v -s

DO NOT add this script to CI — the ANTHROPIC_EVAL_RUN=1 gate + pytest.mark.skipif in
test_drill_eval_live.py are the CI guard. Live tokens are intentionally NOT burned in CI.

W5 FIX — DEV-DB REQUIRED (Postgres only, no non-Postgres fallback):
The @governed decorator writes to governor_calls using Postgres-specific SQL (uses
now(), enum types, jsonb). A non-Postgres fallback would silently mask governor_calls
schema drift and produce misleading eval output. This script requires a real Postgres
dev DB. If DATABASE_URL is missing or not a postgres:// URL, the script exits with an
error message and instructions to start the dev DB.

N1 CAVEAT — L2 (hallucinated target_skill_temp_id) coverage is LIMITED in this eval:
The target_skills list is synthesized ad-hoc with fake UUIDs (uuid4(), not backed by
real skill_nodes rows in a user's graph). Sonnet CAN still hallucinate a new UUID not
in the fake list, and the eval WILL catch that. However, the eval CANNOT verify that
the fake UUID corresponds to a real skill_node owned by a real user. Full L2 coverage
requires Task 3 device verification step o: query user_sessions after a real drill
rating and validate target_skill_node_id resolves to a real skill_nodes row.

6 LANDMINES TO CHECK PER SONG (from RESEARCH.md landmines catalog):
    L1: tab_snippet is a canonical isolated shape (NOT a slice of the main song tab)
    L2: target_skill_temp_id echoes one of the provided fake UUIDs (LIMITED per N1)
    L3: drill count in [2, 4] — if drills=[], Landmine #3 soft-fail fired
    L4: song_specific=true iff `what` copy explicitly names the song
    L5: drills emitted in non-decreasing mechanic_tier order (FLE-45 — replaced
        the five-dimension dominance rule, which was unsatisfiable)
    L6: tab_snippet.measures.length in {1, 2}
    A1: `what` and tab_snippet describe the same exercise — prose promising a
        chord/barre/voicing/strum must have a snippet that sounds 2+ strings
        together (promoted to a counted gate by FLE-45)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

# ---------------------------------------------------------------------------
# Eval song catalogue (5 songs spanning tuning + genre diversity).
# Defined at module level so `from scripts.eval_drills import EVAL_SONGS` works
# for testing without triggering the live-run guards (which live in run_eval()).
# ---------------------------------------------------------------------------

EVAL_SONGS = [
    {
        "title": "Lenny",
        "artist": "Stevie Ray Vaughan",
        "target_skill_names": ["b3-3 slide E-shape", "chord-melody attack", "E-shape barre voicings"],
    },
    {
        "title": "Kashmir",
        "artist": "Led Zeppelin",
        "target_skill_names": ["DADGAD sus4 voicings", "cross-rhythm strumming"],
    },
    {
        "title": "Little Wing",
        "artist": "Jimi Hendrix",
        "target_skill_names": ["thumb-over bass notes", "chord-melody arpeggios"],
    },
    {
        "title": "Beat It",
        "artist": "Michael Jackson",
        "target_skill_names": ["palm-muted eighth notes", "power-chord riff"],
    },
    {
        "title": "Wonderwall",
        "artist": "Oasis",
        "target_skill_names": ["G-Em-D-A7sus4 chord changes", "downstroke pattern"],
    },
]

# ---------------------------------------------------------------------------
# Ensure server/ is on sys.path for app imports (mirrors conftest.py pattern)
# ---------------------------------------------------------------------------

_server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _server_dir not in sys.path:
    sys.path.insert(0, _server_dir)

# ---------------------------------------------------------------------------
# W5 FIX: Postgres-only guard — called from run_eval() before any DB imports.
# Running the guard inside run_eval() (not at module top) lets pytest collect
# this module and import EVAL_SONGS without ANTHROPIC_API_KEY being set.
# The guard uses sys.exit() so it terminates cleanly when called from __main__.
# ---------------------------------------------------------------------------

def _check_env_and_normalise_db_url() -> str:
    """Validate env vars and return a normalised postgresql+asyncpg:// URL.

    W5 FIX: Exits with a clear error if ANTHROPIC_API_KEY or DATABASE_URL is
    missing or not a postgres:// URL. Non-Postgres DBs are NOT supported.

    Returns the normalised DATABASE_URL string (caller sets os.environ["DATABASE_URL"]).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "Set it before running the live eval:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  ANTHROPIC_EVAL_RUN=1 python -m scripts.eval_drills",
            file=sys.stderr,
        )
        sys.exit(1)

    raw_db_url = os.environ.get("DATABASE_URL") or os.environ.get("DEV_DATABASE_URL", "")
    if not raw_db_url or not raw_db_url.startswith(
        ("postgresql://", "postgresql+asyncpg://", "postgres://")
    ):
        print(
            "ERROR: eval_drills requires a real Postgres dev database.\n"
            "Set DATABASE_URL (or DEV_DATABASE_URL) to a postgresql:// URL.\n"
            "\n"
            "Local dev DB startup:\n"
            "  cd server && docker compose up -d postgres\n"
            "  export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/guitar_trainer\n"
            "  alembic upgrade head\n"
            "\n"
            "Non-Postgres DBs are NOT supported — Postgres-specific SQL in @governed + Alembic schema.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Normalise to asyncpg scheme so SQLAlchemy uses the right async driver.
    if raw_db_url.startswith("postgres://"):
        return raw_db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    if raw_db_url.startswith("postgresql://") and "+asyncpg" not in raw_db_url:
        return raw_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw_db_url

# ---------------------------------------------------------------------------
# Reviewer-facing output helpers
# ---------------------------------------------------------------------------

_SEPARATOR = "=" * 80
_SUBSEP = "-" * 60

# EVAL FIX (2026-09-15): timeout override.
#
# run_technique_breakdown defaults to timeout_seconds=30.0, doubled to 60.0 on
# the single retry. A measured full breakdown (Lenny, 8 measures + 4 drills)
# takes ~76s wall-clock, so BOTH attempts time out and every song fails with
# AIBreakdownError before emitting a single drill — which is exactly what the
# first eval run produced (5/5 TimeoutError, 453.3s ≈ 5 × (30+60)).
#
# The eval widens the timeout so drill QUALITY can be graded at all. This is a
# measurement-harness setting and does NOT fix the underlying production
# default, which is too tight for this call — see the results doc.
_EVAL_TIMEOUT_SECONDS = float(os.environ.get("EVAL_TIMEOUT_SECONDS", "300"))


def _print_song_header(idx: int, song: dict) -> None:
    print(f"\n{_SEPARATOR}")
    print(f"SONG {idx + 1}/5: {song['title']} — {song['artist']}")
    print(_SEPARATOR)


def _render_measures(measures) -> list[str]:
    """Render measures as compact 'm1: [6/3 5/5] [4/7]' lines.

    Each beat becomes a bracket group; each note inside is string/fret.
    Used to make L1 (snippet must NOT be a slice of the main tab) gradeable
    by eye — you can only judge "is this a slice" if you can see BOTH.
    """
    lines = []
    for m_idx, measure in enumerate(measures):
        beats = []
        for beat in measure.beats:
            notes = " ".join(f"{n.string}/{n.fret}" for n in beat.notes)
            beats.append(f"[{notes}]")
        lines.append(f"m{m_idx + 1} ({measure.time_signature}): " + " ".join(beats))
    return lines


def _print_main_tab(breakdown) -> None:
    """Print the MAIN song tab.

    EVAL FIX (2026-09-15): the original script printed only drill tab_snippets.
    L1 — 'tab_snippet is a canonical isolated shape, NOT a slice of the main
    song tab' — is the single most likely quality gap per RESEARCH.md, and it
    is impossible to grade honestly without seeing the main tab to compare
    against. Printing it here makes L1 a real gate instead of a guess.
    """
    tab = breakdown.tab
    print(f"\n  MAIN SONG TAB — {len(tab.measures)} measure(s), tuning={tab.tuning}")
    for line in _render_measures(tab.measures):
        print(f"    {line}")


# FLE-45: mirrors the MECHANIC TIERS list in breakdown.SYSTEM_PROMPT. Display
# only — grade_eval.py reads the integer, never these strings.
_TIER_LABELS = {
    1: "single sustained note",
    2: "two-string alternation",
    3: "held shape struck",
    4: "shape change",
    5: "shape change with position shift",
    6: "polyphonic independence",
}


def _print_drill(drill_idx: int, drill, target_skills: list[dict]) -> None:
    """Print one drill in reviewer-friendly format."""
    print(f"\n  DRILL {drill_idx + 1}: {drill.name}")
    print(f"    song_specific : {drill.song_specific}")

    # Resolve the fake target_skill_temp_id back to a skill name for human readers
    matched_name = next(
        (s["name"] for s in target_skills if s["id"] == drill.target_skill_temp_id),
        "<NOT IN PROVIDED LIST — possible L2 hallucination>",
    )
    print(f"    target_skill  : {matched_name!r} (id={drill.target_skill_temp_id})")
    print(f"    tempo         : {drill.start_bpm} → {drill.target_bpm} BPM")
    # FLE-45: the declared MECHANIC TIERS position. grade_eval.py parses this line
    # for gate L5, so the `mechanic_tier : ` prefix is load-bearing — keep it in
    # sync with _TIER_RE there. The label is for human readers only.
    print(f"    mechanic_tier : {drill.mechanic_tier} ({_TIER_LABELS.get(drill.mechanic_tier, 'not declared')})")
    print(f"    repetitions   : {drill.repetitions}")
    print(f"\n    what          : {drill.what}")
    print(f"    success_criterion: {drill.success_criterion}")
    if drill.common_trap:
        print(f"    common_trap   : {drill.common_trap}")

    # tab_snippet — print measures count + raw JSON for reviewer inspection
    measures = drill.tab_snippet.measures
    print(f"\n    tab_snippet   : {len(measures)} measure(s), tuning={drill.tab_snippet.tuning}")
    # Compact render first — this is what you diff against the main tab for L1.
    for line in _render_measures(measures):
        print(f"      {line}")
    snippet_dict = drill.tab_snippet.model_dump()
    snippet_json = json.dumps(snippet_dict, indent=6)
    for line in snippet_json.splitlines():
        print(f"      {line}")


def _print_landmine_checklist(song: dict, target_skills: list[dict]) -> None:
    """Print the blank reviewer landmine checklist. Reviewer fills this in."""
    print(f"\n  {_SUBSEP}")
    print(f"  LANDMINE GATE CHECKLIST — {song['title']} ({song['artist']})")
    print(f"  {_SUBSEP}")
    print(f"  NOTE (N1 caveat): L2 coverage is LIMITED — eval uses fake UUIDs.")
    print(f"  Full L2 coverage requires Task 3 device step o (real user breakdown).")
    print()
    print(f"  [ ] L1: tab_snippet is a canonical isolated shape (NOT a slice of the main song tab)?")
    print(f"  [ ] L2: all target_skill_temp_ids match IDs in the provided list?  (LIMITED per N1)")
    print(f"  [ ] L3: drill count in [2, 4]? (drills=[] means Landmine #3 soft-fail fired)")
    print(f"  [ ] L4: song_specific=true iff `what` explicitly names the song?")
    print(f"  [ ] L5: mechanic_tier non-decreasing down the drill list? (equal tiers are OK)")
    print(f"  [ ] L6: tab_snippet.measures.length in {{1, 2}}?")
    print(f"  [ ] A1: does each `what` describe the exercise its tab_snippet actually produces?")
    print()
    print(f"  SONG DISPOSITION: [ ] SHIP  [ ] REVISE PROMPT  [ ] REVISE SCHEMA  [ ] DEFER")
    print(f"  Notes: _________________________________________________________")

# ---------------------------------------------------------------------------
# DB helpers — imported lazily inside run_eval() after env check passes
# ---------------------------------------------------------------------------

def _make_engine(db_url: str):
    from sqlalchemy.ext.asyncio import create_async_engine
    return create_async_engine(db_url, echo=False, pool_size=1, max_overflow=0)


def _make_session_factory(engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_throwaway_user(db, uid: uuid.UUID) -> None:
    """Insert a throwaway user into `users` so @governed has a valid FK for governor_calls."""
    from sqlalchemy import text
    await db.execute(
        text("INSERT INTO users (id, preferences) VALUES (:uid, '{}'::jsonb) ON CONFLICT DO NOTHING"),
        {"uid": str(uid)},
    )
    await db.commit()


async def _cleanup_throwaway_user(db, uid: uuid.UUID) -> None:
    """Remove the throwaway eval user and all associated governor_calls rows."""
    from sqlalchemy import text
    uid_str = str(uid)
    await db.execute(text(f"DELETE FROM governor_calls WHERE user_id='{uid_str}'"))
    await db.execute(text(f"DELETE FROM users WHERE id='{uid_str}'"))
    await db.commit()


async def _query_total_tokens(db, uids: list[uuid.UUID]) -> dict:
    """Return total input + output token spend for the throwaway eval users.

    EVAL FIX (2026-09-15): column names corrected to match the real
    governor_calls schema (migration 0004): prompt_tokens_actual /
    output_tokens_actual. The previous names (actual_input_tokens /
    actual_output_tokens) do not exist and raised UndefinedColumn in the
    finally block — i.e. AFTER all 5 paid Anthropic calls had been made.

    Also takes a LIST of uids, because the eval now seeds one throwaway user
    per song (see run_eval) to stay under the @governed cap=3/user/7d.
    """
    from sqlalchemy import text
    result = await db.execute(
        text(
            "SELECT COALESCE(SUM(prompt_tokens_actual), 0) AS total_in, "
            "       COALESCE(SUM(output_tokens_actual), 0) AS total_out, "
            "       COALESCE(SUM(dollars_actual), 0) AS total_dollars "
            "FROM governor_calls "
            "WHERE user_id = ANY(CAST(:uids AS uuid[]))"
        ),
        {"uids": [str(u) for u in uids]},
    )
    row = result.fetchone()
    return {
        "total_input_tokens": int(row[0]),
        "total_output_tokens": int(row[1]),
        "total_dollars_actual": float(row[2]),
    }

# ---------------------------------------------------------------------------
# Core eval loop
# ---------------------------------------------------------------------------

async def run_eval() -> None:
    """Run the live-Anthropic drill eval for all 5 EVAL_SONGS.

    W5 FIX: calls _check_env_and_normalise_db_url() first — exits with clear
    error if ANTHROPIC_API_KEY or DATABASE_URL (real Postgres only) is missing.

    - Prints reviewer-friendly output for each song (drills + landmine checklist).
    - On AIBreakdownError per song: prints the exception and continues to the next song.
    - After all songs: prints total token usage from governor_calls (actuals).
    """
    # W5 FIX: validate env before any DB/app imports — exits if missing or non-Postgres
    db_url = _check_env_and_normalise_db_url()
    # Override DATABASE_URL so app/db/session.py picks up the normalised value
    os.environ["DATABASE_URL"] = db_url

    # Lazy app imports — must come AFTER env check so session.py sees the right URL
    from app.ai.breakdown import AIBreakdownError, run_technique_breakdown

    start_epoch = time.monotonic()

    print("\n" + _SEPARATOR)
    print("GUITAR TRAINER v2 — DRILL QUALITY EVAL (Phase 4.1 Plan 04.1-05)")
    print(f"Songs: {len(EVAL_SONGS)} | Landmine gates: 6 per song = {6 * len(EVAL_SONGS)} total")
    print(f"N1 CAVEAT: L2 (hallucinated id) coverage is limited to fake-UUID echo check.")
    print(f"Real L2 coverage lives in Task 3 device step o.")
    print(_SEPARATOR)

    engine = _make_engine(db_url)
    session_factory = _make_session_factory(engine)

    # EVAL FIX (2026-09-15): ONE THROWAWAY USER PER SONG.
    #
    # run_technique_breakdown is wrapped with @governed(feature='breakdown',
    # cap=3, window='7d'). The original eval reused a single throwaway user for
    # all 5 songs, so songs 4 and 5 would raise BudgetExceededError and land in
    # the error list — silently producing a 3-of-5 eval and leaving 12 of the
    # 30 landmine gates ungradeable.
    #
    # The cap is a PRODUCTION SPEND CONTROL, not part of drill quality, so
    # sidestepping it with a fresh user per song is faithful to what this eval
    # measures. Real spend is still bounded by the 5-song catalogue and is
    # reported from governor_calls.dollars_actual below.
    throwaway_uids: list[uuid.UUID] = []

    errors: list[tuple[str, Exception]] = []

    try:
        for idx, song in enumerate(EVAL_SONGS):
            _print_song_header(idx, song)

            throwaway_uid = uuid.uuid4()
            async with session_factory() as db:
                await _seed_throwaway_user(db, throwaway_uid)
            throwaway_uids.append(throwaway_uid)
            print(f"\n  Throwaway eval user for this song: {throwaway_uid}")

            # Build fake target_skills: {id, name} pairs with uuid4 IDs.
            # N1: these IDs are NOT backed by real skill_nodes rows — only gross
            # hallucinations (Sonnet inventing a UUID not in this list) are caught here.
            target_skills = [
                {"id": str(uuid.uuid4()), "name": name}
                for name in song["target_skill_names"]
            ]

            print(f"\n  target_skills provided to Sonnet:")
            for s in target_skills:
                print(f"    id={s['id']}  name={s['name']!r}")

            print(f"\n  Calling run_technique_breakdown for {song['title']}... (live Anthropic)")

            try:
                async with session_factory() as db:
                    breakdown = await run_technique_breakdown(
                        song_title=song["title"],
                        song_artist=song["artist"],
                        target_skills=target_skills,
                        user_level=0.5,  # intermediate — representative eval level
                        db=db,
                        user_id=throwaway_uid,
                        timeout_seconds=_EVAL_TIMEOUT_SECONDS,
                    )

                drills = breakdown.drills
                if not drills:
                    print(
                        f"\n  WARNING: drills=[] for {song['title']!r}.\n"
                        f"  Landmine #3 soft-fail triggered — Sonnet emitted an out-of-range\n"
                        f"  drill count (< 2 or > 4), downgraded to drills=[] per Plan 01 fix."
                    )
                else:
                    _print_main_tab(breakdown)
                    print(f"\n  Drills emitted: {len(drills)}")
                    for drill_idx, drill in enumerate(drills):
                        _print_drill(drill_idx, drill, target_skills)

                _print_landmine_checklist(song, target_skills)

            except AIBreakdownError as exc:
                print(f"\n  ERROR (AIBreakdownError) for {song['title']!r}: {exc}")
                errors.append((song["title"], exc))
                continue
            except Exception as exc:
                print(f"\n  ERROR (unexpected) for {song['title']!r}: {type(exc).__name__}: {exc}")
                errors.append((song["title"], exc))
                continue

    finally:
        # Query actual token spend from governor_calls before cleanup
        token_totals = {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_dollars_actual": 0.0,
        }
        if throwaway_uids:
            async with session_factory() as db:
                token_totals = await _query_total_tokens(db, throwaway_uids)
                for uid in throwaway_uids:
                    await _cleanup_throwaway_user(db, uid)

        await engine.dispose()

    elapsed = time.monotonic() - start_epoch

    # ---------------------------------------------------------------------------
    # Summary section
    # ---------------------------------------------------------------------------
    print(f"\n{_SEPARATOR}")
    print("EVAL SUMMARY")
    print(_SEPARATOR)
    print(f"  Songs attempted     : {len(EVAL_SONGS)}")
    print(f"  Songs with errors   : {len(errors)}")
    if errors:
        for title, exc in errors:
            print(f"    - {title!r}: {type(exc).__name__}: {exc}")
    print(f"  Total run time      : {elapsed:.1f}s")
    print(f"  Actual tokens used  : input={token_totals['total_input_tokens']} | output={token_totals['total_output_tokens']}")

    # Rough cost estimate: Sonnet 4.6 pricing (as of 2026-09-14)
    # $3/M input, $15/M output (approximate — verify at console.anthropic.com)
    input_cost = token_totals["total_input_tokens"] / 1_000_000 * 3.0
    output_cost = token_totals["total_output_tokens"] / 1_000_000 * 15.0
    total_cost = input_cost + output_cost
    print(f"  Estimated cost      : ${total_cost:.4f} (input ${input_cost:.4f} + output ${output_cost:.4f})")
    print(f"  Governor dollars_actual: ${token_totals['total_dollars_actual']:.4f}")
    print(f"  Budget remaining    : $20/mo cap — this eval used ~${total_cost:.2f}")
    print()
    print("Next step: fill in .planning/phases/04.1-ai-drills/04.1-05-EVAL-RESULTS.md")
    print("           with the landmine gate table above + your disposition.")
    print(_SEPARATOR)


# ---------------------------------------------------------------------------
# Entry point: python -m scripts.eval_drills
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_eval())
