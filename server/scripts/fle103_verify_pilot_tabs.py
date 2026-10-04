"""FLE-103 — verify the hand-authored pilot set. Read-only, zero spend, no LLM, no DB.

    server/.venv/bin/python server/scripts/fle103_verify_pilot_tabs.py

This script is deliberately IGNORANT of the chord shapes in
fle103_build_pilot_set.py. It reads only {string, fret, duration} plus the
declared chord NAME, re-derives every pitch from the tuning array, and asks
whether the sounding notes actually spell the chord that is claimed. If I typed
a shape wrong, this is what catches it — the point is that my confidence is not
the evidence.

Checks (unit: the measure, so the number has a stated denominator)
-----------------------------------------------------------------
  V1 foreign note      a sounding pitch class outside the declared chord
  V2 third present     the third is in the voicing (FLE-102 T2: 6 of 10 cached
                       tabs contained no third ANYWHERE across 8 bars)
  V3 characteristic    the tone that defines the quality is present — b5 of a
                       m7b5, #11 of a 7#11, b9 of a 7b9, maj7 of a maj7
  V4 root present      the root is sounded somewhere in the voicing
  V5 meter             beat durations sum to the time signature
  V6 renderer width    <= 4 beats per measure (TabNotation.tsx MEASURE_WIDTH)
  V7 one note / string no string sounded twice in one beat (unplayable)
  V8 fret span         fretted span within a beat <= 5 (one hand position)

Plus two set-level reports:
  COVERAGE   for every player_level in [0.20, 1.00], how many of the set fall
             inside the selector's ABS(difficulty - player_level) <= 0.15
             window. A window of 0 is a dead app; a window of 1 is a user who
             sees the same song every day and whose reroll cannot move.
  SHAPE REUSE  measures identical across two songs. Reported as INFORMATIONAL,
             never as a failure — see the note at the bottom of main().
"""
import json
import pathlib
import sys
from collections import defaultdict

CHROM = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
PC = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
      "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10,
      "B": 11}
DUR = {"whole": 4.0, "half": 2.0, "dotted-half": 3.0, "quarter": 1.0,
       "dotted-quarter": 1.5, "eighth": 0.5, "dotted-eighth": 0.75,
       "sixteenth": 0.25, "triplet-eighth": 1 / 3, "eighth-triplet": 1 / 3}

# interval sets from the root. `third` names which interval must be present for
# V2; `char` is the quality-defining tone for V3.
QUALITIES = {
    "":       {"ivals": {0, 4, 7},            "third": 4,  "char": None},
    "m":      {"ivals": {0, 3, 7},            "third": 3,  "char": None},
    "7":      {"ivals": {0, 4, 7, 10},        "third": 4,  "char": 10},
    "maj7":   {"ivals": {0, 4, 7, 11},        "third": 4,  "char": 11},
    "m7":     {"ivals": {0, 3, 7, 10},        "third": 3,  "char": 10},
    "m7b5":   {"ivals": {0, 3, 6, 10},        "third": 3,  "char": 6},
    "7b9":    {"ivals": {0, 1, 4, 7, 10},     "third": 4,  "char": 1},
    "7#11":   {"ivals": {0, 4, 6, 7, 10},     "third": 4,  "char": 6},
    "6/9":    {"ivals": {0, 2, 4, 7, 9},      "third": 4,  "char": 2},
}


def parse_chord(name):
    """'D/F#' -> (2, '', 6);  'D6/9' -> (2, '6/9', None).

    The root comes off FIRST, because '6/9' is a quality containing a slash and
    would otherwise be read as a bass note named '9'.
    """
    i = 2 if len(name) > 1 and name[1] in "#b" else 1
    root = PC[name[:i].upper()]
    rest = name[i:]
    bass = None
    if rest not in QUALITIES and "/" in rest:
        rest, bass_s = rest.split("/", 1)
        bass = PC[bass_s.upper()]
    if rest not in QUALITIES:
        raise SystemExit(f"verifier does not know quality {rest!r} in {name!r}")
    return root, rest, bass


def open_pcs(tuning):
    """string number (6 low .. 1 high) -> open-string pitch class."""
    if len(tuning) != 6:
        raise SystemExit(f"tuning must be 6 strings, got {tuning!r}")
    # tuning is low->high; string 6 is lowest.
    return {6 - i: PC[n.upper()] for i, n in enumerate(tuning)}


def beat_duration(beat, where, fails):
    ds = {n["duration"] for n in beat["notes"]}
    if len(ds) > 1:
        fails.append(f"{where}: beat mixes durations {sorted(ds)} — "
                     f"simultaneous notes of one voicing must agree")
        return max(DUR.get(d, 0.0) for d in ds)
    d = ds.pop()
    if d not in DUR:
        fails.append(f"{where}: unknown duration {d!r}")
        return 0.0
    return DUR[d]


