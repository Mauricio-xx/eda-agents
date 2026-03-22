"""PMOS-input two-stage OTA topology for GF180MCU.

Port of the AnalogAcademy OTA pattern to GF180MCU 180nm CMOS (3.3V).
LUT-based analytical model (no sEKV dependency). Same 9-transistor
topology as AnalogAcademyOTATopology but with design space adjusted
for 180nm/3.3V: wider L range, higher currents, larger Cc.

Reference schematic (9 transistors):
    M1/M2: PMOS diff pair
    M3/M4: NMOS current mirror
    M5:    PMOS tail bias
    M6:    NMOS output CS
    M7/M9: PMOS current source
    Cc:    MIM Miller compensation cap
"""

from __future__ import annotations

import logging
from pathlib import Path

from eda_agents.core.pdk import GF180MCU_D, PdkConfig, netlist_lib_lines, netlist_osdi_lines
from eda_agents.core.topology import CircuitTopology
from eda_agents.core.spice_runner import SpiceResult

logger = logging.getLogger(__name__)

# Design specs for GF180 OTA (relaxed vs 130nm due to longer channels)
_SPEC_ADC_DB = 40.0    # dB min DC gain
_SPEC_GBW_HZ = 500e3   # Hz min GBW (lower due to larger caps, longer channels)
_SPEC_PM_DEG = 45.0     # deg min phase margin

# Load capacitance
_CL = 2e-12   # 2pF (larger for 180nm process)


