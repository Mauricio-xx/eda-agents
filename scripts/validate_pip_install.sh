#!/usr/bin/env bash
# Clean-venv smoke test for the public MCP pip install path.
#
# Builds a wheel from the current worktree, installs it with the
# [mcp] extra into a throwaway venv, and verifies that:
#   1. All packaged resources survive the build (driver script,
#      skill bundles, package modules).
#   2. The `eda-mcp` console entry point is installed and callable.
#   3. The server module imports without pulling in the editable
#      source tree as a fallback.
#
# Exits non-zero on any failure. Safe to run repeatedly; each run
# uses a fresh temp dir and cleans up on exit.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d -t eda-agents-smoke.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

echo "==> repo: $REPO_ROOT"
echo "==> tmp:  $TMP"

echo "==> creating clean venv"
python3 -m venv "$TMP/venv"
VENV_PY="$TMP/venv/bin/python"
VENV_PIP="$TMP/venv/bin/pip"
"$VENV_PIP" install --upgrade --quiet pip build

echo "==> building wheel"
"$VENV_PY" -m build --wheel --outdir "$TMP/dist" "$REPO_ROOT" >/dev/null

WHEEL="$(ls "$TMP/dist"/eda_agents-*.whl | head -n1)"
echo "==> wheel: $WHEEL"

echo "==> installing wheel with [mcp] extra"
"$VENV_PIP" install --quiet "${WHEEL}[mcp]"

echo "==> verifying imports"
"$VENV_PY" - <<'PY'
import pathlib
from eda_agents.mcp.server import run_server  # noqa: F401
from eda_agents.skills.analog import _load_markdown_bundle
from eda_agents.core.glayout_runner import _DRIVER_SCRIPT
from eda_agents.core.lut_fetcher import resolve_gmid_lut  # noqa: F401

assert pathlib.Path(_DRIVER_SCRIPT).exists(), (
    f"_DRIVER_SCRIPT missing from wheel: {_DRIVER_SCRIPT}"
)

# Sanity-check the skill bundles are packaged (miller_ota ships 3 parts).
content = _load_markdown_bundle("miller_ota", ["core", "sizing", "compensation"])
assert len(content) > 500, "miller_ota bundle looks empty"

print("imports OK; driver =", _DRIVER_SCRIPT)
PY

echo "==> verifying eda-mcp entry point"
EDA_MCP="$TMP/venv/bin/eda-mcp"
test -x "$EDA_MCP" || { echo "FAIL: eda-mcp not installed at $EDA_MCP"; exit 1; }
echo "eda-mcp: $EDA_MCP"

echo ""
echo "pip-install smoke test passed"
