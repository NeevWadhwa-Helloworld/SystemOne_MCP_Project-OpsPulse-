"""Compatibility entrypoint for the OpsPulse MCP server."""

from backend.mcp_server import mcp


if __name__ == "__main__":
    mcp.run(transport="stdio")
