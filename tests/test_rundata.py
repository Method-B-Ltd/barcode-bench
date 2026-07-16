"""RunData policies over a synthetic run directory.

These are the aggregation rules both report stages depend on and that only
comments otherwise document: the decode-verify validity gate on timing
samples, the SVG-twin fallback for symbol sizes, round counting from stamped
rep values, and exception folding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from barcode_bench.adapters.base import EncodeOptions
from barcode_bench.corpus import Case, corpus_to_json
from barcode_bench.publish import (
    _encode_time_section,
    _exceptions_section,
    _outcome_summary,
    _symbol_size_section,
)
from barcode_bench.rundata import RunData


def _timing(case_id: str, trial: int | None, rep: int | None, seconds: float | None,
            status: str = "ok", error: str | None = None) -> dict[str, Any]:
    return {
        "record_type": "timing", "encoder": "segno", "case_id": case_id,
        "trial": trial, "rep": rep, "seconds": seconds, "status": status, "error": error,
    }


def _measurement(case_id: str, trial: int, *, valid: bool,
                 size: dict[str, Any] | None = None,
                 decode_ok: bool | None = None,
                 decoded_text: str | None = None) -> dict[str, Any]:
    # decode_ok defaults to valid; pass it explicitly to model the misdecode
    # case where the symbol *decoded* but to different content (content_match
    # False while decode_ok True), which decoded_text then records.
    return {
        "record_type": "measurement", "encoder": "segno", "case_id": case_id,
        "trial": trial, "decode_ok": valid if decode_ok is None else decode_ok,
        "content_match": valid, "size": size, "decoded_text": decoded_text,
    }


_SVG_SIZE = {
    "area_modules": 625, "modules_w": 25, "modules_h": 25,
    "pitch_px": 8.0, "crisp": True, "note": "",
}


def _write_run(run_dir: Path) -> None:
    cases = [
        Case("qr-a", "qr", "alnum", EncodeOptions("qr", ec_level="M"),
             "spec", ("AAAA", "BBBB"), quick=False),
        Case("qr-a-svg", "qr", "alnum", EncodeOptions("qr", output_format="svg", ec_level="M"),
             "spec", ("AAAA", "BBBB"), quick=False),
        Case("qr-b", "qr", "latin1", EncodeOptions("qr", ec_level="M", encoding="iso-8859-1"),
             "spec", ("CCCC", "DDDD"), quick=False),
        # qr-c: the charset-ambiguity shape - both trials encode fine but the
        # decoder reads them back as different content (no valid sample, no
        # exception), the outcome the report used to drop silently.
        Case("qr-c", "qr", "latin1_ambiguous",
             EncodeOptions("qr", ec_level="M", encoding="iso-8859-1"),
             "spec", ("EEEE", "FFFF"), quick=False),
    ]
    (run_dir / "corpus.json").write_text(corpus_to_json(1, 2, cases), encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-15T00:00:00+00:00", "seed": 1,
                    "trials_per_case": 2, "n_cases": len(cases)}),
        encoding="utf-8",
    )

    timings = [
        {"record_type": "env", "encoder": "segno"},
        {"record_type": "import", "encoder": "segno", "seconds": 0.05},
        {"record_type": "import", "encoder": "segno", "seconds": 0.07},
        # qr-a trial 0: three interleaved rounds, all decode-verified
        _timing("qr-a", 0, 0, 0.010),
        _timing("qr-a", 0, 1, 0.020),
        _timing("qr-a", 0, 2, 0.030),
        # qr-a trial 1: encoded fine but its symbol fails decode-verify below
        _timing("qr-a", 1, 0, 0.999),
        _timing("qr-a-svg", 0, 0, 0.005),
        _timing("qr-b", None, None, None, status="unsupported", error="no ECI support"),
        # qr-c: both trials encode without error (status ok) ...
        _timing("qr-c", 0, 0, 0.040),
        _timing("qr-c", 1, 0, 0.041),
    ]
    raw = run_dir / "raw" / "segno"
    raw.mkdir(parents=True)
    (raw / "timings.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in timings), encoding="utf-8"
    )

    measurements = [
        # PNG trial decode-verifies but its pixel measurement returned no size
        # -> area must come from the SVG twin
        _measurement("qr-a", 0, valid=True),
        _measurement("qr-a", 1, valid=False),
        _measurement("qr-a-svg", 0, valid=True, size=_SVG_SIZE),
        # ... but their symbols decode to different content (katakana readback),
        # so both trials are misdecodes with a recorded decoded_text.
        _measurement("qr-c", 0, valid=False, decode_ok=True, decoded_text="77ｱ6ｰC"),
        _measurement("qr-c", 1, valid=False, decode_ok=True, decoded_text="30ｱ6ｰC"),
    ]
    (run_dir / "measurements.jsonl").write_text(
        "".join(json.dumps(m) + "\n" for m in measurements), encoding="utf-8"
    )


def test_rundata_policies(tmp_path: Path) -> None:
    _write_run(tmp_path)
    data = RunData(tmp_path)

    assert data.encoders == ["segno"]
    assert data.encoders_for("qr") == ["segno"]
    assert [c["case_id"] for c in data.cases_for("qr")] == ["qr-a", "qr-b", "qr-c"]
    assert [c["case_id"] for c in data.cases_for("qr", "svg")] == ["qr-a-svg"]

    # rounds come from distinct stamped rep values, not the env `reps` field
    assert data.rounds == 3

    # validity gate: trial 1's timing sample is excluded because its symbol
    # failed decode-verify - even though the encode itself succeeded
    assert data.valid == {("segno", "qr-a", 0), ("segno", "qr-a-svg", 0)}
    assert data.valid_trials("segno", "qr-a") == {0}
    assert data.samples[("segno", "qr-a")] == [0.010, 0.020, 0.030]
    assert data.median_ms("segno", "qr-a") == 20.0

    # SVG-twin fallback: the PNG case has no measured size, so area comes from
    # the byte-identical `-svg` sibling
    assert data.median_area("segno", "qr-a") == 625  # 25x25 modules
    # misdecoded symbols are excluded from size too (same gate as timing), so a
    # case whose every symbol misdecoded has no measured area
    assert data.median_area("segno", "qr-c") is None

    # exception folding: one record -> (kind, reason, count)
    assert data.exceptions[("segno", "qr-b")] == ("unsupported", "no ECI support", 1)
    assert data.median_ms("segno", "qr-b") is None

    # misdecode: qr-c encoded ok (status ok, no exception) but no trial
    # decode-verified, so it is neither a sample nor an exception - only
    # misdecoded_trials sees it, keyed by trial with the decoded_text readback.
    assert ("segno", "qr-c") not in data.samples
    assert ("segno", "qr-c") not in data.exceptions
    assert set(data.misdecoded_trials("segno", "qr-c")) == {0, 1}
    assert data.misdecoded_trials("segno", "qr-c")[0]["decoded_text"] == "77ｱ6ｰC"
    # a mixed case still exposes its bad trial (qr-a trial 1 failed to decode)
    assert set(data.misdecoded_trials("segno", "qr-a")) == {1}


def test_publish_flags_misdecode(tmp_path: Path) -> None:
    _write_run(tmp_path)
    data = RunData(tmp_path)
    png_cases = data.cases_for("qr", "png")

    # the coverage summary now has a bucket for the produced-but-invalid case
    # instead of dropping it: qr-a ok, qr-b unsupported, qr-c misdecode
    summary = _outcome_summary(data, "segno", png_cases)
    assert "1 misdecode" in summary
    assert summary == "1 ok, 1 misdecode, 1 unsup."

    # and the exceptions table lists it, with the decoded-to readback so the
    # failure mode is legible rather than a blank cell
    exceptions = "\n".join(_exceptions_section(data, "qr"))
    assert "misdecode" in exceptions
    assert "`qr-c`" in exceptions
    assert "decoded to different content" in exceptions
    assert "77ｱ6ｰC" in exceptions
    # the unsupported case is still there too (both sources fold into one table)
    assert "no ECI support" in exceptions


def test_timing_cell_marks_partial(tmp_path: Path) -> None:
    _write_run(tmp_path)
    data = RunData(tmp_path)
    rows = _encode_time_section(data, "qr", "png")
    # qr-a: trial 0 decode-verified, trial 1 misdecoded -> the median rests on
    # one of two trials, so its cell must carry the dagger. qr-a has a clean
    # median (20.0 ms) so the marker is the only signal something was dropped.
    qr_a = next(r for r in rows if r.startswith("| `qr-a`"))
    assert "†" in qr_a
    # qr-c misdecoded every trial -> no median, but it was attempted and failed,
    # so it reads ERR (not the `—` reserved for unsupported/not-attempted).
    qr_c = next(r for r in rows if r.startswith("| `qr-c`"))
    assert "ERR" in qr_c
    assert "†" not in qr_c
    # qr-b is genuinely unsupported -> the dash, distinct from a misdecode ERR
    qr_b = next(r for r in rows if r.startswith("| `qr-b`"))
    assert "—" in qr_b
    assert "ERR" not in qr_b

    # size table applies the same gate: qr-c's misdecoded symbols contribute no
    # footprint, so its cell is ERR (not a silently-plausible number)
    size_rows = _symbol_size_section(data, "qr")
    qr_c_size = next(r for r in size_rows if r.startswith("| `qr-c`"))
    assert "ERR" in qr_c_size
