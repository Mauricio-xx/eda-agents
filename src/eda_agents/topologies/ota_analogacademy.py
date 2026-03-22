"""AnalogAcademy PMOS-input two-stage OTA topology.

Wraps the IHP-AnalogAcademy OTA design (part_1_OTA) as a CircuitTopology.
This is a PMOS-input pair topology with NMOS mirror load, unlike the
Miller OTA which uses NMOS input.

Reference CDL (9 transistors):
    M1/M2: PMOS diff pair     (L=3.64u, W=3.705u)
    M3/M4: NMOS current mirror (L=9.75u, W=0.72u)
    M5:    PMOS tail bias      (L=1.95u, W=5.3u)
    M6:    NMOS output CS      (L=9.75u, W=28.8u, ng=4)
    M7/M9: PMOS current source (L=2.08u, W=75u,   ng=8)
    C2:    MIM Miller comp cap (~750fF)

Supports any PDK via PdkConfig (defaults to IHP SG13G2).
Source: /home/montanares/git/eda_sandbox/IHP-AnalogAcademy/
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from eda_agents.core.pdk import PdkConfig, netlist_lib_lines, netlist_osdi_lines, resolve_pdk
from eda_agents.core.topology import CircuitTopology
from eda_agents.core.spice_runner import SpiceResult

logger = logging.getLogger(__name__)

# AnalogAcademy reference sizing (from CDL netlist)
_REF_SIZING = {
    "M1": {"W": 3.705e-6, "L": 3.64e-6, "ng": 1, "type": "pmos"},  # diff pair
    "M2": {"W": 3.705e-6, "L": 3.64e-6, "ng": 1, "type": "pmos"},  # diff pair
    "M3": {"W": 0.72e-6,  "L": 9.75e-6, "ng": 1, "type": "nmos"},  # mirror (diode)
    "M4": {"W": 0.72e-6,  "L": 9.75e-6, "ng": 1, "type": "nmos"},  # mirror
    "M5": {"W": 5.3e-6,   "L": 1.95e-6, "ng": 1, "type": "pmos"},  # tail bias
    "M6": {"W": 28.8e-6,  "L": 9.75e-6, "ng": 4, "type": "nmos"},  # output CS
    "M7": {"W": 75.0e-6,  "L": 2.08e-6, "ng": 8, "type": "pmos"},  # current src
    "M9": {"W": 75.0e-6,  "L": 2.08e-6, "ng": 8, "type": "pmos"},  # current src (diode)
}

# Design specs (from AnalogAcademy design_of_ota.md)
_VDD = 1.2       # V
_VCM = 0.6       # V
_IBIAS = 80e-6   # A (reference bias current)
_CL = 500e-15    # F (load capacitance)

# Spec targets for validity
_SPEC_ADC_DB = 50.0    # dB min DC gain
_SPEC_GBW_HZ = 1e6     # Hz min GBW
_SPEC_PM_DEG = 45.0     # deg min phase margin


def _estimate_gmid(lut, mos_type: str, L_um: float, target_idw: float,
                    Vds: float = 0.6) -> float:
    """Estimate gm/ID from target |ID/W| via LUT interpolation.

    Uses the monotonically decreasing region of gm/ID (above peak) where
    |ID/W| increases monotonically.  Returns gm/ID clamped to [2, 28].
    """
    import numpy as np

    data = lut.lookup(mos_type, L_um, Vds=Vds)
    gm_id = np.array(data["gm_id"])
    id_w = np.abs(np.array(data["id_w"]))

    peak_idx = int(np.argmax(gm_id))
    gm_id_mono = gm_id[peak_idx:]
    id_w_mono = id_w[peak_idx:]

    valid = gm_id_mono > 0.5
    if np.sum(valid) < 2:
        return 12.0

    gm_id_v = gm_id_mono[valid]
    id_w_v = id_w_mono[valid]

    t = abs(target_idw)
    if t <= float(id_w_v[0]):
        return float(gm_id_v[0])
    if t >= float(id_w_v[-1]):
        return float(gm_id_v[-1])

    result = float(np.interp(t, id_w_v, gm_id_v))
    return max(2.0, min(result, 28.0))


class AnalogAcademyOTATopology(CircuitTopology):
    """AnalogAcademy PMOS-input two-stage OTA.

    Design space is parameterized by:
        - Ibias_uA:   tail bias current [10, 150] uA
        - L_dp_um:    diff pair channel length [0.5, 5.0] um
        - L_load_um:  load/output stage channel length [1.0, 15.0] um
        - Cc_pF:      Miller compensation cap [0.3, 3.0] pF
        - W_dp_um:    diff pair width [0.5, 10.0] um

    Parameters
    ----------
    pdk : PdkConfig or str, optional
        PDK configuration. Defaults to resolve_pdk().
    """

    _lut_cache: dict[str, object] = {}  # class-level per-PDK GmIdLookup cache

    def __init__(self, pdk: PdkConfig | str | None = None):
        self.pdk = resolve_pdk(pdk)

    def topology_name(self) -> str:
        return "aa_ota"

    def design_space(self) -> dict[str, tuple[float, float]]:
        return {
            "Ibias_uA": (10.0, 150.0),
            "L_dp_um": (0.5, 5.0),
            "L_load_um": (1.0, 10.0),
            "Cc_pF": (0.3, 3.0),
            "W_dp_um": (0.5, 10.0),
        }

    def default_params(self) -> dict[str, float]:
        """Reference design point from AnalogAcademy."""
        return {
            "Ibias_uA": 80.0,
            "L_dp_um": 3.64,
            "L_load_um": 9.75,
            "Cc_pF": 0.75,
            "W_dp_um": 3.705,
        }

    # ------------------------------------------------------------------
    # Prompt metadata
    # ------------------------------------------------------------------

    def prompt_description(self) -> str:
        return (
            f"Two-stage OTA on {self.pdk.display_name}. "
            "PMOS-input diff pair with NMOS mirror load "
            "and NMOS common-source second stage with Miller compensation."
        )

    def design_vars_description(self) -> str:
        return (
            "- Ibias_uA: tail bias current [10-150 uA]. Main power/speed knob. "
            "More current = higher GBW but more power consumption.\n"
            "- L_dp_um: diff pair channel length [0.5-5.0 um]. "
            "Affects input stage gain and speed.\n"
            "- L_load_um: load and second-stage channel length [1.0-10.0 um]. "
            "Longer = more gain (higher rds) but slower and more area. Key gain variable.\n"
            "- Cc_pF: Miller compensation cap [0.3-3.0 pF]. "
            "Larger = better phase margin but lower GBW.\n"
            "- W_dp_um: diff pair width [0.5-10.0 um]. "
            "Affects gm, matching, and input capacitance."
        )

    def specs_description(self) -> str:
        return (
            f"Adc >= {_SPEC_ADC_DB:.0f} dB, "
            f"GBW >= {_SPEC_GBW_HZ/1e6:.0f} MHz, "
            f"PM >= {_SPEC_PM_DEG:.0f} deg"
        )

    def fom_description(self) -> str:
        return (
            "FoM = Adc_linear * GBW / (Power * Area). "
            "Higher FoM is better. Designs violating specs get penalized."
        )

    def reference_description(self) -> str:
        return (
            "Reference: Ibias=80uA, L_dp=3.64um, L_load=9.75um, Cc=0.75pF, W_dp=3.705um "
            "-> Adc=56.7dB, GBW=2.1MHz, PM=74.1deg."
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
                        "Ibias_uA": {
                            "type": "number",
                            "description": "Tail bias current [10-150 uA]. Main power knob. More current = higher GBW but more power.",
                        },
                        "L_dp_um": {
                            "type": "number",
                            "description": "Diff pair channel length [0.5-5.0 um]. Affects input pair gain and speed.",
                        },
                        "L_load_um": {
                            "type": "number",
                            "description": "Load/second-stage channel length [1.0-10.0 um]. Longer = more gain but slower. PDK max is 10um.",
                        },
                        "Cc_pF": {
                            "type": "number",
                            "description": "Miller compensation cap [0.3-3.0 pF]. Larger = better PM but lower GBW.",
                        },
                        "W_dp_um": {
                            "type": "number",
                            "description": "Diff pair width [0.5-10.0 um]. Affects gm, input capacitance, and matching.",
                        },
                    },
                    "required": ["Ibias_uA", "L_dp_um", "L_load_um", "Cc_pF", "W_dp_um"],
                },
            },
        }

    def params_to_sizing(self, params: dict[str, float]) -> dict[str, dict]:
        """Convert design parameters to transistor sizing.

        Scaling rules (relative to reference):
        - M1/M2 (diff pair): W and L from params directly
        - M3/M4 (mirror): L = L_load, W scaled to maintain gm/ID ~= reference
        - M5 (tail): scaled for Ibias
        - M6 (output NMOS): L = L_load, W scaled with Ibias for gm2
        - M7/M9 (current src): scaled with Ibias
        - Cc: from params
        """
        Ibias = params["Ibias_uA"] * 1e-6
        L_dp = params["L_dp_um"] * 1e-6
        L_load = params["L_load_um"] * 1e-6
        W_dp = params["W_dp_um"] * 1e-6
        Cc = params["Cc_pF"] * 1e-12

        # Current ratio relative to reference 80uA
        i_ratio = Ibias / _IBIAS

        # Diff pair: direct from params
        W1 = max(W_dp, self.pdk.Wmin_m)
        L1 = max(L_dp, self.pdk.Lmin_m)

        # NMOS mirror load: L from L_load, W scales with current
        W3 = max(_REF_SIZING["M3"]["W"] * i_ratio, self.pdk.Wmin_m)
        L3 = max(L_load, self.pdk.Lmin_m)

        # Tail current source: W scales with current, L from ref
        W5 = max(_REF_SIZING["M5"]["W"] * i_ratio, self.pdk.Wmin_m)
        L5 = max(_REF_SIZING["M5"]["L"], self.pdk.Lmin_m)

        # Output NMOS CS: W scales with current, L = L_load
        W6 = max(_REF_SIZING["M6"]["W"] * i_ratio, self.pdk.Wmin_m)
        L6 = max(L_load, self.pdk.Lmin_m)
        ng6 = max(1, round(W6 / 10e-6))  # split into fingers if W > 10um

        # PMOS current mirror for second stage
        W7 = max(_REF_SIZING["M7"]["W"] * i_ratio, self.pdk.Wmin_m)
        L7 = max(_REF_SIZING["M7"]["L"], self.pdk.Lmin_m)
        ng7 = max(1, round(W7 / 10e-6))

        sizing = {
            "M1": {"W": W1, "L": L1, "ng": 1, "type": "pmos"},
            "M2": {"W": W1, "L": L1, "ng": 1, "type": "pmos"},
            "M3": {"W": W3, "L": L3, "ng": 1, "type": "nmos"},
            "M4": {"W": W3, "L": L3, "ng": 1, "type": "nmos"},
            "M5": {"W": W5, "L": L5, "ng": 1, "type": "pmos"},
            "M6": {"W": W6, "L": L6, "ng": ng6, "type": "nmos"},
            "M7": {"W": W7, "L": L7, "ng": ng7, "type": "pmos"},
            "M9": {"W": W7, "L": L7, "ng": ng7, "type": "pmos"},
            "_Cc": Cc,
            "_Ibias": Ibias,
            "_CL": _CL,
            "_VDD": self.pdk.VDD,
            "_VCM": self.pdk.VDD / 2,
        }

        # Compute analytical estimates for pre-filtering
        sizing["_analytical"] = self._compute_analytical(params, sizing)

        return sizing

    def _compute_analytical(
        self, params: dict[str, float], sizing: dict
    ) -> dict:
        """Estimate Adc, GBW, PM from gm/ID LUT data.

        Uses the current mirror ratio to compute actual branch currents
        (tail current << Ibias due to M5/M9 mirror ratio), then looks
        up intrinsic gains from the LUT at each transistor's operating point.

        Returns a dict with estimated performance metrics, or a note
        if the LUT data is unavailable.
        """
        try:
            from eda_agents.core.gmid_lookup import GmIdLookup

            pdk_name = self.pdk.name
            if pdk_name not in AnalogAcademyOTATopology._lut_cache:
                AnalogAcademyOTATopology._lut_cache[pdk_name] = GmIdLookup(pdk=self.pdk)
            lut = AnalogAcademyOTATopology._lut_cache[pdk_name]

            Ibias = sizing["_Ibias"]
            Cc = sizing["_Cc"]
            CL = sizing["_CL"]

            # --- Compute actual branch currents via mirror ratios ---
            # M9 (diode) carries Ibias; M5 mirrors M9 for tail; M7 mirrors M9 for stage 2
            m5 = sizing["M5"]
            m9 = sizing["M9"]
            m7 = sizing["M7"]

            W5_eff = m5["W"] * m5.get("ng", 1)
            L5 = m5["L"]
            W9_eff = m9["W"] * m9.get("ng", 1)
            L9 = m9["L"]
            W7_eff = m7["W"] * m7.get("ng", 1)
            L7 = m7["L"]

            mirror_tail = (W5_eff / L5) / (W9_eff / L9)
            mirror_stage2 = (W7_eff / L7) / (W9_eff / L9)

            I_tail = Ibias * mirror_tail
            I_branch = I_tail / 2
            I_stage2 = Ibias * mirror_stage2

            # --- Diff pair PMOS (M1/M2) ---
            W_dp = sizing["M1"]["W"]
            L_dp_um = params["L_dp_um"]
            idw_dp = I_branch / W_dp if W_dp > 0 else 1.0
            gmid_dp = _estimate_gmid(lut, "pmos", L_dp_um, idw_dp, Vds=-0.5)
            dp_pt = lut.query_at_gmid(gmid_dp, "pmos", L_dp_um, Vds=-0.5)
            Av_dp = dp_pt["gm_gds"] if dp_pt else 10.0

            # --- Load NMOS (M3/M4 mirror) ---
            W3 = sizing["M3"]["W"]
            L_load_um = params["L_load_um"]
            idw_load = I_branch / W3 if W3 > 0 else 1.0
            gmid_load = _estimate_gmid(lut, "nmos", L_load_um, idw_load, Vds=0.5)
            load_pt = lut.query_at_gmid(gmid_load, "nmos", L_load_um, Vds=0.5)
            Av_load = load_pt["gm_gds"] if load_pt else 10.0

            # Stage 1 gain = parallel output resistances
            A1 = (Av_dp * Av_load) / (Av_dp + Av_load) if (Av_dp + Av_load) > 0 else 1.0

            # --- Output NMOS CS (M6) ---
            m6 = sizing["M6"]
            W6_total = m6["W"] * m6.get("ng", 1)
            idw_m6 = I_stage2 / W6_total if W6_total > 0 else 1.0
            gmid_m6 = _estimate_gmid(lut, "nmos", L_load_um, idw_m6, Vds=0.5)
            m6_pt = lut.query_at_gmid(gmid_m6, "nmos", L_load_um, Vds=0.5)
            Av_m6 = m6_pt["gm_gds"] if m6_pt else 10.0

            # --- PMOS current source (M7) ---
            L7_um = L7 * 1e6
            idw_m7 = I_stage2 / W7_eff if W7_eff > 0 else 1.0
            gmid_m7 = _estimate_gmid(lut, "pmos", L7_um, idw_m7, Vds=-0.5)
            m7_pt = lut.query_at_gmid(gmid_m7, "pmos", L7_um, Vds=-0.5)
            Av_m7 = m7_pt["gm_gds"] if m7_pt else 10.0

            # Stage 2 gain
            A2 = (Av_m6 * Av_m7) / (Av_m6 + Av_m7) if (Av_m6 + Av_m7) > 0 else 1.0

            # --- Total gain ---
            Adc_linear = A1 * A2
            Adc_dB = 20 * math.log10(max(Adc_linear, 1e-10))

            # --- GBW = gm1 / (2*pi*Cc) ---
            gm1 = gmid_dp * I_branch
            GBW_Hz = gm1 / (2 * math.pi * Cc) if Cc > 0 else 0.0

            # --- Phase margin ---
            gm6 = gmid_m6 * I_stage2
            p2_Hz = gm6 / (2 * math.pi * CL) if CL > 0 else 1e12
            z_rhp_Hz = gm6 / (2 * math.pi * Cc) if Cc > 0 else 1e12

            PM_deg = 90.0
            if p2_Hz > 0:
                PM_deg -= math.degrees(math.atan(GBW_Hz / p2_Hz))
            if z_rhp_Hz > 0:
                PM_deg -= math.degrees(math.atan(GBW_Hz / z_rhp_Hz))

            # --- Power and FoM ---
            power_w = sizing["_VDD"] * (I_tail + I_stage2)
            area_m2 = sum(
                d["W"] * d["L"] * d.get("ng", 1)
                for k, d in sizing.items()
                if not k.startswith("_") and isinstance(d, dict)
            )

            fom = 0.0
            if power_w > 0 and area_m2 > 0:
                fom = Adc_linear * GBW_Hz / (power_w * area_m2)

            valid = (
                Adc_dB >= _SPEC_ADC_DB
                and GBW_Hz >= _SPEC_GBW_HZ
                and PM_deg >= _SPEC_PM_DEG
            )

            return {
                "Adc_dB": round(Adc_dB, 1),
                "GBW_Hz": round(GBW_Hz),
                "GBW_MHz": round(GBW_Hz / 1e6, 3),
                "PM_deg": round(PM_deg, 1),
                "A1_dB": round(20 * math.log10(max(A1, 1e-10)), 1),
                "A2_dB": round(20 * math.log10(max(A2, 1e-10)), 1),
                "gm1_uS": round(gm1 * 1e6, 2),
                "gm6_uS": round(gm6 * 1e6, 2),
                "I_tail_uA": round(I_tail * 1e6, 3),
                "I_stage2_uA": round(I_stage2 * 1e6, 1),
                "power_uW": round(power_w * 1e6, 1),
                "FoM": fom,
                "valid": valid,
            }

        except Exception as e:
            logger.warning("Analytical model failed: %s", e)
            return {"note": f"analytical model unavailable: {e}"}

    def generate_netlist(
        self, sizing: dict[str, dict], work_dir: Path
    ) -> Path:
        """Generate flat SPICE netlist for AC analysis.

        Creates three files:
            - aa_ota.net  (circuit subcircuit)
            - aa_ota.par  (parameters)
            - aa_ota.ac.cir (AC analysis control)

        Returns path to the .ac.cir file.
        """
        work_dir.mkdir(parents=True, exist_ok=True)

        Ibias = sizing["_Ibias"]
        Cc = sizing["_Cc"]
        CL = sizing["_CL"]
        VDD = sizing["_VDD"]
        VCM = sizing["_VCM"]

        # Junction area/perimeter
        z1 = self.pdk.z1_m

        def _junc(W: float) -> str:
            AS = W * z1
            PS = 2 * (W + z1)
            return f"AS={AS:.3e} PS={PS:.3e} AD={AS:.3e} PD={PS:.3e}"

        # Device symbols from PDK config
        pmos = self.pdk.pmos_symbol
        nmos = self.pdk.nmos_symbol
        px = self.pdk.instance_prefix  # "X" for subcircuit-based PDKs

        # --- Netlist ---
        # AnalogAcademy topology: PMOS input, NMOS mirror
        # Pin order: drain gate source bulk
        m1 = sizing["M1"]
        m3 = sizing["M3"]
        m5 = sizing["M5"]
        m6 = sizing["M6"]
        m7 = sizing["M7"]

        net_lines = [
            f"* AnalogAcademy Two-Stage OTA - {self.pdk.display_name}",
            "* PMOS input pair, NMOS mirror load, Miller comp",
            "",
            f"* Stage 1: PMOS diff pair + NMOS mirror",
            f"{px}1 net1 inn net2 VDD {pmos} W={m1['W']:.4e} L={m1['L']:.4e} ng={m1['ng']} m=1 {_junc(m1['W'])}",
            f"{px}2 net3 inp net2 VDD {pmos} W={m1['W']:.4e} L={m1['L']:.4e} ng={m1['ng']} m=1 {_junc(m1['W'])}",
            f"{px}3 net1 net1 0 0 {nmos} W={m3['W']:.4e} L={m3['L']:.4e} ng={m3['ng']} m=1 {_junc(m3['W'])}",
            f"{px}4 net3 net1 0 0 {nmos} W={m3['W']:.4e} L={m3['L']:.4e} ng={m3['ng']} m=1 {_junc(m3['W'])}",
            "",
            f"* Tail current source",
            f"{px}5 net2 nb VDD VDD {pmos} W={m5['W']:.4e} L={m5['L']:.4e} ng={m5['ng']} m=1 {_junc(m5['W'])}",
            "",
            f"* Stage 2: NMOS CS + PMOS current source",
            f"{px}6 vout net3 0 0 {nmos} W={m6['W']:.4e} L={m6['L']:.4e} ng={m6['ng']} m=1 {_junc(m6['W'])}",
            f"{px}7 vout nb VDD VDD {pmos} W={m7['W']:.4e} L={m7['L']:.4e} ng={m7['ng']} m=1 {_junc(m7['W'])}",
            "",
            f"* Bias mirror diode",
            f"{px}9 nb nb VDD VDD {pmos} W={m7['W']:.4e} L={m7['L']:.4e} ng={m7['ng']} m=1 {_junc(m7['W'])}",
            "",
            f"* Compensation and load",
            f"Cc net3 vout {Cc:.4e}",
            f"CL vout 0 {CL:.4e}",
            "",
            f"* Bias current source (sinks reference current through M9 diode)",
            f"Ibias nb 0 {Ibias:.4e}",
            "",
            f"* Supply and input",
            f"VVDD VDD 0 {VDD}",
            f"Vic ic 0 {VCM}",
            f"Vid id 0 DC=0 AC=1",
            f"* Inverted input polarity: makes transfer function inverting at DC",
            f"* so PGBW directly gives PM (same convention as Miller OTA)",
            f"Einp inp ic id 0 -0.5",
            f"Einn inn ic id 0 0.5",
        ]

        net_file = work_dir / "aa_ota.net"
        net_file.write_text("\n".join(net_lines) + "\n")

        # --- AC analysis control file ---
        ac_lines = [
            f"AnalogAcademy OTA AC analysis - {self.pdk.display_name}",
            "",
            *netlist_lib_lines(self.pdk),
            f".include {net_file.name}",
            "",
            ".control",
            "  set ngbehavior=hsa",
            *netlist_osdi_lines(self.pdk),
            "  op",
            "  save v(vout)",
            "  ac dec 41 10 100MEG",
            "  let AmagdB=vdb(vout)",
            "  let Aphdeg=180/PI*vp(vout)",
            "  meas ac Adc find AmagdB at=10",
            "  meas ac Adc_peak max AmagdB",
            "  meas ac GBW when AmagdB=0",
            "  meas ac PGBW find Aphdeg at=GBW",
            "  set wr_singlescale",
            "  set wr_vecnames",
            "  wrdata aa_ota.ac.dat AmagdB Aphdeg",
            ".endc",
            ".end",
        ]

        ac_file = work_dir / "aa_ota.ac.cir"
        ac_file.write_text("\n".join(ac_lines) + "\n")

        return ac_file

    def compute_fom(
        self, spice_result: SpiceResult, sizing: dict[str, dict]
    ) -> float:
        """FoM = Adc * GBW / (Power * Area).

        Returns 0.0 for failed or invalid simulations.
        """
        if not spice_result.success:
            return 0.0

        adc_dB = spice_result.Adc_dB
        gbw_hz = spice_result.GBW_Hz
        if adc_dB is None or gbw_hz is None:
            return 0.0

        # Estimate power from bias current and VDD
        Ibias = sizing.get("_Ibias", _IBIAS)
        VDD = sizing.get("_VDD", _VDD)
        # Total current: Ibias for tail + ~Ibias for second stage mirror
        power_w = VDD * 2 * Ibias  # rough estimate

        # Area: sum of W*L for all transistors
        area_m2 = 0.0
        for name, dev in sizing.items():
            if name.startswith("_"):
                continue
            area_m2 += dev["W"] * dev["L"] * dev.get("ng", 1)

        if power_w <= 0 or area_m2 <= 0:
            return 0.0

        adc_linear = 10 ** (adc_dB / 20)
        raw_fom = adc_linear * gbw_hz / (power_w * area_m2)

        # Apply spec penalty
        valid, violations = self.check_validity(spice_result)
        penalty = 1.0 if valid else max(0.01, 1.0 - 0.2 * len(violations))

        return raw_fom * penalty

    def check_validity(
        self, spice_result: SpiceResult, sizing: dict | None = None
    ) -> tuple[bool, list[str]]:
        """Check against AnalogAcademy design specs."""
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