class GF180OTATopology(CircuitTopology):
    """PMOS-input two-stage OTA for GF180MCU.

    Design space:
        - Ibias_uA:   tail bias current [20, 500] uA
        - L_dp_um:    diff pair channel length [0.5, 10.0] um
        - L_load_um:  load/output stage channel length [1.0, 20.0] um
        - Cc_pF:      Miller compensation cap [0.5, 10.0] pF
        - W_dp_um:    diff pair width [1.0, 50.0] um

    Parameters
    ----------
    pdk : PdkConfig, optional
        PDK configuration. Defaults to GF180MCU_D.
    """

    _lut_cache: dict[str, object] = {}

    def __init__(self, pdk: PdkConfig | None = None):
        self.pdk = pdk or GF180MCU_D

    def topology_name(self) -> str:
        return "gf180_ota"

    def design_space(self) -> dict[str, tuple[float, float]]:
        return {
            "Ibias_uA": (20.0, 500.0),
            "L_dp_um": (0.5, 10.0),
            "L_load_um": (1.0, 20.0),
            "Cc_pF": (0.5, 10.0),
            "W_dp_um": (1.0, 50.0),
        }

    def default_params(self) -> dict[str, float]:
        """Starting design point for exploration."""
        return {
            "Ibias_uA": 100.0,
            "L_dp_um": 2.0,
            "L_load_um": 5.0,
            "Cc_pF": 2.0,
            "W_dp_um": 10.0,
        }

    # ------------------------------------------------------------------
    # Prompt metadata
    # ------------------------------------------------------------------

    def prompt_description(self) -> str:
        return (
            f"Two-stage OTA on {self.pdk.display_name}. "
            "PMOS-input diff pair with NMOS mirror load "
            "and NMOS common-source second stage with Miller compensation. "
            f"VDD={self.pdk.VDD}V, devices: {self.pdk.nmos_symbol}/{self.pdk.pmos_symbol}."
        )

    def design_vars_description(self) -> str:
        return (
            "- Ibias_uA: tail bias current [20-500 uA]. Main power/speed knob.\n"
            "- L_dp_um: diff pair channel length [0.5-10.0 um]. "
            "Affects input stage gain and speed.\n"
            "- L_load_um: load and second-stage channel length [1.0-20.0 um]. "
            "Longer = more gain (higher rds) but slower.\n"
            "- Cc_pF: Miller compensation cap [0.5-10.0 pF]. "
            "Larger = better phase margin but lower GBW.\n"
            "- W_dp_um: diff pair width [1.0-50.0 um]. "
            "Affects gm, matching, and input capacitance."
        )

    def specs_description(self) -> str:
        return (
            f"Adc >= {_SPEC_ADC_DB:.0f} dB, "
            f"GBW >= {_SPEC_GBW_HZ/1e3:.0f} kHz, "
            f"PM >= {_SPEC_PM_DEG:.0f} deg"
        )

    def fom_description(self) -> str:
        return (
            "FoM = Adc_linear * GBW / (Power * Area). "
            "Higher FoM is better. Designs violating specs get penalized."
        )

    def reference_description(self) -> str:
        return (
            "Starting point: Ibias=100uA, L_dp=2um, L_load=5um, "
            "Cc=2pF, W_dp=10um. Performance TBD from initial SPICE sweep."
        )

    def params_to_sizing(self, params: dict[str, float]) -> dict[str, dict]:
        """Convert design parameters to transistor sizing."""
        Ibias = params["Ibias_uA"] * 1e-6
        L_dp = params["L_dp_um"] * 1e-6
        L_load = params["L_load_um"] * 1e-6
        W_dp = params["W_dp_um"] * 1e-6
        Cc = params["Cc_pF"] * 1e-12

        Wmin = self.pdk.Wmin_m
        Lmin = self.pdk.Lmin_m

        # Current ratio relative to reference 100uA
        i_ratio = Ibias / 100e-6

        # Diff pair: direct from params
        W1 = max(W_dp, Wmin)
        L1 = max(L_dp, Lmin)

        # NMOS mirror load: L from L_load, W scales with current
        # Reference: W=2um at 100uA
        W3 = max(2.0e-6 * i_ratio, Wmin)
        L3 = max(L_load, Lmin)

        # Tail current source: W scales with current
        # Reference: W=20um at 100uA
        W5 = max(20.0e-6 * i_ratio, Wmin)
        L5 = max(2.0e-6, Lmin)

        # Output NMOS CS: W scales with sqrt(current) to keep reasonable sizes
        # Reference: W=20um at 100uA
        W6 = max(20.0e-6 * (i_ratio ** 0.5), Wmin)
        L6 = max(L_load, Lmin)
        ng6 = max(1, round(W6 / 10e-6))

        # PMOS current mirror for second stage
        # Reference: W=40um at 100uA
        W7 = max(40.0e-6 * (i_ratio ** 0.5), Wmin)
        L7 = max(2.0e-6, Lmin)
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

        return sizing

    def generate_netlist(
        self, sizing: dict[str, dict], work_dir: Path
    ) -> Path:
        """Generate SPICE netlist for AC analysis."""
        work_dir.mkdir(parents=True, exist_ok=True)

        Ibias = sizing["_Ibias"]
        Cc = sizing["_Cc"]
        CL = sizing["_CL"]
        VDD = sizing["_VDD"]
        VCM = sizing["_VCM"]

        z1 = self.pdk.z1_m
        pmos = self.pdk.pmos_symbol
        nmos = self.pdk.nmos_symbol
        px = self.pdk.instance_prefix

        def _junc(W: float) -> str:
            AS = W * z1
            PS = 2 * (W + z1)
            return f"AS={AS:.3e} PS={PS:.3e} AD={AS:.3e} PD={PS:.3e}"

        m1 = sizing["M1"]
        m3 = sizing["M3"]
        m5 = sizing["M5"]
        m6 = sizing["M6"]
        m7 = sizing["M7"]

        net_lines = [
            f"* Two-Stage OTA - {self.pdk.display_name}",
            "* PMOS input pair, NMOS mirror load, Miller comp",
            "",
            "* Stage 1: PMOS diff pair + NMOS mirror",
            f"{px}1 net1 inn net2 VDD {pmos} W={m1['W']:.4e} L={m1['L']:.4e} ng={m1['ng']} m=1 {_junc(m1['W'])}",
            f"{px}2 net3 inp net2 VDD {pmos} W={m1['W']:.4e} L={m1['L']:.4e} ng={m1['ng']} m=1 {_junc(m1['W'])}",
            f"{px}3 net1 net1 0 0 {nmos} W={m3['W']:.4e} L={m3['L']:.4e} ng={m3['ng']} m=1 {_junc(m3['W'])}",
            f"{px}4 net3 net1 0 0 {nmos} W={m3['W']:.4e} L={m3['L']:.4e} ng={m3['ng']} m=1 {_junc(m3['W'])}",
            "",
            "* Tail current source",
            f"{px}5 net2 nb VDD VDD {pmos} W={m5['W']:.4e} L={m5['L']:.4e} ng={m5['ng']} m=1 {_junc(m5['W'])}",
            "",
            "* Stage 2: NMOS CS + PMOS current source",
            f"{px}6 vout net3 0 0 {nmos} W={m6['W']:.4e} L={m6['L']:.4e} ng={m6['ng']} m=1 {_junc(m6['W'])}",
            f"{px}7 vout nb VDD VDD {pmos} W={m7['W']:.4e} L={m7['L']:.4e} ng={m7['ng']} m=1 {_junc(m7['W'])}",
            "",
            "* Bias mirror diode",
            f"{px}9 nb nb VDD VDD {pmos} W={m7['W']:.4e} L={m7['L']:.4e} ng={m7['ng']} m=1 {_junc(m7['W'])}",
            "",
            "* Compensation and load",
            f"Cc net3 vout {Cc:.4e}",
            f"CL vout 0 {CL:.4e}",
            "",
            "* Bias current source",
            f"Ibias nb 0 {Ibias:.4e}",
            "",
            "* Supply and input",
            f"VVDD VDD 0 {VDD}",
            f"Vic ic 0 {VCM}",
            "Vid id 0 DC=0 AC=1",
            "* Inverted input polarity for PM convention",
            "Einp inp ic id 0 -0.5",
            "Einn inn ic id 0 0.5",
        ]

        net_file = work_dir / "gf180_ota.net"
        net_file.write_text("\n".join(net_lines) + "\n")

        # AC analysis control file
        ac_lines = [
            f"GF180 OTA AC analysis - {self.pdk.display_name}",
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
            "  wrdata gf180_ota.ac.dat AmagdB Aphdeg",
            ".endc",
            ".end",
        ]

        ac_file = work_dir / "gf180_ota.ac.cir"
        ac_file.write_text("\n".join(ac_lines) + "\n")
        return ac_file

    def compute_fom(
        self, spice_result: SpiceResult, sizing: dict[str, dict]
    ) -> float:
        """FoM = Adc * GBW / (Power * Area)."""
        if not spice_result.success:
            return 0.0

        adc_dB = spice_result.Adc_dB
        gbw_hz = spice_result.GBW_Hz
        if adc_dB is None or gbw_hz is None:
            return 0.0

        Ibias = sizing.get("_Ibias", 100e-6)
        VDD = sizing.get("_VDD", self.pdk.VDD)
        power_w = VDD * 2 * Ibias

        area_m2 = sum(
            d["W"] * d["L"] * d.get("ng", 1)
            for k, d in sizing.items()
            if not k.startswith("_") and isinstance(d, dict)
        )

        if power_w <= 0 or area_m2 <= 0:
            return 0.0

        adc_linear = 10 ** (adc_dB / 20)
        raw_fom = adc_linear * gbw_hz / (power_w * area_m2)

        valid, violations = self.check_validity(spice_result)
        penalty = 1.0 if valid else max(0.01, 1.0 - 0.2 * len(violations))
        return raw_fom * penalty

    def check_validity(
        self, spice_result: SpiceResult, sizing: dict | None = None
    ) -> tuple[bool, list[str]]:
        """Check against GF180 OTA design specs."""
        violations: list[str] = []

        if not spice_result.success:
            return (False, ["simulation failed"])

        if spice_result.Adc_dB is not None and spice_result.Adc_dB < _SPEC_ADC_DB:
            violations.append(
                f"Adc={spice_result.Adc_dB:.1f}dB < {_SPEC_ADC_DB}dB"
            )
        if spice_result.GBW_Hz is not None and spice_result.GBW_Hz < _SPEC_GBW_HZ:
            violations.append(
                f"GBW={spice_result.GBW_Hz/1e3:.1f}kHz < {_SPEC_GBW_HZ/1e3:.0f}kHz"
            )
        if spice_result.PM_deg is not None and spice_result.PM_deg < _SPEC_PM_DEG:
            violations.append(
                f"PM={spice_result.PM_deg:.1f}deg < {_SPEC_PM_DEG}deg"
            )

        return (len(violations) == 0, violations)
