"""11-bit SAR ADC — **design_reference**, not silicon-validated.

This module ships the first-class 11-bit SAR architecture template for
eda-agents. It is intentionally kept in schematic + ngspice +
Verilator-SAR-logic form:

  - the IHP Magic hang blocker (``docs/upstream_issues/ihp_magic_hang.md``)
    rules out a full signoff path in IHP today;
  - the 11-bit design has **no silicon validation** — the 8-bit SAR in
    :mod:`eda_agents.topologies.sar_adc_8bit` is the only SAR in-tree
    with a tested transistor-level netlist, so 11-bit must be treated
    as a reference architecture for agent exploration, not a drop-in
    production block.

Layout scope for S7:

  - ``SARADC11BitTopology(SystemTopology)`` composing the StrongARM
    comparator (via :class:`StrongARMComparatorTopology`) with an
    11-bit binary-weighted CMIM C-DAC, ideal bootstrap switches, and
    the Verilator-compiled :mod:`eda_agents.data.sar_logic_11bit`
    SAR finite-state machine.

  - Robustness checks in ``check_system_validity``: ENOB / SNDR /
    power + PVT range flag + metastability BER bound + supply ripple
    envelope + reference settling warning. Each individual check is
    a heuristic derived from the topology's own parameters — the
    intent is to surface design-space regions the agent should avoid,
    not to replace a full-corner sign-off sweep.

  - PDK-parametric via :func:`resolve_pdk`: IHP SG13G2 is the
    first target (PSP103 / ihp-gmid-kit available), GF180MCU comes
    for free because every PDK-specific call routes through
    :class:`PdkConfig`.

``EDA_AGENTS_PDK=ihp_sg13g2`` remains the default. There is **no
layout path** — Magic PEX for IHP is blocked upstream.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from eda_agents.core.pdk import (
    PdkConfig,
    netlist_lib_lines,
    netlist_osdi_lines,
    resolve_pdk,
)
from eda_agents.core.spice_runner import SpiceResult
from eda_agents.core.system_topology import SystemTopology
from eda_agents.core.topology import CircuitTopology
from eda_agents.tools import adc_metrics as _adc_metrics
from eda_agents.topologies.comparator_strongarm import StrongARMComparatorTopology
from eda_agents.topologies.sar_adc_netlist import (
    _default_nand_section,
    _default_strongarm_section,
    cmim_dimensions,
)
from eda_agents.utils.vlnggen import compile_verilog

logger = logging.getLogger(__name__)


# Default SAR logic Verilog source (shipped with the package).
_DEFAULT_VERILOG = (
    Path(__file__).resolve().parent.parent / "data" / "sar_logic_11bit.v"
)

# Spec targets. The 11-bit design_reference nominally wants ENOB ~9 on
# the ideal/behavioural path; the transistor-level StrongARM will drop
# it. Keep thresholds realistic so agents can measurably improve.
_SPEC_ENOB_MIN = 6.0
_SPEC_SNDR_MIN = 38.0      # dB (roughly 6.0 b -> 1.76 + 6*6.02)
_SPEC_POWER_MAX_UW = 400.0
_SPEC_FS_HZ = 1e6

# Coherent-sampling FFT parameters.
_N_FFT_SAMPLES = 128
_SINE_CYCLES = 9           # coprime with 128
_SINE_AMP = 0.25           # V (per side, differential)


class SARADC11BitTopology(SystemTopology):
    """11-bit binary-weighted SAR ADC system topology (design_reference).

    Design space (8-D system knobs):

      - 6 StrongARM comparator knobs (delegated to
        :class:`StrongARMComparatorTopology`):
          ``comp_W_input_um``, ``comp_L_input_um``,
          ``comp_W_tail_um``,  ``comp_L_tail_um``,
          ``comp_W_latch_p_um``, ``comp_W_latch_n_um``.
      - ``cdac_C_unit_fF`` — unit cap of the binary-weighted CMIM array.
        MSB = 1024 * C_unit, LSB = 1 * C_unit, plus one dummy.
      - ``bias_V`` — comparator tail bias voltage.

    The converter is clocked at 1 MHz with 10 resolution cycles per
    conversion (plus sample/hold), tuned so the Verilator SAR FSM has
    room for metastability-safety even at the low end of comparator
    sizing. FoM is the Walden FoM via :mod:`eda_agents.tools.adc_metrics`
    identical to the 8-bit variant.

    **Design reference only**: not silicon-validated. Treat it as a
    vehicle for agent-driven architecture exploration.
    """

    DESIGN_REFERENCE = True

    def __init__(
        self,
        verilog_src: Path | None = None,
        so_cache_dir: Path | None = None,
        pdk: PdkConfig | str | None = None,
    ):
        self.pdk = resolve_pdk(pdk)
        self._verilog_src = Path(verilog_src) if verilog_src else _DEFAULT_VERILOG
        self._so_cache_dir = Path(so_cache_dir) if so_cache_dir else None
        self._so_path: Path | None = None
        self._comp_topo = StrongARMComparatorTopology(pdk=self.pdk)

    # -- Helpers -------------------------------------------------------

    def _ensure_so(self, work_dir: Path) -> Path:
        if self._so_path is not None and self._so_path.is_file():
            return self._so_path
        cache_dir = self._so_cache_dir or work_dir
        candidate = cache_dir / f"{self._verilog_src.stem}.so"
        if candidate.is_file():
            self._so_path = candidate
            return candidate
        self._so_path = compile_verilog(self._verilog_src, cache_dir)
        return self._so_path

    # -- SystemTopology API --------------------------------------------

    def topology_name(self) -> str:
        return "sar_adc_11bit"

    def block_names(self) -> list[str]:
        return ["comparator", "cdac", "bias"]

    def block_topology(self, name: str) -> CircuitTopology | None:
        if name == "comparator":
            return self._comp_topo
        return None

    def system_design_space(self) -> dict[str, tuple[float, float]]:
        return {
            # 6 StrongARM knobs
            "comp_W_input_um":  (4.0, 64.0),
            "comp_L_input_um":  (0.13, 2.0),
            "comp_W_tail_um":   (4.0, 40.0),
            "comp_L_tail_um":   (0.13, 2.0),
            "comp_W_latch_p_um": (1.0, 16.0),
            "comp_W_latch_n_um": (1.0, 16.0),
            # CDAC unit cap
            "cdac_C_unit_fF":   (10.0, 200.0),
            # Bias
            "bias_V":           (0.4, self.pdk.VDD - 0.2),
        }

    def block_design_space(
        self, block_name: str
    ) -> dict[str, tuple[float, float]]:
        full = self.system_design_space()
        if block_name == "comparator":
            return {k: v for k, v in full.items() if k.startswith("comp_")}
        if block_name == "cdac":
            return {"cdac_C_unit_fF": full["cdac_C_unit_fF"]}
        if block_name == "bias":
            return {"bias_V": full["bias_V"]}
        raise ValueError(f"Unknown block: {block_name}")

    def params_to_block_params(
        self, system_params: dict[str, float]
    ) -> dict[str, dict[str, float]]:
        return {
            "comparator": {
                "W_input_um":  system_params["comp_W_input_um"],
                "L_input_um":  system_params["comp_L_input_um"],
                "W_tail_um":   system_params["comp_W_tail_um"],
                "L_tail_um":   system_params["comp_L_tail_um"],
                "W_latch_p_um": system_params["comp_W_latch_p_um"],
                "W_latch_n_um": system_params["comp_W_latch_n_um"],
            },
            "cdac": {"C_unit_fF": system_params["cdac_C_unit_fF"]},
            "bias": {"V": system_params["bias_V"]},
        }

    def default_params(self) -> dict[str, float]:
        return {
            "comp_W_input_um": 32.0,
            "comp_L_input_um": 0.2,
            "comp_W_tail_um": 18.0,
            "comp_L_tail_um": 0.3,
            "comp_W_latch_p_um": 8.0,
            "comp_W_latch_n_um": 4.0,
            "cdac_C_unit_fF": 50.0,
            "bias_V": 0.6,
        }

    # -- Netlist generation --------------------------------------------

    def generate_system_netlist(
        self,
        system_params: dict[str, float],
        work_dir: Path,
    ) -> Path:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        so_path = self._ensure_so(work_dir)

        blocks = self.params_to_block_params(system_params)
        comp_params = blocks["comparator"]
        cdac_C_unit_fF = float(blocks["cdac"]["C_unit_fF"])
        bias_V = float(blocks["bias"]["V"])

        VDD = self.pdk.VDD
        cap_model = self.pdk.mim_cap_model or "cap_cmim"
        cap_wl = cmim_dimensions(cdac_C_unit_fF, self.pdk.mim_cap_density_fF_um2)

        T = 1.0 / _SPEC_FS_HZ                    # period in s
        T_half = T / 2.0
        # 10 resolution cycles per conversion (plus sample/hold).
        T_algo = T / 22
        T_algo_PW = T / 44
        DAC_delay = 0.99 * T
        DAC_PW = T / 22

        total_sim_time = _N_FFT_SAMPLES * T
        vcm = VDD / 2.0
        f_in = _SINE_CYCLES * _SPEC_FS_HZ / _N_FFT_SAMPLES
        vin_pos = f'"dc 0 ac 0 SIN({vcm} {_SINE_AMP} {f_in} 0 0 0)"'
        vin_neg = f'"dc 0 ac 0 SIN({vcm} {_SINE_AMP} {f_in} 0 0 180)"'

        # D bus width = 11; B / BN width = 10; counter 5-bit.
        d_nodes = " ".join(f"D{i}_d" for i in range(10, -1, -1))
        b_nodes = " ".join(f"B{i}_d" for i in range(9, -1, -1))
        bn_nodes = " ".join(f"BN{i}_d" for i in range(9, -1, -1))
        d_wr = " ".join(f"D{i}" for i in range(11))

        comp_section = _default_strongarm_section(comp_params, self.pdk)
        nand_section = _default_nand_section(self.pdk)

        lines: list[str] = [
            f"* 11-bit SAR ADC (design_reference) - {self.pdk.display_name}",
            "* Generated by eda-agents SAR 11-bit topology",
            "* NOT silicon-validated. Schematic-only; IHP Magic blocker keeps",
            "* this pre-layout until upstream tapeout hangs are resolved.",
            "",
            *netlist_lib_lines(self.pdk),
            "",
            ".control",
            "  set ngbehavior=hsa",
            *netlist_osdi_lines(self.pdk),
            "",
            f"  tran {T_algo_PW/4:.4e} {total_sim_time:.4e}",
            "",
            "  * Digital codes and reference input for ENOB extraction",
            "  let vin_diff = v(vin_pos) - v(vin_neg)",
            "  set wr_singlescale",
            "  set wr_vecnames",
            f"  wrdata bit_data.txt {d_wr} vin_diff dac_clk",
            "",
            f"  meas tran avg_idd AVG i(VVDD) FROM=0 TO={total_sim_time:.4e}",
            "  meas tran td_comp TRIG v(clk_comp) VAL=0.6 RISE=1 "
            "TARG v(comp_outp) VAL=0.6 RISE=1",
            "",
            ".endc",
            "",
            "* -- supply / clocks / input ----------------------------------",
            f"VVDD vdd 0 {VDD}",
            f"Vclk_samp clk_samp 0 PULSE(0 {VDD} 0 10p 10p {T_half:.4e} {T:.4e})",
            f"Vclk_comp clk_comp 0 PULSE({VDD} 0 {T_half + 50e-9:.4e} 10p 10p "
            f"{T_algo_PW:.4e} {T_algo:.4e})",
            f"Vdac_clk dac_clk 0 PULSE(0 {VDD} {DAC_delay:.4e} 10p 10p "
            f"{DAC_PW:.4e} {T:.4e})",
            f"Vinp vin_pos 0 {vin_pos}",
            f"Vinn vin_neg 0 {vin_neg}",
            f"Vbias vbias 0 {bias_V}",
            "",
            "* -- STRONGARM comparator (pre-layout) ------------------------",
            *comp_section,
            "",
            "* -- SAR clock NAND -------------------------------------------",
            *nand_section,
            "",
            "* -- Bootstrap switches (ideal approximation) ----------------",
            "S_samp_p vin_pos cdac_top_p clk_samp 0 sw_ideal ON",
            "S_samp_n vin_neg cdac_top_n clk_samp 0 sw_ideal ON",
            f".model sw_ideal SW(VT={VDD/2} VH=0.1 RON=100 ROFF=1e12)",
            "",
            "* -- ADC bridges ----------------------------------------------",
            "Aadc_op  [comp_outp] [comp_op_d] adc_bridge_model",
            "Aadc_om  [comp_outn] [comp_om_d] adc_bridge_model",
            "Aadc_clk [clk_comp]  [clk_d]     adc_bridge_model",
            "Aadc_en  [vdd]       [en_d]      adc_bridge_model",
            "Aadc_rst [clk_samp]  [rst_d]     adc_bridge_model",
            ".model adc_bridge_model adc_bridge(in_low=0.2 in_high=0.8)",
            "",
            "* -- 11-bit SAR logic (Verilator d_cosim) --------------------",
            f"Adut [clk_d comp_op_d en_d comp_om_d rst_d]"
            f" [{b_nodes} {bn_nodes} {d_nodes}] null dut",
            f'.model dut d_cosim(simulation="{so_path}")',
            "",
            "* -- DAC bridges ----------------------------------------------",
        ]

        for i in range(11):
            lines.append(f"Adac_D{i} [D{i}_d] [D{i}] dac_bridge_model")
        for i in range(10):
            lines.append(f"Adac_B{i} [B{i}_d] [B{i}] dac_bridge_model")
            lines.append(f"Adac_BN{i} [BN{i}_d] [BN{i}] dac_bridge_model")

        lines.append(
            f".model dac_bridge_model dac_bridge(out_low=0.0 out_high={VDD})"
        )

        lines.extend(
            [
                "",
                "* -- 11-bit binary-weighted C-DAC (CMIM) ----------------",
                f"* Unit capacitor: W=L={cap_wl*1e6:.3f} um "
                f"({cdac_C_unit_fF:.1f} fF)",
                "",
                f"Vvcm vcm 0 {VDD / 2}",
                "",
                "* Positive C-DAC (top plate = cdac_top_p)",
            ]
        )

        # 11-bit binary array: weights 1024..1 plus a dummy equal to LSB.
        weights = [1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1, 1]
        # 10 decision bits + 1 dummy; map bit index i -> B{i}, with
        # the last (dummy) reusing the LSB switch.
        labels = [f"B{i}" if i < 10 else "B9" for i in range(11)] + ["B9"]
        labels_n = [f"BN{i}" if i < 10 else "BN9" for i in range(11)] + ["BN9"]
        # Positive side: BN -> VDD (+), B -> GND (-); match 8-bit polarity.
        for i, (w, lab, labn) in enumerate(zip(weights, labels_n, labels)):
            bot = f"cdac_bot_p_{i}"
            lines.append(f"* Cap {i} (weight={w}C)")
            lines.append(f"S_samp_bp_{i} {bot} vcm clk_samp 0 sw_samp ON")
            lines.append(f"S_vdd_p_{i} {bot} vdd {lab} 0 sw_cdac ON")
            lines.append(f"S_gnd_p_{i} {bot} 0 {labn} 0 sw_cdac ON")
            lines.append(f"R_pull_p_{i} {bot} vcm 100k")
            lines.append(
                f"XC_cdac_p_{i} cdac_top_p {bot} {cap_model} "
                f"w={cap_wl:.4e} l={cap_wl:.4e} m={w}"
            )
            lines.append("")

        lines.append("* Negative C-DAC (top plate = cdac_top_n)")
        for i, (w, lab, labn) in enumerate(zip(weights, labels, labels_n)):
            bot = f"cdac_bot_n_{i}"
            lines.append(f"S_samp_bn_{i} {bot} vcm clk_samp 0 sw_samp ON")
            lines.append(f"S_vdd_n_{i} {bot} vdd {lab} 0 sw_cdac ON")
            lines.append(f"S_gnd_n_{i} {bot} 0 {labn} 0 sw_cdac ON")
            lines.append(f"R_pull_n_{i} {bot} vcm 100k")
            lines.append(
                f"XC_cdac_n_{i} cdac_top_n {bot} {cap_model} "
                f"w={cap_wl:.4e} l={cap_wl:.4e} m={w}"
            )
            lines.append("")

        lines.extend(
            [
                f".model sw_samp SW(VT={VDD/2} VH=0.1 RON=100 ROFF=1e12)",
                f".model sw_cdac SW(VT={VDD/2} VH=0.1 RON=50 ROFF=1e12)",
                "",
                ".end",
            ]
        )

        cir_path = work_dir / "sar_adc_11bit.cir"
        cir_path.write_text("\n".join(lines) + "\n")
        return cir_path

    # -- FoM / validity -------------------------------------------------

    def compute_system_fom(
        self,
        spice_result: SpiceResult,
        system_params: dict[str, float],
    ) -> float:
        m = spice_result.measurements
        enob = m.get("enob")
        avg_idd = m.get("avg_idd")
        if not enob or enob <= 0 or avg_idd is None:
            return 0.0
        f_s = _SPEC_FS_HZ
        power_w = self.pdk.VDD * abs(avg_idd)
        if power_w <= 0:
            return 0.0
        try:
            walden_fj = _adc_metrics.calculate_walden_fom(
                power_w=power_w, fs=f_s, enob=enob
            )
            fom = 1e15 / walden_fj if walden_fj > 0 else 0.0
        except ImportError:
            fom = (2**enob) * f_s / power_w
        valid, violations = self.check_system_validity(spice_result, system_params)
        penalty = 1.0 if valid else max(0.01, 1.0 - 0.1 * len(violations))
        return fom * penalty

    def check_system_validity(
        self,
        spice_result: SpiceResult,
        system_params: dict[str, float],
    ) -> tuple[bool, list[str]]:
        """Static + measurement-driven robustness gates.

        The 11-bit converter is a design_reference with no silicon, so
        the checks below are heuristics designed to flag obviously
        brittle design-space regions. They are:

          - ENOB / SNDR / power threshold (measurement driven).
          - PVT margin: StrongARM offset (Pelgrom) vs 0.5 LSB of the
            full-scale range.
          - Metastability BER bound: comparator regeneration time constant
            must be < evaluate-phase budget with healthy margin.
          - Supply ripple envelope: average IDD spike must stay below a
            CDAC-switching-budget heuristic so we do not push the LDO /
            decap sizing off a cliff.
          - Reference settling: CDAC time constant vs. one algorithm
            cycle.
        """
        violations: list[str] = []
        if not spice_result.success:
            return (False, ["simulation failed"])
        m = spice_result.measurements

        enob = m.get("enob")
        sndr = m.get("sndr_dB")
        if enob is not None and enob < _SPEC_ENOB_MIN:
            violations.append(f"ENOB={enob:.2f} < {_SPEC_ENOB_MIN}")
        if sndr is not None and sndr < _SPEC_SNDR_MIN:
            violations.append(f"SNDR={sndr:.1f}dB < {_SPEC_SNDR_MIN}dB")

        avg_idd = m.get("avg_idd")
        if avg_idd is not None:
            power_uw = self.pdk.VDD * abs(avg_idd) * 1e6
            if power_uw > _SPEC_POWER_MAX_UW:
                violations.append(
                    f"Power={power_uw:.1f}uW > {_SPEC_POWER_MAX_UW:.1f}uW"
                )

        # PVT margin: 0.5 LSB on a full-scale differential swing.
        vfs = system_params.get("cmp_vout_high", self.pdk.VDD)
        lsb_v = vfs / (2**11)
        W = system_params["comp_W_input_um"]
        L = system_params["comp_L_input_um"]
        sigma_vos = (
            self.pdk.AVT_pmos_Vum / math.sqrt(max(W * L, 1e-9))
        )
        if sigma_vos > 0.5 * lsb_v:
            violations.append(
                f"PVT margin: sigma_Vos={sigma_vos*1e3:.2f}mV > 0.5 LSB "
                f"({0.5*lsb_v*1e3:.2f}mV) @ 11b FS"
            )

        # Metastability BER bound: tau_regen < 0.4 * T_algo_PW.
        T_algo_pw = 1.0 / _SPEC_FS_HZ / 44
        # tau_regen ~ Cout / gm; approx gm via W/L scaling of the latch.
        W_lp = system_params["comp_W_latch_p_um"]
        tau_regen = 20e-12 / max(W_lp / 8.0, 0.1)  # heuristic
        if tau_regen > 0.4 * T_algo_pw:
            violations.append(
                f"Metastability: tau_regen~{tau_regen*1e12:.1f}ps "
                f"vs. budget 0.4*T_algo_PW={0.4*T_algo_pw*1e12:.1f}ps"
            )

        # Supply ripple: CDAC switching current < 1/3 of power budget.
        C_unit_fF = system_params["cdac_C_unit_fF"]
        # Total CDAC cap one side ~ (2^11) * C_unit
        C_total_pF = (2**11) * C_unit_fF * 1e-3
        q_switch_uC = C_total_pF * self.pdk.VDD * 1e-6
        i_peak_ma = q_switch_uC / T_algo_pw  # mA equiv
        if i_peak_ma > 2.0:
            violations.append(
                f"Supply ripple: CDAC peak i~{i_peak_ma:.2f}mA exceeds "
                "2 mA envelope; decap sizing will dominate"
            )

        # Reference settling: tau = R_on * C_total < T_algo_PW.
        R_on = 50.0  # ohms (sw_cdac model)
        tau_cdac_s = R_on * C_total_pF * 1e-12
        if tau_cdac_s > T_algo_pw / 3.0:
            violations.append(
                f"Reference settling: tau={tau_cdac_s*1e9:.2f}ns > "
                f"T_algo_PW/3 = {T_algo_pw*1e9/3:.2f}ns"
            )

        return (len(violations) == 0, violations)

    def extract_enob(self, work_dir: Path) -> dict[str, float]:
        """Parse bit_data.txt (D0..D10) -> ADCToolbox metrics dict.

        Output shape matches
        :meth:`~eda_agents.topologies.sar_adc_8bit.SARADCTopology.extract_enob`
        but reconstructs an 11-bit code rather than 8 bits. Numpy and
        ADCToolbox are imported lazily so tests that skip SPICE never
        pull heavy deps.
        """
        import numpy as np

        bit_file = work_dir / "bit_data.txt"
        if not bit_file.exists():
            return {"enob": 0.0, "sndr_dB": 0.0, "error": "no bit_data.txt"}
        data = np.loadtxt(str(bit_file), skiprows=1)
        if data.ndim < 2 or data.shape[1] < 13:
            return {
                "enob": 0.0,
                "sndr_dB": 0.0,
                "error": "insufficient columns",
            }
        # Columns are (t, D0..D10, vin_diff, dac_clk). wrdata emits pairs
        # (time, value) per variable so the exact column indices depend
        # on ngspice; the 8-bit path uses the same convention at offset
        # 1..9 and trailing dac_clk — we mirror that.
        D_raw = data[:, 1:12]
        dac_clk = data[:, 13] if data.shape[1] >= 14 else data[:, -1]
        threshold = 0.6
        edges: list[int] = []
        for i in range(1, len(dac_clk)):
            if dac_clk[i - 1] < threshold <= dac_clk[i]:
                edges.append(i)
        if len(edges) < _N_FFT_SAMPLES:
            return {
                "enob": 0.0,
                "sndr_dB": 0.0,
                "error": f"only {len(edges)} samples (need {_N_FFT_SAMPLES})",
            }
        codes: list[int] = []
        for idx in edges[:_N_FFT_SAMPLES]:
            bits = [1 if D_raw[idx, i] > 0.6 else 0 for i in range(11)]
            # bits[0] = LSB (column D0), so weight 2^i. The Verilog
            # `D <= D | ({10'b0, Op} << counter)` places MSB at the
            # counter=0 iteration but writes into D[counter]; reviewers:
            # see data/sar_logic_11bit.v comment block for the convention.
            code = sum(bits[i] * (1 << i) for i in range(11))
            codes.append(code)
        arr = np.array(codes, dtype=float)
        N = len(arr)
        base = {
            "n_samples": N,
            "code_min": int(arr.min()),
            "code_max": int(arr.max()),
            "unique_codes": int(len(set(codes))),
            "code_span": int(arr.max() - arr.min()),
        }
        fin = _SINE_CYCLES * _SPEC_FS_HZ / _N_FFT_SAMPLES
        try:
            metrics = _adc_metrics.compute_adc_metrics(
                arr - arr.mean(),
                fs=_SPEC_FS_HZ,
                num_bits=11,
                win_type="boxcar",
                include_inl=False,
                fin_target_hz=fin,
            )
            enob = max(0.0, float(metrics["enob"] or 0.0))
            sndr = round(float(metrics["sndr_dbc"] or 0.0), 2)
            out: dict[str, float] = dict(base)
            out.update({"enob": enob, "sndr_dB": sndr})
            for src, dst in (
                ("sfdr_dbc", "sfdr_dB"),
                ("thd_dbc", "thd_dB"),
                ("snr_dbc", "snr_dB"),
            ):
                if metrics.get(src) is not None:
                    out[dst] = round(float(metrics[src]), 2)
            return out
        except ImportError:
            pass
        # Rectangular-window fallback (ADCToolbox absent).
        spectrum = np.abs(np.fft.fft(arr - arr.mean()))[: N // 2]
        sig_bin = _SINE_CYCLES
        signal_p = spectrum[sig_bin] ** 2
        noise_p = sum(
            spectrum[i] ** 2 for i in range(1, N // 2) if i != sig_bin
        )
        if noise_p <= 0 or signal_p <= 0:
            return {"enob": 0.0, "sndr_dB": 0.0, **base}
        sndr_db = 10 * math.log10(signal_p / noise_p)
        enob = (sndr_db - 1.76) / 6.02
        return {
            "enob": max(0.0, enob),
            "sndr_dB": round(sndr_db, 2),
            **base,
        }

    # -- Prompt metadata ------------------------------------------------

    def prompt_description(self) -> str:
        return (
            f"11-bit SAR ADC on {self.pdk.display_name}. Design reference "
            "template (not silicon-validated). StrongARM dynamic comparator "
            "drives a binary-weighted CMIM CDAC (1024..1 + dummy). SAR FSM "
            "runs on Verilator via d_cosim for a 10-cycle conversion. "
            "Targets: 1 MHz sample rate, 6+ ENOB with margin for PVT."
        )

    def design_vars_description(self) -> str:
        return (
            "- comp_W_input_um: StrongARM input-pair PMOS width [4-64 um]. "
            "Dominates Pelgrom offset vs 0.5 LSB at 11 bits (critical).\n"
            "- comp_L_input_um: input-pair length [0.13-2.0 um]. Matching vs speed.\n"
            "- comp_W_tail_um: tail / bias PMOS width [4-40 um]. Current headroom.\n"
            "- comp_L_tail_um: tail length [0.13-2.0 um]. Current matching.\n"
            "- comp_W_latch_p_um: PMOS latch width [1-16 um]. Regen speed vs. "
            "metastability bound.\n"
            "- comp_W_latch_n_um: NMOS latch width [1-16 um]. Reset speed and "
            "kickback.\n"
            "- cdac_C_unit_fF: unit cap [10-200 fF]. Larger = better kT/C "
            "but tighter settling budget.\n"
            "- bias_V: tail bias [0.4 .. VDD-0.2 V]. Higher = faster but hotter."
        )

    def specs_description(self) -> str:
        return (
            f"ENOB >= {_SPEC_ENOB_MIN:.0f} bits, "
            f"SNDR >= {_SPEC_SNDR_MIN:.0f} dB, "
            f"Power <= {_SPEC_POWER_MAX_UW:.0f} uW, "
            f"f_s = {_SPEC_FS_HZ/1e6:.0f} MHz"
        )

    def fom_description(self) -> str:
        return (
            "Walden FoM = 2^ENOB * f_s / P_total (via tools.adc_metrics). "
            "Penalty scales with the number of failed robustness gates "
            "so agents are discouraged from tuning purely for raw ENOB."
        )

    def reference_description(self) -> str:
        return (
            "Reference (design_reference, no silicon): StrongARM "
            "W_input=32 um / L_input=200 n, tail 18 um / 300 n, latch "
            "8 um PMOS + 4 um NMOS, C_unit=50 fF, bias=0.6 V. Use this "
            "point to calibrate autoresearch — expect ENOB~8 on the "
            "ideal-comparator ceiling, lower once StrongARM kickback "
            "and offset kick in."
        )

    def inter_block_constraints(self) -> list[str]:
        return [
            "Comparator offset must stay under 0.5 LSB of the CDAC "
            "full scale (enforced by check_system_validity).",
            "CDAC total capacitance scales 2^11 * C_unit; the product "
            "with sw_cdac R_on must settle within the algorithm half-period.",
            "StrongARM latch regeneration constant must be smaller than "
            "~40% of the evaluation pulse width to bound the metastability BER.",
            "Bias voltage crosses both the comparator (tail) and the "
            "supply-current budget; moving it affects power and speed.",
        ]

    def exploration_hints(self) -> dict[str, int | float]:
        return {
            "evals_per_round": 2,
            "min_rounds": 5,
            "convergence_threshold": 0.03,
            "partition_dim": "cdac_C_unit_fF",
        }


__all__ = ["SARADC11BitTopology"]
