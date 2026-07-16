"""Encoder adapter protocol and the option/capability vocabulary.

Every benchmarked library is wrapped in an adapter implementing
:class:`EncoderAdapter`. Adapters *produce* symbols, and the single timed
operation is :meth:`encode_to_png` - payload in, PNG on disk out. Everything the library does between those two
points (mode analysis, encodation, error correction, rendering, file write)
is inside the timed region, because that is the operation an
application actually pays for.

Options use one canonical vocabulary (:class:`EncodeOptions`) that each
adapter translates into its library's own API. When a case requests a knob
the library simply does not have (e.g. an explicit error-correction level, or
ECI charset signalling), the adapter must declare that via
:meth:`EncoderAdapter.capabilities` so the runner can skip the case and record
it as ``unsupported`` - silently encoding with a *different* setting would
poison the like-for-like comparison.

This module is intentionally stdlib-only: it is imported by the host-side
corpus generator and by the in-container runner, neither of which should drag
in any encoder library.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# The canonical ec_level vocabularies, per the EncodeOptions.ec_level docstring:
# QR uses L/M/Q/H; Aztec an EC *percentage* 5..95; PDF417 a security level 0..8.
# Adapters that expose the knob advertise the matching set in their
# Capabilities.ec_levels; defined once here so the sets can't drift per adapter.
QR_EC_LEVELS = frozenset("LMQH")
AZTEC_EC_LEVELS = frozenset(str(i) for i in range(5, 96))
PDF417_EC_LEVELS = frozenset(str(i) for i in range(9))


@dataclass(frozen=True)
class EncodeOptions:
    """Canonical encode options; adapters translate these per library.

    ``ec_level`` uses one string vocabulary across symbologies so cases can be
    declared uniformly: QR ``"L"|"M"|"Q"|"H"``, Aztec a percentage such as
    ``"23"``, PDF417 a security level ``"0"``..``"8"``. Data Matrix (ECC 200)
    has no error-correction knob, so it is always ``None`` there.

    ``encoding`` is the payload's charset as the *case* intends it: ``None``
    means no charset is declared - the adapter passes a plain ``str`` and the
    library represents it however it likes (its idiomatic auto path);
    otherwise ``"iso-8859-1"``, ``"utf-8"`` or ``"shift_jis"`` pin the charset
    a conformant encoder would signal via ECI (or, for QR Shift-JIS, kanji
    mode). Most ``None`` cases carry pure-ASCII payloads, where auto has
    nothing to decide; the ``*-auto`` cases pair non-ASCII payloads with
    ``None`` specifically to grade each library's own charset selection
    (gated by :attr:`Capabilities.supports_text_auto`).

    ``module_px`` is the requested pixels-per-module for the render. Adapters
    honour it where the library allows; where a library renders at a fixed
    scale it simply does, because the host-side measurement recovers the module
    grid from the pixels regardless of scale.

    ``dm_force_square`` pins Data Matrix to square symbols. Some writers
    (libzint) otherwise pick rectangular/DMRE sizes freely, which changes the
    symbol-size comparison from "how good is the encodation" to "who has the
    finer symbol-size grid" - the corpus sets this on all DM cases so the
    size numbers compare encodation quality like-for-like.

    ``output_format`` selects the rendered artefact: ``"png"`` (the default,
    raster) or ``"svg"`` (vector). SVG cases run only against adapters whose
    :attr:`Capabilities.supports_svg` is set for the symbology; the host-side
    measurement rasterises the SVG *outside* the timed region and applies the
    same decode-verify gate and the same pixel measurement as the PNG.
    """

    symbology: str
    output_format: str = "png"
    ec_level: str | None = None
    encoding: str | None = None
    pdf417_columns: int | None = None
    dm_force_square: bool = False
    module_px: int = 4


@dataclass(frozen=True)
class Capabilities:
    """What an adapter can honour for one symbology.

    ``ec_levels`` is the set of canonical ``ec_level`` tokens the library can
    be *explicitly told* to use; ``None`` means the library exposes no such
    knob at all (the runner then only schedules cases with ``ec_level=None``).
    ``notes`` surfaces methodology caveats verbatim into reports - e.g. that
    OpenCV renders through its own file I/O rather than a dedicated barcode
    writer, or that a vector-only library (qrcodegen, ppf.datamatrix) runs the
    SVG axis only because it documents no raster path.
    """

    ec_levels: frozenset[str] | None
    supports_eci: bool
    supports_pdf417_columns: bool = False
    # True when the *library* accepts a non-ASCII payload as a plain str and
    # chooses its own representation (charset, ECI-or-not, mode). False when
    # the API is bytes-only or ASCII-only - there the adapter would have to
    # pick a charset itself, and grading adapter-authored encodation as the
    # library's "auto" would fake a capability the library doesn't have.
    # Raising on content the library's default charset can't express still
    # counts as True: that is the library's own auto answer, recorded as an
    # encode_error finding (aztec-code-generator's Latin-1 default is the
    # concrete case).
    supports_text_auto: bool = True
    # True when the library has a native SVG writer for this symbology. When
    # set, the adapter must implement encode_to_svg; when unset, svg cases are
    # recorded unsupported. Kept off by default so most adapters need no svg
    # method at all.
    supports_svg: bool = False
    # True when the library documents a way to produce a raster/PNG image. On
    # by default (most encoders write PNG or hand back an image the library's
    # own I/O saves). Set False for libraries whose *only* documented image
    # output is vector/SVG (ppf.datamatrix, qrcodegen): the suite renders per
    # the library's own docs, so it will not fabricate a PNG the library gives
    # no documented path to - those encoders run the SVG axis only and their
    # png cases are recorded unsupported.
    supports_png: bool = True
    notes: str = ""


class EncoderAdapter(Protocol):
    """The contract every encoder adapter module fulfils via ``ADAPTER``."""

    id: str
    supports: frozenset[str]

    def lib_versions(self) -> dict[str, str]:
        """Installed versions of the wrapped library (and relevant deps)."""
        ...

    def capabilities(self, symbology: str) -> Capabilities:
        """Declare what this adapter can honour for ``symbology``."""
        ...

    def encode_to_png(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        """The timed operation: encode ``payload`` and write a PNG to ``out_path``."""
        ...

    def encode_to_svg(self, payload: str, options: EncodeOptions, out_path: Path) -> None:
        """The timed operation for the SVG axis: encode ``payload`` and write
        an SVG to ``out_path``. Only adapters declaring
        :attr:`Capabilities.supports_svg` are ever asked; the others may omit
        this method entirely (the runner never dispatches svg to them)."""
        ...
