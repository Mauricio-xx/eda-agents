#!/usr/bin/env bash
# Run a command inside the eda-agents XSPICE toolchain container.
#
# Usage:
#   scripts/xspice_docker.sh                             # interactive shell
#   scripts/xspice_docker.sh pytest -m xspice tests/     # run tests
#   scripts/xspice_docker.sh bash -lc "ngspice --version"
#
# Environment:
#   XSPICE_IMAGE  Override image tag (default eda-agents-xspice:ng45).
#   XSPICE_REBUILD=1  Force ``docker build`` even if the image exists.
#
# The script builds the image lazily on first use. Rebuild on demand
# by passing ``XSPICE_REBUILD=1`` or by running ``docker image rm``.

set -euo pipefail

IMAGE="${XSPICE_IMAGE:-eda-agents-xspice:ng45}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "${XSPICE_REBUILD:-0}" = "1" ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "[xspice_docker] Building $IMAGE ..." >&2
    docker build \
        -f "$REPO_ROOT/docker/xspice.Dockerfile" \
        -t "$IMAGE" \
        "$REPO_ROOT"
fi

# Default command is an interactive bash login inside /work.
if [ "$#" -eq 0 ]; then
    set -- bash -l
fi

# Detect whether stdin is a TTY so we can enable -it only when it makes
# sense (non-interactive CI runs should not request a TTY).
DOCKER_FLAGS=(--rm)
if [ -t 0 ] && [ -t 1 ]; then
    DOCKER_FLAGS+=(-it)
fi

exec docker run "${DOCKER_FLAGS[@]}" \
    -v "$REPO_ROOT:/work" \
    -w /work \
    -e HOME=/tmp \
    -e PYTHONPATH=/work/src \
    "$IMAGE" "$@"
