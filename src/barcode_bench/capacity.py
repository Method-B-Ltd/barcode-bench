"""Static symbol capacity tables for sizing the near-capacity payloads.

The ``near_capacity`` corpus category needs a target payload length per
(symbology, error-correction level). We size payloads at ~85 % of the
symbology's *byte-mode* data capacity, from the tables below, rather than
binary-searching each encoder's actual limit: static sizing is deterministic
(same corpus for every encoder - the fairness requirement) and byte mode is
the safest common denominator every library can fall back to. Encoders with
smarter mode selection will compress the same payload into a smaller symbol;
that difference is what the codeword measurement reports. An
encoder that *rejects* a near-capacity payload produces an ``encode_error``
record - a finding about that encoder, not a suite failure.
"""

from __future__ import annotations

# QR version 40 data capacity in 8-bit-byte mode, per error-correction level.
QR_BYTE_CAPACITY: dict[str, int] = {"L": 2953, "M": 2331, "Q": 1663, "H": 1273}

# Data Matrix ECC 200, largest symbol (144x144): 1556 data bytes in base-256
# mode. There is no error-correction knob in ECC 200.
DATAMATRIX_BYTE_CAPACITY = 1556

# Aztec full-range symbol, 32 layers, at the spec-recommended 23 % error
# correction: 1914 data bytes.
AZTEC_BYTE_CAPACITY: dict[str, int] = {"23": 1914}

# PDF417: 929 codewords per symbol; one is the symbol length descriptor and
# 2^(ecl+1) are error correction. Byte compaction packs 6 bytes into 5
# codewords.
PDF417_TOTAL_CODEWORDS = 929


def pdf417_byte_capacity(ec_level: str) -> int:
    """Max payload bytes in byte compaction at PDF417 security level 0..8."""
    ecc_codewords = 2 ** (int(ec_level) + 1)
    data_codewords = PDF417_TOTAL_CODEWORDS - 1 - ecc_codewords
    return data_codewords * 6 // 5


NEAR_CAPACITY_FRACTION = 0.85


def near_capacity_target(symbology: str, ec_level: str | None) -> int:
    """Target payload length (ASCII chars) for a near-capacity case."""
    if symbology == "qr":
        assert ec_level is not None
        cap = QR_BYTE_CAPACITY[ec_level]
    elif symbology == "datamatrix":
        cap = DATAMATRIX_BYTE_CAPACITY
    elif symbology == "aztec":
        assert ec_level is not None
        cap = AZTEC_BYTE_CAPACITY[ec_level]
    elif symbology == "pdf417":
        assert ec_level is not None
        cap = pdf417_byte_capacity(ec_level)
    else:
        raise ValueError(f"unknown symbology {symbology!r}")
    return int(cap * NEAR_CAPACITY_FRACTION)
