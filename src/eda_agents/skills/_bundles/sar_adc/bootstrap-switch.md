# Bootstrap sampling switch (architectural note)

The SAR netlists in this repository use **ideal switches** for the
input sampling operation:

```
S_samp_p vin_pos cdac_top_p clk_samp 0 sw_ideal ON
S_samp_n vin_neg cdac_top_n clk_samp 0 sw_ideal ON
.model sw_ideal SW(VT=VDD/2 VH=0.1 RON=100 ROFF=1e12)
```

A proper bootstrap switch (boot-clocked NMOS + charge pump, or a
double-boost stage) is a future enhancement and is intentionally out of
scope for the current flow. The reason is that post-layout parasitic
extraction for IHP is blocked upstream (see
`docs/upstream_issues/ihp_magic_hang.md`); without layout parasitics a
full bootstrap model adds spice time without adding signal. Once a
Magic-free extraction path exists for IHP, this block becomes the next
natural target.

## What the ideal switch pretends

- `R_ON = 100 Ω` is a rough placeholder for a boosted NMOS.
- `V_T = VDD/2` and `V_H = 0.1` give fast on/off transitions without
  dwelling in the linear regime.
- `R_OFF = 1 TΩ` keeps leakage out of the CDAC top-plate trajectory.

## When to revisit

1. **Distortion claims.** If an agent claims THD or HD3 numbers below
   about -70 dBc, the ideal switch is hiding non-linearity. Replace
   with a real NMOS + boot-cap model before trusting the result.
2. **High-frequency inputs.** At `fin > f_s / 10`, the boost time
   constant starts to matter for the sampled voltage. Again: real
   switch model required.
3. **Temperature / supply sweeps.** Ideal switches ignore VDD /
   temperature dependence. `check_system_validity` flags only the
   static switch + CDAC settling constant, not the sample-path
   linearity.

## Migration plan

When the time comes:

1. Keep the ideal-switch path as a behavioural baseline (flag knob on
   the topology constructor, default OFF for production flows).
2. Add a transistor-level `bootstrap` block under
   `src/eda_agents/topologies/`, exposing at least `W_switch_um`,
   `L_switch_um`, `C_boot_fF`, `W_precharge_um`.
3. Parameterise `generate_sar_adc_netlist` to accept a
   `sampling_section: list[str] | None` argument analogous to the
   existing `comparator_section`.

Until then, treat the sampling path as transparent: it is not part of
the agent-facing design space.
