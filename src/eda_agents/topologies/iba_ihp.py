"""Inverter-Based Dynamic Amplifier (IBA) topology for IHP SG13G2.

Port of the IBA reference design from Wrøngm (Code-a-Chip VLSI26 #18,
Apache-2.0, Nithin P et al., 2025) into the eda-agents topology layer.

The IBA is a single CMOS inverter (NMOS pull-down + PMOS pull-up
sharing a drain output and a gate input) driving a capacitive load.
In Wrøngm's reference, the IBA sits inside a switched-capacitor
capacitive feedback loop with ``CL_eff = 667 fF``, ``T_settle = 250 ns``,
``UGB = 9.55 MHz``. The target ``Gm = 2*pi*UGB*CL = 40 uS`` and the
total quiescent current is ``Iq <= 5 uA`` (Wrøngm sized 2.5 uA).

For the eda-agents harness we instantiate the inverter open-loop, drive
the input at a designer-chosen ``Vbias`` (in silicon this comes from
a replica), and run a small AC sweep to extract ``Adc``, ``GBW``, and
``PM``. The settling validation belongs to a downstream transient
testbench (out of scope for this first-port commit); the open-loop
metrics are sufficient to score the Ron/gm methodology in autoresearch
A/B comparisons.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from eda_agents.core.pdk import (
    PdkConfig,
    netlist_lib_lines,
    netlist_osdi_lines,
    resolve_pdk,
)
from eda_agents.core.spice_runner import SpiceResult
from eda_agents.core.topology import CircuitTopology

logger = logging.getLogger(__name__)

# Wrøngm IBA spec (Table II, design example):
#   T_settle = 250 ns, UGB = 9.55 MHz, CL_eff = 667 fF
#   Gm_target = 2*pi*UGB*CL ~ 40 uS
# The constants below are the IHP defaults. They live as class
# attributes so PDK siblings (iba_gf180) can override without
# touching the deck-emission code.


class InverterBasedAmplifier(CircuitTopology):
    """Inverter-Based Dynamic Amplifier on IHP SG13G2 (or any PDK in
    the eda-agents registry that exposes ``lv_nmos`` / ``lv_pmos``-like
    primitives).

    Design space:

    - ``W_n_um``: NMOS unit-cell width [0.13, 5.0] um.
    - ``L_n_um``: NMOS unit-cell length [0.13, 4.0] um.
    - ``m_n``: NMOS multiplier (integer 1..8 in practice; treated as
      continuous for the autoresearch sampler).
    - ``W_p_um``: PMOS unit-cell width [0.13, 10.0] um (PMOS typically
      wider to match NMOS gm).
    - ``L_p_um``: PMOS unit-cell length [0.13, 4.0] um.
    - ``m_p``: PMOS multiplier.
    - ``Vbias_V``: input gate bias [0.4, 1.0] V (the trip-point voltage
      delivered by the replica in real silicon).

    Parameters
    ----------
    pdk : PdkConfig or str, optional
        PDK configuration. Defaults to ``resolve_pdk()`` -- IHP SG13G2
        is the reference target; the methodology is PDK-agnostic and
        the same topology re-targets to GF180 by changing the PDK.
    """

    SPEC_ADC_DB = 20.0      # Single-stage CMOS inverter; modest open-loop gain.
    SPEC_GBW_HZ = 9.55e6    # Wrøngm's UGB target.
    SPEC_PM_DEG = 60.0      # Cap-feedback loop PM; one-pole inverter clears.
    SPEC_IQ_UA = 5.0        # Total quiescent current budget; Wrøngm reports 2.5 uA.
    CL_F = 667e-15          # Load capacitance.

    def __init__(self, pdk: PdkConfig | str | None = None):
        self.pdk = resolve_pdk(pdk)
        # Stash the last computed sizing for netlist generation, mirroring
        # MillerOTATopology's _last_result convention.
        self._last_sizing: dict[str, dict] | None = None

    def topology_name(self) -> str:
        return "iba_ihp"

    def relevant_skills(self) -> list[str | tuple[str, dict]]:
        return ["analog.ron_gm_sizing", "analog.gmid_sizing"]

    def forbidden_insight_patterns(self) -> list[re.Pattern]:
        """Methodology-specific anti-patterns for autoresearch insights.

        These keep the explorer from regressing the IBA into the design
        traps the Ron/gm methodology was meant to surface. The patterns
        are topology-scoped so they do not contaminate the Miller OTA
        or AnalogAcademy loops.
        """
        return [
            re.compile(r"ignore\s+R_?on", re.IGNORECASE),
            re.compile(r"use\s+(an\s+)?ideal\s+current\s+source", re.IGNORECASE),
            re.compile(r"only\s+use\s+gm/ID", re.IGNORECASE),
            re.compile(r"large.?signal\s+phase.*can\s+be\s+ignored", re.IGNORECASE),
        ]

    def design_space(self) -> dict[str, tuple[float, float]]:
        return {
            "W_n_um": (0.13, 5.0),
            "L_n_um": (0.13, 4.0),
            "m_n": (1.0, 8.0),
            "W_p_um": (0.13, 10.0),
            "L_p_um": (0.13, 4.0),
            "m_p": (1.0, 8.0),
            "Vbias_V": (0.4, 1.0),
        }

    def default_params(self) -> dict[str, float]:
        """Wrøngm Ron/gm-sized IBA design point (Table II, gm/ID baseline).

        Their gm/ID sizing: NMOS W=0.3 um, L=3 um, m=4; PMOS W=0.15 um,
        L=0.25 um, m=4; Vbias ~ trip point. We start from that point so
        the autoresearch loop has a known-good seed.
        """
        return {
            "W_n_um": 0.3,
            "L_n_um": 3.0,
            "m_n": 4.0,
            "W_p_um": 0.15,
            "L_p_um": 0.25,
            "m_p": 4.0,
            "Vbias_V": 0.65,
        }

    def exploration_hints(self) -> dict[str, int | float]:
        # IBA has a 7-dim space, two device polarities, with a strong
        # Vbias-vs-trip-point interaction. Give the explorer some extra
        # rounds before declaring convergence.
        return {
            "evals_per_round": 6,
            "min_rounds": 4,
            "convergence_threshold": 0.02,
            "partition_dim": "Vbias_V",
        }

    # ------------------------------------------------------------------
    # Prompt metadata
    # ------------------------------------------------------------------

    def prompt_description(self) -> str:
        return (
            f"Inverter-Based Dynamic Amplifier (IBA) on {self.pdk.display_name}. "
            "A single CMOS inverter (NMOS+PMOS) drives a capacitive load "
            f"CL={self.CL_F*1e15:.0f} fF; the input gate is driven by a replica "
            "bias network at Vbias. Two-phase settling: large-signal RC "
            "phase governed by on-state Ron, small-signal exponential "
            "phase governed by gm. The Ron/gm methodology pre-characterises "
            "both phases so the design point is readable at design entry."
        )

    def design_vars_description(self) -> str:
        return (
            "- W_n_um: NMOS unit-cell width [0.13-5.0 um].\n"
            "- L_n_um: NMOS unit-cell length [0.13-4.0 um]. Longer = larger "
            "Ron at the on-state but lower gm/W.\n"
            "- m_n: NMOS multiplier [1-8]. Effective Wn_total = W_n * m_n.\n"
            "- W_p_um: PMOS unit-cell width [0.13-10.0 um]. Typically wider "
            "than NMOS to match the inverter's pull-up to pull-down gm.\n"
            "- L_p_um: PMOS unit-cell length [0.13-4.0 um].\n"
            "- m_p: PMOS multiplier [1-8].\n"
            "- Vbias_V: input gate bias [0.4-1.0 V]. Sets the inverter "
            "trip point. The right Vbias gives Iq = Iq_NMOS = Iq_PMOS "
            "(NMOS and PMOS conduct the same current). Off-trip-point "
            "values collapse Adc."
        )

    def specs_description(self) -> str:
        return (
            f"Adc >= {self.SPEC_ADC_DB:.0f} dB, "
            f"GBW >= {self.SPEC_GBW_HZ/1e6:.2f} MHz, "
            f"PM >= {self.SPEC_PM_DEG:.0f} deg, "
            f"Iq <= {self.SPEC_IQ_UA:.1f} uA"
        )

    def fom_description(self) -> str:
        return (
            "FoM = Adc_linear * GBW / (Iq * total_area). "
            "Higher FoM is better. Designs violating specs get a "
            "quadratic penalty proportional to the violation count."
        )

    def reference_description(self) -> str:
        return (
            "Reference (Wrøngm Table II, gm/ID-sized IBA): NMOS W=0.3 um "
            "L=3 um m=4; PMOS W=0.15 um L=0.25 um m=4; Vbias=0.65 V. "
            "Expected: Adc ~ 25 dB, GBW ~ 10 MHz at CL=667 fF, Iq ~ 2.5 uA."
        )

    def tool_spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "simulate_iba",
                "description": (
                    f"Run SPICE simulation (ngspice PSP103) for an IBA on "
                    f"{self.pdk.display_name}. Returns SPICE-validated Adc, "
                    "GBW, phase margin, and Iq. "
                    f"Specs: {self.specs_description()}. "
                    f"{self.fom_description()} {self.reference_description()}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "W_n_um": {"type": "number", "description": "NMOS unit width [0.13-5.0 um]"},
                        "L_n_um": {"type": "number", "description": "NMOS length [0.13-4.0 um]"},
                        "m_n": {"type": "number", "description": "NMOS multiplier [1-8]"},
                        "W_p_um": {"type": "number", "description": "PMOS unit width [0.13-10.0 um]"},
                        "L_p_um": {"type": "number", "description": "PMOS length [0.13-4.0 um]"},
                        "m_p": {"type": "number", "description": "PMOS multiplier [1-8]"},
                        "Vbias_V": {"type": "number", "description": "Input gate bias [0.4-1.0 V]"},
                    },
                    "required": list(self.design_space().keys()),
                },
            },
        }

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------

    def params_to_sizing(self, params: dict[str, float]) -> dict[str, dict]:
        """Map design-space params to transistor sizing.

        Multipliers are rounded to the nearest integer; widths are
        clamped to ``Wmin_m`` to keep the netlist valid even if the
        sampler drifts into out-of-PDK territory.
        """
        Wmin = self.pdk.Wmin_m
        Lmin = self.pdk.Lmin_m

        W_n = max(params["W_n_um"] * 1e-6, Wmin)
        L_n = max(params["L_n_um"] * 1e-6, Lmin)
        m_n = max(1, int(round(params["m_n"])))
        W_p = max(params["W_p_um"] * 1e-6, Wmin)
        L_p = max(params["L_p_um"] * 1e-6, Lmin)
        m_p = max(1, int(round(params["m_p"])))
        Vbias = float(params["Vbias_V"])

        sizing = {
            "M_N": {"W": W_n, "L": L_n, "m": m_n, "ng": 1, "type": "nmos"},
            "M_P": {"W": W_p, "L": L_p, "m": m_p, "ng": 1, "type": "pmos"},
            "_Vbias": Vbias,
            "_VDD": self.pdk.VDD,
            "_CL": self.CL_F,
        }
        self._last_sizing = sizing
        return sizing

    # ------------------------------------------------------------------
    # Netlist generation
    # ------------------------------------------------------------------

    def generate_netlist(
        self, sizing: dict[str, dict], work_dir: Path
    ) -> Path:
        """Write the IBA open-loop AC testbench deck.

        The deck:
          - Uses the active PDK's ``netlist_lib_lines`` so it works on
            both IHP SG13G2 and GF180.
          - Biases the input at ``Vbias`` (a design parameter), so the
            DC operating point is fully constrained.
          - Drives a 1V AC stimulus on top of the bias through a
            voltage-controlled voltage source (``Esum``) to keep the
            small-signal probe ideal.
          - Loads the output with ``CL = 667 fF`` (Wrøngm reference).
          - Measures ``Adc``, ``GBW``, ``PM``, and ``Iq``.
        """
        work_dir.mkdir(parents=True, exist_ok=True)

        m_n = sizing["M_N"]
        m_p = sizing["M_P"]
        Vbias = sizing["_Vbias"]
        VDD = sizing["_VDD"]
        CL = sizing["_CL"]

        prefix = self.pdk.instance_prefix
        n_dev = self.pdk.nmos_symbol
        p_dev = self.pdk.pmos_symbol

        def _devline(name: str, drain: str, gate: str, source: str, body: str,
                     dev: str, t: dict) -> str:
            # IHP and GF180 subcircuit primitives expose ``m`` internally
            # and warn ("m=xx on .subckt line will override multiplier m
            # hierarchy") when passed on the instance line. Absorb the
            # design multiplier into ``W`` so we only ship ``w/l/ng`` --
            # the resulting silicon is the same N parallel devices.
            W_total = t["W"] * t.get("m", 1)
            parts = [
                f"{prefix}{name}",
                drain, gate, source, body,
                dev,
                f"w={W_total:.6e}",
                f"l={t['L']:.6e}",
                f"{self.pdk.finger_param}={t.get('ng', 1)}",
            ]
            return " ".join(parts)

        lines: list[str] = [
            f"* IBA open-loop AC analysis - {self.pdk.display_name}",
            "",
            *netlist_lib_lines(self.pdk),
            "",
            "* Power and bias",
            f"VVDD VDD 0 {VDD:.4f}",
            f"VBIAS vbias 0 DC {Vbias:.4f}",
            "Vid id 0 DC=0 AC=1",
            "Esum vin vbias id 0 1",
            "",
            "* CMOS inverter (output stage of the IBA)",
            _devline("MN", "vout", "vin", "0", "0", n_dev, m_n),
            _devline("MP", "vout", "vin", "VDD", "VDD", p_dev, m_p),
            "",
            "* Load capacitance (Wrøngm reference CL = 667 fF)",
            f"CL vout 0 {CL:.4e}",
            "",
            ".control",
            "  set ngbehavior=hsa",
            *netlist_osdi_lines(self.pdk),
            "  op",
            # Iq from the DC operating point: ngspice records the current
            # through every voltage source in the op vector. We read it
            # before AC and stash via let so the print below picks it up.
            "  let Iq_dc = abs(-i(VVDD))",
            "  let Vout_op = v(vout)",
            "  print v(vout) v(vin) v(vbias) Iq_dc",
            "  ac dec 41 10 1e9",
            "  let AmagdB=vdb(vout)",
            "  let Aphdeg=180/PI*vp(vout)",
            "  meas ac Adc find AmagdB at=10",
            "  meas ac Adc_peak max AmagdB",
            "  meas ac GBW when AmagdB=0 cross=1",
            # PGBW = phase at the GBW point (Aphdeg there). SpiceRunner
            # routes the "pgbw" measurement label into PM_deg under the
            # inverting OTA convention -- inverter output is inverting,
            # so the same convention applies.
            "  meas ac PGBW find Aphdeg at=GBW",
            "  print Adc Adc_peak GBW PGBW",
            ".endc",
            ".end",
        ]

        cir_path = work_dir / "iba_ihp_open_loop.cir"
        cir_path.write_text("\n".join(lines) + "\n")
        return cir_path

    # ------------------------------------------------------------------
    # FoM + validity
    # ------------------------------------------------------------------

    def compute_fom(
        self, spice_result: SpiceResult, sizing: dict[str, dict]
    ) -> float:
        """``FoM = Adc_linear * GBW / (Iq * total_area)``.

        ``Iq`` is read from the SPICE measurement when available;
        otherwise estimated as ``VDD * 1 uA`` (a conservative floor so
        FoM stays comparable across simulations with broken Iq probes).
        """
        if not spice_result.success:
            return 0.0
        adc_dB = spice_result.Adc_dB
        gbw_hz = spice_result.GBW_Hz
        if adc_dB is None or gbw_hz is None:
            return 0.0

        m_n = sizing["M_N"]
        m_p = sizing["M_P"]
        total_area_m2 = (
            m_n["W"] * m_n["L"] * m_n.get("m", 1)
            + m_p["W"] * m_p["L"] * m_p.get("m", 1)
        )
        if total_area_m2 <= 0:
            return 0.0

        # Iq from the SPICE meas if present in the parsed extras dict.
        iq_a = (spice_result.measurements or {}).get("iq_dc")
        if iq_a is None or iq_a <= 0:
            iq_a = 1e-6  # 1 uA fallback so FoM stays defined.

        power_w = iq_a * self.pdk.VDD
        adc_linear = 10 ** (adc_dB / 20)
        raw_fom = adc_linear * gbw_hz / (power_w * total_area_m2)

        valid, violations = self.check_validity(spice_result, sizing)
        penalty = 1.0 if valid else max(0.01, 1.0 - 0.2 * len(violations))
        return raw_fom * penalty

    def check_validity(
        self, spice_result: SpiceResult, sizing: dict | None = None
    ) -> tuple[bool, list[str]]:
        """Validate against the Wrøngm IBA spec."""
        violations: list[str] = []
        if not spice_result.success:
            return (False, ["simulation failed"])

        if spice_result.Adc_dB is not None and spice_result.Adc_dB < self.SPEC_ADC_DB:
            violations.append(
                f"Adc={spice_result.Adc_dB:.1f}dB < {self.SPEC_ADC_DB}dB"
            )
        if spice_result.GBW_Hz is not None and spice_result.GBW_Hz < self.SPEC_GBW_HZ:
            violations.append(
                f"GBW={spice_result.GBW_Hz/1e6:.2f}MHz < "
                f"{self.SPEC_GBW_HZ/1e6:.2f}MHz"
            )
        if spice_result.PM_deg is not None and spice_result.PM_deg < self.SPEC_PM_DEG:
            violations.append(
                f"PM={spice_result.PM_deg:.1f}deg < {self.SPEC_PM_DEG}deg"
            )
        iq_a = (spice_result.measurements or {}).get("iq_dc")
        if iq_a is not None and iq_a > self.SPEC_IQ_UA * 1e-6:
            violations.append(
                f"Iq={iq_a*1e6:.2f}uA > {self.SPEC_IQ_UA}uA"
            )

        return (len(violations) == 0, violations)
