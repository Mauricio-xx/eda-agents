"""FastMCP server exposing eda-agents semantic tools.

Three tools are registered:

* ``render_skill`` — render a named skill's prompt, optionally bound
  to a topology.
* ``list_skills`` — enumerate registered skills, optionally filtered by
  dotted prefix.
* ``evaluate_topology`` — run a SPICE evaluation through
  ``SpiceEvaluationHandler`` for a topology at the given parameters.

The server defaults to the stdio transport used by MCP-aware clients
(Claude Code, Cursor, Zed). HTTP transports are opt-in and bind to
``127.0.0.1`` only — this spike does not implement authentication. See
``docs/mcp_spike_design.md``.
"""

from __future__ import annotations

import inspect
import logging
import tempfile
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from eda_agents.agents.handler import SpiceEvaluationHandler
from eda_agents.core.spice_runner import SpiceRunner
from eda_agents.skills import Skill
from eda_agents.skills.registry import get_skill
from eda_agents.skills.registry import list_skills as _list_skills
from eda_agents.topologies import get_topology_by_name

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PDK = "ihp_sg13g2"

mcp = FastMCP(
    name="eda-agents",
    instructions=(
        "Semantic tools over eda-agents: skill rendering and SPICE-in-"
        "the-loop topology evaluation. Localhost-only; no auth on this "
        "spike — do not expose beyond the loopback interface."
    ),
)


def _prompt_fn_requires_topology(skill: Skill) -> bool:
    """True when the skill's ``prompt_fn`` has at least one required
    positional/keyword argument (i.e. a parameter with no default)."""
    if skill.prompt_fn is None:
        return False
    try:
        sig = inspect.signature(skill.prompt_fn)
    except (TypeError, ValueError):
        return False
    for param in sig.parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.default is inspect.Parameter.empty:
            return True
    return False


@mcp.tool()
def render_skill(name: str, topology_name: str | None = None) -> str:
    """Render a registered skill's prompt.

    Parameters
    ----------
    name:
        Dotted skill name as registered in ``eda_agents.skills`` (e.g.
        ``"analog.miller_ota_design"``).
    topology_name:
        Optional canonical topology name (from
        ``list_topology_names``). When provided, the topology is
        instantiated and passed to the skill's ``prompt_fn``.

    Returns
    -------
    str
        The rendered prompt text on success. On error a string prefixed
        with ``"ERROR:"`` so LLM clients surface the failure inline
        instead of propagating a tool-call exception.
    """
    try:
        skill = get_skill(name)
    except KeyError as exc:
        return f"ERROR: {exc}"

    if topology_name is None:
        if _prompt_fn_requires_topology(skill):
            return (
                f"ERROR: skill {name!r} requires a topology argument; "
                "pass topology_name."
            )
        try:
            return skill.render()
        except TypeError as exc:
            return f"ERROR: {skill.name!r} rendering failed: {exc}"

    try:
        topology = get_topology_by_name(topology_name)
    except KeyError as exc:
        return f"ERROR: {exc}"

    try:
        return skill.render(topology)
    except TypeError as exc:
        return f"ERROR: {skill.name!r} rendering failed: {exc}"


@mcp.tool()
def list_skills(prefix: str | None = None) -> list[str]:
    """List registered skill names, optionally filtered by dotted prefix."""
    return [s.name for s in _list_skills(prefix=prefix)]


@mcp.tool()
async def evaluate_topology(
    topology_name: str,
    params: dict[str, float],
    pdk: str = DEFAULT_PDK,
) -> dict[str, Any]:
    """Run a single SPICE evaluation for the named topology.

    The call is backed by ``SpiceEvaluationHandler.evaluate`` so it
    honours the same analytical pre-filter and measurement pipeline the
    in-tree runners use. Budget is forced to one evaluation per call —
    clients driving exploration should issue repeated calls instead of
    expecting server-side state.

    Returns a JSON-serialisable dict with the evaluation result shape
    (``params``, ``eval_mode``, ``fom``, ``valid``, ``violations``,
    ``analytical``, ``spice``). On unknown topology or runner failure
    the dict contains an ``"error"`` key.
    """
    try:
        topology = get_topology_by_name(topology_name)
    except KeyError as exc:
        return {"error": str(exc)}

    try:
        runner = SpiceRunner(pdk=pdk)
    except Exception as exc:  # surface PDK/runner misconfig to caller
        return {"error": f"SpiceRunner init failed: {exc}"}

    with tempfile.TemporaryDirectory(prefix="eda_mcp_eval_") as tmp:
        work_dir = Path(tmp)
        handler = SpiceEvaluationHandler(
            topology=topology,
            runner=runner,
            work_dir=work_dir,
            max_evals=1,
        )
        result = await handler.evaluate(params)

    return {
        "topology": topology_name,
        "pdk": pdk,
        "params": result.params,
        "eval_mode": result.eval_mode,
        "fom": result.fom,
        "valid": result.valid,
        "violations": list(result.violations),
        "analytical": dict(result.analytical),
        "spice": dict(result.spice) if result.spice else {},
    }


def run_server(
    transport: str = "stdio",
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Run the MCP server.

    ``transport`` defaults to ``"stdio"`` which matches the MCP convention
    used by Claude Code / Cursor / Zed. Passing
    ``"streamable-http"`` binds an HTTP endpoint on ``host`` / ``port``;
    ``host`` is forced to ``127.0.0.1`` unless the caller overrides it,
    since the spike does not authenticate requests.
    """
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    if host != DEFAULT_HOST:
        logger.warning(
            "Binding MCP HTTP transport to %s — spike has no auth; "
            "use 127.0.0.1 unless you know what you are doing.",
            host,
        )
    mcp.run(transport=transport, host=host, port=port)


if __name__ == "__main__":
    run_server()
