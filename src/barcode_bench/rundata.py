"""Shared loader for a completed run directory.

Both report stages read the same raw records - ``measurements.jsonl`` (one
per produced symbol) and each encoder's ``raw/<enc>/timings.jsonl`` (env,
import and per-repetition timing records) - and the validity rule that gates
them is identical. ``RunData`` parses those once into plain dicts/sets so the
CSV summary (``report.py``) and the published facts report (``publish.py``)
build their aggregates from one source of truth. It imports
nothing heavy (no matplotlib), so the light CSV stage can use it too.

The aggregation *policies* stay with each consumer: ``publish`` shows medians
only, ``report`` also emits mean/stddev/min and per-case validity - so this
class exposes the raw building blocks (``timings``, ``measures``, ``valid``,
``samples``, ``exceptions``) plus a few median convenience methods, and lets
each caller compute what its output needs.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

from barcode_bench.adapters import ALL_ENCODER_IDS


class RunData:
    """Everything a report needs, loaded once from a run directory."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.manifest = json.loads((run_dir / "manifest.json").read_text())
        corpus = json.loads((run_dir / "corpus.json").read_text())
        self.cases = {c["case_id"]: c for c in corpus["cases"]}
        self.seed = corpus["seed"]
        self.trials_per_case = corpus["trials_per_case"]
        # images.json only exists once `weigh` has run; the CSV stage can run
        # without it, so tolerate its absence rather than requiring a weigh.
        images_path = run_dir / "images.json"
        self.images = json.loads(images_path.read_text()) if images_path.is_file() else {}

        # validity: (encoder, case, trial) -> decode-verified
        self.valid: set[tuple[str, str, int]] = set()
        # module footprint (modules²) measured from the pixels, per symbol.
        self.symbol_area: dict[tuple[str, str, int], int | None] = {}
        # full measurement record per (encoder, case, trial), for consumers
        # that need file_bytes or px beyond the module area.
        self.measures: dict[tuple[str, str, int], dict] = {}
        with (run_dir / "measurements.jsonl").open() as fh:
            for line in fh:
                m = json.loads(line)
                key = (m["encoder"], m["case_id"], m["trial"])
                self.measures[key] = m
                if m["decode_ok"] and m["content_match"]:
                    self.valid.add(key)
                self.symbol_area[key] = (m["size"] or {}).get("area_modules")

        self.envs: dict[str, dict] = {}
        self.imports: dict[str, list[float]] = defaultdict(list)
        # (encoder, case) -> every timing record (ok/unsupported/encode_error),
        # for consumers that need per-trial status/detail; kept in file order.
        self.timings: dict[tuple[str, str], list[dict]] = defaultdict(list)
        # (encoder, case) -> list of valid sample seconds
        self.samples: dict[tuple[str, str], list[float]] = defaultdict(list)
        # (encoder, case) -> ("unsupported"|"encode_error", reason, n_trials)
        self.exceptions: dict[tuple[str, str], tuple[str, str, int]] = {}
        # Rounds actually run: the orchestrator invokes the runner once per
        # round (each with reps=1, stamped rep=round), so the env `reps` field
        # is always 1 - the true round count is the number of distinct rep
        # values seen, not that field.
        rep_values: set[int] = set()
        for enc_dir in sorted((run_dir / "raw").iterdir()):
            timings = enc_dir / "timings.jsonl"
            if not timings.is_file():
                continue
            with timings.open() as fh:
                for line in fh:
                    r = json.loads(line)
                    if r["record_type"] == "env":
                        self.envs[r["encoder"]] = r
                    elif r["record_type"] == "import":
                        self.imports[r["encoder"]].append(r["seconds"])
                    elif r["record_type"] == "timing":
                        ec_key = (r["encoder"], r["case_id"])
                        self.timings[ec_key].append(r)
                        if r["status"] == "ok":
                            if r.get("rep") is not None:
                                rep_values.add(r["rep"])
                            if (r["encoder"], r["case_id"], r["trial"]) in self.valid:
                                self.samples[ec_key].append(r["seconds"])
                        else:
                            _kind, _reason, n = self.exceptions.get(
                                ec_key, (r["status"], r["error"], 0)
                            )
                            self.exceptions[ec_key] = (r["status"], r["error"], n + 1)

        self.encoders = [e for e in ALL_ENCODER_IDS if e in self.envs]
        self.rounds = len(rep_values) or 1

    def valid_trials(self, encoder: str, case_id: str) -> set[int]:
        """Trials of (encoder, case) whose produced symbol decode-verified."""
        return {t for (e, cid, t) in self.valid if e == encoder and cid == case_id}

    def misdecoded_trials(self, encoder: str, case_id: str) -> dict[int, dict]:
        """Trials whose symbol was produced (a measurement exists) but failed
        the decode-verify gate - the decoder either couldn't read it or read it
        back as different content. Keyed by trial -> measurement record, so a
        consumer can explain *how* it failed. A trial that raised at encode time
        has no measurement (the file is unlinked), so it never appears here -
        those are the ``encode_error`` exceptions instead. This is the outcome
        the timing statuses alone miss: the encode succeeds (``status=ok``) yet
        the symbol is wrong, so it belongs to neither ``samples`` nor
        ``exceptions`` without this."""
        return {
            t: m
            for (e, cid, t), m in self.measures.items()
            if e == encoder and cid == case_id and not (m["decode_ok"] and m["content_match"])
        }

    def encoders_for(self, symbology: str) -> list[str]:
        return [
            e
            for e in self.encoders
            if any(
                (e, cid) in self.samples or (e, cid) in self.exceptions
                for cid, c in self.cases.items()
                if c["symbology"] == symbology
            )
        ]

    def cases_for(self, symbology: str, output_format: str = "png") -> list[dict]:
        """Cases of a symbology and output format, ordered by median payload
        length (stated in captions)."""
        cs = [
            c for c in self.cases.values()
            if c["symbology"] == symbology
            and c["options"].get("output_format", "png") == output_format
        ]
        return sorted(cs, key=lambda c: (statistics.median(len(t) for t in c["trials"]), c["case_id"]))

    def median_ms(self, encoder: str, case_id: str) -> float | None:
        s = self.samples.get((encoder, case_id))
        return statistics.median(s) * 1000 if s else None

    def median_area(self, encoder: str, case_id: str) -> int | None:
        # Fall back to the SVG twin: SVG-only encoders (qrcodegen,
        # ppf.datamatrix) carry no PNG measurement, so their size lives on the
        # `-svg` case. The twin is the same symbol, so mixing is exact.
        # Only decode-verified symbols count: a misdecoded symbol has a real
        # module footprint, but reporting it (and letting it win a row) would
        # present a symbol that fails the same validity gate the timing table
        # excludes. Same rule both tables - a misdecode contributes nowhere.
        twins = (case_id, f"{case_id}-svg")
        vals = [
            a
            for (e, cid, t), a in self.symbol_area.items()
            if e == encoder and cid in twins and a is not None and (e, cid, t) in self.valid
        ]
        return int(statistics.median(vals)) if vals else None
