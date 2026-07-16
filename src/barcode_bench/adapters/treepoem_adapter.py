"""treepoem adapter (PyPI ``treepoem``: BWIPP PostScript rendered by ghostscript).

The heavyweight of the suite: every encode round-trips through a ghostscript
subprocess, and the container image carries the full ghostscript stack - this
adapter is the reason the install-weight measurement exists.

Charset handling (all probed against zxing-cpp read-back): ASCII payloads
pass through as ``str``. Cases that declare an encoding get the conformant
treatment pyStrich also applies - a *declared* ECI: payload bytes in the
declared charset, prefixed with BWIPP's ``^ECI0000NN`` escape (3 Latin-1,
20 Shift-JIS, 26 UTF-8) under the ``parsefnc`` option. Any ``^`` byte in the
encoded payload would be swallowed as an escape introducer, so it is doubled
to ``^^`` (BWIPP's literal-caret escape under ``parsefnc``; the ``^NNN``
numeric escape of the ``parse`` option is NOT usable here - ``parse`` decodes
``^094`` back to a raw caret *before* ``parsefnc`` scans for functions, which
then trips on it). The doubling must happen at the *byte* level, after
charset encoding: Shift-JIS trail bytes are free to be 0x5E, so a payload
with no literal ``^`` character can still contain ``^`` bytes - string-level
escaping misses those, BWIPP rejects the data with an error message that
echoes the raw bytes, and treepoem crashes trying to UTF-8-decode that
message from ghostscript's stderr. ASCII cases enable no parse options.

Render geometry (probed): BWIPP draws matrix symbologies at 2 points per
module but PDF417 at 1 point per module, and ghostscript's bounding box
shaves one pixel - so for a requested ``module_px`` the scale passed to
treepoem differs per symbology (``module_px/2`` vs ``module_px``), which is
why ``module_px`` must be even. BWIPP adds no quiet zones. PDF417 rows render
3 modules tall (BWIPP ``rowmult`` default).
"""

from __future__ import annotations

import subprocess
from importlib.metadata import version
from pathlib import Path

import treepoem

from barcode_bench.adapters.base import (
    AZTEC_EC_LEVELS,
    PDF417_EC_LEVELS,
    QR_EC_LEVELS,
    Capabilities,
    EncodeOptions,
)

_BWIPP_TYPES = {
    "qr": "qrcode",
    "datamatrix": "datamatrix",
    "datamatrix_rect": "datamatrix",  # same BWIPP encoder, format=rectangle
    "aztec": "azteccode",
    "pdf417": "pdf417",
}

_ECI_NUMBERS = {"iso-8859-1": 3, "shift_jis": 20, "utf-8": 26}

_NOTE = "BWIPP via ghostscript subprocess; declared-ECI via ^ECI escapes"


class TreepoemAdapter:
    id = "treepoem"
    supports = frozenset({"qr", "datamatrix", "datamatrix_rect", "aztec", "pdf417"})

    def lib_versions(self) -> dict[str, str]:
        gs = subprocess.run(
            ["gs", "--version"], capture_output=True, text=True, check=False
        ).stdout.strip()
        return {
            "treepoem": version("treepoem"),
            "pillow": version("pillow"),
            "ghostscript": gs or "unknown",
        }

    def capabilities(self, symbology: str) -> Capabilities:
        # supports_text_auto=False everywhere: the charset handling here (ECI
        # prefix, byte-level caret escaping) is adapter-authored and only
        # covers the *declared*-encoding path. How treepoem itself marshals a
        # non-ASCII plain str into the PostScript program is version-dependent
        # and unverified - grading it as BWIPP's "auto" would be fiction.
        if symbology == "qr":
            return Capabilities(
                ec_levels=QR_EC_LEVELS,
                supports_eci=True,
                supports_text_auto=False,
                notes=_NOTE,
            )
        if symbology in ("datamatrix", "datamatrix_rect"):
            return Capabilities(
                ec_levels=None, supports_eci=True, supports_text_auto=False, notes=_NOTE
            )
        if symbology == "aztec":
            return Capabilities(
                ec_levels=AZTEC_EC_LEVELS,
                supports_eci=True,
                supports_text_auto=False,
                notes=_NOTE + "; EC percentage is a minimum",
            )
        if symbology == "pdf417":
            return Capabilities(
                ec_levels=PDF417_EC_LEVELS,
                supports_eci=True,
                supports_pdf417_columns=True,
                supports_text_auto=False,
                notes=_NOTE,
            )
        raise ValueError(f"unsupported symbology {symbology!r}")

    def encode_to_png(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        bwipp_options: dict[str, str | bool] = {}
        if options.ec_level is not None:
            bwipp_options["eclevel"] = options.ec_level
        if options.pdf417_columns is not None:
            bwipp_options["columns"] = str(options.pdf417_columns)
        if options.dm_force_square:
            # BWIPP's datamatrix defaults to square already; pin it anyway so
            # the guarantee doesn't rest on an upstream default.
            bwipp_options["format"] = "square"
        if options.symbology == "datamatrix_rect":
            bwipp_options["format"] = "rectangle"

        data: str | bytes = payload
        if options.encoding is not None:
            eci = _ECI_NUMBERS[options.encoding]
            body = payload.encode(options.encoding).replace(b"^", b"^^")
            data = f"^ECI{eci:06d}".encode("ascii") + body
            bwipp_options["parsefnc"] = True

        if options.symbology == "pdf417":
            scale = options.module_px  # BWIPP draws PDF417 at 1 pt/module
        else:
            if options.module_px % 2:
                raise ValueError("treepoem needs an even module_px for matrix symbologies")
            scale = options.module_px // 2  # ...but matrix codes at 2 pt/module

        img = treepoem.generate_barcode(
            _BWIPP_TYPES[options.symbology], data, bwipp_options, scale=scale
        )
        img.save(str(out_path))


ADAPTER = TreepoemAdapter()
