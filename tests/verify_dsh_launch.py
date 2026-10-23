"""Verify the server launches exactly as DSH's mcp-client will launch it.

DSH spawns stdio servers with an absolute command, the configured args/cwd/env, and a
scrubbed parent environment. This script reproduces that launch and exercises one
authoring tool plus one validation tool.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PYTHON = sys.executable
CWD = str(Path(__file__).resolve().parents[1])
HOME = os.environ.get("GXWORKS2_MCP_HOME", str(Path(CWD) / ".mcp-cache"))

# DSH removes KEY/PASSWORD/SECRET/TOKEN names and every DSH_* name, then merges config env.
CONFIG_ENV = {
    "PYTHONPATH": os.path.join(CWD, "src"),
    "PYTHONIOENCODING": "utf-8",
    "GXWORKS2_MCP_HOME": HOME,
}


async def main() -> int:
    scrubbed = {
        name: value
        for name, value in os.environ.items()
        if not any(marker in name.upper() for marker in ("KEY", "PASSWORD", "SECRET", "TOKEN"))
        and not name.startswith("DSH_")
    }
    params = StdioServerParameters(
        command=PYTHON,
        args=["-m", "gxworks2_mcp"],
        env={**scrubbed, **CONFIG_ENV},
        cwd=CWD,
    )
    failures: list[str] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            info = getattr(init, "server_info", None)
            print("handshake ok:", info.name if info else "?")
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print("tools:", len(names))
            expected = {
                "gx_program_create",
                "gx_program_add_rungs",
                "gx_program_validate",
                "gx_program_export",
                "plc_read",
                "plc_write",
                "gx_paste_ladder",
            }
            missing = expected - set(names)
            if missing:
                failures.append("missing tools: " + ", ".join(sorted(missing)))

            created = await session.call_tool(
                "gx_program_create",
                {
                    "name": "verify-run",
                    "overwrite": True,
                    "cpu": "Q",
                    "rungs": [
                        {
                            "out": "Y0",
                            "comment": "self-holding",
                            "logic": {"or": [{"and": ["X0", {"not": "X1"}]}, {"and": ["Y0", {"not": "X1"}]}]},
                        }
                    ],
                },
            )
            payload = json.loads(created.content[0].text)
            il = payload["il"]
            print("IL:", il.replace("\n", " / "))
            if "ORB" not in il or "OUT Y0" not in il:
                failures.append("compiled IL is missing ORB/OUT")
            print("ladder:", payload["ascii"].replace("\n", " | "))

            validated = await session.call_tool("gx_program_validate", {"name": "verify-run"})
            report = json.loads(validated.content[0].text)
            print("validation ok:", report["ok"], "errors:", report["errors"])
            if report["errors"]:
                failures.append("validation reported errors")

            status = await session.call_tool("gx_workspace_status", {})
            status_payload = json.loads(status.content[0].text)
            print("gx works2 install:", status_payload["automation"].get("install", {}).get("found"))
            print("pywinauto:", status_payload["automation"].get("pywinauto"))
            print("running:", status_payload["automation"].get("running"))
    print("FAILURES:", failures or "none")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
