"""Verilog-A -> OSDI compilation stage runner.

Thin subprocess wrapper around ``openvaf`` that turns a ``.va`` source
file into an ``.osdi`` shared object loadable by ngspice via
``pre_osdi``. Pairs with ``PdkConfig.netlist_osdi_lines(pdk,
extra_osdi=...)`` and ``SpiceRunner(extra_osdi=...)`` so user models
can co-exist with the PDK OSDI set.

Returns a ``StageResult`` with stage ``FlowStage.VERILOGA_COMPILE``,
an ``osdi`` artifact entry pointing at the produced file, and
``success=False`` with ``error`` populated when openvaf is absent or
the compile fails. Missing openvaf yields a skip-style result
(``success=False``, error prefixed with ``openvaf not found``) so
callers can branch on availability without raising.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

from eda_agents.core.flow_stage import FlowStage, StageResult

logger = logging.getLogger(__name__)


class VerilogACompiler:
    """Compile Verilog-A source with openvaf into an OSDI artifact.

    Parameters
    ----------
    openvaf_bin : str or Path, optional
        Explicit path to the openvaf executable. Defaults to resolving
        ``openvaf`` on ``PATH``.
    timeout_s : int
        Maximum wall-clock seconds allowed for the compile. Default 60.
    """

    def __init__(
        self,
        openvaf_bin: str | Path | None = None,
        timeout_s: int = 60,
    ):
        if openvaf_bin is not None:
            self.openvaf_bin = str(openvaf_bin)
        else:
            resolved = shutil.which("openvaf")
            self.openvaf_bin = resolved or "openvaf"
        self.timeout_s = timeout_s

    def available(self) -> bool:
        """Whether an openvaf binary was found."""
        return bool(shutil.which(self.openvaf_bin) or Path(self.openvaf_bin).is_file())

    def run(
        self,
        va_path: str | Path,
        out_dir: str | Path | None = None,
    ) -> StageResult:
        """Compile ``va_path`` with openvaf.

        openvaf writes the output alongside the input by default
        (``module.va`` -> ``module.osdi``). When ``out_dir`` is given
        the source is copied there first so the OSDI lands next to it,
        keeping the on-disk layout predictable for downstream ngspice
        includes.
        """
        va_src = Path(va_path).resolve()
        if not va_src.is_file():
            return StageResult(
                stage=FlowStage.VERILOGA_COMPILE,
                success=False,
                error=f"Verilog-A source not found: {va_src}",
            )

        if not self.available():
            return StageResult(
                stage=FlowStage.VERILOGA_COMPILE,
                success=False,
                error=f"openvaf not found (looked for '{self.openvaf_bin}')",
            )

        if out_dir is not None:
            work_va = Path(out_dir).resolve() / va_src.name
            work_va.parent.mkdir(parents=True, exist_ok=True)
            if work_va.resolve() != va_src:
                work_va.write_bytes(va_src.read_bytes())
        else:
            work_va = va_src

        expected_osdi = work_va.with_suffix(".osdi")
        if expected_osdi.is_file():
            expected_osdi.unlink()

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [self.openvaf_bin, str(work_va)],
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                cwd=str(work_va.parent),
            )
        except FileNotFoundError:
            return StageResult(
                stage=FlowStage.VERILOGA_COMPILE,
                success=False,
                error=f"openvaf not found (looked for '{self.openvaf_bin}')",
                run_time_s=time.monotonic() - t0,
            )
        except subprocess.TimeoutExpired:
            return StageResult(
                stage=FlowStage.VERILOGA_COMPILE,
                success=False,
                error=f"openvaf timed out ({self.timeout_s}s)",
                run_time_s=time.monotonic() - t0,
            )

        elapsed = time.monotonic() - t0
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        tail = (stderr + stdout)[-2000:]

        if proc.returncode != 0:
            return StageResult(
                stage=FlowStage.VERILOGA_COMPILE,
                success=False,
                error=f"openvaf exited with code {proc.returncode}",
                log_tail=tail,
                run_time_s=elapsed,
            )

        if not expected_osdi.is_file():
            return StageResult(
                stage=FlowStage.VERILOGA_COMPILE,
                success=False,
                error=f"openvaf reported success but {expected_osdi.name} not found",
                log_tail=tail,
                run_time_s=elapsed,
            )

        return StageResult(
            stage=FlowStage.VERILOGA_COMPILE,
            success=True,
            artifacts={"osdi": expected_osdi, "source": work_va},
            log_tail=tail,
            run_time_s=elapsed,
        )
