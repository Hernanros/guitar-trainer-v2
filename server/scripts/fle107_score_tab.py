"""FLE-107 — score an emitted tab against the committed ground truth. A DIFF, not an opinion.

Read-only, zero spend, no LLM. Four modes:

    # 1. does the committed ground truth hold up against itself?
    server/.venv/bin/python server/scripts/fle107_score_tab.py --self-test

    # 2. does the scorer actually catch anything? (mutation test)
    server/.venv/bin/python server/scripts/fle107_score_tab.py --mutation-test

    # 3. score a file of emitted breakdowns
    server/.venv/bin/python server/scripts/fle107_score_tab.py --emitted path.json

    # 4. score what is cached in prod (READ-ONLY SELECT, no writes, no generation)
    PGURL=$(railway variables --service Postgres --json \
            | python3 -c "import json,sys;print(json.load(sys.stdin)['DATABASE_PUBLIC_URL'])") \
      server/.venv/bin/python server/scripts/fle107_score_tab.py --from-db

This file is deliberately IGNORANT of fle107_build_regression_set.py
--------------------------------------------------------------------
It never imports it and never sees its SHAPES table. Everything here is
re-derived from `tuning` + `capo` + `fret` and checked against separately
hand-declared chord NAMES and SCALE DEGREES. The same separation caught a wrong
#11 in the FLE-103 pilot set — a shape I was confident about and whose
arithmetic I had not done. Confidence is not evidence; that is the whole design.

Three scores, never blended (and why)
-------------------------------------
FLE-102 measured 62% mechanical validity and 0% musical correctness on the SAME
corpus, and showed that a gate built on the first would have passed an invented
riff. Collapsing a note-level diff and a constraint check into one percentage
rebuilds that confusion. So:

    setup       (song x field) over tuning / capo / time_signature / feel / key
    note_exact  bars where I committed notes — a real diff
    constraint  bars where I committed assertions — "nothing disqualifying found"

The third is WEAKER EVIDENCE BY CONSTRUCTION. Do not report it as accuracy.
"""
import argparse
import json
import pathlib
import sys
from copy import deepcopy

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.ai.tab_verification import (  # noqa: E402
    CHROM, NOTE_PC, QUAL, _open_midi, _tuning_offset,
)
from app.models.song import (  # noqa: E402
    DURATION_BEATS, Tab, duration_beats, measure_beats, time_signature_beats,
)

GROUND_TRUTH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "data" / "regression_tabs" / "regression_set.json"
)

# --- the two tables production does not carry ------------------------------
# `QUAL` (app/ai/tab_verification.py) already holds the interval SETS, so they
# are imported rather than copied — FLE-115 collapsed three disagreeing duration
# tables and the same argument applies here. What QUAL does not carry is which
# interval is the THIRD and which is the quality-defining tone, because a live
# write gate does not need them. Declared here, keyed on QUAL's own keys.

THIRD = {"": 4, "m": 3, "m7": 3, "m6": 3, "m7b5": 3, "7": 4, "maj7": 4, "9": 4,
         "7#9": 4, "7b9": 4, "7#11": 4, "6": 4, "6/9": 4, "dim": 3, "aug": 4,
         "sus4": None, "sus2": None, "7sus4": None, "5": None}
# the tone without which the name is a different chord
CHAR = {"7": 10, "maj7": 11, "m7": 10, "m7b5": 6, "7b9": 1, "7#9": 3,
        "7#11": 6, "9": 10, "6": 9, "m6": 9, "6/9": 2, "sus4": 5, "sus2": 2,
        "7sus4": 10, "dim": 6, "aug": 8, "": None, "m": None, "5": 7}

# Suffix match order: most specific first, or "m7b5" is read as "m7" and
# "7sus4" as "7".
_SUFFIX_ORDER = ["m7b5", "7sus4", "maj7", "m7", "m6", "7#11", "7#9", "7b9",
                 "6/9", "sus4", "sus2", "dim", "aug", "9", "7", "6", "5", "m"]

DEGREE = {"1": 0, "b2": 1, "2": 2, "b3": 3, "3": 4, "4": 5, "b5": 6, "5": 7,
          "#5": 8, "b6": 8, "6": 9, "b7": 10, "7": 11, "9": 2, "b9": 1,
          "#9": 3, "11": 5, "#11": 6, "13": 9}

SUSTAINED = {"half", "dotted-half", "whole", "triplet-half"}


class Unscorable(Exception):
    """The candidate cannot be compared, which is not the same as being wrong."""


# --- pitch ----------------------------------------------------------------
def pc_of(name):
    pc = NOTE_PC.get((name or "").strip().upper())
    if pc is None:
        raise SystemExit(f"scorer does not know the note name {name!r}")
    return pc


def parse_chord(name):
    """'Em7' -> (4, 'm7'). Raises on anything it does not know, deliberately.

    Fails loudly rather than guessing: a silently-misparsed chord name turns the
    whole check vacuous, which is worse than a crash.
    """
    s = (name or "").split("(")[0].strip()
    if not s:
        raise SystemExit("empty chord name in the ground truth")
    i = 2 if len(s) > 1 and s[1] in "#b" else 1
    root, suffix = pc_of(s[:i]), s[i:].strip()
    if suffix == "":
        return root, ""
    for key in _SUFFIX_ORDER:
        if suffix.lower() == key:
            return root, key
    raise SystemExit(f"scorer does not know the chord quality {suffix!r} in {name!r}")


