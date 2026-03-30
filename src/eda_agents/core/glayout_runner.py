"""gLayout runner for GF180MCU analog layout generation.

Invokes gLayout (from OpenFASOC) in an isolated venv to avoid
numpy version conflicts with the main eda-agents environment.

The driver script (scripts/glayout_driver.py) runs inside the
gLayout venv, reads a JSON spec from stdin, and writes a JSON
result to stdout.

Setup::

    python3 -m venv .venv-glayout
    .venv-glayout/bin/pip install numpy==1.24.0 gdstk
    .venv-glayout/bin/pip install -e /path/to/OpenFASOC/openfasoc/generators/glayout
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Default paths
_DEFAULT_GLAYOUT_VENV = ".venv-glayout"
_DRIVER_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "glayout_driver.py"


@dataclass
class GLayoutResult:
    """Result of a gLayout generation run."""

    success: bool
    gds_path: str | None = None
    netlist_path: str | None = None
    top_cell: str = ""
    component: str = ""
    params: dict | None = None
    run_time_s: float = 0.0
    error: str | None = None

    @property
    def summary(self) -> str:
        if self.error:
            return f"gLayout error: {self.error}"
        parts = [f"gLayout: generated {self.component} -> {self.gds_path}"]
        if self.netlist_path:
            parts.append(f"netlist: {self.netlist_path}")
        return ", ".join(parts)


class GLayoutRunner:
    """Generates parameterized analog layouts via gLayout (OpenFASOC).

    Uses a separate venv to isolate numpy version requirements.

    Parameters
    ----------
    glayout_venv : str
        Path to the gLayout virtual environment.
    timeout_s : int
        Maximum runtime in seconds.
    driver_script : str or Path or None
        Path to glayout_driver.py. Auto-detected if None.
    pdk : str
        Target PDK for layout generation. Default "gf180mcu".
    """

    def __init__(
        self,
        glayout_venv: str = _DEFAULT_GLAYOUT_VENV,
        timeout_s: int = 300,
        driver_script: str | Path | None = None,
        pdk: str = "gf180mcu",
    ):
        self.venv_path = Path(glayout_venv)
        self.timeout_s = timeout_s
        self.pdk = pdk

        if driver_script:
            self.driver_script = Path(driver_script)
        else:
            self.driver_script = _DRIVER_SCRIPT

        # Python executable inside the gLayout venv
        self._python = self.venv_path / "bin" / "python"

    def validate_setup(self) -> list[str]:
        """Check prerequisites. Returns list of problems (empty = OK)."""
        import subprocess

        problems = []

        if not self.venv_path.is_dir():
            problems.append(
                f"gLayout venv not found: {self.venv_path}. "
                f"Create with: python3 -m venv {self.venv_path}"
            )
            return problems  # Can't check further without venv

        if not self._python.is_file():
            problems.append(f"Python not found in venv: {self._python}")
            return problems

        if not self.driver_script.is_file():
            problems.append(f"Driver script not found: {self.driver_script}")

        # Check gLayout is importable in the venv
        try:
            proc = subprocess.run(
                [str(self._python), "-c", "import glayout; import gdstk"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                problems.append(
                    f"gLayout not installed in venv: {proc.stderr.strip()}"
                )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            problems.append(f"Cannot run venv python: {e}")

        return problems

    def generate_component(
        self,
        component: str,
        params: dict,
        output_dir: str | Path,
    ) -> GLayoutResult:
        """Generate a layout component.

        Parameters
        ----------
        component : str
            Component type (e.g., "nmos", "pmos", "mimcap").
        params : dict
            Component parameters (width, length, fingers, etc.).
        output_dir : path
            Directory to write the output GDS.

        Returns
        -------
        GLayoutResult
        """
        import subprocess

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if not self._python.is_file():
            return GLayoutResult(
                success=False,
                component=component,
                params=params,
                error=f"gLayout venv python not found: {self._python}",
            )

        if not self.driver_script.is_file():
            return GLayoutResult(
                success=False,
                component=component,
                params=params,
                error=f"Driver script not found: {self.driver_script}",
            )

        spec = {
            "component": component,
            "params": params,
            "output_dir": str(output_dir),
            "pdk": self.pdk,
        }

        t0 = time.monotonic()

        try:
            proc = subprocess.run(
                [str(self._python), str(self.driver_script)],
                input=json.dumps(spec),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except FileNotFoundError:
            return GLayoutResult(
                success=False,
                component=component,
                params=params,
                error=f"Cannot execute: {self._python}",
            )
        except subprocess.TimeoutExpired:
            return GLayoutResult(
                success=False,
                component=component,
                params=params,
                error=f"Layout generation timed out after {self.timeout_s}s",
                run_time_s=time.monotonic() - t0,
            )

        elapsed = time.monotonic() - t0

        if proc.returncode != 0:
            return GLayoutResult(
                success=False,
                component=component,
                params=params,
                error=proc.stderr.strip()[-500:] or f"Driver exited {proc.returncode}",
                run_time_s=elapsed,
            )

        # Parse JSON output from driver
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return GLayoutResult(
                success=False,
                component=component,
                params=params,
                error=f"Invalid driver output: {proc.stdout[:200]}",
                run_time_s=elapsed,
            )

        if not result.get("success"):
            return GLayoutResult(
                success=False,
                component=component,
                params=params,
                error=result.get("error", "Unknown driver error"),
                run_time_s=elapsed,
            )

        return GLayoutResult(
            success=True,
            gds_path=result.get("gds_path"),
            netlist_path=result.get("netlist_path"),
            top_cell=result.get("top_cell", component),
            component=component,
            params=params,
            run_time_s=elapsed,
        )

    def generate_ota(
        self,
        sizing: dict,
        output_dir: str | Path,
    ) -> GLayoutResult:
        """Generate a full OTA layout from topology sizing.

        Converts sizing dict (from GF180OTATopology.params_to_sizing()) into
        gLayout opamp_twostage() parameters and invokes the driver.

        Parameters
        ----------
        sizing : dict
            Transistor sizing from GF180OTATopology.params_to_sizing().
        output_dir : path
            Directory for GDS and SPICE netlist output.

        Returns
        -------
        GLayoutResult
            Includes both gds_path and netlist_path on success.
        """
        from eda_agents.topologies.ota_gf180 import GF180OTATopology

        topo = GF180OTATopology()
        glayout_params = topo.sizing_to_glayout_params(sizing)

        return self.generate_component(
            component="opamp_twostage",
            params=glayout_params,
            output_dir=output_dir,
        )
