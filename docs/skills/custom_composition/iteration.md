## Iteration discipline

Each loop iteration passes through these stages in order:

1. **Propose composition** (LLM) — emit composition graph +
   connectivity + testbench + target_specs. On resume, start from the
   last kept composition unless the user requested a fresh sweep.
2. **Size sub-blocks** (LLM + gm/ID rules) — emit per-sub-block params.
3. **Generate netlist** (code) — instantiate each sub-block's SPICE
   model, wire them per connectivity, write deck.
4. **Run SPICE** (code) — ngspice with the testbench, measure
   target_specs.
5. **If SPICE passes → request layout** (code) — for each sub-block
   call `generate_analog_layout` to get a GDS + spice file; compose
   via the thin placer.
6. **Run DRC + LVS** (code) — `klayout_drc.py` + `klayout_lvs.py`.
7. **Critique** (LLM) — inspect all stage outputs, emit a verdict:
   - `converged` if SPICE + DRC + LVS all green.
   - `patch` with a specific sizing/composition change if any stage
     failed in an actionable way.
   - `honest_fail` with a diagnosis if a sub-block is missing or a
     primitive is architecturally inadequate.

## Reading stage outputs

You receive JSON summarising each stage:

```
{
  "iteration": 3,
  "stage": "critique",
  "composition": {<the graph from iteration 3>},
  "sizing": {<sizing dict>},
  "spice": {
    "ran": true,
    "measurements": {<measured_name>: <value>},
    "pass_per_spec": {"INL_LSB": true, "DNL_LSB": false, ...},
    "error": null
  },
  "layout": {
    "attempted": true,
    "gds_path": "<path>",
    "netlist_path": "<path>",
    "error": null
  },
  "drc": {"clean": false, "total_violations": 12, "per_rule": {...}},
  "lvs": {"passed": false, "delta": "..."}
}
```

## Patch proposal format

Be concrete. Don't say "increase W"; say exactly:

```
{
  "verdict": "patch",
  "rationale": "DNL_LSB=0.8 (fail: target<0.5). Unit current mirror has W=2 um which gives sigma_Vt ~ 6 mV -> output current spread ~30% at 1 uA. Doubling W area (W=4 um same L=1 um) reduces sigma_Vt to ~4 mV, should bring DNL under 0.5 LSB.",
  "patch": {
    "sizing": {"cm_unit": {"width": 4.0}}
  }
}
```

## Budget awareness

The user set `max_iterations` and `max_budget_usd`. Honour both:

- If `max_iterations - iterations_spent <= 1` and the last iteration
  was far from converged, switch verdict to `honest_fail` and use the
  final turn to write a good diagnosis.
- If `budget_remaining_usd < 2 × per_iteration_cost`, same — don't
  start an iteration you can't finish.

## Stopping rules

Return `verdict: converged` ONLY when:

- SPICE `pass_per_spec` is all-true.
- DRC `clean == true`.
- LVS `passed == true` OR the composition explicitly accepts a
  documented LVS-blocker (e.g. SG13G2 MIM cap today; this must be
  surfaced in the rationale, never silently waived).

Return `verdict: honest_fail` when:

- A composition change doesn't close after 2 patches in a row.
- A required primitive is unavailable (e.g. SG13G2 opamp_twostage is
  Gap 4 blocked).
- The target specs are internally inconsistent (e.g. 100 dB gain with
  1 MHz GBW on 1 µA bias — thermodynamics won't cooperate).
