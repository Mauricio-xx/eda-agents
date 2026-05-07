# Design intent contract: define a new design without writing Python

`eda-agents` is agnostic infrastructure: program.md, the FoM weights,
and the validity gates are *data*, not code. Once you have written the
data for a new design, the same autoresearch loop, the same
`DigitalAutoresearchRunner`, and the same backend dispatch (`cc_cli`,
`opencode`, `litellm`, `adk`) drive the optimisation. **Do not write a
Python subclass for a new design unless you genuinely need a sidecar
measurement that the LibreLane flow does not expose.**

This page documents the data-only contract: what you maintain, where
it lives, and the boundary at which Python becomes unavoidable.

## What you maintain

For a new design `mydesign`:

1. `program.md` (in the autoresearch work dir) -- the LLM's persistent
   brain. The harness writes the initial template via
   `_autoresearch_core.generate_program_content`; you edit the
   `## Goal`, `## Specs`, and `## Strategy` sections to encode the
   design intent. Subsequent iterations of the autoresearch loop
   refine `## Current Best` and `## Learned So Far` automatically.

2. A LibreLane config (`config.yaml` or `config.json`) that contains:
   - the standard LibreLane keys (`DESIGN_NAME`, `VERILOG_FILES`,
     `CLOCK_PERIOD`, `DIE_AREA`, ...);
   - the testbench declaration -- mandatory:
     - `EDA_AGENTS_TB_DRIVER` (`cocotb` or `iverilog`);
     - `EDA_AGENTS_TB_TARGET` (cocotb make target or iverilog `.v`);
     - `EDA_AGENTS_TB_DIR`, `EDA_AGENTS_TB_ENV` (optional);
   - the FoM calibration -- optional, defaults to
     [`PpaProfile.GF180_EDUCATIONAL`](../src/eda_agents/core/flow_metrics.py):
     - `EDA_AGENTS_FOM_WEIGHTS: {timing_w, perf_w, area_w, power_w}`
       (see `FomWeights` Pydantic model in
       [`generic.py`](../src/eda_agents/core/designs/generic.py));
   - the domain gates -- optional:
     - `EDA_AGENTS_DESIGN_INTENT: {constants, constraints}` (see
       `DesignIntent` Pydantic model).

3. A cocotb testbench (or iverilog tb) that drives RTL, post-synth
   GL, and post-PnR-SDF simulation. The contract is enforced by
   `DigitalDesign.testbench()` being abstract since commit `e386cf2`.

4. (Optional) A cocotb sidecar that emits a custom measurement (e.g.
   `throughput_sps`, `bit_accuracy`, `cycles_per_op`) into the
   measurements dict. **This is the only step that requires Python.**

## When Python becomes unavoidable

Write a Python subclass of `DigitalDesign` (or extend `GenericDesign`)
only when the FoM gate references a measurement that LibreLane does
not natively report. The default measurement columns produced by
`DigitalDesign.extract_measurements` are:

| key                | source                         |
|--------------------|--------------------------------|
| `wns_worst_ns`     | LibreLane `timing__setup__ws`  |
| `cell_count`       | `design__instance__count`      |
| `die_area_um2`     | `design__die__area`            |
| `power_mw`         | `power__total` (mW)            |
| `wire_length_um`   | `route__wirelength`            |
| `clock_period_ns`  | `resolved.json` `CLOCK_PERIOD` |
| `drc_clean`        | derived from DRC counts        |
| `lvs_match`        | LVS pass                       |

Constraints over those keys, plus user-supplied constants, do not
require Python. Constraints that need throughput, latency-cycles,
bit-accuracy, energy-per-op, or any other quantity you have to run a
testbench to obtain *do* require a subclass that overrides
`extract_measurements()` to splice in the sidecar output.

The canonical example is
[`GoertzelDspDesign`](../src/eda_agents/core/designs/goertzel_dsp.py),
which adds `throughput_sps` from a cocotb sidecar.

## EDA_AGENTS_DESIGN_INTENT contract

Two siblings under one umbrella key:

```yaml
EDA_AGENTS_DESIGN_INTENT:
  constants:
    fs_target_hz: 8000.0
    max_um2: 640000.0
  constraints:
    - name: nyquist_floor
      expr: "throughput_sps >= fs_target_hz"
      message: "Throughput {throughput_sps:.0f} sps below floor {fs_target_hz:.0f}"
    - name: area_budget
      expr: "die_area_um2 <= max_um2"
      message: "die area {die_area_um2:.0f} um2 exceeds budget {max_um2:.0f}"
```

`constants` are floats (Pydantic-validated). They are reachable from
constraint expressions by name. They cannot collide with any name in
`DigitalDesign.measurement_columns()` -- doing so raises `ValueError`
at `GenericDesign(...)` construction.

