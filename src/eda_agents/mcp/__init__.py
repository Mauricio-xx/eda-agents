"""MCP server entry point for eda-agents (S10d spike).

Exposes semantic tools (skill rendering, topology evaluation) over the
Model Context Protocol so external clients — Claude Code, Cursor, Zed —
can drive the same capabilities the in-tree runners use. See
``docs/mcp_spike_design.md`` for the mapping between each tool and the
underlying repo code.

Importing this package is cheap; actually starting the server requires
``fastmcp`` (extras ``pip install -e ".[mcp]"``). The
``FastMCP`` instance and tools live in ``server.py`` and are imported
lazily so that modules without the dependency installed still load.
"""

from __future__ import annotations

__all__ = ["get_mcp", "run_server"]


def get_mcp():  # type: ignore[no-untyped-def]
    """Return the configured ``FastMCP`` server instance."""

    from eda_agents.mcp.server import mcp

    return mcp


def run_server() -> None:
    """Run the MCP server via the default stdio transport."""

    from eda_agents.mcp.server import run_server as _run_server

    _run_server()