def main():
    root_dir = pathlib.Path(__file__).resolve().parents[1]
    path = root_dir / "data" / "verified_tabs" / "pilot_verified_set.json"
    if not path.exists():
        raise SystemExit(f"missing {path} — run fle103_build_pilot_set.py first")
    doc = json.loads(path.read_text())
    songs = doc["songs"]

    fails = []
    measures_checked = 0
    measures_with_chord = 0
    shape_index = defaultdict(list)

    for song in songs:
        tab = song["tab"]
        opens = open_pcs(tab["tuning"])
        seq = song["chord_sequence"]
        for mi, meas in enumerate(tab["measures"]):
            measures_checked += 1
            where = f"{song['title']} m{mi + 1}"
            beats = meas["beats"]

            # V6 renderer width
            if len(beats) > 4:
                fails.append(f"{where}: {len(beats)} beats > 4 — overflows "
                             f"TabNotation MEASURE_WIDTH")

            # V5 meter
            num, den = (int(x) for x in meas["time_signature"].split("/"))
            want = num * (4.0 / den)
            got = sum(beat_duration(b, where, fails) for b in beats)
            if abs(got - want) > 1e-6:
                fails.append(f"{where}: durations sum to {got} beats, "
                             f"{meas['time_signature']} wants {want}")

            for bi, beat in enumerate(beats):
                strings = [n["string"] for n in beat["notes"]]
                # V7 one note per string
                if len(strings) != len(set(strings)):
                    fails.append(f"{where} b{bi + 1}: string sounded twice")
                for n in beat["notes"]:
                    if not 1 <= n["string"] <= 6:
                        fails.append(f"{where} b{bi + 1}: string {n['string']}")
                    if n["fret"] < 0:
                        fails.append(f"{where} b{bi + 1}: fret {n['fret']} — "
                                     f"muted strings must be omitted, not -1")
                # V8 fret span
                fr = [n["fret"] for n in beat["notes"] if n["fret"] > 0]
                if fr and max(fr) - min(fr) > 5:
                    fails.append(f"{where} b{bi + 1}: fret span "
                                 f"{min(fr)}-{max(fr)} exceeds one position")

            # chord checks
            name = seq[mi] if mi < len(seq) else None
            if not name:
                continue
            measures_with_chord += 1
            rt, qual, bass = parse_chord(name)
            spec = QUALITIES[qual]
            allowed = {(rt + i) % 12 for i in spec["ivals"]}
            if bass is not None:
                allowed.add(bass)
            sounding = set()
            for beat in beats:
                for n in beat["notes"]:
                    sounding.add((opens[n["string"]] + n["fret"]) % 12)

            # V1 foreign notes
            foreign = sounding - allowed
            if foreign:
                fails.append(f"{where}: {name} sounds foreign "
                             f"{sorted(CHROM[p] for p in foreign)}")
            # V2 third
            if (rt + spec["third"]) % 12 not in sounding:
                fails.append(f"{where}: {name} contains no third "
                             f"({CHROM[(rt + spec['third']) % 12]} absent)")
            # V3 characteristic tone
            if spec["char"] is not None and (rt + spec["char"]) % 12 not in sounding:
                fails.append(f"{where}: {name} missing its defining tone "
                             f"{CHROM[(rt + spec['char']) % 12]}")
            # V4 root
            if rt not in sounding:
                fails.append(f"{where}: {name} has no root {CHROM[rt]}")

            key = json.dumps([[sorted((n["string"], n["fret"])
                                      for n in b["notes"])] for b in beats])
            shape_index[key].append((song["title"], mi + 1, name))

    # ---- coverage ----------------------------------------------------------
    diffs = [(s["catalog_difficulty"], s["title"]) for s in songs]
    print("=== SELECTOR COVERAGE "
          "(ABS(difficulty - player_level) <= 0.15) ===")
    holes, thin = [], []
    pl = 0.20
    while pl <= 1.0001:
        inw = [t for d, t in diffs if abs(d - pl) <= 0.15]
        if not inw:
            holes.append(pl)
        elif len(inw) == 1:
            thin.append((pl, inw[0]))
        pl = round(pl + 0.01, 2)

    def spans(xs):
        out = []
        for x in xs:
            if out and abs(x - out[-1][1] - 0.01) < 1e-9:
                out[-1][1] = x
            else:
                out.append([x, x])
        return out

    if holes:
        for a, b in spans(holes):
            print(f"  DEAD   player_level {a:.2f}-{b:.2f}: 0 songs in window")
    else:
        print("  no dead windows in [0.20, 1.00]")
    if thin:
        bytitle = defaultdict(list)
        for p, t in thin:
            bytitle[t].append(p)
        for t, ps in bytitle.items():
            print(f"  THIN   player_level {min(ps):.2f}-{max(ps):.2f}: "
                  f"only '{t}' — no reroll variety")
    print(f"  difficulties: {sorted(d for d, _ in diffs)}")

    # ---- shape reuse (informational) ---------------------------------------
    dupes = {k: v for k, v in shape_index.items()
             if len({t for t, _, _ in v}) > 1}
    print("\n=== CROSS-SONG IDENTICAL MEASURES (informational, not a failure) ===")
    if not dupes:
        print("  none")
    for v in dupes.values():
        names = {n for _, _, n in v}
        locs = ", ".join(f"{t} m{m}" for t, m, _ in v)
        print(f"  {sorted(names)} at {locs}")
    print("  NOTE for FLE-106: identical measures across songs are LEGITIMATE "
          "when two tunes genuinely share a chord. FLE-102's T7 template-reuse\n"
          "  heuristic would flag these as defects, so T7 must stay a human "
          "prompt and must never become an automated gate.")

    # ---- verdict -----------------------------------------------------------
    print(f"\n=== VERDICT ===")
    print(f"  measures checked: {measures_checked} "
          f"({measures_with_chord} carry a declared chord)")
    print(f"  songs: {len(songs)}")
    if fails:
        print(f"  FAILURES: {len(fails)}")
        for f in fails:
            print(f"    - {f}")
        return 1
    print("  all checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
