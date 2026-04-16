"""``python -m eda_agents.mcp`` entry point.

Starts the FastMCP server on the default stdio transport so MCP-aware
clients (Claude Code, Cursor, Zed) can spawn it from their config.
"""

from eda_agents.mcp.server import run_server

if __name__ == "__main__":
    run_server()
