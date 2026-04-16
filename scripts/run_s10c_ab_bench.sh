#!/usr/bin/env bash
# S10c skill-injection A/B bench driver.
#
# Runs the bench twice per seed against the LLM-backed digital counter
# task: once with skill injection enabled (post-S10c default) and once
# with it disabled via EDA_AGENTS_INJECT_SKILLS=0 (pre-S10c prompt).
# Miller OTA and SAR 11-bit are called once per condition as no-
# regression sanity for the deterministic callable adapters.
#
# Output: ``bench/results/s10c_{on,off}_<TS>_seed<i>/`` directories,
# one per (condition, seed) pair.  TS is emitted to stdout so a
# downstream comparison script (``scripts/compare_s10c_bench.py``) can
# pick up all 8 run-ids in a single sweep.
#
# Usage:
#     . .venv/bin/activate
#     . .env                       # brings in OPENROUTER_API_KEY
#     scripts/run_s10c_ab_bench.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# The repo .env uses plain ``KEY=value`` lines, so auto-export while
# sourcing it — otherwise OPENROUTER_API_KEY is only visible to the
# current bash and never reaches the Python subprocesses the adapter
# spawns.
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

TS="$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS="bench/results"
TASKS_LIVE=(--task e2e_digital_autoresearch_counter_live)
TASKS_ANALOG=(--task e2e_miller_ota_audit_ihp --task e2e_sar11b_enob_ihp)

# Pre-flight environment check. The live task short-circuits to
# SKIPPED inside the adapter if any of these are missing, which would
# waste the slot on "no-op" results, so we fail fast instead.
missing=()
[[ -n "${OPENROUTER_API_KEY:-}" ]] || missing+=("OPENROUTER_API_KEY")

# LibreLaneRunner auto-detects a venv under /home/montanares/git/librelane,
# so we don't require ``librelane`` on PATH — we just confirm the venv
# actually imports the package before burning the 5 h.
PYTHONPATH="$PWD/src" python - <<'PY' || missing+=("librelane venv (python -m librelane fails)")
import sys
sys.path.insert(0, "src")
from eda_agents.core.librelane_runner import _find_librelane_python
if _find_librelane_python() is None:
    sys.exit(1)
PY

# The GF180 PDK lives in its wafer-space fork, not inside ``$PDK_ROOT``
# which points at the IHP tree on this host. Use the bench resolver
# exactly as the live adapter does so the probe matches reality.
PYTHONPATH="$PWD/src" python - <<'PY' || missing+=("GF180 PDK for LibreLane (wafer-space fork)")
import sys
sys.path.insert(0, "src")
from eda_agents.bench.adapters import _resolve_librelane_pdk_root
if _resolve_librelane_pdk_root("gf180mcu") is None:
    sys.exit(1)
PY

if (( ${#missing[@]} > 0 )); then
  printf 'Pre-flight failed: missing %s\n' "${missing[*]}" >&2
  exit 2
fi

run_one() {
  local state="$1"
  local seed="$2"
  shift 2
  local tasks=("$@")
  local id="s10c_${state}_${TS}_seed${seed}"
  local inject
  if [[ "$state" == "on" ]]; then
    inject=1
  else
    inject=0
  fi
  local outdir="$RESULTS/$id"
  mkdir -p "$outdir"
  echo "=== $id (EDA_AGENTS_INJECT_SKILLS=$inject) ==="
  # Bench returns exit 1 when any task is FAIL, which is a legitimate
  # A/B data point we want to keep collecting past. Capture the code
  # explicitly so ``set -e`` doesn't abort the driver mid-sweep.
  local ec=0
  EDA_AGENTS_INJECT_SKILLS=$inject \
    python scripts/run_bench.py \
      "${tasks[@]}" \
      --run-id "$id" \
      --workers 1 \
      --verbose \
      >"$outdir/run.log" 2>&1 || ec=$?
  echo "done: $id (run_bench exit=$ec)"
}

# Analog sanity — one run per condition; the callable adapters are
# deterministic, so this proves the ngspice path still works end-to-
# end, not that skill injection affects anything.
run_one on  0 "${TASKS_ANALOG[@]}"
run_one off 0 "${TASKS_ANALOG[@]}"

# Digital live — the real A/B: three seeds per condition so the
# comparator has variance to measure.
for i in 1 2 3; do
  run_one on  "$i" "${TASKS_LIVE[@]}"
  run_one off "$i" "${TASKS_LIVE[@]}"
done

echo "TS=$TS"
echo "Comparison: python scripts/compare_s10c_bench.py --ts $TS"
