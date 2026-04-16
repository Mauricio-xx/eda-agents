# SAR 11-bit cold-cache flakiness probe — 2026-04-16

Evidence dir for Gap #6a (S9-residual-closure session).

## Why this dir exists

Session S9-gap-closure closed gap #6 (SAR 11-bit ENOB end-to-end task)
as PASS, but noted a **cold-cache flakiness observation**: on at least
one COLD full-bench run (first time Verilator compiled
`sar_logic_11bit.so`), the SAR task returned `SNDR=16.18` — an
effectively-broken ADC. Warm re-runs immediately recovered to the
stable `ENOB=4.45, SNDR=28.56` and the observation was reproduced 3/3
on warm. This was documented as "known flakiness, not a blocker for
merge" in `SESSION_HANDOFF.md` and
`project_s9_gap_closure_commitment.md` at close.

The S9-residual-closure session's charter is to close that caveat
properly — diagnose root cause or document conclusively that it is no
longer reproducible.

## Probe methodology

Nine independent bench runs, each with a **fresh work_dir** (forcing a
fresh Verilator compile of `sar_logic_11bit.so` via `vlnggen`). Two
modes:

1. **SAR-only** (5 reps) — `scripts/run_bench.py --task
   e2e_sar11b_enob_ihp --run-id sar_cold_{i}`. No other bench tasks
   run in the same process; the SAR task is the only workload.
2. **Full bench** (4 reps including the session's paso 0 baseline) —
   `scripts/run_bench.py --run-id ...`. All 16 bench tasks run
   sequentially (workers=1), SAR sits inside that mixed workload.

Between every run the per-task work_dir was re-created from scratch by
virtue of using a new `--run-id`. That guarantees vlnggen's output .so
is rebuilt by Verilator for every single run — which is the exact
"cold" condition the previous session flagged.

## Results

`runs.tsv` carries the raw table. Summary:

| mode       | reps | PASS | FAIL | ENOB (min..max)   | SNDR_dBc (min..max) |
|------------|------|------|------|-------------------|----------------------|
| SAR-only   | 5    | 5    | 0    | 4.451..4.451      | 28.56..28.56         |
| Full bench | 4    | 4    | 0    | 4.451..4.454      | 28.56..28.57         |
| **TOTAL**  | **9**| **9**| **0**| 4.451..4.454      | 28.56..28.57         |

- `_SPEC_ENOB_MIN = 4.0`, min measured = 4.451 → margin 0.451 bit
  (full PASS on every run).
- `_SPEC_SNDR_MIN = 25.0` dB, min measured = 28.56 dB → margin 3.56 dB.
- 8 of 9 runs produced **bit-exact identical** ENOB=4.451,
  SNDR=28.56. The one exception (paso 0 `residual_baseline`) was off
  by 0.003 bit / 0.01 dB — benign numerical jitter, well within FFT
  quantisation noise and orders of magnitude smaller than the
  flakiness-range 12 dB collapse reported before.
- Every run's Verilator compile log confirms a fresh rebuild:
  `Compiling sar_logic_11bit.v via vlnggen` → `Compiled
  sar_logic_11bit.so (227088 bytes)`.

## Conclusion

**The cold-cache flakiness is not reproducible in this session.**
Nine independent cold-compile runs, spanning both isolated
(`SAR-only`) and mixed (`full bench`) workloads, produced zero
degenerate results and near-identical measurements. The 12 dB SNDR
collapse previously seen (SNDR=16.18) did not occur in any of these 9
runs.

No code change applied for Gap #6a. The path the previous session
flagged — "first cold run in full bench context returns degenerate
SNDR" — cannot be exercised here, so a targeted fix is not
justifiable without a reproduction.

If the flakiness reappears in future sessions (CI, a new machine, a
different nix store state), the next session should:

1. Capture the failing `bit_data.txt` (columns D0..D10, `vin_diff`,
   `dac_clk`) — the difference between a 16 dB result and a 28 dB
   result is in the bit-slicing, not the ENOB math itself.
2. Diff the failing `.so` against a known-good one (same Verilator
   version, same source → should be byte-identical; if not, Verilator
   is introducing non-determinism and the fix lives there).
3. Compare `.spiceinit` contents, PSP103 OSDI load order, and
   ngspice environment between the failing run and a recovered warm
   one.

Until then, the default-params SAR 11-bit workload is treated as
**stable across cold-compile cycles**, not flaky.

## Files

- `runs.tsv` — one row per probe run, full dataset.
- The per-run raw reports live under
  `bench/results/sar_cold_{1..5}/report.md`,
  `bench/results/sar_coldfull_{1..3}/report.md`, and
  `bench/results/residual_baseline/report.md`. Those are gitignored
  (per the existing `bench/results/*` rule) — this README + TSV are
  the committed artifact.
