#!/usr/bin/env bash
# install_eda_agents.sh — fresh-machine bootstrap for eda-agents MCP + agents.
#
# What it does (idempotent):
#   1. Installs Python 3.12 via deadsnakes PPA if missing (Ubuntu only).
#   2. Installs pipx if missing.
#   3. Installs eda-agents[mcp] via pipx (or upgrades if already installed).
#   4. Prompts for an LLM API key (hidden input), stores it in
#      <project>/.env with mode 600, and adds .env to <project>/.gitignore.
#   5. Creates the project dir, designs dir, and runs eda-init.
#   6. Optionally pulls the hpretl/iic-osic-tools:next Docker image.
#
# What it does NOT do:
#   - Install Docker Engine (get it from docker.com; add yourself to the
#     docker group).
#   - Install opencode (npm install -g opencode) or Claude Code.
#   - Commit, transmit, or log your API key.
#
# Usage:
#   bash <(curl -sSL https://raw.githubusercontent.com/Mauricio-xx/eda-agents/main/scripts/install_eda_agents.sh)
#
#   The `bash <(...)` form keeps stdin connected to the terminal so the
#   interactive prompts work. A plain `curl ... | bash` breaks the prompts.
#
# Tested on Ubuntu 22.04 (jammy) and 24.04 (noble).

set -euo pipefail

# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #

ORANGE=$'\033[38;5;214m'
GREEN=$'\033[0;32m'
RED=$'\033[0;31m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

info()  { printf "%b[i]%b %s\n"  "${ORANGE}" "${RESET}" "$*"; }
ok()    { printf "%b[ok]%b %s\n" "${GREEN}"  "${RESET}" "$*"; }
warn()  { printf "%b[!]%b %s\n"  "${ORANGE}" "${RESET}" "$*"; }
fail()  { printf "%b[x]%b %s\n"  "${RED}"    "${RESET}" "$*" >&2; }

die()   { fail "$*"; exit 1; }

require_interactive() {
    if [[ ! -t 0 ]]; then
        die "This script needs an interactive terminal for the API-key prompt. \
Run it as: bash <(curl -sSL <url>)  — not as: curl <url> | bash"
    fi
}

# --------------------------------------------------------------------- #
# Step 0. Pre-flight
# --------------------------------------------------------------------- #

require_interactive

if [[ "${OSTYPE:-}" != linux-gnu* ]]; then
    die "Only Linux is supported by this script. macOS / WSL paths are not \
validated yet — install Python 3.12 + pipx manually, then run: \
pipx install 'eda-agents[mcp] @ git+https://github.com/Mauricio-xx/eda-agents.git'"
fi

if ! command -v apt &> /dev/null; then
    die "This script assumes apt (Debian/Ubuntu). For other distros, install \
Python 3.12 + pipx by hand and then: \
pipx install 'eda-agents[mcp] @ git+https://github.com/Mauricio-xx/eda-agents.git'"
fi

info "Starting eda-agents bootstrap on Linux (apt-based)."

# --------------------------------------------------------------------- #
# Step 1. Python 3.12
# --------------------------------------------------------------------- #

if command -v python3.12 &> /dev/null; then
    ok "python3.12 already installed: $(python3.12 --version)"
else
    info "Installing python3.12 via deadsnakes PPA (sudo required)..."
    sudo apt install -y software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt update
    sudo apt install -y python3.12 python3.12-venv
    ok "python3.12 installed: $(python3.12 --version)"
fi

# --------------------------------------------------------------------- #
# Step 2. pipx
# --------------------------------------------------------------------- #

if command -v pipx &> /dev/null; then
    ok "pipx already installed: $(pipx --version)"
else
    info "Installing pipx (sudo required)..."
    sudo apt install -y pipx
    pipx ensurepath
    ok "pipx installed."
fi

# Ensure ~/.local/bin is on PATH for THIS shell (pipx installs go there).
export PATH="${HOME}/.local/bin:${PATH}"

# --------------------------------------------------------------------- #
# Step 3. eda-agents
# --------------------------------------------------------------------- #

PIPX_SPEC='eda-agents[mcp] @ git+https://github.com/Mauricio-xx/eda-agents.git'

if command -v eda-mcp &> /dev/null && command -v eda-init &> /dev/null; then
    info "eda-agents already installed. Reinstalling from main to pick up any updates..."
    pipx install --python python3.12 --force "${PIPX_SPEC}"
else
    info "Installing eda-agents from git (takes a minute)..."
    pipx install --python python3.12 "${PIPX_SPEC}"
fi

command -v eda-mcp &> /dev/null || die "eda-mcp not on PATH after install."
command -v eda-init &> /dev/null || die "eda-init not on PATH after install."
ok "eda-mcp: $(command -v eda-mcp)"
ok "eda-init: $(command -v eda-init)"

# --------------------------------------------------------------------- #
# Step 4. Project scaffolding
# --------------------------------------------------------------------- #

DEFAULT_PROJECT="${HOME}/my-chip"
DESIGNS_DIR="${HOME}/eda/designs"

echo
info "Where should the eda-agents project be bootstrapped?"
printf "  Default: %s\n" "${DEFAULT_PROJECT}"
read -rp "  Path [press Enter for default]: " PROJECT_DIR
PROJECT_DIR="${PROJECT_DIR:-${DEFAULT_PROJECT}}"
PROJECT_DIR="${PROJECT_DIR/#\~/${HOME}}"

