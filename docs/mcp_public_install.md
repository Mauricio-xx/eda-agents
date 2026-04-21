# Using eda-agents as a public MCP server

`eda-agents` ships a FastMCP server that exposes skills and topology
workflows (gm/ID sizing, SPICE evaluation, autoresearch, digital
RTL-to-GDS, analog composition) as MCP tools. Any MCP-capable client
can use it — this guide covers [opencode](https://opencode.ai) and
[Claude Code](https://claude.ai/code).

## 0. Quickstart from a fresh Ubuntu machine

If you are starting with nothing and want the whole thing done for
you — Python 3.12, pipx, eda-agents, API-key handling, project
bootstrap — run:

```bash
bash <(curl -sSL https://raw.githubusercontent.com/Mauricio-xx/eda-agents/main/scripts/install_eda_agents.sh)
```

The script is interactive: it prompts for an API key (hidden input),
writes it to `<project>/.env` with mode `600`, and adds `.env` to the
project's `.gitignore`. Tested on Ubuntu 22.04 and 24.04.

It assumes Docker Engine is already installed and you are in the
`docker` group; it does not install `opencode` or Claude Code CLI
(grab those from their own sites). Use `bash <(...)` — not
`curl ... | bash` — or the interactive prompts will not see your
terminal.

If you prefer the manual path, step 1 below does the same work by
hand.

## 1. Install and bootstrap a project

```bash
pip install "git+https://github.com/Mauricio-xx/eda-agents.git#egg=eda-agents[mcp]"

mkdir my-chip && cd my-chip
eda-init
```

The `[mcp]` extra pulls in `fastmcp`, `litellm` (for the LLM-powered
tools `recommend_topology` and `run_autoresearch`), and `httpx` (for
on-demand LUT downloads). Two console scripts land on your `PATH`:

- `eda-mcp` — the MCP server that an MCP client spawns over stdio.
- `eda-init` — one-shot bootstrapper that drops the canonical
  `opencode.json`, `.mcp.json`, `.opencode/agent/*.md`, and
  `.claude/agents/*.md` into your project. Safe to re-run: it skips
  any file that already exists. Pass `--force` to overwrite.

After `eda-init`, opencode and Claude Code both see the eda-agents
MCP server and the curated subagents with zero further config.

## 2. Configure environment

`eda-agents` is PDK-aware. Tools that run SPICE, DRC, or LVS need a
local PDK install; LLM tools need a model backend key. Set what you
use:

| Variable | Required for | Notes |
|----------|--------------|-------|
| `PDK_ROOT` | SPICE / DRC / LVS / LibreLane | Must contain the active PDK's model files. |
| `EDA_AGENTS_PDK` | Optional | `ihp_sg13g2` (default) or `gf180mcu`. |
| `EDA_AGENTS_IHP_LUT_DIR` | IHP gm/ID lookups | Path to a clone of [ihp-gmid-kit](https://github.com/Mauricio-xx/ihp-gmid-kit). |
| `EDA_AGENTS_GMID_LUT_DIR` | Optional (GF180) | Overrides the download cache; point at a directory that already contains the `.npz` files. |
| `EDA_AGENTS_OFFLINE` | Optional | Set to `1` to disable auto-download of GF180 LUTs. |
| `EDA_AGENTS_GLAYOUT_VENV` | Analog layout | Path to a venv with gLayout installed. |
| `OPENROUTER_API_KEY` or `OPENAI_API_KEY` etc. | `recommend_topology`, `run_autoresearch` | Any LiteLLM-compatible provider. |

On first use of a GF180 gm/ID tool, eda-agents downloads the 73 MB of
LUTs from the project's GitHub Release into
`~/.cache/eda-agents/gmid_luts/` (or `$XDG_CACHE_HOME/eda-agents/`).

## 3. Wire up your MCP client

If you ran `eda-init` in step 1, the client configs already exist at
`opencode.json` and `.mcp.json`. Skip to step 4.

### opencode (manual setup)

`eda-init` writes `opencode.json` for you. The shipped template is:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "eda-agents": {
      "type": "local",
      "command": ["eda-mcp"],
      "enabled": true
    }
  }
}
```

### Claude Code (manual setup)

Two equivalent options. Either register globally via the CLI:

```bash
claude mcp add eda-agents -- eda-mcp
```

Or use the project-level `.mcp.json` that `eda-init` writes at the
repo root (this is the path Claude Code auto-loads, not
`.claude/mcp.json`):

```json
{
  "mcpServers": {
    "eda-agents": {
      "type": "stdio",
      "command": "eda-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

The real `.mcp.json` is gitignored so your per-project overrides stay
out of git.

#### Curated subagents

`eda-init` drops five Claude Code subagents under `.claude/agents/`
(and five opencode twins under `.opencode/agent/`) that front-load
the eda-agents MCP into focused workflows:

| Agent | Purpose |
|-------|---------|
| `analog-topology-recommender` | Map a natural-language analog brief to a registered topology. |
| `analog-sizing-advisor` | gm/ID sizing loop against `describe_topology` + `evaluate_topology` + optional `run_autoresearch`. |
| `digital-testbench-author` | Write cocotb testbenches that survive RTL / gate-level / post-PnR-SDF. |
| `gf180-docker-digital` | Drive an end-to-end GF180MCU RTL-to-GDS flow inside the `hpretl/iic-osic-tools` container via `docker exec`. Needs `Bash`. |
| `gf180-docker-analog` | Drive GF180 analog signoff (KLayout DRC + Magic/Netgen LVS) inside the same container. Needs `Bash`. |

The agents inherit the full tool surface (built-ins + every MCP
server you have configured) so they stay useful in interactive chat
— a user can ask the sizing advisor to inspect a `results.tsv` from
a prior autoresearch run without hitting a permission wall. The two
`gf180-docker-*` agents in particular rely on `Bash` to drive the
container; Claude Code's per-call approval prompts are the real
safety net when they issue `docker run` / `docker exec`.

If you need a **headless / budget-guard** variant (scripted runs
where no human approves each call), add a `tools:` allowlist to the
frontmatter — e.g. `tools: Read, Grep` for a read-only advisor, or
drop the MCP server name entirely to block costly calls like
`run_autoresearch`. Commit it alongside the permissive one.

## 4. Smoke test

From any MCP client connected to the server, call `list_skills` — it
should return the registered skill catalogue without an ImportError.
Then try `describe_topology` with `miller_ota` to confirm topology
metadata plumbing works.

If `eda-mcp` isn't on your `PATH` after install, check that the venv
with `eda-agents[mcp]` installed is activated (or invoke the script
with an absolute path inside your MCP-client config).
