# server/tests/test_fle148_refusal_log_channel.py
"""FLE-148 — a refused payload has to come back out of PROD, not just out of a
unit test.

FLE-143 built `_persist_raw_payload` and it produced nothing in production:
FLETCHER_RAW_PAYLOAD_DIR was unset, the app service has no volume to set it to,
and `railway ssh` wants a key only Hernan can register. Eight refusals in the
FLE-89 measurement cost $1.41 and left no evidence, and FLE-144 is parked
because it cannot be adjudicated without the refused tab beside its defect
list.

So these tests hold the property the file dump never had: a refusal that is NOT
configured for anything still leaves a complete, verifiable copy of what Sonnet
said somewhere a human can read it. The round-trip through the reassembler is
the real assertion — "the log contains some lines" is what FLE-143 would also
have passed.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging

import pytest

from app.ai.breakdown import (
    _LOG_PAYLOAD_CHUNK_CHARS,
    _LOG_PAYLOAD_MAX_CHARS,
    TabVerificationError,
    _persist_raw_payload,
)
from scripts.reassemble_refusal_payload import main as reassemble

# The longest line measured to survive `railway logs` intact on 2026-11-02 is
# the FLE-143 link_to_next clamp warning at 315 chars — verified complete, it
# still ends in "...no-fresh-pick relationships.". Railway documents no
# per-line cap, so this is the only number we actually know, and every line the
# emitter produces has to fit under it.
PROVEN_RAILWAY_LINE_CHARS = 315


def _rendered(records) -> list[str]:
    """What `railway logs` shows, given main.py's logging.basicConfig."""
    return [f"{r.levelname}:{r.name}:{r.getMessage()}" for r in records]


def _dense_payload(measures: int = 8) -> dict:
    """A payload in the size class that actually refuses in prod.

    Pride and Joy, the densest of the ten cached prod breakdowns, is 14,833
    bytes of compact JSON. A 3-note toy would chunk into one line and prove
    nothing about reassembly, which is the part that can silently go wrong.
    """
    return {
        "tab": {
            "tuning": "standard",
            "measures": [
                {
                    "beats": [
                        {
                            "notes": [
                                {"string": s, "fret": 5 + (s + b) % 12,
                                 "duration": "sixteenth",
                                 "technique": "tap" if s == 1 else None}
                                for s in range(1, 7)
                            ]
                        }
                        for b in range(4)
                    ]
                }
                for _ in range(measures)
            ],
        },
        "chords": [
            {"name": "Em7", "positions": [{"string": s, "fret": s % 5} for s in range(1, 7)]}
            for _ in range(6)
        ],
        "technique_notes": ["Keep the thumb anchored behind the neck." * 3] * 5,
    }


def _refuse(caplog, payload, exc=None, level=logging.INFO):
    exc = exc or TabVerificationError(
        "tab verification failed: OWN_CHORDS: m2 b1 C is foreign to Bm",
        defects=["OWN_CHORDS: m2 b1 C is foreign to Bm", "METER: m3 sums to 4.5"],
        warnings=["UNIFORM_DURATION: every note is an eighth"],
    )
    with caplog.at_level(level):
        _persist_raw_payload(payload, "Pride and Joy", "Stevie Ray Vaughan", exc)
    return caplog.records


def _run_reassembler(monkeypatch, capsys, lines: list[str], *argv) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    monkeypatch.setattr("sys.argv", ["reassemble_refusal_payload.py", *argv])
    code = reassemble()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ---------------------------------------------------------------------------
# The property FLE-143 missed
# ---------------------------------------------------------------------------


