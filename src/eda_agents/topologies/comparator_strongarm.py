"""StrongARM dynamic comparator topology.

Wraps the IHP-AnalogAcademy dynamic comparator (module_3, part_1) as a
CircuitTopology.  This is a double-tail strongARM latch with PMOS input
pair, NMOS cross-coupled regeneration, and CMOS output inverter latch.

Reference schematic (12 transistors):
    M1/M2:  PMOS input diff pair    (W=32u, L=200n, ng=4)
    M3:     PMOS bias current src   (W=18u, L=300n, ng=4)
    M13:    PMOS clock tail switch  (W=18u, L=300n, ng=4)
    M4/M5:  PMOS output latch       (W=8u,  L=200n)
    M6/M8:  NMOS cross-coupled      (W=4u,  L=200n)
    M7/M10: NMOS reset switches     (W=4u,  L=200n)
    M11/M12:NMOS output latch       (W=4u,  L=200n)

Supports any PDK via PdkConfig (defaults to IHP SG13G2).
Source: IHP-AnalogAcademy/modules/module_3_8_bit_SAR_ADC/part_1_comparator/
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from eda_agents.core.pdk import PdkConfig, netlist_lib_lines, netlist_osdi_lines, resolve_pdk
from eda_agents.core.spice_runner import SpiceResult
from eda_agents.core.topology import CircuitTopology

logger = logging.getLogger(__name__)

# Default testbench parameters (PDK-independent)
_VIN_DIFF = 10e-3  # V (10 mV differential for characterization)
_FCLK = 100e6     # Hz (100 MHz clock)
_CIN_LOAD = 6.4e-12   # F (C-DAC load on inputs)
_COUT_LOAD = 50e-15    # F (output load)

# Spec targets for validity
_SPEC_TD_NS = 2.0       # ns max decision delay (ref is 1.84ns, barely passes)
_SPEC_VOUT_HIGH = 1.0   # V min for resolved high output
_SPEC_VOUT_LOW = 0.2    # V max for resolved low output
_SPEC_OFFSET_MV = 2.0   # mV max input-referred offset (1-sigma Pelgrom)

# Reference sizing from AnalogAcademy
_REF_SIZING = {
    "M1":  {"W": 32e-6,  "L": 200e-9, "ng": 4, "type": "pmos"},  # input pair
    "M2":  {"W": 32e-6,  "L": 200e-9, "ng": 4, "type": "pmos"},
    "M3":  {"W": 18e-6,  "L": 300e-9, "ng": 4, "type": "pmos"},  # bias src
    "M13": {"W": 18e-6,  "L": 300e-9, "ng": 4, "type": "pmos"},  # clk tail
    "M4":  {"W": 8e-6,   "L": 200e-9, "ng": 1, "type": "pmos"},  # latch inv
    "M5":  {"W": 8e-6,   "L": 200e-9, "ng": 1, "type": "pmos"},
    "M6":  {"W": 4e-6,   "L": 200e-9, "ng": 1, "type": "nmos"},  # cross-coupled
    "M7":  {"W": 4e-6,   "L": 200e-9, "ng": 1, "type": "nmos"},  # reset
    "M8":  {"W": 4e-6,   "L": 200e-9, "ng": 1, "type": "nmos"},  # cross-coupled
    "M10": {"W": 4e-6,   "L": 200e-9, "ng": 1, "type": "nmos"},  # reset
    "M11": {"W": 4e-6,   "L": 200e-9, "ng": 1, "type": "nmos"},  # latch inv
    "M12": {"W": 4e-6,   "L": 200e-9, "ng": 1, "type": "nmos"},
}


class StrongARMComparatorTopology(CircuitTopology):
    """StrongARM dynamic comparator.

    Design space parameterized by 6 variables controlling the four
    transistor groups: input pair, tail/bias, PMOS latch, NMOS latch.
    Latch channel lengths are fixed at L_min for maximum speed.

    Evaluation uses transient simulation: apply 10mV differential input,
    clock the comparator, measure decision delay, output swing, and power.

    Parameters
    ----------
    pdk : PdkConfig or str, optional
        PDK configuration. Defaults to resolve_pdk().
    """

    def __init__(self, pdk: PdkConfig | str | None = None):
        self.pdk = resolve_pdk(pdk)

    def topology_name(self) -> str:
        return "strongarm_comp"

    def relevant_skills(self) -> list[str | tuple[str, dict]]:
        return ["analog.gmid_sizing"]

    def design_space(self) -> dict[str, tuple[float, float]]:
        return {
            "W_input_um": (4.0, 64.0),      # Input pair width per finger
            "L_input_um": (0.13, 2.0),       # Input pair channel length
            "W_tail_um": (4.0, 40.0),        # Tail/bias PMOS width per finger
            "L_tail_um": (0.13, 2.0),        # Tail/bias channel length
            "W_latch_p_um": (1.0, 16.0),     # PMOS latch inverter width
            "W_latch_n_um": (1.0, 16.0),     # NMOS latch + cross-coupled + reset width
        }

    def default_params(self) -> dict[str, float]:
        """Reference design point from AnalogAcademy."""
        return {
            "W_input_um": 32.0,
            "L_input_um": 0.2,
            "W_tail_um": 18.0,
            "L_tail_um": 0.3,
            "W_latch_p_um": 8.0,
            "W_latch_n_um": 4.0,
        }

    # ------------------------------------------------------------------
    # Prompt metadata
    # ------------------------------------------------------------------

    def prompt_description(self) -> str:
        return (
            f"StrongARM dynamic comparator on {self.pdk.display_name}. "
            "Double-tail latch with PMOS input pair, NMOS cross-coupled "
            "regeneration, and CMOS output inverter latch. "
            "12 transistors total."
        )

    def design_vars_description(self) -> str:
        return (
            "- W_input_um: input pair PMOS width [4-64 um]. "
            "Larger = lower offset (sigma_Vos ~ 1/sqrt(W*L)), more input capacitance, "
            "slower due to parasitic load. KEY tradeoff variable.\n"
            "- L_input_um: input pair channel length [0.13-2.0 um]. "
            "Longer = better matching (lower offset) but slower. "
            "W*L product determines offset -- both W and L matter.\n"
            "- W_tail_um: tail/bias PMOS width [4-40 um]. "
            "Larger = more current, faster decision but more power.\n"
            "- L_tail_um: tail/bias channel length [0.13-2.0 um]. "
            "Longer = better current matching but slower turn-on.\n"
            "- W_latch_p_um: PMOS latch inverter width [1-16 um]. "
            "Larger = faster regeneration but more power and area.\n"
            "- W_latch_n_um: NMOS latch/cross-coupled/reset width [1-16 um]. "
            "Larger = faster regeneration and reset but more kickback noise."
        )

    def specs_description(self) -> str:
        return (
            f"td <= {_SPEC_TD_NS:.1f} ns (decision delay at 10mV diff input), "
            f"sigma_Vos <= {_SPEC_OFFSET_MV:.1f} mV (Pelgrom input-referred offset), "
            f"output resolves to valid logic levels "
            f"(high > {_SPEC_VOUT_HIGH:.1f}V, low < {_SPEC_VOUT_LOW:.1f}V)"
        )

    def fom_description(self) -> str:
        return (
            "FoM = 1 / (td * E_per_cycle * sigma_Vos). "
            "td = decision delay [s], E_per_cycle = energy per comparison [J], "
            "sigma_Vos = Pelgrom input-referred offset [V] = A_VT/sqrt(W_input*L_input). "
            "Higher FoM is better. Key tradeoff: small transistors are fast and "
            "low-power but have high offset; large transistors have low offset "
            "but are slower and consume more power."
        )

    def reference_description(self) -> str:
        return (
            "Reference: W_input=32um, L_input=0.2um, W_tail=18um, L_tail=0.3um, "
            "W_latch_p=8um, W_latch_n=4um -> td~1.7ns, Iavg~42uA, "
            "sigma_Vos~0.87mV (barely passes td spec of 2.0ns, excellent offset). "
            "Challenge: achieve td<=2.0ns while keeping sigma_Vos<=2.0mV."
        )

    def auxiliary_tools_description(self) -> str:
        """No auxiliary tools for comparator -- gmid_lookup not useful here."""
        return ""

    def exploration_hints(self) -> dict[str, int | float]:
        return {"partition_dim": "W_input_um"}

    def tool_spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "simulate_circuit",
                "description": (
                    f"Run SPICE transient simulation (ngspice PSP103) for a {self.prompt_description()} "
                    f"Returns decision delay, output levels, power, and FoM. "
                    f"Specs: {self.specs_description()}. "
                    "IMPORTANT: SPICE takes ~2-5s per eval and budget is limited. "
                    f"{self.fom_description()} "
                    f"{self.reference_description()}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "W_input_um": {
                            "type": "number",
                            "description": "Input pair PMOS width [4-64 um]. Affects offset, speed, capacitance.",
                        },
                        "L_input_um": {
                            "type": "number",
                            "description": "Input pair channel length [0.13-2.0 um]. Matching vs speed tradeoff.",
                        },
                        "W_tail_um": {
                            "type": "number",
                            "description": "Tail/bias PMOS width [4-40 um]. Controls current and speed.",
                        },
                        "L_tail_um": {
                            "type": "number",
                            "description": "Tail/bias channel length [0.13-2.0 um]. Current matching.",
                        },
                        "W_latch_p_um": {
                            "type": "number",
                            "description": "PMOS latch width [1-16 um]. Regeneration speed.",
                        },
                        "W_latch_n_um": {
                            "type": "number",
                            "description": "NMOS latch+cross-coupled+reset width [1-16 um]. Regeneration and reset speed.",
                        },
                    },
                    "required": [
                        "W_input_um", "L_input_um", "W_tail_um",
                        "L_tail_um", "W_latch_p_um", "W_latch_n_um",
                    ],
                },
            },
        }

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------

    def params_to_sizing(self, params: dict[str, float]) -> dict[str, dict]:
        """Convert design parameters to transistor sizing.

        Groups:
          - Input pair (M1/M2): W=W_input, L=L_input, ng derived from W
          - Bias source (M3): W=W_tail, L=L_tail, ng derived
          - Clock tail (M13): same as M3
          - PMOS latch (M4/M5): W=W_latch_p, L=L_min (200n)
          - NMOS cross-coupled (M6/M8): W=W_latch_n, L=L_min
          - NMOS reset (M7/M10): W=W_latch_n, L=L_min
          - NMOS output latch (M11/M12): W=W_latch_n, L=L_min
        """
        W_input = max(params["W_input_um"] * 1e-6, self.pdk.Wmin_m)
        L_input = max(params["L_input_um"] * 1e-6, self.pdk.Lmin_m)
        W_tail = max(params["W_tail_um"] * 1e-6, self.pdk.Wmin_m)
        L_tail = max(params["L_tail_um"] * 1e-6, self.pdk.Lmin_m)
        W_lp = max(params["W_latch_p_um"] * 1e-6, self.pdk.Wmin_m)
        W_ln = max(params["W_latch_n_um"] * 1e-6, self.pdk.Wmin_m)

        # Latch at ~1.5x Lmin for speed (200n for 130nm, 270n for 180nm)
        L_latch = max(200e-9, self.pdk.Lmin_m)

        # Derive ng: split into fingers if W > 10um
        ng_input = max(1, round(W_input / 10e-6))
        ng_tail = max(1, round(W_tail / 10e-6))

        sizing = {
            # Input pair
            "M1":  {"W": W_input, "L": L_input, "ng": ng_input, "type": "pmos"},
            "M2":  {"W": W_input, "L": L_input, "ng": ng_input, "type": "pmos"},
            # Bias and clock tail
            "M3":  {"W": W_tail,  "L": L_tail,  "ng": ng_tail, "type": "pmos"},
            "M13": {"W": W_tail,  "L": L_tail,  "ng": ng_tail, "type": "pmos"},
            # PMOS output latch
            "M4":  {"W": W_lp,    "L": L_latch, "ng": 1, "type": "pmos"},
            "M5":  {"W": W_lp,    "L": L_latch, "ng": 1, "type": "pmos"},
            # NMOS cross-coupled
            "M6":  {"W": W_ln,    "L": L_latch, "ng": 1, "type": "nmos"},
            "M8":  {"W": W_ln,    "L": L_latch, "ng": 1, "type": "nmos"},
            # NMOS reset
            "M7":  {"W": W_ln,    "L": L_latch, "ng": 1, "type": "nmos"},
            "M10": {"W": W_ln,    "L": L_latch, "ng": 1, "type": "nmos"},
            # NMOS output latch
            "M11": {"W": W_ln,    "L": L_latch, "ng": 1, "type": "nmos"},
            "M12": {"W": W_ln,    "L": L_latch, "ng": 1, "type": "nmos"},
            # Environment
            "_VDD": self.pdk.VDD,
            "_VBIAS": self.pdk.VDD / 2,
            "_VCM": self.pdk.VDD / 2,
            "_VIN_DIFF": _VIN_DIFF,
            "_FCLK": _FCLK,
            "_CIN_LOAD": _CIN_LOAD,
            "_COUT_LOAD": _COUT_LOAD,
        }

        return sizing

    # ------------------------------------------------------------------
    # Netlist generation
    # ------------------------------------------------------------------

    def generate_netlist(
        self, sizing: dict[str, dict], work_dir: Path
    ) -> Path:
        """Generate SPICE netlist for transient comparator characterization.

        Creates a flat netlist with testbench: applies 10mV differential
        input, clocks the comparator for 2 cycles, measures decision delay,
        output levels, and average supply current.

        Returns path to the .cir control file.
        """
        work_dir.mkdir(parents=True, exist_ok=True)

        VDD = sizing["_VDD"]
        VBIAS = sizing["_VBIAS"]
        VCM = sizing["_VCM"]
        VIN_DIFF = sizing["_VIN_DIFF"]
        FCLK = sizing["_FCLK"]
        CIN = sizing["_CIN_LOAD"]
        COUT = sizing["_COUT_LOAD"]

        T_period = 1.0 / FCLK
        T_half = T_period / 2.0

        # Input voltages
        vinp_v = VCM + VIN_DIFF / 2.0
        vinn_v = VCM - VIN_DIFF / 2.0

        # Junction area/perimeter helper
        z1 = self.pdk.z1_m

        def _junc(W: float) -> str:
            AS = W * z1
            PS = 2 * (W + z1)
            return f"AS={AS:.3e} PS={PS:.3e} AD={AS:.3e} PD={PS:.3e}"

        def _dev(name: str) -> str:
            d = sizing[name]
            return f"w={d['W']:.4e} l={d['L']:.4e} ng={d['ng']} m=1 {_junc(d['W'])}"

        # Device symbols from PDK
        pmos = self.pdk.pmos_symbol
        nmos = self.pdk.nmos_symbol
        px = self.pdk.instance_prefix

        m1 = sizing["M1"]
        m3 = sizing["M3"]
        m4 = sizing["M4"]
        m6 = sizing["M6"]

        lines = [
            f"StrongARM Dynamic Comparator - {self.pdk.display_name} Transient Analysis",
            "",
            *netlist_lib_lines(self.pdk),
            "",
            ".control",
            "  set ngbehavior=hsa",
            *netlist_osdi_lines(self.pdk),
            "",
            f"  tran 10p {2 * T_period:.4e}",
            "",
            "  * Decision delay: clk falling edge -> outn crosses 0.6V falling",
            "  * (outn is the losing output when Vin+ > Vin-)",
            "  meas tran td_decision TRIG v(clk) VAL=0.6 FALL=1 TARG v(outn) VAL=0.6 FALL=1",
            "",
            "  * Output levels at end of evaluation (90% of period)",
            f"  meas tran v_outp FIND v(outp) AT={0.9 * T_period:.4e}",
            f"  meas tran v_outn FIND v(outn) AT={0.9 * T_period:.4e}",
            "",
            "  * Average supply current over one full cycle",
            f"  meas tran avg_idd AVG i(VVDD) FROM=0 TO={T_period:.4e}",
            "",
            "  * Backup: also measure at second cycle for stability",
            f"  meas tran td_decision2 TRIG v(clk) VAL=0.6 FALL=2 TARG v(outn) VAL=0.6 FALL=2",
            "",
            ".endc",
            "",
            "* === Supply and bias ===",
            f"VVDD vdd 0 {VDD}",
            f"Vbias_src vbias 0 {VBIAS}",
            "",
            "* Clock: HIGH=reset, LOW=evaluate",
            f"Vclk clk 0 PULSE({VDD} 0 {T_half:.4e} 50p 50p {T_half - 100e-12:.4e} {T_period:.4e})",
            "",
            "* Differential input: centered at Vcm",
            f"Vinp vinp 0 {vinp_v}",
            f"Vinn vinn 0 {vinn_v}",
            "",
            "* Input C-DAC load capacitors",
            f"Cinp vinp 0 {CIN:.4e}",
            f"Cinn vinn 0 {CIN:.4e}",
            "",
            "* Output load",
            f"Coutp outp 0 {COUT:.4e}",
            f"Coutn outn 0 {COUT:.4e}",
            "",
            "* === Comparator Circuit ===",
            "",
            "* Bias current source PMOS (M3): gate=vbias",
            f"{px}M3  net2 vbias vdd  vdd {pmos} {_dev('M3')}",
            "",
            "* Clock tail switch PMOS (M13): gate=clk",
            f"{px}M13 net1 clk   net2 vdd {pmos} {_dev('M13')}",
            "",
            "* Input pair PMOS",
            f"{px}M2  net4 vinp  net1 vdd {pmos} {_dev('M2')}",
            f"{px}M1  net3 vinn  net1 vdd {pmos} {_dev('M1')}",
            "",
            "* PMOS output latch inverters",
            f"{px}M4  outn net3  vdd  vdd {pmos} {_dev('M4')}",
            f"{px}M5  outp net4  vdd  vdd {pmos} {_dev('M5')}",
            "",
            "* NMOS output latch inverters",
            f"{px}M11 0    net4  outp 0   {nmos} {_dev('M11')}",
            f"{px}M12 0    net3  outn 0   {nmos} {_dev('M12')}",
            "",
            "* NMOS cross-coupled (first-stage regeneration)",
            f"{px}M6  0    net3  net4 0   {nmos} {_dev('M6')}",
            f"{px}M8  0    net4  net3 0   {nmos} {_dev('M8')}",
            "",
            "* NMOS reset (pull net3/net4 to GND during reset)",
            f"{px}M7  0    clk   net3 0   {nmos} {_dev('M7')}",
            f"{px}M10 0    clk   net4 0   {nmos} {_dev('M10')}",
            "",
            ".end",
        ]

        cir_file = work_dir / "strongarm_comp.cir"
        cir_file.write_text("\n".join(lines) + "\n")
        return cir_file

    # ------------------------------------------------------------------
    # Offset estimation and FoM
    # ------------------------------------------------------------------

    def _sigma_vos(self, sizing: dict[str, dict]) -> float:
        """Estimate input-referred offset (1-sigma) via Pelgrom model.

        sigma_Vos = A_VT / sqrt(W_um * L_um) for the PMOS input pair.
        Returns offset in volts.
        """
        m1 = sizing["M1"]
        W_um = m1["W"] * 1e6
        L_um = m1["L"] * 1e6
        wl = W_um * L_um * m1.get("ng", 1)
        if wl <= 0:
            return 1.0  # worst case
        return self.pdk.AVT_pmos_Vum / math.sqrt(wl)

    def compute_fom(
        self, spice_result: SpiceResult, sizing: dict[str, dict]
    ) -> float:
        """FoM = 1 / (td * E_per_cycle * sigma_Vos).

        Returns 0.0 for failed or invalid simulations.
        Higher FoM is better (faster, less energy, lower offset).
        The offset term prevents trivial min-size solutions: smaller
        transistors are faster but have worse matching.
        """
        if not spice_result.success:
            return 0.0

        m = spice_result.measurements

        # Decision delay
        td = m.get("td_decision") or m.get("td_decision2")
        if td is None or td <= 0:
            return 0.0

        # Average supply current -> energy per cycle
        avg_idd = m.get("avg_idd")
        if avg_idd is None:
            return 0.0
        power_w = sizing["_VDD"] * abs(avg_idd)
        T_period = 1.0 / sizing["_FCLK"]
        energy_j = power_w * T_period

        if energy_j <= 0:
            return 0.0

        # Pelgrom offset of input pair
        sigma_vos = self._sigma_vos(sizing)

        raw_fom = 1.0 / (td * energy_j * sigma_vos)

        # Apply spec penalty
        valid, violations = self.check_validity(spice_result, sizing)
        penalty = 1.0 if valid else max(0.01, 1.0 - 0.2 * len(violations))

        return raw_fom * penalty

    def check_validity(
        self, spice_result: SpiceResult, sizing: dict[str, dict] | None = None
    ) -> tuple[bool, list[str]]:
        """Check comparator meets performance specs."""
        violations: list[str] = []

        if not spice_result.success:
            return (False, ["simulation failed"])

        m = spice_result.measurements

        # Decision delay
        td = m.get("td_decision") or m.get("td_decision2")
        if td is None:
            violations.append("td_decision: measurement failed (no resolution)")
        elif td * 1e9 > _SPEC_TD_NS:
            violations.append(
                f"td={td*1e9:.2f}ns > {_SPEC_TD_NS:.1f}ns"
            )

        # Output resolution: outp should be HIGH, outn should be LOW
        v_outp = m.get("v_outp")
        v_outn = m.get("v_outn")

        if v_outp is not None and v_outp < _SPEC_VOUT_HIGH:
            violations.append(
                f"v_outp={v_outp:.3f}V < {_SPEC_VOUT_HIGH:.1f}V (not resolved high)"
            )

        if v_outn is not None and v_outn > _SPEC_VOUT_LOW:
            violations.append(
                f"v_outn={v_outn:.3f}V > {_SPEC_VOUT_LOW:.1f}V (not resolved low)"
            )

        # Pelgrom offset (analytical, from sizing)
        if sizing is not None:
            sigma_mv = self._sigma_vos(sizing) * 1e3
            if sigma_mv > _SPEC_OFFSET_MV:
                violations.append(
                    f"sigma_Vos={sigma_mv:.2f}mV > {_SPEC_OFFSET_MV:.1f}mV"
                )

        return (len(violations) == 0, violations)
