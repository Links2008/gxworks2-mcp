"""Offline stdio benchmark: legacy three-call workflow versus prepare.

Run from the repository: PYTHONPATH=src .venv/bin/python tools/benchmark_prepare.py
Uses only temporary local workspaces. Never invokes GX Works2 or PLC tools.
Reports medians for five runs; excludes initialization and model/network latency.
Response bytes count UTF-8 tool text, not JSON-RPC framing or token counts.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="gx-prepare-bench-") as workspace:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "gxworks2_mcp"],
            env={**os.environ, "PYTHONPATH": str(root / "src"),
                 "GXWORKS2_MCP_HOME": workspace},
            cwd=str(root),
        )
        started = time.perf_counter()
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                print(json.dumps({"initialization_ms": round((time.perf_counter() - started) * 1000, 3)}))
                listed = await session.list_tools()
                tool = next(t for t in listed.tools if t.name == "gx_program_prepare")
                schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema")
                assert {"name", "cpu"}.issubset(schema.get("required", [])), schema
                assert schema["properties"]["preview"]["default"] is False, schema
                print(json.dumps({"prepare_schema_verified": True, "required": schema["required"]}))

                async def call(name: str, arguments: dict) -> tuple[dict, int]:
                    result = await session.call_tool(name, arguments)
                    assert not (getattr(result, "is_error", False) or getattr(result, "isError", False)), result
                    text = "\n".join(c.text for c in result.content if c.type == "text")
                    payload = json.loads(text)
                    assert "error" not in payload, payload
                    return payload, len(text.encode("utf-8"))

                for kind in ("ladder", "FX_STL"):
                    for instructions in (501, 2001):
                        cpu = "Q" if kind == "ladder" else "FX3U"
                        if kind == "ladder":
                            inputs = {"rungs": [{"out": f"M{i}", "logic": f"X{i % 16:X}"}
                                                for i in range((instructions - 1) // 2)]}
                        else:
                            count = (instructions - 1) // 5
                            inputs = {"il": "\n".join(
                                f"STL S{i}\nOUT M{i}\nLD X0\nOUT S{(i + 1) % count}\nRET"
                                for i in range(count)) + "\nEND"}
                        timings = {"legacy": [], "prepare": []}
                        sizes = {"legacy": [], "prepare": []}
                        for repeat in range(5):
                            # Alternate order to reduce systematic warm-cache bias.
                            order = ("legacy", "prepare") if repeat % 2 == 0 else ("prepare", "legacy")
                            for mode in order:
                                name = f"{kind}-{instructions}-{repeat}-{mode}"
                                start = time.perf_counter()
                                if mode == "legacy":
                                    _, b1 = await call("gx_program_create", {"name": name, "cpu": cpu, **inputs})
                                    validation, b2 = await call("gx_program_validate", {"name": name})
                                    assert validation["ok"], validation
                                    payload, b3 = await call("gx_program_export_csv", {"name": name, "cpu_label": cpu})
                                    size = b1 + b2 + b3
                                    csv_path = payload["written"]
                                else:
                                    payload, size = await call("gx_program_prepare", {"name": name, "cpu": cpu, **inputs})
                                    assert payload["prepared"] and payload["validation"]["ok"], payload
                                    assert payload["instructions"] == instructions, payload
                                    assert payload["imported"] is False, payload
                                    assert "ascii" not in payload and "il" not in payload, payload
                                    csv_path = payload["csv"]
                                timings[mode].append((time.perf_counter() - start) * 1000)
                                sizes[mode].append(size)
                                assert Path(csv_path).is_file(), csv_path
                        print(json.dumps({
                            "kind": kind, "instructions": instructions, "repeats": 5,
                            "legacy_calls": 3, "prepare_calls": 1,
                            "legacy_median_ms": round(statistics.median(timings["legacy"]), 3),
                            "prepare_median_ms": round(statistics.median(timings["prepare"]), 3),
                            "legacy_median_response_bytes": statistics.median(sizes["legacy"]),
                            "prepare_median_response_bytes": statistics.median(sizes["prepare"]),
                        }), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
