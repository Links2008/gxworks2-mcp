"""Real local stdio calls; never invokes GUI export, PLC I/O or live imports."""
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_versioned_edit_and_readback_over_stdio(tmp_path):
    async def run():
        root = Path(__file__).resolve().parents[1]
        params = StdioServerParameters(command=sys.executable, args=['-m', 'gxworks2_mcp'],
            env={**os.environ, 'GXWORKS2_MCP_HOME': str(tmp_path), 'PYTHONPATH': str(root / 'src')}, cwd=str(root))
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                assert {'gx_program_slice', 'gx_program_patch', 'gx_sfc_preview', 'gx_verify_import'} <= names
                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    assert not (getattr(result, 'is_error', False) or getattr(result, 'isError', False))
                    return json.loads(next(c.text for c in result.content if c.type == 'text'))
                created = await call('gx_program_prepare', {'name': 'test', 'cpu': 'FX3U', 'il': 'LD X1\nOUT Y1\nEND'})
                assert created['prepared']
                view = await call('gx_program_slice', {'name': 'test'})
                args = {'name': 'test', 'expected_version': view['version'], 'operations': [
                    {'op': 'insert_before', 'target_id': view['instructions'][1]['id'], 'il': 'ANI X2'}]}
                patched = await call('gx_program_patch', args)
                assert patched['applied'] and patched['csvUpdated']
                assert (await call('gx_program_patch', args))['conflict']
                checked = await call('gx_verify_import', {'name': 'test', 'readback_path': patched['csv']})
                assert checked['matches'] and not checked['liveReadback'] and not checked['verified']
                preview = await call('gx_sfc_preview', {'table': {'cpu': 'FX3U'}})
                assert not preview['valid'] and not preview['runnable']
    asyncio.run(run())
