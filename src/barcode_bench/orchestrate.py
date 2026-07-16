"""Host-side podman orchestration: build, weigh and run the encoder images.

Plain ``podman`` subprocess invocations, no client library. Everything an
encoder container needs at run time is bind-mounted -
the shared corpus read-only, the bench source read-only (images contain *no*
bench code, see containers/Containerfile), and a per-encoder output directory
read-write. ``--userns=keep-id`` keeps output files owned by the invoking
user under rootless podman.

Install weight is measured as image size minus base-image size. That delta is
meaningful only because every encoder image is built ``FROM`` the base stage
of the same Containerfile, so the base layers are shared verbatim.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from barcode_bench.adapters import ALL_ENCODER_IDS
from barcode_bench.corpus import BENCH_ROOT, require_run_dir

CONTAINERS_DIR = BENCH_ROOT / "containers"
CONTAINERFILE = CONTAINERS_DIR / "Containerfile"
SRC_DIR = BENCH_ROOT / "src"

BASE_TAG = "barcode-bench-base"

# apt packages an encoder's library needs at run time: treepoem shells out
# to ghostscript (BWIPP is PostScript); pylibdmtx dlopens libdmtx.
ENCODER_APT_PACKAGES: dict[str, str] = {
    "treepoem": "ghostscript",
    "pylibdmtx": "libdmtx0b",
}

# encoders with a dedicated Containerfile stage instead of the generic
# requirements-driven `encoder` stage (multistage builds from source).
ENCODER_BUILD_TARGETS: dict[str, str] = {"pystrich-git": "pystrich-git"}

IMPORT_SAMPLES = 5


def image_tag(encoder_id: str) -> str:
    return f"barcode-bench-{encoder_id}"


# Known-benign podman stderr lines, suppressed from the streamed build/run log.
# Rootless podman's OCI runtime logs one of these per RUN step because it can't
# raise ambient capabilities it was never granted - expected, not actionable.
# Anything not matched here is passed through untouched, so a new
# warning still surfaces. Add patterns here as new benign noise is identified.
_BENIGN_PODMAN_STDERR: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"level=warning msg=\"can't raise ambient capability "
        r"CAP_[A-Z_]+: operation not permitted\""
    ),
)


def _is_benign_podman_line(line: str) -> bool:
    return any(p.search(line) for p in _BENIGN_PODMAN_STDERR)


def _podman(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    if capture:
        return subprocess.run(["podman", *args], check=True, text=True, capture_output=True)
    # Stream stderr live through the benign-noise filter so expected
    # rootless-capability warnings don't drown the build/run log; stdout is
    # inherited unchanged. A one-line tally at the end reports the suppressed
    # line count without reprinting them.
    proc = subprocess.Popen(["podman", *args], text=True, stderr=subprocess.PIPE)
    assert proc.stderr is not None
    suppressed = 0
    for line in proc.stderr:
        if _is_benign_podman_line(line):
            suppressed += 1
        else:
            sys.stderr.write(line)
    ret = proc.wait()
    if suppressed:
        print(f"  ({suppressed} expected rootless-capability warnings suppressed)",
              file=sys.stderr)
    if ret != 0:
        raise subprocess.CalledProcessError(ret, ["podman", *args])
    return subprocess.CompletedProcess(["podman", *args], ret)


def _resolve_encoders(encoders: list[str] | None) -> list[str]:
    if encoders is None:
        return list(ALL_ENCODER_IDS)
    unknown = set(encoders) - set(ALL_ENCODER_IDS)
    if unknown:
        raise SystemExit(f"unknown encoder ids: {', '.join(sorted(unknown))}")
    return encoders


def cmd_build(encoders: list[str] | None, no_cache: bool = False) -> int:
    encoders = _resolve_encoders(encoders)
    cache = ["--no-cache"] if no_cache else []
    print(f"building {BASE_TAG}", file=sys.stderr)
    _podman(
        "build", *cache, "--target", "base", "-t", BASE_TAG,
        "-f", str(CONTAINERFILE), str(BENCH_ROOT),
    )
    for enc in encoders:
        print(f"building {image_tag(enc)}", file=sys.stderr)
        target = ENCODER_BUILD_TARGETS.get(enc, "encoder")
        extra_args: list[str] = []
        if enc in ENCODER_BUILD_TARGETS:
            # Hourly cache-bust for from-source stages: the git clone layer is
            # reused within a UTC hour (repeated builds stay fast) and
            # refreshed across hours (HEAD can't go stale for long while
            # upstream is being actively developed). The recorded sha is
            # always the truth about what was cloned either way; use
            # --no-cache to force a same-hour refresh.
            extra_args = [
                "--build-arg",
                f"CACHE_BUST={datetime.now(UTC).strftime('%Y-%m-%dT%H')}",
            ]
        _podman(
            "build", *cache, "--target", target,
            "--build-arg", f"ENCODER={enc}",
            "--build-arg", f"APT_PACKAGES={ENCODER_APT_PACKAGES.get(enc, '')}",
            *extra_args,
            "-t", image_tag(enc),
            "-f", str(CONTAINERFILE), str(BENCH_ROOT),
        )
    return 0


def _inspect_image(tag: str) -> dict[str, int | str]:
    out = _podman(
        "image", "inspect", "--format", "{{.Size}} {{.Id}}", tag, capture=True
    ).stdout.split()
    return {"bytes": int(out[0]), "id": out[1]}


def _encoder_layer_bytes(tag: str) -> int:
    """Sum of the encoder-stage layers (everything above the base stage).

    Computed from `podman history` rather than image-size minus base-size:
    the subtraction silently drifts whenever the base tag has been rebuilt
    since the encoder image was (the old base layers stay embedded in the
    encoder image, but the tag now points at different bytes).
    """
    # --format json for raw byte sizes; Go-template {{.Size}} comes back
    # humanised ("7.86MB") whatever --human says.
    entries = json.loads(_podman("history", "--format", "json", tag, capture=True).stdout)
    # Encoder-stage instructions carry the stage's build args in their
    # history entry; base/python layers don't mention ENCODER=.
    return sum(e["size"] for e in entries if "ENCODER=" in e.get("CreatedBy", ""))


def cmd_weigh(run_dir: str, encoders: list[str] | None) -> int:
    run_path = require_run_dir(run_dir)
    encoders = _resolve_encoders(encoders)
    base = _inspect_image(BASE_TAG)
    doc: dict[str, object] = {"base": {"tag": BASE_TAG, **base}, "encoders": {}}
    for enc in encoders:
        info = _inspect_image(image_tag(enc))
        delta = _encoder_layer_bytes(image_tag(enc))
        doc["encoders"][enc] = {"tag": image_tag(enc), **info, "delta_bytes": delta}  # type: ignore[index]
        print(f"{enc:12} image {int(info['bytes'])/1e6:8.1f} MB   stack {delta/1e6:8.1f} MB")
    out_path = run_path / "images.json"
    out_path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


def _run_container(encoder_id: str, run_dir: Path, runner_args: list[str]) -> None:
    corpus = run_dir / "corpus.json"
    out_dir = run_dir / "raw" / encoder_id
    out_dir.mkdir(parents=True, exist_ok=True)
    _podman(
        "run", "--rm",
        "--userns=keep-id",
        "-v", f"{corpus}:/bench/corpus.json:ro,Z",
        "-v", f"{SRC_DIR}:/bench/src:ro,Z",
        "-v", f"{out_dir}:/bench/out:Z",
        "-e", "PYTHONPATH=/bench/src",
        image_tag(encoder_id),
        "python", "-m", "barcode_bench.runner",
        "--encoder", encoder_id,
        "--corpus", "/bench/corpus.json",
        "--out", "/bench/out",
        *runner_args,
    )


def cmd_run(run_dir: str, encoders: list[str] | None, reps: int, quick: bool) -> int:
    encoders = _resolve_encoders(encoders)
    run_path = require_run_dir(run_dir)

    # Fresh output dirs up front: timings.jsonl is append-mode inside the
    # container (rounds accumulate into it), so a re-run would
    # otherwise mix old and new samples.
    for enc in encoders:
        out_dir = run_path / "raw" / enc
        if out_dir.exists():
            shutil.rmtree(out_dir)

    # Repetitions are interleaved across encoders (round-robin): every
    # encoder encodes the whole corpus once, then every encoder again, R
    # times. Sequential whole-leg-per-encoder scheduling would hand each
    # encoder a different wall-clock window, letting slow machine drift
    # (thermal, background load) bias encoders by list position; rounds
    # spread that drift across everyone. Each round is a fresh container,
    # so per-round warmup stays untimed and library state can't carry over.
    rounds = 1 if quick else reps
    for rnd in range(rounds):
        for enc in encoders:
            print(f"running benchmark round {rnd + 1}/{rounds}: {enc}", file=sys.stderr)
            args = ["--reps", "1", "--rep-offset", str(rnd)] + (["--quick"] if quick else [])
            _run_container(enc, run_path, args)

    # Cold-import samples need a virgin interpreter each, hence separate
    # short-lived containers rather than a loop inside one process.
    for enc in encoders:
        for _ in range(IMPORT_SAMPLES):
            _run_container(enc, run_path, ["--measure-import"])
    return 0
