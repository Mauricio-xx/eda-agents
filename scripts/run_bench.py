#!/usr/bin/env python3
"""eda-agents benchmark runner.

Discovers ``bench/tasks/`` (or a directory passed via ``--tasks-dir``),
loads every YAML/JSON task into a :class:`BenchTask`, executes them
through :mod:`eda_agents.bench.adapters`, and writes per-task JSON +
``report.md`` + ``summary.json`` under ``bench/results/<run_id>/``.

Modes:

* ``--dry-run``                 only run tasks whose ``harness == dry_run``
                                 (smoke check the runner pipeline without
                                 EDA tools).
* ``--family <name>``           filter tasks by family.
* ``--task <id>``               run a specific task id (repeatable).
* ``--workers N``               parallel execution via the bridge JobRegistry.
* ``--no-real-tools``           skip tasks whose backend needs ngspice /
                                 verilator / librelane (anything except
                                 ``dry-run``).

Exit codes:
    0  every task PASS or SKIPPED
    1  one or more FAIL_* / ERROR statuses
    2  unrecoverable runner-level error (couldn't find tasks, etc.)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from eda_agents.bench import (
    BenchStatus,
    TaskFamily,
    load_tasks_from_dir,
)
from eda_agents.bench.runner import run_batch

# Tools-required backends — used by ``--no-real-tools`` to skip everything
# except the deterministic mock.
_REAL_TOOL_BACKENDS = {
    "ngspice",
    "ngspice-osdi",
    "ngspice-xspice",
    "verilator",
    "librelane",
}


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tasks-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "bench" / "tasks",
        help="Root directory holding the task YAMLs.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "bench" / "results",
        help="Where to write per-run JSON + report.md.",
    )
    parser.add_argument(
        "--family",
        choices=[f.value for f in TaskFamily],
        default=None,
        help="Only run tasks whose family matches.",
    )
    parser.add_argument(
        "--task",
        action="append",
        default=[],
        help="Restrict to a specific task id (repeatable).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only execute tasks whose harness == dry_run (smoke pipeline).",
    )
    parser.add_argument(
        "--no-real-tools",
        action="store_true",
        help="Skip tasks whose expected backend would invoke ngspice / "
             "verilator / librelane.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel workers (1 = single-threaded; >1 uses bridge JobRegistry).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Override the auto-generated run identifier.",
    )
    parser.add_argument(
        "--list-tasks",
        action="store_true",
        help="Print the resolved task ids and exit; no execution.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose runner logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("eda_agents.bench")

    if not args.tasks_dir.is_dir():
        print(
            f"FATAL: tasks dir not found: {args.tasks_dir}",
            file=sys.stderr,
        )
        return 2

    try:
        all_tasks = load_tasks_from_dir(args.tasks_dir, family=args.family)
    except Exception as exc:  # noqa: BLE001 — surface to user
        print(f"FATAL: could not load tasks: {exc}", file=sys.stderr)
        return 2

    selected = list(all_tasks)
    if args.task:
        wanted = set(args.task)
        selected = [t for t in selected if t.id in wanted]
        missing = wanted - {t.id for t in selected}
        if missing:
            print(
                f"FATAL: requested task(s) not found: {sorted(missing)}",
                file=sys.stderr,
            )
            return 2
    if args.dry_run:
        selected = [t for t in selected if t.harness.value == "dry_run"]
    if args.no_real_tools:
        selected = [
            t for t in selected
            if t.expected_backend.value not in _REAL_TOOL_BACKENDS
        ]

    if not selected:
        print("WARN: no tasks selected after filters; nothing to do.", file=sys.stderr)
        return 0

    if args.list_tasks:
        for t in selected:
            print(f"{t.id}\t{t.family.value}\t{t.harness.value}\t{t.expected_backend.value}\t{t.pdk or ''}")
        return 0

    log.info(
        "running bench: %d task(s), workers=%d, results-dir=%s",
        len(selected), args.workers, args.results_dir,
    )

    summary = run_batch(
        selected,
        output_root=args.results_dir,
        run_id=args.run_id,
        workers=max(1, args.workers),
    )

    print()
    print("=" * 60)
    print(f"RUN: {summary.run_id}")
    print(
        f"total={summary.total}  PASS={summary.passed}  FAIL={summary.failed}  "
        f"SKIPPED={summary.skipped}  ERROR={summary.errored}  "
        f"Pass@1={summary.pass_rate():.0%}"
    )
    for fam, b in sorted(summary.by_family.items()):
        print(
            f"  {fam:18s}  total={b['total']:2d} pass={b['pass']:2d} "
            f"fail={b['fail']:2d} skipped={b['skipped']:2d} "
            f"error={b['error']:2d}"
        )
    print(f"report -> {args.results_dir / summary.run_id / 'report.md'}")
    print(f"latest -> {args.results_dir / 'latest.md'}")
    print("=" * 60)

    bad = sum(
        1 for r in summary.results
        if r.status not in {BenchStatus.PASS, BenchStatus.SKIPPED, BenchStatus.FAIL_INFRA}
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
