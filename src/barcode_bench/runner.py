"""The timed benchmark loop. Runs *inside* an encoder container.

Invoked as ``python -m barcode_bench.runner --encoder <id> --corpus <json>
--out <dir>``. The container has exactly one encoder library installed; the
corpus file is bind-mounted read-only and identical across all encoders (the
fairness mechanism - see :mod:`barcode_bench.corpus`), and everything this
module emits lands in the bind-mounted output directory: one
``timings.jsonl`` of raw per-repetition records plus the produced PNGs, which
the host-side measurement step decodes later.

Timing decisions, and why:

- The timed region is exactly ``adapter.encode_to_png`` - payload string in,
  PNG on disk out - because that is the operation an application pays for.
  Library import cost is excluded (it happens once at adapter load) and is
  instead measured explicitly via ``--measure-import`` in a fresh interpreter.
- Every (trial, repetition) sample is written raw; aggregation happens only
  in the report stage. Medians computed too early can't be un-computed.
- One untimed warmup encode runs per distinct option shape before its timed
  cases, so first-use lazy initialisation (font caches, treepoem's first
  ghostscript spawn) doesn't pollute the first timed sample.
- A case whose options the adapter cannot honour (missing EC-level knob, no
  ECI) is recorded as ``unsupported`` and skipped - encoding with silently
  different settings would fake comparability. An encode that raises is
  recorded as ``encode_error`` per trial; that is a finding about the
  encoder, not a suite failure.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

from barcode_bench.adapters import load_adapter
from barcode_bench.adapters.base import Capabilities, EncodeOptions, EncoderAdapter
from barcode_bench.corpus import Case, load_corpus

WARMUP_PAYLOAD = "WARMUP123"


def _encode(adapter: EncoderAdapter, payload: str, options: EncodeOptions, out_path: Path) -> None:
    """Dispatch the timed encode to the format-appropriate adapter method.
    SVG is only ever reached for adapters that declared supports_svg, so the
    attribute is guaranteed present (see `_unsupported_reason`)."""
    if options.output_format == "svg":
        adapter.encode_to_svg(payload, options, out_path)
    else:
        adapter.encode_to_png(payload, options, out_path)


def _emit(fh: TextIO, record: dict[str, Any]) -> None:
    fh.write(json.dumps(record, ensure_ascii=True) + "\n")
    fh.flush()


def _cpu_info() -> dict[str, Any]:
    """CPU facts for the env record: timing numbers without hardware context
    are weak evidence. Read inside the container (= the measurement
    environment); containers share the host CPU, so these values match the run hardware."""
    model = None
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    return {"model": model, "logical_cores": os.cpu_count()}


def _case_non_ascii(case: Case) -> bool:
    return any(ord(ch) > 127 for payload in case.trials for ch in payload)


def _unsupported_reason(
    caps: Capabilities, options: EncodeOptions, *, non_ascii: bool = False
) -> str | None:
    """Why this adapter cannot honour these options, or None if it can."""
    if options.output_format not in ("png", "svg"):
        return f"output_format {options.output_format!r} not supported"
    if options.output_format == "svg" and not caps.supports_svg:
        return "no native SVG writer"
    if options.output_format == "png" and not caps.supports_png:
        return "no documented raster output (vector-only library)"
    if options.ec_level is not None:
        if caps.ec_levels is None:
            return "no explicit error-correction knob"
        if options.ec_level not in caps.ec_levels:
            return f"ec_level {options.ec_level!r} not offered"
    if options.encoding is not None and not caps.supports_eci:
        return f"no charset/ECI support for {options.encoding!r} payloads"
    if options.encoding is None and non_ascii and not caps.supports_text_auto:
        return "cannot take non-ASCII text without a declared charset"
    if options.pdf417_columns is not None and not caps.supports_pdf417_columns:
        return "no explicit PDF417 column control"
    return None


def _timing_record(
    encoder_id: str,
    case: Case,
    *,
    trial: int | None,
    rep: int | None,
    seconds: float | None,
    status: str,
    error: str | None,
    out_file: str | None,
    payload: str | None,
) -> dict[str, Any]:
    return {
        "record_type": "timing",
        "encoder": encoder_id,
        "case_id": case.case_id,
        "symbology": case.symbology,
        "category": case.category,
        "options": asdict(case.options),
        "trial": trial,
        "rep": rep,
        "seconds": seconds,
        "status": status,
        "error": error,
        "out_file": out_file,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest() if payload else None,
        "payload_chars": len(payload) if payload else None,
    }


def run_benchmark(
    adapter: EncoderAdapter,
    cases: list[Case],
    out_dir: Path,
    reps: int,
    fh: TextIO,
    rep_offset: int = 0,
) -> None:
    """One pass over the corpus, timing reps ``rep_offset..rep_offset+reps-1``.

    ``rep_offset`` exists because repetitions are interleaved *across
    encoders* by the orchestrator (round-robin: every encoder encodes the
    whole corpus once, then every encoder again...), so one container
    invocation runs a single rep and stamps it with the round number. Slow
    machine drift (thermal, background load) then lands on every encoder
    roughly equally instead of biasing whoever ran last. Records append to
    the same timings.jsonl across rounds; duplicate env/unsupported records
    are idempotent for every consumer (last-wins or setdefault).
    """
    cases = [c for c in cases if c.symbology in adapter.supports]

    _emit(
        fh,
        {
            "record_type": "env",
            "encoder": adapter.id,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "cpu": _cpu_info(),
            "libs": adapter.lib_versions(),
            "reps": reps,
            # methodology caveats (e.g. "PNG via bench Pillow shim") travel
            # with the raw data so reports can footnote them
            "capability_notes": {
                sym: adapter.capabilities(sym).notes
                for sym in sorted(adapter.supports)
                if adapter.capabilities(sym).notes
            },
        },
    )

    warmed: set[tuple[Any, ...]] = set()
    for case in cases:
        caps = adapter.capabilities(case.symbology)
        reason = _unsupported_reason(caps, case.options, non_ascii=_case_non_ascii(case))
        if reason is not None:
            _emit(
                fh,
                _timing_record(
                    adapter.id, case, trial=None, rep=None, seconds=None,
                    status="unsupported", error=reason, out_file=None, payload=None,
                ),
            )
            continue

        case_dir = out_dir / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        ext = case.options.output_format

        shape = (case.symbology, case.options.ec_level, case.options.encoding,
                 case.options.pdf417_columns, case.options.output_format)
        if shape not in warmed:
            warmed.add(shape)
            # Warmup is best-effort: a tiny fixed payload can hit limits a
            # real case doesn't (e.g. too few codewords for a forced column
            # count). Actual failures surface in the timed encodes.
            with contextlib.suppress(Exception):
                _encode(adapter, WARMUP_PAYLOAD, case.options, case_dir / f"_warmup.{ext}")
            (case_dir / f"_warmup.{ext}").unlink(missing_ok=True)

        for trial, payload in enumerate(case.trials):
            out_file = case_dir / f"trial{trial}.{ext}"
            for rep in range(rep_offset, rep_offset + reps):
                t0 = time.perf_counter()
                try:
                    _encode(adapter, payload, case.options, out_file)
                except Exception as exc:  # noqa: BLE001 - encoder failure is data
                    _emit(
                        fh,
                        _timing_record(
                            adapter.id, case, trial=trial, rep=rep, seconds=None,
                            status="encode_error", error=f"{type(exc).__name__}: {exc}",
                            out_file=None, payload=payload,
                        ),
                    )
                    out_file.unlink(missing_ok=True)
                    break  # identical input -> identical failure; don't repeat
                seconds = time.perf_counter() - t0
                _emit(
                    fh,
                    _timing_record(
                        adapter.id, case, trial=trial, rep=rep, seconds=seconds,
                        status="ok", error=None,
                        out_file=str(out_file.relative_to(out_dir)), payload=payload,
                    ),
                )



def measure_import(encoder_id: str, fh: TextIO) -> None:
    """Time adapter (= library) import in this interpreter.

    Meaningful only in a virgin interpreter, so the orchestrator invokes this
    mode in separate short-lived container processes rather than reusing the
    benchmark run's process.
    """
    t0 = time.perf_counter()
    load_adapter(encoder_id)
    seconds = time.perf_counter() - t0
    _emit(fh, {"record_type": "import", "encoder": encoder_id, "seconds": seconds})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="in-container benchmark runner")
    parser.add_argument("--encoder", required=True)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--rep-offset", type=int, default=0)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--measure-import", action="store_true")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    timings_path = args.out / "timings.jsonl"

    if args.measure_import:
        with open(timings_path, "a", encoding="utf-8") as fh:
            measure_import(args.encoder, fh)
        return 0

    adapter = load_adapter(args.encoder)
    cases = load_corpus(args.corpus)
    if args.quick:
        cases = [c for c in cases if c.quick]

    with open(timings_path, "a", encoding="utf-8") as fh:
        run_benchmark(
            adapter, cases, args.out,
            reps=(1 if args.quick else args.reps),
            fh=fh,
            rep_offset=args.rep_offset,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
