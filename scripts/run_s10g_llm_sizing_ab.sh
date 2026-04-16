#!/usr/bin/env bash
# S10g skill-injection A/B driver — Miller OTA one-shot sizing.
#
# Runs ``spec_llm_miller_ota_ihp_ab`` (temperature 0.7) N seeds times
# per condition. Condition = value of EDA_AGENTS_INJECT_SKILLS. The
# task is one-shot (single LLM call -> 5 sizing knobs -> analytical
# miller designer + ngspice -> audit against Adc/GBW/PM thresholds),
# so "Pass@1 per seed" = did the LLM propose a valid sizing in a
# single attempt.
#
# Cheap and fast: ~5s per seed, ~$0.01 total for N=10 per condition.
#
# Usage:
#     . .venv/bin/activate
#     # .env brings OPENROUTER_API_KEY; auto-exported below.
#     scripts/run_s10g_llm_sizing_ab.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . .env
  set +a
fi

if [[ -z "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="$REPO_ROOT/src"
else
  export PYTHONPATH="$REPO_ROOT/src:$PYTHONPATH"
fi

SEEDS="${S10G_SEEDS:-10}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS="bench/results"
TASK=(--task spec_llm_miller_ota_ihp_ab)

missing=()
[[ -n "${OPENROUTER_API_KEY:-}" ]] || missing+=("OPENROUTER_API_KEY")
if (( ${#missing[@]} > 0 )); then
  printf 'Pre-flight failed: missing %s\n' "${missing[*]}" >&2
  exit 2
fi

run_one() {
  local state="$1"
  local seed="$2"
  local inject
  if [[ "$state" == "on" ]]; then inject=1; else inject=0; fi
  local id="s10g_${state}_${TS}_seed${seed}"
  local outdir="$RESULTS/$id"
  mkdir -p "$outdir"
  local ec=0
  EDA_AGENTS_INJECT_SKILLS=$inject \
    python scripts/run_bench.py \
      "${TASK[@]}" \
      --run-id "$id" \
      --workers 1 \
      >"$outdir/run.log" 2>&1 || ec=$?
  echo "done: $id (run_bench exit=$ec)"
}

echo "S10g A/B: ${SEEDS} seeds per condition against spec_llm_miller_ota_ihp_ab"

for i in $(seq 1 "$SEEDS"); do
  run_one on  "$i"
  run_one off "$i"
done

echo "TS=$TS"
echo "Comparison: python scripts/compare_s10g_llm_sizing.py --ts $TS"
