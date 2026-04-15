# eda-agents bench — run `s9_initial_smoke`

Total: **11** — PASS: **9**, FAIL: **1**, SKIPPED: **1**, ERROR: **0**, Pass@1 (excluding skipped): **90%**

## By family

| family | total | pass | fail | skipped | error |
|---|---|---|---|---|---|
| bugfix | 3 | 3 | 0 | 0 | 0 |
| end-to-end | 3 | 2 | 0 | 1 | 0 |
| spec-to-topology | 3 | 2 | 1 | 0 | 0 |
| tb-generation | 2 | 2 | 0 | 0 | 0 |

## Results

| task | status | harness | backend | pdk | duration_s | weighted | notes |
|---|---|---|---|---|---|---|---|
| `bugfix_bulk_connection_violation` | **PASS** | callable | dry-run | ihp_sg13g2 | 0.00 | 1.00 | expect_violation=True, detected=True; adapter_runtime_s=0.00 |
| `bugfix_floating_node_clean` | **PASS** | callable | dry-run | ihp_sg13g2 | 0.00 | 1.00 | expect_violation=False, detected=False; adapter_runtime_s=0.00 |
| `bugfix_floating_node_detected` | **PASS** | callable | dry-run | ihp_sg13g2 | 0.00 | 1.00 | expect_violation=True, detected=True; adapter_runtime_s=0.00 |
| `e2e_dry_run_pipeline_smoke` | **PASS** | dry_run | dry-run | ihp_sg13g2 | 0.00 | 1.00 | Adc_dB=60 in range (margin=30); GBW_Hz=1.2e+07 in range (margin=1.1e+07) |
| `e2e_gl_sim_post_synth_systolic` | **FAIL_INFRA** | callable | librelane | gf180mcu | 0.01 | 0.00 | bench did not harden a fresh design — see TODO; adapter_runtime_s=0.01 |
| `e2e_miller_ota_audit_ihp` | **PASS** | callable | ngspice-osdi | ihp_sg13g2 | 0.12 | 1.00 | GBW_Hz=1.385e+06 in range (margin=8.85e+05); PM_deg=-42.28 in range (margin=138) |
| `spec_analog_roles_dryrun_dag` | **PASS** | analog_roles | analog_roles-dry | ihp_sg13g2 | 0.01 | 1.00 | final_status=PASS; adapter_runtime_s=0.01 |
| `spec_miller_ota_gf180_easy` | **FAIL_SIM** | callable | ngspice | gf180mcu | 0.05 | 0.50 | adapter_runtime_s=0.05 |
| `spec_miller_ota_ihp_easy` | **PASS** | callable | ngspice-osdi | ihp_sg13g2 | 0.11 | 1.00 | Adc_dB=32.5 in range (margin=7.5); GBW_Hz=1.385e+06 in range (margin=8.85e+05) |
| `tb_miller_ota_ac_runs` | **PASS** | callable | ngspice-osdi | ihp_sg13g2 | 0.11 | 1.00 | adapter_runtime_s=0.11; Adc_dB=32.5 in range (margin=31.5) |
| `tb_miller_ota_deck_emits_ac_meas` | **PASS** | callable | ngspice-osdi | ihp_sg13g2 | 0.11 | 1.00 | ngspice ok in 0.11s; measurements=['Adc_dB', 'GBW_Hz', 'PM_deg']; adapter_runtime_s=0.11 |

