"""Compatibility shim so one code base serves both MCP Python SDK generations.

mcp 1.x exposes mcp.server.fastmcp.FastMCP; mcp 2.x renamed it to
mcp.server.mcpserver.MCPServer and otherwise keeps the decorator API. Importing through
this module keeps server.py free of version branching.
"""

from __future__ import annotations

from typing import Any

SDK_GENERATION: int
McpServerClass: Any

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as McpServerClass  # type: ignore

    SDK_GENERATION = 2
except ImportError:  # mcp 1.x
    try:
        from mcp.server.fastmcp import FastMCP as McpServerClass  # type: ignore

        SDK_GENERATION = 1
    except ImportError as exc:  # pragma: no cover - no SDK installed at all
        raise SystemExit(
            "the MCP Python SDK is required: pip install 'mcp>=1.2.0'"
        ) from exc


def create_server(name: str, instructions: str = "") -> Any:
    """Build a server instance on either SDK generation."""

    try:
        return McpServerClass(name, instructions=instructions or None)
    except TypeError:  # pragma: no cover - older 1.x signatures
        return McpServerClass(name)


def run_server(server: Any, transport: str = "stdio", port: int = 8931) -> None:
    """Run on stdio (default) or SSE, across SDK generations."""

    if transport == "sse":
        settings = getattr(server, "settings", None)
        if settings is not None and hasattr(settings, "port"):
            settings.port = port
    server.run(transport=transport)