def frame(setup_or_tab_tuning, capo):
    """The shape->sounding shift. None when the tuning is not a uniform shift."""
    off = _tuning_offset(list(setup_or_tab_tuning))
    return None if off is None else off + int(capo or 0)


def sounding_pcs_of_notes(notes, om, capo, include_bent=False):
    """§5 — a bent note's sounding pitch is not an integer fret, so it is SKIPPED.

    Omitting this makes every correct bend read as a foreign note. It cuts both
    ways and that is accepted: a bend cannot be scored for pitch, only for
    existence (`contains_bend`).
    """
    out = []
    for nt in notes:
        if nt.get("bend") and not include_bent:
            continue
        out.append((om[nt["string"]] + int(capo or 0) + int(nt["fret"])) % 12)
    return out


def sounding_midi(nt, om, capo):
    return om[nt["string"]] + int(capo or 0) + int(nt["fret"])


def beats_of(measure):
    return measure.get("beats") or []


def notes_of(measure):
    return [nt for b in beats_of(measure) for nt in (b.get("notes") or [])]


def names(pcs):
    """Pitch-class names. Takes `% 12` itself: the first version did not, and a
    midi value reached it from check_notes and blew up on CHROM[57]. Every caller
    here means 'name these pitches', so the normalisation belongs in one place."""
    return "/".join(CHROM[p % 12] for p in sorted({p % 12 for p in pcs}))


def midi_names(midis):
    """Octave-bearing, for the note diff — `E2/B2` vs `E3/B3` is the difference
    between a reposition (A-1, a pass) and an inversion (a fail), and a
    pitch-class-only message cannot show it."""
    return "/".join(f"{CHROM[m % 12]}{m // 12 - 1}" for m in sorted(midis))


# --- requirement checks ---------------------------------------------------
# Each returns (ok: bool|None, detail: str). None means "not applicable to this
# candidate" and is reported, never counted — see A-12.
def _expected_from_degrees(root_name, degrees, shift):
    root = pc_of(root_name)
    return {(root + shift + DEGREE[d]) % 12 for d in degrees}


