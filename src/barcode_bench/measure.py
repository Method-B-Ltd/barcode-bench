"""Host-side measurement: decode every produced symbol and measure its size.

Runs after the containers have finished. For each ``status="ok"`` timing
record, the produced image is decoded with zxing-cpp (the suite's reference
decoder) and the result is verified against the *original corpus payload* - a
timing sample is only trustworthy if the symbol it produced actually decodes
back to its payload, and the report stage excludes invalid samples from summary
statistics.

Symbol size is the module footprint, measured straight from the rendered pixels
(see :mod:`barcode_bench.pixel_measure`): trim the quiet zone, recover the
module grid, area = modules². No per-symbology structure tables and no
adapter-declared geometry - the pixels are the source of truth for every
symbology, PNG and SVG alike. SVG outputs are rasterised here with rsvg-convert
(librsvg), *outside* any timed region, then put through the identical
decode-verify gate and the same pixel measurement. rsvg-convert rather than
cairosvg because cairosvg's pure-Python parse of a large single-path symbol
(python-qrcode near-capacity is a ~13k-op path) costs ~4 s each where librsvg
does it in ~0.1 s.

Measuring is CPU-bound and dominated by SVG rasterisation, so the images fan out
across a process pool.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

import zxingcpp
from PIL import Image

from barcode_bench.corpus import load_corpus, require_run_dir
from barcode_bench.pixel_measure import PixelArea, measure_pixel_area
from barcode_bench.zxing_formats import FORMATS as _FORMATS

# Zoom factors tried in order before giving up on an SVG. A vector has no
# intrinsic px-per-module and encoders author SVGs at different base sizes, so
# decode at a zoom generous enough for the reader and step up if a symbol comes
# out under-resolved. Kept modest because the grid-fit module count
# (pixel_measure) is scale-robust and does not need high resolution to be exact.
_SVG_RASTER_ZOOMS = (6, 10, 16)


def _ok_outputs(timings_path: Path) -> dict[tuple[str, int], dict]:
    """Latest ``status="ok"`` timing record per (case_id, trial)."""
    outputs: dict[tuple[str, int], dict] = {}
    with open(timings_path, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["record_type"] == "timing" and rec["status"] == "ok":
                outputs[(rec["case_id"], rec["trial"])] = rec
    return outputs


def cmd_measure(run_dir: str) -> int:
    run_path = require_run_dir(run_dir)
    cases = {c.case_id: c for c in load_corpus(run_path / "corpus.json")}

    raw_root = run_path / "raw"
    encoder_dirs = sorted(p for p in raw_root.iterdir() if (p / "timings.jsonl").is_file())
    if not encoder_dirs:
        raise SystemExit(f"no encoder outputs under {raw_root}")

    # One task per produced symbol; each is self-contained (reads one file,
    # returns one record) so they fan out across the process pool.
    tasks: list[tuple[Any, ...]] = []
    for enc_dir in encoder_dirs:
        encoder = enc_dir.name
        for (case_id, trial), rec in sorted(_ok_outputs(enc_dir / "timings.jsonl").items()):
            case = cases[case_id]
            tasks.append((
                encoder, case_id, trial, case.symbology,
                case.trials[trial], enc_dir / rec["out_file"],
            ))

    workers = min(len(tasks), os.cpu_count() or 1)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(_measure_task, tasks, chunksize=16))

    # Sort so measurements.jsonl is byte-stable regardless of worker order.
    records.sort(key=lambda r: (r["encoder"], r["case_id"], r["trial"]))
    n_valid = sum(r["decode_ok"] and r["content_match"] for r in records)
    out_path = run_path / "measurements.jsonl"
    with open(out_path, "w", encoding="utf-8") as out:
        for record in records:
            out.write(json.dumps(record, ensure_ascii=True) + "\n")
    print(f"measured {len(records)} symbols, {n_valid} decoded with matching content "
          f"({workers} workers)")
    print(f"wrote {out_path}")
    _report_twin_agreement(records)
    return 0


def _measure_task(task: tuple[Any, ...]) -> dict[str, Any]:
    """Process-pool worker: unpack one task and measure its symbol."""
    encoder, case_id, trial, symbology, payload, out_file = task
    return _measure_one(encoder, case_id, trial, symbology, payload, out_file)


def _report_twin_agreement(records: list[dict[str, Any]]) -> None:
    """Sanity invariant: a case and its ``-svg`` twin are the same symbol, so
    their measured module areas must match. Print the agreement rate; a
    mismatch flags a rasterise/measure regression (e.g. a rasteriser producing
    a ragged symbol)."""
    def base_id(cid: str) -> str:
        return cid[:-4] if cid.endswith("-svg") else cid

    png: dict[tuple[str, str, int], int] = {}
    svg: dict[tuple[str, str, int], int] = {}
    for r in records:
        size = r.get("size")
        if not size:
            continue
        key = (r["encoder"], base_id(r["case_id"]), r["trial"])
        (svg if r["output_format"] == "svg" else png)[key] = size["area_modules"]

    both = sorted(png.keys() & svg.keys())
    disagree = [(k, png[k], svg[k]) for k in both if png[k] != svg[k]]
    print(f"PNG/SVG twin area agreement: {len(both) - len(disagree)}/{len(both)}")
    for (enc, cid, trial), a, b in disagree[:10]:
        print(f"  twin mismatch: {enc} {cid} t{trial}: png {a} vs svg {b} module^2")
    if len(disagree) > 10:
        print(f"  ... and {len(disagree) - 10} more")


def _size_dict(pa: PixelArea | None) -> dict[str, Any] | None:
    return asdict(pa) if pa is not None else None


def _rasterise_svg(
    svg_bytes: bytes, symbology: str, payload: str
) -> tuple[bool, bool, str | None, list[int], Image.Image | None]:
    """Rasterise an SVG (host-side, untimed) and run the decode-verify gate.
    Returns (decode_ok, content_match, decoded_text, px, raster) where
    ``decoded_text`` is what the reader read back (used to explain a content
    mismatch in reports) and ``raster`` is the greyscale image that decoded (for
    the pixel measurer) or the last one attempted. Tries increasing zooms so a
    large symbol that comes out under-resolved at the first zoom still gets a
    fair read; the last attempted image's size is reported either way."""
    px: list[int] = [0, 0]
    raster: Image.Image | None = None
    for zoom in _SVG_RASTER_ZOOMS:
        png = _rsvg_convert(svg_bytes, zoom)
        if png is None:
            continue
        img = Image.open(io.BytesIO(png)).convert("L")
        px = list(img.size)
        raster = img
        result = zxingcpp.read_barcode(img, formats=_FORMATS[symbology])
        if result is not None and result.valid:
            return True, result.text == payload, result.text, px, img
    return False, False, None, px, raster


