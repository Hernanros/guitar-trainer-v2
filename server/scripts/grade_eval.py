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

  L1  no snippet measure is a verbatim copy of a whole main-tab measure — same
      time signature, same beat count, same notes in every beat. REWRITTEN by
      FLE-123; the old "no 3+ shared consecutive beats" rule is retired, see
      below.
  L2  every target_skill_temp_id is one of the ids handed to Sonnet.
  L3  2 <= drill count <= 4.
  L4  every drill declaring song_specific=true plays only fretted notes the main
      tab also plays. One-directional, and N/A on a song whose drills all declare
      false. REWRITTEN by FLE-123; the old title/artist substring IFF is retired,
      see below.
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

WHY L1 AND L4 WERE REWRITTEN (FLE-123)
Both gates had come to grade against rules the shipped SYSTEM_PROMPT does not
state — the same "half the failures were the rule's, not the model's" trap the
L5 section above records, and the reason the 2026-09-24 baseline could not
simply be re-run. FLE-99 (commit e4cbc0e) moved the prompt; these two gates did
not follow.

  L1, RETIRED AS AN ANTI-SLICE RULE. The prompt deliberately dropped the
  anti-slice instruction and now says the opposite in as many words: "the
  tab_snippet IS that passage, simplified", "Quoting the song here is the point,
  not something to dodge". tests/test_breakdowns_mocked.py:503 pins the removal.
  So the old rule failed the model for OBEYING the prompt, and measurably did:
  all 3 of the 3 L1 failures in the 2026-09-24 baseline are textbook
  simplifications — Beat It D2 is the song's F#5 power chord held for a bar with
  every harmonic move removed, Beat It D3 reduces m3's 8 beats to 4, Little Wing
  D2 repeats m3's second half renotated 12/8 -> 4/4. This is also FLE-89's
  measured finding (8 of 16 cached drills already quote the song).
  L1 is NOT merely weakened to a longer run or a higher threshold: shared length
  is the wrong axis entirely, because the prompt wants the quote. What survives
  is the one thing SIMPLIFY still forbids — a lift with no simplification at all.
  See l1_violations().

  L4, RETIRED AS A SUBSTRING IFF. The prompt's SONG_SPECIFIC block states,
  verbatim: "Do not scan `what` for that text, and do not key song_specific off
  whether it appears there." L4 was precisely that scan. Worse, it was never
  testing the model in either direction:
    - false->true is a CODE guarantee. enforce_song_specific() forces the flag
      true whenever `what` names the song, using `_names_song`, the same
      substring test. That direction can therefore never fail.
    - true->false was the only live direction, and NAME THE PASSAGE now requires
      `what` to name the passage ("the intro's hammer-on lick") without
      requiring the title. So a correct passage-anchored drill flags true, does
      not contain the title, and fails.
  Empirically the gate has never tested anything: all 16 drills of the
  2026-09-24 baseline declared song_specific=false, so its 5/5 was vacuous — and
  under the current prompt it would have flipped to a systematic false failure.
  The replacement grades the flag against the drill's notes instead of its prose,
  which is the only mechanical consequence the SONG_SPECIFIC block actually
  states. It cannot grade the false direction; that half is dropped rather than
  guessed, because a gate contradicting the prompt is worse than a missing one.
  See l4_violations().

CONSEQUENCE FOR THE SCORE'S DENOMINATOR: L4 can now report N/A, which main()
excludes from both numerator and denominator. A 5-song run is no longer
necessarily out of 40, so compare gate-by-gate and not just totals.

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
    """Return the first length-n contiguous run of `a` that occurs in `b`.

    NO LONGER A GATE PRIMITIVE as of FLE-123 — L1 used to be built on this and the
    rule it implemented is retired (see the module docstring). Kept because
    `grade_cached_drills.l1_cover` measures shared quoting as a proportion, which
    is still useful *reporting*: it is how FLE-89 measured that 8 of 16 cached
    drills quote the song. Do not rebuild a gate on it.
    """
    for i in range(len(a) - n + 1):
        run = a[i : i + n]
        for j in range(len(b) - n + 1):
            if b[j : j + n] == run:
                return run
    return None


def _measure_sig(m: Measure) -> tuple:
    """A measure's identity for L1: its time signature and its exact beat list."""
    return (m.time_sig, tuple(tuple(b) for b in m.beats))


