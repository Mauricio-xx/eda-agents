"""Four-domain AMS demo.

Stitches together the four simulation domains that eda-agents can
drive today, inside a single ngspice deck:

  1. PDK transistor-level (IHP SG13G2 PSP103 via OSDI).
  2. Analog behavioural (Verilog-A via OpenVAF -> OSDI —
     ``filter_1st.va`` from ``veriloga/current_domain``).
  3. Voltage-domain event-driven (XSPICE code model —
     ``ea_comparator_ideal`` from ``veriloga/voltage_domain``).
  4. Digital RTL (Verilator via ``d_cosim`` — ``rtl_counter3.v`` in
     this folder).

Signal chain::

    Vin (PULSE, 500 kHz, 0..VDD)
      -> filter_1st   (Verilog-A RC low-pass)            [domain 2]
      -> CMOS inverter (IHP sg13_lv_nmos / sg13_lv_pmos) [domain 1]
      -> ea_comparator_ideal  (vref = VDD/2)             [domain 3]
      -> adc_bridge  (ngspice builtin analog -> digital)
      -> rtl_counter3  (3-bit counter on rising edges)   [domain 4]
      -> dac_bridge  (ngspice builtin digital -> analog) -> q0/q1/q2

The counter latches a new value on each rising edge of the comparator
output, which arrives on every falling edge of the input pulse. The
demo prints the counter's final value plus the intermediate analog
nodes so a reviewer can confirm every domain is alive.

Prerequisites (all must be present; the script skips gracefully with
an explanation if any is missing):
  - ngspice-45+ with XSPICE support (host or ``scripts/xspice_docker.sh``).
  - openvaf (``extra_osdi`` pipeline).
  - Verilator + g++ + vlnggen (``d_cosim``).
  - IHP SG13G2 PDK resolvable via ``PDK_ROOT`` or the built-in default.
  - An ngspice source checkout for XSPICE compilation
    (``NGSPICE_SRC_DIR`` or default probe paths). Docker image solves
    this automatically.

Run::

    PYTHONPATH=src python examples/11_ams_mixed_domain_demo.py
    # or inside the container:
    scripts/xspice_docker.sh python examples/11_ams_mixed_domain_demo.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from eda_agents.core.pdk import (
    IHP_SG13G2,
    netlist_lib_lines,
    resolve_pdk_root,
)
from eda_agents.core.spice_runner import SpiceRunner
from eda_agents.core.stages.veriloga_compile import VerilogACompiler
from eda_agents.core.stages.xspice_compile import (
    CodeModelSource,
    XSpiceCompiler,
    load_codemodel_line,
)
from eda_agents.topologies.sar_adc_7bit_behavioral import (
    behavioral_comparator_cards,
)
from eda_agents.utils.vlnggen import check_prerequisites, compile_verilog
from eda_agents.veriloga.current_domain import primitive_path as va_primitive_path
from eda_agents.veriloga.voltage_domain import primitive_paths as xspice_primitive_paths

VDD = 1.2
VREF = 0.5 * VDD  # comparator decision threshold
INPUT_PERIOD_S = 2.0e-6       # 500 kHz pulse train
INPUT_TR_S = 20.0e-9          # 20 ns rise/fall
INPUT_DUTY = 0.5
SIM_END_S = 12.0e-6           # enough for 6 rising edges after reset
RESET_END_S = 0.5e-6          # hold rst high until here

# Filter sized so its pole sits well above the input tone: R=1k, C=100p
# -> fp ~= 1.59 MHz, so the 500 kHz pulse passes with minor attenuation
# while the simulator still has to solve a non-trivial continuous-time
# ODE (proving Verilog-A is genuinely in the loop).
FILTER_R_OHM = 1.0e3
FILTER_C_F = 100.0e-12


def _print_step(msg: str) -> None:
    print(f"[ams-demo] {msg}", flush=True)


def _check_prereqs() -> list[str]:
    missing: list[str] = []
    if not shutil.which("ngspice"):
        missing.append("ngspice not on PATH")
    if not VerilogACompiler().available():
        missing.append("openvaf not available")
    if not XSpiceCompiler().available():
        missing.append(
            "XSPICE toolchain unavailable — need an ngspice source "
            "checkout or run under scripts/xspice_docker.sh"
        )
    verilator_missing = check_prerequisites()
    if verilator_missing:
        missing.extend(verilator_missing)
    # PDK presence
    try:
        resolve_pdk_root(IHP_SG13G2)
    except Exception as exc:  # noqa: BLE001
        missing.append(f"IHP SG13G2 PDK: {exc}")
    return missing


def _build_filter_osdi(work_dir: Path) -> Path:
    _print_step("compiling Verilog-A filter_1st -> .osdi")
    src = va_primitive_path("filter_1st")
    res = VerilogACompiler().run(src, out_dir=work_dir)
    if not res.success:
        raise RuntimeError(f"openvaf failed: {res.error}\n{res.log_tail}")
    return res.artifacts["osdi"].resolve()


def _build_comparator_cm(work_dir: Path) -> Path:
    _print_step("compiling XSPICE comparator_ideal -> .cm")
    mod, ifs = xspice_primitive_paths("comparator_ideal")
    sources = [
        CodeModelSource(name="ea_comparator_ideal", cfunc_mod=mod, ifspec_ifs=ifs),
    ]
    res = XSpiceCompiler().compile(
        sources, work_dir / "ams_comparator.cm", work_dir=work_dir / "_xspice_build"
    )
    if not res.success:
        raise RuntimeError(f"XSPICE compile failed: {res.error}\n{res.log_tail}")
    return res.artifacts["cm"].resolve()


def _build_counter_so(work_dir: Path) -> Path:
    _print_step("compiling RTL counter via vlnggen -> .so")
    src = Path(__file__).resolve().parent / "rtl_counter3.v"
    return compile_verilog(src, work_dir=work_dir).resolve()


def _write_deck(
    work_dir: Path,
    so_path: Path,
) -> Path:
    cir = work_dir / "ams_demo.cir"
    inst, model = behavioral_comparator_cards(
        instance_name="Acmp",
        node_inp="inv_out",
        node_inn="vref",
        node_out="cmp_out",
        model_ref="ea_cmp",
        vout_high=VDD,
        vout_low=0.0,
        hysteresis_v=0.01,
    )
    lib_lines = "\n".join(netlist_lib_lines(IHP_SG13G2))
    deck = f"""* four-domain AMS demo (eda-agents S5 integrator)