def check_requirement(r, *, bars_notes, om, capo, shift, tab, emitted_chords,
                      truth_setup, truth_measures):
    t = r["type"]
    flat = [nt for b in bars_notes for nt in b]
    pcs = set(sounding_pcs_of_notes(flat, om, capo))

    if t in ("scale_degrees_exact", "scale_degrees_present"):
        if shift is None:
            return None, "tuning is not a uniform shift; shape frame undefined"
        want = _expected_from_degrees(r["root"], r["degrees"], shift)
        if t == "scale_degrees_exact":
            ok = pcs == want
        else:
            ok = want <= pcs
        return ok, f"want {names(want)}, got {names(pcs)}"

    if t == "key_pitch_content":
        if shift is None:
            return None, "shape frame undefined"
        want = _expected_from_degrees(r["root"], r["degrees"], shift)
        extra = pcs - want
        return not extra, f"outside the scale: {names(extra) or 'none'}"

    if t == "pitch_class_absent":
        if shift is None:
            return None, "shape frame undefined"
        banned = {(pc_of(p) + shift) % 12 for p in r["pitch_classes"]}
        floor = DURATION_BEATS.get(r.get("min_duration") or "thirty-second")
        hits, short = [], []
        for nt in flat:
            if nt.get("bend"):
                continue
            if (om[nt["string"]] + capo + nt["fret"]) % 12 not in banned:
                continue
            d = duration_beats(nt.get("duration"))
            (hits if d is not None and d >= floor else short).append(nt)
        if hits:
            return False, f"{len(hits)} structural note(s) on {names(banned)}"
        if short:
            # A-13 — a passing tone is not FLE-102's T5. Reported, not failed.
            return True, (f"A-13: {len(short)} note(s) on {names(banned)} below "
                          f"{r.get('min_duration')} — passing tones, flagged for an ear")
        return True, f"{names(banned)} absent"

    if t == "pitch_class_required":
        if shift is None:
            return None, "shape frame undefined"
        want = {(pc_of(p) + shift) % 12 for p in r["pitch_classes"]}
        missing = want - pcs
        return not missing, f"missing {names(missing) or 'none'}"

    if t == "pitch_set_not":
        banned = {pc_of(p) for p in r["pitch_classes"]}   # absolute, not shape
        return pcs != banned, f"bar sounds {names(pcs)}, banned set {names(banned)}"

    if t == "max_distinct_pitch_classes":
        return len(pcs) <= r["n"], f"{len(pcs)} distinct ({names(pcs)}), max {r['n']}"

    if t == "contains_bend":
        best = max((float(nt["bend"]["semitones"]) for nt in flat if nt.get("bend")),
                   default=0.0)
        return best >= r["min_semitones"], f"widest bend {best}, need {r['min_semitones']}"

    if t == "contains_technique":
        got = {nt.get("technique") for nt in flat} - {None}
        return bool(got & set(r["techniques"])), f"techniques present: {sorted(got) or 'none'}"

    if t == "contains_link":
        got = {nt.get("link_to_next") for nt in flat} - {None}
        return bool(got & set(r["links"])), f"links present: {sorted(got) or 'none'}"

    if t == "contains_effect":
        got = {e for nt in flat for e in (nt.get("effects") or [])}
        return bool(got & set(r["effects"])), f"effects present: {sorted(got) or 'none'}"

    if t == "contains_duration":
        got = {nt.get("duration") for nt in flat} - {None}
        return bool(got & set(r["durations"])), f"durations used: {sorted(got)}"

    if t == "not_uniform_duration":
        got = {nt.get("duration") for nt in flat} - {None}
        return len(got) > 1, f"{len(got)} distinct duration(s): {sorted(got)}"

    if t == "sustain_present":
        ok = any(nt.get("duration") in SUSTAINED for nt in flat) \
            or any(nt.get("link_to_next") == "tie" for nt in flat) \
            or any("let_ring" in (nt.get("effects") or []) for nt in flat)
        return ok, "half-or-longer, a tie, or let_ring"

    if t == "min_distinct_bass_pitches":
        strings = set(r["strings"])
        bass = {sounding_midi(nt, om, capo) for nt in flat if nt["string"] in strings}
        return len(bass) >= r["n"], f"{len(bass)} distinct bass pitch(es), need {r['n']}"

    if t == "interleaved_upper_voice":
        strings = set(r["bass_strings"])
        cols = [any(nt["string"] in strings for nt in col) for col in bars_notes]
        idx = [i for i, has in enumerate(cols) if has]
        ok = any(
            any(not cols[k] for k in range(a + 1, b))
            for a, b in zip(idx, idx[1:])
        )
        return ok, f"bass columns at {idx}; need a non-bass column between two of them"

    if t == "drone_present":
        strings, want = set(r["strings"]), {pc_of(p) for p in r["pitch_classes"]}
        ok = any(
            nt["string"] in strings
            and sounding_midi(nt, om, capo) % 12 in want
            and (nt.get("duration") in SUSTAINED
                 or nt.get("link_to_next") == "tie"
                 or "let_ring" in (nt.get("effects") or []))
            for nt in flat
        )
        return ok, f"a sustained {names(want)} on string(s) {sorted(strings)}"

    if t == "stepwise_ascent":
        seq = [sounding_midi(nt, om, capo) for col in bars_notes for nt in col]
        run, best = 1, 1
        for a, b in zip(seq, seq[1:]):
            run = run + 1 if b - a in (1, 2) else 1
            best = max(best, run)
        return best - 1 >= r["min_steps"], f"longest ascending run {best - 1} step(s)"

    if t == "feel_is":
        got = tab.get("feel") or "straight"
        return got == r["feel"], f"feel={got!r}, want {r['feel']!r}"

    if t == "tuning_is":
        return _open_midi(list(tab.get("tuning") or [])) == _open_midi(list(r["tuning"])), \
            f"tuning={tab.get('tuning')}, want {r['tuning']}"

    if t == "capo_is":
        return int(tab.get("capo") or 0) == r["capo"], \
            f"capo={tab.get('capo') or 0}, want {r['capo']}"

    if t == "sounding_name_present":
        if int(tab.get("capo") or 0) == 0:
            return False, "capo is 0, so there is no sounding_name to carry"
        if not emitted_chords:
            return None, "no chord list on the candidate"
        missing = [c.get("name") for c in emitted_chords if not c.get("sounding_name")]
        return not missing, f"missing sounding_name on {missing or 'none'}"

    if t == "no_literal_swing_subdivision":
        if (tab.get("feel") or "straight") == "straight":
            return True, "feel is straight; nothing to double-apply"
        bad = [nt["duration"] for nt in flat
               if nt.get("duration") in ("triplet-eighth", "triplet-quarter")]
        return not bad, f"{len(bad)} literal triplet(s) under feel={tab.get('feel')!r}"

    if t == "absolute_fret_transposition":
        # A-11 — right sounding pitches, capo dropped, shapes transposed.
        if int(tab.get("capo") or 0) == int(truth_setup.get("capo") or 0):
            return True, "capo matches"
        om_truth = _open_midi(list(truth_setup["tuning"]))
        truth_pcs, cand_pcs = set(), set()
        for m in truth_measures:
            truth_pcs |= set(sounding_pcs_of_notes(
                notes_of(m), om_truth, truth_setup.get("capo") or 0))
        for col in bars_notes:
            cand_pcs |= set(sounding_pcs_of_notes(col, om, capo))
        if truth_pcs and cand_pcs == truth_pcs:
            return True, ("A-11: capo dropped but the sounding pitches are right — "
                          "unidiomatic fingering, not a pitch defect")
        return True, "capo differs and the pitches differ too (counted elsewhere)"

    raise SystemExit(f"unimplemented requirement type {t!r} — the scorer must "
                     f"never silently skip an assertion")


IMPLEMENTED = {
    "scale_degrees_exact", "scale_degrees_present", "key_pitch_content",
    "pitch_class_absent", "pitch_class_required", "pitch_set_not",
    "max_distinct_pitch_classes", "contains_bend", "contains_technique",
    "contains_link", "contains_effect", "contains_duration",
    "not_uniform_duration", "sustain_present", "min_distinct_bass_pitches",
    "interleaved_upper_voice", "drone_present", "stepwise_ascent", "feel_is",
    "tuning_is", "capo_is", "sounding_name_present",
    "no_literal_swing_subdivision", "absolute_fret_transposition",
}


