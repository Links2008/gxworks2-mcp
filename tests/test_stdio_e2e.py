"""End-to-end check: start the server over stdio and list its tools."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    temporary = tempfile.TemporaryDirectory(prefix="gxworks2-stdio-")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gxworks2_mcp"],
        env={
            **os.environ,
            "PYTHONPATH": os.path.join(root, "src"),
            "PYTHONIOENCODING": "utf-8",
            "GXWORKS2_MCP_HOME": temporary.name,
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
            assert {"gx_write_project", "gx_sfc_write", "gx_sfc_read"} <= {tool.name for tool in tools.tools}
            result = await session.call_tool(
                "gx_program_create",
                {
                    "name": "stdio-demo",
                    "cpu": "FX3U",
                    "rungs": [{"out": "Y0", "logic": {"and": ["X10", {"not": "X1"}]}, "comment": "lamp"}],
                },
            )
            text = result.content[0].text if result.content else ""
            assert "LD X10" in text and "OUT Y0" in text
            ascii_result = await session.call_tool("gx_program_export", {"name": "stdio-demo", "format": "ascii"})
            print("ascii:", ascii_result.content[0].text.strip())
            written = await session.call_tool("gx_write_project", {
                "name": "stdio-demo", "path": str(Path(temporary.name) / "ladder.gxw"), "confirm": True,
            })
            report = json.loads(written.content[0].text)
            assert report.get("ok") and report["computerUseCalls"] == 0, report
            opened = await session.call_tool("gx_gxw_open", {"path": report["path"]})
            assert "LD X10" in opened.content[0].text
            from examples.fx3u_sequence import sequence_spec
            result = await session.call_tool("gx_program_create", {"name": "sfc-demo", "cpu": "FX3U", "sfc": sequence_spec()})
            assert "STL S0" in result.content[0].text
            written_sfc = await session.call_tool("gx_write_project", {
                "name": "sfc-demo", "path": str(Path(temporary.name) / "sfc.gxw"), "confirm": True,
            })
            sfc_report = json.loads(written_sfc.content[0].text)
            assert sfc_report.get("ok") and sfc_report["sourceReadback"], sfc_report
            print(json.dumps({"ladderWriteMs": report["writeMs"], "sfcWriteMs": sfc_report["writeMs"], "stdioVerified": True}))
            template = os.environ.get("GXWORKS2_SFC_TEST_TEMPLATE")
            if template:
                from examples.fx3u_sequence import native_sequence_spec
                output_dir = Path(os.environ.get("GXWORKS2_SFC_TEST_OUTPUT_DIR", temporary.name))
                output_dir.mkdir(parents=True, exist_ok=True)
                path = output_dir / "顺启逆停_原生SFC.gxw"
                started = time.perf_counter()
                written = await session.call_tool("gx_sfc_write", {
                    "spec": native_sequence_spec(), "path": str(path), "template_path": template,
                    "installation_root": os.environ.get("GXWORKS2_INSTALL_ROOT", ""), "confirm": True,
                })
                native_report = json.loads(written.content[0].text)
                assert native_report.get("ok") and not native_report["requiresProjectCompile"], native_report
                native_report["mcpWriteMs"] = round((time.perf_counter() - started) * 1000, 2)
                started = time.perf_counter()
                opened = await session.call_tool("gx_sfc_read", {"path": str(path)})
                readback = json.loads(opened.content[0].text)
                native_report["mcpReadMs"] = round((time.perf_counter() - started) * 1000, 2)
                assert readback["ok"] and len(readback["blocks"][0]["steps"]) == 6
                assert len(readback["blocks"][0]["transitions"]) == 8
                for filename, data in [("原生编译核验.json", native_report), ("原生SFC读回.json", readback),
                                       ("原生SFC步骤.json", native_sequence_spec())]:
                    (output_dir / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps({"nativeSfcVerified": True, "mcpWriteMs": native_report["mcpWriteMs"],
                                  "mcpReadMs": native_report["mcpReadMs"]}))
    temporary.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
