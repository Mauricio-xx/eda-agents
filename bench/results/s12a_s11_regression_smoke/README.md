# S12-A — S11 regression smoke

**Status: GREEN (Pass@1 = 100%)** — proves S11 single-shot path
remains byte-equivalent under live LLM after both Gap 2 and Gap 1
land.

`scripts/run_bench.py --task e2e_idea_to_digital_counter_live`
on GF180MCU-D, with `EDA_AGENTS_ALLOW_DANGEROUS=1` and
`PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu`. This is the
**iverilog** TB path (no `tb_framework: cocotb` in the YAML), so
GlSimRunner takes its iverilog branch — the exact same code S11
shipped — not the new cocotb dispatch.

| metric | value | S11 baseline (`s11_fase0_live`) |
|---|---|---|
| status | PASS | PASS |
| cost_usd | 0.68 | 0.61 |
| wall_time_s | 155.3 (~2.6 min) | 164.2 (~2.7 min) |
| num_turns (CC CLI) | 11 | 12 |
| gds_exists | 1 | 1 |
| gl_post_synth_ok | 1 (iverilog `iverilog -g2012` + `vvp`) | 1 |
| gl_post_pnr_ok | 1 (iverilog `iverilog -g2012` + `vvp`) | 1 |

Cost / wall delta is within noise (different LLM session, different
prompt-cache state). Both runs hit the same audit gate.

## Why this matters

S12-A added two new code paths to the GL-sim machinery:

1. `GlSimRunner._detect_tb_flavour()` dispatches between iverilog
   and cocotb backends based on `tb/` filesystem contents.
2. The cocotb backend (`_run_cocotb_gl_sim`) generates a Makefile,
   shells out to `make sim`, parses cocotb's summary line.

The byte-equivalence claim was: when no cocotb files exist
(`tb/Makefile` absent), `_detect_tb_flavour` returns `"iverilog"`
and the runner falls through to the original iverilog path,
unchanged. Unit tests proved this in isolation; this live smoke
proves it under the real flow with the real Claude CLI generating
the artefacts.

Confirmed in the run log:

```
[INFO eda_agents.core.stages.gl_sim_runner] GlSimRunner compile:
  iverilog -g2012 -o .../gl_sim/post_pnr/sim.out ...
[INFO eda_agents.core.stages.gl_sim_runner] GlSimRunner simulate:
  vvp .../gl_sim/post_pnr/sim.out
```

— no `make sim` invocation, no cocotb-config shell-out. Iverilog
path intact.

The `IdeaToRTLLoop` wrapper is also bypassed for this task: the
YAML doesn't set `loop_budget`, so it defaults to `1`, and
`generate_rtl_draft` short-circuits past the loop dispatch (lazy
import never fires). Counted in the 15 unit tests but worth
re-confirming live: `result.loop_result is None` in
`idea_to_chip_result.json`.

## Reproduce

```bash
export PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu
export EDA_AGENTS_ALLOW_DANGEROUS=1
.venv/bin/python scripts/run_bench.py \
    --task e2e_idea_to_digital_counter_live \
    --run-id s12a_s11_regression_smoke
```