# --- chord and note checks ------------------------------------------------
def check_chord(bar, measure, om, capo, shift):
    """Root present, third present, no foreign pitch, characteristic tone present."""
    if shift is None:
        return [], ["tuning is not a uniform shift; chord frame undefined"]
    root, qual = parse_chord(bar["chord"])
    root = (root + shift) % 12
    want = {(root + iv) % 12 for iv in QUAL[qual]}
    third = THIRD[qual]
    char = CHAR[qual]
    fails, notes_ = [], []

    cols = [set(sounding_pcs_of_notes(b.get("notes") or [], om, capo))
            for b in beats_of(measure)]
    bar_pcs = set().union(*cols) if cols else set()

    foreign = bar_pcs - want
    if foreign:
        fails.append(f"foreign pitch {names(foreign)} against {bar['chord']}")

    def complete(s):
        if root not in s:
            return False
        if third is not None and (root + third) % 12 not in s:
            return False
        if char is not None and (root + char) % 12 not in s:
            return False
        return True

    if bar.get("chord_spelled_over") == "beat":
        if not any(complete(c) for c in cols):
            fails.append(
                f"{bar['chord']} is never sounded whole in one column "
                f"(T11) — root/third/characteristic tone not together in any beat"
            )
    else:
        if not complete(bar_pcs):
            miss = []
            if root not in bar_pcs:
                miss.append("root")
            if third is not None and (root + third) % 12 not in bar_pcs:
                miss.append("third (T2)")
            if char is not None and (root + char) % 12 not in bar_pcs:
                miss.append(f"the {CHROM[(root + char) % 12]} that defines the quality")
            fails.append(f"{bar['chord']}: missing {', '.join(miss)}")
    # A-3: the fifth is never required, so an omitted fifth cannot fail here.
    return fails, notes_


def check_notes(bar, truth_measure, cand_measure, om_t, capo_t, om_c, capo_c):
    """The diff. Returns (fails, notes_)."""
    fails, notes_ = [], []
    t_beats, c_beats = beats_of(truth_measure), beats_of(cand_measure)
    rhythm = bool(bar.get("rhythm_scored"))

    if bar.get("match") == "superset_ok":
        # Bar-level (A-6): a correct arpeggiation redistributes the same harmony
        # across more columns, so a column-by-column diff would fail it.
        t_pcs = set(sounding_pcs_of_notes(notes_of(truth_measure), om_t, capo_t))
        c_pcs = set(sounding_pcs_of_notes(notes_of(cand_measure), om_c, capo_c))
        missing = t_pcs - c_pcs
        if missing:
            fails.append(f"missing pitch {names(missing)} (want {names(t_pcs)}, "
                         f"got {names(c_pcs)})")
        t_low = min((sounding_midi(nt, om_t, capo_t)
                     for nt in notes_of(truth_measure) if not nt.get("bend")),
                    default=None)
        c_low = min((sounding_midi(nt, om_c, capo_c)
                     for nt in notes_of(cand_measure) if not nt.get("bend")),
                    default=None)
        if t_low is not None and c_low is not None and t_low % 12 != c_low % 12:
            fails.append(f"bass is {CHROM[c_low % 12]}, reference bass is "
                         f"{CHROM[t_low % 12]} (A-1: a different inversion)")
        return fails, notes_

    if len(t_beats) != len(c_beats):
        fails.append(f"{len(c_beats)} column(s), reference has {len(t_beats)} "
                     f"(match=exact requires the same count)")
        return fails, notes_

    for i, (tb, cb) in enumerate(zip(t_beats, c_beats), start=1):
        tn, cn = tb.get("notes") or [], cb.get("notes") or []
        if (tb.get("rest") is None) != (cb.get("rest") is None):
            fails.append(f"col {i}: rest/sounding mismatch")
            continue
        t_sf = sorted((nt["string"], nt["fret"]) for nt in tn)
        c_sf = sorted((nt["string"], nt["fret"]) for nt in cn)
        t_mid = sorted(sounding_midi(nt, om_t, capo_t) for nt in tn)
        c_mid = sorted(sounding_midi(nt, om_c, capo_c) for nt in cn)
        if t_sf != c_sf:
            if t_mid == c_mid:
                notes_.append(f"col {i}: A-1 repositioned — same pitches, "
                              f"different string/fret")
            elif set(m % 12 for m in t_mid) == set(m % 12 for m in c_mid) \
                    and t_mid and c_mid and t_mid[0] == c_mid[0]:
                notes_.append(f"col {i}: A-2 octave-shifted upper voice")
            else:
                fails.append(
                    f"col {i}: sounds {midi_names(c_mid) or 'nothing'}, reference "
                    f"{midi_names(t_mid) or 'nothing'}"
                )
                continue
        if rhythm:
            td = {nt["duration"] for nt in tn}
            cd = {nt.get("duration") for nt in cn}
            if td != cd:
                fails.append(f"col {i}: duration {sorted(cd)}, reference {sorted(td)}")
    return fails, notes_


def check_meter(measure, time_signature):
    """Never relaxed, on any bar, under any tolerance (A-7)."""
    try:
        got = measure_beats(Tab(measures=[measure]).measures[0])
    except Exception as exc:
        return [f"unparseable measure: {exc}"]
    want = time_signature_beats(measure.get("time_signature") or time_signature)
    if got != want:
        return [f"T10: bar sums to {got} beat(s), {measure.get('time_signature') or time_signature} needs {want}"]
    return []


