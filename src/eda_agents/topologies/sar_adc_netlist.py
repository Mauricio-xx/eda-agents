"""SAR ADC mixed-signal netlist generator.

Generates a standalone ngspice netlist for an 8-bit SAR ADC using:
  - StrongARM dynamic comparator (analog models)
  - Charge redistribution C-DAC (MIM capacitors)
  - SAR logic (digital, Verilator d_cosim via vlnggen)
  - Bootstrap switch approximated as ideal switch
  - NAND gate for clock generation (analog transistors)
  - ADC/DAC bridges for mixed-signal interface

Supports any PDK via PdkConfig (defaults to IHP SG13G2).
Reference: IHP-AnalogAcademy Module 3 - 8-bit SAR ADC
"""

from __future__ import annotations

import math
from pathlib import Path

from eda_agents.core.pdk import PdkConfig, netlist_lib_lines, netlist_osdi_lines, resolve_pdk


def _ng(W: float, max_finger_w: float = 10e-6) -> int:
    """Compute number of fingers."""
    return max(1, round(W / max_finger_w))


def cmim_dimensions(C_fF: float, density_fF_um2: float = 1.5) -> float:
    """Compute CMIM cap W=L dimension for target capacitance.

    Returns W (= L) in meters. CMIM is square.
    """
    C_F = C_fF * 1e-15
    cap_per_area = density_fF_um2 * 1e-15 / 1e-12  # F/m^2
    area_m2 = C_F / cap_per_area
    if area_m2 <= 0:
        return 1e-6  # minimum 1um
    return math.sqrt(area_m2)


def _default_strongarm_section(
    comp_params: dict[str, float],
    _pdk: PdkConfig,
) -> list[str]:
    """Build the default transistor-level StrongARM comparator block.

    Returned lines go between the "COMPARATOR BIAS" header and the
    "NAND GATE" header in the SAR deck. Kept as a standalone helper so
    behavioural variants can swap it for XSPICE / Verilog-A equivalents
    via ``generate_sar_adc_netlist(..., comparator_section=...)``.
    """
    W_input = comp_params["W_input_um"] * 1e-6
    L_input = comp_params["L_input_um"] * 1e-6
    W_tail = comp_params["W_tail_um"] * 1e-6
    L_tail = comp_params["L_tail_um"] * 1e-6
    W_lp = comp_params["W_latch_p_um"] * 1e-6
    W_ln = comp_params["W_latch_n_um"] * 1e-6
    L_latch = max(200e-9, _pdk.Lmin_m)

    ng_input = _ng(W_input)
    ng_tail = _ng(W_tail)

    z1 = _pdk.z1_m
    pmos = _pdk.pmos_symbol
    nmos = _pdk.nmos_symbol
    px = _pdk.instance_prefix

    def _junc(W: float) -> str:
        AS = W * z1
        PS = 2 * (W + z1)
        return f"AS={AS:.3e} PS={PS:.3e} AD={AS:.3e} PD={PS:.3e}"

    def _dev(W: float, L: float, ng: int = 1) -> str:
        return f"w={W:.4e} l={L:.4e} ng={ng} m=1 {_junc(W)}"

    return [
        "* Bias current source PMOS (M3)",
        f"{px}M3  comp_net2 vbias vdd  vdd {pmos} {_dev(W_tail, L_tail, ng_tail)}",
        "",
        "* Clock tail switch PMOS (M13) -- clk_comp controls evaluation",
        f"{px}M13 comp_net1 clk_comp comp_net2 vdd {pmos} {_dev(W_tail, L_tail, ng_tail)}",
        "",
        "* Input pair PMOS (connected to C-DAC top plates)",
        f"{px}M2  comp_net4 cdac_top_p comp_net1 vdd {pmos} {_dev(W_input, L_input, ng_input)}",
        f"{px}M1  comp_net3 cdac_top_n comp_net1 vdd {pmos} {_dev(W_input, L_input, ng_input)}",
        "",
        "* PMOS output latch inverters",
        f"{px}M4  comp_outn comp_net3 vdd vdd {pmos} {_dev(W_lp, L_latch)}",
        f"{px}M5  comp_outp comp_net4 vdd vdd {pmos} {_dev(W_lp, L_latch)}",
        "",
        "* NMOS output latch inverters",
        f"{px}M11 0 comp_net4 comp_outp 0 {nmos} {_dev(W_ln, L_latch)}",
        f"{px}M12 0 comp_net3 comp_outn 0 {nmos} {_dev(W_ln, L_latch)}",
        "",
        "* NMOS cross-coupled (first-stage regeneration)",
        f"{px}M6  0 comp_net3 comp_net4 0 {nmos} {_dev(W_ln, L_latch)}",
        f"{px}M8  0 comp_net4 comp_net3 0 {nmos} {_dev(W_ln, L_latch)}",
        "",
        "* NMOS reset switches",
        f"{px}M7  0 clk_comp comp_net3 0 {nmos} {_dev(W_ln, L_latch)}",
        f"{px}M10 0 clk_comp comp_net4 0 {nmos} {_dev(W_ln, L_latch)}",
    ]


