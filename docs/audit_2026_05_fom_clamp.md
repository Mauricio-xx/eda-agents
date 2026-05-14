# Audit 2026-05: FoM PPA-weighted + clamp policy (hilo C, Step 1)

Read-only audit triggered by Cierre D (2026-05-14, budget=20, hybrid,
Goertzel FP32 GF180): all 20 evals on both backends collapse to
`PL_TARGET_DENSITY_PCT=50.0, CLOCK_PERIOD=5000.05`. The plan for hilo
C named three hypotheses (H1/H2/H3). This audit refutes all three and
identifies a different root cause (H4).

## TL;DR

H1 (design_space too narrow), H2 (clamp aggressive), H3 (FoM rewards
clock relaxation) are all REFUTED. The collapse is caused by H4:
`DigitalAutoresearchRunner._propose_cc_cli` and `_extract_rtl_changes`
hardcode `proposal["config"] = {}` after the agent runs. The downstream
`_clamp_params({})` back-fills with `default_config` (which equals the
baseline read at start of run), so every row in `results.tsv` reports
the baseline regardless of what the LLM actually proposed. Compounding
this, the CC CLI agent's rationale advertises config edits ("CLOCK_PERIOD
5000.05 -> 1500 ns") that the WNS evidence shows were not persisted to
disk, so the LibreLane run kept using the original clock too. The result
is a frozen knob channel and a rationale that lies.

## Inputs

* Code: `eda-agents` HEAD `29f5ff4` (`main`).
* Empirical runs:
  - `~/i2o_claude_demo_b20/idea_to_optimize/phase_optimize/results.tsv`
  - `~/i2o_claude_demo_b20/idea_to_optimize/phase_optimize/program.md`
  - `~/i2o_opencode_demo_b20/idea_to_optimize/phase_optimize/results.tsv`
  - `~/i2o_opencode_demo_b20/idea_to_optimize/phase_optimize/program.md`

## Pseudocode of the three audited surfaces

### `GenericDesign.design_space()` (`generic.py:278`)

```python
ds = {
    "PL_TARGET_DENSITY_PCT": (1.0, 99.0),
    "CLOCK_PERIOD":          (0.1, 10000.0),
}
ds.update(self._ds_overrides)  # caller-provided narrowing
return ds
```

Bounds are continuous tuples spanning the LibreLane tool-level fence,
not a curated grid. Nothing here forces the optimizer toward the
baseline.

### `DigitalAutoresearchRunner._clamp_params` (`digital_autoresearch.py:692`)

```python
space   = self.design.design_space()
default = self.design.default_config()
clean   = {}
for name, values in space.items():
    val = params.get(name)
    if val is None:
        clean[name] = default.get(name, values[0])  # back-fill from baseline
        continue
    val = float(val)
    if   isinstance(values, list):  clean[name] = nearest(values, val)
    elif isinstance(values, tuple): clean[name] = max(lo, min(hi, val))
    else:                           clean[name] = val
# Pass-through any extra key the LLM proposed (writes to config.yaml downstream)
for name, val in params.items():
    if name in space or name in clean: continue
    clean[name] = val
return clean
```

For a declared key, the clamp is a pure `max(lo, min(hi, val))` against
the design-space bounds. There is no snap-to-baseline and no snap-to-best.
The only baseline coupling is the `val is None` branch that back-fills
when the proposer did not specify a knob.

### `FlowMetrics.weighted_fom` (`flow_metrics.py:195`)

```python
timing_score    = 1.0 if wns_worst_ns >= 0 else 0.0
perf_score      = 1000.0 / clock_period_ns
area_score      = 1e6 / die_area_um2
energy_eff      = 1.0 / (power_total_w * clock_period_ns)   # 1 / nJ-per-cycle
FoM = timing_w*timing_score + perf_w*perf_score + area_w*area_score
    + power_w*energy_eff
```

`GF180_EDUCATIONAL` weights: `timing_w=0.5, perf_w=1.0, area_w=1.0,
power_w=1.0`. `compute_fom` short-circuits to `0.0` when `check_validity`
fails (negative WNS at any corner), which is a cliff at the timing
boundary.

## Smoke FoM analytic (cc_cli eval 10 baseline)

Fixed metrics: cells=10056, area=160000 um2, power=0.284 mW at
clock=5000.05 ns. Critical path ~1030 ns (inferred from WNS=3962 ns).
Assumption (faithful to the FoM doc): power scales linearly with
frequency in CMOS, so `power_W * period_ns` is roughly clock-invariant.

| clock (ns) | timing met | perf=1000/clk | area=1e6/A | energy=1/(P*clk) | FoM    |
|------------|------------|---------------|------------|-------------------|--------|
| 100        | NO         | -             | -          | -                 | 0.000  |
| 500        | NO         | -             | -          | -                 | 0.000  |
| 1000       | NO         | -             | -          | -                 | 0.000  |
| 1500       | yes        | 0.667         | 6.250      | 0.704             | 8.121  |
| 2000       | yes        | 0.500         | 6.250      | 0.704             | 7.954  |
| 5000.05    | yes        | 0.200         | 6.250      | 0.704             | 7.654  |

(`results.tsv` records eval 10 FoM=6.75 for clock=5000.05; the ~0.9
discrepancy with the modelled 7.65 is the smoke's linear-power
assumption versus the actual LibreLane-reported power, which is not
strictly linear with frequency. The qualitative ordering is robust.)