def parse_key(s, bare_is_major=True):
    """'B minor' -> (11, 'minor'); 'Em' -> (4, 'minor'); 'E' -> (4, 'major').

    `bare_is_major`: a key named by its root alone conventionally means major, and
    reading it as "mode unstated" scored the cached `key: 'E'` on Sweet Home
    Chicago as WRONG against a committed 'E major'. That was a false positive —
    the generator said the right thing. Set False for the GROUND TRUTH side,
    where a bare root ('D' for Kashmir) deliberately means "modal centre, mode
    not scored".
    """
    s = (s or "").strip()
    if not s:
        return None, None
    low = s.lower()
    if "minor" in low or "min" in low:
        mode = "minor"
    elif "major" in low or "maj" in low:
        mode = "major"
    elif low.endswith("m"):
        mode = "minor"
    else:
        mode = "major" if bare_is_major else None
    head = s.split()[0]
    if mode == "minor" and head.lower().endswith("m"):
        head = head[:-1]
    try:
        return pc_of(head), mode
    except SystemExit:
        return None, None


def acceptable_key_roots(sounding_root, setup):
    """A-15 — both frames, because `key` carries no frame marker.

    `songs.key` is a bare string with nothing saying whether it names the SHAPES
    or the SOUNDING pitch. On Eb-tuned Little Wing the cached value is 'Em': that
    is correct in the shape frame and a semitone off in the sounding frame, and
    scoring it wrong would be a false positive of exactly the kind FLE-89 already
    burned a plan revision on. So either is accepted and the report says which.

    That the field needs a frame marker at all is a schema gap, spec'd out in the
    FLE-107 writeup rather than patched here.
    """
    shift = _tuning_offset(list(setup["tuning"]))
    roots = {sounding_root}
    if shift is not None:
        roots.add((sounding_root - shift - int(setup.get("capo") or 0)) % 12)
    return roots


# --- scoring one song -----------------------------------------------------
def score_song(truth, cand):
    """cand: {"tab": {...}, "chords": [...], "key": "..."} or None."""
    out = {"title": truth["title"], "setup": {}, "bars": [], "passage": [],
           "flags": [], "unscorable": None}
    setup = truth["setup"]
    # Only note_exact bars carry notes; a constraint bar has none by design.
    t_measures = [{"beats": b["beats"], "time_signature": setup["time_signature"]}
                  for b in truth["bars"] if b.get("beats")]

    if cand is None:
        out["unscorable"] = "no candidate tab"
        return out

    tab = cand.get("tab") or {}
    c_measures = tab.get("measures") or []
    capo_c = int(tab.get("capo") or 0)
    om_c = _open_midi(list(tab.get("tuning") or []))
    shift_c = frame(tab.get("tuning") or [], capo_c)
    capo_t = int(setup.get("capo") or 0)
    om_t = _open_midi(list(setup["tuning"]))

    # --- setup, 5 fields
    out["setup"]["tuning"] = om_c == om_t
    out["setup"]["capo"] = capo_c == capo_t
    ts = {m.get("time_signature") or "4/4" for m in c_measures} or {"4/4"}
    out["setup"]["time_signature"] = ts == {setup["time_signature"]}
    out["setup"]["feel"] = (tab.get("feel") or "straight") == setup["feel"]
    wk, wm = parse_key(setup.get("key_sounding"), bare_is_major=False)
    gk, gm = parse_key(cand.get("key"))
    if wk is None or gk is None:
        out["setup"]["key"] = None          # nothing emitted, or unparseable
    else:
        roots = acceptable_key_roots(wk, setup)       # A-15, both frames
        out["setup"]["key"] = gk in roots and (wm is None or gm == wm)
        if out["setup"]["key"] and gk != wk:
            out["flags"].append(
                f"A-15: key {cand['key']!r} names the SHAPE frame; the sounding "
                f"key is {setup['key_sounding']!r}. Accepted — the field has no "
                f"frame marker."
            )

    # --- A-12 passage alignment
    n_truth = len(truth["bars"])
    if len(c_measures) != n_truth:
        out["unscorable"] = (
            f"A-12 passage_mismatch: candidate has {len(c_measures)} bar(s), the "
            f"committed passage is {n_truth}. Scoring nothing — a diff against "
            f"the wrong bars is noise, not a measurement."
        )
        return out

    # --- requirements, split by scope
    song_reqs = truth.get("song_requirements") or []
    cols_by_bar = [[(b.get("notes") or []) for b in beats_of(m)] for m in c_measures]

    def run(r, bars_notes):
        return check_requirement(
            r, bars_notes=bars_notes, om=om_c, capo=capo_c, shift=shift_c,
            tab=tab, emitted_chords=cand.get("chords") or [],
            truth_setup=setup, truth_measures=t_measures,
        )

    for r in song_reqs:
        if r["scope"] != "passage":
            continue
        ok, detail = run(r, [nt for bn in cols_by_bar for nt in bn])
        out["passage"].append({"type": r["type"], "severity": r["severity"],
                               "ok": ok, "detail": detail})

    for bi, (tbar, cm) in enumerate(zip(truth["bars"], c_measures), start=1):
        fails, flags, notes_ = [], [], []
        fails += check_meter(cm, setup["time_signature"])

        for r in song_reqs + (tbar.get("requirements") or []):
            if r["scope"] != "every_bar":
                continue
            ok, detail = run(r, cols_by_bar[bi - 1])
            if ok is None:
                notes_.append(f"{r['type']}: not applicable — {detail}")
            elif not ok:
                (fails if r["severity"] == "fail" else flags).append(
                    f"{r['type']}: {detail}")
            elif "A-1" in detail or "A-13" in detail:
                flags.append(detail)

        if tbar.get("chord_checked"):
            cf, cn = check_chord(tbar, cm, om_c, capo_c, shift_c)
            fails += cf
            notes_ += cn
        if tbar["basis"] == "note_exact":
            nf, nn = check_notes(tbar, t_measures[bi - 1], cm,
                                 om_t, capo_t, om_c, capo_c)
            fails += nf
            notes_ += nn

        out["bars"].append({"index": bi, "basis": tbar["basis"],
                            "pass": not fails, "fails": fails,
                            "flags": flags, "notes": notes_})
    return out


