"""pyStrich adapter (PyPI ``pystrich``, all four symbologies).

API per symbology:
``*Data(text, encoding=...)`` wraps the payload with its charset (pyStrich
emits ECI for non-default charsets), the encoder class takes the
symbology-specific knobs, and ``.save(path, cellsize=...)`` renders a PNG at
``cellsize`` px per module.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

from pystrich.aztec import AztecData, AztecEncoder
from pystrich.datamatrix import DataMatrixData, DataMatrixEncoder
from pystrich.pdf417 import PDF417Data, PDF417Encoder, PDF417ErrorCorrectionLevel
from pystrich.qrcode import QRCodeData, QRCodeEncoder, QRErrorCorrectionLevel

from barcode_bench.adapters.base import (
    AZTEC_EC_LEVELS,
    PDF417_EC_LEVELS,
    QR_EC_LEVELS,
    Capabilities,
    EncodeOptions,
)


class PystrichAdapter:
    id = "pystrich"
    # datamatrix_rect since 0.17: rectangular DM (symbol_shape) shipped in that
    # release (b1e18a0), so the released package enters it just like git HEAD.
    supports = frozenset({"qr", "datamatrix", "datamatrix_rect", "aztec", "pdf417"})

    def lib_versions(self) -> dict[str, str]:
        return {"pystrich": version("pystrich"), "pillow": version("pillow")}

    def capabilities(self, symbology: str) -> Capabilities:
        # Native SVG writer (save_svg) for every symbology.
        if symbology == "qr":
            return Capabilities(ec_levels=QR_EC_LEVELS, supports_eci=True, supports_svg=True)
        if symbology == "datamatrix":
            return Capabilities(ec_levels=None, supports_eci=True, supports_svg=True)
        if symbology == "datamatrix_rect":
            return Capabilities(
                ec_levels=None,
                supports_eci=True,
                supports_svg=True,
                notes='rectangular DM via symbol_shape="rectangular" (pyStrich 0.17+)',
            )
        if symbology == "aztec":
            return Capabilities(
                ec_levels=AZTEC_EC_LEVELS,
                supports_eci=True,
                supports_svg=True,
            )
        if symbology == "pdf417":
            return Capabilities(
                ec_levels=PDF417_EC_LEVELS,
                supports_eci=True,
                supports_pdf417_columns=True,
                supports_svg=True,
            )
        raise ValueError(f"unsupported symbology {symbology!r}")

    def _build_encoder(self, payload: str, options: EncodeOptions) -> Any:
        # Charset policy (verified per-trial against run data): a plain str
        # takes the library's idiomatic auto-selection path (the *Data
        # wrappers exist only to PIN an encoding); a case-declared encoding
        # is passed as that pin. For latin-1/utf-8 payloads auto and pinned
        # produce identical symbols, so the pin only matters where it
        # unlocks capability the auto path avoids: shift_jis
        # enabling QR kanji mode (auto picks UTF-8 there, costing a version
        # tier). Same philosophy as the segno adapter.
        text: Any = payload
        if options.encoding is not None:
            wrapper = {
                "qr": QRCodeData,
                "datamatrix": DataMatrixData,
                "datamatrix_rect": DataMatrixData,
                "aztec": AztecData,
                "pdf417": PDF417Data,
            }[options.symbology]
            text = wrapper(payload, encoding=cast(Any, options.encoding))

        if options.symbology == "qr":
            ecl = cast(QRErrorCorrectionLevel | None, options.ec_level)
            return QRCodeEncoder(text, ecl=ecl)
        if options.symbology in ("datamatrix", "datamatrix_rect"):
            # symbol_shape shipped in pyStrich 0.17; both the released and git
            # adapters route datamatrix_rect here now. The kwarg is passed only
            # for the rect symbology, so square cases keep the default shape.
            kwargs: dict[str, Any] = (
                {"symbol_shape": "rectangular"}
                if options.symbology == "datamatrix_rect"
                else {}
            )
            return DataMatrixEncoder(text, **kwargs)
        if options.symbology == "aztec":
            assert options.ec_level is not None
            return AztecEncoder(text, ecc=int(options.ec_level))
        if options.symbology == "pdf417":
            assert options.ec_level is not None
            return PDF417Encoder(
                text,
                ecl=cast(PDF417ErrorCorrectionLevel, int(options.ec_level)),
                columns=options.pdf417_columns,
            )
        raise ValueError(f"unsupported symbology {options.symbology!r}")

    def encode_to_png(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        self._build_encoder(payload, options).save(str(out_path), cellsize=options.module_px)

    def encode_to_svg(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        # save_svg mirrors save: same encoder, vector renderer, cellsize px
        # per module (cancels out in the module-grid geometry the PNG axis
        # already measures).
        self._build_encoder(payload, options).save_svg(str(out_path), cellsize=options.module_px)


ADAPTER = PystrichAdapter()
