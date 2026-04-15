# SAR robustness heuristics — calibration backlog

The `check_system_validity` heuristics in
`SARADC11BitTopology` (and the lighter set in
`SARADC8BitBehavioralTopology`) are **engineering placeholders**. They
were tuned by feel during S7 — closed-form expressions with constant
factors picked to flag obviously-bad design points, not silicon-grade
PVT margins.

This file tracks what would be needed to upgrade them from
"design_reference shortcut" to "trustworthy robustness gate".

## What we have today

| Gate                  | Heuristic                                                                                 | Confidence                                                                                |
|-----------------------|-------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------|
| ENOB / SNDR / Power   | Direct measurement vs static thresholds.                                                  | High (it's the actual SPICE result), but the thresholds (`_SPEC_*`) themselves are guesses anchored on the AnalogAcademy 8-bit reference. |
| PVT margin            | `sigma_Vos = A_VT / sqrt(W*L)` for the input pair vs `0.5 * VDD/2^N`.                     | Medium. Pelgrom holds, but `A_VT` from PdkConfig is itself a vendor estimate.             |
| Metastability BER     | `tau_regen ~ 20 ps / (W_latch_p / 8)` < 0.4 * `T_algo_PW`.                                | Low. The 20 ps and 0.4 constants are placeholders.                                        |
| Supply ripple         | `i_peak = 2^N * C_unit * VDD / T_algo_PW` < 2 mA envelope.                                | Medium-low. The 2 mA limit is a single-rail guess; depends on LDO + decap design (absent today, see ldo.md). |
| Reference settling    | `tau = R_on * C_total` < `T_algo_PW / 3`.                                                 | Medium. `R_on=50 Ω` is the `sw_cdac` model constant, not a measured switch.               |

## What it would take to recalibrate

1. **Anchor the spec thresholds against silicon**. The 8-bit
   AnalogAcademy reference is the only silicon-traceable point. Either
   (a) absorb that reference's PVT corner spread into our `_SPEC_*`
   constants, or (b) compute the thresholds from the active `BlockSpec`
   YAML so the values follow the actual project, not topology defaults.

2. **Replace the metastability heuristic with a measured `tau_regen`**.
   Add a small DC sweep harness on `StrongARMComparatorTopology` that
   extracts `tau_regen` from the regen-pair small-signal `gm` and
   feedback capacitance, then feed that into the gate. Today's
   `20 ps / (W_latch_p / 8)` is dimensionally right but the constant
   has no physical anchor.

3. **Wire a real LDO and replace the supply-ripple envelope.** Today
   the ngspice deck has an ideal `VVDD`. The 2 mA limit is what we'd
   ask of a "decent" LDO, but with no LDO model the gate can only flag
   designs that would draw too much *if* an LDO were eventually added.
   See `ldo.md` for the migration plan.

4. **Switch from the `sw_cdac` ideal switch to a real bootstrap or
   transmission-gate**. `R_on = 50 Ω` underestimates linear-region
   resistance for a real boot switch under low-Vgs conditions. See
   `bootstrap-switch.md`.

5. **Add a corner sweep harness**. The validator currently looks at one
   nominal SPICE result. Real PVT confidence requires sweeping
   `corners:` from the `BlockSpec` and reporting worst-case.

## Where this should land in the roadmap

S9 (Benchmark suite) is the natural home: by then we will be running
matched configurations across both PDKs and across multiple agent
backends, which gives us the data to recalibrate the constants from
"feel" to "median observed". Until then, treat the violation messages
as exploration heuristics, not production-readiness signals.

The `analog.sar_adc_design` skill prompt warns agents about this
explicitly: a non-empty violation list flags brittle regions, not
necessarily broken designs, and a non-zero FoM does not imply PASS.
