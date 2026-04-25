#!/bin/bash
# Host-side driver: loops every biasgen cell through lvs_one_cell.sh
# inside the gf180-chip-test container.
#
# Assumptions: `gf180-chip-test` container runs hpretl/iic-osic-tools:next
#              with /tmp/gf180-chip-test mounted at /foss/designs.
#
# The AutoMOS repo must already be cloned under
#     /tmp/gf180-chip-test/chipathon-lvs-audit/AutoMOS-chipathon2025/

set -euo pipefail

HOST_ROOT="/tmp/gf180-chip-test/chipathon-lvs-audit"
CONTAINER_ROOT="/foss/designs/chipathon-lvs-audit"
CONTAINER_NAME="${CHIPATHON_LVS_CONTAINER:-gf180-chip-test}"
LIB_REL="AutoMOS-chipathon2025/designs/libs/core_biasgen"
HOST_LIB="$HOST_ROOT/$LIB_REL"
CONTAINER_LIB="$CONTAINER_ROOT/$LIB_REL"

# Sanity
if [ ! -d "$HOST_LIB" ]; then
  echo "ERROR: $HOST_LIB not found. Did you clone AutoMOS-chipathon2025?"
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "ERROR: container '$CONTAINER_NAME' is not running."
  exit 1
fi

# Sync this script's sibling into the bind-mount in case we edited it.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp -f "$SCRIPT_DIR/lvs_one_cell.sh" "$HOST_ROOT/lvs_one_cell.sh"
chmod +x "$HOST_ROOT/lvs_one_cell.sh"

# Fresh runs dir
RUNS_DIR="$HOST_ROOT/runs"
mkdir -p "$RUNS_DIR"

# Enumerate cells
CELLS=()
for d in "$HOST_LIB"/*/; do
  CELLS+=("$(basename "$d")")
done

echo "=============================================================="
echo "Cells detected: ${#CELLS[@]}"
printf '  - %s\n' "${CELLS[@]}"
echo "=============================================================="

for cell in "${CELLS[@]}"; do
  echo
  echo ">>> $cell"
  cell_out="$RUNS_DIR/$cell"
  rm -rf "$cell_out"
  mkdir -p "$cell_out"
  docker exec "$CONTAINER_NAME" bash "$CONTAINER_ROOT/lvs_one_cell.sh" \
      "$cell" \
      "$CONTAINER_LIB/$cell" \
      "$CONTAINER_ROOT/runs/$cell" \
      2>&1 | tail -3 || true
done

echo
echo "Done. Artefacts under $RUNS_DIR/<cell>/"
