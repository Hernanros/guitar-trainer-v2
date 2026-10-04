"""FLE-102 — tab audit: render every cached tab and run the mechanical checks.

Read-only, zero spend, no LLM. Companion to fle89_quality_probe.py; that script
answered "is the tuning frame right" (it is). This one asks the harder question:
are the NOTES right?

    PGURL=$(railway variables --service Postgres --json \
            | python3 -c "import json,sys;print(json.load(sys.stdin)['DATABASE_PUBLIC_URL'])") \
      server/.venv/bin/python server/scripts/fle102_tab_audit.py

Two kinds of output, deliberately kept apart:

  RENDER   — ASCII tab + pitch spelling per beat, so a human with the record in
             their ear can judge the notes. No machine can do this part; see the
             audit writeup for the by-ear verdicts.

  CHECKS   — defects that need no recording to call, because they are wrong
             against music notation or against the guitar itself:

   C1 duration/meter   do a measure's beat durations sum to its time signature?
   C2 one-note-per-str two notes on the same string in one beat is unplayable
   C3 fret span        a non-open fretted span > 5 frets inside one beat
   C4 uniform duration every note in a measure carrying the same duration is the
                       signature of a model emitting a grid, not a rhythm
   C5 beat granularity a "beat" holding a partial chord that completes on the
                       next beat — the voicing is never actually sounded
   C6 self-consistency a tab note outside the scale implied by the breakdown's
                       OWN chord list. Needs no reference recording: the tab and
                       the chord diagrams ship together and must agree.
   C7 template reuse   a measure whose shape (string set + intervals above the
                       bass, per beat) also appears in a DIFFERENT song. One
                       house pattern slid around the neck is not a transcription.

Each check is counted per measure so the accuracy number has a stated unit.
"""
import os, json, psycopg2
from collections import Counter

# --- pitch model ----------------------------------------------------------
CHROM = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
# midi numbers for standard tuning, string 6 (low) .. string 1 (high)
STD_MIDI = {6: 40, 5: 45, 4: 50, 3: 55, 2: 59, 1: 64}
NOTE_PC = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
           "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10,
           "BB": 10, "B": 11}

DUR = {"whole": 4.0, "half": 2.0, "dotted-half": 3.0, "dotted_half": 3.0,
       "quarter": 1.0, "dotted-quarter": 1.5, "dotted_quarter": 1.5,
       "eighth": 0.5, "dotted-eighth": 0.75, "dotted_eighth": 0.75,
       "sixteenth": 0.25, "thirty-second": 0.125, "triplet-eighth": 1/3,
       "eighth-triplet": 1/3, "quarter-triplet": 2/3, "sixteenth-triplet": 1/6}


def open_midi(tuning):
    """tuning is a 6-list low->high; map to string number 6..1 midi."""
    if not tuning or len(tuning) != 6:
        return dict(STD_MIDI)
    # anchor each course to the standard octave nearest its standard pitch
    out = {}
    for i, name in enumerate(tuning):
        strnum = 6 - i
        std = STD_MIDI[strnum]
        pc = NOTE_PC.get(name.strip().upper())
        if pc is None:
            out[strnum] = std
            continue
        cands = [pc + 12 * o for o in range(1, 9)]
        out[strnum] = min(cands, key=lambda m: abs(m - std))
    return out


def pitch(strnum, fret, om):
    m = om.get(strnum, STD_MIDI.get(strnum, 40)) + int(fret)
    return f"{CHROM[m % 12]}{m // 12 - 1}", m


