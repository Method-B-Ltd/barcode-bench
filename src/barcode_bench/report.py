"""Aggregate raw run records into summary.csv and summary.md.

This is the only stage that computes statistics - the runner
emits every raw sample so that aggregation choices stay revisable. Headline
timing metric is the *median* (robust against container scheduling noise);
mean/stddev/min ride along in the CSV.

The validity rule from the plan is enforced here: a timing sample counts
toward the statistics only if its trial's PNG decoded back to the exact
payload (per measurements.jsonl). Cases with invalid trials surface a
``validity_rate`` < 1 rather than silently mixing broken symbols into the
comparison. ``unsupported`` and ``encode_error`` cases stay visible as
explicit outcomes - an encoder that can't do a case is a result, not a gap.
"""

from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Any

from barcode_bench.corpus import require_run_dir
from barcode_bench.rundata import RunData

_SYMBOLOGY_TITLES = {
    "qr": "QR Code",
    "datamatrix": "Data Matrix",
    "datamatrix_rect": "Data Matrix (rectangular, forced)",
    "aztec": "Aztec",
    "pdf417": "PDF417",
}


def _median(values: list[Any]) -> Any:
    return statistics.median(values) if values else None


def cmd_report(run_dir: str) -> int:
    run_path = require_run_dir(run_dir)
    data = RunData(run_path)

    # One row per (encoder, case) that has any timing record. RunData is the
    # shared loader; the CSV aggregation (mean/stddev/min, per-case validity)
    # stays local to this stage.
    rows = [_case_row(data, encoder, case_id) for encoder, case_id in sorted(data.timings)]

    _write_csv(run_path / "summary.csv", rows)
    _write_md(run_path / "summary.md", data, rows)
    print(f"wrote {run_path / 'summary.csv'}")
    print(f"wrote {run_path / 'summary.md'}")
    return 0


def _case_row(data: RunData, encoder: str, case_id: str) -> dict[str, Any]:
    recs = data.timings[(encoder, case_id)]
    valid_trials = data.valid_trials(encoder, case_id)
    case = data.cases[case_id]
    measures = data.measures
    ok = [r for r in recs if r["status"] == "ok"]
    unsupported = next((r for r in recs if r["status"] == "unsupported"), None)
    errors = [r for r in recs if r["status"] == "encode_error"]

    trials_attempted = {r["trial"] for r in ok} | {r["trial"] for r in errors}
    ok_valid = [r for r in ok if r["trial"] in valid_trials]
    times = [r["seconds"] for r in ok_valid]

    symbol_measures = [
        measures[(encoder, case_id, t)] for t in sorted(valid_trials)
        if (encoder, case_id, t) in measures
    ]
    areas = [
        m["size"]["area_modules"] for m in symbol_measures
        if m["size"] and m["size"]["area_modules"] is not None
    ]
    file_bytes = [m["file_bytes"] for m in symbol_measures]

    if unsupported is not None:
        status = "unsupported"
    elif errors and not ok:
        status = "encode_error"
    else:
        status = "ok"

    options = case["options"]
    return {
        "encoder": encoder,
        "case_id": case_id,
        "symbology": case["symbology"],
        "category": case["category"],
        "ec_level": options.get("ec_level"),
        "encoding": options.get("encoding"),
        "pdf417_columns": options.get("pdf417_columns"),
        "status": status,
        "detail": (unsupported or {}).get("error") or (errors[0]["error"] if errors else None),
        "n_samples": len(times),
        "n_trials_valid": len(valid_trials),
        "validity_rate": (len(valid_trials) / len(trials_attempted)) if trials_attempted else None,
        "median_ms": _ms(_median(times)),
        "mean_ms": _ms(statistics.mean(times)) if times else None,
        "stddev_ms": _ms(statistics.stdev(times)) if len(times) > 1 else None,
        "min_ms": _ms(min(times)) if times else None,
        "median_area_modules": _median(areas),
        "median_file_bytes": _median(file_bytes),
    }