`constraints` is a list of `{name, expr, message?}`. Each `expr` is
compiled by [`_constraint_eval.compile_expr`](../src/eda_agents/core/_constraint_eval.py)
at construction time so a syntax error or a disallowed AST node fails
fast (not 30 minutes into a flow). `message` may contain
`str.format`-style placeholders against the same scope; format
failures fall back to the raw `expr`.

## Constraint mini-DSL

The expression grammar is intentionally small. Top-level node must be
a comparison or a boolean combination -- a bare arithmetic expression
like `"x + 1"` is rejected at compile time so Python's truthiness does
not silently coerce it.

Supported:

| feature           | example                                |
|-------------------|----------------------------------------|
| numeric literals  | `1`, `1.5`, `1e6`                      |
| named variables   | `throughput_sps`, `fs_target_hz`       |
| arithmetic        | `+`, `-`, `*`, `/`, `%`, `**`          |
| unary             | `-x`, `+x`                             |
| comparisons       | `<`, `<=`, `>`, `>=`, `==`, `!=`       |
| chained compare   | `0 <= x <= 100`                        |
| boolean           | `and`, `or`                            |
| parentheses       | `(a + b) / 2 >= 5`                     |

`**` exponents must be literal integers with absolute value <= 64
(DoS guard against `x ** (10**18)` resource exhaustion).

Not supported:

* function calls (no `abs`, `min`, `max`, `log`, `pow`, ...);
* attribute access (`obj.attr`);
* subscripts (`arr[0]`);
* lambdas, comprehensions, conditional expressions;
* string literals or string operations;
* `not` (use `!=` for negation);
* control flow of any kind.

If you need a function, do the transform in `extract_measurements`
(write a Python subclass) and surface the result as a new
measurement key.

## Variable scope at evaluation time

For each `check_validity(measurements)` call, the scope is built by
merging:

1. all keys from `measurements` whose value is not `None`;
2. all keys from `EDA_AGENTS_DESIGN_INTENT.constants`.

A reference to a name that is in neither raises a constraint
violation (not a Python exception): the constraint is treated as
failed with the message `"<name>: Unknown variable '<var>'..."`. This
matches the precedent set by `GoertzelDspDesign.check_validity`,
where a missing sidecar measurement produces a violation rather than
a benefit-of-the-doubt pass.

A constraint that fails because the AST raised
`ConstraintEvalError` at evaluation (e.g. division by zero) becomes a
violation with the exception's message; it does not crash the
autoresearch loop.

## Minimal end-to-end example: pure area gate

[`tests/fixtures/intent/area_budget.yaml`](../tests/fixtures/intent/area_budget.yaml)
is the smallest data-only design intent: a die-area budget. It uses
only default measurements, so it is directly usable in production
without a Python subclass. Drop it under your project, point
`GenericDesign(config_path=...)` at it, hand the design to
`DigitalAutoresearchRunner`, and the loop will reject any flow run
whose `die_area_um2` exceeds the configured `max_um2`.

The DSP and IoT fixtures
([`dsp.yaml`](../tests/fixtures/intent/dsp.yaml),
[`iot.yaml`](../tests/fixtures/intent/iot.yaml)) demonstrate the
constraint format for designs whose FoM gate references a sidecar
measurement (`throughput_sps`, `samples_per_sec`); they are
documentation templates, not production-ready as-is.

## How program.md, FoM weights, and constraints connect

The autoresearch loop reads:

* `DigitalDesign.specs_description()` -> appended into
  `program.md ## Specs` (declares the design intent the LLM sees);
* `DigitalDesign.fom_description()` -> appended into
  `program.md ## Goal` (declares the optimisation target);
* `DigitalDesign.design_vars_description()` -> appended into
  `program.md ## Design Space` (declares what the LLM may tune).

`GenericDesign.specs_description()` automatically appends domain
gate names from `EDA_AGENTS_DESIGN_INTENT.constraints` so the LLM
sees them. `GenericDesign.fom_description()` reflects the effective
FoM weights (after the GF180_EDUCATIONAL profile + config + constructor
merge).

The result: a new design without a Python subclass produces a
program.md that already encodes the gates and the goal correctly.

## See also

* [`flow_metrics.py`](../src/eda_agents/core/flow_metrics.py)
  -- `PpaProfile`, `GF180_EDUCATIONAL`, `LOW_POWER_KHZ`, the FoM
  formula.
* [`generic.py`](../src/eda_agents/core/designs/generic.py)
  -- `GenericDesign`, `FomWeights`, `Constraint`, `DesignIntent`.
* [`_constraint_eval.py`](../src/eda_agents/core/_constraint_eval.py)
  -- the AST whitelist evaluator, including the operator and node
  whitelists.
* [`_autoresearch_core.py`](../src/eda_agents/agents/_autoresearch_core.py)
  -- `generate_program_content` (program.md template).
* [`tests/fixtures/intent/`](../tests/fixtures/intent/) -- the
  three starter templates referenced above.
