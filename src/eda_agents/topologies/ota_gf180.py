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
        """Starting design point for exploration.

        Validated via SPICE: Adc~52dB, GBW~3.87MHz, PM~73.7deg.
        """
        return {
            "Ibias_uA": 200.0,
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
            "Starting point: Ibias=200uA, L_dp=2um, L_load=5um, "
            "Cc=2pF, W_dp=10um. SPICE-measured: Adc=52.0dB, GBW=3.87MHz, "
            "PM=73.7deg, FoM=6.02e+20."
        )

    # ------------------------------------------------------------------
    # gLayout integration
    # ------------------------------------------------------------------

    # MIM cap density for GF180MCU mimcap_1p0fF: ~1.0 fF/um^2
    _MIM_CAP_DENSITY = 1.0  # fF/um^2
    _MIM_CAP_COLS = 2  # opamp_twostage always uses 2 columns

    @staticmethod
    def glayout_default_params() -> dict:
        """gLayout opamp_twostage defaults -- known to produce valid layout.

        These are the default parameter values from gLayout's
        opamp_twostage() function signature.  Using these directly
        bypasses the topology mismatch in sizing_to_glayout_params().
        """
        return {
            "half_diffpair_params": (6, 1, 4),
            "diffpair_bias": (6, 2, 4),
            "half_common_source_params": (7, 1, 10, 3),
            "half_common_source_bias": (6, 2, 8, 2),
            "half_pload": (6, 1, 6),
            "mim_cap_size": (12, 12),
            "mim_cap_rows": 3,
        }

    def sizing_to_glayout_params(self, sizing: dict) -> dict:
        """Convert params_to_sizing() output to gLayout opamp_twostage() args.

        .. deprecated::
            This mapping is incorrect: it maps our PMOS-input OTA transistors
            (M1=PMOS diff pair, M3=NMOS load, M5=PMOS tail) to gLayout's
            NMOS-input topology (half_diffpair=NMOS, half_pload=PMOS,
            diffpair_bias=NMOS tail).  This is a type mismatch that produces
            an unbalanced circuit.  Use ``glayout_default_params()`` for
            validated defaults, or build a proper NMOS-aware mapping.

        Maps transistor W/L/ng (SI units) to gLayout's tuple format
        (width_um, length_um, fingers[, mults]). Computes MIM cap array
        geometry from the Cc value.

        Returns
        -------
        dict
            Ready to pass to ``opamp_twostage(pdk, **result)``.
        """
        m1 = sizing["M1"]  # PMOS diff pair half
        m5 = sizing["M5"]  # PMOS tail bias
        m6 = sizing["M6"]  # NMOS output CS
        m3 = sizing["M3"]  # NMOS mirror (used for CS bias)
        m7 = sizing["M7"]  # PMOS current mirror / pload
        Cc = sizing["_Cc"]

        def _to_um(val_m: float) -> float:
            return val_m * 1e6

        # half_diffpair_params: (W, L, fingers) -- PMOS diff pair
        half_diffpair_params = (
            _to_um(m1["W"]),
            _to_um(m1["L"]),
            max(1, m1.get("ng", 1)),
        )

        # diffpair_bias: (W, L, fingers) -- NMOS mirror ref at diff pair
        diffpair_bias = (
            _to_um(m5["W"]),
            _to_um(m5["L"]),
            max(1, m5.get("ng", 1)),
        )

        # half_common_source_params: (W, L, fingers, mults) -- PMOS top of 2nd stage
        # In gLayout this is the PMOS amp transistor
        half_common_source_params = (
            _to_um(m7["W"]),
            _to_um(m7["L"]),
            max(1, m7.get("ng", 1)),
            max(2, m7.get("ng", 1)),  # mults >= 2 required by gLayout
        )

        # half_common_source_bias: (W, L, fingers, mults) -- NMOS bottom of 2nd stage
        # mults must be >= 2
        half_common_source_bias = (
            _to_um(m6["W"]),
            _to_um(m6["L"]),
            max(1, m6.get("ng", 1)),
            max(2, m6.get("ng", 1)),
        )

        # half_pload: (W, L, fingers) -- PMOS load of 1st stage
        half_pload = (
            _to_um(m3["W"]),
            _to_um(m3["L"]),
            max(1, m3.get("ng", 1)),
        )

        # MIM cap: compute array size from total Cc
        # Total cap = mim_cap_size[0] * mim_cap_size[1] * density * mim_cap_rows * cols
        Cc_fF = Cc * 1e15
        # Target: square-ish individual caps, then scale rows
        # Start with a reasonable per-unit area, then pick rows
        cap_per_unit_fF = max(Cc_fF / (self._MIM_CAP_COLS * 3), 1.0)  # start with 3 rows
        side_um = max(5.0, (cap_per_unit_fF / self._MIM_CAP_DENSITY) ** 0.5)
        cap_per_unit_actual = side_um * side_um * self._MIM_CAP_DENSITY
        total_units = max(1, round(Cc_fF / cap_per_unit_actual))
        mim_cap_rows = max(1, (total_units + self._MIM_CAP_COLS - 1) // self._MIM_CAP_COLS)
        mim_cap_size = (round(side_um, 1), round(side_um, 1))

        return {
            "half_diffpair_params": half_diffpair_params,
            "diffpair_bias": diffpair_bias,
            "half_common_source_params": half_common_source_params,
            "half_common_source_bias": half_common_source_bias,
            "half_pload": half_pload,
            "mim_cap_size": mim_cap_size,
            "mim_cap_rows": mim_cap_rows,
        }

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

    # gLayout opamp_twostage port ordering: VDD, GND, DIFFPAIR_BIAS, VP, VN, CS_BIAS, VOUT
    _GLAYOUT_PORTS = ("VDD", "GND", "DIFFPAIR_BIAS", "VP", "VN", "CS_BIAS", "VOUT")

    def generate_postlayout_testbench(
        self,
        extracted_netlist_path: Path,
        sizing: dict[str, dict],
        work_dir: Path,
    ) -> Path:
        """Generate AC testbench wrapping an extracted (post-layout) netlist.

        The extracted netlist from Magic PEX contains a subcircuit with
        parasitic R/C. This testbench includes it and runs the same AC
        analysis as the pre-layout flow, allowing direct comparison.

        Parameters
        ----------
        extracted_netlist_path : Path
            Path to the .rcx.spice file from Magic PEX.
        sizing : dict
            Output from params_to_sizing() (for bias values).
        work_dir : Path
            Directory for output files.

        Returns
        -------
        Path
            Path to the .cir control file for SpiceRunner.
        """
        work_dir.mkdir(parents=True, exist_ok=True)
        extracted_netlist_path = Path(extracted_netlist_path).resolve()

        Ibias = sizing["_Ibias"]
        VDD = sizing["_VDD"]
        VCM = sizing["_VCM"]

        # The extracted subcircuit name is typically the design_name used during PEX.
        # Parse it from the .rcx.spice file to be safe.
        subckt_name = self._find_subckt_name(extracted_netlist_path)

        tb_lines = [
            f"Post-Layout AC Analysis - {self.pdk.display_name}",
            "",
            *netlist_lib_lines(self.pdk),
            f".include {extracted_netlist_path}",
            "",
            "* Instantiate extracted subcircuit",
            f"* Port order: {', '.join(self._GLAYOUT_PORTS)}",
            f"X1 VDD 0 nb inp inn nb2 vout {subckt_name}",
            "",
            "* Bias: DIFFPAIR_BIAS and CS_BIAS may need separate sources",
            "* In the pre-layout schematic, a single Ibias through M9 (diode)",
            "* mirrors to M5 (tail) and M7 (output stage PMOS).",
            "* Post-layout: nb is the bias node, nb2 is CS_BIAS.",
            "* Connect both to the same bias node for equivalent operation.",
            f"Ibias nb 0 {Ibias:.4e}",
            f"Ibias2 nb2 0 {Ibias:.4e}",
            "",
            "* Supply and input",
            f"VVDD VDD 0 {VDD}",
            f"Vic ic 0 {VCM}",
            "Vid id 0 DC=0 AC=1",
            "* Inverted input polarity for PM convention",
            "Einp inp ic id 0 -0.5",
            "Einn inn ic id 0 0.5",
            "",
            "* Load capacitance (external, not in extracted netlist)",
            f"CL vout 0 {sizing['_CL']:.4e}",
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
            "  wrdata gf180_ota_postlayout.ac.dat AmagdB Aphdeg",
            ".endc",
            ".end",
        ]

        cir_path = work_dir / "gf180_ota_postlayout.ac.cir"
        cir_path.write_text("\n".join(tb_lines) + "\n")
        return cir_path

    # ------------------------------------------------------------------
    # gLayout netlist preprocessing helpers
    # ------------------------------------------------------------------

    def _preprocess_glayout_netlist(
        self,
        glayout_netlist_path: Path,
        work_dir: Path,
        glayout_params: dict | None = None,
    ) -> Path:
        """Preprocess gLayout netlist for ngspice compatibility.

        Applies four fixes:
        1. MIM cap model name: mimcap_1p0fF -> cap_mim_1f0_m2m3_noshield
        2. MIM cap param names: l,w -> c_length,c_width
        3. CS_BIAS bug: patch GAIN_STAGE CMIRROR with half_common_source_bias
        4. Transistor l/w: um -> meters (GF180 PDK models expect SI units)

        Parameters
        ----------
        glayout_netlist_path : Path
            Original gLayout netlist (um units, gLayout model names).
        work_dir : Path
            Directory for the fixed netlist.
        glayout_params : dict or None
            gLayout parameter dict (must contain ``half_common_source_bias``
            for the CS_BIAS fix).  If None, the CS_BIAS fix is skipped.

        Returns
        -------
        Path
            Path to the preprocessed netlist.
        """
        import re as _re

        work_dir.mkdir(parents=True, exist_ok=True)
        text = glayout_netlist_path.read_text()

        # Fix 1: MIM cap model name
        text = text.replace("mimcap_1p0fF", "cap_mim_1f0_m2m3_noshield")

        # Fix 2: MIM cap parameter names (l,w -> c_length,c_width)
        text = text.replace(
            ".subckt MIMCap V1 V2 l=1 w=1",
            ".subckt MIMCap V1 V2 c_length=1 c_width=1",
        )
        text = text.replace(
            "cap_mim_1f0_m2m3_noshield l={l} w={w}",
            "cap_mim_1f0_m2m3_noshield c_length={c_length} c_width={c_width}",
        )
        text = _re.sub(
            r"MIMCap\s+l=([\d.]+)\s+w=([\d.]+)",
            r"MIMCap c_length=\1 c_width=\2",
            text,
        )

        # Fix 3: CS_BIAS bug (must precede um->m conversion)
        if glayout_params and "half_common_source_bias" in glayout_params:
            text = self._fix_cs_bias_netlist(text, glayout_params)

        # Fix 4: Convert transistor l= and w= from um to meters.
        # Matches space-preceded l= or w= with numeric values, skips
        # {l}/{w} template references and c_length/c_width.
        def _um_to_m(m: _re.Match) -> str:
            param = m.group(1)
            value = float(m.group(2))
            return f" {param}={value * 1e-6:.6e}"

        text = _re.sub(r"\s([lw])=([\d.]+(?:e[+-]?\d+)?)\b", _um_to_m, text)

        fixed_netlist = work_dir / glayout_netlist_path.name
        fixed_netlist.write_text(text)
        return fixed_netlist

    @staticmethod
    def _fix_cs_bias_netlist(text: str, glayout_params: dict) -> str:
        """Patch GAIN_STAGE CMIRROR instance to use half_common_source_bias.

        gLayout's opamp_twostage has a bug (line 223-228 in
        ``opamp_twostage.py``) where the CS_BIAS current mirror netlist
        is created with ``diffpair_bias`` parameters instead of
        ``half_common_source_bias``.  The layout uses the correct sizing,
        but the exported netlist is wrong, causing a netlist-layout
        mismatch and grossly unbalanced output stage bias.

        We fix this by finding the CMIRROR instance inside the GAIN_STAGE
        subcircuit and replacing its l/w/m with the correct values derived
        from ``half_common_source_bias``.
        """
        import re as _re

        hcsb = glayout_params["half_common_source_bias"]
        cs_w, cs_l, cs_fingers = hcsb[0], hcsb[1], hcsb[2]

        lines = text.splitlines()
        in_gain_stage = False
        for i, line in enumerate(lines):
            stripped = line.strip().lower()
            if stripped.startswith(".subckt") and "gain_stage" in stripped:
                in_gain_stage = True
            elif in_gain_stage and stripped.startswith(".ends"):
                in_gain_stage = False
            elif in_gain_stage and "cmirror" in stripped:
                line = _re.sub(r" l=[\d.e+-]+", f" l={cs_l}", line)
                line = _re.sub(r" w=[\d.e+-]+", f" w={cs_w}", line)
                line = _re.sub(r" m=[\d.e+-]+", f" m={cs_fingers}", line)
                lines[i] = line
                logger.info(
                    "CS_BIAS fix: patched GAIN_STAGE CMIRROR "
                    "to w=%.1f l=%.1f m=%d",
                    cs_w, cs_l, cs_fingers,
                )

        return "\n".join(lines)

    def _build_parasitic_cap_lines(
        self,
        parasitic_caps: list,
        port_map: dict[str, str],
    ) -> list[str]:
        """Convert ParasiticCap list to SPICE cap element lines.

        Port-to-port caps become direct coupling capacitors.
        Port-to-internal caps are lumped to GND on the port node.
        """
        cap_lines: list[str] = []
        lumped: dict[str, float] = {}
        port_set = {p.upper() for p in self._GLAYOUT_PORTS}

        for i, cap in enumerate(parasitic_caps):
            n1_is_port = cap.net1.upper() in port_set
            n2_is_port = cap.net2.upper() in port_set

            if n1_is_port and n2_is_port:
                node1 = port_map.get(cap.net1.upper(), cap.net1)
                node2 = port_map.get(cap.net2.upper(), cap.net2)
                if node1 != node2:
                    cap_lines.append(
                        f"Cp{i} {node1} {node2} {cap.value_fF:.4f}f"
                    )
            elif n1_is_port:
                key = cap.net1.upper()
                lumped[key] = lumped.get(key, 0.0) + cap.value_fF
            elif n2_is_port:
                key = cap.net2.upper()
                lumped[key] = lumped.get(key, 0.0) + cap.value_fF

        for port_name, total_fF in sorted(lumped.items()):
            node = port_map.get(port_name, port_name.lower())
            cap_lines.append(
                f"Cp_load_{port_name.lower()} {node} 0 {total_fF:.4f}f"
            )

        return cap_lines

    # Port mapping for gLayout NMOS-input OTA testbenches.
    # VP is the inverting input, VN is the non-inverting input.
    # DIFFPAIR_BIAS and CS_BIAS get separate bias nodes.
    _GLAYOUT_PORT_MAP = {
        "VDD": "VDD",
        "GND": "0",
        "DIFFPAIR_BIAS": "nb_dp",
        "VP": "inn",
        "VN": "inp",
        "CS_BIAS": "nb_cs",
        "VOUT": "vout",
    }

    # Hybrid port map: gLayout port names -> pre-layout OTA node names.
    # Both bias pins (DIFFPAIR_BIAS, CS_BIAS) map to a single "nb" node
    # because the pre-layout OTA uses one bias mirror (M9 diode -> M5/M7).
    _HYBRID_PORT_MAP = {
        "VDD": "VDD",
        "GND": "0",
        "DIFFPAIR_BIAS": "nb",
        "CS_BIAS": "nb",
        "VP": "inn",
        "VN": "inp",
        "VOUT": "vout",
    }

    def _glayout_ac_testbench_lines(
        self,
        fixed_netlist: Path,
        sizing: dict,
        parasitic_cap_lines: list[str] | None = None,
        dat_prefix: str = "gf180_ota_baseline",
    ) -> list[str]:
        """Build AC testbench lines wrapping a preprocessed gLayout netlist.

        Shared structure for both baseline (no parasitics) and overlay
        (with parasitic caps) testbenches.
        """
        Ibias = sizing["_Ibias"]
        VDD = sizing["_VDD"]
        VCM = sizing["_VCM"]

        subckt_name = self._find_subckt_name(fixed_netlist)

        if parasitic_cap_lines:
            title = f"Post-Layout AC Analysis (Overlay) - {self.pdk.display_name}"
        else:
            title = f"gLayout Baseline AC Analysis - {self.pdk.display_name}"

        lines = [
            title,
            "",
            *netlist_lib_lines(self.pdk),
            f".include {fixed_netlist}",
            "",
            "* Instantiate gLayout subcircuit (NMOS diff pair topology)",
            f"* Port order: {', '.join(self._GLAYOUT_PORTS)}",
            "* gLayout convention: VP=inverting, VN=non-inverting",
            f"X1 VDD 0 nb_dp inn inp nb_cs vout {subckt_name}",
            "",
            "* Bias: separate NMOS mirrors for diff-pair tail and CS output",
            f"Ibias_dp 0 nb_dp {Ibias:.4e}",
            f"Ibias_cs 0 nb_cs {Ibias:.4e}",
            "",
            "* DC operating point: inductor feedback (DC short, AC open)",
            "* sets inn=vout at DC, ensuring amplifier is in active region",
            "Lfb vout inn 1T",
            "",
            "* AC input on non-inverting input (VN=inp)",
            f"VVDD VDD 0 {VDD}",
            f"Vinp inp 0 DC={VCM} AC=1",
            "",
            "* Load capacitance",
            f"CL vout 0 {sizing['_CL']:.4e}",
        ]

        if parasitic_cap_lines:
            lines.append("")
            lines.append(
                f"* Parasitic caps from .ext file ({len(parasitic_cap_lines)} entries)"
            )
            lines.extend(parasitic_cap_lines)

        lines.extend([
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
            f"  wrdata {dat_prefix}.ac.dat AmagdB Aphdeg",
            ".endc",
            ".end",
        ])

        return lines

    # ------------------------------------------------------------------
    # gLayout testbench generators
    # ------------------------------------------------------------------

    def generate_postlayout_testbench_overlay(
        self,
        glayout_netlist_path: Path,
        parasitic_caps: list,
        sizing: dict[str, dict],
        work_dir: Path,
        glayout_params: dict | None = None,
    ) -> Path:
        """Generate AC testbench using gLayout netlist + parasitic cap overlay.

        When Magic's PEX extraction produces a degenerate netlist (due to
        missing internal GDS labels), this method uses gLayout's own
        hierarchical SPICE netlist as the circuit model and overlays
        parasitic capacitances parsed from the .ext file.

        Parameters
        ----------
        glayout_netlist_path : Path
            Path to gLayout's SPICE netlist (correct topology).
        parasitic_caps : list[ParasiticCap]
            Parasitic caps from ExtFileParser.parse_port_caps().
        sizing : dict
            Output from params_to_sizing() (for bias values).
        work_dir : Path
            Directory for output files.
        glayout_params : dict or None
            gLayout parameter dict for CS_BIAS fix.  If None, uses
            ``glayout_default_params()``.

        Returns
        -------
        Path
            Path to the .cir control file for SpiceRunner.
        """
        work_dir.mkdir(parents=True, exist_ok=True)
        glayout_netlist_path = Path(glayout_netlist_path).resolve()

        if glayout_params is None:
            glayout_params = self.glayout_default_params()

        fixed_netlist = self._preprocess_glayout_netlist(
            glayout_netlist_path, work_dir, glayout_params,
        )

        cap_lines = self._build_parasitic_cap_lines(
            parasitic_caps, self._GLAYOUT_PORT_MAP,
        )

        tb_lines = self._glayout_ac_testbench_lines(
            fixed_netlist,
            sizing,
            parasitic_cap_lines=cap_lines,
            dat_prefix="gf180_ota_postlayout_overlay",
        )

        cir_path = work_dir / "gf180_ota_postlayout_overlay.ac.cir"
        cir_path.write_text("\n".join(tb_lines) + "\n")
        return cir_path

    def generate_glayout_baseline_testbench(
        self,
        glayout_netlist_path: Path,
        sizing: dict[str, dict],
        work_dir: Path,
        glayout_params: dict | None = None,
    ) -> Path:
        """Generate AC testbench using gLayout netlist WITHOUT parasitics.

        This is the "pre-layout" reference for the gLayout topology: same
        preprocessed netlist (MIM fix, unit conversion, CS_BIAS fix), same
        bias and input structure, but no parasitic caps.  Comparing this
        against the overlay testbench isolates the impact of parasitics
        within the same topology.

        Parameters
        ----------
        glayout_netlist_path : Path
            Path to gLayout's SPICE netlist.
        sizing : dict
            Output from params_to_sizing() (for bias values).
        work_dir : Path
            Directory for output files.
        glayout_params : dict or None
            gLayout parameter dict for CS_BIAS fix.  If None, uses
            ``glayout_default_params()``.

        Returns
        -------
        Path
            Path to the .cir control file for SpiceRunner.
        """
        work_dir.mkdir(parents=True, exist_ok=True)
        glayout_netlist_path = Path(glayout_netlist_path).resolve()

        if glayout_params is None:
            glayout_params = self.glayout_default_params()

        fixed_netlist = self._preprocess_glayout_netlist(
            glayout_netlist_path, work_dir, glayout_params,
        )

        tb_lines = self._glayout_ac_testbench_lines(
            fixed_netlist,
            sizing,
            parasitic_cap_lines=None,
            dat_prefix="gf180_ota_baseline",
        )

        cir_path = work_dir / "gf180_ota_baseline.ac.cir"
        cir_path.write_text("\n".join(tb_lines) + "\n")
        return cir_path

    def generate_hybrid_postlayout_testbench(
        self,
        parasitic_caps: list,
        sizing: dict[str, dict],
        work_dir: Path,
    ) -> Path:
        """Generate AC testbench overlaying gLayout parasitics on pre-layout OTA.

        The hybrid approach uses the working pre-layout OTA netlist (PMOS-input,
        9 transistors) as the circuit, and adds parasitic capacitances extracted
        from the gLayout physical layout (NMOS-input, different topology).  Port
        names are translated via ``_HYBRID_PORT_MAP``.

        This decouples circuit correctness (our OTA) from physical parasitics
        (gLayout layout), sidestepping the gLayout topology's broken gain.

        Parameters
        ----------
        parasitic_caps : list[ParasiticCap]
            Parasitic caps from ExtFileParser.parse_port_caps().
        sizing : dict
            Output from params_to_sizing() (for netlist generation + bias).
        work_dir : Path
            Directory for output files.

        Returns
        -------
        Path
            Path to the hybrid post-layout .cir control file.
        """
        work_dir.mkdir(parents=True, exist_ok=True)

        # Generate the pre-layout netlist (circuit only, no .control)
        ac_cir_path = self.generate_netlist(sizing, work_dir)

        # Build parasitic cap lines using the hybrid port map
        cap_lines = self._build_parasitic_cap_lines(
            parasitic_caps, self._HYBRID_PORT_MAP,
        )

        # Read the generated AC testbench and inject parasitic caps
        ac_text = ac_cir_path.read_text()
        lines = ac_text.splitlines()

        # Insert cap lines between the .include and .control blocks
        insert_idx = None
        for i, line in enumerate(lines):
            if line.strip().startswith(".control"):
                insert_idx = i
                break

        if insert_idx is None:
            insert_idx = len(lines)

        hybrid_lines = []
        if cap_lines:
            hybrid_lines.append("")
            hybrid_lines.append(
                f"* Parasitic caps from gLayout PEX ({len(cap_lines)} entries)"
            )
            hybrid_lines.append("* Hybrid: gLayout physical parasitics on pre-layout OTA")
            hybrid_lines.extend(cap_lines)

        new_lines = lines[:insert_idx] + hybrid_lines + lines[insert_idx:]

        # Update title line
        if new_lines:
            new_lines[0] = f"Hybrid Post-Layout AC Analysis - {self.pdk.display_name}"

        # Update .dat output name
        new_lines = [
            line.replace("gf180_ota.ac.dat", "gf180_ota_hybrid_postlayout.ac.dat")
            for line in new_lines
        ]

        hybrid_cir_path = work_dir / "gf180_ota_hybrid_postlayout.ac.cir"
        hybrid_cir_path.write_text("\n".join(new_lines) + "\n")
        return hybrid_cir_path

    @staticmethod
    def _find_subckt_name(netlist_path: Path) -> str:
        """Extract the top-level .subckt name from a SPICE netlist.

        In hierarchical netlists the top-level cell is defined last,
        so we return the *last* .subckt name found.
        """
        text = netlist_path.read_text()
        last_name = None
        for line in text.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith(".subckt"):
                parts = line.split()
                if len(parts) >= 2:
                    last_name = parts[1]
        if last_name:
            return last_name
        # Fallback to filename stem
        return netlist_path.stem.replace(".rcx", "")

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
