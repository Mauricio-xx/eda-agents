# SAR ADC Core Architecture (eda-agents rewrite)

This note covers the architectural shape of the SAR ADCs shipped in
`eda_agents.topologies.sar_adc_8bit`, `sar_adc_8bit_behavioral`, and
`sar_adc_11bit`. It is **not** a tutorial — assume the reader already
knows charge-redistribution SAR. The goal is to describe what this
repository implements so an agent can navigate the code confidently.

## Top-level partitioning

Every SAR converter in this tree is a `SystemTopology` with the same
three-block decomposition:

| Block       | Implementation                                           |
|-------------|----------------------------------------------------------|
| comparator  | StrongARM transistor (`topologies/comparator_strongarm.py`) *or* XSPICE `ea_comparator_ideal` (`veriloga/voltage_domain/comparator_ideal`) |
| cdac        | Binary-weighted CMIM array built inline in `sar_adc_netlist.py` / `sar_adc_11bit.py`; bottom plates switched to VDD/GND via `sw_cdac` ideal switches |
| digital SAR | Verilator-compiled `sar_logic.v` (8 bit) or `sar_logic_11bit.v`, loaded in ngspice through the `d_cosim` XSPICE bridge |

Sampling is handled by ideal switches with a gated `clk_samp` window;
the bootstrap devices from a real design are intentionally *not* in
scope for the schematic flow because open-PDK post-layout parasitic
extraction is blocked upstream for IHP (see
`docs/upstream_issues/ihp_magic_hang.md`). Architectural notes on the
bootstrap switch live in `bootstrap-switch.md`.

## Conversion timing

The conversion cycle runs at 1 MHz. Each period splits into:

1. Sample phase (`clk_samp = HIGH`, first half-period): sampling
   switches close, CDAC bottom plates are tied to `Vcm`, top plates
   track the differential input.
2. Evaluate phase (`clk_samp = LOW`, `clk_comp` drops after a 50 ns
   settle delay): the comparator regenerates and the SAR FSM latches
   the decision on the next rising edge of `clk_comp`.
3. DAC update: bottom plates switch to VDD/GND based on the previous
   decisions, collapsing one binary weight per algorithm cycle.

The behavioural variant reuses the same timing; the XSPICE comparator
is combinatorial, so the `clk_comp` rising edge still samples a
fully-settled decision.

## Why three topologies

| Topology                       | Purpose                                                   |
|--------------------------------|-----------------------------------------------------------|
| `SARADCTopology` (8b, transistor) | Primary production-quality flow, the only SAR here with silicon-traceable numbers (AnalogAcademy-derived). **Effectively 7-bit** (the FSM does 7 iterations and the LSB binary cap shares its switch with the dummy — that's the AA convention; "8-bit" refers to the D bus width, not the resolution). |
| `SARADC8BitBehavioralTopology` | Upper bound on ENOB/SNDR for the 8-bit topology: any gap measures what the StrongARM contributes to non-idealities. Same 7-effective-bit limit as the AA parent. Fast (~4x less SPICE time). |
| `SARADC11BitTopology`          | Design reference for 11-bit exploration. Not silicon-validated; marked `DESIGN_REFERENCE = True`. **True 11-bit**: 11 distinct decision iterations, 11 binary-weighted caps (1024..1) + 1 dummy tied permanently to vcm so the array sums to 2^11. Runs an 11-cycle SAR at 1 MHz. |

## A note on the dummy cap

In a real binary-weighted CDAC the "termination" / dummy cap exists so
that the array sums to a clean 2^N total (so during sampling the top
plate sees the full input voltage). Two conventions appear in this
tree:

1. **AA 8-bit convention (legacy)**: the dummy reuses the LSB switch.
   N decision bits + 1 dummy = N+1 caps but only N distinct controls,
   so the LSB cap effectively carries weight 2 instead of 1. The
   converter then has missing codes between the bottom two binary
   weights. Effective resolution = N-1 bits despite the "N-bit" label.
2. **11-bit convention (this repo, new)**: the dummy is tied
   permanently to vcm and never switches. N decision bits + 1 dummy
   = N+1 caps with N distinct controls and N independent binary
   weights. Effective resolution = N bits as advertised.

The 11-bit topology's `test_cdac_is_true_11bit` regression test pins
the per-bit switched weight ladder so the converter can never
silently regress to the AA-style 10-effective-bit shape.

Agents should treat the 11-bit version as an architectural target, not
a drop-in replacement. The transistor-level comparator and the ideal
bootstrap switches are the weakest points — patches are welcome once a
Magic-free layout path exists for IHP.