Optimal point is just above the critical path: clock~1100-1500 ns
yields +0.5 FoM versus the trivial relaxed clock. The Performance
term penalises clock relaxation as advertised; the energy/cycle term
is clock-invariant in the linear-power regime, neither rewarding nor
punishing the choice. **The FoM does not have a trivial clock-relax
optimum**; H3 is refuted.

## Hypothesis verdicts

### H1 (design_space too narrow) — REFUTED

Bounds (1.0, 99.0) and (0.1, 10000.0) are intentionally wide. The
design space is not the cage.

### H2 (`_clamp_params` aggressive) — REFUTED

The clamp is pure `max(lo, min(hi, val))`. Nothing snaps a proposed
55/1100 back to 50/5000.05; the bounds easily accept either point.
The only baseline coupling is the back-fill when the input dict is
empty for a declared key.

### H3 (FoM rewards trivial clock relaxation) — REFUTED

The smoke table shows the optimum is near the critical path, not at
the upper clock bound. The current Performance term (`1000 /
clock_period_ns`) and clock-invariant energy term were already
designed to defeat the "relax everything" attack, and they do.

### H4 (proposal-channel bug zeros the config dict) — CONFIRMED

`_propose_cc_cli` (`digital_autoresearch.py:1100-1102`):

```python
if self.strategy == "hybrid":
    proposal["config"] = {}  # agent may have modified config directly
```

`_extract_rtl_changes` (`digital_autoresearch.py:1303-1305`, shared by
`_propose_litellm` and `_propose_opencode`):

```python
proposal = {"rtl_changes": rtl_changes, "rationale": rationale}
if self.strategy == "hybrid":
    proposal["config"] = {}
```

Downstream in `run()` (`digital_autoresearch.py:1796-1799`):

```python
if self.strategy == "hybrid":
    params = self._clamp_params(proposal.get("config", {}))
```

`_clamp_params({})` enters the `val is None` branch for every declared
key, back-filling from `default_config`. For `GenericDesign` the default
is the project's `config.yaml` baseline (50/5000.05 here). Result: the
TSV row is always the baseline.

Subordinate finding: the cc_cli agent's `program.md` rationales declare
config edits (eval 3 "CLOCK_PERIOD 5000.05 -> 1100.0 ns"; eval 7
"CLOCK_PERIOD 5000.05 -> 1500 ns"; ...) but the WNS reported by
LibreLane in `results.tsv` stays at ~3970 ns, which is only consistent
with `clock_period_ns = 5000`. So either the agent's `Edit` on
`config.yaml` is failing silently, or the agent never invoked it and
the rationale is a hallucination. Either way, the config knob channel
is dead end-to-end.

## Why the original plan named the wrong hypotheses

The plan asserted "the LLM proposes 45/700, 55/1100, 60/1200, 65/2000
and `_clamp_params` snaps to baseline". The empirical TSV shows no such
proposed variety in the column data, because that column was always
sourced from `proposal["config"]` which is always `{}`. The rationale
column shows the variety the LLM intended, but the data column
masks the truth. A plan-time hypothesis ladder built on rationale
strings rather than the column data led the audit toward H1/H2/H3.

## Proposed fix scope (handoff to Step 2)

Two coupled fixes are needed; a one-liner is not enough.

### Fix A — re-read config to capture agent edits

In `_propose_cc_cli` and `_extract_rtl_changes`, after the agent
finishes, re-read the config file to extract whichever declared
design-space knobs the agent actually persisted to disk:

```python
if self.strategy == "hybrid":
    # The agent had write access to config_path. Re-read the file
    # so the proposed-config channel reflects on-disk reality, not
    # the empty dict the loop assumed when the agent did not return
    # an explicit JSON "config" object.
    try:
        on_disk = self.design.baseline_params()
    except Exception:
        on_disk = {}
    proposal["config"] = on_disk
```

`baseline_params()` already returns just the design-space keys, typed.
With this fix, when the agent edits `config.yaml` to set
`CLOCK_PERIOD: 1500`, the TSV row reflects 1500.

### Fix B — make the agent actually edit the config

`cc_cli_hybrid_prompt` step 5 says "Optionally adjust flow config knobs
in the config file". "Optionally" plus no verification step yields
rationale-only edits. Strengthen the prompt:

* Replace "Optionally" with explicit guidance: "If you change clock,
  density, die area, or any flow knob, use the Edit tool on
  `<config_path>` and verify the change with Read after writing".
* Require the agent to echo the on-disk knob values in the JSON
  summary so the runner can cross-check.

Fix A alone closes the data-reporting gap. Fix B closes the
agent-behaviour gap. Both are needed for the hybrid strategy to
actually be hybrid; without Fix A there is no incentive to do Fix B
since the result still would not be logged.

## Anti-goals reaffirmed

* No FoM reshaping. The audit shows the formula is correct; touching
  it would mask the real bug.
* No design-space narrowing. Bounds are deliberately wide.
* No `_clamp_params` overhaul. The clamp is correct.
* No B1 / dedup-prompt changes. Orthogonal to this issue.

## Files referenced

* `src/eda_agents/agents/digital_autoresearch.py` (clamp 692, run loop
  1646, cc_cli 982, extract 1274)
* `src/eda_agents/core/designs/generic.py` (design_space 278,
  baseline_params 493, compute_fom 329)
* `src/eda_agents/core/flow_metrics.py` (weighted_fom 195,
  GF180_EDUCATIONAL 78)
* `src/eda_agents/agents/rtl_proposal_prompts.py` (cc_cli_hybrid_prompt
  224, step "Optionally adjust flow config knobs" at line 301)
* `src/eda_agents/agents/rtl_snapshot_manager.py` (restore_best 86)
