# S12-B Gap 5 live bench — 4-bit current-steering DAC (honest-fail)

## Mode: honest-fail (user-confirmed acceptable outcome)

First live exercise of `AnalogCompositionLoop` on a target outside
the registered-topology set. Per the S12-B plan, honest-fail is a
first-class outcome: we measure the loop's convergence trajectory,
diagnose the ceiling, and commit the evidence.

## Target

NL description:

> A 4-bit binary-weighted current-steering DAC on IHP SG13G2 1.2 V.
> Four NMOS current sources sized as 1x, 2x, 4x, 8x unit currents
> (LSB = 1 µA, MSB = 8 µA). Each source steered to either the
> positive (IOP) or negative (ION) output leg by a pair of NMOS
> differential switches whose gates are the 4-bit control inputs
> B0..B3. The outputs sum on a pair of sense resistors to produce a
> differential analog current. Target: INL < 0.5 LSB, DNL < 0.5 LSB,
> static op point.

Constraints:
```json
{"supply_v": 1.2, "lsb_current_uA": 1.0, "n_bits": 4,
 "inl_lsb_max": 0.5, "dnl_lsb_max": 0.5}
```

## Outcome

| metric | value |
|---|---|
| converged | **false** |
| honest_fail_reason | `loop exhausted 8/8 iterations without a 'converged' verdict` |
| iterations_run | 8 / 8 |
| total_tokens | 220,169 |
| total_cost_usd | **$0.066** |
| total_time_s | 203 |
| last_iteration_verdict | `patch` (loop was still iterating when budget hit) |
| model | `google/gemini-2.5-flash` |
| attempt_layout | true (never fired — SPICE never passed all specs) |

## What worked

- **Composition synthesis**: the loop translated the NL into a
  13-sub-block composition (1 reference current mirror + 4 binary-
  weighted current mirrors + 8 switches) with full connectivity
  edges. Topologically correct on the first try.
- **Sizing**: 13 per-sub-block sizings emitted with W / L / fingers /
  multipliers across iterations. `multipliers` correctly scales 1, 2,
  4, 8 for the binary weighting.
- **Iteration discipline**: every iteration went propose → size →
  simulate → critique. Each critique identified specific issues
  with the prior iteration's measurements (e.g. *"the testbench was
  trying to measure current through nodes 'IOP' and 'ION' directly,
  which are not voltage sources"*) and proposed a concrete
  testbench change.
- **Budget hygiene**: $0.066 spend, 203 s wall-clock for 8 iterations.
  Well under the $10 ceiling. Gemini Flash scaled fine.
- **Persistence**: `loop_state/program.md`, `loop_state/iterations.jsonl`,
  `loop_state/result.json` captured every stage's payload; each
  iteration has its own `iter_<N>/composition.cir` +
  `target_specs.json`.

## Where the loop ceilinged

SPICE measurement friction. The loop never produced a populated
`pass_per_spec` dict — ngspice `.meas` directives and the loop's
measurement extraction didn't align. Trajectory:

| iter | spice.ran | spice.success | measurements | note |
|---|---|---|---|---|
| 0 | true | false | `{}` | ngspice exited 1 — measurement nodes were wrong |
| 1 | true | **true** | `{}` | ran to completion but no values extracted |
| 2 | true | true | `{}` | same — LLM changed meas syntax, parser still empty |
| 3 | true | true | `{}` | same |
| 4 | true | false | `{}` | back to ngspice error (single-transient DAC code sweep) |
| 5-7 | true | false | `{}` | oscillating around the same meas failure |

The critique at iter 3 flagged it clearly:

> *"The current testbench attempts to simulate all codes in a single
> transient run, which is not suitable for a purely static INL/DNL
> measurement. A DC sweep or separate op-point runs per code would be
> more appropriate."*

The loop **correctly diagnosed** the structural issue but couldn't
converge because the LLM's proposed DC-sweep measurement pattern
didn't round-trip through our deck renderer's `.meas` handling (see
next section).

