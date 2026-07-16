"""Shared symbology → zxing-cpp ``BarcodeFormat`` map.

Used by the zxing-cpp encoder adapter (in-container, encode side) and the
host-side measurement decode. Kept in one place so encode and decode can never
drift to different formats for a symbology - a mismatch there would silently
fail the decode-verify gate. Importing this pulls in ``zxingcpp``, so only the
zxing-cpp container and the host measurement (both of which have it installed)
ever import it; no other encoder container touches it.
"""

from __future__ import annotations

import zxingcpp

FORMATS = {
    "qr": zxingcpp.BarcodeFormat.QRCode,
    "datamatrix": zxingcpp.BarcodeFormat.DataMatrix,
    "datamatrix_rect": zxingcpp.BarcodeFormat.DataMatrix,
    "aztec": zxingcpp.BarcodeFormat.Aztec,
    "pdf417": zxingcpp.BarcodeFormat.PDF417,
}
