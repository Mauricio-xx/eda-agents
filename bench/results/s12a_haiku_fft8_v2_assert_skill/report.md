# eda-agents bench — run `s12a_haiku_fft8_v2_assert_skill`

Total: **1** — PASS: **0**, FAIL: **1**, SKIPPED: **0**, ERROR: **0**, Pass@1 (excluding skipped): **0%**

## By family

| family | total | pass | fail | skipped | error |
|---|---|---|---|---|---|
| end-to-end | 1 | 0 | 1 | 0 | 0 |

## Results

| task | status | harness | backend | pdk | duration_s | weighted | notes |
|---|---|---|---|---|---|---|---|
| `e2e_idea_to_digital_fft8_haiku_gf180_live` | **FAIL_AUDIT** | callable | idea-to-chip | gf180mcu | 9051.64 | 0.00 | loop_turns_used=1 in range (margin=0); audit downgraded: ['compile', 'metrics_in_range', 'sim_run'] did not pass |

