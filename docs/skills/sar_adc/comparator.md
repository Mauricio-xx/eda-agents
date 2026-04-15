# StrongARM comparator in the SAR context

`eda_agents.topologies.comparator_strongarm.StrongARMComparatorTopology`
wraps a 12-transistor double-tail dynamic comparator with PMOS input
pair. This note focuses on the **SAR-specific** concerns; the
`StrongARMComparatorTopology.prompt_description()` and the module
docstring cover the standalone sizing story.

## What the SAR asks of this block

1. **Offset below 0.5 LSB of the system full scale.** At 11 bits with a
   differential full swing of VDD, 0.5 LSB is ~290 µV at IHP VDD=1.2 V.
   The input-pair Pelgrom law (`sigma_Vos = A_VT / sqrt(W * L)`) drives
   the sizing, and `SARADC11BitTopology.check_system_validity` raises a
   violation when the computed sigma exceeds the budget.
2. **Regeneration fast enough to bound metastability.** The evaluate
   pulse is `T_algo_PW = 1/(22 * f_s)` (~45 ns at 1 MHz) for the 11-bit
   flow; the validator flags designs where a crude `tau_regen ~
   20 ps / (W_latch_p/8)` heuristic eats more than 40 % of that budget.
3. **Low kickback onto the CDAC top plate.** The StrongARM input pair
   couples to `cdac_top_p` / `cdac_top_n` during evaluation; large
   input pairs give better matching but worse kickback. The 8-bit SAR
   partly absorbs the kickback through the weak pull-to-Vcm resistors
   on every CDAC bottom plate (see `sar_adc_netlist.py` comment block).

## Behavioural substitute

`sar_adc_8bit_behavioral.SARADC8BitBehavioralTopology` swaps the
StrongARM for two instances of `ea_comparator_ideal` (XSPICE) wired
with opposite polarity so `comp_outp` / `comp_outn` follow the same
differential convention as the transistor-level comparator. Knobs:

- `vout_high` / `vout_low` — rails for the downstream adc_bridge.
- `hysteresis_v` — differential hysteresis band. Larger = immune to
  CDAC settling ripple but wastes LSBs.

Use the behavioural path to upper-bound ENOB before committing SPICE
budget to a StrongARM sweep.

## Coupling to the CDAC

The input transistors sit between `cdac_top_p` / `cdac_top_n` and the
internal evaluation nodes. Any net you add between them (ESD diodes,
Miller compensation, etc.) changes the CDAC settling envelope and must
be re-verified against `reference settling` checks in
`check_system_validity`.