def test_an_unset_payload_dir_round_trips_the_whole_payload(
    monkeypatch, capsys, caplog
):
    """The prod configuration — nothing set — now yields the payload verbatim.

    Before this change, an unset dir logged a single "not persisting" line and
    dropped the only copy of what Sonnet said. The assertion is byte equality
    after a round trip through the log and the reassembler, because anything
    weaker (a count of lines, a substring) is what the file dump would also
    have satisfied while producing nothing readable.
    """
    monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)
    payload = _dense_payload()
    records = _refuse(caplog, payload)

    lines = _rendered(records)
    code, out, err = _run_reassembler(monkeypatch, capsys, lines)

    assert code == 0, f"reassembly reported a problem: {err}"
    recovered = json.loads(out)
    assert recovered["raw_input"] == payload, (
        "the refused payload must come back byte-for-byte — a near-miss copy is "
        "worse than none, because FLE-144 would be adjudicated against it"
    )
    assert recovered["defects"] == [
        "OWN_CHORDS: m2 b1 C is foreign to Bm",
        "METER: m3 sums to 4.5",
    ]
    assert recovered["error_type"] == "TabVerificationError"
    assert recovered["song_title"] == "Pride and Joy"


def test_the_payload_really_does_span_many_lines(monkeypatch, caplog):
    """Guard on the test above: a single-chunk payload would prove nothing."""
    monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)
    records = _refuse(caplog, _dense_payload())
    chunks = [r for r in records if "FLE148-PAYLOAD" in r.getMessage()]
    assert len(chunks) > 20, (
        f"expected a prod-sized payload to chunk; got {len(chunks)} lines"
    )


def test_every_emitted_line_fits_inside_the_proven_railway_envelope(
    monkeypatch, caplog
):
    """The sizing is measured, so hold it: nothing may exceed 315 rendered chars.

    This is the test that keeps option B honest. The chunk size was chosen to
    sit under the longest line PROVEN to cross `railway logs` whole, not under
    a cap Railway documents (it documents none). If a future edit widens a
    marker or a message, the budget has to be recomputed rather than discovered
    during the next refusal.
    """
    monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)
    records = _refuse(caplog, _dense_payload())
    too_long = [line for line in _rendered(records) if len(line) > PROVEN_RAILWAY_LINE_CHARS]
    assert not too_long, (
        f"{len(too_long)} line(s) exceed the proven {PROVEN_RAILWAY_LINE_CHARS}-char "
        f"envelope, longest {max(map(len, too_long))}: {too_long[0][:120]}..."
    )


def test_the_diagnosis_survives_even_if_every_chunk_is_lost(monkeypatch, caplog):
    """Error and defects ride their own short lines, not only the chunk stream.

    What FLE-144 needs first is the defect list; the tab is the second half of
    the question. Keeping the defects independent of reassembly means a payload
    that chunks badly still leaves an adjudicable record.
    """
    monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)
    records = _refuse(caplog, _dense_payload())
    standalone = [
        r.getMessage() for r in records if "FLE148-PAYLOAD" not in r.getMessage()
    ]
    joined = " ".join(standalone)
    assert "OWN_CHORDS: m2 b1 C is foreign to Bm" in joined
    assert "METER: m3 sums to 4.5" in joined
    assert "FLE148-ERROR" in joined
    # And the pointer, so whoever finds these lines knows what to run.
    assert "reassemble_refusal_payload.py" in joined


def test_the_refusal_header_is_findable_by_one_grep(monkeypatch, caplog):
    monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)
    records = _refuse(caplog, _dense_payload())
    headers = [r for r in records if "FLE148-REFUSAL" in r.getMessage()]
    assert headers, "a refusal must announce itself at ERROR"
    assert headers[0].levelno == logging.ERROR
    assert "sha256=" in headers[0].getMessage()
    assert "parts=" in headers[0].getMessage()


# ---------------------------------------------------------------------------
# Truncation must be reported, never silent
# ---------------------------------------------------------------------------


def test_a_truncated_log_line_is_reported_not_silently_accepted(
    monkeypatch, capsys, caplog
):
    """The whole reason the emitter carries its own sha256.

    If Railway's undocumented line cap turns out to sit below our chunk size,
    the failure mode without this check is a payload that parses and is WRONG —
    a tab with a hole in it, adjudicated as if it were what Sonnet sent. That
    is the one outcome worse than having no payload at all.
    """
    monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)
    lines = _rendered(_refuse(caplog, _dense_payload()))
    mangled = [
        line[: len(line) - 40] if " 005/" in line else line for line in lines
    ]

    code, _out, err = _run_reassembler(monkeypatch, capsys, mangled)
    assert code != 0
    assert "TRUNCATED LINE" in err
    assert "_LOG_PAYLOAD_CHUNK_CHARS" in err, (
        "the message has to name the knob to turn, or the next reader re-derives it"
    )


