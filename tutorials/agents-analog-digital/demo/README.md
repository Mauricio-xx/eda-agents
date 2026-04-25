# Agents walk-throughs — analog + digital

Four hands-on notebooks. Two per loop: a **short** one that's safe to run in
a workshop, and a **deep** one tied to the matching chapter of the HTML deck.

These notebooks **depend on the `eda_agents` package** — the first cell in
each runs `pip install -e .` from your cloned repo root in an activated venv.
(The sibling `../../rtl2gds-gf180-docker/demo/` is standalone; these are not,
because they call into `eda_agents` classes like `AutoresearchRunner`,
`GenericDesign`, `DigitalAutoresearchRunner`.)

| Notebook                                       | Loop    | Depth  | Referenced from                       | Real wall time |
| ---------------------------------------------- | ------- | ------ | ------------------------------------- | -------------- |
| `agents_miller_ota.ipynb`                      | analog  | short  | combined deck · analog.html           | 2–3 min        |
| `agents_analog_topology_to_sizing.ipynb`       | analog  | deep   | analog.html, Part 8                   | ~15 min        |
| `agents_rtl2gds_counter.ipynb`                 | digital | short  | combined deck · digital.html          | 10–15 min      |
| `agents_digital_autoresearch.ipynb`            | digital | deep   | digital.html, Part 4 + 8              | 25–50 min      |

Each notebook has a plain-Python twin (`*.py`) with the same steps and
`input()` pauses — use it when you don't have Jupyter or for headless demos.

## Quickstart

```bash
# 1. Activate a venv at the repo root.
cd ~/personal_exp/eda-agents
python3 -m venv .venv && source .venv/bin/activate

# 2. Pick a notebook.
jupyter lab tutorials/agents-analog-digital/demo/agents_miller_ota.ipynb

# 3. In the first cell, flip RUN_PIP_INSTALL=True once per venv.
# 4. Walk the cells top to bottom. All RUN_* flags default to False,
#    so opening the notebook is a safe no-op.
```

## Analog loop

### `agents_miller_ota.ipynb` — short

Five steps, Miller OTA on IHP SG13G2 via `AutoresearchRunner` alone.
Budget 6, ~3 min, cents of LLM budget. Best for "the loop works on my
machine in under ten minutes".

### `agents_analog_topology_to_sizing.ipynb` — deep

Six steps; the full chain referenced by Part 8 of `analog.html`:

1. NL spec → `analog-topology-recommender` → topology JSON.
2. JSON → `analog-sizing-advisor` → starter sizing vector.
3. One SPICE eval at the starter (sanity).
4. `AutoresearchRunner(budget=8)` — greedy refinement.
5. `analog.corner_validator` — PVT sweep on the winner, back off on fail.
6. Tail `program.md` and `results.tsv`.

**Analog prerequisites:**

- `ngspice` on PATH.
- IHP-Open-PDK cloned, `PDK_ROOT` pointing at it, `ihp-sg13g2/` present.
- gm/ID LUT clone at `$EDA_AGENTS_IHP_LUT_DIR` (typically
  `~/git/ihp-gmid-kit`).
- LLM key: `OPENROUTER_API_KEY` or `ZAI_API_KEY`.

## Digital loop

### `agents_rtl2gds_counter.ipynb` — short

Wraps a minimal LibreLane config with `GenericDesign` and hands it to
`ProjectManager` (master ADK LlmAgent). Dry-run by default; real run
gated by `RUN_REAL=True`.

Uses the **same** 4-bit counter RTL + `config.yaml` as the sibling
`../../rtl2gds-gf180-docker/demo/rtl2gds_counter.ipynb`.

Note on backends: `ProjectManager` currently accepts `adk` and `cc_cli`
only. For an OpenCode end-to-end, either use the TUI
(`opencode --agent gf180-docker-digital`) or the autoresearch notebook
below (which DOES support `backend="opencode"`).

### `agents_digital_autoresearch.ipynb` — deep

