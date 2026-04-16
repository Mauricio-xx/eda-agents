#!/usr/bin/env bash
# Entrypoint for eda-agents-xspice container.
#
# On first invocation installs eda-agents editable from /work so that
# ``import eda_agents`` resolves to the bind-mounted source tree.
# Subsequent invocations reuse the cached metadata.
set -euo pipefail

if [ -f /work/pyproject.toml ]; then
    if ! pip show eda-agents >/dev/null 2>&1; then
        pip install --no-deps --quiet -e /work
    fi
else
    echo "[xspice_entrypoint] warning: /work does not contain pyproject.toml; is the repo bind-mounted?" >&2
fi

exec "$@"
