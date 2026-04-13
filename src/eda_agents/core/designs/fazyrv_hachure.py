"""FazyRV-Hachure digital design wrapper.

Wraps meiniKi/gf180mcu-fazyrv-hachure — a 7-variant bit-serial RISC-V
SoC with Wishbone bus, UART, SPI, Timer, and GPIO peripherals.
GF180MCU 7T5V0 standard cells, hardened via LibreLane v3 on the
leo/gf180mcu branch.

Design space values come from Phase 0 observations
(``docs/digital_flow_field_notes.md`` sections 5 and 6), not from
guessing.  Only knobs whose effect was measured are exposed.

Phase 0 reference runs:
- frv_1 macro: 267s, 12,201 cells, WNS +1.41 ns (worst), +19.57 ns (tt)
- chip-top: 3h16m, 264k cells, WNS +5.96 ns (worst), 20.1 mm2 die
"""

from __future__ import annotations

import os
from pathlib import Path

from eda_agents.core.digital_design import DigitalDesign, TestbenchSpec
from eda_agents.core.flow_metrics import FlowMetrics


_DEFAULT_DESIGNS_DIR = "/home/montanares/git"

# Phase 0 reference: frv_1 macro at PL_TARGET_DENSITY_PCT=65, CLOCK_PERIOD=40
_REFERENCE_METRICS = {
    "synth_cell_count": 12_201,
    "wns_worst_ns": 1.407,
    "wns_tt_ns": 19.566,
    "power_total_mw": 51.85,
    "die_area_um2": 256_175,
    "wire_length_um": 155_900,
}


