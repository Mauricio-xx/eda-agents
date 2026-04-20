# S12-A Gap 2 — cocotb GL sim LIVE GATE

**Status: GREEN (Pass@1 = 100%)**.

`scripts/run_bench.py --task e2e_idea_to_digital_counter_cocotb_live`
on GF180MCU-D, with `EDA_AGENTS_ALLOW_DANGEROUS=1` and
`PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu`.

| metric | value |
|---|---|
| status | PASS |
| cost_usd | 1.07 |
| wall_time_s | 224.8 (~3.7 min) |
| num_turns | 4 (CC CLI sub-turns) |
| prompt_length | 17 856 chars |
| gds_exists | 1 |
| gl_post_synth_ok | 1 (real PASS, not skip) |
| gl_post_pnr_ok | 1 (real PASS, not skip) |
| post_synth GL sim | 1.01 s, cocotb summary `TESTS=1 PASS=1 FAIL=0 SKIP=0` |
| post_pnr GL sim | 1.02 s, cocotb summary `TESTS=1 PASS=1 FAIL=0 SKIP=0`, SDF warnings = 0 |

## Agent verdict

```
design:  counter4_cocotb (4-bit synchronous up-counter, async-low reset, enable)
RTL:     src/counter4_cocotb.v
TB:      tb/test_counter4_cocotb.py + tb/Makefile (cocotb 2.x, SIM=icarus)
config:  config.yaml (LibreLane GF180MCU-D, generated from template)
flow:    LibreLane RTL -> GDS, signoff clean (DRC + LVS + STA all pass)
gl sim:  post-synth + post-PnR via the new GlSimRunner cocotb backend
verdict: TAPEOUT READY
```

## What this proves

S11 Fase 1.5 left the cocotb path with `gl_sim_skipped=1.0` because
`GlSimRunner` was iverilog-only — the same testbench could not be
re-driven against post-synth or post-PnR netlists. The S11
audit gate on this task was intentionally weaker than the iverilog
variants (asserted skip rather than pass).

S12-A Gap 2 closes that gap: the same cocotb test that ran pre-synth
now runs end-to-end against the post-synth and post-PnR gate-level
netlists via a generated Makefile in the GL-sim work dir. The
audit metrics on this YAML are now the same triple
(`gds_exists`, `gl_post_synth_ok`, `gl_post_pnr_ok`) the iverilog
variants assert. There is no longer a feature-flag gap between the
two TB flavours.

## Honest diagnostics — first two attempts

The third attempt closed clean. The first two failed; both failures
are documented honestly here so the regression test that landed
with the fix has context.

**Attempt 1 (12:20 UTC, FAIL_AUDIT, $0.17 spent):**
Claude CLI returned 429 ("You've hit your limit · resets 2pm
Europe/Prague") on the agent's second turn. This is the same
rate-limit footgun that hit the S11 FFT first attempt — documented
in the S12-A handoff as a known risk. No code defect; waited for
the quota reset.

**Attempt 2 (~14:07 UTC, FAIL_AUDIT, $1.04 spent):**
The agent succeeded — RTL + cocotb TB written, LibreLane signoff
clean, `gds_exists=1`. But both GL sim stages reported
`make sim exited with code 2` after only 5 ms each. Root cause:
the cocotb backend's PATH prepend used
`Path(librelane_python).resolve().parent`. `.resolve()` follows
the venv-python symlink back to the system interpreter
(`/home/montanares/git/librelane/.venv/bin/python` →
`/usr/bin/python3.12`), so the segment prepended to PATH was
`/usr/bin` — not the venv's `bin/`. `cocotb-config` only lives in
the venv, so `make`'s `$(shell cocotb-config --makefiles)` returned
empty, the include resolved to a non-existent path, and `make`
exited with code 2 immediately.

**Fix**: use the lexical `Path(librelane_python).parent` (skipped
when the path has no separator, to avoid prepending `.`). Locked
in by two new tests in
`tests/stages/test_gl_sim_runner.py::TestCocotbPostSynth`:
* `test_path_prepend_uses_lexical_parent_not_resolved_symlink` —
  builds a real symlinked python in tmp_path and asserts the
  lexical parent (not the symlink target) is on PATH.
* `test_path_prepend_skipped_for_bare_python_command` — asserts a
  bare `"python3"` does not get its useless / unsafe `.` parent
  prepended.

**Attempt 3 (14:14 UTC, PASS, $1.07 spent):**
With the PATH fix in place, the agent re-ran and the cocotb GL
sim closed both stages in ~1 s each. Total session spend on
Gap 2: $2.28.

## Files / artefacts

* `e2e_idea_to_digital_counter_cocotb_live/idea_to_chip_result.json`
  — full structured result (gl_sim block carries the new
  per-stage success/error/run_time/sdf_warnings).
* `e2e_idea_to_digital_counter_cocotb_live/config.yaml`
  — LibreLane config the agent generated.
* The `runs/RUN_*` LibreLane tree, agent-written `src/`, agent-
  written `tb/`, and per-stage `gl_sim/post_*/Makefile` are all
  reproducible from these inputs and stay gitignored under the
  s12a_cocotb_gl_sim_live allowlist.

## Reproduce

```bash
export PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu
export EDA_AGENTS_ALLOW_DANGEROUS=1
.venv/bin/python scripts/run_bench.py \
    --task e2e_idea_to_digital_counter_cocotb_live \
    --run-id s12a_cocotb_gl_sim_live
```
