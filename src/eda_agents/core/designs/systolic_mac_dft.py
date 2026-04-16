"""Systolic MAC with DFT digital design wrapper (stub).

Wraps Essenceia/Systolic_MAC_with_DFT — a 2x2 systolic MAC array
with JTAG DFT, targeting a Tiny Tapeout GF180MCU shuttle.

Phase 0 found this design requires LibreLane 2.4.2 via a devcontainer,
which is incompatible with fazyrv-hachure's leo/gf180mcu branch.
Full implementation is deferred to Phase 6 (task #8).

This stub provides enough structure for imports and dry-run tests
to work.
"""

from __future__ import annotations

import os
from pathlib import Path

from eda_agents.core.digital_design import DigitalDesign
from eda_agents.core.flow_metrics import FlowMetrics


_DEFAULT_DESIGNS_DIR = "/home/montanares/git"


class SystolicMacDftDesign(DigitalDesign):
    """Systolic MAC + DFT design (stub, deferred to Phase 6).

    All run-related methods raise ``NotImplementedError``.
    Metadata methods work for dry-run and prompt-generation tests.
    """

    def __init__(self, designs_dir: Path | str | None = None):
        if designs_dir is None:
            designs_dir = os.environ.get(
                "EDA_AGENTS_DIGITAL_DESIGNS_DIR", _DEFAULT_DESIGNS_DIR
            )
        self._designs_dir = Path(designs_dir)
        self._repo_dir = self._designs_dir / "Systolic_MAC_with_DFT"

    def project_name(self) -> str:
        return "systolic-mac-dft"

    def relevant_skills(self) -> list[str | tuple[str, dict]]:
        return ["digital.verification", "digital.synthesis"]

    def specification(self) -> str:
        return (
            "2x2 systolic MAC array with JTAG DFT, targeting Tiny Tapeout "
            "GF180MCU shuttle. INT8 multiply-accumulate with scan chain "
            "for manufacturing test."
        )

    def design_space(self) -> dict[str, list | tuple]:
        # Placeholder — will be populated from real runs in Phase 6
        return {
            "PL_TARGET_DENSITY_PCT": [50, 60, 70, 80],
            "CLOCK_PERIOD": [20, 25, 30, 40],
        }

    def flow_config_overrides(self) -> dict[str, object]:
        return {}

    def project_dir(self) -> Path:
        return self._repo_dir

    def librelane_config(self) -> Path:
        # Tiny Tapeout template uses a different config structure
        return self._repo_dir / "config.yaml"

    def compute_fom(self, metrics: FlowMetrics) -> float:
        valid, _ = self.check_validity(metrics)
        if not valid:
            return 0.0
        return metrics.weighted_fom(timing_w=1.0, area_w=0.5, power_w=0.3)

    def check_validity(self, metrics: FlowMetrics) -> tuple[bool, list[str]]:
        return metrics.validity_check()

    def pdk_config(self):
        from eda_agents.core.pdk import GF180MCU_D
        return GF180MCU_D

    # ------------------------------------------------------------------
    # Prompt metadata
    # ------------------------------------------------------------------

    def prompt_description(self) -> str:
        return (
            "Systolic MAC with DFT: 2x2 INT8 multiply-accumulate array "
            "with JTAG scan chain for manufacturing test. Targets Tiny "
            "Tapeout GF180MCU shuttle. Small design (<5 min hardening), "
            "intended as CI fixture."
        )

    def design_vars_description(self) -> str:
        return (
            "- PL_TARGET_DENSITY_PCT: [50, 60, 70, 80] — placement density\n"
            "- CLOCK_PERIOD: [20, 25, 30, 40] ns — clock period target"
        )

    def specs_description(self) -> str:
        return "WNS >= 0, DRC clean, LVS match"

    def fom_description(self) -> str:
        return (
            "FoM = 1.0 * WNS_worst_ns + 0.5 * (1e6/die_area_um2) + "
            "0.3 * (1/power_W). Same formula as fazyrv-hachure."
        )

    def reference_description(self) -> str:
        return (
            "Reference: not yet established (deferred to Phase 6). "
            "Expected: <5 min hardening, ~1k cells."
        )
