"""pdf417gen adapter (PyPI ``pdf417gen``, PDF417 only).

Pure-Python PDF417 encoder. ``security_level`` (0..8) and ``columns`` map
straight onto the suite's canonical options and were verified effective by
read-back. Latin-1 and UTF-8 payloads round-trip exactly through zxing-cpp
via the ``encoding`` parameter.

One capability caveat: the library has **no automatic column selection** -
``columns`` is a plain parameter defaulting to 6. Cases that leave
``pdf417_columns`` unset therefore run at that fixed default where every
other PDF417 encoder here auto-sizes; that is the library's real-world
behaviour, and it is what gets measured (declared in the capability notes so
reports can flag it). A near-capacity payload at 6 columns can exceed
PDF417's 90-row limit - such failures are recorded as ``encode_error``
findings.

Rendering: ``render_image(scale, ratio, padding)`` - ``scale`` px per module,
``ratio`` row-height multiplier (kept at the spec-usual 3), ``padding`` in
pixels (set to a 2-module quiet zone to match the other PDF417 adapters).
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from pdf417gen import encode, render_image, render_svg

from barcode_bench.adapters.base import (
    PDF417_EC_LEVELS,
    Capabilities,
    EncodeOptions,
)

_QUIET_ZONE_MODULES = 2
_ROW_HEIGHT_MODULES = 3
_DEFAULT_COLUMNS = 6


class Pdf417GenAdapter:
    id = "pdf417gen"
    supports = frozenset({"pdf417"})

    def lib_versions(self) -> dict[str, str]:
        return {"pdf417gen": version("pdf417gen"), "pillow": version("pillow")}

    def capabilities(self, symbology: str) -> Capabilities:
        if symbology == "pdf417":
            return Capabilities(
                ec_levels=PDF417_EC_LEVELS,
                supports_eci=True,
                supports_pdf417_columns=True,
                supports_svg=True,
                notes="pure-Python; native PNG and SVG writers; no auto column "
                f"selection — unset columns means the library default of {_DEFAULT_COLUMNS}",
            )
        raise ValueError(f"unsupported symbology {symbology!r}")

    def _encode_codes(self, payload: str, options: EncodeOptions) -> list:
        assert options.ec_level is not None
        return encode(
            payload,
            columns=(
                options.pdf417_columns if options.pdf417_columns is not None else _DEFAULT_COLUMNS
            ),
            security_level=int(options.ec_level),
            encoding=options.encoding or "utf-8",
        )

    def encode_to_png(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        # The library's documented two-step render path (README): encode() to
        # code words, render_image() to a Pillow image, image.save(). We drive
        # it exactly that way so the timed cost is the one its users pay.
        img = render_image(
            self._encode_codes(payload, options),
            scale=options.module_px,
            ratio=_ROW_HEIGHT_MODULES,
            padding=_QUIET_ZONE_MODULES * options.module_px,
        )
        img.save(str(out_path))

    def encode_to_svg(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        # render_svg() -> ElementTree.write() is the README's documented SVG
        # path. render_svg has no padding knob (unlike render_image); the host
        # rasteriser adds a white background and zxing tolerates the tight
        # quiet zone - verified by decode read-back.
        render_svg(
            self._encode_codes(payload, options),
            scale=options.module_px,
            ratio=_ROW_HEIGHT_MODULES,
        ).write(str(out_path))


ADAPTER = Pdf417GenAdapter()
