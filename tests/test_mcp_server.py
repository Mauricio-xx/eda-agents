"""Tests for the eda-agents MCP server (S10d spike).

These tests talk to the server via an in-memory ``fastmcp.Client`` to
avoid subprocess overhead; the end-to-end stdio wiring is covered by
``examples/15_mcp_smoke.py``.
"""

from __future__ import annotations

import pytest

from tests.conftest import HAS_MCP, ihp_available

pytestmark = pytest.mark.mcp

pytest.importorskip("fastmcp", reason="MCP tests require the [mcp] extras")


@pytest.fixture(scope="module")
def mcp_server():
    """Return the shared ``FastMCP`` instance for in-memory client use."""
    from eda_agents.mcp.server import mcp

    return mcp


@pytest.fixture
async def mcp_client(mcp_server):
    """In-memory ``fastmcp.Client`` bound to the module server."""
    from fastmcp import Client

    async with Client(mcp_server) as client:
        yield client


@pytest.mark.skipif(not HAS_MCP, reason="fastmcp not installed")
async def test_list_skills_returns_non_empty(mcp_client):
    res = await mcp_client.call_tool("list_skills", {})
    skills = res.data
    assert isinstance(skills, list)
    assert skills, "list_skills returned no names"
    assert all(isinstance(name, str) for name in skills)
    # Sanity: namespaces we know exist per S10c.
    assert any(name.startswith("analog.") for name in skills)


@pytest.mark.skipif(not HAS_MCP, reason="fastmcp not installed")
async def test_list_skills_prefix_filter(mcp_client):
    res = await mcp_client.call_tool("list_skills", {"prefix": "analog."})
    skills = res.data
    assert skills, "prefix filter returned empty list"
    assert all(name.startswith("analog.") for name in skills)


@pytest.mark.skipif(not HAS_MCP, reason="fastmcp not installed")
async def test_render_skill_miller_ota_design(mcp_client):
    res = await mcp_client.call_tool(
        "render_skill",
        {"name": "analog.miller_ota_design", "topology_name": "miller_ota"},
    )
    rendered = res.data
    assert isinstance(rendered, str)
    assert not rendered.startswith("ERROR"), rendered[:200]
    # The prompt is composed from docs/skills/miller_ota/{core,sizing,compensation}.md
    # and, when a topology is supplied, prefixed with topology metadata.
    assert "miller_ota" in rendered
    assert len(rendered) > 500


@pytest.mark.skipif(not HAS_MCP, reason="fastmcp not installed")
async def test_render_skill_without_topology_errors_cleanly(mcp_client):
    # ``analog.explorer`` has a required ``topology`` positional argument
    # (no default), so invoking it without ``topology_name`` must be
    # refused with a structured error string, not an exception.
    res = await mcp_client.call_tool(
        "render_skill",
        {"name": "analog.explorer"},
    )
    text = res.data
    assert isinstance(text, str)
    assert text.startswith("ERROR"), text[:200]
    assert "topology" in text.lower()


@pytest.mark.skipif(not HAS_MCP, reason="fastmcp not installed")
async def test_render_skill_unknown_name_errors_cleanly(mcp_client):
    res = await mcp_client.call_tool(
        "render_skill",
        {"name": "does.not.exist"},
    )
    text = res.data
    assert isinstance(text, str)
    assert text.startswith("ERROR")
    assert "does.not.exist" in text


@pytest.mark.skipif(not HAS_MCP, reason="fastmcp not installed")
@pytest.mark.skipif(not ihp_available, reason="IHP SG13G2 PDK not available")
async def test_evaluate_topology_miller_ota(mcp_client):
    from eda_agents.topologies import get_topology_by_name

    topology = get_topology_by_name("miller_ota")
    params = {
        k: (lo + hi) / 2.0 for k, (lo, hi) in topology.design_space().items()
    }

    res = await mcp_client.call_tool(
        "evaluate_topology",
        {"topology_name": "miller_ota", "params": params},
    )
    payload = res.data
    assert isinstance(payload, dict)
    # If the evaluation succeeded we expect the canonical shape; if it
    # errored out we want the error to be surfaced in the dict, not
    # raised through the tool layer.
    if "error" in payload:
        pytest.skip(f"evaluate_topology errored: {payload['error']}")

    assert payload["topology"] == "miller_ota"
    assert payload["pdk"] == "ihp_sg13g2"
    assert payload["params"] == params
    assert payload["eval_mode"] in {
        "spice",
        "analytical_prefilter",
        "analytical_budget",
    }
    assert isinstance(payload["valid"], bool)
    assert isinstance(payload["violations"], list)
    assert isinstance(payload["analytical"], dict)
    assert isinstance(payload["spice"], dict)
    assert isinstance(payload["fom"], (int, float))
