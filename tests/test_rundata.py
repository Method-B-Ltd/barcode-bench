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
from barcode_bench.rundata import RunData


def _timing(case_id: str, trial: int | None, rep: int | None, seconds: float | None,
            status: str = "ok", error: str | None = None) -> dict[str, Any]:
    return {
        "record_type": "timing", "encoder": "segno", "case_id": case_id,
        "trial": trial, "rep": rep, "seconds": seconds, "status": status, "error": error,
    }


def _measurement(case_id: str, trial: int, *, valid: bool,
                 size: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "record_type": "measurement", "encoder": "segno", "case_id": case_id,
        "trial": trial, "decode_ok": valid, "content_match": valid, "size": size,
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
    ]
    (run_dir / "measurements.jsonl").write_text(
        "".join(json.dumps(m) + "\n" for m in measurements), encoding="utf-8"
    )


def test_rundata_policies(tmp_path: Path) -> None:
    _write_run(tmp_path)
    data = RunData(tmp_path)

    assert data.encoders == ["segno"]
    assert data.encoders_for("qr") == ["segno"]
    assert [c["case_id"] for c in data.cases_for("qr")] == ["qr-a", "qr-b"]
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

    # exception folding: one record -> (kind, reason, count)
    assert data.exceptions[("segno", "qr-b")] == ("unsupported", "no ECI support", 1)
    assert data.median_ms("segno", "qr-b") is None
