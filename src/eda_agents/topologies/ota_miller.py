"""Miller OTA topology wrapper for SPICE-in-the-loop experiments.

Adapts the existing MillerOTADesigner as a CircuitTopology, delegating
analytical design and netlist generation to the proven implementation
while conforming to the common topology interface.
"""

from __future__ import annotations

from pathlib import Path

from eda_agents.core.pdk import PdkConfig, resolve_pdk
from eda_agents.topologies.miller_ota import DesignResult, MillerOTADesigner
from eda_agents.core.topology import CircuitTopology
from eda_agents.core.spice_runner import SpiceResult

# Spec targets (matching MillerOTASpecs defaults)
_SPEC_ADC_DB = 50.0
_SPEC_GBW_HZ = 1e6
_SPEC_PM_DEG = 60.0


class MillerOTATopology(CircuitTopology):
    """Miller OTA (NMOS input) topology wrapper.

    Delegates to MillerOTADesigner for analytical sizing and netlist
    generation. Design space matches the experiment harness conventions
    (units: S/A, um, pF, uA).

    Parameters
    ----------
    pdk : PdkConfig or str, optional
        PDK configuration. Defaults to resolve_pdk().
    """

    def __init__(self, pdk: PdkConfig | str | None = None):
        self.pdk = resolve_pdk(pdk)
        self.designer = MillerOTADesigner(pdk=self.pdk)
        # Cache the last DesignResult for netlist generation
        self._last_result: DesignResult | None = None

    def topology_name(self) -> str:
        return "miller_ota"

    def relevant_skills(self) -> list[str | tuple[str, dict]]:
        return ["analog.gmid_sizing"]

    def design_space(self) -> dict[str, tuple[float, float]]:
        return {
            "gmid_input": (5.0, 25.0),
            "gmid_load": (5.0, 20.0),
            "L_input_um": (0.13, 2.0),
            "L_load_um": (0.13, 2.0),
            "Cc_pF": (0.1, 5.0),
            "Ibias_uA": (0.5, 50.0),
        }

    def default_params(self) -> dict[str, float]:
        """Nominal design point from Phase 10.1 experiments."""
        return {
            "gmid_input": 12.0,
            "gmid_load": 10.0,
            "L_input_um": 0.5,
            "L_load_um": 0.5,
            "Cc_pF": 0.5,
            "Ibias_uA": 10.0,
        }

    # ------------------------------------------------------------------
    # Prompt metadata
    # ------------------------------------------------------------------

    def prompt_description(self) -> str:
        return (
            f"Miller OTA on {self.pdk.display_name}. "
            "NMOS-input diff pair with PMOS current mirror load "
            "and PMOS common-source second stage with Miller compensation."
        )

    def design_vars_description(self) -> str:
        return (
            "- gmid_input: gm/ID of input pair [5-25 S/A]. "
            "Moderate inversion (~10-15) balances gain and speed.\n"
            "- gmid_load: gm/ID of load [5-20 S/A]. "
            "Lower values give more gain but cost area.\n"
            "- L_input_um: input pair channel length [0.13-2.0 um]. "
            "Longer = more gain, more area.\n"
            "- L_load_um: load channel length [0.13-2.0 um].\n"
            "- Cc_pF: compensation capacitor [0.1-5.0 pF]. "
            "Larger Cc improves phase margin but reduces GBW.\n"
            "- Ibias_uA: first-stage bias current per branch [0.5-50.0 uA]. "
            "More current = higher GBW but more power. "
            "Main knob for gain-bandwidth vs power tradeoff."
        )

    def specs_description(self) -> str:
        return (
            f"Adc >= {_SPEC_ADC_DB:.0f} dB, "
            f"GBW >= {_SPEC_GBW_HZ/1e6:.0f} MHz, "
            f"PM >= {_SPEC_PM_DEG:.0f} deg"
        )

    def fom_description(self) -> str:
        return (
            "FoM = Gain * GBW / (Power * Area). "
            "Higher FoM is better. Designs violating specs get "
            "quadratically penalized -- balance performance against "
            "power and area."
        )

    def reference_description(self) -> str:
        return (
            "Reference: gmid_input=12, gmid_load=10, L_input=0.5um, "
            "L_load=0.5um, Cc=0.5pF, Ibias=10uA."
        )

    def tool_spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "simulate_ota",
                "description": (
                    f"Run SPICE simulation (ngspice PSP103) for a {self.prompt_description()} "
                    f"Returns SPICE-validated gain, GBW, phase margin, and FoM. "
                    f"Specs: {self.specs_description()}. "
                    "IMPORTANT: SPICE takes ~10s per eval and budget is limited. "
                    f"{self.fom_description()} "
                    f"{self.reference_description()}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "gmid_input": {
                            "type": "number",
                            "description": "gm/ID of input pair [5-25 S/A]",
                        },
                        "gmid_load": {
                            "type": "number",
                            "description": "gm/ID of load [5-20 S/A]",
                        },
                        "L_input_um": {
                            "type": "number",
                            "description": "Input pair channel length [0.13-2.0 um]",
                        },
                        "L_load_um": {
                            "type": "number",
                            "description": "Load channel length [0.13-2.0 um]",
                        },
                        "Cc_pF": {
                            "type": "number",
                            "description": "Compensation capacitor [0.1-5.0 pF]",
                        },
                        "Ibias_uA": {
                            "type": "number",
                            "description": "First-stage bias current per branch [0.5-50.0 uA]",
                        },
                    },
                    "required": ["gmid_input", "gmid_load", "L_input_um", "L_load_um", "Cc_pF", "Ibias_uA"],
                },
            },
        }

    def params_to_sizing(self, params: dict[str, float]) -> dict[str, dict]:
        """Run analytical design and return transistor sizing.

        Returns dict with device names -> {W, L} in SI units,
        plus metadata keys prefixed with '_'.
        """
        result = self.designer.analytical_design(
            gmid_input=params["gmid_input"],
            gmid_load=params["gmid_load"],
            L_input=params["L_input_um"] * 1e-6,
            L_load=params["L_load_um"] * 1e-6,
            Cc=params["Cc_pF"] * 1e-12,
            Ibias=params["Ibias_uA"] * 1e-6,
        )

        # Cache for generate_netlist
        self._last_result = result

        sizing = {}
        for name, t in result.transistors.items():
            sizing[name] = {
                "W": t.W,
                "L": t.L,
                "type": t.mos_type,
            }

        # Metadata for FoM computation
        sizing["_analytical"] = {
            "Adc_dB": result.Adc_dB,
            "GBW_Hz": result.GBW,
            "PM_deg": result.PM,
            "power_uW": result.power_uW,
            "area_um2": result.area_um2,
            "FoM": result.FoM,
            "valid": result.valid,
            "violations": result.violations,
        }

        return sizing

    def generate_netlist(
        self, sizing: dict[str, dict], work_dir: Path
    ) -> Path:
        """Generate Miller OTA netlist using MillerOTADesigner.

        Requires params_to_sizing() to have been called first (uses
        cached DesignResult for netlist generation).
        """
        if self._last_result is None:
            msg = "Call params_to_sizing() before generate_netlist()"
            raise RuntimeError(msg)

        return self.designer.generate_netlist(self._last_result, work_dir)

    def compute_fom(
        self, spice_result: SpiceResult, sizing: dict[str, dict]
    ) -> float:
        """FoM using SPICE results with analytical power/area estimates.

        Uses same formula as DesignResult.FoM but with SPICE Adc and GBW.
        """
        if not spice_result.success:
            return 0.0

        adc_dB = spice_result.Adc_dB
        gbw_hz = spice_result.GBW_Hz
        if adc_dB is None or gbw_hz is None:
            return 0.0

        # Get power and area from analytical estimate
        ana = sizing.get("_analytical", {})
        power_uW = ana.get("power_uW", 0)
        area_um2 = ana.get("area_um2", 0)
        if power_uW <= 0 or area_um2 <= 0:
            return 0.0

        # Convert to SI
        power_w = power_uW * 1e-6
        area_m2 = area_um2 * 1e-12

        adc_linear = 10 ** (adc_dB / 20)
        raw_fom = adc_linear * gbw_hz / (power_w * area_m2)

        # Apply spec penalty
        valid, violations = self.check_validity(spice_result)
        penalty = 1.0 if valid else max(0.01, 1.0 - 0.2 * len(violations))

        return raw_fom * penalty

    def check_validity(
        self, spice_result: SpiceResult, sizing: dict | None = None
    ) -> tuple[bool, list[str]]:
        """Check against Miller OTA design specs."""
        violations: list[str] = []

        if not spice_result.success:
            return (False, ["simulation failed"])

        if spice_result.Adc_dB is not None and spice_result.Adc_dB < _SPEC_ADC_DB:
            violations.append(
                f"Adc={spice_result.Adc_dB:.1f}dB < {_SPEC_ADC_DB}dB"
            )

        if spice_result.GBW_Hz is not None and spice_result.GBW_Hz < _SPEC_GBW_HZ:
            violations.append(
                f"GBW={spice_result.GBW_Hz/1e6:.3f}MHz < {_SPEC_GBW_HZ/1e6:.1f}MHz"
            )

        if spice_result.PM_deg is not None and spice_result.PM_deg < _SPEC_PM_DEG:
            violations.append(
                f"PM={spice_result.PM_deg:.1f}deg < {_SPEC_PM_DEG}deg"
            )

        return (len(violations) == 0, violations)
