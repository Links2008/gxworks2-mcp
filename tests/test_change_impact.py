"""从工艺变化独立核对影响范围；不能将输入局部引用修改扩大为全部输出。"""
import pytest
from gxworks2_mcp.il import parse_il


def impact(before,after):
    from gxworks2_mcp.impact import change_impact
    return change_impact(parse_il(before),parse_il(after))


def test_contact_edit_only_seeds_its_writer_then_propagates():
    old='LD X0\nOUT M0\nLD M0\nOUT Y0\nLD X0\nOUT Y1\nEND'
    r=impact(old,old.replace('LD X0','LD X1',1))
    assert r['changedWrites']==['M0']
    assert r['affectedOutputs']==['Y0']
    assert r['reasons']['Y0']['from']=='M0'


def test_timer_constant_change_reaches_timer_contact_consumer():
    old='LD X0\nOUT T0 K10\nLD T0\nOUT Y0\nEND'
    r=impact(old,old.replace('K10','K20'))
    assert 'TN0' in r['affectedTimers'] and r['affectedOutputs']==['Y0']
    assert any(x['kind']=='timing' for x in r['retestRecommendations'])


def test_hidden_double_word_and_compare_result_bits_are_traced():
    old='LD X0\nDMOV K1 D10\nLD= D11 K0\nCMP D0 K10 M100\nLD M101\nOUT Y0\nEND'
    r=impact(old,old.replace('K1 D10','K65536 D10'))
    assert {'D10','D11','M100','M101','M102','Y0'}<=set(r['affectedDevices'])
    assert r['affectedOutputs']==['Y0']


def test_stl_transition_change_reaches_target_step_and_output():
    old='STL S0\nLD X0\nSET S20\nRET\nSTL S20\nOUT Y0\nRET\nEND'
    r=impact(old,old.replace('LD X0','LD X1'))
    assert 'S20' in r['affectedSteps'] and r['affectedOutputs']==['Y0']


def test_deleted_output_is_still_in_affected_list():
    r=impact('LD X0\nOUT Y0\nEND','END')
    assert r['affectedOutputs']==['Y0']


def test_reordering_writes_is_not_treated_as_equivalent():
    a='LD X0\nOUT M0\nLD M0\nOUT Y0\nEND'
    b='LD M0\nOUT Y0\nLD X0\nOUT M0\nEND'
    assert impact(a,b)['affectedOutputs']==['Y0']


def test_cascade_and_mps_conditions_are_in_dependencies():
    old='LD X0\nMPS\nOUT M0\nMPP\nAND M0\nOUT Y0\nOUT Y1\nEND'
    r=impact(old,old.replace('OUT M0','RST M0'))
    assert r['affectedOutputs']==['Y0','Y1']


def test_cycles_terminate_and_keep_downstream_outputs():
    a='LD M1\nOUT M0\nLD M0\nOUT M1\nLD M1\nOUT Y0\nEND'
    assert impact(a,a.replace('OUT M0','SET M0'))['affectedOutputs']==['Y0']


def test_comments_and_display_steps_do_not_request_logic_retests():
    r=impact('0 LD X0 // old\n1 OUT Y0\n2 END','LD X0 // new\nOUT Y0\nEND')
    assert not r['codeChanged'] and r['commentsOnly']
    assert not r['affectedDevices'] and not r['retestRecommendations']


def test_control_flow_is_conservative_and_explicit():
    a='LD X0\nCJ P0\nP0:\nLD X1\nOUT Y0\nLD X2\nOUT Y1\nEND'
    r=impact(a,a.replace('LD X1','LD X3'))
    assert not r['analysisComplete'] and r['affectedOutputs']==['Y0','Y1']


def test_recommendations_are_not_passing_test_results():
    r=impact('LD X0\nOUT Y0\nEND','LD X1\nOUT Y0\nEND')
    assert r['runtimeVerified'] is False and r['externalMappingsVerified'] is False
    assert r['retestRecommendations'] and all('passed' not in x for x in r['retestRecommendations'])


def test_invalid_limit_is_rejected():
    from gxworks2_mcp.impact import change_impact
    with pytest.raises(ValueError):change_impact(parse_il('END'),parse_il('END'),limit=0)


def test_comparison_width_change_is_local_even_with_identical_expressions():
    old='LD>= D0 K10\nOUT Y0\nLDD>= D0 K10\nOUT Y1\nEND'
    r=impact(old,old.replace('LD>=','LDD>=',1))
    assert r['affectedOutputs']==['Y0']


def test_constant_comparison_width_change_is_not_lost():
    old='LD= K-1 HFFFF\nOUT Y0\nEND'
    assert impact(old,old.replace('LD=','LDD='))['affectedOutputs']==['Y0']


def test_changed_read_addresses_are_listed_without_seeding_unrelated_writers():
    old='LDD>= D0 K10\nOUT Y0\nLD= D0 K1\nOUT Y1\nEND'
    r=impact(old,old.replace('LDD>= D0','LDD>= D100'))
    assert r['affectedOutputs']==['Y0']
    assert {'D0','D1','D100','D101'}<=set(r['registersToReview'])


def test_tool_reads_both_versions_without_modifying_or_connecting(monkeypatch,tmp_path):
    import json
    from gxworks2_mcp import server, simulator
    from gxworks2_mcp.store import Store
    database=Store(tmp_path)
    for name,source in [('old','LD X0\nOUT Y0\nEND'),('new','LD X1\nOUT Y0\nEND')]:
        p=database.create(name,cpu='FX3U');p.save(parse_il(source))
    monkeypatch.setattr(server,'_store',database)
    monkeypatch.setattr(simulator,'read_devices',lambda *a:pytest.fail('影响分析不得访问模拟器'))
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result=json.loads(server.gx_change_impact('old','new'))
    assert result['readOnly'] and result['affectedOutputs']==['Y0']
    assert {p:__import__('pathlib').Path(p).read_bytes() for p in before}==before
    assert not json.loads(server.gx_change_impact('missing','new'))['ok']


def test_new_tool_over_real_stdio(tmp_path):
    import asyncio,json,os,sys
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
                assert any(t.name=='gx_change_impact' for t in (await client.list_tools()).tools)
                async def call(tool_name,**args):
                    r=await client.call_tool(tool_name,args)
                    return json.loads(next(c.text for c in r.content if c.type=='text'))
                for name,preset in [('old','K10'),('new','K20')]:
                    assert (await call('gx_program_prepare',name=name,cpu='FX3U',
                        il=f'LD X0\nOUT T0 {preset}\nLD T0\nOUT Y0\nEND'))['prepared']
                report=await call('gx_change_impact',before_name='old',after_name='new')
                assert report['affectedOutputs']==['Y0'] and 'TN0' in report['affectedTimers']
                same=await call('gx_change_impact',before_name='old',after_name='old')
                assert not same['codeChanged'] and not same['affectedDevices']
    asyncio.run(run())