# --- reporting ------------------------------------------------------------
def report(truth_doc, results, label):
    songs = {s["title"]: s for s in truth_doc["songs"]}
    print(f"\n{'=' * 78}\nFLE-107 regression score — {label}\n{'=' * 78}")

    setup_ok = setup_n = 0
    print("\nSETUP (unit: song x field)")
    print(f"  {'song':<30} {'tuning':<7}{'capo':<6}{'meter':<7}{'feel':<7}{'key':<6}")
    for r in results:
        if r["unscorable"] and not r["setup"]:
            print(f"  {r['title'][:29]:<30} — {r['unscorable'][:40]}")
            continue
        row = ""
        for f in ("tuning", "capo", "time_signature", "feel", "key"):
            v = r["setup"].get(f)
            row += f"{'ok' if v else ('--' if v is None else 'WRONG'):<7}" \
                if f != "capo" else f"{'ok' if v else ('--' if v is None else 'WRONG'):<6}"
            if v is not None:
                setup_n += 1
                setup_ok += bool(v)
        print(f"  {r['title'][:29]:<30} {row}")
    print(f"  SETUP SCORE: {setup_ok}/{setup_n}")

    for basis in ("note_exact", "constraint"):
        tot = passed = 0
        print(f"\nBARS — basis={basis} (unit: the bar)")
        for r in results:
            bars = [b for b in r["bars"] if b["basis"] == basis]
            if not bars:
                if any(b["basis"] == basis for b in songs[r["title"]]["bars"]) \
                        and r["unscorable"]:
                    n = sum(1 for b in songs[r['title']]['bars'] if b['basis'] == basis)
                    print(f"  {r['title'][:29]:<30} UNSCORED ({n} bars) — "
                          f"{r['unscorable'][:46]}")
                continue
            p = sum(b["pass"] for b in bars)
            tot += len(bars)
            passed += p
            print(f"  {r['title'][:29]:<30} {p}/{len(bars)}")
            for b in bars:
                for f in b["fails"]:
                    print(f"      m{b['index']} FAIL  {f}")
                for f in b["flags"]:
                    print(f"      m{b['index']} flag  {f}")
        print(f"  {basis.upper()} SCORE: {passed}/{tot}"
              + (f"   ({100 * passed // tot}%)" if tot else "   (nothing scored)"))

    print("\nPASSAGE-LEVEL ASSERTIONS (unit: song x assertion — NOT folded into "
          "the bar scores)")
    pok = pn = 0
    for r in results:
        for p in r["passage"]:
            if p["ok"] is None:
                mark = "--"
            elif p["ok"]:
                mark = "ok"
            else:
                mark = "FAIL" if p["severity"] == "fail" else "flag"
            if p["severity"] == "fail" and p["ok"] is not None:
                pn += 1
                pok += bool(p["ok"])
            if mark != "ok":
                print(f"  {r['title'][:28]:<29} {mark:<5} {p['type']}: {p['detail']}")
    print(f"  PASSAGE SCORE (fail-severity only): {pok}/{pn}")

    print(f"\nTAB SCORE (conjunctive: setup correct AND every bar passes)")
    tabs_ok = 0
    for r in results:
        ok = (not r["unscorable"]
              and all(v is not False for v in r["setup"].values())
              and r["bars"] and all(b["pass"] for b in r["bars"])
              and all(p["ok"] is not False or p["severity"] != "fail"
                      for p in r["passage"]))
        tabs_ok += ok
        print(f"  {r['title'][:29]:<30} {'FIT' if ok else 'NOT FIT'}")
    print(f"  TAB SCORE: {tabs_ok}/{len(results)}")
    print("\nReminder: the constraint score says 'nothing disqualifying was "
          "found', not 'this is the song'. Do not report it as accuracy "
          "(regression_set.json -> scoring.what_this_cannot_measure).")
    return {"setup": (setup_ok, setup_n), "tabs": (tabs_ok, len(results))}


# --- modes ----------------------------------------------------------------
def load_truth():
    doc = json.loads(GROUND_TRUTH.read_text())
    unknown = set()
    for s in doc["songs"]:
        for r in (s.get("song_requirements") or []):
            unknown.add(r["type"]) if r["type"] not in IMPLEMENTED else None
        for b in s["bars"]:
            for r in (b.get("requirements") or []):
                unknown.add(r["type"]) if r["type"] not in IMPLEMENTED else None
    if unknown:
        raise SystemExit(f"ground truth asserts {sorted(unknown)}, which the "
                         f"scorer does not implement — an unchecked assertion is "
                         f"worse than no assertion")
    return doc


