#!/bin/bash
# LVS audit for a single GF180MCU biasgen cell.
# Runs THREE engines on the same (GDS, SPICE) pair and writes summary.json.
#
#   1. KLayout LVS     via  $PDK_ROOT/gf180mcuD/libs.tech/klayout/tech/lvs/run_lvs.py
#   2. Magic+Netgen PROJECT  (project-bundled gf180mcuD_setup.tcl)
#   3. Magic+Netgen PDK      (PDK-shipped gf180mcuD_setup.tcl)
#
# Designed to run INSIDE hpretl/iic-osic-tools:next container.
# Invoked with: bash lvs_one_cell.sh <cell_name> <cell_dir> <out_dir>

set -u  # do NOT set -e -- we want to survive per-engine failures.

CELL_NAME="${1:?cell name required}"
CELL_DIR="${2:?cell dir required}"
OUT_DIR="${3:?out dir required}"

# IIC-OSIC-TOOLS normally loads tool PATHs via the login shell profile. We
# resolve them explicitly so the script runs under `docker exec` without -l
# (which otherwise spams [INFO] lines and picks a non-GF180 PDK).
export PATH="/foss/tools/bin:/foss/tools/sak:/foss/tools/klayout:${PATH}"
export PYTHONPATH="/foss/tools/klayout/pymod:${PYTHONPATH:-}"
export PDK_ROOT="${PDK_ROOT:-/foss/pdks}"
export PDK="gf180mcuD"

GDS="$CELL_DIR/$CELL_NAME.gds"
SPICE_SRC="$CELL_DIR/$CELL_NAME.spice"
PROJECT_SETUP="$CELL_DIR/${PDK}_setup.tcl"
PDK_SETUP="$PDK_ROOT/$PDK/libs.tech/netgen/${PDK}_setup.tcl"

mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

# Gate: both files must exist
if [ ! -f "$GDS" ] || [ ! -f "$SPICE_SRC" ]; then
  cat > "$OUT_DIR/summary.json" <<EOF
{
  "cell": "$CELL_NAME",
  "ready": false,
  "reason": "missing $([ -f $GDS ] || echo .gds )$([ -f $SPICE_SRC ] || echo ' .spice') — not LVS-ready",
  "has_gds": $([ -f "$GDS" ] && echo true || echo false),
  "has_spice": $([ -f "$SPICE_SRC" ] && echo true || echo false)
}
EOF
  echo "SKIP $CELL_NAME — not LVS-ready"
  exit 0
fi

# ---------------------------------------------------------------------------
# 1) Magic extraction — shared by both Netgen runs.
#    We mirror the project's run_lvs.sh magic section verbatim so our
#    extracted layout netlist is identical to theirs.
# ---------------------------------------------------------------------------
mkdir -p "$OUT_DIR/magic"
pushd "$OUT_DIR/magic" >/dev/null
cp "$GDS" "$CELL_NAME.gds"
rm -rf extfiles
MAGIC_LOG="$OUT_DIR/magic/magic.log"
MAGIC_T0=$(date +%s)
magic -dnull -noconsole -rcfile "$PDK_ROOT/$PDK/libs.tech/magic/$PDK.magicrc" <<EOF >"$MAGIC_LOG" 2>&1
gds flatglob cap_mim
gds flatglob pfet*
gds flatglob nfet*
gds read $CELL_NAME
load $CELL_NAME
select top cell
extract path extfiles
extract all
ext2spice lvs
ext2spice merge conservative
ext2spice -p extfiles -o ${CELL_NAME}_layout.spice
quit -noprompt
EOF
MAGIC_RC=$?
MAGIC_T=$(( $(date +%s) - MAGIC_T0 ))
MAGIC_LAYOUT_SPICE="$OUT_DIR/magic/${CELL_NAME}_layout.spice"
popd >/dev/null

if [ ! -f "$MAGIC_LAYOUT_SPICE" ]; then
  MAGIC_OK=false
else
  MAGIC_OK=true
fi

