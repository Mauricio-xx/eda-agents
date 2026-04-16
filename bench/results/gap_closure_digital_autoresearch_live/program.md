# Circuit Design Exploration Program

## Goal
Maximize FoM for counter on GF180MCU 180nm CMOS.
FoM definition: FoM = 1.0 * WNS_worst_ns + 0.5 * (1e6/die_area_um2) + 0.3 * (1/power_W). Higher is better. Returns 0.0 for designs that fail timing.

## Metrics
Primary: FoM (higher is better)
Constraints (all must be met for a valid design):
  WNS >= 0 at all corners, DRC clean, LVS match

## Design Space
- PL_TARGET_DENSITY_PCT: [30, 40, 50, 55]
- CLOCK_PERIOD: [40.0, 50.0, 62.5]

Ranges:
- PL_TARGET_DENSITY_PCT: one of [30, 40, 50, 55]
- CLOCK_PERIOD: one of [40.0, 50.0, 62.5]

## Specs
WNS >= 0 at all corners, DRC clean, LVS match

## Current Best
Eval #1: FoM=9.62e+02
Parameters:
```json
{
  "PL_TARGET_DENSITY_PCT": 40,
  "CLOCK_PERIOD": 50.0
}
```
Measurements: WNS=35.64314810852963ns, cells=135, area=3600um2, power=0.3811813367065042mW, wire=576um

## Strategy
Starting exploration. No data yet -- begin with the reference design
point and systematically explore around it.

Reference: No reference run established. The first successful flow run will serve as the baseline.

## Learned So Far
- Eval #1: FoM improved to 9.62e+02 (WNS=35.64314810852963ns, cells=135) with {"PL_TARGET_DENSITY_PCT": 40, "CLOCK_PERIOD": 50.0}

## Rules
- Propose parameters as a JSON object. Keys must match the design space variables.
- Each evaluation costs 1 evaluation from the budget.
- A design is "valid" only if ALL specs are met simultaneously.
- FoM is only meaningful for valid designs.
- Crashes: If a run crashes (OOM, or a bug, or etc.), use your judgment:
  If it's something dumb and easy to fix (e.g. a typo, a missing import),
  fix it and re-run. If the idea itself is fundamentally broken, just skip
  it, log "crash" as the status in the tsv, and move on.
- NEVER STOP: Once the experiment loop has begun, do NOT pause to ask the
  human if you should continue. The human might be asleep, or gone from a
  computer and expects you to continue working indefinitely until you are
  manually stopped. You are autonomous. If you run out of ideas, think
  harder -- re-read the design space, try combining previous near-misses,
  try more radical parameter changes. The loop runs until the budget is
  exhausted or the human interrupts you, period.
