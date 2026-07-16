"""Facts-only public report: Markdown + matplotlib charts, generated per run.

This is the *evidence artefact*: everything in it is computed from the run
directory's records by mechanical rules stated in captions, so the report can
be checked into the repository (GitHub renders the Markdown and images) and
cited by narrative writing elsewhere without the two ever disagreeing.
Editorial content (recommendations, winners, bug narratives) lives
outside; the only prose here is fixed methodology/limitations
boilerplate versioned with this module.

Design rules (see the report-content discussion in the repo history):

- Deterministic: the same run directory produces byte-identical Markdown and
  stable charts, so a regenerated report cannot drift under a post citing it.
- Every table's ordering/highlighting rule is mechanical and stated in its
  caption (sorted by X; bold = column minimum).
- Timing spread is shown, not summarised: charts underlay all raw valid
  samples as low-opacity dots behind the median line ("dots are samples,
  tick is median"), avoiding any choice of summary statistic. Tables carry
  the median only.
- A timing sample counts only if its trial's PNG decode-verified against the
  payload (the same validity rule as report.py); unsupported/error outcomes
  appear as footnoted cells and verbatim in the exceptions table.
"""

from __future__ import annotations

import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from barcode_bench.adapters import ALL_ENCODER_IDS
from barcode_bench.corpus import require_run_dir
from barcode_bench.rundata import RunData

_SYMBOLOGY_TITLES = {
    "qr": "QR Code",
    "datamatrix": "Data Matrix (square)",
    "datamatrix_rect": "Data Matrix (rectangular, forced)",
    "aztec": "Aztec",
    "pdf417": "PDF417",
}

# consistent encoder colour across every chart, keyed by registry order.
# tab20 interleaves dark/light pairs of the same hue, so walk the dark hues
# first (even indices) and only then the light variants - adjacent encoders
# get distinct hues instead of two oranges.
# ListedColormap has .colors but the Colormap base type mypy sees doesn't
_PALETTE = plt.get_cmap("tab20").colors  # type: ignore[attr-defined]


def _encoder_color(encoder: str) -> tuple[float, float, float]:
    idx = ALL_ENCODER_IDS.index(encoder) if encoder in ALL_ENCODER_IDS else 19
    spread = idx * 2 if idx < 10 else (idx - 10) * 2 + 1
    return _PALETTE[spread % len(_PALETTE)]


# Bench encoder ids are short internal slugs; these differ from the exact PyPI
# distribution name. Labels and links in the report use the PyPI name so the
# artefact is traceable to the published package. `pystrich-git` is not a
# release (a wheel built from GitHub HEAD), so it keeps its slug and links to
# the source repo, not a PyPI page.
_PYPI_NAME = {
    "zxingcpp": "zxing-cpp",
    "aztecgen": "aztec-code-generator",
    "ppfdm": "ppf-datamatrix",
    "opencv": "opencv-python-headless",
}
_PYSTRICH_GIT_URL = "https://github.com/mmulqueen/pyStrich"


def _display(encoder: str) -> str:
    """PyPI-accurate label for a bench encoder id (used in tables and charts)."""
    return _PYPI_NAME.get(encoder, encoder)


def _project_link(encoder: str) -> str:
    """Markdown link to the encoder's PyPI project (or source repo for -git)."""
    if encoder == "pystrich-git":
        return f"[pyStrich@HEAD]({_PYSTRICH_GIT_URL}) (git build, not a release)"
    name = _PYPI_NAME.get(encoder, encoder)
    return f"[{name}](https://pypi.org/project/{name}/)"


# --------------------------------------------------------------------------
# formatting helpers


def _fmt_ms(ms: float) -> str:
    if ms >= 1000:
        return f"{ms:,.0f}"
    if ms >= 100:
        return f"{ms:.0f}"
    if ms >= 10:
        return f"{ms:.1f}"
    return f"{ms:.2f}"


def _fmt_mb(b: int) -> str:
    return f"{b / 1e6:.1f}"


# --------------------------------------------------------------------------
# charts


