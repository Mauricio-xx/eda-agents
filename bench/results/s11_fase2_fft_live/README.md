# S11 Fase 2 extra — FFT 4-point live probe (INCONCLUSIVE)

Second Fase 2 target: 4-point real-input FFT with trivial twiddles
on GF180MCU-D. Scientific intent was to test single-shot + retry
reliability across DSP-regular dataflow designs (to complement the
compute-regular accumulator CPU probe).

## Result: INCONCLUSIVE (rate-limited by Claude subscription, NOT a
## model-capability failure)

Raw verdict from the bench runner: `FAIL_AUDIT` (metrics missing).
Root cause from the CLI result JSON:

```
"is_error": true,
"api_error_status": 429,
"result": "You've hit your limit · resets 2am (Europe/Prague)",
"duration_ms": 80891,
"num_turns": 3
```

The Claude Code CLI subscription used for the session (counter + ALU
+ CPU + FFT probes all in the same window) hit its quota
mid-generation on this FFT run. The agent never got far enough to
write RTL / run LibreLane / exercise the prompt's retry logic. The
adapter correctly surfaced the missing-metric failure; the bench
correctly refused to mark a fake PASS.

**This is NOT a finding about single-shot FFT capability.** It is a
finding about the operational constraint of long unattended runs
against a shared subscription. To characterise FFT single-shot
capability we need a re-run after the rate limit resets
(>= 2am Europe/Prague local) or with a higher-tier subscription.

## Why the other S11 Fase 2 probe stands

The accumulator CPU probe (`bench/results/s11_fase2_cpu_live/`)
passed BEFORE the rate limit hit. That run exercised the full
prompt + 3-iteration retry path + GL sim, produced a 1865-cell
signoff-clean GDS. Whatever this FFT run would have concluded, the
CPU probe already proves that single-shot scales past 2k cells on a
compute-shape design.

## Honest conclusion for Fase 2

- Closed for compute-shape (accumulator CPU, 1865 cells).
- Open for dataflow-shape (FFT). Rate-limit-blocked, not
  capability-disproven.

## How to resume

```bash
# After rate limit resets (local time >= 2am Europe/Prague):
export PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu
export EDA_AGENTS_ALLOW_DANGEROUS=1
.venv/bin/python scripts/run_bench.py \
    --task e2e_idea_to_digital_fft4_gf180_live \
    --run-id s11_fase2_fft_live_retry
```

The task YAML and adapter are already in place. Expect ~10-20 min
wall time + $1-5 LLM cost based on the CPU precedent.