def test_a_chunk_altered_in_flight_is_reported(monkeypatch, capsys, caplog):
    """The sha256 backstop, for damage that leaves the line shape intact.

    A cut line loses its closing fence and is caught by shape alone. Anything
    that edits a chunk's contents without changing its framing — a re-encoding,
    a replaced character — can only be caught by the digest, so check that it
    is, rather than assuming the fence covers every case.
    """
    monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)
    lines = _rendered(_refuse(caplog, _dense_payload()))
    altered = [
        line.replace("duration", "duratiox") if " 005/" in line else line
        for line in lines
    ]

    code, _out, err = _run_reassembler(monkeypatch, capsys, altered)
    assert code != 0
    assert "MANGLED" in err


def test_a_chunk_ending_in_a_space_survives_whitespace_on_the_wire(
    monkeypatch, capsys
):
    """Why the chunk body is fenced at all.

    A chunk boundary can fall inside a JSON string, so a chunk may legitimately
    END in a space. Unfenced, that space is indistinguishable from whitespace a
    log pipeline added or trimmed — and getting it wrong silently alters a
    technique note's text instead of failing loudly, which is the one outcome
    this card exists to prevent.

    Built by hand rather than by refusing a shaped payload: where a boundary
    lands depends on the length of the `refused_at` timestamp, so a payload
    that happens to split on a space today would not tomorrow. The hazard is in
    the parser, so test the parser against it directly.
    """
    blob = json.dumps({"technique_notes": ["slide up to the "]}, separators=(",", ":"))
    half = blob.index("the ") + 4  # a chunk that ends in a real space
    bodies = [blob[:half], blob[half:]]
    digest = hashlib.sha256(blob.encode("ascii", "replace")).hexdigest()[:16]
    lines = [
        f"ERROR:app.ai.breakdown:FLE148-REFUSAL dump=deadbeef song='X' by='Y' "
        f"error=E parts={len(bodies)} chars={len(blob)} sha256={digest}",
        # Trailing whitespace is what a log pipeline might add — outside the
        # fence, so it must not reach the payload.
        *(
            f"WARNING:app.ai.breakdown:FLE148-PAYLOAD dump=deadbeef "
            f"{i:03d}/{len(bodies):03d} |{body}|   "
            for i, body in enumerate(bodies, 1)
        ),
    ]
    assert bodies[0].endswith(" "), "the test must exercise its own premise"

    code, out, err = _run_reassembler(monkeypatch, capsys, lines)
    assert code == 0, err
    assert json.loads(out)["technique_notes"] == ["slide up to the "]


def test_a_missing_chunk_is_reported(monkeypatch, capsys, caplog):
    """A log window that rolled mid-dump is a gap, and must read as one."""
    monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)
    lines = _rendered(_refuse(caplog, _dense_payload()))
    dropped = [line for line in lines if " 003/" not in line]

    code, _out, err = _run_reassembler(monkeypatch, capsys, dropped)
    assert code != 0
    assert "INCOMPLETE" in err and "missing part" in err


def test_the_size_cap_announces_itself_when_it_bites(monkeypatch, capsys, caplog):
    """The cap is measured to be unreachable for a model payload — prove it says so.

    _LOG_PAYLOAD_MAX_CHARS is 64 KiB, above the ~50 KB that
    _MAX_OUTPUT_TOKENS=20,000 allows Sonnet to emit and 4.3x the densest real
    prod breakdown (14,833 bytes). So this can only fire on a pathological
    `default=str` expansion — and if it ever does, the reader must be told the
    tail is gone rather than reading a truncated tab as the whole one.
    """
    monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)
    oversized = {"filler": "x" * (_LOG_PAYLOAD_MAX_CHARS + 5000)}
    lines = _rendered(_refuse(caplog, oversized))

    assert any("TRUNCATED_BY=" in line for line in lines)
    code, _out, err = _run_reassembler(monkeypatch, capsys, lines)
    assert code != 0
    assert "size cap" in err
    assert "option C" in err, "a truncation is the trigger to stop widening this"


