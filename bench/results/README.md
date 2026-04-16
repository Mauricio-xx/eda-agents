# bench/results/

This directory is mostly gitignored. Only two things are committed:

- **`s9_initial_smoke/`** — the frozen Session 9 baseline run
  (9/11 PASS, 1 FAIL_SIM, 1 SKIPPED, Pass@1 = 90% excluding skipped).
  The canonical human-readable summary lives at
  [`s9_initial_smoke/report.md`](s9_initial_smoke/report.md). This is
  the number the README and CHANGELOG cite; do not overwrite it.
- **this `README.md`** — the explainer you are reading.

Every other run directory and the `latest.md` pointer are ignored by
git so that local re-runs do not dirty the repo. Re-create the pointer
by running:

```bash
PYTHONPATH=src python scripts/run_bench.py --run-id my_run
```

The runner will write `bench/results/my_run/` and a fresh
`bench/results/latest.md`. Both are local-only.

## Why the baseline is frozen

Session 9's smoke run surfaced a real blocker (GF180 Miller OTA
FAIL_SIM — see
[`docs/upstream_issues/miller_ota_gf180_process_params.md`](../../docs/upstream_issues/miller_ota_gf180_process_params.md))
and a deliberate skip (GL sim post-synth — the bench does not harden
digital designs). Keeping the original JSONs and artifacts on disk
lets reviewers audit the claim without re-running ngspice. A future
gap-closure session will re-baseline with the GF180 designer fix and
a real hardened run; until then this directory is the source of truth.
