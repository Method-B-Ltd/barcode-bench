"""Command-line entry point for the benchmark suite.

Subcommands mirror the pipeline stages and are individually runnable so a
stage can be re-executed without repeating the ones before it (e.g. re-measure
existing PNGs after fixing a codeword table, without re-running containers):

    gen      generate the seeded payload corpus for a new run directory
    build    build the per-encoder podman images
    weigh    record image sizes (install weight = delta over the base image)
    run      execute the timed benchmark inside each encoder container
    measure  decode produced PNGs with zxing-cpp, derive symbol sizes
    report   aggregate raw records into summary.csv / summary.md
    publish  generate the facts-only public report (report_*.md + charts/)
    all      the whole pipeline in order
"""

from __future__ import annotations

import argparse
import sys

from barcode_bench.corpus import SYMBOLOGIES


def _add_run_dir_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--run-dir",
        required=True,
        help="run directory created by `gen` (bench/runs/<stamp>-seed<N>)",
    )


def _add_encoders_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--encoders",
        default=None,
        help="comma-separated encoder ids (default: all registered)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="barcode-bench",
        description="Benchmark barcode encoder libraries: runtime, symbol size, install weight.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("gen", help="generate a seeded payload corpus")
    p_gen.add_argument("--seed", type=int, default=None, help="master seed (default: random, printed)")
    p_gen.add_argument("--trials", type=int, default=5, help="payload variants per case")
    p_gen.add_argument(
        "--symbology", choices=SYMBOLOGIES, default=None,
        help="restrict the corpus to one symbology (default: all)",
    )

    p_build = sub.add_parser("build", help="build per-encoder podman images")
    _add_encoders_arg(p_build)
    p_build.add_argument("--no-cache", action="store_true", help="pass --no-cache to podman build")

    p_weigh = sub.add_parser("weigh", help="record image sizes into a run directory")
    _add_run_dir_arg(p_weigh)
    _add_encoders_arg(p_weigh)

    p_run = sub.add_parser("run", help="run the benchmark containers")
    _add_run_dir_arg(p_run)
    _add_encoders_arg(p_run)
    p_run.add_argument("--reps", type=int, default=3, help="timed repetitions per trial payload")
    p_run.add_argument("--quick", action="store_true", help="only cases marked quick, 1 rep")

    p_measure = sub.add_parser("measure", help="decode PNGs and derive symbol sizes")
    _add_run_dir_arg(p_measure)

    p_report = sub.add_parser("report", help="aggregate into summary.csv / summary.md")
    _add_run_dir_arg(p_report)

    p_publish = sub.add_parser(
        "publish", help="generate the facts-only public report (report.md + charts/)"
    )
    _add_run_dir_arg(p_publish)

    p_all = sub.add_parser(
        "all", help="gen + build + weigh + run + measure + report + publish"
    )
    p_all.add_argument("--seed", type=int, default=None)
    p_all.add_argument("--trials", type=int, default=5)
    p_all.add_argument("--symbology", choices=SYMBOLOGIES, default=None)
    _add_encoders_arg(p_all)
    p_all.add_argument("--reps", type=int, default=3)
    p_all.add_argument("--quick", action="store_true")
    p_all.add_argument("--no-cache", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Imports are deferred per subcommand so that `gen`/`report` (pure stdlib +
    # host deps) never import podman plumbing, and vice versa.
    if args.command == "gen":
        from barcode_bench.corpus import cmd_gen

        return cmd_gen(seed=args.seed, trials=args.trials, symbology=args.symbology)
    if args.command == "build":
        from barcode_bench.orchestrate import cmd_build

        return cmd_build(encoders=_split(args.encoders), no_cache=args.no_cache)
    if args.command == "weigh":
        from barcode_bench.orchestrate import cmd_weigh

        return cmd_weigh(run_dir=args.run_dir, encoders=_split(args.encoders))
    if args.command == "run":
        from barcode_bench.orchestrate import cmd_run

        return cmd_run(
            run_dir=args.run_dir,
            encoders=_split(args.encoders),
            reps=args.reps,
            quick=args.quick,
        )
    if args.command == "measure":
        from barcode_bench.measure import cmd_measure

        return cmd_measure(run_dir=args.run_dir)
    if args.command == "report":
        from barcode_bench.report import cmd_report

        return cmd_report(run_dir=args.run_dir)
    if args.command == "publish":
        from barcode_bench.publish import cmd_publish

        return cmd_publish(run_dir=args.run_dir)
    if args.command == "all":
        return _cmd_all(args)

    raise AssertionError(f"unhandled command {args.command!r}")


def _cmd_all(args: argparse.Namespace) -> int:
    from barcode_bench.corpus import generate_run
    from barcode_bench.measure import cmd_measure
    from barcode_bench.orchestrate import cmd_build, cmd_run, cmd_weigh
    from barcode_bench.publish import cmd_publish
    from barcode_bench.report import cmd_report

    encoders = _split(args.encoders)
    run_dir = str(generate_run(seed=args.seed, trials=args.trials, symbology=args.symbology))
    for step in (
        lambda: cmd_build(encoders=encoders, no_cache=args.no_cache),
        lambda: cmd_weigh(run_dir=run_dir, encoders=encoders),
        lambda: cmd_run(run_dir=run_dir, encoders=encoders, reps=args.reps, quick=args.quick),
        lambda: cmd_measure(run_dir=run_dir),
        lambda: cmd_report(run_dir=run_dir),
        lambda: cmd_publish(run_dir=run_dir),
    ):
        rc = step()
        if rc:
            return rc
    return 0


def _split(value: str | None) -> list[str] | None:
    return value.split(",") if value else None


if __name__ == "__main__":
    sys.exit(main())
