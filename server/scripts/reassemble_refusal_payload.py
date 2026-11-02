#!/usr/bin/env python3
"""FLE-148 — turn `railway logs` back into the refused Sonnet payload.

    cd server && railway logs -s guitar-trainer-v2 | python3 scripts/reassemble_refusal_payload.py
    ... | python3 scripts/reassemble_refusal_payload.py --dump a1b2c3d4 -o refusal.json

Why this exists rather than "just read the log". FLE-143 wrote the payload to a
file and prod turned out to have nowhere to put one: no volume on the app
service, and `railway ssh` needs a key only Hernan can register. So the log is
the retrieval path (option B on FLE-148), and a 15 KB payload crosses it as
~62 `FLE148-PAYLOAD` lines. Rejoining those by hand is the kind of step that
does not get done at 1am, which is how a diagnostic ends up looking like
evidence and producing none.

What it guarantees, and this is the point of it: the emitter records the byte
count and sha256 of exactly what it sent, so this script can tell "here is the
payload" apart from "here is most of the payload". A silent half-payload would
be worse than no payload — you would adjudicate FLE-144 against a tab whose
tail went missing and never know. Every shortfall is reported on stderr and
exits non-zero.

Reads a plain `railway logs` stream (or a saved copy) on stdin; order and
interleaving do not matter, and several dumps in one window are fine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict

# Matches the emitter in app/ai/breakdown.py. The level/logger prefix that
# basicConfig renders and Railway's own timestamp column are both tolerated by
# searching rather than anchoring.
# The `|...|` fence is greedy to its LAST pipe on purpose: a chunk body is raw
# JSON and may well contain a pipe of its own, but the closing fence is always
# the final one on the line. Matching it this way also means whitespace the
# transport may have added outside the fence cannot slip into the payload.
_CHUNK = re.compile(
    r"FLE148-PAYLOAD dump=(?P<dump>[0-9a-f]{8}) "
    r"(?P<idx>\d{3})/(?P<total>\d{3}) \|(?P<body>.*)\|\s*$"
)
# A chunk line whose OPENING fence is present but whose closing fence is not:
# the single most likely production failure, i.e. a log pipeline whose per-line
# cap sits below the emitter's chunk size. Worth naming separately from "that
# part never arrived", because the fix is different — lower the chunk size,
# rather than go looking for a log window that rolled.
_CHUNK_CUT = re.compile(
    r"FLE148-PAYLOAD dump=(?P<dump>[0-9a-f]{8}) (?P<idx>\d{3})/(?P<total>\d{3}) \|"
)
_HEADER = re.compile(
    r"FLE148-REFUSAL dump=(?P<dump>[0-9a-f]{8}) song=(?P<song>.*?) "
    r"by=(?P<artist>.*?) error=(?P<error>\S+) parts=(?P<parts>\d+) "
    r"chars=(?P<chars>\d+) sha256=(?P<sha>[0-9a-f]{16})"
    r"(?P<trunc> TRUNCATED_BY=\d+)?"
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dump",
        help="only this dump id (default: every dump found; one JSON per line "
             "when there is more than one)",
    )
    ap.add_argument("-o", "--out", help="write the payload here instead of stdout")
    args = ap.parse_args()

    chunks: dict[str, dict[int, str]] = defaultdict(dict)
    totals: dict[str, int] = {}
    headers: dict[str, dict[str, str]] = {}
    cut: dict[str, set[int]] = defaultdict(set)

    for line in sys.stdin:
        head = _HEADER.search(line)
        if head:
            headers[head.group("dump")] = head.groupdict()
            continue
        hit = _CHUNK.search(line)
        if not hit:
            cut_hit = _CHUNK_CUT.search(line)
            if cut_hit:
                cut[cut_hit.group("dump")].add(int(cut_hit.group("idx")))
                totals.setdefault(cut_hit.group("dump"), int(cut_hit.group("total")))
            continue
        dump = hit.group("dump")
        # A duplicate part is a repeated log line, not a conflict — the body is
        # the same text either way, so last-one-wins is safe.
        chunks[dump][int(hit.group("idx"))] = hit.group("body")
        totals[dump] = int(hit.group("total"))

    wanted = [args.dump] if args.dump else sorted(set(chunks) | set(cut))
    if not wanted:
        print(
            "No FLE148-PAYLOAD lines on stdin. Either no refusal happened in "
            "the log window, or FLETCHER_RAW_PAYLOAD_DIR is set on the service "
            "and the payload went to a file instead.",
            file=sys.stderr,
        )
        return 2

    out: list[str] = []
    ok = True
    for dump in wanted:
        parts, total = chunks.get(dump, {}), totals.get(dump, 0)
        # Report a cut line BEFORE bailing on an empty dump: if every line was
        # truncated there are no usable parts at all, and "no chunks found"
        # alone would send the reader hunting for the wrong cause.
        if cut.get(dump):
            print(
                f"dump={dump}: TRUNCATED LINE(S) — part(s) "
                f"{','.join(map(str, sorted(cut[dump])))} arrived without their "
                f"closing fence, so a log line was cut. The per-line cap on this "
                f"channel is below the emitter's chunk size: lower "
                f"_LOG_PAYLOAD_CHUNK_CHARS in app/ai/breakdown.py (and say so on "
                f"FLE-148 — the 240 there was measured, so this would mean the "
                f"measurement no longer holds).",
                file=sys.stderr,
            )
            ok = False

        if not parts:
            print(f"dump={dump}: no usable chunks found", file=sys.stderr)
            ok = False
            continue

        missing = [i for i in range(1, total + 1) if i not in parts]
        if missing:
            # Report and still emit: a payload with a hole in it is worth
            # reading as long as nobody can mistake it for a whole one.
            print(
                f"dump={dump}: INCOMPLETE — missing part(s) "
                f"{','.join(map(str, missing))} of {total}. The log window "
                f"probably rolled; what follows has gaps.",
                file=sys.stderr,
            )
            ok = False

        blob = "".join(parts[i] for i in sorted(parts))
        header = headers.get(dump)
        if header:
            expected_chars, expected_sha = int(header["chars"]), header["sha"]
            got_sha = hashlib.sha256(blob.encode("ascii", "replace")).hexdigest()[:16]
            if len(blob) != expected_chars or got_sha != expected_sha:
                # The emitter hashes exactly what it sent, so a mismatch here
                # means the transport mangled it — most likely a per-line cap
                # below the emitter's 240-char chunk. Say so precisely: the fix
                # is to lower _LOG_PAYLOAD_CHUNK_CHARS, or to take option C.
                print(
                    f"dump={dump}: MANGLED — emitter sent {expected_chars} chars "
                    f"(sha256 {expected_sha}), reassembled {len(blob)} "
                    f"(sha256 {got_sha}). A log line was truncated; lower "
                    f"_LOG_PAYLOAD_CHUNK_CHARS in app/ai/breakdown.py.",
                    file=sys.stderr,
                )
                ok = False
            if header.get("trunc"):
                print(
                    f"dump={dump}: the EMITTER hit its size cap "
                    f"({header['trunc'].strip()} chars dropped from the tail of "
                    f"raw_input). This was measured as unreachable for a model "
                    f"payload — if you are seeing it, say so on FLE-148 and "
                    f"take option C (a refused_payloads table).",
                    file=sys.stderr,
                )
                ok = False
        else:
            print(
                f"dump={dump}: no FLE148-REFUSAL header in the window, so "
                f"completeness could NOT be verified — treat the payload below "
                f"as possibly partial.",
                file=sys.stderr,
            )
            ok = False

        try:
            out.append(json.dumps(json.loads(blob), indent=2))
        except json.JSONDecodeError as exc:
            print(
                f"dump={dump}: reassembled text is not valid JSON ({exc}). "
                f"Emitting it raw so the readable part is still usable.",
                file=sys.stderr,
            )
            ok = False
            out.append(blob)

    text = "\n".join(out)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out} ({len(text)} chars)", file=sys.stderr)
    else:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
