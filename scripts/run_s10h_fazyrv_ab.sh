#!/usr/bin/env bash
# S10h skill-injection A/B driver — fazyrv-hachure frv_1 on GF180MCU.
#
# Runs ``e2e_digital_autoresearch_fazyrv_live`` (budget 4 per run,
# nix-shell-wrapped LibreLane on the fazyrv worktree) 3 seeds per
# condition (EDA_AGENTS_INJECT_SKILLS = 1 vs 0). Wall-clock ~18 min
# per run, so ~110 min end-to-end.
#
# Usage:
#     . .venv/bin/activate
#     scripts/run_s10h_fazyrv_ab.sh

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

TS="$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS="bench/results"
TASK=(--task e2e_digital_autoresearch_fazyrv_live)
SEEDS="${S10H_SEEDS:-3}"

missing=()
[[ -n "${OPENROUTER_API_KEY:-}" ]] || missing+=("OPENROUTER_API_KEY")

PYTHONPATH="$REPO_ROOT/src" python - <<'PY' || missing+=("fazyrv worktree + bundled PDK")
import sys
sys.path.insert(0, "src")
from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign
d = FazyRvHachureDesign(macro="frv_1")
cfg = d.librelane_config()
pdk = d.pdk_root()
if not cfg.is_file():
    sys.exit(1)
if pdk is None or not pdk.is_dir():
    sys.exit(1)
PY

command -v nix-shell >/dev/null 2>&1 || missing+=("nix-shell on PATH")

if (( ${#missing[@]} > 0 )); then
  printf 'Pre-flight failed: missing %s\n' "${missing[*]}" >&2
  exit 2
fi

run_one() {
  local state="$1"
  local seed="$2"
  local inject
  if [[ "$state" == "on" ]]; then inject=1; else inject=0; fi
  local id="s10h_${state}_${TS}_seed${seed}"
  local outdir="$RESULTS/$id"
  mkdir -p "$outdir"
  echo "=== $id (EDA_AGENTS_INJECT_SKILLS=$inject) ==="
  local ec=0
  EDA_AGENTS_INJECT_SKILLS=$inject \
    python scripts/run_bench.py \
      "${TASK[@]}" \
      --run-id "$id" \
      --workers 1 \
      --verbose \
      >"$outdir/run.log" 2>&1 || ec=$?
  echo "done: $id (run_bench exit=$ec)"
}

echo "S10h A/B: ${SEEDS} seeds per condition against e2e_digital_autoresearch_fazyrv_live"

for i in $(seq 1 "$SEEDS"); do
  run_one on  "$i"
  run_one off "$i"
done

echo "TS=$TS"
echo "Comparison: python scripts/compare_s10h_fazyrv.py --ts $TS"
