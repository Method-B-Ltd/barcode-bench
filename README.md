# barcode-bench

Encoder benchmark suite for 2D barcode libraries: *how do Python encoder
libraries compare?* on four axes:

1. **Runtime** — the end-to-end operation *payload string → encode → PNG on
   disk*, timed with `perf_counter` around exactly that region. Library
   import cost is excluded from encode timings and measured separately
   (cold-import samples in fresh interpreters).
2. **Symbol efficiency** — every produced symbol is decoded with zxing-cpp and
   its **module area** measured straight from the rendered pixels (trim the
   quiet zone, recover the module grid, area = modules²). This is the physical
   footprint on one scale for every encoder regardless of render settings, and
   it captures what better encodation actually shrinks: fewer modules for the
   same payload. Measuring from pixels means no per-symbology structure tables
   and no adapter-declared geometry — the pixels are the source of truth for
   every symbology, PNG and SVG alike.
3. **Install weight** — each encoder stack is a podman image built `FROM` a
   bare `python:3.12-slim-bookworm` base; image size minus base size is the weight of
   that stack alone — the library **plus its dependencies, Pillow included**
   where the encoder renders through it (segno writes its own PNG/SVG and needs
   none; treepoem's ghostscript dependency is the headline case).
4. **Vector (SVG) output** — every PNG case is *also* encoded to SVG (the
   vector axis mirrors the PNG workload exactly) and timed the same way, for
   the encoders with a documented SVG writer (pyStrich, zxing-cpp, segno,
   qrcode, pdf417gen, aztec-code-generator, ppf-datamatrix, qrcodegen). The
   produced SVG is rasterised host-side (rsvg-convert, *outside* the timed region)
   and put through the identical decode-verify gate and the same pixel
   measurement. The module footprint is identical for a case and its SVG twin,
   so it is reported once; this is also how the SVG-only encoders below (which
   have no PNG) get a size at all — measured from their decoded SVG.

**Render each library the way its own docs show.** The timed operation is
"payload → image on disk", and each adapter drives the exact writer the
library documents (e.g. zxing-cpp's `to_image` + `Image.fromarray`, OpenCV's
`imwrite`, qrcodegen's `to_svg_str` demo). Where a library documents *no*
raster path, the suite will not fabricate one through a foreign shim: the
SVG-only libraries **ppf-datamatrix** (SVG-native) and **qrcodegen** (matrix +
an SVG demo, no PNG) run the SVG axis only, and their QR/DM size is derived
from the decoded SVG so they still appear in the size tables. OpenCV keeps the
PNG axis because it *is* an image library and documents its own file I/O.

## Encoders

All installed **from PyPI** (pinned in `containers/requirements/`) — with one
exception:
`pystrich-git` builds a wheel from the GitHub HEAD in a discarded builder
stage (git/clone/wheel never enter the weighed image) and reports the commit
sha as its version, so released-vs-development comparisons are explicit:

| encoder | symbologies |
|---|---|
| pystrich | QR, Data Matrix (+rect since 0.17), Aztec, PDF417 |
| pystrich-git (GitHub HEAD, version = commit sha) | QR, Data Matrix (+rect), Aztec, PDF417 |
| treepoem (BWIPP via ghostscript) | QR, Data Matrix (+rect), Aztec, PDF417 |
| zxing-cpp (writer backed by libzint) | QR, Data Matrix, Aztec, PDF417 |
| segno | QR |
| qrcode | QR |
| qrcodegen | QR |
| aztec-code-generator | Aztec |
| pdf417gen | PDF417 |
| pylibdmtx (native libdmtx) | Data Matrix (+rect) |
| ppf-datamatrix | Data Matrix (+rect) |
| OpenCV (opencv-python-headless) | QR |

(`qrencode` was evaluated and dropped — its PyPI binding is broken on
Python ≥ 3.10.)

## Prerequisites

- Linux with [podman](https://podman.io/) — rootless is fine (the suite runs
  containers with `--userns=keep-id`); the orchestrator shells out to
  `podman build` / `podman run`.
- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.12 for the host side.
- The `measure` stage rasterises SVGs with `rsvg-convert` (librsvg), a system
  binary the host must have on `PATH` (Debian/Ubuntu: `librsvg2-bin`) — `uv
  sync` does not provide it, and `measure` will fail without it.
- Expect a full run to take a while: `build` creates one image per encoder
  (~12), and treepoem's ghostscript-subprocess-per-encode dominates the run's
  wall clock.

## Running

```sh
uv sync                                   # host deps (orchestrator + measurement)
uv run barcode-bench all --seed 42        # full pipeline
uv run barcode-bench all --quick          # ~8 cases, 1 rep — smoke run
uv run barcode-bench all --symbology qr    # one symbology only (qr/datamatrix/datamatrix_rect/aztec/pdf417)

# or stage by stage (each re-runnable on its own):
uv run barcode-bench gen --seed 42 [--symbology qr]
uv run barcode-bench build                # per-encoder podman images (cached)
uv run barcode-bench weigh --run-dir runs/<id>
uv run barcode-bench run   --run-dir runs/<id> [--encoders a,b] [--reps 3]
uv run barcode-bench measure --run-dir runs/<id>
uv run barcode-bench report  --run-dir runs/<id>
uv run barcode-bench publish --run-dir runs/<id>   # per-symbology report_<sym>.md + index + charts/
```

`publish` renders the public evidence artefact — one deterministic,
GitHub-renderable page per symbology (`report_qr.md`, `report_datamatrix.md`,
…) with an index `report.md`, plus matplotlib charts. Each page carries only
that symbology's facts (correctness/coverage, install size, PNG and SVG encode
time, symbol area, exceptions) with rules stated in captions;
symbology-specific wording appears only on its own page. Narrative
interpretation is out of scope; write it elsewhere and cite the
report.

Each run lives in `runs/<UTCstamp>-seed<N>/`: `corpus.json`, `images.json`,
per-encoder `raw/<encoder>/` (timings.jsonl + PNGs), `measurements.jsonl`,
`summary.csv`, `summary.md`. `runs/` is gitignored; summaries worth keeping
are copied into `results/`.

For fast adapter iteration without containers there is an opt-in host mode:
`uv sync --group local-encoders`, then invoke `python -m barcode_bench.runner`
directly. Containers remain authoritative for published numbers.

## Fairness & methodology

- **One corpus per run, shared by all encoders.** `gen` materialises every
  payload string into `corpus.json`, which is bind-mounted read-only into
  every container. Payloads are semi-random (seeded) and realistic: digit
  runs, alphanumeric codes, mode-switching mixes, URLs, prose, Latin-1 /
  UTF-8 / Shift-JIS text, IATA BCBP-like boarding passes, and near-capacity
  payloads sized at ~85 % of each symbology's byte-mode capacity
  (`capacity.py`) in five real-world shapes: ASCII prose, well-formed XML,
  base64 blobs, JSON documents, and percent-encoded URLs.
- **T trials × R reps, reps interleaved across encoders.** Each case carries
  T (default 5) *different* payload variants — defeating any content-keyed
  caching — each timed R (default 3) times. The R repetitions run as
  round-robin *rounds*: every encoder encodes the whole corpus once, then
  every encoder again, so slow machine drift (thermal, background load)
  lands on all encoders roughly equally instead of biasing whoever ran
  last. Each round is a fresh container, making repeat samples independent
  processes — within-payload memoisation can't help either. Every raw
  sample is recorded with its round number; medians are computed only at
  report time. Per-case RNG streams (`seed:case_id`) mean adding a case
  never reshuffles the others; the same seed reproduces the corpus
  byte-for-byte.
- **Same OS, same Python.** Every encoder container extends the same pinned
  base image stage.
- **Explicit options, never silent degradation.** Cases request explicit EC
  levels/encodings; an adapter that cannot honour a knob reports the case
  `unsupported` (visible in the report as `—`) instead of encoding with
  different settings. Encoders that raise produce `encode_error` (`ERR`) —
  a finding, not a suite failure.
- **Declared vs auto charset, as paired cases.** Each non-ASCII case exists
  twice: pinned (`encoding` declared, conformant encoders signal it via
  ECI/kanji mode) and as an `*-auto` twin with byte-identical payloads and
  *no* declaration — the library receives a plain `str` and chooses its own
  representation. The pair isolates charset-*selection* quality from
  charset-*encodation* quality. Auto twins only run where the library itself
  accepts undeclared non-ASCII text (`supports_text_auto`); bytes-only or
  ASCII-only APIs (pylibdmtx, ppf-datamatrix) are `—`, and a library whose
  auto path rejects content its default charset can't express shows `ERR` —
  its own answer, recorded as data.
- **Square Data Matrix only (in `datamatrix`).** All DM cases set
  `dm_force_square`: libzint otherwise picks rectangular/DMRE sizes freely,
  which turns the size comparison into a symbol-grid-granularity contest
  instead of an encodation contest (pyStrich and BWIPP are square-by-default).
- **`datamatrix_rect` is a separate symbology** for *forced* rectangular
  Data Matrix (small payloads only — standard rectangles top out at 49 data
  codewords). Only encoders that can guarantee rectangular output take part:
  treepoem (BWIPP `format=rectangle`), pylibdmtx (`RectAuto`),
  ppf-datamatrix (`rect=True`), zxing-cpp (zint size indices 25-30 via
  `version`, smallest-first — verified by read-back after the boolean
  spellings all proved to be silently swallowed), and pyStrich
  (`symbol_shape="rectangular"`, since 0.17; the git HEAD build inherits it).
- **Validity gate.** A timing sample only enters the summary statistics if
  its PNG decoded back to the exact payload (zxing-cpp, host-side). Cases
  with invalid trials surface a `validity_rate < 1` (`⚠` in the report).
- **zxing-cpp writer caveat.** `create_barcode` silently ignores unknown
  kwargs; every option this suite passes was verified by decode read-back.

## Layout

```
containers/            one parametrised multi-stage Containerfile + pinned
                       per-encoder requirements
src/barcode_bench/
  cli.py               subcommand entry point (one per pipeline stage)
  corpus.py            case matrix + seeded payload generation
  capacity.py          ISO capacity tables (near-capacity sizing)
  adapters/            EncoderAdapter protocol + one module per library
  runner.py            in-container timed loop -> timings.jsonl + PNGs/SVGs
  orchestrate.py       host: podman build / weigh / run
  measure.py           host: decode (rasterise SVGs), verify, measure size
  pixel_measure.py     module footprint from pixels: trim, pitch, grid fit
  zxing_formats.py     shared symbology -> zxing-cpp format map
  rundata.py           shared loader for a completed run (validity gate)
  report.py            aggregate -> summary.csv / summary.md
  publish.py           facts-only per-symbology report pages + charts
```

## Adding an encoder

1. `src/barcode_bench/adapters/<name>_adapter.py` exporting `ADAPTER`
   (see `adapters/base.py`; the registry in `adapters/__init__.py` is lazy —
   never import encoder libs at registry level).
2. One line in `ADAPTER_MODULES`.
3. `containers/requirements/<name>.txt` with `==` pins; if the library needs
   system packages, add them to `ENCODER_APT_PACKAGES` in `orchestrate.py`.
4. Implement the output method for each format the library documents:
   `encode_to_png` and/or `encode_to_svg`. The two are independent axes, each
   gated by its own capability flag - `supports_png` (on by default) and
   `supports_svg` (off by default). A raster-only library implements just
   `encode_to_png`; an SVG-only library (qrcodegen, ppf-datamatrix) sets
   `supports_png=False` and implements just `encode_to_svg`; a library with
   both writers does both. The corpus already carries a PNG and an SVG variant
   of every case, so no corpus change is needed - the runner schedules each
   variant only for the formats the adapter declares and records the rest
   `unsupported`.
5. Verify options by decode read-back, then declare accurate `capabilities()`
   (including `notes` for anything a report reader should know).

## Output formats: PNG and SVG

`EncodeOptions.output_format` selects `"png"` or `"svg"`, and the two are
co-equal axes. `corpus.py` carries a PNG and an SVG variant of every case with
byte-identical payloads, so output format is the only variable between the
twins. The runner dispatches each variant to `encode_to_png` / `encode_to_svg`
for the adapters that declare `supports_png` / `supports_svg`, and records the
rest `unsupported` - which is how the raster-only and SVG-only libraries each
run just their own axis. `measure.py` rasterises SVGs with rsvg-convert
**outside** the timed region and puts them through the same decode-verify gate
as PNGs.

Symbol geometry is derived once per payload: for QR and Data Matrix it comes
wholly from the decoder metadata, read from whichever variant decoded (which is
how the SVG-only encoders enter the size tables); for Aztec and PDF417 it needs
pixel dimensions and is taken from the PNG, with the SVG twin carrying encode
time and validity only. Reports render PNG and SVG encode-time sections. Only
the host measurement environment needs rsvg-convert; encoder containers never
rasterise.

## Motivations

This project came about as an offshoot of QA/improvement work on
[pyStrich](https://www.method-b.uk/pyStrich/docs/), that
I realised would be a good fit for a public benchmark. I acknowledge my
own conflict of interest as a maintainer of pyStrich, but have tried to make this
benchmark as fair as possible.

## License

MIT — see [`LICENSE`](LICENSE).