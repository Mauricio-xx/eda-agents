"""Smoke-test the eda-agents MCP server (S10d spike).

What this exercises:

  1. ``eda_agents.mcp.server`` launched as a Python subprocess over the
     stdio transport — the same wiring an external MCP client (Claude
     Code, Cursor, Zed) uses.
  2. ``render_skill`` with and without a topology, plus the error paths
     for unknown skill name / unknown topology name.
  3. ``list_skills`` (no-filter and prefix filter).
  4. ``evaluate_topology`` for ``miller_ota`` at its default params.
     The SPICE eval is attempted only when ngspice + PDK are on the
     host; otherwise we exercise the tool and accept an ``error`` key in
     the response.

Exit code is ``0`` when every tool returns a well-shaped response
(string for ``render_skill``, ``list[str]`` for ``list_skills``, dict
with the expected keys for ``evaluate_topology``). Anything else
raises ``AssertionError`` and the script exits non-zero.

Usage::

    PYTHONPATH=src python examples/15_mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "src" / "eda_agents" / "mcp" / "server.py"


def _miller_default_params() -> dict[str, float]:
    """Midpoint of ``MillerOTATopology.design_space()`` for a smoke eval."""
    from eda_agents.topologies import get_topology_by_name

    topology = get_topology_by_name("miller_ota")
    return {k: (lo + hi) / 2.0 for k, (lo, hi) in topology.design_space().items()}


async def _run_smoke() -> None:
    env = os.environ.copy()
    # Keep subprocess PYTHONPATH aligned with the example caller's.
    env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{env.get('PYTHONPATH', '')}"

    transport = PythonStdioTransport(
        script_path=str(SERVER_PATH),
        env=env,
        cwd=str(REPO_ROOT),
    )

    async with Client(transport) as client:
        tools = await client.list_tools()
        tool_names = sorted(t.name for t in tools)
        print(f"[mcp] registered tools: {tool_names}")
        assert set(tool_names) == {
            "render_skill",
            "list_skills",
            "evaluate_topology",
        }, tool_names

        res = await client.call_tool("list_skills", {})
        skills = res.data
        assert isinstance(skills, list) and all(isinstance(s, str) for s in skills)
        assert skills, "list_skills returned empty list"
        print(f"[mcp] list_skills: {len(skills)} skills, sample={skills[:3]}")

        res = await client.call_tool("list_skills", {"prefix": "analog."})
        filtered = res.data
        assert filtered and all(s.startswith("analog.") for s in filtered)
        print(f"[mcp] list_skills(prefix='analog.'): {len(filtered)} hits")

        res = await client.call_tool(
            "render_skill",
            {"name": "analog.miller_ota_design", "topology_name": "miller_ota"},
        )
        rendered = res.data
        assert isinstance(rendered, str) and "miller_ota" in rendered
        assert not rendered.startswith("ERROR"), rendered[:200]
        print(f"[mcp] render_skill(miller): {len(rendered)} chars")

        res = await client.call_tool(
            "render_skill",
            {"name": "analog.explorer"},
        )
        error_text = res.data
        assert isinstance(error_text, str) and error_text.startswith("ERROR")
        print(f"[mcp] render_skill(explorer, no topo): {error_text}")

        res = await client.call_tool(
            "render_skill",
            {"name": "does.not.exist"},
        )
        assert isinstance(res.data, str) and res.data.startswith("ERROR")
        print(f"[mcp] render_skill(unknown): {res.data[:80]}")

        params = _miller_default_params()
        res = await client.call_tool(
            "evaluate_topology",
            {"topology_name": "miller_ota", "params": params},
        )
        payload = res.data
        assert isinstance(payload, dict)
        if "error" in payload:
            print(f"[mcp] evaluate_topology reported error (expected without "
                  f"ngspice/PDK): {payload['error']}")
        else:
            for key in ("topology", "pdk", "params", "eval_mode", "fom",
                        "valid", "violations"):
                assert key in payload, (key, payload.keys())
            print(
                f"[mcp] evaluate_topology(miller_ota): eval_mode="
                f"{payload['eval_mode']} valid={payload['valid']} "
                f"fom={payload['fom']:.3g}"
            )


def main() -> int:
    try:
        asyncio.run(_run_smoke())
    except AssertionError as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
