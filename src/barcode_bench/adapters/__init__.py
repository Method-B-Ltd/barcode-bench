"""Adapter registry.

The registry is lazy by necessity: each benchmark container has exactly one
encoder library installed,
so importing every adapter module up front would raise ``ImportError`` for the
five libraries that aren't there. The runner asks for one adapter by id and
only that module is imported.
"""

from __future__ import annotations

import importlib

from barcode_bench.adapters.base import EncoderAdapter

# Also the default run order. treepoem is last: its
# ghostscript-per-encode round-trips dominate a full run's wall clock, so
# every other encoder's results are available early.
ADAPTER_MODULES: dict[str, str] = {
    "pystrich": "barcode_bench.adapters.pystrich_adapter",
    "pystrich-git": "barcode_bench.adapters.pystrich_git_adapter",
    "zxingcpp": "barcode_bench.adapters.zxingcpp_adapter",
    "segno": "barcode_bench.adapters.segno_adapter",
    "qrcode": "barcode_bench.adapters.qrcode_adapter",
    "qrcodegen": "barcode_bench.adapters.qrcodegen_adapter",
    "aztecgen": "barcode_bench.adapters.aztecgen_adapter",
    "pdf417gen": "barcode_bench.adapters.pdf417gen_adapter",
    "pylibdmtx": "barcode_bench.adapters.pylibdmtx_adapter",
    "ppfdm": "barcode_bench.adapters.ppfdm_adapter",
    # qrencode (libqrencode binding) was evaluated and dropped: its C
    # extension raises `SystemError: PY_SSIZE_T_CLEAN macro must be defined for
    # '#' formats` on import-and-use - the pre-3.10 argument-parsing style
    # Python removed in 3.10. The package is sdist-only and unmaintained, so
    # the canonical native QR encoder has no working PyPI binding on modern
    # Python; zint (via zxing-cpp) is the suite's native QR representative.
    "opencv": "barcode_bench.adapters.opencv_adapter",
    "treepoem": "barcode_bench.adapters.treepoem_adapter",
}

ALL_ENCODER_IDS: list[str] = list(ADAPTER_MODULES)


def load_adapter(encoder_id: str) -> EncoderAdapter:
    """Import and return the adapter for ``encoder_id`` (raises KeyError/ImportError)."""
    module = importlib.import_module(ADAPTER_MODULES[encoder_id])
    adapter: EncoderAdapter = module.ADAPTER
    return adapter