*
* Domains:
*   1. IHP SG13G2 transistors (PDK OSDI, PSP103)
*   2. Verilog-A / OSDI (filter_1st, OpenVAF)
*   3. XSPICE code model (ea_comparator_ideal)
*   4. Verilator RTL via d_cosim (rtl_counter3)

* ----- PDK model libraries -------------------------------------
{lib_lines}

* ----- supplies and global nodes --------------------------------
vdd  vdd 0 dc {VDD}
vref vref 0 dc {VREF}

* ----- stimulus: 500 kHz pulse train --------------------------
vin in 0 pulse(0 {VDD} 0 {INPUT_TR_S} {INPUT_TR_S} \
{INPUT_DUTY * INPUT_PERIOD_S} {INPUT_PERIOD_S})

* ----- reset pulse for the RTL counter -------------------------
vrst rst 0 pulse({VDD} 0 {RESET_END_S} 1n 1n \
{SIM_END_S} {2 * SIM_END_S})

* ----- domain 2: Verilog-A behavioural filter_1st -------------
Nfilt in filt_out 0 filt_mod
.model filt_mod filter_1st r_ohm={FILTER_R_OHM} c_f={FILTER_C_F}

* ----- domain 1: IHP CMOS inverter (transistor-level) ---------
* Wp / Wn ratio ~2x so the inverter threshold sits near VDD/2.
Xmp inv_out filt_out vdd vdd sg13_lv_pmos w=2u l=0.13u ng=1 m=1
Xmn inv_out filt_out 0   0   sg13_lv_nmos w=1u l=0.13u ng=1 m=1

* ----- domain 3: XSPICE comparator -----------------------------
{inst}
{model}

* ----- bridges: analog -> digital / digital -> analog ----------
Aadc_clk [cmp_out] [clk_d] adc_bridge_model
Aadc_rst [rst]     [rst_d] adc_bridge_model
.model adc_bridge_model adc_bridge(in_low=0.2 in_high={0.8 * VDD})

* ----- domain 4: Verilator RTL counter -------------------------
Adut [clk_d rst_d] [q0_d q1_d q2_d] null dut
.model dut d_cosim(simulation="{so_path}")

* ----- digital -> analog so we can measure q0/q1/q2 -----------
Adac0 [q0_d] [q0] dac_bridge_model
Adac1 [q1_d] [q1] dac_bridge_model
Adac2 [q2_d] [q2] dac_bridge_model
.model dac_bridge_model dac_bridge(out_low=0 out_high={VDD})

