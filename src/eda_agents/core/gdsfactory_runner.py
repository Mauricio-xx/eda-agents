"""gdsfactory runner for RF layout generation.

Invokes a Python callable inside an isolated ``.venv-gdsfactory`` so
gdsfactory and downstream PDK adapters (for example the iic-jku/IHP
package on branch ``IHP-TO``) stay out of the main eda-agents
environment. This mirrors :mod:`eda_agents.core.glayout_runner`:
a thin subprocess wrapper that drives a stdin/stdout JSON
contract against a ``_gdsfactory_driver.py`` script shipped alongside
the package.

Setup::

    python3 -m venv .venv-gdsfactory
    .venv-gdsfactory/bin/pip install gdsfactory

    # Optional: install the iic-jku/IHP gdsfactory adapter
    git clone -b IHP-TO https://github.com/iic-jku/IHP.git iic-jku-IHP
    .venv-gdsfactory/bin/pip install ./iic-jku-IHP

The runner is intentionally narrow. Callers identify their factory
with a ``module:callable`` string and pass keyword params; the driver
imports the module inside the venv, calls the function, and reports
back the GDS path. The factory may return a ``gdsfactory.Component``,
a path to an already-written GDS, or ``None`` (driver picks the
newest GDS written into ``output_dir`` during the call).

Callers that need eda-agents modules to be importable inside the
gdsfactory venv (for example :mod:`eda_agents.topologies.rf`) must
have ``PYTHONPATH`` cover the source tree. The runner forwards
``EDA_AGENTS_SRC`` from the parent environment when set; otherwise it
auto-detects the worktree's ``src/`` directory from the location of
this file.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from importlib.resources import files as _files
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_GDSFACTORY_VENV = ".venv-gdsfactory"
_DRIVER_SCRIPT = Path(str(_files("eda_agents.core") / "_gdsfactory_driver.py"))


def _autodetect_src_root() -> Path | None:
    """Return the worktree's ``src/`` directory if eda_agents lives in one."""
    here = Path(__file__).resolve()
    # core/gdsfactory_runner.py -> core/ -> eda_agents/ -> src/
    src = here.parent.parent.parent
    if src.name == "src" and (src / "eda_agents").is_dir():
        return src
    return None


@dataclass
class GdsResult:
    """Result of a gdsfactory generation run."""

    success: bool
    gds_path: str | None = None
    lyp_path: str | None = None
    log_path: str | None = None
    top_cell: str = ""
    component_factory: str = ""
    params: dict = field(default_factory=dict)
    run_time_s: float = 0.0
    error: str | None = None

    @property
    def summary(self) -> str:
        if self.error:
            return f"gdsfactory error: {self.error}"
        bits = [f"gdsfactory: {self.component_factory} -> {self.gds_path}"]
        if self.top_cell:
            bits.append(f"top {self.top_cell}")
        return ", ".join(bits)