def test_a_real_prod_sized_payload_is_nowhere_near_the_cap(monkeypatch, caplog):
    """The claim that the cap cannot bite in practice, asserted rather than asserted-in-prose."""
    monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)
    records = _refuse(caplog, _dense_payload())
    chunks = [r for r in records if "FLE148-PAYLOAD" in r.getMessage()]
    emitted = len(chunks) * _LOG_PAYLOAD_CHUNK_CHARS
    assert emitted < _LOG_PAYLOAD_MAX_CHARS / 2, (
        f"a prod-sized payload emitted {emitted} chars against a "
        f"{_LOG_PAYLOAD_MAX_CHARS} cap — the headroom claim no longer holds"
    )


# ---------------------------------------------------------------------------
# The file channel still wins where there is somewhere to write
# ---------------------------------------------------------------------------


def test_a_set_dir_still_writes_the_file_and_does_not_flood_the_log(
    monkeypatch, caplog, tmp_path
):
    """FLE-143's behaviour is unchanged where a durable path exists."""
    monkeypatch.setenv("FLETCHER_RAW_PAYLOAD_DIR", str(tmp_path))
    records = _refuse(caplog, _dense_payload())

    dumps = list(tmp_path.glob("*.json"))
    assert len(dumps) == 1
    assert json.loads(dumps[0].read_text())["raw_input"]["tab"]["measures"]
    assert not [r for r in records if "FLE148-PAYLOAD" in r.getMessage()], (
        "the log channel is the fallback, not a second copy"
    )


def test_an_unwritable_dir_falls_back_to_the_log_rather_than_losing_the_payload(
    monkeypatch, capsys, caplog, tmp_path
):
    """A read-only mount or a path that is a file is exactly how evidence vanishes.

    Setting the variable to something that cannot be written is operator error,
    but the cost of it used to be the payload. The payload is the expensive
    thing ($0.145 a song, and for a cached row the song permanently), so it
    goes to the channel that still works.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("occupied")
    monkeypatch.setenv("FLETCHER_RAW_PAYLOAD_DIR", str(blocker))

    payload = _dense_payload(measures=2)
    lines = _rendered(_refuse(caplog, payload, level=logging.WARNING))
    assert any("Could not persist" in line for line in lines)

    code, out, err = _run_reassembler(monkeypatch, capsys, lines)
    assert code == 0, err
    assert json.loads(out)["raw_input"] == payload


def test_nothing_is_emitted_when_there_was_no_payload(monkeypatch, caplog):
    """A refusal before any tool_use block has nothing to keep (unchanged)."""
    monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)
    with caplog.at_level(logging.INFO):
        _persist_raw_payload(None, "Lenny", "SRV", RuntimeError("no api key"))
    assert not [r for r in caplog.records if "FLE148" in r.getMessage()]


def test_the_reassembler_says_so_when_there_is_nothing_to_reassemble(
    monkeypatch, capsys
):
    code, _out, err = _run_reassembler(
        monkeypatch, capsys, ["INFO:app.main:Application startup complete."]
    )
    assert code == 2
    assert "No FLE148-PAYLOAD lines" in err
    # The two real explanations, so nobody concludes "the diagnostic is broken".
    assert "no refusal happened" in err and "FLETCHER_RAW_PAYLOAD_DIR" in err


@pytest.mark.parametrize("dir_set", [True, False])
def test_the_refusal_itself_is_unchanged(monkeypatch, caplog, tmp_path, dir_set):
    """Log the evidence, then refuse — the diagnostic must not alter serving.

    A blocking defect means a wrong note or a wrong bar; serving it would trade
    "no session" for "a session with a wrong tab". `_persist_raw_payload` is
    called from the refusal handlers and returns None on every path.
    """
    if dir_set:
        monkeypatch.setenv("FLETCHER_RAW_PAYLOAD_DIR", str(tmp_path))
    else:
        monkeypatch.delenv("FLETCHER_RAW_PAYLOAD_DIR", raising=False)
    with caplog.at_level(logging.INFO):
        assert _persist_raw_payload(
            _dense_payload(measures=1), "Lenny", "SRV", RuntimeError("boom")
        ) is None
