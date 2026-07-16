"""Pixel-based module-area measurement (pixel_measure.measure_pixel_area).

Two layers: synthetic grids that pin the logic (pitch-independence, non-square
rows, the ragged-raster crispness flag, the square-symbology guard), and real
symbols encoded with zxing-cpp whose measured module grid is checked against
the decoder's own ground truth - so the tests stay honest about finder
patterns, timing tracks and the Aztec border, not just clean checkerboards.
"""

from __future__ import annotations

import numpy as np
import pytest
import zxingcpp
from PIL import Image

from barcode_bench.pixel_measure import measure_pixel_area

# --- synthetic renderers ----------------------------------------------------


def _bordered_checker(w: int, h: int) -> list[list[bool]]:
    """Checkerboard with a dark border, so the dark bbox spans the full grid
    (the property real finder patterns provide)."""
    g = [[(r + c) % 2 == 0 for c in range(w)] for r in range(h)]
    for c in range(w):
        g[0][c] = g[h - 1][c] = True
    for r in range(h):
        g[r][0] = g[r][w - 1] = True
    return g


def _render(grid: list[list[bool]], pitch: int, quiet: int, row_mult: int = 1) -> Image.Image:
    """Render a module grid to greyscale: ``pitch`` px per module, a ``quiet``
    module white border, each row ``row_mult`` modules tall (row_mult > 1 gives
    the PDF417 non-square shape)."""
    h, w = len(grid), len(grid[0])
    arr = np.full(((h * row_mult + 2 * quiet) * pitch, (w + 2 * quiet) * pitch), 255, np.uint8)
    for r in range(h):
        for c in range(w):
            if grid[r][c]:
                y = (r * row_mult + quiet) * pitch
                x = (c + quiet) * pitch
                arr[y : y + row_mult * pitch, x : x + pitch] = 0
    return Image.fromarray(arr, "L")


def _render_fractional(grid: list[list[bool]], pitch: float, quiet: int = 4) -> Image.Image:
    """Render with module boundaries at *fractional* pixel positions, so modules
    come out e.g. 15 or 16 px wide for pitch 15.1 - the mm-based-SVG-at-a-
    non-integer-scale case that breaks ``round(extent / pitch)``."""
    h, w = len(grid), len(grid[0])
    def edge(i: int) -> int:
        return round((i + quiet) * pitch)

    arr = np.full((edge(h) + quiet * round(pitch), edge(w) + quiet * round(pitch)), 255, np.uint8)
    for r in range(h):
        for c in range(w):
            if grid[r][c]:
                arr[edge(r) : edge(r + 1), edge(c) : edge(c + 1)] = 0
    return Image.fromarray(arr, "L")


# --- synthetic logic tests --------------------------------------------------


@pytest.mark.parametrize(("pitch", "quiet"), [(6, 4), (5, 2), (12, 1), (3, 3)])
def test_module_count_independent_of_pitch_and_quiet_zone(pitch: int, quiet: int) -> None:
    pa = measure_pixel_area(_render(_bordered_checker(8, 8), pitch, quiet), "qr")
    assert pa is not None
    assert (pa.modules_w, pa.modules_h, pa.area_modules) == (8, 8, 64)
    assert pa.crisp
    assert pa.pitch_px == pitch


def test_non_square_pdf417_rows_counted_in_x_units() -> None:
    # 10 columns, 4 codeword rows, each row 3 X-dimensions tall -> 10 x 12.
    pa = measure_pixel_area(_render(_bordered_checker(10, 4), pitch=4, quiet=2, row_mult=3), "pdf417")
    assert pa is not None
    assert (pa.modules_w, pa.modules_h, pa.area_modules) == (10, 12, 120)
    assert pa.crisp


def test_ragged_raster_recovers_count_but_flags_not_crisp() -> None:
    img = _render(_bordered_checker(8, 8), pitch=10, quiet=4)
    # a non-integer bilinear resize is the anti-aliased-SVG failure mode
    img = img.resize((int(img.width * 1.37), int(img.height * 1.37)), Image.Resampling.BILINEAR)
    pa = measure_pixel_area(img, "qr")
    assert pa is not None
    assert (pa.modules_w, pa.modules_h) == (8, 8)  # grid fit still recovers it
    assert not pa.crisp  # fractional pitch -> the GCD crispness signal is off


