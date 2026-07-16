"""aztec-code-generator adapter (PyPI ``aztec_code_generator``, Aztec only).

Pure-Python Aztec encoder with mode optimisation
(``find_optimal_sequence``) and ECI support via its ``encoding`` parameter
(it carries an ``encoding_to_eci`` table; Latin-1, UTF-8 and Shift-JIS
payloads all round-trip through zxing-cpp with exact content). ``ec_percent``
maps directly onto the suite's canonical Aztec EC vocabulary and was verified
effective by read-back (50 % produces a larger symbol than the 23 % default).
It does pick compact symbols when they fit.

Rendering is a PIL image at ``module_size`` px per module; ``border`` is the
quiet zone in modules - pinned to 2 to match the pyStrich adapter's Aztec
geometry. The library also has a native SVG writer (``save_svg``, reached via
``save`` on a ``.svg`` filename), so this adapter joins the SVG axis too.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from aztec_code_generator import AztecCode

from barcode_bench.adapters.base import (
    AZTEC_EC_LEVELS,
    Capabilities,
    EncodeOptions,
)

_QUIET_ZONE_MODULES = 2


class AztecGenAdapter:
    id = "aztecgen"
    supports = frozenset({"aztec"})

    def lib_versions(self) -> dict[str, str]:
        return {
            "aztec-code-generator": version("aztec-code-generator"),
            "pillow": version("pillow"),
        }

    def capabilities(self, symbology: str) -> Capabilities:
        if symbology == "aztec":
            return Capabilities(
                ec_levels=AZTEC_EC_LEVELS,
                supports_eci=True,
                supports_svg=True,
                notes=(
                    "pure-Python; native PNG (PIL) and SVG writers; EC "
                    "percentage is a minimum; undeclared-charset text defaults "
                    "to Latin-1 (content beyond it raises — surfaces as ERR on "
                    "utf8-auto cases)"
                ),
            )
        raise ValueError(f"unsupported symbology {symbology!r}")

    def _build(self, payload: str, options: EncodeOptions) -> AztecCode:
        assert options.ec_level is not None
        return AztecCode(payload, ec_percent=int(options.ec_level), encoding=options.encoding)

    def encode_to_png(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        # AztecCode(...).save(path, module_size=, border=) is the README's
        # documented image method; we call it directly (native PIL PNG writer).
        self._build(payload, options).save(
            str(out_path), module_size=options.module_px, border=_QUIET_ZONE_MODULES
        )

    def encode_to_svg(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        # save() routes to the native save_svg on the .svg extension.
        self._build(payload, options).save(
            str(out_path), module_size=options.module_px, border=_QUIET_ZONE_MODULES
        )


ADAPTER = AztecGenAdapter()
