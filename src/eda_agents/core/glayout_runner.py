"""gLayout runner for GF180MCU analog layout generation.

Invokes gLayout (from OpenFASOC) in an isolated venv to avoid
numpy version conflicts with the main eda-agents environment.

The driver script ships inside the package as
``eda_agents.core._glayout_driver`` and is resolved via
``importlib.resources`` so it works under editable and wheel
installs alike. It runs inside the gLayout venv, reads a JSON spec
from stdin, and writes a JSON result to stdout.

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
from importlib.resources import files as _files
from pathlib import Path

logger = logging.getLogger(__name__)

# Default paths
_DEFAULT_GLAYOUT_VENV = ".venv-glayout"
# Driver script resolved via importlib.resources so it works in
# editable installs (src/) and wheel installs (site-packages/) alike.
_DRIVER_SCRIPT = Path(str(_files("eda_agents.core") / "_glayout_driver.py"))


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

        # Parse JSON output from driver first — the driver writes a
        # structured error message to stdout even when it exits non-zero
        # (spec-level failures: unknown PDK, unknown component). Only
        # fall back to the generic "Driver exited N" message when stdout
        # is not well-formed JSON.
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return GLayoutResult(
                success=False,
                component=component,
                params=params,
                error=(
                    proc.stderr.strip()[-500:]
                    or proc.stdout[:200]
                    or f"Driver exited {proc.returncode}"
                ),
                run_time_s=elapsed,
            )

        if not result.get("success"):
            return GLayoutResult(
                success=False,
                component=component,
                params=params,
                error=result.get(
                    "error", f"Driver exited {proc.returncode}"
                ),
                run_time_s=elapsed,
            )

        if proc.returncode != 0:
            # Structured JSON says success but the process exited non-zero.
            # Surface that loudly — it's a driver contract violation.
            return GLayoutResult(
                success=False,
                component=component,
                params=params,
                error=(
                    f"Driver success=True but exit={proc.returncode}; "
                    f"stderr={proc.stderr.strip()[-300:]}"
                ),
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

    def stitch_gdses(
        self,
        sub_block_gdses: dict[str, str | Path],
        output_gds: str | Path,
        top_cell_name: str = "composition_top",
        gutter_um: float = 2.0,
    ) -> GLayoutResult:
        """Compose a single top-level GDS from already-generated sub-block GDSes.

        Places each sub-block in a simple row layout left-to-right with a
        fixed gutter between them. Intended for the analog composition
        loop's top-level placement step — it is NOT a routing-aware
        placer. No inter-block nets are drawn; the loop keeps its
        top-level SPICE netlist as the source of truth for connectivity
        and LVS wiring.

        Runs inside ``.venv-glayout`` via gdstk (available in the
        gLayout environment, not in the main eda-agents venv). Returns
        a ``GLayoutResult`` whose ``gds_path`` points at the stitched
        output on success.
        """
        import subprocess

        if not sub_block_gdses:
            return GLayoutResult(
                success=False,
                component="composition_top",
                error="stitch_gdses called with empty sub_block_gdses",
            )

        if not self._python.is_file():
            return GLayoutResult(
                success=False,
                component="composition_top",
                error=f"gLayout venv python not found: {self._python}",
            )

        # The stitcher is short enough to inline; the alternative (a
        # separate driver.py) would pull in the same subprocess +
        # JSON-over-stdin plumbing for twelve lines of work.
        stitcher_py = r"""
import json, sys
import gdstk

spec = json.loads(sys.stdin.read())
out_gds = spec["output_gds"]
top_name = spec["top_cell_name"]
gutter = float(spec["gutter_um"])

top_lib = gdstk.Library(name=top_name, unit=1e-6, precision=1e-9)
top_cell = top_lib.new_cell(top_name)

x_offset = 0.0
placements = {}
for name, gds_path in spec["sub_block_gdses"].items():
    try:
        sub_lib = gdstk.read_gds(gds_path)
    except Exception as exc:
        print(json.dumps({"success": False,
                          "error": f"read_gds failed for {name}: {exc}"}))
        sys.exit(1)
    tops = sub_lib.top_level()
    if not tops:
        print(json.dumps({"success": False,
                          "error": f"no top cell in {gds_path}"}))
        sys.exit(1)
    sub_top = tops[0]
    # Re-register the sub-cell + its full dependency tree under a
    # unique name so multiple placements never collide.
    for dep in [sub_top] + list(sub_top.dependencies(True)):
        dep.name = f"{name}__{dep.name}"
        top_lib.add(dep)
    bb = sub_top.bounding_box()
    width = (bb[1][0] - bb[0][0]) if bb else 0.0
    height = (bb[1][1] - bb[0][1]) if bb else 0.0
    top_cell.add(gdstk.Reference(sub_top, origin=(x_offset - (bb[0][0] if bb else 0.0), 0.0)))
    placements[name] = {"x": x_offset, "y": 0.0, "w": width, "h": height}
    x_offset += width + gutter