## Diagnosed blockers (concrete follow-ups)

### 1. Testbench format / `.meas` parser alignment

`AnalogCompositionLoop._write_spice_deck` currently:

- Treats `testbench.measurements` as raw ngspice `.meas` lines,
  stripped of leading `.` and inserted into `.control`.
- Builds a single analysis (`tran` / `ac` / `op`) from
  `testbench.analysis`.

What the LLM wanted (iter 3+):

- A multi-step sweep — loop over 16 DAC codes, set B0..B3 to each
  code, `op` per step, capture IOP / ION per code, post-process to
  INL / DNL.

ngspice's native scripting (`foreach` in `.control`) supports this,
but the loop's current renderer doesn't know how to turn a structured
"per-code-sweep" intent into that script. Two paths:

- **Extend the renderer** to accept a `sweep` testbench section
  (`{"type": "code_sweep", "codes_bits": ["B0", "B1", "B2", "B3"],
  "measurements_per_code": [...]}`) and emit the `.control` foreach
  boilerplate.
- **Trust the LLM to emit full .control scripts** (the raw SPICE
  approach) and just paste them verbatim when the LLM tags a
  testbench field as `"script": "..."`. Simpler but harder to
  sanity-check.

### 2. Measurement parsing

Even when ngspice ran (iters 1-3, `spice.success=true`), the
`measurements` dict came back `{}`. `SpiceRunner.run` parses `.meas`
output via regex against `stdout`; if the LLM's measurement names
don't follow the pattern the parser expects
(`^[A-Za-z_]\w*\s*=\s*<number>`), they silently drop. The LLM's
"IOP_current_code_0" style names should work, but if the measure
prints fewer sig-figs or uses scientific notation with spaces, the
regex misses. Follow-up: widen
`src/eda_agents/core/spice_runner.py:_MEAS_LINE_RE` or add a
fallback parser that reads `ngspice` log sections.

### 3. Top-level layout placer

`attempt_layout=True` was set but never fired (gated on `spice_all_pass`).
The per-sub-block generation path is in place
(`_generate_layouts` calls `GLayoutRunner.generate_component` for
each block), but once we have per-sub-block GDSes we still need a
thin placer to stitch them into one top cell. Out of scope for this
session (noted in the S12-B plan).

## Reproduction

```bash
cd /home/montanares/personal_exp/eda-agents
# or a worktree. Env has OPENROUTER_API_KEY (loaded from .env).
.venv/bin/python bench/results/s12b_custom_composition_live_i4dac/run_bench.py
```

Output artefacts:

- `summary.json` — this file's numeric summary (regenerated per run).
- `loop_state/result.json` — full serialised `AnalogCompositionResult`
  including every iteration record.
- `loop_state/program.md` — human-readable narrative log.
- `loop_state/iterations.jsonl` — one JSON object per iteration.
- `loop_state/iter_<N>/composition.cir` + `target_specs.json` —
  per-iteration SPICE deck + target specs.

## Why this is a valid outcome

Per the S12-B plan (Gap 5 acceptance, user-confirmed):

> Best case: one NL target → LVS-clean GDS + passing SPICE sim.
> Realistic: rigorous honest-fail analysis with convergence
> trajectory (best sim result, closest LVS mismatch count, what the
> critique-proposed patch got wrong). Honest-fail is a first-class
> outcome.

This run is the **realistic** case. The infrastructure is proven:
NL → composition → sizing → SPICE → critique → patch works
end-to-end. The composition the loop landed on is topologically
reasonable. The measurement format is the concrete blocker; fixing
it is a targeted follow-up, not an architectural rewrite.

Per the honest-fail discipline in `docs/skills/custom_composition/
iteration.md`, we **do not** fabricate a "close enough" pass. Shipped
as honest-fail + detailed diagnosis.
