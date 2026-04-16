# LDO + supply-ripple notes

The SAR topologies in this tree use an **ideal** `VDD` source
(`VVDD vdd 0 VDD`). There is no LDO model inline. That omission is
deliberate: nothing downstream of the supply net is instrumented to
watch ripple, so putting a compensated LDO behind it would only slow
down the simulation.

This note exists so agents do not mistakenly add an LDO without a
plan for how it interacts with the SAR specs.

## What the schematic flow actually enforces

- `check_system_validity` includes a **supply-ripple envelope** gate:
  the switching transient `Q / T_algo_PW` from toggling 2048 * C_unit
  (worst case) must stay under a 2 mA peak envelope. If it does not,
  the validator flags "CDAC peak i exceeds 2 mA envelope; decap sizing
  will dominate". The constant is a placeholder; tune it when a real
  LDO lands.
- `avg_idd` from `meas tran avg_idd AVG i(VVDD)` folds directly into
  the Walden FoM via the power term. That measurement is on the ideal
  source, so LDO inefficiency is currently invisible.

## What a real LDO would add

When we introduce a proper LDO block in a future session, at minimum
it needs:

1. A dedicated `topologies/ldo_cap_pmos.py` (or similar) exposing
   `Vref`, `I_load_max`, `C_load`, and the pass-transistor
   `W_pass_um`. The CMOS implementation from IHP-AnalogAcademy is a
   natural starting point; the gmoverid-skill methodology covers the
   sizing walk.
2. Miller compensation and PSRR checks in `check_validity` — the
   existing `check_pre_sim` gates flag floating nets but not PSRR
   margin.
3. Integration as an extra row in the SAR netlist header: replace the
   `VVDD vdd 0 VDD` line with a regulated path `vdd_raw -> Xldo ->
   vdd`, and add a transient supply perturbation so the validator
   can read off PSRR.

Until that block exists, any claim of "supply-robust" 11-bit SAR in
this codebase should be qualified: the supply is ideal, only the
ripple envelope is gated, and the FoM ignores LDO efficiency.
