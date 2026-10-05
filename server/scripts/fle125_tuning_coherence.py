"""FLE-125 — can a row's OWN content tell us its declared `tab.tuning` is wrong?

Read-only. No LLM, no DB writes, no generation, zero spend.

    server/.venv/bin/python server/scripts/fle125_tuning_coherence.py --self-test

    PGURL=$(railway variables --service Postgres --json \
            | python3 -c "import json,sys;print(json.load(sys.stdin)['DATABASE_PUBLIC_URL'])") \
      server/.venv/bin/python server/scripts/fle125_tuning_coherence.py --from-db

    server/.venv/bin/python server/scripts/fle125_tuning_coherence.py --emitted path.json


WHY THIS FILE EXISTS, AND WHAT IT DELIBERATELY DOES NOT DO
----------------------------------------------------------
FLE-125 asked for a tuning check "that needs no ground truth": does the declared
`tuning` agree with the key implied by the note content and by the declared
chords? The issue called it "probably the highest-value item here, since it works
on every song."

I built that check first and it is WRONG. Run it on the nine cached prod
breakdowns and it flags four songs whose tuning is *verified correct* against the
catalog — Lenny, Pride and Joy, Little Wing, Eruption, every one `eb_standard`.
4 of 4 false positives on the E-flat songs, i.e. it does not fire on wrong
tunings, it fires on down-tuned songs.

The reason is a notation convention, not a bug:

    ON A UNIFORMLY DOWN-TUNED GUITAR, CHORD NAMES ARE SHAPE NAMES.

SRV's "Lenny" is cut in E-flat standard. Everyone — SRV included — writes the
changes as E / A / B7 and plays open E / A / B7 SHAPES. The sounding pitches are
E-flat / A-flat / B-flat 7. The cached row says exactly that: tuning E-flat,
`songs.key` 'E', chords literally named "E (open)", "A (open)", "B7 (open)". The
tuning is right, the key label is a shape label, and a checker that insists the
chord name equal the sounding pitch calls the whole thing broken.

So the check here is in two steps, and the first step EXISTS ONLY TO ABSORB THAT
CONVENTION so the second step can do the real work:

  Step 1  Find T* = the uniform transposition of the declared tuning at which the
          chord DIAGRAMS best match their own NAMES.
            T* == 0  -> names are sounding pitches.
            T* != 0  -> names are shape names. Legitimate, and T* should come out
                        equal to minus the tuning's offset from standard.
          T* ALONE PROVES NOTHING ABOUT TUNING. Lenny (correct E-flat, shape
          names) and song 73 (standard record mislabelled E-flat) have the
          IDENTICAL signature here: T* = +1, all chords matching. That identity is
          the whole reason step 1 cannot be the gate.

  Step 2  At that SAME T*, how much of the tab sits outside the pitch classes the
          declared chords assert? Chords and melody have to be in one key. This
          is what actually separates song 73: whichever frame you read it in, its
          melody and its chords are in different keys.

Measured on the nine cached prod rows (--from-db, 2026-10-05):

      id  song                          T*  chords@T*  notes outside
      60  Lenny                         +1   4ok/0bad    19/64  = 29.7%
      71  Pride and Joy                 +1   2ok/1bad    13/110 = 11.8%
      73  Cause We've Ended as Lovers   +1   3ok/0bad    32/33  = 97.0%  <-- FAIL
      74  Comfortably Numb              +0   4ok/0bad     5/29  = 17.2%
      76  Big Love                      +0   3ok/0bad     1/64  =  1.6%
     103  Chan Chan                     +0   3ok/0bad     2/31  =  6.5%
     106  Little Wing                   +1   5ok/1bad     6/98  =  6.1%
     107  Eruption                      +1   3ok/0bad    25/60  = 41.7%
     108  Kashmir                       +0   3ok/0bad     0/32  =  0.0%

Eight rows span 0.0%-41.7%. Song 73 is 97.0%. Nothing lands between 42% and 97%,
so THRESHOLD is set at 70% — roughly centred in an empty gap rather than tuned to
the one positive. One true positive and zero false positives over nine rows is not
a validated precision, which is why this returns a REVIEW FLAG and not a hard
gate. See `review_flag` in the result.


THE LIMIT THAT MATTERS MOST — READ BEFORE BUILDING A GATE ON THIS
-----------------------------------------------------------------
This check CANNOT verify a tuning. It detects that a row CONTRADICTS ITSELF.

A row that is globally transposed but internally consistent — tab notes, chord
names and chord shapes all coherently in E-flat for a record cut in standard —
passes every step here with a perfect score, because there is nothing inside it to
disagree with. FLE-125's residual risk is exactly that row, and no amount of
content analysis closes it. Song 73 is caught only because it is ALSO incoherent
(melody in G Dorian, chords in B-flat minor, `songs.key` 'Bm', four unreconciled
frames), which the issue correctly called luck.

Tuning ground truth is irreducible. It comes from the catalog or it is a default
we own; it never comes from the content and it never comes from the model. That
conclusion, not this script, is FLE-125's deliverable.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

CHROM = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# string number -> open midi, standard tuning. 1 = high e ... 6 = low E,
# matching Note.string in app/models/song.py and _open_midi in tab_verification.
STD_MIDI = {6: 40, 5: 45, 4: 50, 3: 55, 2: 59, 1: 64}

NOTE_PC = {
    "C": 0, "B#": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4,
    "FB": 4, "F": 5, "E#": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8,
    "A": 9, "A#": 10, "BB": 10, "B": 11, "CB": 11,
}

# Intervals from the root that a chord name ASSERTS are present.
CHORD_QUALITY = {
    "": {0, 4, 7}, "maj": {0, 4, 7}, "m": {0, 3, 7}, "min": {0, 3, 7},
    "5": {0, 7}, "sus2": {0, 2, 7}, "sus4": {0, 5, 7},
    "7": {0, 4, 7, 10}, "maj7": {0, 4, 7, 11}, "m7": {0, 3, 7, 10},
    "m7b5": {0, 3, 6, 10}, "dim": {0, 3, 6}, "dim7": {0, 3, 6, 9},
    "aug": {0, 4, 8}, "6": {0, 4, 7, 9}, "m6": {0, 3, 7, 9},
    "9": {0, 4, 7, 10, 2}, "m9": {0, 3, 7, 10, 2}, "maj9": {0, 4, 7, 11, 2},
    "add9": {0, 4, 7, 2}, "7sus4": {0, 5, 7, 10},
    "11": {0, 4, 7, 10, 2, 5}, "m11": {0, 3, 7, 10, 2, 5},
    "13": {0, 4, 7, 10, 2, 9}, "7#9": {0, 4, 7, 10, 3}, "7b9": {0, 4, 7, 10, 1},
}

# Centred in the empty 42%-97% gap, not fitted to the single positive.
THRESHOLD = 70.0

# Transpositions searched in step 1. +/-3 semitones covers every uniform shift in
# ALLOWED_TUNINGS (E-flat is -1, drop-C's whole-tune is -2) with room to spare.
T_RANGE = range(-3, 4)


def open_midi(tuning: list[str], shift: int = 0) -> dict[int, int]:
    """`tuning` is a 6-list low->high; map string number 6..1 to open midi.

    Mirrors _open_midi in app/ai/tab_verification.py: each name is placed in the
    octave nearest that string's standard pitch, so a tuning array cannot
    silently reorder the strings.
    """
    if not tuning or len(tuning) != 6:
        tuning = ["E", "A", "D", "G", "B", "E"]
    out: dict[int, int] = {}
    for i, name in enumerate(tuning):
        strnum = 6 - i
        std = STD_MIDI[strnum]
        pc = NOTE_PC.get(str(name).strip().upper())
        if pc is None:
            out[strnum] = std + shift
            continue
        out[strnum] = min(
            (pc + 12 * octv for octv in range(1, 9)), key=lambda m: abs(m - std)
        ) + shift
    return out


def parse_chord(name: str):
    """'Em7 (open)' -> (4, {0,3,7}). None when unparseable — counted, not guessed.

    A silently-misparsed chord name makes the whole check vacuous, so anything
    not understood is reported as `unparsed` rather than folded into a score.
    Scale names in the chord list ("A minor pentatonic (XII position)") land here
    on purpose: they assert no chord.
    """
    s = str(name or "").split("(")[0].strip()
    s = s.replace("minor", "m").replace("major", "maj")
    if not s:
        return None
    if "/" in s:                      # slash chord: the name's root still rules
        s = s.split("/", 1)[0].strip()
    i = 2 if len(s) > 1 and s[1] in "#b" else 1
    root = NOTE_PC.get(s[:i].upper())
    if root is None:
        return None
    quality = CHORD_QUALITY.get(s[i:].strip())
    if quality is None:
        return None
    return root, quality


def _played(chord: dict) -> list[dict]:
    """Positions actually sounding. fret -1 means 'string not played' (§7.1.3)."""
    return [
        p for p in (chord.get("positions") or [])
        if p.get("fret") is not None and int(p["fret"]) >= 0
        and int(p.get("string", 0)) in STD_MIDI
    ]


def score_chords(chords: list[dict], tuning: list[str], shift: int):
    """How many chord diagrams match their own name, reading `tuning` + `shift`.

    A diagram matches when its LOWEST sounding pitch class is the named root, or
    — for an inversion or a rootless voicing — any pitch class the name asserts.
    """
    ok = bad = unparsed = 0
    om = open_midi(tuning, shift)
    for chord in chords:
        parsed = parse_chord(chord.get("name"))
        positions = _played(chord)
        if parsed is None or not positions:
            unparsed += 1
            continue
        root, quality = parsed
        low = min(om[int(p["string"])] + int(p["fret"]) for p in positions) % 12
        if low == root or low in {(root + iv) % 12 for iv in quality}:
            ok += 1
        else:
            bad += 1
    return ok, bad, unparsed


def tab_pitch_classes(tab: dict, tuning: list[str], shift: int):
    """Sounding pitch classes of every tab note, with counts.

    Bent notes are skipped: a bend's sounding pitch is not an integer fret, so
    scoring it for pitch would make every correct bend read as a foreign note
    (same rule as FLE-107's scorer, §5). `capo` is added because fret numbers are
    capo-relative while `tuning` means the open strings BEFORE the capo.
    """
    om = open_midi(tuning, shift)
    capo = int(tab.get("capo") or 0)
    counts: collections.Counter = collections.Counter()
    for measure in tab.get("measures") or []:
        for beat in measure.get("beats") or []:
            for note in beat.get("notes") or []:
                if note.get("bend") or note.get("rest"):
                    continue
                strnum, fret = note.get("string"), note.get("fret")
                if strnum not in om or fret is None:
                    continue
                counts[(om[strnum] + int(fret) + capo) % 12] += 1
    return counts


def check(breakdown: dict) -> dict:
    """The two-step check. Returns a result dict; never raises on odd content."""
    tab = (breakdown or {}).get("tab") or {}
    tuning = tab.get("tuning") or []
    chords = (breakdown or {}).get("chords") or []

    # --- step 1: which frame do the chord diagrams and their names agree in? ---
    scores = {T: score_chords(chords, tuning, T) for T in T_RANGE}
    # Prefer the T with the best ok-minus-bad; tie-break toward no transposition,
    # so a row that reads fine as sounding pitches is never called shape-named.
    t_star = max(scores, key=lambda T: (scores[T][0] - scores[T][1], -abs(T)))
    ok, bad, unparsed = scores[t_star]

    # --- step 2: at that frame, are the melody and the chords in one key? ------
    asserted: set[int] = set()
    for chord in chords:
        parsed = parse_chord(chord.get("name"))
        if parsed:
            asserted |= {(parsed[0] + iv) % 12 for iv in parsed[1]}

    counts = tab_pitch_classes(tab, tuning, t_star)
    total = sum(counts.values())
    outside = sum(n for pc, n in counts.items() if pc not in asserted)
    pct = (100.0 * outside / total) if total else 0.0

    # No chords to compare against, or no notes: the check is VACUOUS here, which
    # is a different answer from "coherent" and must not be reported as a pass.
    inconclusive = not asserted or total == 0

    return {
        "declared_tuning": list(tuning),
        "t_star": t_star,
        "names_are": "sounding" if t_star == 0 else "shape",
        "chords_ok": ok,
        "chords_bad": bad,
        "chords_unparsed": unparsed,
        "notes_outside": outside,
        "notes_total": total,
        "outside_pct": round(pct, 1),
        "asserted_pcs": [CHROM[p] for p in sorted(asserted)],
        "tab_pcs": [CHROM[p] for p, _ in counts.most_common()],
        "inconclusive": inconclusive,
        "review_flag": (not inconclusive) and pct > THRESHOLD,
    }


# ---------------------------------------------------------------------------
# Self-test — synthetic rows, so the thresholds are exercised without the DB
# ---------------------------------------------------------------------------

def _bar(*sf):
    return {"time_signature": "4/4",
            "beats": [{"notes": [{"string": s, "fret": f, "duration": "quarter"}]}
                      for s, f in sf]}


def _self_test() -> int:
    EB = ["Eb", "Ab", "Db", "Gb", "Bb", "Eb"]
    STD = ["E", "A", "D", "G", "B", "E"]
    # An open E shape: 022100 on strings 6..1.
    E_SHAPE = {"name": "E (open)", "base_fret": 0, "positions": [
        {"string": 6, "fret": 0}, {"string": 5, "fret": 2}, {"string": 4, "fret": 2},
        {"string": 3, "fret": 1}, {"string": 2, "fret": 0}, {"string": 1, "fret": 0}]}
    # An open A shape: x02220.
    A_SHAPE = {"name": "A (open)", "base_fret": 0, "positions": [
        {"string": 6, "fret": -1}, {"string": 5, "fret": 0}, {"string": 4, "fret": 2},
        {"string": 3, "fret": 2}, {"string": 2, "fret": 2}, {"string": 1, "fret": 0}]}

    cases = []

    # 1. Standard tuning, sounding names, melody inside the chords -> clean.
    cases.append(("standard/coherent", {
        "chords": [E_SHAPE, A_SHAPE],
        "tab": {"tuning": STD, "measures": [_bar((6, 0), (5, 2), (4, 2), (3, 1))]},
    }, dict(t_star=0, review_flag=False)))

    # 2. E-flat tuning with SHAPE names — the four-false-positive case. Must come
    #    out T*=+1 and MUST NOT be flagged.
    cases.append(("eb/shape-names", {
        "chords": [E_SHAPE, A_SHAPE],
        "tab": {"tuning": EB, "measures": [_bar((6, 0), (5, 2), (4, 2), (3, 1))]},
    }, dict(t_star=1, review_flag=False)))

    # 3. Chords in one key, melody in a flatly different one -> the song-73 shape.
    #    Chords assert E/A triads; the melody is a chromatic run that mostly
    #    avoids them.
    cases.append(("melody vs chords", {
        "chords": [E_SHAPE, A_SHAPE],
        "tab": {"tuning": STD, "measures": [_bar((1, 1), (1, 6), (1, 9), (1, 2))]},
    }, dict(review_flag=True)))

    # 4. No chords at all -> inconclusive, NOT a pass.
    cases.append(("no chords", {
        "chords": [],
        "tab": {"tuning": STD, "measures": [_bar((6, 0), (5, 2))]},
    }, dict(inconclusive=True, review_flag=False)))

    # 5. No notes -> inconclusive.
    cases.append(("no notes", {
        "chords": [E_SHAPE], "tab": {"tuning": STD, "measures": []},
    }, dict(inconclusive=True, review_flag=False)))

    # 6. A capo must not shift the verdict: capo 2 with every fret dropped by 2
    #    sounds identical, so the melody stays inside the same chords.
    cases.append(("capo invariance", {
        "chords": [{"name": "F#m", "positions": [
            {"string": 6, "fret": 2}, {"string": 5, "fret": 4}, {"string": 4, "fret": 4},
            {"string": 3, "fret": 2}, {"string": 2, "fret": 2}, {"string": 1, "fret": 2}]}],
        "tab": {"tuning": STD, "capo": 2,
                "measures": [_bar((6, 0), (5, 2), (4, 2), (3, 0))]},
    }, dict(review_flag=False)))

    # 7. A scale name in the chord list is unparsed, not scored as a bad chord.
    cases.append(("scale name unparsed", {
        "chords": [E_SHAPE, {"name": "A minor pentatonic (XII position)",
                             "positions": [{"string": 1, "fret": 12}]}],
        "tab": {"tuning": STD, "measures": [_bar((6, 0), (5, 2), (4, 2), (3, 1))]},
    }, dict(chords_unparsed=1, review_flag=False)))

    # 8. Unknown tuning names must not crash and must not flag.
    cases.append(("garbage tuning", {
        "chords": [E_SHAPE],
        "tab": {"tuning": ["X", "Y", "Z", "Q", "W", "V"],
                "measures": [_bar((6, 0), (5, 2), (4, 2), (3, 1))]},
    }, dict(review_flag=False)))

    passed = failed = 0
    for label, breakdown, want in cases:
        got = check(breakdown)
        bad = {k: (want[k], got[k]) for k in want if got[k] != want[k]}
        if bad:
            failed += 1
            print(f"  FAIL  {label}: {bad}")
            print(f"        full: {got}")
        else:
            passed += 1
            print(f"  ok    {label}: T*={got['t_star']:+d} "
                  f"{got['chords_ok']}ok/{got['chords_bad']}bad "
                  f"outside {got['notes_outside']}/{got['notes_total']} "
                  f"= {got['outside_pct']}%"
                  f"{' INCONCLUSIVE' if got['inconclusive'] else ''}"
                  f"{' FLAG' if got['review_flag'] else ''}")
    print(f"\nself-test: {passed}/{passed + failed}")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _report(rows: list[dict]) -> int:
    hdr = (f"{'id':>4}  {'song':31} {'catalog':12} {'decl':5} {'T*':>3} "
           f"{'names':>8}  {'chords@T*':>12}  {'notes outside':>15}  verdict")
    print(hdr)
    print("-" * len(hdr))
    flagged = []
    for row in rows:
        res = check(row.get("breakdown") or {})
        decl = (res["declared_tuning"] or ["?"])[0]
        if res["inconclusive"]:
            verdict = "inconclusive"
        elif res["review_flag"]:
            verdict = "REVIEW <<<"
            flagged.append(row)
        else:
            verdict = "coherent"
        print(f"{row.get('id', '?'):>4}  {str(row.get('title', ''))[:30]:31} "
              f"{str(row.get('catalog_tuning') or '(no row)'):12} {str(decl):5} "
              f"{res['t_star']:>+3} {res['names_are']:>8}  "
              f"{res['chords_ok']:>3}ok/{res['chords_bad']}bad/{res['chords_unparsed']}unp  "
              f"{res['notes_outside']:>5}/{res['notes_total']:<4} "
              f"= {res['outside_pct']:5.1f}%  {verdict}")
    print(f"\n{len(rows)} rows, {len(flagged)} flagged for review "
          f"(threshold {THRESHOLD}% of notes outside the declared chords).")
    for row in flagged:
        res = check(row["breakdown"])
        print(f"\n  {row.get('id')} {row.get('title')}")
        print(f"    declared tuning   {res['declared_tuning']}")
        print(f"    chords assert     {res['asserted_pcs']}")
        print(f"    tab plays         {res['tab_pcs']}")
        print(f"    -> at T*={res['t_star']:+d} the chord names match their shapes "
              f"({res['chords_ok']}ok/{res['chords_bad']}bad) but "
              f"{res['outside_pct']}% of the melody is outside those chords.")
        print("    This row contradicts itself. It does NOT by itself prove the "
              "tuning is wrong —")
        print("    only that notes, chord names and tuning cannot all be right. "
              "Needs ground truth.")
    return 0


def _from_db() -> int:
    url = os.environ.get("PGURL") or os.environ.get("DATABASE_URL")
    if not url:
        print("set PGURL (see the module docstring)", file=sys.stderr)
        return 2
    import psycopg2                          # same driver as fle107_score_tab
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    # READ-ONLY. One SELECT, no writes, no generation calls.
    sql = """
        SELECT s.id, s.title, s.artist, s.key, c.tuning, s.breakdown
        FROM songs s
        LEFT JOIN song_catalog c
          ON lower(c.title) = lower(s.title) AND lower(c.artist) = lower(s.artist)
        WHERE s.breakdown_generated_at IS NOT NULL
        ORDER BY s.id
    """
    with psycopg2.connect(url) as conn:      # SELECT only. No writes.
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = [
                {"id": r[0], "title": r[1], "artist": r[2], "songs_key": r[3],
                 "catalog_tuning": r[4], "breakdown": r[5]}
                for r in cur.fetchall()
            ]
    return _report(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--from-db", action="store_true")
    ap.add_argument("--emitted", metavar="PATH",
                    help="JSON: a breakdown, a list of them, or JSONL")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if args.from_db:
        return _from_db()
    if args.emitted:
        text = open(args.emitted).read().strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:       # JSONL
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        if isinstance(data, dict):
            data = [data]
        rows = [
            d if "breakdown" in d else {"id": d.get("id", i), "title": d.get("title", ""),
                                        "breakdown": d}
            for i, d in enumerate(data)
        ]
        return _report(rows)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