class FazyRvHachureDesign(DigitalDesign):
    """fazyrv-hachure GF180MCU SoC design.

    Parameters
    ----------
    designs_dir : Path or str, optional
        Parent directory containing ``gf180mcu-fazyrv-hachure/``.
        Defaults to ``$EDA_AGENTS_DIGITAL_DESIGNS_DIR`` or
        ``/home/montanares/git``.
    macro : str
        Which macro subdirectory to target.  Default: ``"frv_1"``
        (smallest, fastest feedback).  Set to ``""`` for chip-top.
    """

    def __init__(
        self,
        designs_dir: Path | str | None = None,
        macro: str = "frv_1",
    ):
        if designs_dir is None:
            designs_dir = os.environ.get(
                "EDA_AGENTS_DIGITAL_DESIGNS_DIR", _DEFAULT_DESIGNS_DIR
            )
        self._designs_dir = Path(designs_dir)
        self._macro = macro
        self._repo_dir = self._designs_dir / "gf180mcu-fazyrv-hachure"

    def project_name(self) -> str:
        if self._macro:
            return f"fazyrv-hachure-{self._macro}"
        return "fazyrv-hachure-chip"

    def specification(self) -> str:
        return (
            "FazyRV-Hachure: 7-variant bit-serial RISC-V SoC (RV32I) "
            "on GF180MCU 7T5V0. Wishbone interconnect with UART, SPI, "
            "Timer, GPIO. Variants differ in datapath width (1/2/4/8 bits) "
            "and optional BRAM/CCX. Chip-top includes padring (slot_1x1: "
            "3.9x5.1 mm die), 7 hardened macros, 20 SRAM instances.\n\n"
            "Target: timing closure at 40 ns clock (25 MHz), DRC clean, "
            "LVS match, precheck pass for wafer-space GF180MCU shuttle."
        )

    def design_space(self) -> dict[str, list | tuple]:
        # Phase 0 observed ranges (field notes sections 6.1, 6.2).
        # Discrete lists because response is non-monotonic.
        return {
            "PL_TARGET_DENSITY_PCT": [45, 55, 65, 75, 85],
            "CLOCK_PERIOD": [35, 40, 45, 50],
        }

    def flow_config_overrides(self) -> dict[str, object]:
        # Design-specific values that must always be set.
        # These come from the upstream config, not from exploration.
        return {}

    def project_dir(self) -> Path:
        if self._macro:
            return self._repo_dir / "macros" / self._macro
        return self._repo_dir / "librelane"

    def librelane_config(self) -> Path:
        return self.project_dir() / "config.yaml"

    def pdk_root(self) -> Path | None:
        # fazyrv clones its own PDK at tag 1.6.4 via `make clone-pdk`
        pdk_path = self._repo_dir / "gf180mcu"
        if pdk_path.is_dir():
            return pdk_path
        return None

    def shell_wrapper(self) -> str | None:
        # fazyrv's LibreLane (v3.0.0.dev45, leo/gf180mcu branch) lives
        # inside the project's nix-shell.  The wrapper invokes commands
        # through nix-shell --run so the correct Python + tools are used.
        shell_nix = self._repo_dir / "shell.nix"
        flake_nix = self._repo_dir / "flake.nix"
        if shell_nix.is_file() or flake_nix.is_file():
            return f"nix-shell {self._repo_dir} --run"
        return None

    def flow_type(self):
        return "Chip" if not self._macro else "Classic"

    def compute_fom(self, metrics: FlowMetrics) -> float:
        """Weighted FoM: timing (1.0) + area (0.5) + power (0.3).

        Returns 0.0 for invalid designs (negative WNS).
        """
        valid, _ = self.check_validity(metrics)
        if not valid:
            return 0.0
        return metrics.weighted_fom(timing_w=1.0, area_w=0.5, power_w=0.3)

    def check_validity(self, metrics: FlowMetrics) -> tuple[bool, list[str]]:
        """Design is valid iff timing is closed and DRC is clean."""
        return metrics.validity_check()

    def testbench(self) -> TestbenchSpec | None:
        # Sim runs from repo root (Makefile `sim:` target does `cd cocotb;`)
        # project_dir() -> macros/frv_1/ or librelane/, so navigate up.
        if self._macro:
            work_dir = "../.."  # macros/<name>/ -> repo root
        else:
            work_dir = ".."    # librelane/ -> repo root
        return TestbenchSpec(
            driver="cocotb",
            target="make sim",
            env_overrides={
                "PDK_ROOT": str(self.pdk_root() or ""),
                "PDK": "gf180mcuD",
            },
            work_dir_relative=work_dir,
        )

    def validate_clone(self) -> list[str]:
        """Check that the design repo is cloned and accessible."""
        problems: list[str] = []
        if not self._repo_dir.is_dir():
            problems.append(
                f"Design repo not found: {self._repo_dir}. "
                f"Run scripts/fetch_digital_designs.sh or set "
                f"EDA_AGENTS_DIGITAL_DESIGNS_DIR."
            )
            return problems

        config = self.librelane_config()
        if not config.is_file():
            problems.append(f"LibreLane config not found: {config}")

        return problems

    # ------------------------------------------------------------------
    # Prompt metadata
    # ------------------------------------------------------------------

    def prompt_description(self) -> str:
        return (
            "FazyRV-Hachure is a 7-variant bit-serial RISC-V SoC (RV32I) "
            "targeting the GF180MCU 7T5V0 process. It uses a Wishbone bus "
            "with UART, SPI, Timer, and GPIO peripherals. The design is "
            "hardened via LibreLane v3 (leo/gf180mcu branch). Macro "
            f"'{self._macro or 'chip-top'}' is the current target. "
            "The flow is perfectly deterministic (0.00% run-to-run variance)."
        )

    def design_vars_description(self) -> str:
        lines = [
            "- PL_TARGET_DENSITY_PCT: [45, 55, 65, 75, 85] — placement "
            "density target. NON-MONOTONIC timing response: d=55 is worst, "
            "d=85 is best for timing. All values pass DRC.",
            "- CLOCK_PERIOD: [35, 40, 45, 50] ns — target clock period. "
            "Values below 35 ns fail timing at worst corner (max_ss). "
            "Power scales linearly with frequency.",
        ]
        return "\n".join(lines)

    def specs_description(self) -> str:
        return (
            "WNS >= 0 at all corners (timing closed), DRC clean "
            "(Magic + KLayout), LVS match, antenna violations = 0"
        )

    def fom_description(self) -> str:
        return (
            "FoM = 1.0 * WNS_worst_ns + 0.5 * (1e6/die_area_um2) + "
            "0.3 * (1/power_W). Higher is better. Returns 0.0 for "
            "designs that fail timing."
        )

    def reference_description(self) -> str:
        return (
            f"Reference: frv_1 macro at PL_TARGET_DENSITY_PCT=65, "
            f"CLOCK_PERIOD=40.\n"
            f"  Cells: {_REFERENCE_METRICS['synth_cell_count']}, "
            f"WNS worst: +{_REFERENCE_METRICS['wns_worst_ns']:.3f} ns, "
            f"WNS tt: +{_REFERENCE_METRICS['wns_tt_ns']:.3f} ns\n"
            f"  Power: {_REFERENCE_METRICS['power_total_mw']:.2f} mW, "
            f"Area: {_REFERENCE_METRICS['die_area_um2']:,} um2, "
            f"Wire: {_REFERENCE_METRICS['wire_length_um']:,} um\n"
            f"  DRC: clean, LVS: match, Antenna: 0"
        )
