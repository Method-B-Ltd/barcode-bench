"""Pixel-based symbol footprint measurement (prototype).

Runs alongside the codeword-table derivation in ``measure.py`` for now, so the
two can be diffed before the tables are removed. It derives a symbol's module
area straight from the rendered pixels, with no per-symbology structure tables
and no adapter-declared geometry:

  1. threshold the image to dark-module / background,
  2. trim the solid quiet-zone border to the dark bounding box,
  3. get a rough module pitch (px per module) from the run lengths - the finest
     run is one module for every symbology here, and PDF417's taller rows fall
     out of measuring the height in that same X-dimension unit,
  4. pick the integer module count per axis whose uniform grid best aligns to
     the actual module boundaries (grid fit), and area = width * height.

Step 4 replaces a naive ``round(extent / pitch)`` because that drifts by a
whole module over a large symbol when the true pitch is fractional - an
mm-based SVG (e.g. python-qrcode) rasterised at a non-integer px/module. The
grid fit is scale-robust: a wrong count beats against the boundaries and scores
worse, so the count is right even off a ragged raster. An exact GCD of the run
lengths still rides along as a crispness flag (whether the raster had an
integer pitch), but it no longer gates correctness.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from math import gcd

import numpy as np
from PIL import Image

_DARK_THRESHOLD = 128  # 0..255 luma; below this is a dark module
_MAX_SCANLINES = 256  # cap on lines sampled per axis when finding the pitch
# Symbologies whose symbol is always square, so w != h flags a trim problem
# (Aztec is the one whose border is not guaranteed dark at the extremes).
_SQUARE = ("qr", "datamatrix", "aztec")


@dataclass(frozen=True)
class PixelArea:
    area_modules: int
    pitch_px: float
    modules_w: int
    modules_h: int
    crisp: bool  # exact GCD pitch agreed, i.e. the raster edges are sharp
    note: str


def measure_pixel_area(img: Image.Image, symbology: str) -> PixelArea | None:
    """Module footprint of the symbol in ``img``, or None if it can't be read."""
    mask = np.asarray(img.convert("L")) < _DARK_THRESHOLD
    trimmed = _trim(mask)
    if trimmed is None:
        return None
    h_px, w_px = trimmed.shape
    pitch, gcd_pitch = _pitch(trimmed)
    if pitch is None or pitch < 1:
        return None
    modules_w = _module_count(trimmed, pitch)
    modules_h = _module_count(trimmed.T, pitch)
    if not modules_w or not modules_h:
        return None

    note = ""
    if symbology in _SQUARE and modules_w != modules_h:
        note = f"expected square, measured {modules_w}x{modules_h}"
    return PixelArea(
        area_modules=modules_w * modules_h,
        pitch_px=round(w_px / modules_w, 3),
        modules_w=modules_w,
        modules_h=modules_h,
        crisp=gcd_pitch is not None,
        note=note,
    )


def _module_count(mask: np.ndarray, rough_pitch: float) -> int | None:
    """Modules along axis 1 (columns) by grid fit.

    ``round(extent / pitch)`` drifts by a whole module over a large symbol when
    the true pitch is fractional (an mm-based SVG rasterised at a non-integer
    px/module). Instead, aggregate the column-boundary strength - how many rows
    change colour at each x - and, over a few integer candidates around
    ``extent / pitch``, choose the count whose uniform grid lines land nearest
    those boundaries. A wrong count beats against the boundaries and scores
    worse, so this is right even off a ragged raster. Only boundaries that carry
    a colour change contribute; module edges between two same-colour modules
    have no transition and are simply not scored.
    """
    e = mask.shape[1]
    if rough_pitch < 1 or e < rough_pitch:
        return None
    n0 = max(1, round(e / rough_pitch))
    strength = (mask[:, 1:] ^ mask[:, :-1]).sum(axis=0).astype(float)
    total = strength.sum()
    if total == 0:
        return n0
    pos = np.arange(1, e)
    best_n, best_score = n0, None
    for n in range(max(1, n0 - 3), n0 + 4):
        phase = (pos / (e / n)) % 1.0
        misalign = np.minimum(phase, 1.0 - phase)
        score = float((misalign * strength).sum() / total)
        if best_score is None or score < best_score:
            best_n, best_score = n, score
    return best_n


def _trim(mask: np.ndarray) -> np.ndarray | None:
    """Crop to the bounding box of dark pixels (drops the quiet zone)."""
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any() or not cols.any():
        return None
    r0, r1 = np.where(rows)[0][[0, -1]]
    c0, c1 = np.where(cols)[0][[0, -1]]
    return mask[r0 : r1 + 1, c0 : c1 + 1]


def _pitch(mask: np.ndarray) -> tuple[float | None, int | None]:
    """Return (robust pitch, exact-GCD pitch) in pixels from the run lengths.

    Interior runs only - the first and last run of each scanline are dropped,
    because a bbox crop can shave a pixel off the outermost module and that
    would poison the GCD. The robust pitch folds every run onto the smallest
    run and takes the median, so it survives a little sub-module jitter; the
    exact GCD rides along as the crispness signal (None when it is 1).
    """
    lengths: list[int] = []
    for line in _scanlines(mask):
        r = _runs(line)
        if len(r) >= 3:
            lengths.extend(r[1:-1])
    if not lengths:
        return None, None
    arr = np.asarray(lengths, dtype=float)
    p0 = float(arr.min())
    if p0 < 1:
        return None, None
    k = np.maximum(np.round(arr / p0), 1)
    robust = float(np.median(arr / k))
    g = reduce(gcd, (int(x) for x in lengths))
    return robust, (g if g >= 2 else None)


def _scanlines(mask: np.ndarray):
    """Evenly sampled rows then columns of the mask (capped for speed)."""
    h, w = mask.shape
    for i in np.unique(np.linspace(0, h - 1, min(h, _MAX_SCANLINES)).astype(int)):
        yield mask[i]
    for j in np.unique(np.linspace(0, w - 1, min(w, _MAX_SCANLINES)).astype(int)):
        yield mask[:, j]


def _runs(line: np.ndarray) -> list[int]:
    """Run lengths of a boolean scanline."""
    change = np.flatnonzero(np.diff(line.astype(np.int8))) + 1
    bounds = [0, *change.tolist(), int(line.size)]
    return [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)]
