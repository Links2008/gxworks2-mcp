"""不动作诊断应解释快照，不能把未知值或未用支路当成故障。"""
import pytest
from gxworks2_mcp.il import parse_il


def diagnose(source, values, target='Y0'):
    from gxworks2_mcp.diagnostics import diagnose_output
    return diagnose_output(parse_il(source+'\nEND'),target,values)


def test_series_exposes_normally_closed_blocker():
    r=diagnose('LD X0\nANI X1\nOUT Y0',{'X0':1,'X1':1,'Y0':0,'M8000':1})
    assert r['status']=='blocked'
    leaf=r['writers'][0]['unmet']['children'][0]
    assert (leaf['device'],leaf['actual'],leaf['required'])==('X1',1,0)


def test_unused_parallel_branch_is_not_reported_as_a_blocker():
    r=diagnose('LD X0\nOR X1\nOUT Y0',{'X0':1,'X1':0,'Y0':1})
    assert r['status']=='active' and r['writers'][0]['unmet'] is None


def test_parallel_alternatives_are_not_presented_as_all_required():
    r=diagnose('LD X0\nOR X1\nOUT Y0',{'X0':0,'X1':0,'Y0':0})
    assert r['writers'][0]['unmet']['join']=='any'


def test_inv_reverses_the_required_value():
    r=diagnose('LD X0\nINV\nOUT Y0',{'X0':1,'Y0':0})
    assert r['status']=='blocked'
    assert r['writers'][0]['unmet']['required']==0


def test_stl_step_must_be_active():
    r=diagnose('STL S20\nLD X0\nOUT Y0\nRET',{'S20':0,'X0':1,'Y0':0})
    assert r['status']=='blocked'
    assert r['writers'][0]['unmet']['children'][0]['device']=='S20'


def test_missing_value_never_becomes_zero():
    r=diagnose('LD X0\nOUT Y0',{'Y0':0})
    assert r['status']=='unknown' and 'X0' in r['missingDevices']


@pytest.mark.parametrize('source',[
    'LDP X0\nOUT Y0', 'LD>= R0 K10\nOUT Y0',
])
def test_temporal_or_unsupported_operand_is_explicitly_unknown(source):
    assert diagnose(source,{'X0':1,'D0':9,'Y0':0})['status']=='unknown'


def test_latch_and_multiple_writers_need_execution_context():
    assert diagnose('LD X0\nSET Y0',{'X0':0,'Y0':0})['status']=='history_required'
    r=diagnose('LD X0\nOUT Y0\nLD X1\nOUT Y0',{'X0':1,'X1':0,'Y0':0})
    assert r['status']=='multiple_writers' and len(r['writers'])==2


def test_stop_and_disagreement_are_not_misreported_as_contacts():
    assert diagnose('LD X0\nOUT Y0',{'X0':1,'Y0':0,'M8000':0})['status']=='cpu_stopped'
    assert diagnose('LD X0\nOUT Y0',{'X0':1,'Y0':0,'M8000':1})['status']=='condition_disagrees'


def test_unknown_flow_is_not_assumed_to_execute():
    assert diagnose('LD X0\nCJ P0\nLD X1\nOUT Y0',{'X0':0,'X1':0,'Y0':0})['status']=='unsupported_context'


@pytest.mark.parametrize('values',[{'X0':True},{'X0':2},{'X0':0,'x0':1}])
def test_invalid_snapshot_is_rejected(values):
    with pytest.raises(ValueError):diagnose('LD X0\nOUT Y0',values)


def test_no_writer_and_implicit_data_writer_are_distinguished():
    assert diagnose('LD X0\nOUT Y1',{'Y0':0})['status']=='no_writer'
    assert diagnose('LD X0\nCMP D0 K10 M100',{'M101':0,'X0':1},'M101')['status']=='history_required'


def test_comments_are_evidence_labels_not_logic():
    from gxworks2_mcp.diagnostics import diagnose_output
    r=diagnose_output(parse_il('LD X0\nOUT Y0\nEND'),'Y0',{'X0':0,'Y0':0},{'X0':'伸出许可'})
    assert r['writers'][0]['unmet']['label']=='伸出许可'


def test_live_adapter_reads_once_and_never_writes(monkeypatch,tmp_path):
    import json
    from gxworks2_mcp import server, simulator
    from gxworks2_mcp.store import Store
    database=Store(tmp_path);project=database.create('diag',cpu='FX3U')
    project.save(parse_il('LD X0\nOUT Y0\nEND'))
    monkeypatch.setattr(server,'_store',database)
    original=project.il_path.read_bytes();calls=[]
    def read(points):
        calls.append(points)
        return dict(source='test sample',pid=99,values={'X0':0,'Y0':0,'M8000':1})
    monkeypatch.setattr(simulator,'read_devices',read)
    monkeypatch.setattr(simulator,'write_inputs',lambda *a:pytest.fail('诊断不得写输入'))
    r=json.loads(server.gx_diagnose_output('diag','Y0'))
    assert r['status']=='blocked' and len(calls)==1
    assert set(calls[0])=={'X0','Y0','M8000'}
    assert r['sampling']['pid']==99 and not r['programMatchVerified']
    assert project.il_path.read_bytes()==original
    monkeypatch.setattr(simulator,'read_devices',lambda *a:pytest.fail('离线诊断不得访问模拟器'))
    assert json.loads(server.gx_diagnose_output('diag','Y0',{'X0':1,'Y0':1}))['status']=='active'


def test_tool_is_discoverable_and_strict_over_real_stdio(tmp_path):
    import asyncio, json, os, sys
    from pathlib import Path
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def run():
        root=Path(__file__).resolve().parents[1]
        params=StdioServerParameters(command=sys.executable,args=['-X','utf8','-m','gxworks2_mcp'],cwd=str(root),
            env={**os.environ,'GXWORKS2_MCP_HOME':str(tmp_path),'PYTHONPATH':str(root/'src')})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                tool=next(t for t in (await client.list_tools()).tools if t.name=='gx_diagnose_output')
                schema=getattr(tool,'input_schema',None) or getattr(tool,'inputSchema',{})
                assert 'snapshot' in schema['properties']
                async def call(tool_name,**args):
                    r=await client.call_tool(tool_name,args)
                    return json.loads(next(c.text for c in r.content if c.type=='text'))
                assert (await call('gx_program_prepare',name='diag',cpu='FX3U',il='LD X0\nANI X1\nOUT Y0\nEND'))['prepared']
                r=await call('gx_diagnose_output',name='diag',device='Y0',snapshot={'X0':1,'X1':1,'Y0':0})
                assert r['status']=='blocked' and r['readOnly']
                assert not (await call('gx_diagnose_output',name='diag',device='Y0',snapshot={'X0':True}))['ok']
                assert not (await call('gx_diagnose_output',name='diag',device='D0',snapshot={}))['ok']
                assert (await call('gx_diagnose_output',name='diag',device='Y0',snapshot={'Y0':0}))['status']=='unknown'
                assert (await call('gx_program_prepare',name='numeric',cpu='FX3U',il='LD>= D0 K10\nOUT Y0\nEND'))['prepared']
                numeric=await call('gx_diagnose_output',name='numeric',device='Y0',snapshot={'D0':9,'Y0':0})
                assert numeric['status']=='blocked' and numeric['writers'][0]['unmet']['leftActual']==9
                assert numeric['writers'][0]['unmet']['requiredComparison']=='>='
    asyncio.run(run())