def _ms(seconds: float | None) -> float | None:
    return round(seconds * 1000, 4) if seconds is not None else None


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise SystemExit("no timing records found — did `run` complete?")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_md(path: Path, data: RunData, rows: list[dict[str, Any]]) -> None:
    by_key = {(r["encoder"], r["case_id"]): r for r in rows}
    cases_list = list(data.cases.values())
    lines: list[str] = []
    w = lines.append

    w(f"# barcode-bench summary — {data.run_dir.name}")
    w("")
    w(f"Seed {data.seed}, {data.trials_per_case} trial payloads per case, "
      f"{len(data.cases)} cases. Timing cell: median ms of valid samples; "
      "`mod²` is the symbol's module footprint (area in modules², quiet zone "
      "excluded). `—` = case unsupported by that encoder, `ERR` = encoder raised.")
    w("")

    if data.images:
        w("## Install weight (podman image delta over shared base)")
        w("")
        w("| encoder | encoder stack MB | full image MB | libs |")
        w("|---|---:|---:|---|")
        for enc in data.encoders:
            info = data.images.get("encoders", {}).get(enc)
            if not info:
                continue
            libs = ", ".join(
                f"{k} {v}" for k, v in data.envs.get(enc, {}).get("libs", {}).items()
            )
            w(f"| {enc} | {info['delta_bytes'] / 1e6:.1f} | {info['bytes'] / 1e6:.1f} | {libs} |")
        w("")

    if data.imports:
        w("## Cold import (fresh interpreter, median of "
          f"{max(len(v) for v in data.imports.values())} runs)")
        w("")
        w("| encoder | import ms |")
        w("|---|---:|")
        for enc in data.encoders:
            if data.imports.get(enc):
                w(f"| {enc} | {_ms(_median(data.imports[enc])):.1f} |")
        w("")

    def _fmt(c: dict) -> str:
        return c["options"].get("output_format", "png")

    for symbology, title in _SYMBOLOGY_TITLES.items():
        for out_fmt in ("png", "svg"):
            case_ids = [
                c["case_id"] for c in cases_list
                if c["symbology"] == symbology and _fmt(c) == out_fmt
                if any((enc, c["case_id"]) in by_key for enc in data.encoders)
            ]
            if not case_ids:
                continue
            cols = [
                enc for enc in data.encoders if any((enc, cid) in by_key for cid in case_ids)
            ]
            suffix = "" if out_fmt == "png" else " — SVG (encode time only; size = PNG twin)"
            w(f"## {title}{suffix}")
            w("")
            w("| case | payload | " + " | ".join(cols) + " |")
            w("|---|---|" + "---:|" * len(cols))
            for cid in case_ids:
                case = next(c for c in cases_list if c["case_id"] == cid)
                payload_desc = f"{case['category']} ({len(case['trials'][0])} ch)"
                cells = [_cell(by_key.get((enc, cid))) for enc in cols]
                w(f"| {cid} | {payload_desc} | " + " | ".join(cells) + " |")
            w("")

    notes = {
        (enc, sym): note
        for enc in data.encoders
        for sym, note in data.envs.get(enc, {}).get("capability_notes", {}).items()
    }
    if notes:
        w("## Methodology notes")
        w("")
        for (enc, sym), note in sorted(notes.items()):
            w(f"- **{enc}** ({sym}): {note}")
        w("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _cell(row: dict[str, Any] | None) -> str:
    if row is None:
        return ""
    if row["status"] == "unsupported":
        return "—"
    if row["status"] == "encode_error":
        return "ERR"
    ms = f"{row['median_ms']:.2f}" if row["median_ms"] is not None else "?"
    area = row["median_area_modules"]
    area_text = f" / {area:g} mod²" if area is not None else ""
    flag = "" if row["validity_rate"] in (1, None) else " ⚠"
    return f"{ms} ms{area_text}{flag}"