# ---------------------------------------------------------------------------
# 2) Netgen LVS with PROJECT setup
# ---------------------------------------------------------------------------
run_netgen() {
  local tag="$1"            # project | pdk
  local setup="$2"
  local run_dir="$OUT_DIR/netgen_$tag"
  mkdir -p "$run_dir"
  if [ "$MAGIC_OK" != true ]; then
    echo "{\"tag\":\"$tag\",\"status\":\"skipped\",\"reason\":\"magic extraction failed\"}" > "$run_dir/result.json"
    return
  fi
  # Need the SC spice lib the project uses
  local SCLIB="$PDK_ROOT/$PDK/libs.ref/gf180mcu_fd_sc_mcu9t5v0/spice/gf180mcu_fd_sc_mcu9t5v0.spice"
  local COMP="$run_dir/${CELL_NAME}_comp.out"
  local TCL="$run_dir/run_lvs.tcl"
  cat > "$TCL" <<TCL
set pdklib $PDK_ROOT/$PDK
set sclib  $SCLIB
set setupfile $setup
set circuit1 [readnet spice $MAGIC_LAYOUT_SPICE]
set circuit2 [readnet spice \$sclib]
readnet spice $SPICE_SRC \$circuit2
lvs "\$circuit1 $CELL_NAME" "\$circuit2 $CELL_NAME" \$setupfile $COMP
TCL
  local t0=$(date +%s)
  local log="$run_dir/netgen.log"
  netgen -batch source "$TCL" >"$log" 2>&1
  local rc=$?
  local t=$(( $(date +%s) - t0 ))
  # Parse verdict
  local final match_status
  final=$(grep -E '^Final result:' "$COMP" 2>/dev/null | tail -1 | sed 's/^Final result: *//')
  match_status=$(grep -Ec 'match uniquely' "$COMP" 2>/dev/null || echo 0)
  python3 - "$COMP" "$run_dir/result.json" "$tag" "$rc" "$t" "$final" <<'PY'
import json, re, sys
from pathlib import Path
comp_path, out_path, tag, rc, t, final = sys.argv[1:]
text = Path(comp_path).read_text(errors='replace') if Path(comp_path).is_file() else ''
def grab(pattern, default=None):
    m = re.search(pattern, text)
    return m.group(1) if m else default
# Device / net counts from last Subcircuit summary
counts = {}
for prop, rx in [
    ('devices_c1', r'Number of devices:\s*(\d+)'),
    ('nets_c1',    r'Number of nets:\s*(\d+)'),
]:
    # Take the LAST (top-cell) occurrence
    matches = re.findall(rx, text)
    if matches:
        counts[prop] = int(matches[-1])
# Property errors
prop_errs = re.findall(r'([wlWL])\s+circuit1:\s*([\d.\-eE]+)\s+circuit2:\s*([\d.\-eE]+)\s+\(delta=([\d\.]+)%', text)
match = 'match uniquely' in text.lower()
property_error = 'match uniquely with property errors' in text.lower()
out = {
    'tag': tag,
    'engine': 'magic_netgen',
    'setup': 'project' if 'project' in tag else 'pdk',
    'rc': int(rc),
    'run_time_s': int(t),
    'final_result': final.strip() if final else None,
    'match': match,
    'property_error': property_error,
    'counts': counts,
    'property_errors_count': len(prop_errs),
    'comp_out': comp_path if Path(comp_path).is_file() else None,
}
Path(out_path).write_text(json.dumps(out, indent=2))
PY
}

run_netgen project "$PROJECT_SETUP"
run_netgen pdk     "$PDK_SETUP"

