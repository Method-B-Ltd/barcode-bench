"""segno adapter (PyPI ``segno``, QR only).

Pure-Python with its own PNG writer - the only adapter here whose timed path
touches neither Pillow nor any native code.

Two fairness-relevant details:

- ``boost_error=False``. segno silently upgrades the error level when the
  chosen version has slack; benchmarking explicit-ECL cases requires the
  symbol to carry exactly the requested level, like every other adapter.
- ``eci=True`` only for cases that declare a non-ASCII charset, paired with
  the case's ``encoding``, so charset signalling matches what the case
  intends. Shift-JIS payloads need no ECI: segno picks QR kanji mode
  natively (what the kanji corpus case exercises).

``save(..., border=4)`` pins the spec quiet zone explicitly rather than
trusting the writer default.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import segno

from barcode_bench.adapters.base import (
    QR_EC_LEVELS,
    Capabilities,
    EncodeOptions,
)

_QUIET_ZONE_MODULES = 4


class SegnoAdapter:
    id = "segno"
    supports = frozenset({"qr"})

    def lib_versions(self) -> dict[str, str]:
        return {"segno": version("segno")}

    def capabilities(self, symbology: str) -> Capabilities:
        if symbology == "qr":
            return Capabilities(
                ec_levels=QR_EC_LEVELS,
                supports_eci=True,
                supports_svg=True,
                notes="native PNG and SVG writers; boost_error disabled for explicit-ECL fairness",
            )
        raise ValueError(f"unsupported symbology {symbology!r}")

    def _make(self, payload: str, options: EncodeOptions) -> segno.QRCode:
        assert options.ec_level is not None
        use_eci = options.encoding in ("iso-8859-1", "utf-8")
        return segno.make_qr(
            payload,
            error=options.ec_level.lower(),
            boost_error=False,
            encoding=options.encoding,
            eci=use_eci,
        )

    def encode_to_png(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        # save(kind=, scale=, border=) is segno's documented writer - a native
        # PNG writer (no Pillow), which is exactly what we want to time here.
        self._make(payload, options).save(
            str(out_path), kind="png", scale=options.module_px, border=_QUIET_ZONE_MODULES
        )

    def encode_to_svg(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        self._make(payload, options).save(
            str(out_path), kind="svg", scale=options.module_px, border=_QUIET_ZONE_MODULES
        )


ADAPTER = SegnoAdapter()