def truth_as_candidate(song):
    """The committed ground truth dressed as a candidate. Only valid where every
    bar is note_exact; a constraint song has no notes to re-present.

    DEEP-COPIED. The mutation test breaks the candidate on purpose, and the first
    version of this returned references into the loaded ground truth — so every
    mutation leaked into the rows after it and three results were attributed to
    the wrong check. A scorer whose own harness is contaminated proves nothing.
    """
    measures = []
    for b in song["bars"]:
        if not b.get("beats"):
            return None
        measures.append({"beats": deepcopy(b["beats"]),
                         "time_signature": song["setup"]["time_signature"]})
    s = song["setup"]
    return {
        "tab": {"measures": measures, "tuning": s["tuning"], "capo": s["capo"],
                "feel": s["feel"]},
        "chords": [{"name": b["chord"],
                    "sounding_name": "set" if s["capo"] else None,
                    "positions": []}
                   for b in song["bars"] if b.get("chord")],
        "key": s.get("key_sounding"),
    }


def self_test(doc):
    print("Validating every committed tab against the real FLE-115 Tab model...")
    bad = 0
    for s in doc["songs"]:
        cand = truth_as_candidate(s)
        if cand is None:
            print(f"  {s['title'][:34]:<35} constraint-only, no notes to validate")
            continue
        try:
            Tab(**cand["tab"])
            print(f"  {s['title'][:34]:<35} parses ({len(cand['tab']['measures'])} measures)")
        except Exception as exc:
            bad += 1
            print(f"  {s['title'][:34]:<35} DOES NOT PARSE: {exc}")
    if bad:
        raise SystemExit(f"{bad} committed tab(s) are not valid v2 Tabs")

    results = [score_song(s, truth_as_candidate(s)) for s in doc["songs"]]
    summary = report(doc, results, "SELF-TEST (ground truth vs itself)")
    note_fail = [(r["title"], b) for r in results for b in r["bars"]
                 if b["basis"] == "note_exact" and not b["pass"]]
    if note_fail:
        print("\nSELF-TEST FAILED — the committed notes do not satisfy the "
              "committed assertions. That is a builder bug, not a generator bug:")
        for title, b in note_fail:
            print(f"  {title} m{b['index']}: {b['fails']}")
        return 1
    if summary["setup"][0] != summary["setup"][1]:
        print("\nSELF-TEST FAILED — setup fields disagree with themselves.")
        return 1
    print("\nSELF-TEST PASSED: 48/48 note_exact bars and every setup field hold.")
    return 0


def _set_fret(c, measure, beat_i, string, fret):
    for nt in c["tab"]["measures"][measure]["beats"][beat_i]["notes"]:
        if nt["string"] == string:
            nt["fret"] = fret


def _double_apply_swing(c):
    """The FLE-118 defect: feel=shuffle AND the swing notated literally.

    12 triplet-eighths, so the bar still sums to 4 beats. The naive version of
    this mutation reused the 8 existing columns and was caught by the METER
    check instead — a real catch of an unreal defect, which would have let
    `no_literal_swing_subdivision` ship untested.
    """
    for m in c["tab"]["measures"]:
        src = [b for b in m["beats"] if b.get("notes")]
        m["beats"] = [
            {"notes": [dict(nt, duration="triplet-eighth")
                       for nt in src[i % len(src)]["notes"]]}
            for i in range(12)
        ]


def _flatten_boogie(c):
    """Replace the 6 and the b7 with the 5th — FLE-102's "root+5th / root+octave,
    a rock pattern, not a shuffle"."""
    for m in c["tab"]["measures"]:
        for b in m["beats"]:
            for nt in b.get("notes") or []:
                if nt["fret"] in (4, 5):
                    nt["fret"] = 2


def _split_third_away(c, measure):
    """T11 in the form that actually costs the player the chord: the third is
    never in the same column as the root, so the quality is never sounded."""
    m = c["tab"]["measures"][measure]
    whole = {nt["string"]: nt for b in m["beats"] for nt in b["notes"]}
    lower = [whole[s] for s in (6, 5, 4) if s in whole]
    third = [whole[s] for s in (3,) if s in whole]
    for i, b in enumerate(m["beats"]):
        b["notes"] = [dict(nt) for nt in (lower if i % 2 == 0 else third)]


