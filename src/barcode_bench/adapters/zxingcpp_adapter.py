"""zxing-cpp adapter (PyPI ``zxing-cpp`` >= 3, writer backed by libzint).

CAUTION verified empirically: ``create_barcode`` silently ignores unknown
kwargs, so every option used here was validated by encode -> decode read-back
(requested EC level visible in the decode's ``extra``, requested PDF417
``columns`` visible in symbol width) rather than by "the call didn't fail".

Payloads are always passed as ``str``: the writer then handles charset
selection/ECI itself and Latin-1, UTF-8 and Shift-JIS payloads all round-trip
exactly. Passing pre-encoded bytes instead breaks UTF-8/Shift-JIS round-trips
(the reader sees unattributed binary), so the harness never does that.

Aztec's requested EC percentage is a *minimum*: symbols have fixed capacity
per layer count, and slack becomes extra error correction, so decoders report
a higher percentage than requested. That is symbology behaviour, not an
option failure.

The library rasterises (``to_image``) but has no PNG writer of its own, so
the PNG is written through Pillow - inside the timed region, same as every
other adapter's file write, and declared in the capability notes.

The writer may
legitimately pick rectangular Data Matrix symbols.

``datamatrix_rect`` support (2026-07-11 read-back probe): none of the
boolean rectangle spellings exist (all silently swallowed), but ``version``
maps onto zint's size-request knob (``option_2``), where indices 25-30 are
the six standard rectangles 8x18..16x48 - each pins its exact size, and an
oversized payload raises zint's "Error 522: Input too long", proving
the option is live. Rect-*auto* is therefore a smallest-first search over
those six versions, adapter-side; the failed attempts stay inside the timed
region (the cost of getting rect-auto from this API) and the search is
declared in the capability notes.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import zxingcpp
from PIL import Image

from barcode_bench.adapters.base import (
    AZTEC_EC_LEVELS,
    PDF417_EC_LEVELS,
    QR_EC_LEVELS,
    Capabilities,
    EncodeOptions,
)
from barcode_bench.zxing_formats import FORMATS as _FORMATS

# zint option_2 size indices for the six standard DM rectangles, capacity
# order (8x18 .. 16x48). Exposed through create_barcode's `version` kwarg.
_DM_RECT_VERSIONS = (25, 26, 27, 28, 29, 30)

_PNG_NOTE = (
    "raster via library, PNG written through Pillow (no native PNG writer); "
    "native SVG writer (to_svg)"
)


class ZxingCppAdapter:
    id = "zxingcpp"
    supports = frozenset({"qr", "datamatrix", "datamatrix_rect", "aztec", "pdf417"})

    def lib_versions(self) -> dict[str, str]:
        return {"zxing-cpp": version("zxing-cpp"), "pillow": version("pillow")}

    def capabilities(self, symbology: str) -> Capabilities:
        if symbology == "qr":
            return Capabilities(
                ec_levels=QR_EC_LEVELS, supports_eci=True, supports_svg=True, notes=_PNG_NOTE
            )
        if symbology == "datamatrix":
            return Capabilities(
                ec_levels=None, supports_eci=True, supports_svg=True, notes=_PNG_NOTE
            )
        if symbology == "datamatrix_rect":
            return Capabilities(
                ec_levels=None,
                supports_eci=True,
                supports_svg=True,
                notes=_PNG_NOTE
                + "; rect sizes pinned via zint size indices (version=25..30), "
                "smallest-first adapter-side search — failed fits are timed",
            )
        if symbology == "aztec":
            return Capabilities(
                ec_levels=AZTEC_EC_LEVELS,
                supports_eci=True,
                supports_svg=True,
                notes=_PNG_NOTE + "; EC percentage is a minimum",
            )
        if symbology == "pdf417":
            return Capabilities(
                ec_levels=PDF417_EC_LEVELS,
                supports_eci=True,
                supports_pdf417_columns=True,
                supports_svg=True,
                notes=_PNG_NOTE,
            )
        raise ValueError(f"unsupported symbology {symbology!r}")

    def _build_barcode(self, payload: str, options: EncodeOptions) -> zxingcpp.Barcode:
        kwargs: dict[str, object] = {}
        if options.ec_level is not None:
            # Aztec EC must be percent-suffixed: bare numbers ("50") are
            # silently ignored while "50%" takes effect - verified by
            # read-back, one more instance of the swallowed-kwargs hazard.
            suffix = "%" if options.symbology == "aztec" else ""
            kwargs["ec_level"] = options.ec_level + suffix
        if options.pdf417_columns is not None:
            kwargs["columns"] = options.pdf417_columns
        if options.dm_force_square:
            # camelCase per CreatorOptions' JSON keys; the snake_case
            # force_square_data_matrix of the old writer API is swallowed
            # silently. Verified by read-back (8x64 -> 22x22).
            kwargs["forceSquare"] = True
        if options.symbology == "datamatrix_rect":
            return self._create_rect(payload)
        return zxingcpp.create_barcode(payload, _FORMATS[options.symbology], **kwargs)

    def encode_to_png(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        # to_image -> Image.fromarray -> save is the writer path zxing-cpp's own
        # README documents; benchmark the library the way its users are told to
        # render, so the timed cost is the one they'd actually pay.
        img = self._build_barcode(payload, options).to_image(scale=options.module_px)
        Image.fromarray(img).save(str(out_path))

    def encode_to_svg(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        # Native vector writer; scale is irrelevant to the module grid the
        # host rasteriser re-scales for decode.
        out_path.write_text(self._build_barcode(payload, options).to_svg(), encoding="utf-8")

    def _create_rect(self, payload: str) -> zxingcpp.Barcode:
        """Smallest standard rectangle that fits, by trying zint size
        indices in capacity order. A too-small size raises zint's
        capacity error, so the first success IS the smallest fit; if even
        16x48 raises, that error propagates as the encode_error finding."""
        for i, v in enumerate(_DM_RECT_VERSIONS):
            try:
                return zxingcpp.create_barcode(
                    payload, zxingcpp.BarcodeFormat.DataMatrix, version=v
                )
            except ValueError:
                if i == len(_DM_RECT_VERSIONS) - 1:
                    raise
        raise AssertionError("unreachable")


ADAPTER = ZxingCppAdapter()