def _chart_install_weight(data: RunData, out: Path) -> None:
    encoders = sorted(
        data.images["encoders"].items(), key=lambda kv: kv[1]["delta_bytes"]
    )
    ids = [e for e, _ in encoders]
    labels = [_display(e) for e in ids]
    sizes = [max(i["delta_bytes"], 1) / 1e6 for _, i in encoders]
    fig, ax = plt.subplots(figsize=(8, 0.42 * len(ids) + 1.2))
    ax.barh(labels, sizes, color=[_encoder_color(e) for e in ids])
    ax.set_xscale("log")
    ax.set_xlabel("encoder stack size, MB (log scale) — image minus shared base")
    for y, size in enumerate(sizes):
        ax.text(size * 1.15, y, f"{size:.1f}", va="center", fontsize=8)
    ax.set_xlim(right=max(sizes) * 3)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def _chart_timing(data: RunData, symbology: str, out: Path, output_format: str = "png") -> None:
    """Median line per encoder over cases (ordered by payload length), with
    every raw valid sample underlaid as a low-opacity dot."""
    cases = data.cases_for(symbology, output_format)
    encoders = _fmt_encoders(data, symbology, output_format)
    fig, ax = plt.subplots(figsize=(max(7.0, 0.62 * len(cases) + 2.5), 5))
    xs = range(len(cases))
    for enc in encoders:
        color = _encoder_color(enc)
        med_x, med_y = [], []
        for x, case in zip(xs, cases, strict=True):
            samples = data.samples.get((enc, case["case_id"]), [])
            if not samples:
                continue
            med_x.append(x)
            med_y.append(statistics.median(samples) * 1000)
            # deterministic jitter: samples fan out evenly around the tick
            n = len(samples)
            offsets = [(i - (n - 1) / 2) / (2.2 * max(n, 1)) for i in range(n)]
            ax.scatter(
                [x + o for o in offsets],
                [s * 1000 for s in sorted(samples)],
                s=6, color=color, alpha=0.25, linewidths=0, zorder=1,
            )
        ax.plot(med_x, med_y, marker="_", markersize=11, color=color, label=_display(enc),
                linewidth=1.2, zorder=2)
    ax.set_yscale("log")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([c["case_id"] for c in cases], rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("encode time, ms (log scale)")
    fmt_label = "" if output_format == "png" else f" ({output_format.upper()})"
    ax.set_title(
        f"{_SYMBOLOGY_TITLES[symbology]}{fmt_label} — dots: all decode-verified "
        "samples; tick/line: median. Cases ordered by payload length."
    , fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


# --------------------------------------------------------------------------
# markdown sections


def _cpu_line(data: RunData) -> list[str]:
    """CPU provenance from the env records - omitted (not re-derived) when a
    run predates its capture, so a regenerated report can't stamp the wrong
    hardware."""
    cpus = {
        (e.get("cpu") or {}).get("model"): (e.get("cpu") or {}).get("logical_cores")
        for e in data.envs.values()
        if e.get("cpu", {}).get("model")
    }
    if not cpus:
        return []
    model, cores = next(iter(cpus.items()))
    return [f"- CPU: `{model}` ({cores} logical cores)"]


def _fmt_encoders(data: RunData, symbology: str, output_format: str) -> list[str]:
    """Encoders with at least one decode-verified sample for this symbology and
    output format, in registry order. An encoder that only has exceptions (e.g.
    no SVG writer) is left out of the timing tables and charts - an all-`-`
    column or an empty legend entry is noise; its outcome is already in
    Correctness/coverage and Exceptions."""
    cases = data.cases_for(symbology, output_format)
    return [
        e for e in data.encoders
        if any((e, c["case_id"]) in data.samples for c in cases)
    ]


def _time_cell(
    v: float | None, best: float | None, kind: str, partial: bool = False, misdecoded: bool = False
) -> str:
    if v is None:
        # no valid sample at all. `—` is reserved for "not attempted"
        # (unsupported); anything that was attempted and failed - every trial
        # raised, or every symbol misdecoded - is an `ERR`, not a blank.
        if kind == "encode_error" or misdecoded:
            return "ERR"
        return "—"
    text = _fmt_ms(v)
    if v == best:
        text = f"**{text}**"
    if partial:
        # some trials didn't contribute a valid sample - raised at encode or
        # misdecoded - so the median is over the survivors. The dagger points
        # the reader at Exceptions for which and why; the *reason* is recorded
        # there, the cell just flags that the number is incomplete.
        text += " †"
    return text


def _int_cell(
    v: int | None,
    best: int | None,
    kind: str,
    comma: bool = False,
    partial: bool = False,
    misdecoded: bool = False,
) -> str:
    if v is None:
        # mirror _time_cell: `—` only for "not attempted" (unsupported); a
        # symbol that was produced but every trial raised or misdecoded is ERR.
        if kind == "encode_error" or misdecoded:
            return "ERR"
        return "—"
    disp = f"{v:,}" if comma else f"{v}"
    if v == best:
        text = f"**{disp}**"
    else:
        assert best is not None
        text = f"{disp} (+{(v - best) / best * 100:.0f}%)"
    if partial:
        text += " †"
    return text


def _run_header(data: RunData, symbology: str, encoders: list[str]) -> list[str]:
    title = _SYMBOLOGY_TITLES[symbology]
    m = data.manifest
    platform = next(iter(data.envs.values()))["platform"] if data.envs else "?"
    n_png = len(data.cases_for(symbology, "png"))
    n_svg = len(data.cases_for(symbology, "svg"))
    twin = "each mirrored as an SVG twin" if n_svg == n_png else f"{n_svg} with an SVG twin"
    rounds = data.rounds
    samples = data.trials_per_case * rounds
    lines = [
        f"# Barcode encoder benchmark — {title}",
        "",
        "Facts-only report generated by `barcode-bench publish` — one page per "
        "symbology. Every table's ordering and highlighting rule is stated in its "
        "caption; narrative and interpretation live elsewhere.",
        "",
        f"- Run: `{data.run_dir.name}` generated {m['generated_at']}",
        f"- Corpus: seed {data.seed}, {n_png} {title} PNG cases ({twin}), "
        f"{data.trials_per_case} trial payloads per case × {rounds} timed "
        f"round{'s' if rounds != 1 else ''} = {samples} samples per case per encoder",
        f"- Environment: containers on `{platform}`, one image per encoder over a "
        "shared `python:3.12-slim-bookworm` base",
        *_cpu_line(data),
        "",
        "Encoder labels use the exact PyPI distribution name; the **PyPI package** "
        "column links each project. `pystrich-git` is a wheel built from GitHub "
        "HEAD, not a release.",
        "",
        "| encoder | PyPI package | libraries | image id |",
        "|---|---|---|---|",
    ]
    for enc in encoders:
        libs = ", ".join(f"{k} {v}" for k, v in data.envs[enc].get("libs", {}).items())
        img = data.images["encoders"].get(enc, {})
        lines.append(
            f"| {_display(enc)} | {_project_link(enc)} | {libs} "
            f"| `{str(img.get('id', ''))[:12]}` |"
        )
    lines.append("")
    return lines


def _outcome_summary(data: RunData, enc: str, cases: list[dict]) -> str:
    counts = {"ok": 0, "partial": 0, "misdecode": 0, "unsup.": 0, "err": 0}
    for c in cases:
        cid = c["case_id"]
        has_samples = (enc, cid) in data.samples
        kind = data.exceptions.get((enc, cid), (None,))[0]
        if kind == "unsupported":
            counts["unsup."] += 1
        elif has_samples and kind == "encode_error":
            counts["partial"] += 1
        elif has_samples:
            counts["ok"] += 1
        elif kind == "encode_error":
            counts["err"] += 1
        elif data.misdecoded_trials(enc, cid):
            # encoded fine (status=ok) but no trial decode-verified: the symbol
            # is wrong, not absent. Without this bucket the case would fall
            # through every branch and vanish from the counts.
            counts["misdecode"] += 1
    return ", ".join(f"{n} {k}" for k, n in counts.items() if n) or "—"


def _coverage_section(data: RunData, symbology: str, encoders: list[str]) -> list[str]:
    png_cases = data.cases_for(symbology, "png")
    svg_cases = data.cases_for(symbology, "svg")
    # both axes: an SVG-only encoder (qrcodegen, ppf.datamatrix) has no PNG
    # symbols, so a PNG-only count would read a misleading 0 for it.
    sym_cids = {c["case_id"] for c in png_cases} | {c["case_id"] for c in svg_cases}
    lines = [
        "## Correctness and coverage",
        "",
        "Cases by outcome for this symbology, PNG and SVG axes shown separately: "
        "*ok* = every trial decode-verified against its payload (zxing-cpp; SVG "
        "rasterised host-side first); *partial* = some trials raised; *misdecode* = "
        "the symbol was produced but the reference decoder read it back as different "
        "content (a charset-ambiguous byte-mode symbol, say); *err* = every trial "
        "raised; *unsup.* = options the encoder cannot honour. Non-ok outcomes are "
        "listed under [Exceptions](#exceptions). *Verified symbols* counts "
        "decode-verified (symbol, trial) pairs across the PNG and SVG axes.",
        "",
        "| encoder | PNG | SVG | verified symbols |",
        "|---|---|---|---:|",
    ]
    for enc in encoders:
        n_valid = sum(1 for (e, cid, _t) in data.valid if e == enc and cid in sym_cids)
        lines.append(
            f"| {_display(enc)} | {_outcome_summary(data, enc, png_cases)} "
            f"| {_outcome_summary(data, enc, svg_cases)} | {n_valid} |"
        )
    lines.append("")
    return lines


def _install_section(data: RunData, encoders: list[str]) -> list[str]:
    lines = [
        "## Installation size and startup",
        "",
        "Encoder stack = full image size minus the shared Python base — i.e. the "
        "library plus every dependency it installs, **including Pillow** where the "
        "encoder renders through it (segno writes its own PNG/SVG and needs none). "
        "Sorted by stack size. Import time is the median of cold interpreter starts "
        "inside the container. Footprint is a property of the library, not the "
        "symbology; the chart shows the whole field for context.",
        "",
        "![Install weight](charts/install_weight.svg)",
        "",
        "| encoder | stack MB | full image MB | cold import ms |",
        "|---|---:|---:|---:|",
    ]
    ordered = sorted(
        (e for e in encoders if e in data.images["encoders"]),
        key=lambda e: int(data.images["encoders"][e]["delta_bytes"]),
    )
    for enc in ordered:
        info = data.images["encoders"][enc]
        imp = data.imports.get(enc)
        imp_text = _fmt_ms(statistics.median(imp) * 1000) if imp else "—"
        lines.append(
            f"| {_display(enc)} | {_fmt_mb(info['delta_bytes'])} "
            f"| {_fmt_mb(info['bytes'])} | {imp_text} |"
        )
    lines.append("")
    return lines


def _encode_time_section(data: RunData, symbology: str, output_format: str) -> list[str]:
    encoders = _fmt_encoders(data, symbology, output_format)
    cases = data.cases_for(symbology, output_format)
    if not encoders or not cases:
        return []
    title = _SYMBOLOGY_TITLES[symbology]
    label = "PNG" if output_format == "png" else "SVG"
    chart = (
        f"charts/timing_{symbology}.svg"
        if output_format == "png"
        else f"charts/timing_{symbology}_svg.svg"
    )
    if output_format == "png":
        caption = (
            "Median ms of decode-verified samples, rows ordered by payload length. "
            "**Bold** = row minimum. `†` = some trials were excluded from the median "
            "(raised at encode, or the symbol misdecoded) — see "
            "[Exceptions](#exceptions) for which and why. `ERR` = every trial failed "
            "(raised, or the symbol misdecoded); `—` = not attempted (unsupported). "
            "Chart dots are individual samples (trials × rounds); the tick/line is the "
            "median."
        )
        if "treepoem" in encoders:
            caption += (
                " treepoem's timings include a ghostscript subprocess per encode — "
                "part of its cost of use, not an artefact."
            )
    else:
        caption = (
            "Same conventions and columns as the PNG table above, for SVG output — the "
            "*same case set*. Each SVG is rasterised host-side (rsvg-convert, outside the "
            "timed region) only for the validity gate, so the timing is the encoder's "
            "vector write alone."
        )
    lines = [
        f"## Encode time ({label})",
        "",
        f"![{title} {label} encode times]({chart})",
        "",
        caption,
        "",
        "| case | payload (chars) | " + " | ".join(_display(e) for e in encoders) + " |",
        "|---|---|" + "---:|" * len(encoders),
    ]
    for case in cases:
        cid = case["case_id"]
        chars = int(statistics.median(len(t) for t in case["trials"]))
        n_trials = len(case["trials"])
        med = {e: data.median_ms(e, cid) for e in encoders}
        values = [v for v in med.values() if v is not None]
        best = min(values) if values else None
        row = [f"`{cid}`", f"{case['category']} ({chars})"]
        for e in encoders:
            # partial = the median rests on fewer than all trials because some
            # raised or misdecoded; flag it so a clean-looking number isn't read
            # as a clean run. misdecoded tells an all-failed cell apart from an
            # unsupported one (ERR vs —) when there's no valid sample.
            partial = 0 < len(data.valid_trials(e, cid)) < n_trials
            misdecoded = bool(data.misdecoded_trials(e, cid))
            row.append(
                _time_cell(
                    med[e], best, data.exceptions.get((e, cid), ("",))[0], partial, misdecoded
                )
            )
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return lines


def _size_encoders(data: RunData, symbology: str) -> list[str]:
    """Encoders with a measured symbol size for this symbology, registry order:
    the PNG participants plus SVG-only encoders (qrcodegen, ppf.datamatrix)
    whose size is measured from the decoded SVG. median_area already falls back
    to the `-svg` twin, so this just asks who has a size."""
    cases = data.cases_for(symbology, "png")
    return [
        e
        for e in data.encoders
        if any(data.median_area(e, c["case_id"]) is not None for c in cases)
    ]


def _symbol_size_section(data: RunData, symbology: str) -> list[str]:
    encoders = _size_encoders(data, symbology)
    cases = data.cases_for(symbology, "png")
    if not encoders or not cases:
        return []
    header = "| case | " + " | ".join(_display(e) for e in encoders) + " |"
    rule = "|---|" + "---:|" * len(encoders)

    # SVG-only encoders appear here via their decoded-SVG size (no PNG twin).
    svg_only = [e for e in encoders if e not in set(_fmt_encoders(data, symbology, "png"))]
    svg_only_note = (
        (
            " Sizes for "
            + ", ".join(_display(e) for e in svg_only)
            + " are measured from their decoded SVG (SVG-only libraries; the "
            "symbol is identical to the PNG a raster encoder would emit)."
        )
        if svg_only
        else ""
    )

    caption = (
        "Median module footprint (modules² excluding quiet zones), measured from the "
        "rendered symbol's module grid — render scale and quiet zones cancel out, so "
        "every encoder is on the same scale."
    )
    if symbology == "pdf417":
        caption += (
            " PDF417 rows carry ~69 modules of start/stop/indicator overhead each and "
            "render 3 modules tall, so a layout with fewer data columns but more rows "
            "can still be larger in area."
        )
    elif symbology == "aztec":
        caption += (
            " A full-range Aztec's footprint includes its reference-grid lines, so it "
            "can exceed a compact symbol of similar data capacity."
        )
    caption += (
        " **Bold** = row minimum; percentages are overhead vs it. Only decode-verified "
        "symbols are measured: `ERR` = every trial failed (raised, or the symbol "
        "misdecoded — see [Exceptions](#exceptions)); `—` = not attempted (unsupported); "
        "`†` = measured over a subset of trials (the rest raised or misdecoded)."
    )
    caption += svg_only_note

    lines = ["## Symbol size", "", caption, "", header, rule]
    for case in cases:
        cid = case["case_id"]
        svg_cid = f"{cid}-svg"
        n_trials = len(case["trials"])
        area = {e: data.median_area(e, cid) for e in encoders}
        values = [v for v in area.values() if v is not None]
        best = min(values) if values else None
        row = [f"`{cid}`"]
        for e in encoders:
            # size draws from the PNG case or its SVG twin, so validity/misdecode
            # are checked across both (same conventions as the timing cell).
            valid_ts = data.valid_trials(e, cid) | data.valid_trials(e, svg_cid)
            partial = 0 < len(valid_ts) < n_trials
            misdecoded = bool(data.misdecoded_trials(e, cid)) or bool(
                data.misdecoded_trials(e, svg_cid)
            )
            row.append(
                _int_cell(
                    area[e],
                    best,
                    data.exceptions.get((e, cid), ("",))[0],
                    comma=True,
                    partial=partial,
                    misdecoded=misdecoded,
                )
            )
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return lines


def _misdecode_reason(measures: list[dict]) -> str:
    """Human reason for a misdecode: how many trials produced no decodable
    symbol vs decoded to different content, with a sample of what the reader
    actually read back (captured by the measure stage) when available."""
    no_decode = sum(1 for m in measures if not m["decode_ok"])
    mismatched = [m for m in measures if m["decode_ok"] and not m["content_match"]]
    parts: list[str] = []
    if no_decode:
        parts.append(f"{no_decode} produced no decodable symbol")
    if mismatched:
        sample = next((m.get("decoded_text") for m in mismatched if m.get("decoded_text")), None)
        detail = f" (read back e.g. `{_table_safe(sample)}`)" if sample else ""
        parts.append(f"{len(mismatched)} decoded to different content{detail}")
    return "; ".join(parts)


def _table_safe(s: str, limit: int = 40) -> str:
    """Trim and escape decoder output for a Markdown table cell."""
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


def _misdecode_items(
    data: RunData, sym_cids: set[str]
) -> list[tuple[tuple[str, str], tuple[str, str, int]]]:
    """Produced-but-invalid outcomes for this symbology, in the same
    (key, (kind, reason, n)) shape as ``data.exceptions`` so both fold into one
    table. These carry ``status=ok`` timing records, so they are absent from
    ``data.exceptions`` - the gap this surfaces."""
    items = []
    for enc in data.encoders:
        for cid in sym_cids:
            bad = data.misdecoded_trials(enc, cid)
            if bad:
                items.append(
                    ((enc, cid), ("misdecode", _misdecode_reason(list(bad.values())), len(bad)))
                )
    return items


def _exceptions_section(data: RunData, symbology: str) -> list[str]:
    sym_cids = {cid for cid, c in data.cases.items() if c["symbology"] == symbology}
    items = sorted(
        [(k, v) for k, v in data.exceptions.items() if k[1] in sym_cids]
        + _misdecode_items(data, sym_cids)
    )
    lines = [
        "## Exceptions",
        "",
        "Every non-ok outcome for this symbology: unsupported options and encode "
        "errors carry the recorded reason verbatim; a *misdecode* (the symbol was "
        "produced but failed the decode-verify gate) shows how it failed.",
        "",
    ]
    if not items:
        lines += ["_None — every case decode-verified for every participating encoder._", ""]
        return lines
    lines += [
        "| encoder | case | outcome | trials | reason |",
        "|---|---|---|---:|---|",
    ]
    for (enc, cid), (kind, reason, n) in items:
        lines.append(f"| {_display(enc)} | `{cid}` | {kind} | {n} | {reason} |")
    lines.append("")
    return lines


def _methodology_section(data: RunData, symbology: str) -> list[str]:
    bullets = [
        "- **One corpus, all encoders.** Payloads are generated once per run from a "
        "seeded RNG and materialised to `corpus.json`; every encoder receives the "
        "identical byte-for-byte payloads. The same seed reproduces the corpus exactly.",
        "- **Same environment.** Every encoder runs in its own podman image over the "
        "same pinned Python base; libraries install from PyPI as published (except "
        "`pystrich-git`, a wheel from GitHub HEAD).",
        "- **What is timed.** The full operation *payload string → encode → image on "
        "disk*, `perf_counter`, after one untimed warm-up per option shape. Import "
        "cost is excluded and measured separately (cold-import column). Each encoder "
        "renders through the writer its own documentation shows; a library that "
        "documents no raster path runs the SVG axis only rather than being forced "
        "through a foreign shim (noted per encoder in the run's capability notes).",
        "- **Same cases, both formats.** Every PNG case has an SVG twin with "
        "byte-identical payloads. SVG rasterisation and measurement happen host-side, "
        "outside the timed region, so SVG timing is the encoder's vector write alone; "
        "the module footprint is identical for a case and its SVG twin, so it is "
        "reported once.",
        "- **Validity gate.** Every produced image (PNG, or SVG rasterised host-side) "
        "is decoded with zxing-cpp and compared to its payload; samples from failed "
        "round-trips are excluded from all statistics.",
        "- **Explicit options, never degraded.** Cases request explicit EC levels / "
        "charsets; an encoder that cannot honour a knob is recorded *unsupported* "
        "rather than silently re-configured.",
    ]
    if symbology == "datamatrix":
        bullets.append(
            "- **Square symbols.** Data Matrix cases force square symbols so the size "
            "comparison measures encodation, not symbol-grid granularity; "
            "forced-rectangular Data Matrix is a separate report."
        )
    elif symbology == "datamatrix_rect":
        bullets.append(
            "- **Forced rectangular.** Only encoders that can *force* a standard "
            "rectangular Data Matrix take part; payloads are limited to what the "
            "largest standard rectangle holds."
        )
    elif symbology == "aztec":
        bullets.append(
            "- **Aztec EC is a floor.** The requested error-correction percentage is a "
            "minimum; a symbol with slack capacity carries more, so decoders report a "
            "higher percentage than requested — expected, not an error."
        )
    return ["## Methodology", "", *bullets, ""]


def _limitations_section(data: RunData, symbology: str, encoders: list[str]) -> list[str]:
    parts = [
        "Not measured: print/scan robustness or decode margins, output file size (PNG "
        "compression / SVG verbosity), memory use, concurrency, or cross-platform "
        "variation. Timings are sequential on one machine — treat small differences "
        "accordingly; the charts draw every raw sample so the spread is visible.",
        "Symbol area is quantised: a symbol is the smallest standard size that fits its "
        "payload, so differences smaller than one size tier don't appear in the tables.",
    ]
    if "treepoem" in encoders:
        parts.append(
            "treepoem shells out to ghostscript once per encode; its timings are orders "
            "of magnitude larger by construction — the cost of that approach, kept "
            "in the tables rather than hidden."
        )
    parts.append(
        "Benchmark authored by the maintainer of pyStrich; the corpus and validity "
        "rules are encoder-neutral and the raw records ship alongside this report."
    )
    lines = ["## Limitations", ""]
    for p in parts:
        lines += [p, ""]
    return lines


def _reproduction_section(data: RunData) -> list[str]:
    return [
        "## Reproduction",
        "",
        "```sh",
        f"uv run barcode-bench all --seed {data.seed} --trials {data.trials_per_case}",
        "```",
        "",
        "Raw records alongside this report: [`corpus.json`](corpus.json), "
        "[`summary.csv`](summary.csv), [`measurements.jsonl`](measurements.jsonl), "
        "[`images.json`](images.json).",
        "",
    ]


def _symbology_page(data: RunData, symbology: str) -> list[str]:
    encoders = data.encoders_for(symbology)
    if not encoders:
        return []
    return [
        *_run_header(data, symbology, encoders),
        *_coverage_section(data, symbology, encoders),
        *_install_section(data, encoders),
        *_encode_time_section(data, symbology, "png"),
        *_encode_time_section(data, symbology, "svg"),
        *_symbol_size_section(data, symbology),
        *_exceptions_section(data, symbology),
        *_methodology_section(data, symbology),
        *_limitations_section(data, symbology, encoders),
        *_reproduction_section(data),
    ]


# --------------------------------------------------------------------------


def cmd_publish(run_dir: str) -> int:
    run_path = require_run_dir(run_dir)
    if not (run_path / "measurements.jsonl").is_file():
        raise SystemExit(f"run directory has no measurements (run `measure` first): {run_dir!r}")
    data = RunData(run_path)
    # unlike the CSV stage, the published report needs the install-weight data
    if not data.images:
        raise SystemExit(f"run directory has no images.json (run `weigh` first): {run_dir!r}")

    charts = run_path / "charts"
    charts.mkdir(exist_ok=True)
    _chart_install_weight(data, charts / "install_weight.svg")
    for sym in _SYMBOLOGY_TITLES:
        if data.cases_for(sym) and data.encoders_for(sym):
            _chart_timing(data, sym, charts / f"timing_{sym}.svg")
        if data.cases_for(sym, "svg"):
            _chart_timing(data, sym, charts / f"timing_{sym}_svg.svg", output_format="svg")

    written: list[str] = []
    index: list[str] = [
        "# Barcode encoder benchmark — reports",
        "",
        f"Facts-only reports from run `{data.run_dir.name}` (seed {data.seed}). "
        "One page per symbology; narrative and interpretation live elsewhere.",
        "",
    ]
    for sym in _SYMBOLOGY_TITLES:
        page = _symbology_page(data, sym)
        if not page:
            continue
        out = run_path / f"report_{sym}.md"
        out.write_text("\n".join(page), encoding="utf-8")
        written.append(out.name)
        index.append(f"- [{_SYMBOLOGY_TITLES[sym]}](report_{sym}.md)")
    index.append("")
    (run_path / "report.md").write_text("\n".join(index), encoding="utf-8")

    print(f"wrote report.md (index) + {len(written)} pages: {', '.join(written)}")
    print(f"wrote {charts}/ ({len(list(charts.glob('*.svg')))} charts)")
    return 0
