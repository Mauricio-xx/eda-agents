# SAR 11-bit architectural ceiling — 2026-04-16

Evidence dir for Gap #6b (S9-residual-closure session).

## Why this dir exists

Session S9-gap-closure closed gap #3 (SAR threshold anchors) by
pinning `SARADC11BitTopology._SPEC_ENOB_MIN=4.0` / `_SPEC_SNDR_MIN=25.0`
to the ENOB=4.45 / SNDR=28.56 dB that the default design point
produced end-to-end. That matched reality but left **zero numeric
anchor for what the architecture can actually reach** under
parameter tuning — the thresholds were literally the defaults with
a 0.45 bit / 3.5 dB margin pulled out of thin air.

This dir captures the measurement that fixes that. A 12-point
Latin-square sweep across the four dominant ENOB levers was run by
`scripts/characterize_sar11_ceiling.py` and the results drove the
S9-residual-closure decision to raise the anchors to 4.5 bit / 28 dB
**and** shift `default_params()` to the measured optimum so the
bench baseline keeps margin above the new floor.

## Sweep design

L9 Taguchi orthogonal array (3 levels x 4 factors) + 3 corner probes
= 12 configurations.

Factors and levels (rest of `default_params` held constant):

| Factor              | Low    | Mid     | High   |
|---------------------|--------|---------|--------|
| `comp_W_input_um`   | 8.0    | 32.0    | 50.0   |
| `comp_L_input_um`   | 0.15   | 0.20    | 0.50   |
| `cdac_C_unit_fF`    | 20.0   | 50.0    | 150.0  |
| `bias_V`            | 0.50   | 0.60    | 0.90   |

The `all-mid` anchor (run_12) replicates the old defaults; the
`all-low` anchor (run_10) replays run_01 to detect run-to-run
jitter.

## Results (`sweep.tsv`)

| run   | W_in (um) | L_in (um) | Cu (fF) | Vb (V) | status | ENOB   | SNDR (dB) | SFDR (dB) |
|-------|-----------|-----------|---------|--------|--------|--------|-----------|-----------|
| 01    | 8.0       | 0.15      | 20      | 0.50   | OK     | 5.637  | 35.70     | 42.90     |
| 02    | 8.0       | 0.20      | 50      | 0.60   | OK     | 4.463  | 28.62     | 33.85     |
| 03    | 8.0       | 0.50      | 150     | 0.90   | OK     | 0.000  | -inf      | nan       |
| 04    | 32.0      | 0.15      | 50      | 0.90   | OK     | 0.000  | -inf      | nan       |
| 05    | 32.0      | 0.20      | 150     | 0.50   | OK     | 3.920  | 25.36     | 31.05     |
| 06    | 32.0      | 0.50      | 20      | 0.60   | OK     | 5.597  | 35.45     | 43.16     |
| 07    | 50.0      | 0.15      | 150     | 0.60   | OK     | 3.920  | 25.36     | 31.05     |
| 08    | 50.0      | 0.20      | 20      | 0.90   | OK     | 0.000  | -inf      | nan       |
| 09    | 50.0      | 0.50      | 50      | 0.50   | OK     | 4.445  | 28.52     | 33.84     |
| 10    | 8.0       | 0.15      | 20      | 0.50   | OK     | 5.637  | 35.70     | 42.90     |
| 11    | 50.0      | 0.50      | 150     | 0.90   | OK     | 0.000  | -inf      | nan       |
| 12    | 32.0      | 0.20      | 50      | 0.60   | OK     | 4.454  | 28.57     | 33.89     |

8 of 12 runs produced valid measurements. The 4 degenerate runs
(ENOB=0, SNDR=-inf, `unique_codes=1`) all have `bias_V=0.9`: the
bias starves the StrongARM tail at the top of the range so the
comparator never resolves a non-trivial decision. This is a known
limitation of the naive Vbias delivery path and is part of the
scope of TODO_calibration.md items 2-4 (LDO + real bootstrap).

Key observations:

- **Architectural ceiling = ENOB 5.64 bit / SNDR 35.70 dB** at
  W=8 um, L=0.15 um, Cu=20 fF, Vb=0.5 V. Reproduced bit-exact across
  the two replica runs (`run_01` and `run_10`).
- The old defaults (W=32, L=0.20, Cu=50, Vb=0.6) land at ENOB=4.45,
  SNDR=28.57 dB — `run_12` matches the S9-gap-closure baseline
  bit-exact, confirming the sweep shares ground truth with the bench
  task.
- Large CDAC (`cdac_C_unit_fF=150`) with mid bias degrades the
  converter to ENOB=3.92 / SNDR=25.36 dB (runs 05 and 07). This was
  just above the S9-gap-closure anchors; S9-residual-closure's
  raised anchors (4.5 / 28) now correctly flag these as failing.
- THD tells the same story as ENOB: -46.94 dB at the ceiling vs
  -28.30 dB at the large-CDAC degraded configs, ~18 dB of
  nonlinearity between the best and worst valid point.

## Decision recorded

Anchors raised to `_SPEC_ENOB_MIN=4.5`, `_SPEC_SNDR_MIN=28.0` per
the `floor(5.64) - 0.5 = 4.5` rule, and `default_params()` shifted
to the ceiling point so the bench baseline stays above the new
threshold with ~1.1 bit margin. See the commit that introduced this
README for the full diff.

`tests/test_sar_adc_11bit_ceiling.py` locks this invariant: the
sweep TSV's max ENOB must stay >= `_SPEC_ENOB_MIN`. Any future
topology change that pulls the ceiling below the threshold will
fail that test loudly.

## Reproducing

```bash
set -a && source /home/montanares/personal_exp/eda-agents/.env && set +a
PYTHONPATH=src .venv/bin/python scripts/characterize_sar11_ceiling.py \
    --out bench/results/sar11_ceiling_characterization
```

Takes roughly 15-17 minutes wall-clock on the session reference
machine (12 simulations + overhead).

## Files

- `sweep.tsv` — one row per configuration, full dataset, committed.
- `sweep.json` — same data in structured form, committed.
- `run_{NN}/` per-config scratch dirs (netlist + `bit_data.txt`) —
  gitignored under the default `bench/results/*` rule.
