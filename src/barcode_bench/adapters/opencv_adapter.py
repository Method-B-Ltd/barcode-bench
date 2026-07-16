"""OpenCV adapter (PyPI ``opencv-python-headless``, QR only).

Included less for its encoder pedigree than for ecosystem reality: an
enormous number of systems generate QR codes through OpenCV because it is
already installed - and its wheel makes it the install-weight heavyweight of
the Python-native field, which is exactly what the weighing axis exists to
show. The headless wheel is used (the GUI one is bigger still and needs
system GL libraries).

``QRCodeEncoder`` takes an explicit correction level and returns a bare
module matrix (uint8, 0 = dark, 255 = light, one pixel per module, no quiet
zone). Unlike the pure-encoder libraries, OpenCV *is* an image library, so it
documents its own way to turn that matrix into a PNG - ``copyMakeBorder`` for
the quiet zone, ``resize`` for scale, ``imwrite`` to save - and this adapter
uses exactly that native OpenCV I/O rather than a foreign (Pillow) shim, so
the timed render is OpenCV's own. Charset support is decided by read-back like
every other capability claim in this suite.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import cv2

from barcode_bench.adapters.base import (
    QR_EC_LEVELS,
    Capabilities,
    EncodeOptions,
)

_QUIET_ZONE_MODULES = 4

_EC_LEVELS = {
    "L": cv2.QRCodeEncoder_CORRECT_LEVEL_L,
    "M": cv2.QRCodeEncoder_CORRECT_LEVEL_M,
    "Q": cv2.QRCodeEncoder_CORRECT_LEVEL_Q,
    "H": cv2.QRCodeEncoder_CORRECT_LEVEL_H,
}


class OpenCvAdapter:
    id = "opencv"
    supports = frozenset({"qr"})

    def lib_versions(self) -> dict[str, str]:
        return {"opencv-python-headless": version("opencv-python-headless")}

    def capabilities(self, symbology: str) -> Capabilities:
        if symbology == "qr":
            return Capabilities(
                ec_levels=QR_EC_LEVELS,
                supports_eci=True,  # verified by read-back: latin-1/utf-8/kanji round-trip
                notes="PNG via OpenCV's own I/O (copyMakeBorder quiet zone, "
                "resize, imwrite); encoder returns a bare module matrix",
            )
        raise ValueError(f"unsupported symbology {symbology!r}")

    def encode_to_png(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        assert options.ec_level is not None
        # the flat-underscore aliases exist at runtime but not in cv2's stubs
        params = cv2.QRCodeEncoder.Params()
        params.correction_level = _EC_LEVELS[options.ec_level]
        matrix = cv2.QRCodeEncoder.create(params).encode(payload)
        # OpenCV's own documented image pipeline: pad the spec quiet zone
        # (copyMakeBorder), scale to module_px with nearest-neighbour (resize),
        # write the PNG (imwrite). No third-party imaging library involved.
        q = _QUIET_ZONE_MODULES
        bordered = cv2.copyMakeBorder(matrix, q, q, q, q, cv2.BORDER_CONSTANT, value=255)
        scale = options.module_px
        scaled = cv2.resize(
            bordered,
            (bordered.shape[1] * scale, bordered.shape[0] * scale),
            interpolation=cv2.INTER_NEAREST,
        )
        cv2.imwrite(str(out_path), scaled)


ADAPTER = OpenCvAdapter()