@pytest.mark.parametrize("pitch", [15.1, 18.03, 22.667, 27.2])
def test_grid_fit_survives_fractional_pitch(pitch: float) -> None:
    """The python-qrcode-SVG bug: a large symbol whose module boundaries land at
    fractional pixel positions. ``round(extent / pitch)`` drifts by a whole
    module (and by more for a checker, whose min-run biases the pitch low); the
    grid fit stays exact and scale-independent."""
    pa = measure_pixel_area(_render_fractional(_bordered_checker(120, 120), pitch), "qr")
    assert pa is not None
    assert (pa.modules_w, pa.modules_h) == (120, 120)
    assert not pa.crisp  # boundaries at fractional pixels -> no integer pitch


def test_blank_image_returns_none() -> None:
    assert measure_pixel_area(Image.new("L", (40, 40), 255), "qr") is None


def test_square_symbology_flags_non_square_measurement() -> None:
    img = _render(_bordered_checker(6, 4), pitch=6, quiet=2)
    assert (m := measure_pixel_area(img, "qr")) is not None and "expected square" in m.note
    # the same footprint is fine for a symbology that is legitimately rectangular
    assert (m := measure_pixel_area(img, "datamatrix_rect")) is not None and m.note == ""


# --- real symbols: measured grid vs decoder ground truth --------------------


def _encode(fmt: zxingcpp.BarcodeFormat, text: str, **kw: object) -> Image.Image:
    """zxing-cpp symbol as a crisp greyscale PIL image (scale=8, nearest)."""
    return Image.fromarray(zxingcpp.create_barcode(text, fmt, **kw).to_image(scale=8))


def test_real_qr_side_matches_decoder_version() -> None:
    img = _encode(zxingcpp.BarcodeFormat.QRCode, "HELLO WORLD 12345")
    version = int(zxingcpp.read_barcode(img, formats=zxingcpp.BarcodeFormat.QRCode).extra["Version"])
    pa = measure_pixel_area(img, "qr")
    assert pa is not None and pa.crisp
    assert pa.modules_w == pa.modules_h == 17 + 4 * version  # QR side = 17 + 4V


def test_real_datamatrix_matches_decoder_dimensions() -> None:
    img = _encode(zxingcpp.BarcodeFormat.DataMatrix, "HELLO WORLD 12345", forceSquare=True)
    rows, cols = (
        int(x)
        for x in zxingcpp.read_barcode(img, formats=zxingcpp.BarcodeFormat.DataMatrix)
        .extra["Version"]
        .split("x")
    )
    pa = measure_pixel_area(img, "datamatrix")
    assert pa is not None and pa.crisp
    assert (pa.modules_h, pa.modules_w) == (rows, cols)


def test_real_aztec_side_is_a_valid_core_and_square() -> None:
    img = _encode(zxingcpp.BarcodeFormat.Aztec, "AZ")
    layers = int(zxingcpp.read_barcode(img, formats=zxingcpp.BarcodeFormat.Aztec).extra["Version"])
    pa = measure_pixel_area(img, "aztec")
    assert pa is not None and pa.crisp
    # small payload -> no reference-grid lines; side is the compact or full core
    assert pa.modules_w == pa.modules_h
    assert pa.modules_w in (4 * layers + 11, 4 * layers + 15)


def test_real_pdf417_width_fits_the_row_grammar() -> None:
    img = _encode(zxingcpp.BarcodeFormat.PDF417, "HELLO WORLD 12345 SOME MORE TEXT FOR WIDTH")
    pa = measure_pixel_area(img, "pdf417")
    assert pa is not None and pa.crisp
    # width = 17*data_columns + 69 (start + stop + two row indicators)
    assert (pa.modules_w - 69) % 17 == 0 and pa.modules_w > 69
    assert pa.modules_h % 3 == 0  # rows render 3 X-dimensions tall