def _default_nand_section(_pdk: PdkConfig) -> list[str]:
    """Default transistor-level NAND for SAR clock generation."""
    px = _pdk.instance_prefix
    pmos = _pdk.pmos_symbol
    nmos = _pdk.nmos_symbol
    z1 = _pdk.z1_m
    Lmin = _pdk.Lmin_m

    def _junc(W: float) -> str:
        AS = W * z1
        PS = 2 * (W + z1)
        return f"AS={AS:.3e} PS={PS:.3e} AD={AS:.3e} PD={PS:.3e}"

    def _dev(W: float, L: float) -> str:
        return f"w={W:.4e} l={L:.4e} ng=1 m=1 {_junc(W)}"

    return [
        "* Simple CMOS NAND: when both comp outputs valid, generate clock edge",
        f"{px}Mn_nand1 nand_mid comp_outp 0     0   {nmos} {_dev(0.25e-6, Lmin)}",
        f"{px}Mn_nand2 clk_algo comp_outn nand_mid 0   {nmos} {_dev(0.25e-6, Lmin)}",
        f"{px}Mp_nand1 clk_algo comp_outp vdd   vdd {pmos} {_dev(0.5e-6, Lmin)}",
        f"{px}Mp_nand2 clk_algo comp_outn vdd   vdd {pmos} {_dev(0.5e-6, Lmin)}",
    ]


