"""技能必须按需读取、随包交付，并复用已有 MCP 工具。"""
import asyncio
import json
import os
from pathlib import Path
import sys
import pytest


def test_catalog_does_not_eagerly_load_full_instructions(monkeypatch):
    from gxworks2_mcp import skill_library as library
    monkeypatch.setattr(Path,'read_text',lambda *a,**k:pytest.fail('列目录不应读取全文'))
    items=library.list_skills()
    assert len(items)==3 and len({i['name'] for i in items})==3
    assert all('content' not in i for i in items)


@pytest.mark.parametrize('name',['../server.py','..\\server.py','C:\\private','mcp-builder','',None])
def test_unknown_names_cannot_read_local_files(name):
    from gxworks2_mcp.skill_library import read_skill
    with pytest.raises(ValueError):read_skill(name)


def test_all_bundled_skills_are_complete_and_bounded():
    from gxworks2_mcp.skill_library import list_skills,read_skill
    import hashlib,yaml
    for entry in list_skills():
        r=read_skill(entry['name']);s=r['content']
        front=yaml.safe_load(s.split('---',2)[1])
        assert front['name']==entry['name'] and front['description']
        assert r['sha256']==hashlib.sha256(s.encode('utf-8')).hexdigest()
        assert 200<len(s)<10000


def test_read_refuses_symlink_escape(monkeypatch,tmp_path):
    from gxworks2_mcp import skill_library as library
    # 验证路径解析的边界，不依赖本机创建符号链接的权限。
    outside=tmp_path.parent/'outside-skill'
    monkeypatch.setattr(Path,'resolve',lambda *a,**k:outside)
    with pytest.raises(ValueError):library.read_skill('mitsubishi-io-planner')


def test_skills_over_fresh_stdio_and_real_analysis(tmp_path):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def run():
        root=Path(__file__).resolve().parents[1]
        params=StdioServerParameters(command=sys.executable,args=['-m','gxworks2_mcp'],cwd=str(root),
            env={**os.environ,'GXWORKS2_MCP_HOME':str(tmp_path),'PYTHONPATH':str(root/'src')})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                async def call(tool,**args):
                    r=await client.call_tool(tool,args)
                    return json.loads(next(c.text for c in r.content if c.type=='text'))
                catalog=await call('gx_skill_list')
                assert catalog['ok'] and len(catalog['skills'])==3
                for skill in catalog['skills']:
                    assert (await call('gx_skill_read',name=skill['name']))['ok']
                assert not (await call('gx_skill_read',name='../server.py'))['ok']
                assert (await call('gx_program_prepare',name='occupied',cpu='FX3U',
                    il='LD X0\nDMOV D0 D10\nLD X1\nOUT M100\nEND'))['prepared']
                occupied=(await call('gx_where_used',name='occupied'))['usage']
                assert {'D0','D1','D10','D11','M100'}<=occupied.keys()
                proposed={'D12','D13','M101'}
                assert proposed.isdisjoint(occupied)
                review=await call('gx_trace_device',name='occupied',device='D11')
                assert review['writes'][0]['mnemonic']=='DMOV'
    asyncio.run(run())
