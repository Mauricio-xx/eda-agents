# Wave-3 `--workers 4` run proof (gap #9)

Captured at session S9-gap-closure close. Shows that the bench's
`JobRegistry` path (bridge/jobs.py) produces identical results to the
serial path, only faster wall-clock.

## Measurements on this host

| workers | wall-clock | user | summary               |
|---------|-----------:|-----:|-----------------------|
| 1       |  2m17.9s   | 11m21.2s | 16/16 PASS, 0 FAIL |
| 4       |  1m21.3s   | 11m38.2s | 16/16 PASS, 0 FAIL |

Speedup: **~1.7x** wall-clock. Not 4x because the slowest two tasks
(`e2e_sar11b_enob_ihp` at ~79s and `e2e_digital_counter_gf180` at
~55s) each run on a single worker once scheduled. User CPU time is
nearly identical, as expected.

## Reproducing

```bash
PYTHONPATH=src .venv/bin/python scripts/run_bench.py \
    --workers 4 --run-id gap_closure_parallel
```

Files `summary.json` and `report.md` next to this README are the
actual frozen evidence. Per-task JSON artefacts are gitignored
(`bench/results/**` exclusion + narrow allowlist for these).

## Unit test

See `tests/test_bench_runner.py::test_run_batch_workers_consistent`
for the offline regression — it runs the same tasks twice under
workers=1 and workers=2 and asserts matching per-task statuses.
