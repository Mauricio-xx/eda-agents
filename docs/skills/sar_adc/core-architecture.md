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
| `SARADCTopology` (8b, transistor) | Primary production-quality flow, the only SAR here with silicon-traceable numbers (AnalogAcademy-derived). |
| `SARADC8BitBehavioralTopology` | Upper bound on ENOB/SNDR: any gap measures what the StrongARM contributes to non-idealities. Fast (~4x less SPICE time). |
| `SARADC11BitTopology`          | Design reference for 11-bit exploration. Not silicon-validated; marked `DESIGN_REFERENCE = True`. Lets agents shape-check a 10-cycle SAR at 1 MHz. |

Agents should treat the 11-bit version as an architectural target, not
a drop-in replacement. The transistor-level comparator and the ideal
bootstrap switches are the weakest points — patches are welcome once a
Magic-free layout path exists for IHP.
