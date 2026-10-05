"""grade_cached_drills.py — run the 04.1 eval gates over drills already in the DB.

FLE-32. The backfill (`scripts/backfill_drills.py`) banks whatever sits in
`songs.breakdown->'drills'`. FLE-32's "done when" is not just that the rows land —
it is that *"the drills banked are ones we are willing to hand a user"*. That is a
quality question, and FLE-32 offers two ways to answer it: pay for another eval
run, or apply a filter that is **mechanically checkable offline with no spend**.

This script is the second option. It reuses `scripts.grade_eval.grade` — the same
gate code, unmodified — against the real cached rows instead of against an eval
transcript. No LLM, no network beyond the DB read, no writes. So the quality of
the exact 16 drills the backfill would bank is measurable for free, as often as
you like, including after every new breakdown a user generates.

WHY THIS IS NOT REDUNDANT WITH THE EVAL
The eval graded 5 hand-picked songs from a dev run. The bank gets whatever the
user actually generated on their phone. Those are different drill sets, so an
eval disposition does not transfer: a 34/40 on Lenny/Kashmir/Little Wing says
nothing about whether *Pride and Joy* drill 4 is orderable. Grade the rows you
are about to ship.

SONG-LEVEL GATES, PER-DRILL DECISIONS
`grade_eval.grade` reports one verdict per gate per song, which is right for
judging a prompt but unusable as a filter — you cannot exclude "song 71's L5".
So each failure is additionally attributed to the specific drill(s) that caused
it, using the same primitives the gate itself uses (`l1_violations`,
`l4_violations`, the A1 `poly_words` list, the L5 tier comparison) so the two
views cannot drift. L1 and L4 were hand-rolled copies here until FLE-123, which
is exactly how they drifted; they now call the gate's own predicates.

Attribution rules, and why:
  L1  the drill that lifted a whole main measure verbatim. Per-drill already.
  L5  the LATER drill of a descending pair. A drop at 3->4 is drill 4 being
      placed too late, so drill 4 is the one to hold back; dropping the earlier
      drill would leave the same gap.
  A1  the drill whose prose promises polyphony. Per-drill already.
  L2  the drill carrying an unresolvable skill id. Per-drill already.
  L6  the drill with the wrong snippet measure count. Per-drill already.
  L7  the drill whose fretted span misses the song entirely. Per-drill already.
  L3  drill COUNT — a property of the set, not of any one drill, so it is
      reported but never attributed. Excluding a drill cannot fix a count gate;
      it makes it worse.

GATE SEVERITY IS A JUDGEMENT AND IS SPELLED OUT, NOT BURIED
`--exclude-gates` defaults to **L1 only**, and L1 is additionally qualified by a
coverage ratio (below). Two gates that look like obvious exclusions are not:

  A1 is EXCLUDED FROM THE DEFAULT — it is 0-for-4 on real production rows.
     A1 fires when `what` contains a polyphony word and the snippet is
     monophonic. On the eval's dev songs that caught a real defect (Lenny D4
     promised an E-shape barre change and shipped four single notes). On the
     cached production rows every hit is a substring artifact:
       song 76 D1 "Hold an open E chord shape and pick only the two bass
         strings... **No treble strings at all.**" — the prose explicitly
         DISCLAIMS polyphony and A1 still flags it. Same for 76 D2/D3: hold a
         shape, pick one note at a time, which is just what fingerstyle is.
       song 60 D3 "...the pentatonic melody in a **chord**-melody context" —
         the word appears in a context clause; the drill says "all on string 2,
         one note per eighth beat".
     A1 cannot tell "promises a chord" from "mentions a chord", so as a filter
     it would hold back correct fingerstyle drills. Reported, never excluded by
     default. Pass `--exclude-gates L1,A1` to override.

  L5 is EXCLUDED FROM THE DEFAULT — a tier drop means the *sequence* is
     mis-ordered, not that any drill is bad. The session planner orders drills
     itself at assembly time, so a tier-drop drill is still a usable drill.

L1'S RATIO QUALIFICATION IS NOW INERT (FLE-123)
This section used to argue that `grade_eval`'s L1 needed a coverage ratio on top,
because firing on any 3 consecutive shared beats conflated two defects. The
production rows split cleanly along that line:

    100% cover  song 71 D1, 71 D3, 76 D3   the snippet IS one bar of the song
     75% cover  song 76 D2
  38-50% cover  song 73 D1/D2/D3, 74 D3    a 3-note coincidence inside the
                                           same pentatonic box the song uses

That reasoning was right about the old gate and is now obsolete, because FLE-123
fixed the gate instead of compensating for it: L1 no longer measures shared
length at all, only whether a snippet measure is a verbatim copy of a whole main
measure. The prompt now REQUIRES the quoting the old rule punished ("the
tab_snippet IS that passage, simplified"), so the bottom group is not a defect in
any degree. `--l1-cover-min` therefore defaults to 0.0 and the exclusion test is
just `L1 failed`; see the constant for why a non-zero floor would now suppress
true positives rather than filter false ones.

Raw `grade_eval` verdicts are always reported unmodified — the ratio changes
which failures become exclusions, never what the gate says.

USAGE (from the server/ directory):
    DATABASE_URL=postgresql+asyncpg://... python -m scripts.grade_cached_drills
    ... --exclude-gates L1,A1,L5      # stricter
    ... --l1-cover-min 1.0            # only hold verbatim whole-bar copies
    ... --json                        # machine-readable, for a filter file
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.db import SkillNode, Song as SongRow
from scripts.grade_eval import (
    Drill,
    Measure,
    Song,
    _has_common_run,
    grade,
    l1_violations,
    l4_violations,
)

# The A1 detector's vocabulary, imported by value rather than re-typed so the
# attribution pass and the gate agree by construction.
_POLY_WORDS = ("barre", "chord", "voicing", "strum", "triad")

# Gates whose failure means "do not hand this drill to a user". See the module
# docstring for why neither A1 nor L5 is in here.
_DEFAULT_EXCLUDE = ("L1",)

# Fraction of a drill's beats that must be a contiguous slice of one main-tab
# measure before an L1 failure counts as "this drill is just the song again".
#
# FLE-123 DROPPED THIS FROM 0.75 TO 0.0 — it is now inert by default, and that is
# deliberate. The ratio existed to compensate for the OLD L1, which fired on any
# 3 shared beats and so conflated "the snippet IS a bar of the song" with "three
# notes of a shared pentatonic box coincide". The rewritten L1 fires only on a
# verbatim whole-measure lift, so the defect the ratio was there to isolate is now
# the only thing the gate reports — and a non-zero floor would actively SUPPRESS
# true positives: a 2-measure drill whose first measure is a verbatim lift covers
# only ~0.5 of its own beats and would escape the filter. The flag is kept so the
# old behaviour is still reachable (`--l1-cover-min 0.75`).
_DEFAULT_L1_COVER_MIN = 0.0

_ALL_GATES = ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "A1")


def _measures_from_json(raw: Any) -> list[Measure]:
    """Convert a breakdown tab/tab_snippet block into grade_eval's Measure list.

    The eval grader parses its own compact text render; the cached JSON is the
    same information one layer up. Durations are dropped on purpose — no
    surviving gate uses them (L5's rhythm dimension was retired in FLE-45).
    """
    if not isinstance(raw, dict):
        return []
    out: list[Measure] = []
    for i, m in enumerate(raw.get("measures") or [], start=1):
        if not isinstance(m, dict):
            continue
        beats: list[list[tuple[int, int]]] = []
        for beat in m.get("beats") or []:
            if not isinstance(beat, dict):
                continue
            notes = [
                (int(n["string"]), int(n["fret"]))
                for n in beat.get("notes") or []
                if isinstance(n, dict) and "string" in n and "fret" in n
            ]
            beats.append(notes)
        out.append(Measure(i, str(m.get("time_signature") or "4/4"), beats))
    return out


def song_from_row(row: SongRow, valid_skill_ids: set[str]) -> Song:
    """Build the grader's Song from a DB row, so `grade()` can run unmodified.

    `valid_skill_ids` is what L2 checks against. The eval fed Sonnet an explicit
    target-skill list and L2 asked whether the echo was on that list; the list is
    long gone by the time a breakdown is cached, so the equivalent question here
    is the one `backfill_drills.py` actually enforces before inserting: does the
    id resolve to a skill node this user owns? Same defect caught (a hallucinated
    or stale id), against the only ground truth still available.
    """
    breakdown = row.breakdown if isinstance(row.breakdown, dict) else {}
    song = Song(
        index=row.id,
        title=row.title or "",
        artist=row.artist or "",
        skill_ids=sorted(valid_skill_ids),
        main=_measures_from_json(breakdown.get("tab")),
    )
    for i, raw in enumerate(breakdown.get("drills") or []):
        if not isinstance(raw, dict):
            continue
        tier = raw.get("mechanic_tier")
        song.drills.append(
            Drill(
                index=i + 1,  # grade_eval drill indices are 1-based
                name=str(raw.get("name") or ""),
                song_specific=raw.get("song_specific"),
                skill_id=str(raw.get("target_skill_temp_id") or ""),
                start_bpm=raw.get("start_bpm"),
                target_bpm=raw.get("target_bpm"),
                mechanic_tier=int(tier) if isinstance(tier, int) else None,
                what=str(raw.get("what") or ""),
                measures=_measures_from_json(raw.get("tab_snippet")),
            )
        )
    return song


def l1_cover(song: Song, drill: Drill) -> float:
    """Fraction of `drill`'s beats that form a contiguous run inside one main measure.

    REPORTING ONLY as of FLE-123, and no longer the qualifier it was written to be
    (`--l1-cover-min` now defaults to 0.0). It separates "the snippet IS a bar of
    the song" (1.0) from "three notes of a shared scale box coincide" (~0.4), which
    mattered when L1 fired on any 3 shared beats; the rewritten L1 reports only the
    former. Still printed because quoting-as-a-proportion is the measurement
    FLE-89 used, and it remains the fastest way to see how much of a drill is the
    song. See the module docstring.
    """
    db = [tuple(b) for m in drill.measures for b in m.beats]
    if not db:
        return 0.0
    best = 0
    for m in song.main:
        mb = [tuple(b) for b in m.beats]
        for i in range(len(db)):
            for j in range(len(mb)):
                k = 0
                while i + k < len(db) and j + k < len(mb) and db[i + k] == mb[j + k]:
                    k += 1
                best = max(best, k)
    return best / len(db)


def attribute(song: Song) -> dict[int, list[str]]:
    """Map each failing gate onto the drill index (1-based) that caused it.

    L3 is absent by design: a bad drill count cannot be fixed by excluding a
    drill. See the module docstring.
    """
    blame: dict[int, list[str]] = {d.index: [] for d in song.drills}

    for d in song.drills:
        w = d.what.lower()

        # FLE-123: L1 and L4 call the gate's own per-drill predicates instead of
        # re-implementing them. They used to be hand-rolled copies here, which is
        # how both outlived the prompt change that retired them while this module's
        # docstring promised the two views "cannot drift".
        if l1_violations(song, d):
            blame[d.index].append("L1")

        if d.skill_id not in set(song.skill_ids):
            blame[d.index].append("L2")

        if l4_violations(song, d):
            blame[d.index].append("L4")

        if len(d.measures) not in (1, 2):
            blame[d.index].append("L6")

        spans = [(min(m.fretted), max(m.fretted)) for m in song.main if m.fretted]
        if d.fretted:
            dlo, dhi = min(d.fretted), max(d.fretted)
            if not any(dlo <= hi and lo <= dhi for lo, hi in spans):
                blame[d.index].append("L7")

        voices = max((len(b) for m in d.measures for b in m.beats), default=0)
        if voices <= 1 and any(word in w for word in _POLY_WORDS):
            blame[d.index].append("A1")

    # L5 — attribute a descending pair to the LATER drill, and a missing tier to
    # the drill that omitted it.
    for i, d in enumerate(song.drills):
        if d.mechanic_tier is None:
            blame[d.index].append("L5")
        elif i > 0:
            prev = song.drills[i - 1].mechanic_tier
            if prev is not None and d.mechanic_tier < prev:
                blame[d.index].append("L5")

    return blame


def exclusions(
    song: Song,
    *,
    exclude_gates: tuple[str, ...] = _DEFAULT_EXCLUDE,
    l1_cover_min: float = _DEFAULT_L1_COVER_MIN,
) -> dict[int, list[str]]:
    """1-based drill index -> the gates that hold that drill out of the bank.

    The single place the exclusion decision is made, so the report and
    `backfill_drills.py --quality-filter` cannot disagree about which drills are
    held back.
    """
    blame = attribute(song)
    out: dict[int, list[str]] = {}
    for d in song.drills:
        out[d.index] = [
            g
            for g in blame[d.index]
            if g in exclude_gates and (g != "L1" or l1_cover(song, d) >= l1_cover_min)
        ]
    return out


def excluded_drill_indices(
    row: SongRow,
    valid_skill_ids: set[str],
    *,
    exclude_gates: tuple[str, ...] = _DEFAULT_EXCLUDE,
    l1_cover_min: float = _DEFAULT_L1_COVER_MIN,
) -> dict[int, list[str]]:
    """The same decision keyed by **0-based** index, which is what the backfill uses.

    `backfill_drills.py` enumerates `breakdown['drills']` from 0; the grader's
    drill indices are 1-based to match the eval's own `DRILL 1:` render. Convert
    here rather than making either caller remember the offset.
    """
    song = song_from_row(row, valid_skill_ids)
    kills = exclusions(song, exclude_gates=exclude_gates, l1_cover_min=l1_cover_min)
    return {i - 1: gates for i, gates in kills.items() if gates}


async def _valid_skill_ids(db: AsyncSession, user_id: uuid.UUID) -> set[str]:
    rows = (
        await db.execute(select(SkillNode.id).where(SkillNode.user_id == user_id))
    ).scalars().all()
    return {str(r) for r in rows}


async def run(
    db: AsyncSession,
    *,
    exclude_gates: tuple[str, ...],
    l1_cover_min: float = _DEFAULT_L1_COVER_MIN,
    user_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Grade every cached breakdown and return the report as plain data."""
    query = select(SongRow).where(
        SongRow.user_id.isnot(None), SongRow.breakdown_generated_at.isnot(None)
    )
    if user_id is not None:
        query = query.where(SongRow.user_id == user_id)
    rows = (await db.execute(query.order_by(SongRow.id))).scalars().all()

    skill_cache: dict[uuid.UUID, set[str]] = {}
    songs_out: list[dict[str, Any]] = []
    gate_tally = {g: {"PASS": 0, "FAIL": 0} for g in _ALL_GATES}
    total_drills = 0
    excluded_drills = 0

    for row in rows:
        if row.user_id not in skill_cache:
            skill_cache[row.user_id] = await _valid_skill_ids(db, row.user_id)
        song = song_from_row(row, skill_cache[row.user_id])
        if not song.drills:
            continue

        gates = grade(song)
        blame = attribute(song)
        kills = exclusions(
            song, exclude_gates=exclude_gates, l1_cover_min=l1_cover_min
        )
        for gate, (verdict, _) in gates.items():
            if verdict in gate_tally[gate]:
                gate_tally[gate][verdict] += 1

        drills_out = []
        for d in song.drills:
            total_drills += 1
            failed = blame[d.index]
            cover = l1_cover(song, d)
            kill = kills[d.index]
            if kill:
                excluded_drills += 1
            drills_out.append(
                {
                    "index": d.index,
                    "name": d.name,
                    "mechanic_tier": d.mechanic_tier,
                    "failed_gates": failed,
                    "l1_cover": round(cover, 3),
                    "excluded_by": kill,
                    "bank": not kill,
                }
            )

        songs_out.append(
            {
                "song_id": row.id,
                "title": song.title,
                "artist": song.artist,
                "generated_at": row.breakdown_generated_at.isoformat(),
                "gates": {g: {"verdict": v, "evidence": e} for g, (v, e) in gates.items()},
                "drills": drills_out,
            }
        )

    return {
        "exclude_gates": list(exclude_gates),
        "l1_cover_min": l1_cover_min,
        "songs": songs_out,
        "gate_tally": gate_tally,
        "totals": {
            "songs": len(songs_out),
            "drills": total_drills,
            "excluded": excluded_drills,
            "bankable": total_drills - excluded_drills,
        },
    }


def _print_report(report: dict[str, Any]) -> None:
    gates = _ALL_GATES
    print("\n=== cached-drill quality gates (no spend, no writes) ===")
    print(
        f"exclusion set: {', '.join(report['exclude_gates']) or '(none)'}"
        f"   (L1 needs cover >= {report['l1_cover_min']:.0%})\n"
    )

    header = f"{'song':<32} " + " ".join(f"{g:>4}" for g in gates)
    print(header)
    print("-" * len(header))
    for s in report["songs"]:
        cells = []
        for g in gates:
            v = s["gates"][g]["verdict"]
            cells.append(f"{'ok' if v == 'PASS' else v:>4}")
        print(f"{s['title'][:32]:<32} " + " ".join(cells))

    print("\n--- per-drill decisions ---")
    for s in report["songs"]:
        print(f"\nsong {s['song_id']} — {s['title']}")
        for d in s["drills"]:
            mark = "BANK " if d["bank"] else "HOLD "
            fails = ",".join(d["failed_gates"]) or "-"
            why = f"  excluded_by={','.join(d['excluded_by'])}" if d["excluded_by"] else ""
            print(
                f"  {mark}D{d['index']} tier={d['mechanic_tier']} "
                f"cover={d['l1_cover']:.0%} fails={fails}{why}"
            )
            print(f"        {d['name'][:70]}")

    print("\n--- gate tally (per song) ---")
    for g in gates:
        t = report["gate_tally"][g]
        print(f"  {g:<4} PASS {t['PASS']}  FAIL {t['FAIL']}")

    t = report["totals"]
    print(
        f"\n  songs {t['songs']}   drills {t['drills']}   "
        f"bankable {t['bankable']}   held {t['excluded']}"
    )
    for s in report["songs"]:
        for g in gates:
            if s["gates"][g]["verdict"] == "FAIL":
                print(f"  FAIL {g} song {s['song_id']}: {s['gates'][g]['evidence']}")


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Grade already-cached breakdown drills against the 04.1 eval gates."
    )
    parser.add_argument(
        "--exclude-gates",
        default=",".join(_DEFAULT_EXCLUDE),
        help=(
            "Comma-separated gates whose failure holds a drill out of the bank. "
            f"Default {','.join(_DEFAULT_EXCLUDE)}. Pass '' to grade without excluding."
        ),
    )
    parser.add_argument(
        "--l1-cover-min",
        type=float,
        default=_DEFAULT_L1_COVER_MIN,
        help=(
            "Fraction of a drill's beats that must be a contiguous slice of one main "
            f"measure before an L1 failure excludes it. Default {_DEFAULT_L1_COVER_MIN} "
            "— inert since FLE-123 rewrote L1 to fire only on verbatim whole-measure "
            "lifts; a non-zero floor now suppresses true positives."
        ),
    )
    parser.add_argument("--user-id", metavar="UUID", help="Limit to one user.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = parser.parse_args(argv)

    requested = tuple(g.strip().upper() for g in args.exclude_gates.split(",") if g.strip())
    unknown = [g for g in requested if g not in _ALL_GATES]
    if unknown:
        raise SystemExit(f"--exclude-gates: unknown gate(s) {unknown}; known are {list(_ALL_GATES)}")
    if "L3" in requested:
        raise SystemExit(
            "--exclude-gates: L3 is the drill COUNT gate. Excluding a drill cannot fix it, "
            "it can only make the count worse. Remove L3."
        )
    if not 0.0 <= args.l1_cover_min <= 1.0:
        raise SystemExit(f"--l1-cover-min must be in [0,1]; got {args.l1_cover_min}")

    async with AsyncSessionLocal() as db:
        report = await run(
            db,
            exclude_gates=requested,
            l1_cover_min=args.l1_cover_min,
            user_id=uuid.UUID(args.user_id) if args.user_id else None,
        )
        await db.rollback()  # read-only by construction; make that explicit

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
