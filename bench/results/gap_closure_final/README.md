# S9-gap-closure — final bench run

Captured at session close, with `OPENROUTER_API_KEY` sourced so the
real LLM task runs end-to-end (not SKIPPED).

## Summary

| metric   | value                         |
|----------|-------------------------------|
| total    | 16                            |
| PASS     | 16                            |
| FAIL     | 0                             |
| SKIPPED  | 0                             |
| ERROR    | 0                             |
| Pass@1   | **100%**                      |

`summary.json` and `report.md` next to this README are the
authoritative evidence.

## Reproducing

```bash
# .env is gitignored; source it so the LLM task PASSes instead of SKIPping.
set -a && source .env && set +a
PYTHONPATH=src .venv/bin/python scripts/run_bench.py --run-id gap_closure_final
```

Without the key, expect `total=16 PASS=15 SKIPPED=1` (the
`spec_llm_miller_ota_ihp` task's SKIP path). That is still an honest
green bench result for a tool-less host.
