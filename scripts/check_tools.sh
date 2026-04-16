#!/usr/bin/env bash
# Verify EDA tool versions and PDK roots required by eda-agents.
#
# Covers the tools and PDK paths needed across the roadmap (analog +
# digital). Checks are non-fatal individually: the script prints a
# coloured report and exits non-zero only if any mandatory check fails.
#
# Mandatory (block the flow): ngspice, yosys, magic, klayout, netgen,
# one of $PDK_ROOT / GF180 install.
# Optional (warn only): openvaf (needed from Session 2 onward).
#
# Version floors come from eda-agents' own gates:
#   - ngspice >= 38   (OSDI loader)
#   - yosys   >= 0.62 (LibreLane v3 compatibility; see Nix fallback)
#   - openvaf present (Session 2 Verilog-A -> OSDI pipeline)

set -u

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
RESET=$'\033[0m'

mandatory_fail=0
optional_warn=0

have() { command -v "$1" >/dev/null 2>&1; }

report_ok()   { printf '  %sOK%s     %s\n' "$GREEN" "$RESET" "$1"; }
report_warn() { printf '  %sWARN%s   %s\n' "$YELLOW" "$RESET" "$1"; optional_warn=$((optional_warn + 1)); }
report_fail() { printf '  %sFAIL%s   %s\n' "$RED" "$RESET" "$1"; mandatory_fail=$((mandatory_fail + 1)); }

# ------------------------------------------------------------------
# PATH with Nix EDA store dirs prepended, mirroring
# detect_nix_eda_tool_dirs() in src/eda_agents/agents/digital_autoresearch.py.
nix_prefix=""
for pat in \
    '/nix/store/*-yosys-with-plugins-0.6*/bin' \
    '/nix/store/*-openroad-202[56]*/bin' \
    '/nix/store/*-magic-*/bin' \
    '/nix/store/*-netgen-*/bin' \
    '/nix/store/*-klayout-*/bin'; do
    # shellcheck disable=SC2086
    match=$(ls -d $pat 2>/dev/null | sort -r | head -n1)
    if [[ -n "$match" ]]; then
        nix_prefix="${nix_prefix:+$nix_prefix:}$match"
    fi
done
if [[ -n "$nix_prefix" ]]; then
    export PATH="$nix_prefix:$PATH"
    echo "Nix EDA tools detected, prepended to PATH:"
    echo "  $nix_prefix" | tr ':' '\n' | sed 's/^/    /'
fi

echo
echo "=== SPICE ==="
if have ngspice; then
    ver=$(ngspice --version 2>&1 | awk '/ngspice-/{ for(i=1;i<=NF;i++) if ($i ~ /ngspice-/) { sub(/ngspice-/, "", $i); print $i; exit } }' | head -n1)
    if [[ -z "$ver" ]]; then
        ver=$(ngspice --version 2>&1 | head -n1)
    fi
    major=${ver%%.*}
    major=${major//[^0-9]/}
    if [[ -n "$major" ]] && (( major >= 38 )); then
        report_ok "ngspice $ver (>=38 required for OSDI)"
    else
        report_fail "ngspice $ver (need >=38 for OSDI; upgrade)"
    fi
else
    report_fail "ngspice not in PATH"
fi

echo
echo "=== Verilog-A compiler (optional until Session 2) ==="
if have openvaf; then
    ver=$(openvaf --version 2>&1 | head -n1)
    report_ok "openvaf: $ver"
else
    report_warn "openvaf not in PATH (needed from Session 2: Verilog-A -> OSDI)"
fi

echo
echo "=== Digital / physical tools ==="
if have yosys; then
    ver=$(yosys -V 2>&1 | awk '{print $2}' | head -n1)
    numeric=$(echo "$ver" | awk -F'[.+-]' '{printf "%d.%02d\n", $1, $2}' 2>/dev/null)
    if awk -v v="$numeric" 'BEGIN{ exit !(v+0 >= 0.62) }'; then
        report_ok "yosys $ver (>=0.62 required by LibreLane v3)"
    else
        report_fail "yosys $ver (need >=0.62 for LibreLane v3; use Nix)"
    fi
else
    report_fail "yosys not in PATH"
fi

# Helper: call tool with a short timeout and a version flag known to be
# non-interactive. klayout --version opens a GUI window on some systems,
# so we use -v and timeout to be safe.
_probe_version() {
    local tool="$1"
    local flag="$2"
    timeout 5s "$tool" "$flag" 2>&1 | head -n1 || echo "(version probe timed out)"
}

for tool in openroad magic klayout netgen; do
    if have "$tool"; then
        case "$tool" in
            klayout) ver=$(_probe_version "$tool" "-v" </dev/null) ;;
            magic)
                # Magic version lives in its install path rather than a CLI flag.
                path=$(command -v magic)
                ver="(in $path)"
                ;;
            *)       ver=$(_probe_version "$tool" "--version" </dev/null) ;;
        esac
        report_ok "$tool: $ver"
    else
        if [[ "$tool" == "openroad" ]]; then
            report_warn "$tool not in PATH (invoked by LibreLane; OK if only used via Nix shell)"
        else
            report_fail "$tool not in PATH"
        fi
    fi
done

echo
echo "=== PDKs ==="
pdk_ok=0
if [[ -n "${PDK_ROOT:-}" && -d "$PDK_ROOT" ]]; then
    if [[ -d "$PDK_ROOT/ihp-sg13g2" ]]; then
        report_ok "PDK_ROOT=$PDK_ROOT contains ihp-sg13g2/"
        pdk_ok=$((pdk_ok + 1))
    else
        report_warn "PDK_ROOT=$PDK_ROOT present but ihp-sg13g2/ missing"
    fi
    if [[ -d "$PDK_ROOT/gf180mcuD" || -d "$PDK_ROOT/gf180mcu" ]]; then
        report_ok "PDK_ROOT contains gf180mcuD/ or gf180mcu/"
        pdk_ok=$((pdk_ok + 1))
    else
        report_warn "PDK_ROOT has no gf180mcuD/; may use separate install"
    fi
else
    report_warn "PDK_ROOT not set or not a directory"
fi

if (( pdk_ok == 0 )); then
    report_fail "No usable PDK install detected (need at least IHP SG13G2 or GF180MCU)"
fi

echo
echo "=== Python baseline ==="
if have python3; then
    pyver=$(python3 -c 'import sys; print("{}.{}".format(sys.version_info[0], sys.version_info[1]))')
    major=${pyver%%.*}
    minor=${pyver##*.}
    if (( major > 3 || (major == 3 && minor >= 11) )); then
        report_ok "python3 $pyver (>=3.11 per pyproject.toml)"
    else
        report_fail "python3 $pyver (need >=3.11)"
    fi
else
    report_fail "python3 not in PATH"
fi

echo
if (( mandatory_fail > 0 )); then
    printf '%sFAILED%s: %d mandatory check(s) failed, %d warning(s).\n' "$RED" "$RESET" "$mandatory_fail" "$optional_warn"
    exit 1
fi
printf '%sOK%s: all mandatory checks passed, %d warning(s).\n' "$GREEN" "$RESET" "$optional_warn"
exit 0
