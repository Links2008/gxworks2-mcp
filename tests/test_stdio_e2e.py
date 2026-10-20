"""End-to-end check: start the server over stdio and list its tools."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gxworks2_mcp"],
        env={
            **os.environ,
            "PYTHONPATH": os.path.join(root, "src"),
            "PYTHONIOENCODING": "utf-8",
            "GXWORKS2_MCP_HOME": os.path.join(root, ".mcp-cache"),
        },
        cwd=root,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            info = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
            print("server:", info.name, info.version)
            tools = await session.list_tools()
            print("tools:", len(tools.tools))
            for tool in tools.tools:
                first = (tool.description or "").strip().splitlines()[0]
                print(" -", tool.name, "::", first[:80])
            result = await session.call_tool(
                "gx_program_create",
                {
                    "name": "stdio-demo",
                    "cpu": "Q",
                    "rungs": [{"out": "Y0", "logic": {"and": ["X0", {"not": "X1"}]}, "comment": "lamp"}],
                },
            )
            text = result.content[0].text if result.content else ""
            print("create ok:", "LD X0" in text and "OUT Y0" in text)
            ascii_result = await session.call_tool("gx_program_export", {"name": "stdio-demo", "format": "ascii"})
            print("ascii:", ascii_result.content[0].text.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
