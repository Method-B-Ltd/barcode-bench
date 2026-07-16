"""Runner policy: unsupported knobs are skipped, never silently degraded."""

from __future__ import annotations

import io
import json
from pathlib import Path

from barcode_bench.adapters.base import Capabilities
from barcode_bench.corpus import build_cases
from barcode_bench.runner import run_benchmark


class _StubAdapter:
    """QR-only, ECL M-only, no ECI, no undeclared-charset text - a
    narrow test encoder."""

    id = "stub"
    supports = frozenset({"qr"})

    def lib_versions(self) -> dict[str, str]:
        return {}

    def capabilities(self, symbology: str) -> Capabilities:
        return Capabilities(
            ec_levels=frozenset("M"), supports_eci=False, supports_text_auto=False
        )

    def encode_to_png(self, payload, options, out_path) -> None:  # noqa: ANN001
        Path(out_path).write_bytes(b"not a real png")

    def encode_to_svg(self, payload, options, out_path) -> None:  # noqa: ANN001
        # supports_svg defaults False, so the runner must gate svg cases out
        # before dispatch; this must never fire.
        raise AssertionError("svg case should have been gated out, not dispatched")


def test_unsupported_knobs_are_recorded_not_degraded(tmp_path: Path) -> None:
    wanted = {
        "qr-alnum-eclM", "qr-alnum-eclQ", "qr-latin1-eclM",
        "qr-latin1-eclM-auto", "qr-alnum-eclM-svg", "dm-alnum",
    }
    cases = [c for c in build_cases(1, 2) if c.case_id in wanted]
    buf = io.StringIO()
    # _StubAdapter.encode_to_svg raises: the svg case must be gated out before
    # any encode is attempted (supports_svg defaults False), so it never fires.
    run_benchmark(_StubAdapter(), cases, tmp_path, reps=1, fh=buf)

    status: dict[str, str] = {}
    for line in buf.getvalue().splitlines():
        rec = json.loads(line)
        if rec["record_type"] == "timing":
            status.setdefault(rec["case_id"], rec["status"])

    assert status["qr-alnum-eclM"] == "ok"
    assert status["qr-alnum-eclQ"] == "unsupported"  # ECL knob limited to M
    assert status["qr-latin1-eclM"] == "unsupported"  # no ECI
    # non-ASCII payload, no declared charset, supports_text_auto=False
    assert status["qr-latin1-eclM-auto"] == "unsupported"
    assert status["qr-alnum-eclM-svg"] == "unsupported"  # no native SVG writer
    assert "dm-alnum" not in status  # symbology not supported -> not attempted


def test_rep_offset_stamps_round_number(tmp_path: Path) -> None:
    # The orchestrator interleaves reps across encoders (round-robin); each
    # container invocation runs one rep and must stamp it with the round.
    cases = [c for c in build_cases(1, 2) if c.case_id == "qr-alnum-eclM"]
    buf = io.StringIO()
    run_benchmark(_StubAdapter(), cases, tmp_path, reps=1, fh=buf, rep_offset=2)

    reps = {
        rec["rep"]
        for rec in map(json.loads, buf.getvalue().splitlines())
        if rec["record_type"] == "timing" and rec["status"] == "ok"
    }
    assert reps == {2}
