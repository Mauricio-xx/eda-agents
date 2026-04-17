# eda-agents bench — run `s11_fase2_fft_live`

Total: **1** — PASS: **0**, FAIL: **1**, SKIPPED: **0**, ERROR: **0**, Pass@1 (excluding skipped): **0%**

## By family

| family | total | pass | fail | skipped | error |
|---|---|---|---|---|---|
| end-to-end | 1 | 0 | 1 | 0 | 0 |

## Results

| task | status | harness | backend | pdk | duration_s | weighted | notes |
|---|---|---|---|---|---|---|---|
| `e2e_idea_to_digital_fft4_gf180_live` | **FAIL_AUDIT** | callable | idea-to-chip | gf180mcu | 83.89 | 0.00 | metric 'gl_post_pnr_ok' missing from result; audit downgraded: ['compile', 'metrics_in_range', 'sim_run'] did not pass |

