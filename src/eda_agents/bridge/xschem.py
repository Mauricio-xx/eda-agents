"""Headless xschem netlister wrapper.

xschem is the open-source schematic capture front end for ngspice. Given
an ``.sch`` file, it can emit a SPICE netlist via a no-X batch invocation::

    xschem -n -s -q -x -r --no_x -o <out_dir> -N <out_filename> <input.sch>

Flags used:

  - ``-n / --netlist``       run the netlister
  - ``-s / --spice``         force SPICE format
  - ``-q / --quit``          exit after the netlist is written
  - ``-x / --no_x``          no X11 (command mode only)
  - ``-r / --no_readline``   safe under stdin/stdout redirection
  - ``-o``                   output directory
  - ``-N``                   output filename
  - ``-l``                   log file (we always set this so we can capture
                              tcl-level errors that xschem swallows on stdout)

Returns a ``BridgeResult`` so callers can persist outcomes through the
JobRegistry.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from eda_agents.bridge.models import BridgeResult, ExecutionStatus

logger = logging.getLogger(__name__)


@dataclass
class XschemNetlistResult:
    """Detailed result of a netlist export.

    A thin dataclass alongside ``BridgeResult`` so callers that want
    typed access to ``netlist_path`` / ``log_path`` don't have to pull
    them out of ``BridgeResult.artifacts``.

    The ``infra_error`` flag separates "couldn't run xschem at all"
    (binary missing, timeout, schematic file not found) from "xschem
    ran but the result is wrong" (non-zero rc, no netlist produced).
    The first maps to ``ExecutionStatus.ERROR``, the second to
    ``ExecutionStatus.FAILURE`` so callers can distinguish a deck bug
    from a tool/setup bug.
    """

    success: bool
    netlist_path: Path | None
    log_path: Path | None
    duration_s: float
    stdout: str
    stderr: str
    error: str | None = None
    infra_error: bool = False

    def to_bridge_result(self) -> BridgeResult:
        if self.success:
            status = ExecutionStatus.SUCCESS
        elif self.infra_error:
            status = ExecutionStatus.ERROR
        else:
            status = ExecutionStatus.FAILURE
        artifacts: list[str] = []
        if self.netlist_path:
            artifacts.append(str(self.netlist_path))
        if self.log_path:
            artifacts.append(str(self.log_path))
        errors = [self.error] if self.error else []
        return BridgeResult(
            status=status,
            tool="xschem",
            output=(self.stdout or "")[-4000:],
            errors=errors,
            duration_s=self.duration_s,
            artifacts=artifacts,
            metadata={"stderr_tail": (self.stderr or "")[-1500:]},
        )


class XschemRunner:
    """Headless invoker for xschem's batch netlister.

    Parameters
    ----------
    xschem_cmd : str, optional
        xschem binary. Auto-resolved via ``shutil.which`` if omitted.
    timeout_s : int
        Hard timeout per invocation. Default 120 s.
    rcfile : Path, optional
        Override the project ``xschemrc``. xschem also honours
        ``./xschemrc``, so most callers pass the schematic's directory
        as ``cwd`` and skip this.
    """

    def __init__(
        self,
        xschem_cmd: str | None = None,
        timeout_s: int = 120,
        rcfile: Path | None = None,
    ) -> None:
        self.xschem_cmd = xschem_cmd or shutil.which("xschem") or "xschem"
        self.timeout_s = timeout_s
        self.rcfile = rcfile

    def validate_setup(self) -> list[str]:
        """Return a list of issues blocking xschem invocation."""
        problems: list[str] = []
        if not shutil.which(self.xschem_cmd) and not Path(self.xschem_cmd).is_file():
            problems.append(f"xschem not found: {self.xschem_cmd}")
        if self.rcfile and not Path(self.rcfile).is_file():
            problems.append(f"rcfile not found: {self.rcfile}")
        return problems

    def build_command(
        self,
        sch_path: Path,
        out_dir: Path,
        out_name: str,
        log_path: Path,
    ) -> list[str]:
        """Public for tests — return the argv that ``run`` would invoke."""
        cmd: list[str] = [
            self.xschem_cmd,
            "-n",       # netlist
            "-s",       # spice format
            "-q",       # quit after
            "-x",       # no X
            "-r",       # no readline (safe under redirection)
            "--no_x",   # belt + suspenders for older xschem builds
            "-o", str(out_dir),
            "-N", out_name,
            "-l", str(log_path),
        ]
        if self.rcfile:
            cmd += ["--rcfile", str(self.rcfile)]
        cmd.append(str(sch_path))
        return cmd

    def export_netlist(
        self,
        sch_path: str | Path,
        out_dir: str | Path | None = None,
        out_name: str | None = None,
        cwd: str | Path | None = None,
    ) -> XschemNetlistResult:
        """Generate a SPICE netlist from ``sch_path``.

        Parameters
        ----------
        sch_path : Path
            ``.sch`` file to netlist.
        out_dir : Path, optional
            Where the netlist lands. Defaults to ``sch_path.parent``.
        out_name : str, optional
            Netlist filename. Defaults to ``<sch.stem>.spice``.
        cwd : Path, optional
            Working directory for xschem. Defaults to ``out_dir`` so a
            project-local ``xschemrc`` is honoured.

        Returns
        -------
        XschemNetlistResult
        """
        sch_path = Path(sch_path).resolve()
        if not sch_path.is_file():
            return XschemNetlistResult(
                success=False,
                netlist_path=None,
                log_path=None,
                duration_s=0.0,
                stdout="",
                stderr="",
                error=f"schematic not found: {sch_path}",
                infra_error=True,
            )

        out_dir = Path(out_dir).resolve() if out_dir else sch_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out_name = out_name or f"{sch_path.stem}.spice"
        log_path = out_dir / f"{Path(out_name).stem}.xschem.log"
        netlist_path = out_dir / out_name
        cwd_path = Path(cwd).resolve() if cwd else out_dir

        cmd = self.build_command(sch_path, out_dir, out_name, log_path)
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                cwd=str(cwd_path),
            )
        except FileNotFoundError as exc:
            return XschemNetlistResult(
                success=False,
                netlist_path=None,
                log_path=None,
                duration_s=time.monotonic() - t0,
                stdout="",
                stderr="",
                error=f"xschem binary not found: {exc}",
                infra_error=True,
            )
        except subprocess.TimeoutExpired as exc:
            return XschemNetlistResult(
                success=False,
                netlist_path=None,
                log_path=log_path if log_path.is_file() else None,
                duration_s=time.monotonic() - t0,
                stdout=exc.stdout if isinstance(exc.stdout, str) else "",
                stderr=exc.stderr if isinstance(exc.stderr, str) else "",
                error=f"xschem timed out after {self.timeout_s}s",
                infra_error=True,
            )

        elapsed = time.monotonic() - t0
        produced = netlist_path.is_file()
        success = proc.returncode == 0 and produced
        error = None
        if not success:
            if not produced and proc.returncode == 0:
                error = "xschem returned 0 but no netlist was produced"
            elif proc.returncode != 0:
                tail = (proc.stderr or "").strip().splitlines()
                error = (
                    f"xschem exited with code {proc.returncode}: "
                    + (tail[-1] if tail else "no stderr")
                )

        return XschemNetlistResult(
            success=success,
            netlist_path=netlist_path if produced else None,
            log_path=log_path if log_path.is_file() else None,
            duration_s=elapsed,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            error=error,
        )


__all__ = ["XschemNetlistResult", "XschemRunner"]
