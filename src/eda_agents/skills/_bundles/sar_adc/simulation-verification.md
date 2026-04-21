# SAR ADC simulation and verification

## Standard run

A normal SAR evaluation goes:

1. Agent picks a point in the system design space.
2. Topology generates the netlist (transistor-level comparator by
   default, XSPICE primitives if using the behavioural 8-bit variant).
3. `SpiceRunner.run_async` runs ngspice. For the behavioural path,
   the runner is built with `extra_codemodel=[kit.cm_path]` and
   `preload_pdk_osdi=True` so PSP103 OSDI is loaded before the deck.
4. `extract_enob` reads `bit_data.txt`, reconstructs codes on rising
   edges of `dac_clk`, and calls `eda_agents.tools.adc_metrics.
   compute_adc_metrics` (ADCToolbox) for ENOB / SNDR / SFDR / THD /
   SNR. The fallback path is a plain coherent-FFT if ADCToolbox is
   absent.
5. `compute_system_fom` / `check_system_validity` produce the scalar
   FoM and the violation list. The Walden FoM reporting is
   higher-is-better; agents must not invert the sign.

## Coherent sampling

All SAR decks use a sine input with `f_in = M * f_s / N`, `M` coprime
with `N` (M=7 for 8-bit, 64 samples; M=9 for 11-bit, 128 samples). The
choice comes from `_N_FFT_SAMPLES` / `_SINE_CYCLES` constants at the
top of each topology module — change both together or the FFT window
falls out of coherent sampling and the ENOB number degrades.

## Corner sweeps

The existing `PdkConfig` registry carries a single `ltvs` corner at
nominal. For multi-corner sweeps, agents should:

- Rely on the `corners:` list in the `BlockSpec` YAML (see
  `eda_agents.specs.BlockSpec`) to request PVT coverage.
- Re-invoke the topology per corner; `SpiceRunner` picks up the
  relevant `.lib` section through `PdkConfig`.

The `SARADC11BitTopology.check_system_validity` heuristic for PVT
margin (input-pair Pelgrom vs 0.5 LSB) is a shortcut to guard against
obviously unsafe sizing, not a replacement for a real corner sweep.

## What to believe in the reported numbers

- **ENOB / SNDR / SFDR / THD / SNR**: come from ADCToolbox when
  available, from the boxcar FFT fallback otherwise. The fallback does
  not compute SFDR/THD/SNR separately, so those keys will be missing.
- **Walden FoM**: via `tools.adc_metrics.calculate_walden_fom`. The
  penalty factor in `compute_system_fom` is a *soft* multiplier; a
  point that just barely fails the robustness heuristics can still
  report a non-zero FoM. Do not treat FoM > 0 as "passes".
- **Validity**: `check_system_validity` returns the human-readable
  violations list. An agent should *only* claim PASS when that list
  is empty for the relevant corner.

## Things to watch in the deck

- The `R_pull_p_i` / `R_pull_n_i` 100 kΩ weak pulls anchor CDAC
  bottom plates during the undecided-bit phase; remove them and the
  comparator kickback dominates.
- The Verilog port order in the `Adut` instance must match the
  Verilator-generated pin order; a mismatch fails silently with
  random bit reordering rather than a clean error.
- `wrdata` on ngspice emits `(time, value)` *pairs* per variable, so
  the first data column is always time. Both `extract_enob` readers
  already account for this — replace at your own risk.