def l1_violations(song: Song, drill: Drill) -> list[str]:
    """L1 (FLE-123): snippet measures that are verbatim copies of a whole main measure.

    What the prompt still forbids is a snippet that did NO isolating work. Every
    simplification SIMPLIFY names is a REMOVAL — drop strings, drop beats, slow the
    subdivision, hold a shape longer, cut to the hardest moment — so an obedient
    snippet differs from its source measure in beat count, in some beat's note set,
    or in time signature. A measure matching on all three removed nothing.

    Durations are deliberately NOT consulted: beats plus time signature already pin
    a verbatim copy, and this keeps L1 off `_parse_durations`' fragile whole-file
    cursor walk.

    Per-drill so `grade()` and `grade_cached_drills.attribute()` cannot drift.
    """
    main_sigs = {_measure_sig(m): m.index for m in song.main}
    hits = []
    for i, dm in enumerate(drill.measures):
        src = main_sigs.get(_measure_sig(dm))
        if src is not None:
            hits.append(
                f"drill {drill.index} m{i + 1} is a verbatim copy of main m{src} "
                f"({len(dm.beats)} beats, {dm.time_sig}) — nothing simplified"
            )
    return hits


def l4_violations(song: Song, drill: Drill) -> list[str]:
    """L4 (FLE-123): a song_specific=true drill whose snippet is not from the song.

    The prompt defines the flag by the MECHANIC, which no code can read. But it
    states one mechanical consequence: when song_specific is true, "the tab_snippet
    IS that passage, simplified". Simplification removes and never substitutes, so a
    true-flagged snippet cannot contain a fretted note the song never plays. One
    that does was not derived from any passage, and the flag is unsupported by the
    only evidence the harness can see.

    ONE-DIRECTIONAL, like A1 and for the same reason: deciding that a drill is
    foundational-rather-than-song-anchored is the semantic judgement
    `enforce_song_specific()` exists to avoid making, and the false->true half is
    already a code-level guarantee there. Open strings are excluded, matching L7 —
    fret 0 is position-neutral and the prompt's own exception allows it.
    """
    if not drill.song_specific:
        return []
    main_pairs = {p for m in song.main for p in m.pairs if p[1] > 0}
    foreign = sorted({p for p in drill.pairs if p[1] > 0} - main_pairs)
    if not foreign:
        return []
    return [
        f"drill {drill.index} declares song_specific but plays "
        f"{[f'{s}/{f}' for s, f in foreign]}, absent from the main tab"
    ]


def grade(song: Song) -> dict[str, tuple[str, str]]:
    """Return {gate: (PASS|FAIL|N/A, evidence)}."""
    out: dict[str, tuple[str, str]] = {}
    if song.error:
        return {g: ("FAIL", song.error) for g in ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "A1")}

    # L1 — UNSIMPLIFIED LIFT (rewritten, FLE-123). The rule lives in
    # l1_violations(); see it and the module docstring for why the old
    # shared-3-beat-run test was the opposite of what the prompt ships.
    hits = [h for d in song.drills for h in l1_violations(song, d)]
    out["L1"] = ("FAIL", "; ".join(hits)) if hits else ("PASS", "every snippet reduces its source")

    # L2 — id echo
    bad = [d.index for d in song.drills if d.skill_id not in song.skill_ids]
    out["L2"] = ("FAIL", f"drills {bad} off-list") if bad else ("PASS", "all ids on list")

    # L3 — count
    n = len(song.drills)
    out["L3"] = (("PASS" if 2 <= n <= 4 else "FAIL"), f"{n} drills")

    # L4 — does a song_specific=true drill actually DERIVE from the song?
    # Rewritten by FLE-123; the rule lives in l4_violations(). The old title/artist
    # substring IFF is the exact test the prompt forbids in as many words.
    #
    # A song whose drills ALL declare false is N/A, not PASS — see main(), which
    # keeps N/A out of the denominator rather than banking an untested gate as a
    # pass. All 16 drills of the 2026-09-24 baseline were false, so the 5/5 L4 that
    # run reported was vacuous.
    checked = sum(1 for d in song.drills if d.song_specific)
    bad4 = [h for d in song.drills for h in l4_violations(song, d)]
    if not checked:
        out["L4"] = ("N/A", "no drill declared song_specific=true")
    elif bad4:
        out["L4"] = ("FAIL", "; ".join(bad4))
    else:
        out["L4"] = ("PASS", f"{checked} song_specific drill(s) quote the main tab")

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
    passed = total = skipped = 0
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
            # FLE-45: A1 is counted now. FLE-123: an N/A gate had nothing to grade,
            # so it scores in NEITHER half — counting it as a failure would punish
            # the model for a case that never arose, and counting it as a pass would
            # bank an untested gate. It is reported either way.
            if status != "N/A":
                total += 1
                passed += status == "PASS"
            else:
                skipped += 1
            print(f"   {g}: {status:4} — {ev}")
    tail = f" ({skipped} N/A, excluded)" if skipped else ""
    print(f"\n{'=' * 72}\nTOTAL: {passed}/{total} gates passed{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