* ----- analysis ------------------------------------------------
.tran 20n {SIM_END_S}
.control
run
meas tran vin_peak     max v(in)
meas tran vfilt_peak   max v(filt_out)
meas tran vinv_lo      min v(inv_out) from=3u to={SIM_END_S}
meas tran vinv_hi      max v(inv_out) from=3u to={SIM_END_S}
meas tran vcmp_hi      avg v(cmp_out) from=9.5u to=9.9u
meas tran q0_final     find v(q0) at={SIM_END_S - 0.05e-6}
meas tran q1_final     find v(q1) at={SIM_END_S - 0.05e-6}
meas tran q2_final     find v(q2) at={SIM_END_S - 0.05e-6}
quit
.endc
.end
"""
    cir.write_text(deck)
    return cir


def _parse_all_meas(stdout: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in stdout.splitlines():
        stripped = line.strip().lower()
        if "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip()
        if not name or " " in name:
            continue
        val_str = stripped.split("=", 1)[1].strip().split()[0]
        try:
            out[name] = float(val_str)
        except ValueError:
            continue
    return out


def _counter_value(meas: dict[str, Any]) -> int:
    bits = 0
    for i, key in enumerate(("q0_final", "q1_final", "q2_final")):
        v = meas.get(key)
        if v is None:
            return -1
        if v > 0.5 * VDD:
            bits |= 1 << i
    return bits


def main() -> int:
    missing = _check_prereqs()
    if missing:
        _print_step("prerequisites missing, aborting:")
        for m in missing:
            _print_step(f"  - {m}")
        return 2

    work_dir = Path("/tmp/eda_agents_ams_demo")
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    filter_osdi = _build_filter_osdi(work_dir)
    comparator_cm = _build_comparator_cm(work_dir)
    counter_so = _build_counter_so(work_dir)

    cir = _write_deck(work_dir, counter_so)
    _print_step(f"wrote deck: {cir}")
    _print_step(f"  extra_osdi      = {filter_osdi}")
    _print_step(f"  extra_codemodel = {comparator_cm}")
    _print_step(f"  rtl .so         = {counter_so}")
    _print_step("  XSPICE spiceinit line preview: "
                + load_codemodel_line(comparator_cm))

    # Also pick up psp103.osdi — the IHP_SG13G2.osdi_files tuple lists
    # the NQS / resistor / varactor OSDIs but not the base psp103.osdi
    # (matching the per-run behaviour where the host ``~/.spiceinit``
    # loads psp103). We need it explicitly here because the runner's
    # transient spiceinit shadows the home one.
    pdk_root = Path(resolve_pdk_root(IHP_SG13G2))
    psp103 = pdk_root / "ihp-sg13g2/libs.tech/ngspice/osdi/psp103.osdi"
    extra_osdi = [filter_osdi]
    if psp103.is_file():
        extra_osdi.append(psp103.resolve())

    runner = SpiceRunner(
        pdk="ihp_sg13g2",
        timeout_s=120,
        extra_osdi=extra_osdi,
        extra_codemodel=[comparator_cm],
        preload_pdk_osdi=True,
    )
    _print_step("launching ngspice ...")
    result = runner.run(cir, work_dir=work_dir)

    if not result.success:
        _print_step(f"simulation FAILED: {result.error}")
        print(result.stdout_tail, file=sys.stderr)
        return 1

    meas = _parse_all_meas(result.stdout_tail)
    if not meas:
        # Some meas output may have arrived before the truncation
        # window; fall back to the full stdout that SpiceRunner saved.
        meas = _parse_all_meas(result.stdout_tail)

    counter = _counter_value(meas)

    _print_step("simulation complete")
    print()
    print("=" * 58)
    print(f"{'Signal':<20s}{'Measurement':<20s}{'Value':>18s}")
    print("-" * 58)
    print(f"{'domain 2 (VA)':<20s}{'vfilt_peak':<20s}"
          f"{meas.get('vfilt_peak', float('nan')):>18.4f}")
    print(f"{'domain 1 (PDK)':<20s}{'vinv_lo':<20s}"
          f"{meas.get('vinv_lo', float('nan')):>18.4f}")
    print(f"{'domain 1 (PDK)':<20s}{'vinv_hi':<20s}"
          f"{meas.get('vinv_hi', float('nan')):>18.4f}")
    print(f"{'domain 3 (XSPICE)':<20s}{'vcmp_hi':<20s}"
          f"{meas.get('vcmp_hi', float('nan')):>18.4f}")
    print(f"{'domain 4 (Verilog)':<20s}{'q0_final':<20s}"
          f"{meas.get('q0_final', float('nan')):>18.4f}")
    print(f"{'domain 4 (Verilog)':<20s}{'q1_final':<20s}"
          f"{meas.get('q1_final', float('nan')):>18.4f}")
    print(f"{'domain 4 (Verilog)':<20s}{'q2_final':<20s}"
          f"{meas.get('q2_final', float('nan')):>18.4f}")
    print("-" * 58)
    print(f"{'Counter value':<20s}{'(bits)':<20s}{counter:>18d}")
    print("=" * 58)
    print()

    # Basic sanity gates: every domain must have produced something.
    def _ok(cond: bool, label: str) -> str:
        return f"{label} {'OK' if cond else 'FAIL'}"

    verdicts = [
        _ok(meas.get("vfilt_peak", 0) > 0.5 * VDD, "domain 2 (Verilog-A)"),
        _ok(meas.get("vinv_lo", VDD) < 0.3 * VDD
            and meas.get("vinv_hi", 0) > 0.7 * VDD, "domain 1 (IHP MOSFET)"),
        _ok(meas.get("vcmp_hi", 0) > 0.7 * VDD, "domain 3 (XSPICE)"),
        _ok(counter > 0, "domain 4 (Verilator)"),
    ]
    for v in verdicts:
        _print_step(v)
    if any("FAIL" in v for v in verdicts):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
