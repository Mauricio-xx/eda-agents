# eda-agents bench — run `s9_residual_final`

Total: **17** — PASS: **17**, FAIL: **0**, SKIPPED: **0**, ERROR: **0**, Pass@1 (excluding skipped): **100%**

## By family

| family | total | pass | fail | skipped | error |
|---|---|---|---|---|---|
| bugfix | 4 | 4 | 0 | 0 | 0 |
| end-to-end | 7 | 7 | 0 | 0 | 0 |
| spec-to-topology | 4 | 4 | 0 | 0 | 0 |
| tb-generation | 2 | 2 | 0 | 0 | 0 |

## Results

| task | status | harness | backend | pdk | duration_s | weighted | notes |
|---|---|---|---|---|---|---|---|
| `bugfix_bulk_connection_violation` | **PASS** | callable | dry-run | ihp_sg13g2 | 0.00 | 1.00 | expect_violation=True, detected=True; adapter_runtime_s=0.00 |
| `bugfix_floating_node_clean` | **PASS** | callable | dry-run | ihp_sg13g2 | 0.00 | 1.00 | expect_violation=False, detected=False; adapter_runtime_s=0.00 |
| `bugfix_floating_node_detected` | **PASS** | callable | dry-run | ihp_sg13g2 | 0.00 | 1.00 | expect_violation=True, detected=True; adapter_runtime_s=0.00 |
| `bugfix_strongarm_vds_inversion` | **PASS** | callable | dry-run | ihp_sg13g2 | 0.00 | 1.00 | expect_violation=True, detected=True; adapter_runtime_s=0.00 |
| `e2e_digital_autoresearch_counter` | **PASS** | digital_autoresearch | librelane | gf180mcu | 4.24 | 1.00 | adapter_runtime_s=4.24; iterations_kept=1 in range (margin=0) |
| `e2e_digital_autoresearch_counter_live` | **PASS** | digital_autoresearch | librelane | gf180mcu | 111.80 | 1.00 | adapter_runtime_s=111.80; iterations_kept=1 in range (margin=0) |
| `e2e_digital_counter_gf180` | **PASS** | callable | librelane | gf180mcu | 53.83 | 1.00 | adapter_runtime_s=53.83; DRC_violations=0 in range (margin=0) |
| `e2e_dry_run_pipeline_smoke` | **PASS** | dry_run | dry-run | ihp_sg13g2 | 0.00 | 1.00 | Adc_dB=60 in range (margin=30); GBW_Hz=1.2e+07 in range (margin=1.1e+07) |
| `e2e_gl_sim_post_synth_counter` | **PASS** | callable | librelane | gf180mcu | 0.14 | 1.00 | stage=POST_SYNTH_SIM; adapter_runtime_s=0.14 |
| `e2e_miller_ota_audit_ihp` | **PASS** | callable | ngspice-osdi | ihp_sg13g2 | 0.12 | 1.00 | GBW_Hz=1.385e+06 in range (margin=8.85e+05); PM_deg=-42.28 in range (margin=138) |
| `e2e_sar11b_enob_ihp` | **PASS** | callable | ngspice-osdi | ihp_sg13g2 | 71.80 | 1.00 | ENOB=5.637 in range (margin=1.14); SNDR_dBc=35.7 in range (margin=7.7) |
| `spec_analog_roles_dryrun_dag` | **PASS** | analog_roles | analog_roles-dry | ihp_sg13g2 | 0.01 | 1.00 | final_status=PASS; adapter_runtime_s=0.01 |
| `spec_llm_miller_ota_ihp` | **PASS** | callable | llm+ngspice-osdi | ihp_sg13g2 | 1.25 | 1.00 | Adc_dB=36.3 in range (margin=11.3); GBW_Hz=1.627e+06 in range (margin=1.13e+06) |
| `spec_miller_ota_gf180_easy` | **PASS** | callable | ngspice | gf180mcu | 0.05 | 1.00 | Adc_dB=42.47 in range (margin=17.5); GBW_Hz=1.359e+06 in range (margin=8.59e+05) |
| `spec_miller_ota_ihp_easy` | **PASS** | callable | ngspice-osdi | ihp_sg13g2 | 0.11 | 1.00 | Adc_dB=32.5 in range (margin=7.5); GBW_Hz=1.385e+06 in range (margin=8.85e+05) |
| `tb_miller_ota_ac_runs` | **PASS** | callable | ngspice-osdi | ihp_sg13g2 | 0.11 | 1.00 | adapter_runtime_s=0.11; Adc_dB=32.5 in range (margin=31.5) |
| `tb_miller_ota_deck_emits_ac_meas` | **PASS** | callable | ngspice-osdi | ihp_sg13g2 | 0.11 | 1.00 | ngspice ok in 0.11s; measurements=['Adc_dB', 'GBW_Hz', 'PM_deg']; adapter_runtime_s=0.11 |