# --- render ---------------------------------------------------------------
def render(tab, width=4):
    """ASCII tab, measures laid out `width` per system."""
    ms = tab.get("measures") or []
    om = open_midi(tab.get("tuning"))
    names = [t for t in (tab.get("tuning") or ["E", "A", "D", "G", "B", "e"])]
    lines = []
    for blk in range(0, len(ms), width):
        group = ms[blk:blk + width]
        rows = {s: "" for s in range(1, 7)}
        for mi, m in enumerate(group):
            beats = m.get("beats") or []
            for bt in beats:
                cell = {}
                for n in bt.get("notes") or []:
                    s = n.get("string")
                    cell.setdefault(s, []).append(str(n.get("fret")))
                w = max([len("/".join(v)) for v in cell.values()] or [1])
                for s in range(1, 7):
                    tok = "/".join(cell.get(s, [])) or "-"
                    rows[s] += tok.ljust(w, "-") + "-"
            for s in range(1, 7):
                rows[s] += "|"
        hdr = "  m" + "  m".join(str(blk + i + 1) for i in range(len(group)))
        lines.append(hdr)
        for s in range(1, 7):                       # string 1 printed on top
            lines.append(f"{names[6 - s]:>2}|{rows[s]}")
        lines.append("")
    return "\n".join(lines)


def spell(tab):
    """pitch spelling per beat, for ear-checking against the record."""
    om = open_midi(tab.get("tuning"))
    out = []
    for mi, m in enumerate(tab.get("measures") or [], 1):
        cells = []
        for bi, bt in enumerate(m.get("beats") or [], 1):
            ns = bt.get("notes") or []
            ps = [pitch(n.get("string"), n.get("fret"), om) for n in ns]
            ps.sort(key=lambda x: x[1])
            durs = sorted({n.get("duration") for n in ns})
            cells.append(f"b{bi}[{' '.join(p[0] for p in ps)}]{'/'.join(d or '?' for d in durs)}")
        out.append(f"  m{mi} {m.get('time_signature','?')}: " + "  ".join(cells))
    return "\n".join(out)


# --- checks ---------------------------------------------------------------
def check_measure(m):
    """return list of (code, detail) defects for one measure."""
    bad = []
    beats = m.get("beats") or []
    ts = m.get("time_signature") or "4/4"
    try:
        num, den = (int(x) for x in ts.split("/"))
        want = num * 4.0 / den
    except Exception:
        want = 4.0

    # C1 — do durations sum to the bar? one duration per beat (the longest note
    # in it) is the generous reading; use max so a chord isn't counted 6x.
    total = 0.0
    unknown = False
    for bt in beats:
        ds = [DUR.get((n.get("duration") or "").lower()) for n in (bt.get("notes") or [])]
        ds = [d for d in ds if d]
        if not ds:
            unknown = True
            continue
        total += max(ds)
    if not unknown and abs(total - want) > 1e-6:
        bad.append(("C1", f"durations sum to {total:g} beats, bar is {ts} ({want:g})"))

    # C2 — same string twice in one beat
    for bi, bt in enumerate(beats, 1):
        seen = Counter(n.get("string") for n in (bt.get("notes") or []))
        dup = [s for s, c in seen.items() if c > 1]
        if dup:
            bad.append(("C2", f"b{bi} string(s) {dup} fretted twice in one beat"))

    # C3 — fretted span inside one beat
    for bi, bt in enumerate(beats, 1):
        fr = [int(n.get("fret")) for n in (bt.get("notes") or []) if int(n.get("fret") or 0) > 0]
        if fr and max(fr) - min(fr) > 5:
            bad.append(("C3", f"b{bi} fretted span {min(fr)}-{max(fr)} = {max(fr)-min(fr)} frets"))

    # C4 — every note in the measure the same duration
    alld = {(n.get("duration") or "") for bt in beats for n in (bt.get("notes") or [])}
    if len(alld) == 1 and len(beats) > 2:
        bad.append(("C4", f"every note in the bar is a {alld.pop()!r} — a grid, not a rhythm"))

    # C5 — adjacent beats whose note sets are disjoint by string and which
    # together form one chord shape: the voicing is never sounded whole.
    for bi in range(len(beats) - 1):
        a = {n.get("string"): int(n.get("fret")) for n in (beats[bi].get("notes") or [])}
        b = {n.get("string"): int(n.get("fret")) for n in (beats[bi + 1].get("notes") or [])}
        if len(a) >= 2 and len(b) >= 2 and not (set(a) & set(b)) and len(set(a) | set(b)) >= 5:
            bad.append(("C5", f"b{bi+1}/b{bi+2} are disjoint halves of one {len(a)+len(b)}-string voicing"))
    return bad