# (song, what it breaks, mutator, the check that MUST fire)
# `expect` is the point. "something failed" is not evidence that the check I
# care about works — three rows of the first version of this test were being
# credited to checks that had not fired at all.
MUTATIONS = [
    ("Little Wing", "T1: raise the Em third to G#",
     lambda c: _set_fret(c, 0, 0, 3, 1), "pitch_class_absent"),
    ("The House of the Rising Sun", "reverse mode flip: D major -> Dm (F# -> F)",
     lambda c: _set_fret(c, 2, 3, 1, 1), "foreign pitch F against D"),
    ("Sultans of Swing", "mode flip on the V: A major -> Am (C# -> C)",
     lambda c: _set_fret(c, 3, 0, 2, 1), "foreign pitch C against A"),
    ("Sweet Home Chicago", "A-9: drop the shuffle mark",
     lambda c: c["tab"].__setitem__("feel", "straight"), "feel_is"),
    ("Sweet Home Chicago", "A-9: notate the swing literally as well",
     _double_apply_swing, "no_literal_swing_subdivision"),
    ("Wonderwall", "drop the capo",
     lambda c: c["tab"].__setitem__("capo", 0), "capo_is"),
    ("Wonderwall", "drop sounding_name from the chord list",
     lambda c: [ch.pop("sounding_name", None) for ch in c["chords"]],
     "sounding_name_present"),
    ("The House of the Rising Sun", "T10: call a 6/8 bar 4/4",
     lambda c: c["tab"]["measures"][0].__setitem__("time_signature", "4/4"), "T10"),
    ("Pride and Joy", "T9/T6: flatten the boogie to root+fifth only",
     _flatten_boogie, "scale_degrees_present"),
    ("Little Wing", "T11: the third never sounds with the root",
     lambda c: _split_third_away(c, 0), "never sounded whole"),
    ("Pride and Joy", "wrong tuning: standard instead of Eb",
     lambda c: c["tab"].__setitem__("tuning", ["E", "A", "D", "G", "B", "e"]),
     "tuning_is"),
    ("Sweet Home Chicago", "A-12: emit a different number of bars",
     lambda c: c["tab"].__setitem__("measures", c["tab"]["measures"][:6]),
     "passage_mismatch"),
]


def mutation_test(doc):
    songs = {s["title"]: s for s in doc["songs"]}
    print("MUTATION TEST — break the ground truth on purpose. Each row names the "
          "check\nthat must fire; a check that survives its own mutation is "
          "decorative.\n")
    missed = []
    for title, what, mutate, expect in MUTATIONS:
        song = songs[title]
        cand = truth_as_candidate(song)
        mutate(cand)
        r = score_song(song, cand)

        reasons = [r["unscorable"]] if r["unscorable"] else []
        reasons += [f for b in r["bars"] for f in b["fails"]]
        reasons += [f"{p['type']}: {p['detail']}" for p in r["passage"]
                    if p["ok"] is False and p["severity"] == "fail"]
        reasons += [f"setup.{k} WRONG" for k, v in r["setup"].items() if v is False]

        hit = next((x for x in reasons if expect in x), None)
        if hit is None:
            missed.append((title, what, expect, reasons))
        print(f"  {'CAUGHT ' if hit else 'MISSED!'} {title[:26]:<27} {what}")
        print(f"            expect {expect!r} -> {(hit or 'NOT FIRED')[:88]}")
    print(f"\n{len(MUTATIONS) - len(missed)}/{len(MUTATIONS)} mutations caught by "
          f"the intended check.")
    for title, what, expect, reasons in missed:
        print(f"  MISSED {title} — {what}: {expect!r} never fired. "
              f"What did fire: {reasons[:3] or 'nothing'}")
    if missed:
        print("MUTATION TEST FAILED — those checks do not bite.")
    return 1 if missed else 0


def load_emitted(path):
    raw = json.loads(pathlib.Path(path).read_text())
    rows = raw["songs"] if isinstance(raw, dict) and "songs" in raw else raw
    return {(r.get("title") or "").strip().lower(): r for r in rows}


def load_from_db():
    import os
    url = os.environ.get("PGURL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("set PGURL (read-only). See the module docstring.")
    import psycopg2                             # same driver as fle102_tab_audit
    out = {}
    with psycopg2.connect(url) as conn:         # SELECT only. No writes.
        cur = conn.cursor()
        # `key` is a column on `songs`, NOT a field inside the breakdown JSON —
        # Breakdown holds tab/chords/technique_notes/drills and no key. Reading
        # it off the JSON made the whole key column read as unscorable.
        cur.execute(
            "SELECT title, artist, key, breakdown FROM songs "
            "WHERE breakdown IS NOT NULL ORDER BY breakdown_generated_at DESC"
        )
        for title, artist, song_key, bd in cur.fetchall():
            k = (title or "").strip().lower()
            if k in out or not isinstance(bd, dict):
                continue                        # newest instance per title
            if not bd.get("tab"):
                continue
            out[k] = {"title": title, "artist": artist, "tab": bd["tab"],
                      "chords": bd.get("chords") or [], "key": song_key}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--mutation-test", action="store_true")
    ap.add_argument("--emitted")
    ap.add_argument("--from-db", action="store_true")
    a = ap.parse_args()
    doc = load_truth()

    if a.self_test:
        return self_test(doc)
    if a.mutation_test:
        return mutation_test(doc)

    if a.emitted:
        cands, label = load_emitted(a.emitted), f"emitted: {a.emitted}"
    elif a.from_db:
        cands, label = load_from_db(), "cached prod breakdowns (read-only)"
    else:
        print(__doc__)
        return 2

    results = []
    for s in doc["songs"]:
        results.append(score_song(s, cands.get(s["title"].strip().lower())))
    report(doc, results, label)
    seen = {s["title"].strip().lower() for s in doc["songs"]}
    extra = sorted(k for k in cands if k not in seen)
    if extra:
        print(f"\n{len(extra)} candidate(s) with no ground truth in this set "
              f"(not scored, not a defect): {', '.join(extra)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