# ---------------------------------------------------------------------------
# 3) KLayout LVS (uses the PDK's run_lvs.py)
# ---------------------------------------------------------------------------
KLAYOUT_DIR="$OUT_DIR/klayout"
mkdir -p "$KLAYOUT_DIR"
KLAYOUT_SCRIPT="$PDK_ROOT/$PDK/libs.tech/klayout/tech/lvs/run_lvs.py"
KLAYOUT_LOG="$KLAYOUT_DIR/klayout.log"
KLAYOUT_T0=$(date +%s)
# Pick a python that has klayout.db available; the image exposes `klayout -b` +
# a standalone python. Try klayout's bundled python first, fall back to python3.
PYBIN=$(command -v klayout)
if [ -n "$PYBIN" ]; then
  # klayout -b can execute .py via -r but run_lvs.py uses docopt; easier to use
  # the klayout python wrapper. Fall through to python3 — the image's system
  # python3 already has klayout.db via pip in the IIC image.
  :
fi
pushd "$KLAYOUT_DIR" >/dev/null
# --lvs_sub=VSS collapses the synthetic `gf180mcu_gnd` substrate pin into VSS
# so the layout pin list matches xschem schematics (which reference VSS only).
# We intentionally omit --top_lvl_pins: that flag suppresses the SIMPLIFY
# pass, and without simplification the layout-extracted parallel fingers do
# not combine, producing a spurious topology mismatch vs the schematic.
python3 "$KLAYOUT_SCRIPT" \
    --layout="$GDS" \
    --netlist="$SPICE_SRC" \
    --variant=D \
    --lvs_sub=VSS \
    --run_dir="$KLAYOUT_DIR" \
    >"$KLAYOUT_LOG" 2>&1
KLAYOUT_RC=$?
KLAYOUT_T=$(( $(date +%s) - KLAYOUT_T0 ))
popd >/dev/null

python3 - "$KLAYOUT_LOG" "$KLAYOUT_DIR/result.json" "$KLAYOUT_RC" "$KLAYOUT_T" "$KLAYOUT_DIR" <<'PY'
import json, re, sys
from pathlib import Path
log_path, out_path, rc, t, run_dir = sys.argv[1:]
text = Path(log_path).read_text(errors='replace') if Path(log_path).is_file() else ''
low = text.lower()
# KLayout gf180 LVS emits either:
#   "Netlists match"                 -> success
#   "ERROR : Netlists don't match"   -> failure (rc still 0)
failure_marker = ("don't match" in low) or ("error : netlists" in low)
success_marker = ("netlists match" in low) and not failure_marker
match = success_marker and not failure_marker
# Try to extract the KLayout "LVS Total Run time X seconds"
lvs_time_m = re.search(r'lvs total run time\s+([\d\.]+)\s+seconds', low)
lvsdb = sorted(Path(run_dir).rglob('*.lvsdb'))
ext = sorted(Path(run_dir).rglob('*.cir'))
out = {
    'tag': 'klayout',
    'engine': 'klayout_lvs',
    'rc': int(rc),
    'run_time_s': int(t),
    'match': bool(match),
    'failure_marker_found': failure_marker,
    'success_marker_found': success_marker,
    'lvs_internal_s': float(lvs_time_m.group(1)) if lvs_time_m else None,
    'lvsdb': str(lvsdb[0]) if lvsdb else None,
    'extracted_cir': str(ext[0]) if ext else None,
    'log': log_path,
    'log_tail': text[-3000:] if text else '',
}
Path(out_path).write_text(json.dumps(out, indent=2))
PY

# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------
python3 - "$OUT_DIR" "$CELL_NAME" <<'PY'
import json, sys
from pathlib import Path
out_dir = Path(sys.argv[1])
cell = sys.argv[2]
def load(p):
    p = Path(p)
    if not p.is_file():
        return {'status': 'missing'}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        return {'status': 'parse_error', 'error': str(e)}
summary = {
    'cell': cell,
    'ready': True,
    'klayout':            load(out_dir/'klayout'/'result.json'),
    'magic_netgen_project': load(out_dir/'netgen_project'/'result.json'),
    'magic_netgen_pdk':     load(out_dir/'netgen_pdk'/'result.json'),
}
(out_dir/'summary.json').write_text(json.dumps(summary, indent=2))
print('WROTE', out_dir/'summary.json')
PY
