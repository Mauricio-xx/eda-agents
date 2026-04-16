"""``eda-bridge`` CLI — minimal job orchestrator over the bridge layer.

Subcommands:

  - ``init``     — create the cache dir and print configuration.
  - ``status``   — print whether the underlying tools (xschem, klayout,
                    ssh) are reachable.
  - ``jobs``     — list jobs persisted under ``~/.cache/eda_agents/jobs/``.
  - ``cancel``   — mark a job cancelled.
  - ``start``    — start a long-running netlist or DRC job.
  - ``stop``     — alias for ``cancel`` (parity with virtuoso-bridge-lite).

The CLI is deliberately thin: it does not embed business logic. Each
subcommand maps onto a JobRegistry / KLayoutOps / XschemRunner call so
agent-driven flows and the CLI exercise exactly the same code path.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from eda_agents.bridge.jobs import DEFAULT_JOBS_DIR, JobRegistry
from eda_agents.bridge.ssh import DEFAULT_LOG_PATH
from eda_agents.bridge.xschem import XschemRunner


# -- helpers ----------------------------------------------------------------------------


def _print(msg: str) -> None:
    print(msg, flush=True)


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True), flush=True)


def _registry(args: argparse.Namespace) -> JobRegistry:
    return JobRegistry(jobs_dir=args.jobs_dir)


# -- subcommands ------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    Path(args.jobs_dir).mkdir(parents=True, exist_ok=True)
    Path(DEFAULT_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    _print(f"jobs dir       : {args.jobs_dir}")
    _print(f"command log    : {DEFAULT_LOG_PATH}")
    _print(f"xschem binary  : {shutil.which('xschem') or 'NOT FOUND'}")
    _print(f"ngspice binary : {shutil.which('ngspice') or 'NOT FOUND'}")
    _print(f"klayout binary : {shutil.which('klayout') or 'NOT FOUND'}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    payload = {
        "tools": {
            "xschem": shutil.which("xschem"),
            "ngspice": shutil.which("ngspice"),
            "klayout": shutil.which("klayout"),
            "magic": shutil.which("magic"),
            "openroad": shutil.which("openroad"),
            "ssh": shutil.which("ssh"),
        },
        "jobs_dir": str(args.jobs_dir),
        "jobs_dir_exists": Path(args.jobs_dir).is_dir(),
    }
    if args.json:
        _print_json(payload)
    else:
        for tool, path in payload["tools"].items():
            tag = path or "MISSING"
            _print(f"{tool:9s} {tag}")
        _print(f"jobs dir : {payload['jobs_dir']}")
    return 0 if all(payload["tools"].values()) else 1


def cmd_jobs(args: argparse.Namespace) -> int:
    reg = _registry(args)
    records = reg.list()
    if args.json:
        _print_json([dict(r) for r in records])
    else:
        if not records:
            _print("(no jobs)")
            return 0
        _print(f"{'JOB ID':14s} {'STATUS':10s} {'KIND':20s} SUBMITTED")
        for r in records:
            _print(
                f"{r['job_id']:14s} {r['status']:10s} "
                f"{r.get('kind', '-'):20s} {r['submitted']}"
            )
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    reg = _registry(args)
    ok = reg.cancel(args.job_id)
    _print(f"cancel {args.job_id}: {'ok' if ok else 'no-op'}")
    return 0 if ok else 1


def cmd_start_netlist(args: argparse.Namespace) -> int:
    reg = _registry(args)
    runner = XschemRunner(timeout_s=args.timeout)
    job_id = reg.submit(
        runner.export_netlist,
        sch_path=args.sch,
        out_dir=args.out_dir,
        out_name=args.out_name,
        kind="xschem-netlist",
        metadata={"sch": str(args.sch)},
    )
    _print(f"submitted xschem job {job_id}")
    if args.wait:
        rec = reg.wait(job_id, timeout=args.timeout + 10)
        _print(f"final status: {rec.status.value if rec else 'unknown'}")
        return 0 if (rec and rec.status.value == "done") else 1
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    # alias for cancel
    return cmd_cancel(args)


# -- argparse setup ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eda-bridge", description=__doc__)
    parser.add_argument(
        "--jobs-dir",
        default=str(DEFAULT_JOBS_DIR),
        type=Path,
        help="Override the job registry directory.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create cache dirs and report config.")
    p_init.set_defaults(func=cmd_init)

    p_status = sub.add_parser("status", help="Probe tool availability.")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_jobs = sub.add_parser("jobs", help="List persisted jobs.")
    p_jobs.add_argument("--json", action="store_true")
    p_jobs.set_defaults(func=cmd_jobs)

    p_cancel = sub.add_parser("cancel", help="Cancel a queued/running job.")
    p_cancel.add_argument("job_id")
    p_cancel.set_defaults(func=cmd_cancel)

    p_stop = sub.add_parser("stop", help="Alias for ``cancel``.")
    p_stop.add_argument("job_id")
    p_stop.set_defaults(func=cmd_stop)

    p_start = sub.add_parser(
        "start",
        help="Submit a long-running task (currently: xschem-netlist).",
    )
    p_start.add_argument("kind", choices=["xschem-netlist"])
    p_start.add_argument("--sch", type=Path, required=True)
    p_start.add_argument("--out-dir", type=Path, default=None)
    p_start.add_argument("--out-name", default=None)
    p_start.add_argument("--timeout", type=int, default=120)
    p_start.add_argument("--wait", action="store_true")
    p_start.set_defaults(func=cmd_start_netlist)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
