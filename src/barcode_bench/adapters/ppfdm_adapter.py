"""ppf.datamatrix adapter (PyPI ``ppf-datamatrix``, Data Matrix only, SVG axis).

The library is, per its own README, "a pure-python package to generate
datamatrix codes in **SVG**": its only documented output is ``.svg()`` (plus
the raw ``.matrix``). It documents no raster/PNG path, so - following the
suite rule of rendering each library the way *it* documents - this adapter
runs the **SVG axis only**.

``rect=True`` yields rectangular symbols, so this adapter also joins the
``datamatrix_rect`` symbology; plain construction is square-only, which
trivially honours the suite's ``dm_force_square`` policy.

Limitations: no base256 codec and no charset/ECI signalling - non-ASCII
payloads raise (a bare ``ValueError`` from an empty ``min()``), so
encoding-declaring cases are gated off as ``unsupported`` before ever
reaching the library.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from ppf.datamatrix import DataMatrix

from barcode_bench.adapters.base import Capabilities, EncodeOptions

_NOTE = (
    "pure Python with EDIFACT codec; SVG-only library (native svg() writer, "
    "no documented raster output) — runs the SVG axis, size derived from the "
    "decoded SVG"
)


class PpfDatamatrixAdapter:
    id = "ppfdm"
    supports = frozenset({"datamatrix", "datamatrix_rect"})

    def lib_versions(self) -> dict[str, str]:
        return {"ppf-datamatrix": version("ppf-datamatrix")}

    def capabilities(self, symbology: str) -> Capabilities:
        if symbology in ("datamatrix", "datamatrix_rect"):
            # supports_text_auto=False: any non-ASCII char raises (the bare
            # ValueError documented above), declared charset or not.
            # supports_png=False: the library documents SVG output only.
            return Capabilities(
                ec_levels=None,
                supports_eci=False,
                supports_text_auto=False,
                supports_svg=True,
                supports_png=False,
                notes=_NOTE,
            )
        raise ValueError(f"unsupported symbology {symbology!r}")

    def encode_to_svg(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        # SVG is the library's *native* format ("a pure-python package to
        # generate datamatrix codes in SVG"): DataMatrix(...).svg() is the
        # documented output, used directly. Renders whatever shape the
        # DataMatrix was built as (square, or rectangular for datamatrix_rect).
        dm = DataMatrix(payload, rect=(options.symbology == "datamatrix_rect"))
        out_path.write_text(dm.svg(), encoding="utf-8")


ADAPTER = PpfDatamatrixAdapter()
