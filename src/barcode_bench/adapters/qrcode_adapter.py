"""python-qrcode adapter (PyPI ``qrcode``, QR only).

Renders through Pillow (``make_image``). The library exposes no charset/ECI
control - non-ASCII payloads are emitted as unattributed UTF-8 bytes and
there is no kanji mode - so every case that declares an ``encoding`` is
reported ``unsupported`` rather than silently encoded with different
semantics than the case intends (plan default; the utf8 payloads would often
still round-trip via decoder charset guessing, but that measures the decoder,
not the encoder).

``fit=True`` lets the library pick the smallest version, matching the
auto-sizing behaviour of every other adapter here.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import qrcode
import qrcode.constants
import qrcode.image.svg

from barcode_bench.adapters.base import (
    QR_EC_LEVELS,
    Capabilities,
    EncodeOptions,
)

_QUIET_ZONE_MODULES = 4

_EC_CONSTANTS = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}


class QrcodeAdapter:
    id = "qrcode"
    supports = frozenset({"qr"})

    def lib_versions(self) -> dict[str, str]:
        return {"qrcode": version("qrcode"), "pillow": version("pillow")}

    def capabilities(self, symbology: str) -> Capabilities:
        if symbology == "qr":
            return Capabilities(
                ec_levels=QR_EC_LEVELS,
                supports_eci=False,
                supports_svg=True,
                notes="no ECI/charset control; PNG and SVG via Pillow image factories",
            )
        raise ValueError(f"unsupported symbology {symbology!r}")

    def _make(self, payload: str, options: EncodeOptions) -> qrcode.QRCode:
        assert options.ec_level is not None
        qr = qrcode.QRCode(
            error_correction=_EC_CONSTANTS[options.ec_level],
            box_size=options.module_px,
            border=_QUIET_ZONE_MODULES,
        )
        qr.add_data(payload)
        qr.make(fit=True)
        return qr

    def encode_to_png(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        # QRCode()/add_data/make(fit=True)/make_image().save() is the README's
        # documented generation path (see _make); we follow it verbatim.
        self._make(payload, options).make_image().save(str(out_path))

    def encode_to_svg(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        # SvgPathImage: one <path> for the whole symbol (compact), vs
        # SvgImage's rect-per-module. Same module grid either way.
        img = self._make(payload, options).make_image(
            image_factory=qrcode.image.svg.SvgPathImage
        )
        img.save(str(out_path))


ADAPTER = QrcodeAdapter()
