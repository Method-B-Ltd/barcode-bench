"""qrcodegen adapter (PyPI ``qrcodegen``, QR only, SVG axis only).

The reference implementation ships no image output: encoding
yields a module matrix (``get_module``/``get_size``) and nothing else. Its
project *demo* documents exactly one image renderer - ``to_svg_str`` (plus a
console printer) - and no PNG path at all. The suite renders each library the
way its own docs show, so qrcodegen runs the **SVG axis only**: we reproduce
the demo's ``to_svg_str`` faithfully and record png cases as unsupported
(``supports_png=False``) rather than inventing a raster path the library
never documents. Its symbol size still compares against the other QR encoders
- derived host-side from the decoded SVG (see ``measure.py``).

``encode_text`` auto-segments (numeric/alphanumeric/byte/kanji) but offers
no ECI signalling, so encoding-declaring cases are unsupported, same policy
as the qrcode adapter.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from qrcodegen import QrCode

from barcode_bench.adapters.base import (
    QR_EC_LEVELS,
    Capabilities,
    EncodeOptions,
)

_QUIET_ZONE_MODULES = 4

_ECC = {
    "L": QrCode.Ecc.LOW,
    "M": QrCode.Ecc.MEDIUM,
    "Q": QrCode.Ecc.QUARTILE,
    "H": QrCode.Ecc.HIGH,
}


def _to_svg_str(qr: QrCode, border: int) -> str:
    """The QR-Code-generator project demo's documented SVG renderer,
    reproduced verbatim in structure (one ``h1v1`` path op per dark module,
    white background rect, viewBox sized to symbol + border). This is the
    library's *own* documented way to turn a QrCode into an image, so timing
    it is timing the path its users are shown.

    Source: https://github.com/nayuki/QR-Code-generator (python/qrcodegen-demo.py)
    """
    if border < 0:
        raise ValueError("Border must be non-negative")
    parts: list[str] = []
    for y in range(qr.get_size()):
        for x in range(qr.get_size()):
            if qr.get_module(x, y):
                parts.append(f"M{x + border},{y + border}h1v1h-1z")
    size = qr.get_size() + border * 2
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'viewBox="0 0 {size} {size}" stroke="none">\n'
        '\t<rect width="100%" height="100%" fill="#FFFFFF"/>\n'
        f'\t<path d="{" ".join(parts)}" fill="#000000"/>\n'
        "</svg>\n"
    )


class QrcodegenAdapter:
    id = "qrcodegen"
    supports = frozenset({"qr"})

    def lib_versions(self) -> dict[str, str]:
        return {"qrcodegen": version("qrcodegen")}

    def capabilities(self, symbology: str) -> Capabilities:
        if symbology == "qr":
            return Capabilities(
                ec_levels=QR_EC_LEVELS,
                supports_eci=False,
                supports_svg=True,
                supports_png=False,
                notes="SVG only (library outputs a module matrix; its demo "
                "documents to_svg_str, no PNG path) — no raster axis",
            )
        raise ValueError(f"unsupported symbology {symbology!r}")

    def encode_to_svg(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        # encode_text (the library's only documented output) -> to_svg_str (its
        # demo's documented renderer). module_px is irrelevant to the vector
        # (viewBox units are modules); the border is the spec 4-module quiet zone.
        assert options.ec_level is not None
        qr = QrCode.encode_text(payload, _ECC[options.ec_level])
        out_path.write_text(_to_svg_str(qr, _QUIET_ZONE_MODULES), encoding="utf-8")


ADAPTER = QrcodegenAdapter()