`DigitalAutoresearchRunner` on the same counter. Knob sweep over
density (60 / 65 / 70), clock period (50 / 40 / 30 ns), PDN pitch
(4 / 6 / 8). Defaults to `backend="opencode"` with
`OPENCODE_MODEL="openrouter/google/gemini-3-flash-preview"` because
multi-provider is an economic feature for a loop that burns five
LibreLane runs per invocation.

Swap `BACKEND` to any of `adk | cc_cli | litellm | opencode` to switch
runtimes. Each uses the same design, same FoM, same results.tsv.

**Digital prerequisites:**

- Docker Engine; user in the `docker` group; ~15 GB disk.
- One of:
  - `OPENROUTER_API_KEY` / `GOOGLE_API_KEY` / `ZAI_API_KEY` — ADK /
    LiteLLM / OpenCode (with matching `-m`).
  - Claude Code CLI on PATH + Anthropic subscription — cc_cli backend.
  - OpenCode CLI on PATH (`npm install -g opencode-ai`) — opencode
    backend.

## Troubleshooting

### Analog

- **`SPICE failed: ...`** — Step 3 is the canary. If it fails,
  autoresearch fails the same way. Check `PDK_ROOT` points at a PDK tree
  with `ihp-sg13g2/libs.tech/ngspice/models/` present.
- **`ModuleNotFoundError: eda_agents`** — flip `RUN_PIP_INSTALL=True` in
  Step 0 for this venv, re-run the cell, restart the kernel.
- **LLM call hangs or 401s** — confirm the right env var is exported in
  the same shell you launched Jupyter from (`echo $OPENROUTER_API_KEY`).

### Digital

- **`docker: command not found`** — install Docker Engine, add your user
  to the `docker` group, log out and back in.
- **`hpretl image present: no`** — run
  `docker pull hpretl/iic-osic-tools:next` once (~15 GB); it's cached
  thereafter.
- **`opencode: command not found`** with `BACKEND="opencode"` —
  `npm install -g opencode-ai`. Verify with `opencode --version`.
- **`claude: command not found`** with `BACKEND="cc_cli"` —
  `npm install -g @anthropic-ai/claude-code`, or switch to
  `BACKEND="adk"` / `"opencode"` and set the matching key.
- **Real run times out** — bump `MAX_BUDGET_USD` and re-run. For IHP runs
  specifically, the Magic streamout stage can exceed 1 h; set
  `timeout=7200` when calling the script.

## Cleanup

```bash
# Short analog
rm -rf tutorials/agents-analog-digital/demo/autoresearch_miller_ota/

# Deep analog chain
rm -rf tutorials/agents-analog-digital/demo/analog_chain_results/

# Short digital
rm -rf tutorials/agents-analog-digital/demo/rtl2gds_counter_results/

# Deep digital autoresearch
rm -rf tutorials/agents-analog-digital/demo/digital_autoresearch_results/

# Optional: reclaim the container and image
docker rm -f gf180 2>/dev/null || true
docker image rm hpretl/iic-osic-tools:next   # ~15 GB
```

## Source of truth

Agent names, tool allowlists, skill namespaces, and backend enums come
from the current repo tip:

- `.claude/agents/*.md` and `.opencode/agent/*.md` — the five agents
  registered in both runtimes.
- `src/eda_agents/agents/digital_adk_agents.py` — `ProjectManager` +
  four specialists + `*_TOOLS` frozensets. Current `backend=` options:
  `"adk"`, `"cc_cli"`.
- `src/eda_agents/agents/digital_autoresearch.py` —
  `DigitalAutoresearchRunner`. Current `backend=` options:
  `"adk"`, `"cc_cli"`, `"litellm"`, `"opencode"`.
- `src/eda_agents/agents/opencode_harness.py` — the OpenCode harness.
- `src/eda_agents/agents/autoresearch_runner.py` — the analog greedy
  loop.
- `src/eda_agents/skills/{analog,digital,flow}.py` — skill registry.
- `src/eda_agents/templates/{mcp,opencode}.json` — MCP registration
  templates for both clients. `eda-init` bootstraps them.
