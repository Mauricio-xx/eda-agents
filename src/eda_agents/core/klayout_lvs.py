"""KLayout LVS runner for GF180MCU PDK.

Invokes the PDK's run_lvs.py as a subprocess, captures exit code,
and parses stdout for match/mismatch verdict.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_VARIANT = "C"

_LVS_SCRIPT_REL = "gf180mcuD/libs.tech/klayout/tech/lvs/run_lvs.py"


@dataclass
class KLayoutLvsResult:
    """Result of a KLayout LVS run."""

    success: bool
    """True if klayout executed without crashing."""
    match: bool
    """True if layout and schematic netlists match."""
    extracted_netlist_path: str | None = None
    """Path to the extracted .cir netlist from layout."""
    report_path: str | None = None
    """Path to the .lvsdb report."""
    run_time_s: float = 0.0
    stdout_tail: str = ""
    error: str | None = None

    @property
    def summary(self) -> str:
        if self.error:
            return f"KLayout LVS error: {self.error}"
        return "KLayout LVS: match" if self.match else "KLayout LVS: MISMATCH"


class KLayoutLvsRunner:
    """Runs GF180MCU KLayout LVS via the PDK's run_lvs.py script.

    Parameters
    ----------
    pdk_root : str or None
        Path to PDK root. Falls back to PDK_ROOT env, then GF180MCU_D default.
    variant : str
        PDK variant (A-D). Default "C" = 5LM, 9K top metal.
    timeout_s : int
        Maximum runtime in seconds.
    python_cmd : str or None
        Python interpreter with klayout.db and docopt.
        Auto-detected if None.
    """

    def __init__(
        self,
        pdk_root: str | None = None,
        variant: str = DEFAULT_VARIANT,
        timeout_s: int = 600,
        python_cmd: str | None = None,
    ):
        self.variant = variant
        self.timeout_s = timeout_s

        if python_cmd:
            self.python_cmd = python_cmd
        else:
            from eda_agents.core.klayout_drc import _find_klayout_python
            self.python_cmd = _find_klayout_python()

        if pdk_root:
            self.pdk_root = Path(pdk_root)
        else:
            env_root = os.environ.get("PDK_ROOT")
            if env_root:
                self.pdk_root = Path(env_root)
            else:
                from eda_agents.core.pdk import GF180MCU_D
                self.pdk_root = Path(GF180MCU_D.default_pdk_root)

        self._lvs_script = self.pdk_root / _LVS_SCRIPT_REL

    def validate_setup(self) -> list[str]:
        """Check prerequisites. Returns list of problems (empty = OK)."""
        import shutil
        import subprocess

        problems = []

        if not self.pdk_root.is_dir():
            problems.append(f"PDK root not found: {self.pdk_root}")

        if not self._lvs_script.is_file():
            problems.append(f"run_lvs.py not found: {self._lvs_script}")

        if not shutil.which("klayout"):
            problems.append("klayout not found in PATH")

        try:
            proc = subprocess.run(
                [self.python_cmd, "-c", "import klayout.db; import docopt"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                problems.append(
                    f"{self.python_cmd} missing klayout.db or docopt: "
                    f"{proc.stderr.strip()}"
                )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            problems.append(f"Cannot run {self.python_cmd}: {e}")

        return problems

    def run(
        self,
        gds_path: str | Path,
        netlist_path: str | Path,
        run_dir: str | Path,
        top_cell: str | None = None,
        lvs_sub: str | None = None,
    ) -> KLayoutLvsResult:
        """Run LVS comparing layout GDS against schematic netlist.

        Parameters
        ----------
        gds_path : path
            Input GDS layout file.
        netlist_path : path
            Reference schematic netlist (SPICE/CDL).
        run_dir : path
            Output directory for reports.
        top_cell : str or None
            Top cell name. Auto-detected if None.
        lvs_sub : str or None
            Substrate net name. Default: gf180mcu_gnd.

        Returns
        -------
        KLayoutLvsResult
        """
        import subprocess

        gds_path = Path(gds_path).resolve()
        netlist_path = Path(netlist_path).resolve()
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        if not gds_path.is_file():
            return KLayoutLvsResult(
                success=False,
                match=False,
                error=f"GDS file not found: {gds_path}",
            )

        if not netlist_path.is_file():
            return KLayoutLvsResult(
                success=False,
                match=False,
                error=f"Netlist file not found: {netlist_path}",
            )

        if not self._lvs_script.is_file():
            return KLayoutLvsResult(
                success=False,
                match=False,
                error=f"run_lvs.py not found: {self._lvs_script}",
            )

        cmd = [
            self.python_cmd,
            str(self._lvs_script),
            f"--layout={gds_path}",
            f"--netlist={netlist_path}",
            f"--variant={self.variant}",
            f"--run_dir={run_dir}",
        ]

        if top_cell:
            cmd.append(f"--topcell={top_cell}")

        if lvs_sub:
            cmd.append(f"--lvs_sub={lvs_sub}")

        logger.info("Running KLayout LVS: %s", " ".join(cmd))
        t0 = time.monotonic()

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                cwd=str(run_dir),
            )
        except FileNotFoundError:
            return KLayoutLvsResult(
                success=False,
                match=False,
                error=f"Python interpreter not found: {self.python_cmd}",
            )
        except subprocess.TimeoutExpired:
            return KLayoutLvsResult(
                success=False,
                match=False,
                error=f"LVS timed out after {self.timeout_s}s",
                run_time_s=time.monotonic() - t0,
            )

        elapsed = time.monotonic() - t0
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # Detect match from stdout
        stdout_lower = stdout.lower()
        match = (
            "congratulations" in stdout_lower
            or ("match" in stdout_lower and "mismatch" not in stdout_lower)
        )

        # Find output files
        layout_stem = gds_path.stem
        lvsdb = run_dir / f"{layout_stem}.lvsdb"
        ext_cir = run_dir / f"{layout_stem}.cir"

        # Also search in timestamped subdirectories (run_lvs.py creates them)
        if not lvsdb.is_file():
            lvsdb_files = sorted(run_dir.rglob("*.lvsdb"))
            lvsdb = lvsdb_files[0] if lvsdb_files else None

        if not ext_cir.is_file():
            cir_files = sorted(run_dir.rglob("*.cir"))
            ext_cir = cir_files[0] if cir_files else None

        # exit 0 = clean, exit 1 = mismatch (not a crash)
        success = proc.returncode in (0, 1)

        if not success:
            return KLayoutLvsResult(
                success=False,
                match=False,
                error=stderr.strip()[-500:] or f"LVS crashed (exit {proc.returncode})",
                run_time_s=elapsed,
                stdout_tail=stdout[-2000:],
            )

        return KLayoutLvsResult(
            success=True,
            match=match,
            extracted_netlist_path=str(ext_cir) if ext_cir and ext_cir.is_file() else None,
            report_path=str(lvsdb) if lvsdb and lvsdb.is_file() else None,
            run_time_s=elapsed,
            stdout_tail=stdout[-2000:],
        )