def _rsvg_convert(svg_bytes: bytes, zoom: int) -> bytes | None:
    """Rasterise SVG bytes to PNG bytes via rsvg-convert (librsvg), white
    background. Returns None if rsvg-convert fails on this input."""
    proc = subprocess.run(
        ["rsvg-convert", "-z", str(zoom), "-b", "white", "-f", "png"],
        input=svg_bytes,
        capture_output=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 and proc.stdout else None


def _measure_one(
    encoder: str,
    case_id: str,
    trial: int,
    symbology: str,
    payload: str,
    out_file: Path,
) -> dict[str, Any]:
    is_svg = out_file.suffix.lower() == ".svg"
    record: dict[str, Any] = {
        "record_type": "measurement",
        "encoder": encoder,
        "case_id": case_id,
        "trial": trial,
        "output_format": "svg" if is_svg else "png",
        "decode_ok": False,
        "content_match": False,
        # What the reference decoder read back, when it decoded to *something*
        # other than the payload (a content mismatch). None when the symbol
        # didn't decode at all or round-tripped exactly - reports use it to
        # show how a misdecode failed (e.g. Latin-1 bytes read as katakana).
        "decoded_text": None,
        "file_bytes": out_file.stat().st_size,
        "px": None,
        # Module footprint measured from the pixels (pixel_measure.PixelArea) or
        # None if the symbol didn't decode.
        "size": None,
    }
    if is_svg:
        decode_ok, content_match, decoded_text, px, raster = _rasterise_svg(
            out_file.read_bytes(), symbology, payload
        )
        record["decode_ok"] = decode_ok
        record["content_match"] = content_match
        if decode_ok and not content_match:
            record["decoded_text"] = decoded_text
        record["px"] = px
        if decode_ok and raster is not None:
            record["size"] = _size_dict(measure_pixel_area(raster, symbology))
        return record

    with Image.open(out_file) as img:
        record["px"] = list(img.size)
        result = zxingcpp.read_barcode(img, formats=_FORMATS[symbology])
        if result is None or not result.valid:
            return record
        record["decode_ok"] = True
        record["content_match"] = result.text == payload
        if not record["content_match"]:
            record["decoded_text"] = result.text
        record["size"] = _size_dict(measure_pixel_area(img, symbology))
    return record
