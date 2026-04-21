# Using eda-agents as a public MCP server

`eda-agents` ships a FastMCP server that exposes skills and topology
workflows (gm/ID sizing, SPICE evaluation, autoresearch, digital
RTL-to-GDS, analog composition) as MCP tools. Any MCP-capable client
can use it — this guide covers [opencode](https://opencode.ai) and
[Claude Code](https://claude.ai/code).

## 1. Install

```bash
pip install -e "git+https://github.com/Mauricio-xx/eda-agents.git#egg=eda-agents[mcp]"
```

The `[mcp]` extra pulls in `fastmcp`, `litellm` (for the LLM-powered
tools `recommend_topology` and `run_autoresearch`), and `httpx` (for
on-demand LUT downloads).

Once installed, the `eda-mcp` console script is on your `PATH`:

```bash
eda-mcp --help      # stdio transport, no args needed for MCP clients
```

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

### opencode

Create `opencode.json` in your project root:

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

The repo ships `opencode.json.example` with this exact content. Copy
it: `cp opencode.json.example opencode.json`.

### Claude Code

Two equivalent options. Either register globally via the CLI:

```bash
claude mcp add eda-agents -- eda-mcp
```

Or drop a project-level `.mcp.json` at the repo root (this is the
path Claude Code auto-loads, not `.claude/mcp.json`):

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

The repo ships `.mcp.json.example` with this exact content. Copy it:
`cp .mcp.json.example .mcp.json` (the real `.mcp.json` is gitignored
so per-user overrides don't dirty the tree).

#### Curated subagents

The repo also ships three Claude Code subagents under `.claude/agents/`
that front-load the eda-agents MCP into focused workflows:

| Agent | Purpose |
|-------|---------|
| `analog-topology-recommender` | Map a natural-language analog brief to a registered topology. MCP-only (no bash/write). |
| `analog-sizing-advisor` | gm/ID sizing loop against `describe_topology` + `evaluate_topology` + optional `run_autoresearch`. MCP-only. |
| `digital-testbench-author` | Write cocotb testbenches that survive RTL / gate-level / post-PnR-SDF. Has filesystem access. |

Claude Code's allowlist is per-server, not per-tool — each subagent
gets access to the full `eda-agents` MCP surface, with its prompt
body steering it to the right handful. The opencode equivalents at
`.opencode/agent/` use per-tool whitelisting; functionally the two
sets behave the same from a user's point of view.

## 4. Smoke test

From any MCP client connected to the server, call `list_skills` — it
should return the registered skill catalogue without an ImportError.
Then try `describe_topology` with `miller_ota` to confirm topology
metadata plumbing works.

If `eda-mcp` isn't on your `PATH` after install, check that the venv
with `eda-agents[mcp]` installed is activated (or invoke the script
with an absolute path inside your MCP-client config).
