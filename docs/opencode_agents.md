# opencode agents for eda-agents

Three curated agents ship under `.opencode/agent/`. They turn the
registered skills from *text the model reads voluntarily* into
*system prompts the model operates under from the first token* — which
makes weaker models consistent and removes one class of user-visible
friction (having to call `render_skill` before every interaction).

All three agents inherit the full opencode tool surface (built-ins
plus every registered MCP server) and rely on the prompt body to
steer usage. This is the **interactive-first** posture: users get
more control and mid-conversation flexibility, at the cost of the
hard blast-radius guarantee a strict whitelist would give.

If you need a **headless / budget-constrained** mode (CI, automated
exploration runs where no human approves each tool call), add a
`tools:` block to the agent file — for example
`tools: { bash: false, write: false, "eda-agents_recommend_topology": true }`
— and commit that variant alongside the permissive one.

## Shipped agents

| Agent | Launched with | System prompt | Primary MCP tools it targets |
|---|---|---|---|
| `analog-topology-recommender` | `opencode --agent analog-topology-recommender` | `analog.idea_to_topology` | `recommend_topology`, `describe_topology` |
| `analog-sizing-advisor` | `opencode --agent analog-sizing-advisor` | `analog.gmid_sizing` (+ operational loop) | `describe_topology`, `evaluate_topology`, `run_autoresearch` |
| `digital-testbench-author` | `opencode --agent digital-testbench-author` | `digital.cocotb_testbench` (+ authoring enabled) | `render_skill`, `generate_rtl_draft` + built-in read/write/edit |
| `gf180-docker-digital` | `opencode --agent gf180-docker-digital` | `flow.rtl2gds_gf180_docker` (+ docker driver loop) | `render_skill` + built-in bash/read/write/edit |
| `gf180-docker-analog` | `opencode --agent gf180-docker-analog` | `flow.analog_signoff_gf180_docker` (+ docker driver loop) | `render_skill` + built-in bash/read/write/edit |

"Primary MCP tools" describes what the prompt body steers toward;
the agent can still access other tools if the user asks directly.

The model is **not pinned** in the agent frontmatter. Each agent
inherits whatever the global opencode config (or `-m` on the command
line) selects, so a user on Z.AI Coding Plan keeps
`zai-coding-plan/glm-5.1` while someone on OpenRouter keeps
`openrouter/google/gemini-3-flash-preview`.

## Invocation

Headless:

```bash
opencode run --agent analog-topology-recommender \
    "Low-noise 1 kHz amplifier for a biomedical sensor, 60 dB gain, 45 deg PM"
```

Interactive TUI:

```bash
opencode --agent analog-sizing-advisor
# or call a subagent mid-conversation from the default agent:
# @digital-testbench-author write a cocotb testbench for fsm.v
```

The MCP registration at the project-root `opencode.json` is picked up
automatically, so `eda-agents_*` tools resolve as long as opencode is
launched from the repo root (`cd eda-agents && opencode …`). The
`command` uses a relative path (`.venv/bin/python -m eda_agents.mcp`),
which opencode resolves against the launch cwd — so the repo works
portably for any cloner as long as their venv lives at `.venv/` and
they `cd` into the repo before starting opencode. If your venv is
elsewhere, edit `opencode.json` locally (it is committed — do not
rename the venv expecting the config to adapt).

## Generating a new agent from a skill

The curated agents above were bootstrapped with
`scripts/generate_opencode_agent.py` and then hand-edited to add an
"OPERATIONAL LOOP" section explaining how the agent should sequence
the whitelisted MCP tools. The skill body alone is not enough — it
teaches the methodology but doesn't say *which MCP tool to call when*.

Example — bootstrap a `sar-adc-designer` agent from the SAR ADC skill:

```bash
python scripts/generate_opencode_agent.py \
    --skill analog.sar_adc_design \
    --topology sar_adc_7bit \
    --name sar-adc-designer \
    --description "Size a 7-bit SAR ADC on IHP SG13G2 with MCP-backed eval." \
    --mcp-tools "describe_topology,evaluate_topology,run_autoresearch" \
    --temperature 0.2
```

Output lands at `.opencode/agent/sar-adc-designer.md`. Edit the body
to add the tool-sequencing guidance (who calls what, in what order,
and when to stop). Commit when the agent actually behaves under a
live run.

## Validation

Before committing a new curated agent, run at least one live prompt
against it:

```bash
opencode run --agent <your-agent> "<representative user ask>" 2>&1 | tail -40
```

Check that:

1. The model surfaces the MCP tool call (you will see
   `eda-agents_*` calls in the trace).
2. The model stays on-task per the prompt body (no unprompted jumps
   into `bash` or write ops when the task is purely MCP-driven).
3. The final reply matches the expected output contract (the
   topology-recommender, for example, emits a single JSON object).

If you ship a locked-down variant (headless / budget-guard), include
a negative control: the agent attempting a disabled built-in and
opencode refusing the call. Paste that trace into the PR description.