def generate_sar_adc_netlist(
    comp_params: dict[str, float],
    cdac_C_unit_fF: float,
    bias_V: float,
    T_period_us: float,
    work_dir: Path,
    so_path: Path,
    n_samples: int = 8,
    input_mode: str = "dc",
    vin_dc_pos: float = 0.8,
    vin_dc_neg: float = 0.4,
    vin_sine_amp: float = 0.3,
    vin_sine_freq_hz: float = 12700.0,
    pdk: PdkConfig | str | None = None,
    comparator_section: list[str] | None = None,
    nand_section: list[str] | None = None,
    extra_model_lines: list[str] | None = None,
) -> Path:
    """Generate a complete 8-bit SAR ADC mixed-signal SPICE netlist.

    Parameters
    ----------
    comp_params : dict
        Comparator design parameters:
          W_input_um, L_input_um, W_tail_um, L_tail_um,
          W_latch_p_um, W_latch_n_um
    cdac_C_unit_fF : float
        Unit capacitance for C-DAC in femtofarads.
    bias_V : float
        Comparator bias voltage (gate of M3 current source).
    T_period_us : float
        Base sampling period in microseconds.
    work_dir : Path
        Output directory.
    so_path : Path
        Path to compiled sar_logic.so shared library.
    n_samples : int
        Number of ADC samples to simulate.
    input_mode : str
        "dc" for constant differential input, "sine" for sinusoidal.
    vin_dc_pos, vin_dc_neg : float
        DC input voltages.
    vin_sine_amp : float
        Sine amplitude (centered at VDD/2).
    vin_sine_freq_hz : float
        Sine frequency for AC analysis.

    Returns
    -------
    Path
        Path to the generated .cir file.
    """
    _pdk = resolve_pdk(pdk)
    VDD = _pdk.VDD
    cap_model = _pdk.mim_cap_model or "cap_cmim"

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # --- Timing ---
    T = T_period_us * 1e-6  # base period in seconds
    T_half = T / 2
    T_algo = T / 16          # SAR algorithm clock
    T_algo_PW = T / 32       # algorithm pulse width
    # comparator-clock delay `0.328 * T` is embedded directly in the
    # Vclk_comp PULSE source below; no need for a named constant.
    DAC_delay = 0.99 * T     # DAC reconstruction clock delay
    DAC_PW = T / 20

    total_sim_time = n_samples * T

    # --- C-DAC dimensions ---
    cap_wl = cmim_dimensions(cdac_C_unit_fF, _pdk.mim_cap_density_fF_um2)

    # --- Input sources ---
    vcm = VDD / 2
    if input_mode == "sine":
        vin_pos = f'"dc 0 ac 0 SIN({vcm} {vin_sine_amp} {vin_sine_freq_hz} 0 0 0)"'
        vin_neg = f'"dc 0 ac 0 SIN({vcm} {vin_sine_amp} {vin_sine_freq_hz} 0 0 180)"'
    else:
        vin_pos = f"{vin_dc_pos}"
        vin_neg = f"{vin_dc_neg}"

    lines = [
        f"* 8-bit SAR ADC - {_pdk.display_name} Mixed-Signal Simulation",
        "* Generated by eda-agents SAR ADC infrastructure",
        "",
        *netlist_lib_lines(_pdk),
        "",
        ".control",
        "  set ngbehavior=hsa",
        *netlist_osdi_lines(_pdk),
        "",
        f"  tran {T:.4e} {total_sim_time:.4e}",
        "",
        "  * Extract digital outputs and input for post-processing",
        "  let vin_diff = v(vin_pos) - v(vin_neg)",
        "  set wr_singlescale",
        "  set wr_vecnames",
        "  wrdata bit_data.txt D0 D1 D2 D3 D4 D5 D6 D7 vin_diff dac_clk",
        "",
        "  * Measure average supply current for power",
        f"  meas tran avg_idd AVG i(VVDD) FROM=0 TO={total_sim_time:.4e}",
        "",
        "  * Measure comparator decision delay (first conversion)",
        "  meas tran td_comp TRIG v(clk_comp) VAL=0.6 RISE=1 TARG v(comp_outp) VAL=0.6 RISE=1",
        "",
        ".endc",
        "",
        "* ============================================================",
        "* POWER SUPPLY",
        "* ============================================================",
        f"VVDD vdd 0 {VDD}",
        "",
        "* ============================================================",
        "* CLOCK GENERATION",
        "* ============================================================",
        "",
        f"* Sampling clock: T={T*1e6:.2f}us",
        f"Vclk_samp clk_samp 0 PULSE(0 {VDD} 0 10p 10p {T_half:.4e} {T:.4e})",
        "",
        "* Comparator clock: HIGH=reset, LOW=evaluate",
        "* Starts HIGH (reset during sampling), first evaluate 50ns after sampling ends",
        "* to let C-DAC bottom plates settle before comparator draws charge",
        f"Vclk_comp clk_comp 0 PULSE({VDD} 0 {T_half + 50e-9:.4e} 10p 10p {T_algo_PW:.4e} {T_algo:.4e})",
        "",
        "* DAC reconstruction clock (for post-processing)",
        f"Vdac_clk dac_clk 0 PULSE(0 {VDD} {DAC_delay:.4e} 10p 10p {DAC_PW:.4e} {T:.4e})",
        "",
        "* ============================================================",
        "* INPUT SOURCES",
        "* ============================================================",
        f"Vinp vin_pos 0 {vin_pos}",
        f"Vinn vin_neg 0 {vin_neg}",
        "",
        "* ============================================================",
        "* COMPARATOR BIAS",
        "* ============================================================",
        f"Vbias vbias 0 {bias_V}",
        "",
        "* ============================================================",
        (
            "* STRONGARM DYNAMIC COMPARATOR"
            if comparator_section is None
            else "* COMPARATOR (caller-supplied section)"
        ),
        "* ============================================================",
        "",
        *(
            comparator_section
            if comparator_section is not None
            else _default_strongarm_section(comp_params, _pdk)
        ),
        "",
        "* ============================================================",
        "* NAND GATE (generates SAR clock from comparator outputs)",
        "* ============================================================",
        *(
            nand_section
            if nand_section is not None
            else _default_nand_section(_pdk)
        ),
        *(extra_model_lines or []),
        "",
        "* ============================================================",
        "* BOOTSTRAP SWITCH (ideal approximation)",
        "* Input sampling onto C-DAC top plate during sample phase",
        "* ============================================================",
        "* Ideal switches: closed when clk_samp=high (sampling phase)",
        "S_samp_p vin_pos cdac_top_p clk_samp 0 sw_ideal ON",
        "S_samp_n vin_neg cdac_top_n clk_samp 0 sw_ideal ON",
        f".model sw_ideal SW(VT={VDD/2} VH=0.1 RON=100 ROFF=1e12)",
        "",
        "* ============================================================",
        "* ADC BRIDGES (analog -> digital domain)",
        "* ============================================================",
        "",
        "* Comparator outputs to SAR logic",
        "Aadc_op [comp_outp] [comp_op_d] adc_bridge_model",
        "Aadc_om [comp_outn] [comp_om_d] adc_bridge_model",
        "",
        "* SAR clock: use clk_comp rising edge (end of evaluate phase)",
        "* At posedge clk_comp, comparator has been evaluating for T_algo_PW (~31ns),",
        "* outputs are fully resolved. SAR captures Op/Om at this moment.",
        "Aadc_clk [clk_comp] [clk_d] adc_bridge_model",
        "Aadc_en [vdd] [en_d] adc_bridge_model",
        "Aadc_rst [clk_samp] [rst_d] adc_bridge_model",
        "",
        ".model adc_bridge_model adc_bridge(in_low=0.2 in_high=0.8)",
        "",
        "* ============================================================",
        "* SAR LOGIC (d_cosim: Verilator-compiled Verilog)",
        "* ============================================================",
        "",
        "* Port order matches Verilator outputs.h: B[6:0] BN[6:0] D[7:0]",
        "* Each bus MSB-first: B6..B0, BN6..BN0, D7..D0",
        "* Inputs: clk Op En Om rst (from inputs.h, all scalar)",
        "Adut [clk_d comp_op_d en_d comp_om_d rst_d]"
        " [B6_d B5_d B4_d B3_d B2_d B1_d B0_d"
        "  BN6_d BN5_d BN4_d BN3_d BN2_d BN1_d BN0_d"
        "  D7_d D6_d D5_d D4_d D3_d D2_d D1_d D0_d] null dut",
        f'.model dut d_cosim(simulation="{so_path}")',
        "",
        "* ============================================================",
        "* DAC BRIDGES (digital -> analog domain)",
        "* ============================================================",
        "",
        "* D bits (8-bit output for measurement/post-processing)",
        "Adac_D0 [D0_d] [D0] dac_bridge_model",
        "Adac_D1 [D1_d] [D1] dac_bridge_model",
        "Adac_D2 [D2_d] [D2] dac_bridge_model",
        "Adac_D3 [D3_d] [D3] dac_bridge_model",
        "Adac_D4 [D4_d] [D4] dac_bridge_model",
        "Adac_D5 [D5_d] [D5] dac_bridge_model",
        "Adac_D6 [D6_d] [D6] dac_bridge_model",
        "Adac_D7 [D7_d] [D7] dac_bridge_model",
        "",
        "* B bits (switch control for C-DAC bottom plates)",
        "Adac_B0 [B0_d] [B0] dac_bridge_model",
        "Adac_B1 [B1_d] [B1] dac_bridge_model",
        "Adac_B2 [B2_d] [B2] dac_bridge_model",
        "Adac_B3 [B3_d] [B3] dac_bridge_model",
        "Adac_B4 [B4_d] [B4] dac_bridge_model",
        "Adac_B5 [B5_d] [B5] dac_bridge_model",
        "Adac_B6 [B6_d] [B6] dac_bridge_model",
        "",
        "* BN bits (complementary switch control)",
        "Adac_BN0 [BN0_d] [BN0] dac_bridge_model",
        "Adac_BN1 [BN1_d] [BN1] dac_bridge_model",
        "Adac_BN2 [BN2_d] [BN2] dac_bridge_model",
        "Adac_BN3 [BN3_d] [BN3] dac_bridge_model",
        "Adac_BN4 [BN4_d] [BN4] dac_bridge_model",
        "Adac_BN5 [BN5_d] [BN5] dac_bridge_model",
        "Adac_BN6 [BN6_d] [BN6] dac_bridge_model",
        "",
        f".model dac_bridge_model dac_bridge(out_low=0.0 out_high={VDD})",
        "",
        "* ============================================================",
        "* C-DAC: 8-bit charge redistribution (binary-weighted CMIM)",
        "* ============================================================",
        f"* Unit capacitor: W=L={cap_wl*1e6:.3f}um ({cdac_C_unit_fF:.1f} fF)",
        "",
        "* C-DAC with proper 3-state switching:",
        "* - Sampling phase (clk_samp=HIGH): bottom plates at Vcm via sampling switches",
        "* - Conversion: B/BN control VDD/GND switches, undecided bits float at Vcm",
        "",
        "* Common mode voltage for bottom plates during sampling",
        f"Vvcm vcm 0 {VDD / 2}",
        "",
        "* Positive C-DAC array (top plate = cdac_top_p)",
        "* Positive side: BN=1 -> VDD (increase V_top_p), B=1 -> GND (decrease V_top_p)",
        "* When Op=1: B=1, BN=0 -> pos bottom to GND (reduce positive = correct feedback)",
        "* When Op=0: B=0, BN=1 -> pos bottom to VDD (increase positive = correct feedback)",
        "* Undecided (B=0, BN=0): bottom floats at Vcm from sampling phase",
    ]

    # C-DAC: binary weighted MSB (64C) to LSB (1C) + dummy (1C)
    # B0/BN0 = MSB (first decided, counter=0), B6/BN6 = LSB
    weights = [64, 32, 16, 8, 4, 2, 1, 1]
    # Positive C-DAC: BN -> VDD, B -> GND
    bit_vdd_p = ["BN0", "BN1", "BN2", "BN3", "BN4", "BN5", "BN6", "BN6"]
    bit_gnd_p = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B6"]

    for i, (w, bvdd, bgnd) in enumerate(zip(weights, bit_vdd_p, bit_gnd_p)):
        bot = f"cdac_bot_p_{i}"
        lines.append(f"* Cap {i} (weight={w}C)")
        lines.append(f"S_samp_bp_{i} {bot} vcm clk_samp 0 sw_samp ON")
        lines.append(f"S_vdd_p_{i} {bot} vdd {bvdd} 0 sw_cdac ON")
        lines.append(f"S_gnd_p_{i} {bot} 0 {bgnd} 0 sw_cdac ON")
        # Weak pull to Vcm: anchors floating bottom plates against kickback
        lines.append(f"R_pull_p_{i} {bot} vcm 100k")
        # Capacitor
        lines.append(
            f"XC_cdac_p_{i} cdac_top_p {bot} {cap_model} "
            f"w={cap_wl:.4e} l={cap_wl:.4e} m={w}"
        )
        lines.append("")

    lines.extend([
        "* Negative C-DAC array (top plate = cdac_top_n)",
        "* Negative side: B=1 -> VDD (increase V_top_n), BN=1 -> GND (decrease V_top_n)",
    ])

    # Negative C-DAC: B -> VDD, BN -> GND
    bit_vdd_n = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B6"]
    bit_gnd_n = ["BN0", "BN1", "BN2", "BN3", "BN4", "BN5", "BN6", "BN6"]

    for i, (w, bvdd, bgnd) in enumerate(zip(weights, bit_vdd_n, bit_gnd_n)):
        bot = f"cdac_bot_n_{i}"
        lines.append(f"S_samp_bn_{i} {bot} vcm clk_samp 0 sw_samp ON")
        lines.append(f"S_vdd_n_{i} {bot} vdd {bvdd} 0 sw_cdac ON")
        lines.append(f"S_gnd_n_{i} {bot} 0 {bgnd} 0 sw_cdac ON")
        lines.append(f"R_pull_n_{i} {bot} vcm 100k")
        lines.append(
            f"XC_cdac_n_{i} cdac_top_n {bot} {cap_model} "
            f"w={cap_wl:.4e} l={cap_wl:.4e} m={w}"
        )
        lines.append("")

    lines.extend([
        "* Sampling switch: low RON to set Vcm during sampling, high ROFF during conversion",
        f".model sw_samp SW(VT={VDD/2} VH=0.1 RON=100 ROFF=1e12)",
        "",
        "* Weak pull to Vcm on all bottom plates: anchors floating nodes against",
        "* comparator kickback coupling. Without this, kickback shifts bottom plate",
        "* voltages from Vcm, corrupting subsequent charge redistribution steps.",
        "* 100k is weak enough to not affect DAC switching (<1% error for 50 ohm DAC switches)",
        "* but strong enough to pull back ~0.3V kickback in ~10ns (100k * 200fF = 20ns).",
        "* DAC switches: connect to VDD or GND based on B/BN decisions",
        f".model sw_cdac SW(VT={VDD/2} VH=0.1 RON=50 ROFF=1e12)",
        "",
        ".end",
    ])

    cir_path = work_dir / "sar_adc_7bit.cir"
    cir_path.write_text("\n".join(lines) + "\n")
    return cir_path
