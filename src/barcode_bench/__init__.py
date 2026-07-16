"""Encoder benchmark suite for 2D barcode libraries.

Benchmarks the end-to-end operation *payload -> encode -> PNG on disk* across
QR, Data Matrix, Aztec and PDF417 for several encoder libraries (all installed
from PyPI), measures the resulting symbol efficiency by decoding each PNG with
zxing-cpp and deriving codeword counts, and weighs each encoder stack's
installation as a podman image-size delta over a common base image.

See README.md for the methodology and fairness guarantees.
"""

from __future__ import annotations