# --- C6: scale implied by the breakdown's own chord list ------------------
QUAL = {            # intervals above the root, by chord-name suffix
    "maj7": (0, 4, 7, 11), "m7b5": (0, 3, 6, 10), "m7": (0, 3, 7, 10),
    "7#9": (0, 4, 7, 10, 3), "7b9": (0, 4, 7, 10, 1), "9": (0, 4, 7, 10, 2),
    "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9), "sus4": (0, 5, 7),
    "sus2": (0, 2, 7), "5": (0, 7), "dim": (0, 3, 6), "aug": (0, 4, 8),
    "7": (0, 4, 7, 10), "m": (0, 3, 7), "": (0, 4, 7),
}


def chord_pcs(name):
    """pitch classes a chord name asserts; None if unparseable."""
    s = (name or "").split("(")[0].strip().replace("minor", "m").replace("major", "maj")
    if not s:
        return None
    root = s[0].upper()
    i = 1
    if i < len(s) and s[i] in "#b":
        root += s[i]
        i += 1
    pc = NOTE_PC.get(root.upper())
    if pc is None:
        return None
    suf = s[i:].strip()
    if suf.lower().startswith("m") and not suf.lower().startswith("maj"):
        key = "m7" if "7" in suf else ("m6" if "6" in suf else "m")
    else:
        key = next((k for k in ("maj7", "m7b5", "7#9", "7b9", "sus4", "sus2",
                                "dim", "aug", "9", "7", "6", "5") if k in suf.lower()), "")
    return {(pc + iv) % 12 for iv in QUAL[key]}


def tuning_offset(tuning):
    """semitones the whole guitar is shifted, or None if not a uniform shift.

    Chord NAMES in these breakdowns describe shapes ("E (open)"), which in Eb
    tuning sound a semitone lower than the name. So a note must be checked in
    both frames before it can be called wrong; only a note outside BOTH is a
    real wrong note. DADGAD is not a uniform shift, so no second frame exists.
    """
    om = open_midi(tuning)
    deltas = {om[s] - STD_MIDI[s] for s in range(1, 7)}
    return deltas.pop() if len(deltas) == 1 else None


def check_c6(bd):
    """notes outside the breakdown's own chord tones in BOTH reference frames."""
    tab = bd.get("tab") or {}
    om = open_midi(tab.get("tuning"))
    allowed, unparsed = set(), []
    for c in bd.get("chords") or []:
        pcs = chord_pcs(c.get("name"))
        if pcs is None:
            unparsed.append(c.get("name"))
        else:
            allowed |= pcs
    if not allowed:
        return {}, unparsed, allowed, None
    off = tuning_offset(tab.get("tuning"))
    frames = [0] if not off else [0, -off]   # sounding, and shape-name frame
    bad = {}
    for mi, m in enumerate(tab.get("measures") or [], 1):
        for bi, bt in enumerate(m.get("beats") or [], 1):
            for n in bt.get("notes") or []:
                nm, midi = pitch(n.get("string"), n.get("fret"), om)
                if all((midi + f) % 12 not in allowed for f in frames):
                    bad.setdefault(mi, []).append(f"b{bi} {nm}")
    return bad, unparsed, allowed, off


# --- C7: cross-song template reuse ---------------------------------------
def measure_sig(m):
    """transposition-invariant contour: (string offsets, semitone offsets) per
    beat, relative to the measure's first note.

    Returns None for measures too plain to be evidence of anything — a bar of
    four single notes matches every other such bar, so counting those as
    "reuse" would be noise dressed as a finding. A signature must carry at
    least 3 distinct pitch offsets to qualify.
    """
    om = open_midi((m.get("_tuning")))
    out, base, offs = [], None, set()
    for bt in m.get("beats") or []:
        cell = []
        for n in bt.get("notes") or []:
            _, midi = pitch(n.get("string"), n.get("fret"), om)
            if base is None:
                base = midi
            cell.append((n.get("string"), midi - base))
            offs.add(midi - base)
        out.append(tuple(sorted(cell)))
    if len(offs) < 3:
        return None
    return tuple(out)


