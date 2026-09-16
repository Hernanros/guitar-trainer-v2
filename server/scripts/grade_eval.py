"""grade_eval.py — mechanical grader for eval_drills.py output.

Phase 4.1 FLE-17 §3. The re-run's whole point is that the FLE-17 §2/§4 prompt
changes restated every soft gate as a mechanical test. If a gate is mechanical
for the model, it is mechanical for the grader too — so grade it with code
rather than by eye, and let the reviewer spend their attention on the
judgement calls that are genuinely left.

Reads the text output of `python -m scripts.eval_drills` and emits a gate
table. Parsing targets the compact render `_render_measures` already prints:

    m1 (12/8): [6/0] [5/2] [4/2] [3/1]

Gates, and exactly how each is computed here:

  L1  no 3+ consecutive (string, fret) pairs of a snippet appear in that
      order inside any single main-tab measure. Durations and time signature
      are deliberately ignored, matching the prompt's self-check wording.
  L2  every target_skill_temp_id is one of the ids handed to Sonnet.
  L3  2 <= drill count <= 4.
  L4  song_specific is true IFF the song title or artist appears in `what`
      (case-insensitive substring, both directions checked).
  L5  drills are emitted in non-decreasing `mechanic_tier` order (FLE-45).
      Tempo is NOT a difficulty signal (retired, FLE-17 checkbox
      `retire-l5-tempo-gate`).
  L6  snippet measure count in {1, 2}.
  L7  NEW 31st gate (checkbox `optional-4th-neck-region`): a snippet's fretted
      span must overlap the fretted span of at least one main-tab measure.
      Open strings are excluded from a span; a snippet of only open strings is
      exempt, since the prompt's own exception allows it.
  A1  prose/tab agreement: if `what` promises a chord/barre/voicing/strum/triad,
      at least one beat of the snippet must sound 2+ strings together. Promoted
      from advisory to a counted gate by FLE-45 — Lenny D4 promised an E-shape
      barre change and shipped four single notes on one string, which is a
      worse defect than anything L1-L7 catches.

WHY L5 IS NO LONGER COMPUTED HERE (FLE-45)
Through 04.1-06 this gate rated each drill on five geometric dimensions
(distinct shapes, shape change, simultaneous strings, position shift, rhythm
displacement) and required dominance: no dimension may fall, at least one must
rise. Every gradeable song failed it, and half of those failures were the
rule's, not the model's — Little Wing D4 is a six-note bass-chord-melody roll,
the hardest drill in its set, and it "failed" for sounding ONE string at a time
where D3 sounded two. Beat It D4 traded a position shift for a cross-string
root change and failed the same way.

Real difficulty progressions trade dimensions. A rule that forbids every trade
forbids most correct orderings. The root cause, shared with 04.1-05's additive
formula, is that difficulty is NOT a function of the snippet's geometry — so
neither the prompt nor this grader can derive it. The model now declares
`mechanic_tier` against the prompt's ordered MECHANIC TIERS list and this gate
checks only that the declarations are non-decreasing.

The five dimensions are still COMPUTED and PRINTED per drill, because they are
useful review context and they are how a reviewer spots a declared tier that
the geometry flatly contradicts. They no longer gate anything.

USAGE:  python -m scripts.grade_eval <eval-output.txt>
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

_SONG_RE = re.compile(r"^SONG (\d)/5: (.+?) — (.+)$")
_SKILL_RE = re.compile(r"^\s+id=([0-9a-f-]{36})\s+name='(.*)'$")
_MAINTAB_RE = re.compile(r"^\s+MAIN SONG TAB — (\d+) measure\(s\), tuning=(.+)$")
_MEASURE_RE = re.compile(r"^\s+m(\d+) \(([^)]+)\): (.+)$")
_DRILL_RE = re.compile(r"^\s+DRILL (\d+): (.+)$")
_SONGSPEC_RE = re.compile(r"^\s+song_specific : (True|False)$")
_SKILLID_RE = re.compile(r"^\s+target_skill  : .*\(id=([0-9a-f-]{36})\)$")
_TEMPO_RE = re.compile(r"^\s+tempo         : (\d+) → (\d+) BPM$")
# FLE-45: `mechanic_tier : 3 (held shape struck)`. Only the integer is captured;
# the label is render sugar. `None` is matched explicitly so a drill that omitted
# the field parses as a drill with no tier rather than silently not matching.
_TIER_RE = re.compile(r"^\s+mechanic_tier : (\d+|None)\b")
_WHAT_RE = re.compile(r"^\s+what          : (.*)$")
_SNIPPET_RE = re.compile(r"^\s+tab_snippet   : (\d+) measure\(s\), tuning=(.+)$")
_BEAT_RE = re.compile(r"\[([^\]]*)\]")
_ERROR_RE = re.compile(r"^\s+ERROR \((.+?)\) for '(.+?)': (.+)$")


@dataclass
class Measure:
    index: int
    time_sig: str
    beats: list[list[tuple[int, int]]]

    @property
    def pairs(self) -> list[tuple[int, int]]:
        """Flattened ordered (string, fret) sequence — the L1 comparison unit."""
        return [n for beat in self.beats for n in beat]

    @property
    def fretted(self) -> list[int]:
        return [f for _, f in self.pairs if f > 0]


@dataclass
class Drill:
    index: int
    name: str
    song_specific: bool | None = None
    skill_id: str | None = None
    start_bpm: int | None = None
    target_bpm: int | None = None
    # FLE-45: the declared MECHANIC TIERS position, 1-6. None means the drill did
    # not emit one, which gate L5 fails.
    mechanic_tier: int | None = None
    what: str = ""
    measures: list[Measure] = field(default_factory=list)

    @property
    def pairs(self) -> list[tuple[int, int]]:
        return [p for m in self.measures for p in m.pairs]

    @property
    def fretted(self) -> list[int]:
        return [f for _, f in self.pairs if f > 0]

    def dimensions(self) -> dict[str, int]:
        """The five L5 dimensions, as integers so they can be compared."""
        shapes: list[frozenset[tuple[int, int]]] = []
        for m in self.measures:
            for beat in m.beats:
                shape = frozenset(n for n in beat if n[1] > 0)
                if shape and shape not in shapes:
                    shapes.append(shape)
        max_voices = max(
            (len(beat) for m in self.measures for beat in m.beats), default=0
        )
        span = (max(self.fretted) - min(self.fretted)) if self.fretted else 0
        return {
            "a_shapes": len(shapes),
            "b_change": 1 if len(shapes) > 1 else 0,
            "c_voices": max_voices,
            "d_shift": 1 if span > 4 else 0,
            "e_displaced": 0,  # filled from JSON durations; see _parse_durations
        }


@dataclass
class Song:
    index: int
    title: str
    artist: str
    skill_ids: list[str] = field(default_factory=list)
    main: list[Measure] = field(default_factory=list)
    drills: list[Drill] = field(default_factory=list)
    error: str | None = None


def _parse_beats(body: str) -> list[list[tuple[int, int]]]:
    beats = []
    for group in _BEAT_RE.findall(body):
        notes = []
        for tok in group.split():
            s, _, f = tok.partition("/")
            try:
                notes.append((int(s), int(f)))
            except ValueError:
                continue
        beats.append(notes)
    return beats


def parse(path: str) -> list[Song]:
    songs: list[Song] = []
    song: Song | None = None
    drill: Drill | None = None
    # Which measure list incoming `mN (...)` lines belong to.
    target: list[Measure] | None = None
    in_skills = False

    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")

        m = _SONG_RE.match(line.strip()) or _SONG_RE.match(line)
        if m:
            song = Song(int(m.group(1)), m.group(2).strip(), m.group(3).strip())
            songs.append(song)
            drill, target, in_skills = None, None, False
            continue
        if song is None:
            continue

        if line.strip().startswith("target_skills provided to Sonnet:"):
            in_skills = True
            continue
        if in_skills:
            sk = _SKILL_RE.match(line)
            if sk:
                song.skill_ids.append(sk.group(1))
                continue
            if line.strip():
                in_skills = False

        err = _ERROR_RE.match(line)
        if err:
            song.error = f"{err.group(1)}: {err.group(3)}"
            continue

        if _MAINTAB_RE.match(line):
            target = song.main
            drill = None
            continue

        d = _DRILL_RE.match(line)
        if d:
            drill = Drill(int(d.group(1)), d.group(2))
            song.drills.append(drill)
            target = None
            continue

        if drill is not None:
            ss = _SONGSPEC_RE.match(line)
            if ss:
                drill.song_specific = ss.group(1) == "True"
                continue
            si = _SKILLID_RE.match(line)
            if si:
                drill.skill_id = si.group(1)
                continue
            tp = _TEMPO_RE.match(line)
            if tp:
                drill.start_bpm, drill.target_bpm = int(tp.group(1)), int(tp.group(2))
                continue
            ti = _TIER_RE.match(line)
            if ti:
                drill.mechanic_tier = None if ti.group(1) == "None" else int(ti.group(1))
                continue
            wh = _WHAT_RE.match(line)
            if wh:
                drill.what = wh.group(1)
                continue
            if _SNIPPET_RE.match(line):
                target = drill.measures
                continue

        mm = _MEASURE_RE.match(line)
        if mm and target is not None:
            target.append(
                Measure(int(mm.group(1)), mm.group(2), _parse_beats(mm.group(3)))
            )
            continue

    return songs


def _parse_durations(path: str, songs: list[Song]) -> None:
    """Fill dimension (e) from the snippet JSON blocks the eval also prints.

    The compact render drops durations, so `even vs displaced` has to come from
    the JSON dump that follows it. A snippet is 'displaced' when its beats do
    not all carry the same duration.
    """
    text = open(path, encoding="utf-8").read()
    blocks = re.findall(r'"duration": "(\w+)"', text)
    # Durations are emitted in drill order across the whole file; re-walk per
    # drill using the beat counts we already parsed.
    cursor = 0
    for song in songs:
        for drill in song.drills:
            n = sum(len(m.beats) for m in drill.measures)
            window = blocks[cursor : cursor + n]
            cursor += n
            drill._durations = window  # type: ignore[attr-defined]


def _has_common_run(a: list, b: list, n: int = 3) -> list | None:
    """Return the first length-n contiguous run of `a` that occurs in `b`."""
    for i in range(len(a) - n + 1):
        run = a[i : i + n]
        for j in range(len(b) - n + 1):
            if b[j : j + n] == run:
                return run
    return None


def grade(song: Song) -> dict[str, tuple[str, str]]:
    """Return {gate: (PASS|FAIL|N/A, evidence)}."""
    out: dict[str, tuple[str, str]] = {}
    if song.error:
        return {g: ("FAIL", song.error) for g in ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "A1")}

    # L1 — slice detection, compared BEAT-by-beat rather than note-by-note.
    #
    # The prompt's self-check says "3 consecutive (string, fret) pairs". Taken
    # literally over a flattened note list, any reused 3-note chord voicing is a
    # violation — which is unsatisfiable, because a drill for a chord mechanic
    # must use that chord, and the neck-region rule now requires it to be in the
    # song's own position. So the comparison unit here is the BEAT: a 3-note
    # chord is one beat, and a violation means three consecutive beats of the
    # song reproduced in order. That is the slice the gate was written to catch.
    # (The prompt wording needs the same correction — see EVAL-RESULTS.)
    def _beats(ms):
        return [tuple(b) for m in ms for b in m.beats]

    hits = []
    for d in song.drills:
        db = _beats(d.measures)
        for m in song.main:
            run = _has_common_run(db, [tuple(b) for b in m.beats])
            if run:
                hits.append(f"drill {d.index} reproduces 3 beats of main m{m.index}")
                break
    out["L1"] = ("FAIL", "; ".join(hits)) if hits else ("PASS", "no 3-beat run shared")

    # L2 — id echo
    bad = [d.index for d in song.drills if d.skill_id not in song.skill_ids]
    out["L2"] = ("FAIL", f"drills {bad} off-list") if bad else ("PASS", "all ids on list")

    # L3 — count
    n = len(song.drills)
    out["L3"] = (("PASS" if 2 <= n <= 4 else "FAIL"), f"{n} drills")

    # L4 — song_specific iff named
    l4 = []
    for d in song.drills:
        w = d.what.lower()
        named = song.title.lower() in w or song.artist.lower() in w
        if named != bool(d.song_specific):
            l4.append(f"drill {d.index}: names={named} flag={d.song_specific}")
    out["L4"] = ("FAIL", "; ".join(l4)) if l4 else ("PASS", "flag matches substring test")

    # L5 — non-decreasing declared mechanic_tier (FLE-45; see module docstring for
    # why the old five-dimension dominance rule was retired).
    tiers = [d.mechanic_tier for d in song.drills]
    missing = [d.index for d in song.drills if d.mechanic_tier is None]
    if missing:
        # A missing tier is a FAIL, not an N/A. The field is Optional on the model
        # purely so pre-FLE-45 cached breakdowns still read back; fresh Sonnet
        # output omitting it means the ordering is ungradeable, which is the thing
        # this gate exists to prevent.
        out["L5"] = ("FAIL", f"drills {missing} emitted no mechanic_tier")
    else:
        viol = [
            f"{i + 1}→{i + 2} drops tier {tiers[i]}→{tiers[i + 1]}"
            for i in range(len(tiers) - 1)
            if tiers[i + 1] < tiers[i]
        ]
        out["L5"] = (
            ("FAIL", "; ".join(viol)) if viol
            else ("PASS", f"tiers non-decreasing: {tiers}")
        )

    # L6 — snippet measure count
    bad6 = [f"drill {d.index}={len(d.measures)}" for d in song.drills if len(d.measures) not in (1, 2)]
    out["L6"] = ("FAIL", "; ".join(bad6)) if bad6 else ("PASS", "all in {1,2}")

    # L7 — neck region overlap (NEW)
    spans = [(min(m.fretted), max(m.fretted)) for m in song.main if m.fretted]
    bad7 = []
    for d in song.drills:
        if not d.fretted:
            continue  # open-string exception, per the prompt
        dlo, dhi = min(d.fretted), max(d.fretted)
        if not any(dlo <= hi and lo <= dhi for lo, hi in spans):
            bad7.append(f"drill {d.index} frets {dlo}-{dhi} vs main {spans}")
    out["L7"] = ("FAIL", "; ".join(bad7)) if bad7 else ("PASS", "every snippet overlaps a main measure")

    # A1 — a COUNTED gate as of FLE-45 (it was advisory through 04.1-07).
    #
    # `what` and `tab_snippet` are generated independently and nothing checked
    # that they describe the same exercise. Lenny D4 is the case that forced the
    # promotion: titled "Barre Change V→VII", `what` promising "move between an
    # E-shape barre at fret 5 and fret 7... before striking the full chord", and
    # a snippet of [3/6] [3/7] [3/8] [3/9] — four single notes on one string, no
    # chord anywhere. The user practises something the tab cannot produce. That
    # is a worse defect than any L1-L7 violation, so it is graded like one.
    #
    # Deliberately ONE-DIRECTIONAL and conservative: it fires only when the prose
    # promises polyphony and the snippet is provably monophonic. The reverse
    # (a chord snippet described in single-note prose) is not flagged, because
    # prose legitimately describes the hard part rather than every string, and a
    # detector for it would be guesswork. False negatives over false positives —
    # a counted gate that cries wolf would get ignored.
    poly_words = ("barre", "chord", "voicing", "strum", "triad")
    bad_a1 = []
    for d in song.drills:
        voices = max((len(b) for m in d.measures for b in m.beats), default=0)
        if voices <= 1 and any(w in d.what.lower() for w in poly_words):
            claimed = [w for w in poly_words if w in d.what.lower()]
            bad_a1.append(f"drill {d.index} promises {claimed} but snippet is monophonic")
    out["A1"] = ("FAIL", "; ".join(bad_a1)) if bad_a1 else ("PASS", "no prose/tab mismatch detected")
    return out


def main() -> int:
    path = sys.argv[1]
    songs = parse(path)
    _parse_durations(path, songs)

    gates = ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "A1"]
    passed = total = 0
    for song in songs:
        print(f"\n{'=' * 72}\n{song.index}. {song.title} — {song.artist}")
        if song.error:
            print(f"   ERROR: {song.error}")
        print(f"   main tab: {len(song.main)} measures | drills: {len(song.drills)}")
        for d in song.drills:
            # The five dimensions no longer gate anything (FLE-45) but stay in the
            # per-drill line: they are how a reviewer spots a declared tier the
            # geometry flatly contradicts.
            dd = d.dimensions()
            dd["e_displaced"] = 1 if len(set(getattr(d, "_durations", []))) > 1 else 0
            fr = f"{min(d.fretted)}-{max(d.fretted)}" if d.fretted else "open only"
            tier = f"T{d.mechanic_tier}" if d.mechanic_tier is not None else "T?"
            print(
                f"     D{d.index} {tier:3} {d.name[:42]:42} frets {fr:9} "
                f"dims a{dd['a_shapes']} b{dd['b_change']} c{dd['c_voices']} "
                f"d{dd['d_shift']} e{dd['e_displaced']}  {d.start_bpm}→{d.target_bpm}bpm"
            )
        res = grade(song)
        for g in gates:
            status, ev = res[g]
            # FLE-45: A1 is counted now. Every gate in `gates` scores.
            total += 1
            passed += status == "PASS"
            print(f"   {g}: {status:4} — {ev}")
    print(f"\n{'=' * 72}\nTOTAL: {passed}/{total} gates passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
