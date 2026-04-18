# eda-agents bench — run `s12a_stretch_fft8_live`

Total: **1** — PASS: **0**, FAIL: **1**, SKIPPED: **0**, ERROR: **0**, Pass@1 (excluding skipped): **0%**

## By family

| family | total | pass | fail | skipped | error |
|---|---|---|---|---|---|
| end-to-end | 1 | 0 | 1 | 0 | 0 |

## Results

| task | status | harness | backend | pdk | duration_s | weighted | notes |
|---|---|---|---|---|---|---|---|
| `e2e_idea_to_digital_fft8_gf180_live` | **FAIL_AUDIT** | callable | idea-to-chip | gf180mcu | 7200.38 | 0.00 | loop_converged=0 OUT of range (margin=-1); audit downgraded: ['compile', 'metrics_in_range', 'sim_run'] did not pass |