# --- main -----------------------------------------------------------------
def main():
    cur = psycopg2.connect(os.environ["PGURL"]).cursor()
    cur.execute("""select id,title,artist,created_at,breakdown from songs
      where breakdown->'tab'->'measures' is not null
        and jsonb_array_length(breakdown->'tab'->'measures') > 0
      order by created_at, id""")
    rows = cur.fetchall()
    tally = Counter()
    bars_total = bars_clean = 0
    summary = []

    # C7 needs the whole corpus first: which measure shapes recur across songs?
    sig_songs = {}
    for sid, title, artist, ca, bd in rows:
        tun = (bd.get("tab") or {}).get("tuning")
        for m in ((bd.get("tab") or {}).get("measures") or []):
            sig = measure_sig(dict(m, _tuning=tun))
            if sig is not None:
                sig_songs.setdefault(sig, set()).add(title)

    print(f"=== {len(rows)} cached tabs in prod ===\n")
    for sid, title, artist, ca, bd in rows:
        tab = bd.get("tab") or {}
        ms = tab.get("measures") or []
        print("=" * 78)
        print(f"[{sid}] {title} — {artist}")
        print(f"      cached {ca}  tuning={tab.get('tuning')}  measures={len(ms)}")
        print(f"      chords: {[c.get('name') for c in (bd.get('chords') or [])]}")
        print()
        print(render(tab))
        print(spell(tab))
        print()
        c6, unparsed, allowed, off = check_c6(bd)
        if allowed:
            frame = f"  (also checked +{-off:+d} shape-name frame)" if off else ""
            print(f"  chord tones asserted: {sorted(CHROM[p] for p in allowed)}{frame}"
                  f"{'  UNPARSED: ' + str(unparsed) if unparsed else ''}")
        songbad = []
        percode = Counter()
        for mi, m in enumerate(ms, 1):
            d = check_measure(m)
            if mi in c6:
                d.append(("C6", f"outside the breakdown's own chords: {', '.join(c6[mi])}"))
            sig = measure_sig(dict(m, _tuning=tab.get("tuning")))
            others = (sig_songs.get(sig, set()) - {title}) if sig else set()
            if others:
                d.append(("C7", f"same shape as {sorted(others)}"))
            bars_total += 1
            if d:
                songbad.append((mi, d))
                for code, _ in d:
                    tally[code] += 1
                    percode[code] += 1
                if all(c == "C4" for c, _ in d):
                    percode["clean_ex_C4"] += 1
            else:
                bars_clean += 1
                percode["clean_ex_C4"] += 1
        summary.append((title, len(ms), percode))
        if songbad:
            print("  CHECKS:")
            for mi, d in songbad:
                for code, detail in d:
                    print(f"    m{mi} {code}  {detail}")
        else:
            print("  CHECKS: clean")
        print()
    codes = ["C1", "C2", "C3", "C4", "C5", "C6", "C7"]
    print("=" * 78)
    print("PER-SONG BARS AFFECTED, by check\n")
    print(f"{'song':32}{'bars':>5}" + "".join(f"{c:>5}" for c in codes) + f"{'ex-C4 ok':>10}")
    for title, nb, pc in summary:
        print(f"{title[:31]:32}{nb:>5}"
              + "".join(f"{pc.get(c, 0) or '.':>5}" for c in codes)
              + f"{pc.get('clean_ex_C4', 0):>7}/{nb}")
    print()
    print(f"bars: {bars_total}   passing ALL mechanical checks: {bars_clean}"
          f"  ({100*bars_clean/max(bars_total,1):.0f}%)")
    exc4 = sum(pc.get("clean_ex_C4", 0) for _, _, pc in summary)
    print(f"                 passing all EXCEPT C4 (uniform grid): {exc4}"
          f"  ({100*exc4/max(bars_total,1):.0f}%)")
    print("defect counts by code (bars affected):", dict(sorted(tally.items())))
    print()
    print("C4 is listed separately because 'every note is an eighth' is a feel")
    print("judgement, not a notation error. The other six are errors on their face.")


if __name__ == "__main__":
    main()
