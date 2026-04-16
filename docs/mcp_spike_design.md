# MCP spike — eda-agents as a semantic tool server (S10d)

## Purpose

S10a–S10c stitched skills into the in-tree runners (`AutoresearchRunner`,
`DigitalAutoresearchRunner`, the Claude Code CLI prompt builders). S10d
exposes the same cohesion to *external* MCP-aware clients — Claude
Code, Cursor, Zed, or any other caller that speaks the Model Context
Protocol — so the repo's domain knowledge is reusable outside the
library's own harnesses.

The spike ships a minimum-viable server with three tools. It is scoped
as a **spike**: no authentication, localhost-only, one evaluation per
call. Production hardening (auth, rate limits, streaming) is follow-on
work explicitly out of scope for this arc.

## Tools

All three live in `src/eda_agents/mcp/server.py` and are registered via
`@mcp.tool()` on a single `FastMCP` instance.

### `render_skill(name, topology_name=None) -> str`

- **Backed by**: `skills.registry.get_skill` + `Skill.render()`, plus
  `topologies.get_topology_by_name` (added in S10b) when a topology
  name is supplied.
- **Behaviour**:
  - Unknown skill name → string prefixed `ERROR:`.
  - Skill with a required topology argument invoked without
    `topology_name` → `ERROR: … requires a topology argument …`.
    Signature inspection looks for any parameter without a default.
  - Unknown `topology_name` → string prefixed `ERROR:`.
  - Otherwise returns the fully rendered prompt text.
- **Why structured error strings, not MCP errors**: keeping failures in
  the normal response surface lets LLM clients handle them inline —
  they read the string, realise they mis-called the tool, and retry —
  instead of a tool-call error that some clients hide from the model.

### `list_skills(prefix=None) -> list[str]`

- **Backed by**: `skills.registry.list_skills`.
- Returns just the dotted names (not the full `Skill` dataclass) for
  compactness; clients that need the description can follow up with
  `render_skill`.

### `evaluate_topology(topology_name, params, pdk="ihp_sg13g2") -> dict`

- **Backed by**: `topologies.get_topology_by_name` +
  `core.spice_runner.SpiceRunner` + `agents.handler.SpiceEvaluationHandler`.
- Runs **one** evaluation per call (`max_evals=1`). Server-side
  exploration state is intentionally avoided — clients that want a
  search loop should own it and issue repeated calls.
- Result dict shape:

  ```json
  {
    "topology": "miller_ota",
    "pdk": "ihp_sg13g2",
    "params": {...},
    "eval_mode": "spice" | "analytical_prefilter" | "analytical_budget",
    "fom": 12.5,
    "valid": true,
    "violations": [],
    "analytical": {...},
    "spice": {...}
  }
  ```

- On unknown topology, bad PDK, or runner-init failure: the response is
  `{"error": "..."}` with the diagnostic instead of the usual fields.

## Transport and auth model

- **Default transport**: stdio. Matches the convention used by Claude
  Code, Cursor, and Zed when they spawn an MCP server from a command
  line. No network listener.
- **HTTP transport**: opt-in via `run_server(transport="streamable-http")`.
  Host defaults to `127.0.0.1`; `port` defaults to `8765`. A warning is
  logged if the caller overrides `host` to something non-loopback,
  because the spike ships **no authentication**. Do not expose beyond
  the loopback interface.
- **Roadmap to auth**: the upstream `fastmcp.FastMCP` constructor
  accepts an `auth` provider. Next step (out of this spike) is a
  token-based provider plus per-tool permission gating — likely
  `evaluate_topology` gated behind a write token and the skill tools
  open.

## Installation and local use

```bash
pip install -e ".[mcp]"
python -m eda_agents.mcp              # starts stdio server
python examples/15_mcp_smoke.py       # end-to-end smoke via subprocess
pytest -m mcp                         # test suite
```

To wire the server into Claude Code (stdio convention):

```json
{
  "mcpServers": {
    "eda-agents": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "eda_agents.mcp"],
      "cwd": "/path/to/eda-agents"
    }
  }
}
```

## Follow-on tools considered and descartados for this spike

- **`run_bench_task(task_id)`** — would wrap the Arcadia bench runner.
  Skipped because bench runs are long-lived; exposing them over MCP
  without task/progress streaming would give an opaque "hangs for five
  minutes" client experience. Need `fastmcp` task support (which the
  3.x API does have — `FastMCP(tasks=True)`) plus a result streaming
  design before it's worth shipping.
- **`submit_bridge_job(pdk, ...)`** — would wrap `eda_agents.bridge`.
  Skipped because the bridge already has its own job registry and
  demo example (14_bridge_e2e); the MCP layer would duplicate
  semantics. Revisit when there's a concrete external caller that
  wants bridge access without adopting the Python API.
- **`list_topologies()`** — deliberately collapsed: `list_skills` +
  `render_skill` already surface topology-keyed content through the
  skill prompts. Adding a third enumeration tool bloats the surface
  without new capability. Clients that truly need the raw list can ask
  `render_skill("analog.miller_ota_design", "miller_ota")` or similar
  and parse.

## Testing strategy

- **In-memory tests** (`tests/test_mcp_server.py`, marker `mcp`) use
  `fastmcp.Client(mcp)` so they avoid a subprocess. Fast (~200 ms)
  and deterministic, but they do not exercise stdio framing.
- **Subprocess smoke** (`examples/15_mcp_smoke.py`) launches the
  server via `PythonStdioTransport` — this is the integration test
  that catches e.g. stdio framing regressions, module-import failures
  on a cold interpreter, or path issues. Kept as an example rather
  than a pytest test because it depends on the installed interpreter
  and PYTHONPATH, which varies across environments.
- **PDK-sensitive path**: `evaluate_topology` needs ngspice + IHP
  SG13G2 to produce a SPICE result. The corresponding test skips when
  the PDK is missing — the same skip pattern used by
  `tests/conftest.py::_pdk_available`.

## Relationship to the rest of the arc

| Layer | Owner | What it consumes |
| --- | --- | --- |
| `skills/` | S10a–S10b | Owned by the skill authors. |
| `topologies.get_topology_by_name` | S10b | Added precisely so MCP + tests can name topologies without importing them. |
| `AutoresearchRunner._system_prompt` | S10c | Renders `relevant_skills` into the prompt — unchanged by S10d. |
| `mcp/server.py` | S10d | Re-exports the same render pipeline over MCP for external clients. |

The MCP tools do **not** implement any new domain logic. Every code
path they expose is already exercised by an in-tree caller. The spike
is a thin adapter, not a parallel track.