top_lib.write_gds(out_gds)
print(json.dumps({
    "success": True,
    "gds_path": out_gds,
    "top_cell": top_name,
    "placements": placements,
}))
"""

        spec = {
            "sub_block_gdses": {k: str(v) for k, v in sub_block_gdses.items()},
            "output_gds": str(output_gds),
            "top_cell_name": top_cell_name,
            "gutter_um": float(gutter_um),
        }

        Path(output_gds).parent.mkdir(parents=True, exist_ok=True)

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [str(self._python), "-c", stitcher_py],
                input=json.dumps(spec),
                capture_output=True,
                text=True,
                timeout=max(60, self.timeout_s),
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except subprocess.TimeoutExpired:
            return GLayoutResult(
                success=False,
                component="composition_top",
                error=f"stitch_gdses timed out after {self.timeout_s}s",
                run_time_s=time.monotonic() - t0,
            )

        elapsed = time.monotonic() - t0

        try:
            result = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return GLayoutResult(
                success=False,
                component="composition_top",
                error=(
                    proc.stderr.strip()[-500:]
                    or proc.stdout.strip()[-500:]
                    or f"stitcher exited {proc.returncode}"
                ),
                run_time_s=elapsed,
            )
        if not result.get("success"):
            return GLayoutResult(
                success=False,
                component="composition_top",
                error=result.get("error", "unknown stitcher error"),
                run_time_s=elapsed,
            )
        return GLayoutResult(
            success=True,
            gds_path=result.get("gds_path"),
            top_cell=result.get("top_cell", top_cell_name),
            component="composition_top",
            run_time_s=elapsed,
        )

    def drc_gds(
        self,
        gds_path: str | Path,
        output_dir: str | Path,
        design_name: str | None = None,
    ) -> dict:
        """Run the active PDK's KLayout DRC deck on a GDS file.

        Executes inside ``.venv-glayout`` so both gdsfactory and the
        MappedPDK DRC bindings are available. Returns a dict with
        ``{"clean": bool, "total_violations": int, "per_rule": {...},
        "lyrdb_path": str, "error": str | None}``.
        """
        import subprocess

        gds_path = Path(gds_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if not self._python.is_file():
            return {"clean": False, "error": f"gLayout venv python not found: {self._python}"}

        if not gds_path.is_file():
            return {"clean": False, "error": f"GDS not found: {gds_path}"}

        design_name = design_name or gds_path.stem

        drc_py = r"""
import json, sys, os
from pathlib import Path
import xml.etree.ElementTree as ET
from collections import Counter

spec = json.loads(sys.stdin.read())
pdk_name = spec["pdk"]
gds_path = spec["gds_path"]
output_dir = Path(spec["output_dir"])
design_name = spec["design_name"]

if pdk_name == "ihp_sg13g2":
    from glayout.pdk.sg13g2_mapped.sg13g2_mapped import sg13g2_mapped_pdk as _pdk
elif pdk_name in ("gf180mcu", "gf180"):
    from glayout.pdk.gf180_mapped import gf180_mapped_pdk as _pdk
else:
    print(json.dumps({"clean": False, "error": f"unsupported pdk: {pdk_name}"}))
    sys.exit(1)

os.chdir(output_dir)
try:
    drc_clean = _pdk.drc(gds_path, str(output_dir))
except Exception as exc:
    print(json.dumps({"clean": False, "error": f"drc() raised: {type(exc).__name__}: {exc}"}))
    sys.exit(1)

# Find and parse lyrdb
lyrdb_candidates = list(output_dir.rglob("*drcreport*.lyrdb"))
lyrdb_candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
lyrdb = lyrdb_candidates[0] if lyrdb_candidates else None
per_rule = {}
total = 0
if lyrdb and lyrdb.is_file():
    try:
        tree = ET.parse(lyrdb)
        counts: Counter = Counter()
        for item in tree.getroot().iter("item"):
            cat = item.findtext("category", default="(uncategorized)").strip().strip("'\"")
            counts[cat] += 1
        total = int(sum(counts.values()))
        per_rule = dict(counts)
    except Exception as exc:
        per_rule = {"_parse_error": str(exc)}

print(json.dumps({
    "clean": bool(drc_clean) and total == 0,
    "total_violations": total,
    "per_rule": per_rule,
    "lyrdb_path": str(lyrdb) if lyrdb else None,
    "error": None,
}))
"""

        spec = {
            "pdk": self.pdk,
            "gds_path": str(gds_path),
            "output_dir": str(output_dir),
            "design_name": design_name,
        }

        try:
            proc = subprocess.run(
                [str(self._python), "-c", drc_py],
                input=json.dumps(spec),
                capture_output=True,
                text=True,
                timeout=max(120, self.timeout_s),
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except subprocess.TimeoutExpired:
            return {"clean": False, "error": f"drc_gds timed out after {self.timeout_s}s"}

        last_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        try:
            return json.loads(last_line)
        except json.JSONDecodeError:
            return {
                "clean": False,
                "error": (
                    proc.stderr.strip()[-500:]
                    or last_line[-500:]
                    or f"drc subprocess exited {proc.returncode}"
                ),
            }

    def generate_ota_defaults(
        self,
        output_dir: str | Path,
    ) -> GLayoutResult:
        """Generate OTA layout with validated gLayout default params.

        Uses the known-good default parameters from gLayout's
        opamp_twostage() function, bypassing the deprecated
        sizing_to_glayout_params() mapping.
        """
        from eda_agents.topologies.ota_gf180 import GF180OTATopology

        return self.generate_component(
            component="opamp_twostage",
            params=GF180OTATopology.glayout_default_params(),
            output_dir=output_dir,
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
