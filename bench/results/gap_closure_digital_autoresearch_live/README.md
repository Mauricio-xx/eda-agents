# digital_autoresearch live-mode proof — 2026-04-16

Evidence dir for Gap #4 (S9-residual-closure session).

## Why this dir exists

Session S9-gap-closure closed gap #4 by replacing the
``NOT_IMPLEMENTED`` stub for ``digital_autoresearch_adapter`` with a
real wrapper around :class:`DigitalAutoresearchRunner`. Coverage was
provided by a **mock-mode** path
(``bench/tasks/end-to-end/digital_autoresearch_counter.yaml`` with
``mock_metrics_path``) that loads a pre-recorded ``FlowMetrics`` JSON
instead of running LibreLane. That kept CI honest but left the real
LLM → LibreLane → metrics path unexercised — the "cerrado con
caveat" from the close-out.

S9-residual-closure flips the switch. The new task
``bench/tasks/end-to-end/digital_autoresearch_counter_live.yaml``
has no ``mock_metrics_path``, so the adapter walks the full live
path: OpenRouter LLM proposals → LibreLane flow runs through signoff
(``Checker.KLayoutDRC``) → FlowMetrics extraction → greedy
keep/discard.

## Run

```bash
set -a && source /home/montanares/personal_exp/eda-agents/.env && set +a
PYTHONPATH=src .venv/bin/python scripts/run_bench.py \
    --task e2e_digital_autoresearch_counter_live --run-id gap4_live_proof
```

Wall-clock: **113.44 s** (≈2 budget × ~55 s LibreLane + LLM roundtrips).
Result: **PASS**, ``audit_passed=1.0``, ``iterations_kept=1/2``.

## What happened, eval by eval

``results.tsv`` (committed in this dir):

| eval | PL_TARGET_DENSITY_PCT | CLOCK_PERIOD (ns) | WNS (ns) | cells | area (um²) | power (mW) | wire (um) | FoM      | valid | status     |
|------|-----------------------|-------------------|----------|-------|------------|------------|-----------|----------|-------|------------|
| 1    | 40                    | 50                | 35.64    | 135   | 3600       | 0.381      | 576       | 9.62e+02 | true  | **kept**   |
| 2    | 50                    | 40                | 27.82    | 146   | 3600       | 0.473      | 549       | 8.01e+02 | true  | discarded  |

The LLM really **did** explore (density 40→50, clock 50→40). Eval 2
tightened the clock and bumped density, got a valid result (timing
still met, DRC still clean) but worse FoM than eval 1, so the greedy
loop discarded it. Eval 1's (40, 50) combo remained the "best" at
the end.

## Invariants verified by this run

- **Adapter reaches the real LLM.** The logger emits
  ``LiteLLM completion() model= anthropic/claude-haiku-4.5;
  provider = openrouter`` on both proposals. Model string is the
  runner default today; see
  ``memory/feedback_openrouter_model.md`` for the preferred
  provider pin.
- **Adapter injects PDK + PDK_ROOT + nix tools correctly.** Log
  lines confirm ``PDK=gf180mcuD`` and
  ``PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu`` plus the
  full nix tool chain (``yosys-0.62``, ``openroad-2026-02-17``,
  ``magic-8.3.581``, ``netgen-1.5.295``, ``klayout-0.30.2``). Gap
  #5's plumbing is exercised end-to-end.
- **LibreLane runs through signoff.** Both evals log GL sim compile
  + simulate, and land on ``Checker.KLayoutDRC``. No short-circuit.
- **Metrics come from the real run dir.** ``FlowMetrics`` captured
  WNS, cell count, area, power, wire length per eval — those are
  LibreLane outputs, not defaults.
- **Greedy keep/discard works on real data.** Eval 1 kept as best,
  eval 2 discarded for worse FoM, ``best_fom=961.56`` carried
  through to the audit metric.

## Integration test

``tests/test_digital_autoresearch_live.py`` exercises the same path
programmatically behind ``@pytest.mark.librelane``. It skips when
``OPENROUTER_API_KEY`` / LibreLane venv / GF180MCU-D PDK are
missing, so tool-less hosts stay unaffected.

## Files (committed)

- ``summary.json`` — bench summary as produced by ``run_bench.py``.
- ``report.md`` — markdown task table.
- ``program.md`` — the autoresearch "program" that the LLM sees
  (domain, FoM, spec, design space, reference). Captures what the
  LLM was *asked to optimize*.
- ``results.tsv`` — per-eval parameter + metric + status table.

## Reproducing

Requires simultaneously: OPENROUTER_API_KEY, LibreLane v3 venv, GF180
PDK, nix EDA tools on the host. The bench task returns FAIL_INFRA
(bench marks SKIPPED) when any is missing, matching the pattern of
the other LLM-dependent tasks (``spec_llm_miller_ota_ihp``).
