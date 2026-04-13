#!/usr/bin/env bash
# Fetch and pin digital design repos for the eda-agents framework.
#
# Clones target designs into $EDA_AGENTS_DIGITAL_DESIGNS_DIR (default:
# /home/montanares/git).  Each repo is pinned to the commit validated
# in Phase 0 (docs/digital_flow_field_notes.md §0).
#
# Idempotent: skips repos that already exist at the correct commit.
# Does NOT modify the eda-agents repo tree.

set -euo pipefail

DESIGNS_DIR="${EDA_AGENTS_DIGITAL_DESIGNS_DIR:-/home/montanares/git}"

# Pinned commits from Phase 0 validation (2026-04-11)
FAZYRV_REPO="https://github.com/meiniKi/gf180mcu-fazyrv-hachure.git"
FAZYRV_COMMIT="51047e63"
FAZYRV_DIR="${DESIGNS_DIR}/gf180mcu-fazyrv-hachure"

PRECHECK_REPO="https://github.com/wafer-space/gf180mcu-precheck.git"
PRECHECK_COMMIT="a7b75cb1"
PRECHECK_DIR="${DESIGNS_DIR}/gf180mcu-precheck"

SYSTOLIC_REPO="https://github.com/Essenceia/Systolic_MAC_with_DFT.git"
SYSTOLIC_COMMIT="c63eee5c"
SYSTOLIC_DIR="${DESIGNS_DIR}/Systolic_MAC_with_DFT"

clone_or_verify() {
    local name="$1" repo="$2" commit="$3" dir="$4"

    if [ -d "$dir" ]; then
        # Verify commit prefix matches
        local current
        current=$(git -C "$dir" rev-parse --short=8 HEAD 2>/dev/null || echo "unknown")
        if [[ "$current" == "$commit"* ]]; then
            echo "[OK] $name already at $commit in $dir"
            return 0
        else
            echo "[WARN] $name exists at $current (expected $commit). Not modifying."
            echo "       To update: cd $dir && git fetch && git checkout $commit"
            return 0
        fi
    fi

    echo "[CLONE] $name -> $dir (pinned to $commit)"
    git clone "$repo" "$dir"
    git -C "$dir" checkout "$commit" --quiet

    # Initialize submodules if present
    if [ -f "$dir/.gitmodules" ]; then
        echo "  Initializing submodules..."
        git -C "$dir" submodule update --init --recursive --quiet
    fi

    echo "[OK] $name cloned and checked out at $commit"
}

echo "Digital design repos — target dir: $DESIGNS_DIR"
echo "---"
mkdir -p "$DESIGNS_DIR"

clone_or_verify "fazyrv-hachure" "$FAZYRV_REPO" "$FAZYRV_COMMIT" "$FAZYRV_DIR"
clone_or_verify "gf180mcu-precheck" "$PRECHECK_REPO" "$PRECHECK_COMMIT" "$PRECHECK_DIR"
clone_or_verify "Systolic_MAC_with_DFT" "$SYSTOLIC_REPO" "$SYSTOLIC_COMMIT" "$SYSTOLIC_DIR"

echo "---"
echo "Done. PDK cloning is per-project (run 'make clone-pdk' inside each design)."