mkdir -p "${PROJECT_DIR}" "${DESIGNS_DIR}"
cd "${PROJECT_DIR}"

info "Running eda-init in ${PROJECT_DIR}..."
eda-init
ok "Project scaffolded in ${PROJECT_DIR}."

# --------------------------------------------------------------------- #
# Step 5. API key
# --------------------------------------------------------------------- #

echo
info "Which LLM backend will opencode / Claude Code use?"
echo "  1) Z.AI Coding Plan         -> ZAI_API_KEY         (recommended)"
echo "  2) OpenRouter (Gemini etc.) -> OPENROUTER_API_KEY"
echo "  3) Anthropic direct         -> ANTHROPIC_API_KEY"
echo "  4) Skip (set the key myself later)"
read -rp "  Enter 1 / 2 / 3 / 4: " PROVIDER_CHOICE

case "${PROVIDER_CHOICE}" in
    1) KEY_VAR="ZAI_API_KEY";         SUGGESTED_MODEL="zai-coding-plan/glm-5.1" ;;
    2) KEY_VAR="OPENROUTER_API_KEY";  SUGGESTED_MODEL="openrouter/google/gemini-2.5-flash" ;;
    3) KEY_VAR="ANTHROPIC_API_KEY";   SUGGESTED_MODEL="anthropic/claude-sonnet-4-6" ;;
    4) KEY_VAR="";                    SUGGESTED_MODEL="<provider-specific>" ;;
    *) die "Invalid choice: ${PROVIDER_CHOICE}" ;;
esac

ENV_FILE="${PROJECT_DIR}/.env"
GITIGNORE="${PROJECT_DIR}/.gitignore"

if [[ -n "${KEY_VAR}" ]]; then
    printf "  Paste your %s (input hidden, will not echo): " "${KEY_VAR}"
    read -rs API_KEY
    echo
    if [[ -z "${API_KEY}" ]]; then
        die "Empty API key. Re-run the script to try again."
    fi

    # Write / update .env
    if [[ -f "${ENV_FILE}" ]] && grep -q "^${KEY_VAR}=" "${ENV_FILE}"; then
        # Use sed with a portable delimiter (| is safe here — API keys won't contain it).
        sed -i "s|^${KEY_VAR}=.*|${KEY_VAR}=${API_KEY}|" "${ENV_FILE}"
        ok "Updated ${KEY_VAR} in ${ENV_FILE}."
    else
        printf "%s=%s\n" "${KEY_VAR}" "${API_KEY}" >> "${ENV_FILE}"
        ok "Wrote ${KEY_VAR} to ${ENV_FILE}."
    fi
    chmod 600 "${ENV_FILE}"

    # Make sure .env is gitignored in this project
    if [[ ! -f "${GITIGNORE}" ]] || ! grep -qxF ".env" "${GITIGNORE}"; then
        printf ".env\n" >> "${GITIGNORE}"
        ok ".env added to ${GITIGNORE}."
    fi

    # Clear from memory as best we can
    unset API_KEY
else
    info "Skipping API key. Before launching opencode, export one of:"
    echo "    export ZAI_API_KEY=..."
    echo "    export OPENROUTER_API_KEY=..."
    echo "    export ANTHROPIC_API_KEY=..."
fi

# --------------------------------------------------------------------- #
# Step 6. Optional Docker image pre-pull
# --------------------------------------------------------------------- #

echo
info "Pre-pull hpretl/iic-osic-tools:next now? (18.8 GB, 10-30 min)"
info "Recommended so the first LibreLane run does not stall."
read -rp "  Pull now? [y/N]: " PULL_CHOICE

if [[ "${PULL_CHOICE}" =~ ^[Yy]$ ]]; then
    if ! docker info &> /dev/null; then
        warn "Docker is not running or you are not in the docker group."
        warn "Fix: sudo usermod -aG docker \$USER  (then log out + back in)"
        warn "Skipping image pull. Re-run:  docker pull hpretl/iic-osic-tools:next"
    else
        docker pull hpretl/iic-osic-tools:next
        ok "Image ready."
    fi
else
    info "Skipped. When you are ready:  docker pull hpretl/iic-osic-tools:next"
fi

# --------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------- #

cat <<EOF

${BOLD}=============================================${RESET}
${GREEN}Setup complete.${RESET}

  Project : ${PROJECT_DIR}
  Designs : ${DESIGNS_DIR}
  Python  : $(python3.12 --version 2>&1)
  eda-mcp : $(command -v eda-mcp)
  eda-init: $(command -v eda-init)

${BOLD}Next steps:${RESET}

  cd ${PROJECT_DIR}
EOF

if [[ -n "${KEY_VAR}" ]]; then
cat <<EOF
  source .env                         # load the API key into the shell
  opencode --agent gf180-docker-digital -m ${SUGGESTED_MODEL}
EOF
else
cat <<EOF
  export <YOUR_KEY_VAR>=...           # e.g. ZAI_API_KEY, OPENROUTER_API_KEY
  opencode --agent gf180-docker-digital -m ${SUGGESTED_MODEL}
EOF
fi

cat <<EOF

  Or, for Claude Code:
      claude   # picks up .mcp.json + .claude/agents automatically
EOF

if [[ -n "${KEY_VAR}" ]]; then
cat <<EOF

${ORANGE}Your API key is stored at ${ENV_FILE} with mode 600 and is gitignored.
Never commit .env. Never paste it into chat.${RESET}
EOF
fi

cat <<EOF

Reference: docs/mcp_public_install.md in the eda-agents repo.
EOF
