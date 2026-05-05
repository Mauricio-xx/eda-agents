"""Generic digital design wrapper derived from a LibreLane config file.

Auto-populates all ``DigitalDesign`` abstract methods by reading an
existing LibreLane config (YAML or JSON). Users point at a config file
and get a working design object without writing a Python class.

Usage::

    from eda_agents.core.designs.generic import GenericDesign

    design = GenericDesign(
        config_path="/path/to/project/config.yaml",
        pdk_root="/path/to/gf180mcu",
    )
    # All 13 abstract methods are auto-derived from the config.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from eda_agents.core.digital_design import DigitalDesign, TestbenchSpec
from eda_agents.core.flow_metrics import FlowMetrics
from eda_agents.core.pdk import PdkConfig, resolve_pdk

logger = logging.getLogger(__name__)


def _clamp(val: int | float, lo: int | float, hi: int | float):
    """Clamp a value to [lo, hi]."""
    return max(lo, min(hi, val))


class GenericDesign(DigitalDesign):
    """A DigitalDesign auto-derived from a LibreLane config file.

    Reads ``DESIGN_NAME``, ``CLOCK_PERIOD``, ``PL_TARGET_DENSITY_PCT``,
    ``VERILOG_FILES``, ``DIE_AREA``, etc. from the config and provides
    sensible defaults for everything else.

    Parameters
    ----------
    config_path : Path or str
        Path to a LibreLane config file (YAML or JSON).
    pdk_root : Path or str, optional
        Explicit PDK root. Required for GF180MCU flows (F5 rule).
    design_space_overrides : dict, optional
        Override the default design space. Keys are config knob names,
        values are lists (discrete) or tuples (min, max).
    fom_weights : dict, optional
        Override FoM weights. Keys: ``timing_w``, ``area_w``, ``power_w``.
        Defaults: 1.0, 0.5, 0.3.
    shell_wrapper : str, optional
        Explicit shell wrapper command. If ``None`` (default),
        auto-detects ``shell.nix`` or ``flake.nix`` in the project
        directory and sets ``"nix-shell <dir> --run"``.
    """

    def __init__(
        self,
        config_path: Path | str,
        pdk_root: Path | str | None = None,
        design_space_overrides: dict[str, list | tuple] | None = None,
        fom_weights: dict[str, float] | None = None,
        shell_wrapper: str | None = ...,  # sentinel: None = no wrapper
        pdk_config: PdkConfig | str | None = None,
    ):
        self._config_path = Path(config_path).resolve()
        self._pdk_root = Path(pdk_root) if pdk_root else None
        self._ds_overrides = design_space_overrides or {}
        # FoM weights for ``FlowMetrics.weighted_fom`` (PPA: Performance,
        # Power, Area). The defaults (0.5/1.0/1.0/1.0) match the
        # reference formula in ``flow_metrics.py``, which includes a
        # Performance term (achievable frequency) so that clock
        # relaxation alone cannot win the optimizer trivially.
        self._fom_w = {
            "timing_w": 0.5,
            "perf_w": 1.0,
            "area_w": 1.0,
            "power_w": 1.0,
            **(fom_weights or {}),
        }
        self._pdk = resolve_pdk(pdk_config)

        # Read and cache the config
        self._config = self._read_config()

        # Shell wrapper: auto-detect if sentinel (...), explicit otherwise
        if shell_wrapper is ...:
            self._shell_wrapper = self._detect_shell_wrapper()
        else:
            self._shell_wrapper = shell_wrapper

    def _read_config(self) -> dict:
        """Read the config file (JSON or YAML)."""
        if not self._config_path.is_file():
            logger.warning("Config not found: %s", self._config_path)
            return {}
        text = self._config_path.read_text()
        if self._config_path.suffix in (".yaml", ".yml"):
            return yaml.safe_load(text) or {}
        return json.loads(text)

    def _detect_shell_wrapper(self) -> str | None:
        """Auto-detect nix-shell if shell.nix or flake.nix exists."""
        # Check project dir and up to 2 ancestors
        check_dir = self._config_path.parent
        for _ in range(3):
            if (check_dir / "shell.nix").is_file() or (check_dir / "flake.nix").is_file():
                return f"nix-shell {check_dir} --run"
            parent = check_dir.parent
            if parent == check_dir:
                break
            check_dir = parent
        return None

    # ------------------------------------------------------------------
    # Core abstract methods
    # ------------------------------------------------------------------

    def project_name(self) -> str:
        name = self._config.get("DESIGN_NAME", "unknown")
        return str(name).replace("_", "-")

    def relevant_skills(self) -> list[str | tuple[str, dict]]:
        return ["digital.synthesis", "digital.physical"]

    def specification(self) -> str:
        name = self._config.get("DESIGN_NAME", "unknown")
        clock = self._config.get("CLOCK_PERIOD", "?")
        die = self._config.get("DIE_AREA", [])
        die_str = f"{die[2]:.0f}x{die[3]:.0f} um" if len(die) >= 4 else "auto-sized"
        vfiles = self._config.get("VERILOG_FILES", "")
        return (
            f"Digital design '{name}' targeting {self._pdk.display_name}. "
            f"Clock period: {clock} ns. Die area: {die_str}. "
            f"Verilog: {vfiles}."
        )

    def design_space(self) -> dict[str, list | tuple]:
        """Return the design space as continuous (lo, hi) tuples.

        The bounds reflect what LibreLane accepts (a tool-level fence,
        not an autoresearch policy cap). The optimizer sees these as
        the search range; it is free to land anywhere inside, including
        values that previously sat far outside the discrete grid we
        used to ship.

        Rationale (user direction): the LLM should have maximum
        freedom to experiment within tool-imposed limits. The only
        things the optimizer cannot change are the spec assertions
        (``check_validity``) and the FoM definition (``compute_fom``),
        both of which live in the ``DigitalDesign`` subclass and are
        out of band from ``design_space``. Anything else, including
        clock relaxation beyond 1.25x of baseline, is fair game.

        Bounds:

        * ``CLOCK_PERIOD``: ``(0.1, 10000.0)`` ns. The lower bound is
          a sanity floor (sub-100 ps clocks are unphysical at
          GF180MCU 5V0); the upper bound lets the LLM relax timing
          to ~100 kHz if it needs to (well past any reasonable
          design point). LibreLane accepts arbitrary positive floats
          here.
        * ``PL_TARGET_DENSITY_PCT``: ``(1.0, 99.0)``. Densities at
          the extremes are degenerate (1% leaves the placer with
          almost no constraints; 99% will fail legalization on any
          real design) but neither crashes the tool, so we let the
          LLM see them and learn from the failure feedback.

        Override via ``GenericDesign(design_space_overrides=...)``
        is still honoured for callers that want to pin a narrower
        range.
        """
        ds: dict[str, list | tuple] = {
            "PL_TARGET_DENSITY_PCT": (1.0, 99.0),
            "CLOCK_PERIOD": (0.1, 10000.0),
        }
        ds.update(self._ds_overrides)
        return ds

    def flow_config_overrides(self) -> dict[str, object]:
        return {}

    def project_dir(self) -> Path:
        return self._config_path.parent

    def librelane_config(self) -> Path:
        return self._config_path

    def compute_fom(
        self, measurements: dict[str, float | int | None]
    ) -> float:
        valid, _ = self.check_validity(measurements)
        if not valid:
            return 0.0
        metrics = FlowMetrics.from_measurements(measurements)
        return metrics.weighted_fom(
            timing_w=self._fom_w["timing_w"],
            perf_w=self._fom_w["perf_w"],
            area_w=self._fom_w["area_w"],
            power_w=self._fom_w["power_w"],
        )

    def check_validity(
        self, measurements: dict[str, float | int | None]
    ) -> tuple[bool, list[str]]:
        return FlowMetrics.from_measurements(measurements).validity_check()

    # ------------------------------------------------------------------
    # Optional overrides
    # ------------------------------------------------------------------

    def pdk_root(self) -> Path | None:
        return self._pdk_root

    def pdk_config(self) -> PdkConfig:
        return self._pdk

    def shell_wrapper(self) -> str | None:
        return self._shell_wrapper

    def rtl_sources(self) -> list[Path]:
        vfiles = self._config.get("VERILOG_FILES", "")
        if not vfiles:
            return []
        # Handle LibreLane dir:: prefix and glob patterns
        if isinstance(vfiles, str):
            vfiles = [vfiles]
        sources = []
        for vf in vfiles:
            vf = str(vf)
            if vf.startswith("dir::"):
                vf = vf[5:]
            # Resolve relative to project dir
            p = self._config_path.parent / vf
            sources.append(p)
        return sources

    def testbench(self) -> TestbenchSpec | None:
        """Auto-detect testbench in project directory.

        Looks for ``tb/tb_*.v`` or ``testbench/*.v`` patterns. Returns
        an iverilog-based :class:`TestbenchSpec` pointing at the first
        matching file, or ``None`` when none is found.

        The ``target`` is the project-relative path to the testbench
        file only — downstream runners (``GlSimRunner``, ``RtlSimRunner``)
        wire the RTL / post-synth netlist in separately from
        :meth:`rtl_sources`.
        """
        project = self._config_path.parent
        for pattern in ["tb/tb_*.v", "testbench/*.v", "tb/*.v"]:
            matches = sorted(project.glob(pattern))
            if matches:
                return TestbenchSpec(
                    driver="iverilog",
                    target=str(matches[0].relative_to(project)),
                    work_dir_relative=".",
                )
        return None

    def validate_clone(self) -> list[str]:
        problems: list[str] = []
        if not self._config_path.is_file():
            problems.append(f"Config not found: {self._config_path}")
        if not self._config_path.parent.is_dir():
            problems.append(f"Project dir not found: {self._config_path.parent}")
        if not self._config.get("DESIGN_NAME"):
            problems.append("Config missing DESIGN_NAME")
        return problems

    # ------------------------------------------------------------------
    # Prompt metadata
    # ------------------------------------------------------------------

    def prompt_description(self) -> str:
        name = self._config.get("DESIGN_NAME", "unknown")
        clock = self._config.get("CLOCK_PERIOD", "?")
        return (
            f"Digital design '{name}' on {self._pdk.display_name}, "
            f"clock period {clock} ns. "
            f"Config: {self._config_path}. "
            f"Auto-derived design object (GenericDesign) -- no Phase 0 "
            f"characterization available. Default tuning ranges used."
        )

    def baseline_params(self) -> dict[str, float | int]:
        """Read design-space baseline values from the cached config.

        The default :class:`DigitalDesign.baseline_params` re-parses
        the YAML/JSON on every call; ``GenericDesign`` already
        cached the config at ``__init__`` so this override skips the
        I/O.
        """
        out: dict[str, float | int] = {}
        for key, values in self.design_space().items():
            if key not in self._config:
                continue
            raw = self._config[key]
            if isinstance(values, tuple):
                try:
                    out[key] = float(raw)
                except (TypeError, ValueError):
                    continue
            else:
                out[key] = raw
        return out

    def design_vars_description(self) -> str:
        """Describe each tunable knob with its baseline + valid range.

        The baseline (read from ``self._config`` via
        :meth:`baseline_params`) is the project's current value; the
        range is the design-space tuple/list. The text is intentionally
        plain prose — the LLM's "DESIGN-SPACE POLICY" cue lives in the
        runner's system prompt and gives the directional guidance, so
        this method only conveys the data.
        """
        ds = self.design_space()
        baseline = self.baseline_params()
        lines: list[str] = []
        for key, values in ds.items():
            b = baseline.get(key)
            b_str = (
                f"baseline {b}" if b is not None else "no baseline in config"
            )
            if isinstance(values, list):
                lines.append(f"- {key}: {b_str}; choose from {values}.")
            else:
                lo, hi = values
                lines.append(
                    f"- {key}: {b_str}; valid range [{lo}, {hi}]. "
                    f"Explore freely; bounds are a tool-level fence, not "
                    f"a recommended target."
                )
        return "\n".join(lines) if lines else "(none)"

    def specs_description(self) -> str:
        return "WNS >= 0 at all corners, DRC clean, LVS match"

    def fom_description(self) -> str:
        tw = self._fom_w["timing_w"]
        pw_perf = self._fom_w["perf_w"]
        aw = self._fom_w["area_w"]
        pw = self._fom_w["power_w"]
        return (
            f"FoM (PPA: Performance, Power, Area) = "
            f"{tw} * timing_met"  # binary 0/1; not a slack-stuffer
            f" + {pw_perf} * (1000 / clock_period_ns)"  # MHz achievable
            f" + {aw} * (1e6 / die_area_um2)"           # smaller area better
            f" + {pw} * (1 / (power_W * clock_period_ns))"  # 1/(nJ per cycle)
            f". Higher is better. Returns 0.0 for designs that fail "
            f"timing/DRC/LVS. The Performance term (frequency in MHz) "
            f"prevents trivial wins by clock relaxation; the energy/cycle "
            f"term replaces 1/power so that slowing down the clock does "
            f"not pretend to be a power optimization (P scales linearly "
            f"with f in CMOS, so P*period is roughly clock-invariant)."
        )

    def reference_description(self) -> str:
        return (
            "No reference run established. The first successful flow "
            "run will serve as the baseline."
        )