class GdsfactoryRunner:
    """Subprocess wrapper that drives a Python callable inside .venv-gdsfactory.

    Parameters
    ----------
    gdsfactory_venv : str or Path
        Path to the gdsfactory venv. The runner reads ``<venv>/bin/python``.
    timeout_s : int
        Maximum runtime in seconds (default 600). SPARX layout at 60 GHz
        is the smallest top cell and still takes minutes; bump for
        higher-frequency designs.
    driver_script : str or Path or None
        Path to ``_gdsfactory_driver.py``. Auto-resolved via
        ``importlib.resources`` if None.
    pythonpath_extra : sequence of str or None
        Additional directories prepended to ``PYTHONPATH`` for the
        subprocess. Defaults include the worktree's ``src/`` (so
        ``eda_agents.topologies.rf`` is importable inside the venv).
    """

    def __init__(
        self,
        gdsfactory_venv: str | Path = _DEFAULT_GDSFACTORY_VENV,
        timeout_s: int = 600,
        driver_script: str | Path | None = None,
        pythonpath_extra: list[str] | None = None,
    ):
        self.venv_path = Path(gdsfactory_venv)
        self.timeout_s = timeout_s
        self.driver_script = (
            Path(driver_script) if driver_script else _DRIVER_SCRIPT
        )
        self._python = self.venv_path / "bin" / "python"

        extras: list[str] = []
        if pythonpath_extra:
            extras.extend(pythonpath_extra)
        src_root = _autodetect_src_root()
        if src_root:
            extras.append(str(src_root))
        self._pythonpath_extra = extras

    def validate_setup(self) -> list[str]:
        """Return a list of problems with the setup (empty list = OK)."""
        problems: list[str] = []

        if not self.venv_path.is_dir():
            problems.append(
                f"gdsfactory venv not found: {self.venv_path}. "
                f"Create with: python3 -m venv {self.venv_path}"
            )
            return problems

        if not self._python.is_file():
            problems.append(f"Python not found in venv: {self._python}")
            return problems

        if not self.driver_script.is_file():
            problems.append(f"Driver script not found: {self.driver_script}")

        try:
            proc = subprocess.run(
                [str(self._python), "-c", "import gdsfactory"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode != 0:
                problems.append(
                    f"gdsfactory not importable in venv: {proc.stderr.strip()}"
                )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            problems.append(f"Cannot run venv python: {exc}")

        return problems

    def _build_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if self._pythonpath_extra:
            existing = env.get("PYTHONPATH", "")
            merged = os.pathsep.join([*self._pythonpath_extra, existing])
            env["PYTHONPATH"] = merged.rstrip(os.pathsep)
        if extra:
            env.update(extra)
        return env

    def generate_component(
        self,
        component_factory: str,
        params: dict,
        output_dir: str | Path,
        output_gds_name: str | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> GdsResult:
        """Run ``component_factory(**params)`` inside .venv-gdsfactory.

        Parameters
        ----------
        component_factory : str
            ``"module.path:callable"`` identifier; resolved via
            ``importlib.import_module`` inside the venv.
        params : dict
            Keyword arguments forwarded verbatim to the callable. Values
            must be JSON-serialisable.
        output_dir : path
            Directory where the GDS (and the driver log) will land.
        output_gds_name : str or None
            File name for the GDS when the factory returns a
            ``Component``. Defaults to ``<component.name>.gds`` if the
            factory returns a Component; ignored when the factory
            already wrote a file.
        env_extra : dict or None
            Extra environment variables for the subprocess (e.g.
            ``{"PDK_ROOT": "..."}``).
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if not self._python.is_file():
            return GdsResult(
                success=False,
                component_factory=component_factory,
                params=params,
                error=f"gdsfactory venv python not found: {self._python}",
            )

        if not self.driver_script.is_file():
            return GdsResult(
                success=False,
                component_factory=component_factory,
                params=params,
                error=f"Driver script not found: {self.driver_script}",
            )

        spec = {
            "component_factory": component_factory,
            "params": params,
            "output_dir": str(output_dir),
            "output_gds_name": output_gds_name,
        }

        t0 = time.monotonic()

        try:
            proc = subprocess.run(
                [str(self._python), str(self.driver_script)],
                input=json.dumps(spec),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                env=self._build_env(env_extra),
            )
        except FileNotFoundError:
            return GdsResult(
                success=False,
                component_factory=component_factory,
                params=params,
                error=f"Cannot execute: {self._python}",
            )
        except subprocess.TimeoutExpired:
            return GdsResult(
                success=False,
                component_factory=component_factory,
                params=params,
                error=f"Generation timed out after {self.timeout_s}s",
                run_time_s=time.monotonic() - t0,
            )

        elapsed = time.monotonic() - t0
        last_line = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        try:
            result = json.loads(last_line[0]) if last_line[0] else {}
        except json.JSONDecodeError:
            return GdsResult(
                success=False,
                component_factory=component_factory,
                params=params,
                error=(
                    (proc.stderr or "").strip()[-500:]
                    or (proc.stdout or "")[:300]
                    or f"Driver exited {proc.returncode}"
                ),
                run_time_s=elapsed,
            )

        if not result.get("success"):
            return GdsResult(
                success=False,
                component_factory=component_factory,
                params=params,
                error=result.get(
                    "error", f"Driver exited {proc.returncode}"
                ),
                log_path=result.get("log_path"),
                run_time_s=elapsed,
            )

        if proc.returncode != 0:
            return GdsResult(
                success=False,
                component_factory=component_factory,
                params=params,
                error=(
                    f"Driver reported success but exited {proc.returncode}; "
                    f"stderr={(proc.stderr or '').strip()[-300:]}"
                ),
                run_time_s=elapsed,
            )

        return GdsResult(
            success=True,
            gds_path=result.get("gds_path"),
            lyp_path=result.get("lyp_path"),
            log_path=result.get("log_path"),
            top_cell=result.get("top_cell", ""),
            component_factory=component_factory,
            params=params,
            run_time_s=elapsed,
        )
