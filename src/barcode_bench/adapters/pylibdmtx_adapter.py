"""pylibdmtx adapter (PyPI ``pylibdmtx`` wrapping native libdmtx, DM only).

The only encoder here that covers BOTH Data Matrix symbologies: ``size=
"SquareAuto"`` honours the suite's square-only policy for ``datamatrix`` and
``size="RectAuto"`` makes it treepoem's first companion in
``datamatrix_rect``.

Encodation runs at the library default (ASCII scheme). libdmtx *has*
best-mode selection (DmtxSchemeAutoBest) but it is unreachable through
pylibdmtx 0.1.10's public API: ``encode()`` applies ``scheme.capitalize()``
to the name, mangling ``"AutoBest"`` into the nonexistent ``"Autobest"`` -
only single-word scheme names survive. The suite benchmarks libraries as
their public PyPI API ships them, so this stays unpatched and declared in
the capability notes; expect visibly larger symbols on text-heavy payloads
than the mode-optimising encoders produce.

No ECI/charset signalling exists in libdmtx's writer, so encoding-declaring
cases are unsupported - same policy as qrcode/qrcodegen.

Render geometry is fixed by libdmtx (not configurable through pylibdmtx):
5 px per module and a 10 px margin (= 2 modules).

Packaging notes: pylibdmtx 0.1.10 imports ``distutils`` (gone in Python
3.12), so setuptools must be installed as the shim; the container also needs
the ``libdmtx0b`` system library (an ENCODER_APT_PACKAGES entry, and another
data point for the install-weight axis).
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from PIL import Image
from pylibdmtx.pylibdmtx import encode

from barcode_bench.adapters.base import Capabilities, EncodeOptions

_NOTE = (
    "ASCII scheme only: AutoBest mode selection is unreachable via "
    "pylibdmtx 0.1.10's public API (scheme-name capitalisation bug); "
    "fixed 5 px/module render (library default, not configurable — "
    "slightly more PNG-write work than the 4 px/module the suite requests)"
)


class PylibdmtxAdapter:
    id = "pylibdmtx"
    supports = frozenset({"datamatrix", "datamatrix_rect"})

    def lib_versions(self) -> dict[str, str]:
        return {"pylibdmtx": version("pylibdmtx"), "pillow": version("pillow")}

    def capabilities(self, symbology: str) -> Capabilities:
        if symbology in ("datamatrix", "datamatrix_rect"):
            # supports_text_auto=False: encode() takes bytes, so a non-ASCII
            # str with no declared charset would need the *adapter* to pick
            # an encoding - adapter-authored encodation, not library auto.
            return Capabilities(
                ec_levels=None, supports_eci=False, supports_text_auto=False, notes=_NOTE
            )
        raise ValueError(f"unsupported symbology {symbology!r}")

    def encode_to_png(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        size = "RectAuto" if options.symbology == "datamatrix_rect" else "SquareAuto"
        enc = encode(payload.encode("ascii"), size=size)
        Image.frombytes("RGB", (enc.width, enc.height), enc.pixels).save(str(out_path))


ADAPTER = PylibdmtxAdapter()
