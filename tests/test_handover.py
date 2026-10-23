"""档案保留证据来源与版本，不把缺失记录或静态检查写成已验收。"""
import hashlib,json,zipfile
from pathlib import Path
import pytest
from gxworks2_mcp.il import parse_il,render_il
from gxworks2_mcp.store import Store


@pytest.fixture
def project(tmp_path):
    p=Store(tmp_path).create('demo',cpu='FX3U')
    p.save(parse_il('LD X0\nSET M0\nLD M0\nOUT Y0\nLD X1\nRST M0\nEND'),comments={'X0':'启动','X1':'','Y0':'=危险公式'})
    return p


def contents(path):
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        return {name:z.read(name) for name in z.namelist()}


def test_archive_contains_source_io_reset_and_missing_evidence(project):
    from gxworks2_mcp.handover import export_handover
    source={str(p):p.read_bytes() for p in project.root.iterdir() if p.is_file()}
    r=export_handover(project)
    files=contents(r['path']);manifest=json.loads(files['归档清单.json'])
    assert files['程序.il'].decode('utf-8')==render_il(project.read_program())
    assert '启动' in files['I_O清单.csv'].decode('utf-8-sig')
    assert "'=危险公式" in files['I_O清单.csv'].decode('utf-8-sig')
    assert '[待填写]' in files['I_O清单.csv'].decode('utf-8-sig')
    import csv,io
    rows=list(csv.reader(io.StringIO(files['I_O清单.csv'].decode('utf-8-sig'))))
    assert next(row for row in rows if row[0]=='X1')[2]=='[待填写]'
    assert manifest['evidenceCount']==0 and not manifest['programMatchVerified']
    assert '未归档' in files['交付说明.txt'].decode('utf-8')
    assert 'RST M0' in files['步骤与置复位.json'].decode('utf-8')
    assert {s:Path(s).read_bytes() for s in source}==source


def test_each_file_is_covered_by_the_manifest(project):
    from gxworks2_mcp.handover import export_handover
    files=contents(export_handover(project)['path']);m=json.loads(files['归档清单.json'])
    assert set(m['files'])==set(files)-{'归档清单.json'}
    assert all(hashlib.sha256(files[n]).hexdigest()==h for n,h in m['files'].items())


def test_matching_evidence_is_immutable_and_stale_version_is_excluded(project):
    from gxworks2_mcp.handover import remember_evidence,export_handover
    version=hashlib.sha256(render_il(project.read_program()).encode('utf-8')).hexdigest()
    result={'ok':False,'trace':[{'passed':False,'reason':'timeout'}]}
    remember_evidence(project,version,'simulator_verify',result)
    result['ok']=True
    f=contents(export_handover(project)['path'])
    records=json.loads(f['归档证据.json'])
    assert records and records[-1]['result']['ok'] is False
    project.save(parse_il('LD X2\nOUT Y0\nEND'))
    m=json.loads(contents(export_handover(project)['path'])['归档清单.json'])
    assert m['evidenceCount']==0 and m['excludedVersionRecords']>=1


def test_existing_archive_is_never_overwritten(project,tmp_path):
    from gxworks2_mcp.handover import export_handover
    target=tmp_path/'keep.zip';target.write_bytes(b'keep')
    with pytest.raises(ValueError):export_handover(project,target)
    assert target.read_bytes()==b'keep'


def test_evidence_is_bounded_and_cannot_read_arbitrary_files(project):
    from gxworks2_mcp.handover import remember_evidence,export_handover
    version=hashlib.sha256(render_il(project.read_program()).encode('utf-8')).hexdigest()
    assert remember_evidence(project,version,'diagnostic',{'data':'x'*200000}) is None
    files=contents(export_handover(project)['path'])
    assert all('/' not in n and '\\' not in n and '..' not in n for n in files)


def test_stl_steps_are_extracted_without_inventing_a_process(project):
    from gxworks2_mcp.handover import export_handover
    project.save(parse_il('STL S0\nLD X0\nSET S20\nRET\nSTL S20\nOUT Y0\nRET\nEND'))
    details=json.loads(contents(export_handover(project)['path'])['步骤与置复位.json'])
    assert details['sfc']['steps'][0]['transitions'][0]['to']=='S20'


def test_invalid_named_verification_stops_before_input_writes(monkeypatch,tmp_path):
    from gxworks2_mcp import server,simulator
    monkeypatch.setattr(server,'_store',Store(tmp_path))
    monkeypatch.setattr(simulator,'verify',lambda *a:pytest.fail('无效项目不可写输入'))
    result=json.loads(server.gx_simulator_verify([{'expect':{'Y0':0}}],confirm=True,project_name='missing'))
    assert not result['ok']


def test_legacy_verify_response_is_unchanged_without_project_name(monkeypatch):
    from gxworks2_mcp import server,simulator
    expected={'ok':True,'pid':123,'trace':[{'passed':True}]}
    monkeypatch.setattr(simulator,'verify',lambda *a:expected)
    assert json.loads(server.gx_simulator_verify([{'expect':{'Y0':0}}],confirm=True))==expected


def test_repeated_reset_instructions_keep_their_real_line_numbers(project):
    from gxworks2_mcp.handover import export_handover
    project.save(parse_il('LD X0\nRST M0\nLD X1\nRST M0\nEND'))
    rows=json.loads(contents(export_handover(project)['path'])['步骤与置复位.json'])['setReset']
    assert [r['line'] for r in rows]==[2,4]


def test_current_session_cache_is_bounded(project):
    from gxworks2_mcp.handover import remember_evidence,export_handover
    version=hashlib.sha256(render_il(project.read_program()).encode('utf-8')).hexdigest()
    for index in range(40):remember_evidence(project,version,'diagnostic',{'index':index})
    records=json.loads(contents(export_handover(project)['path'])['归档证据.json'])
    assert len(records)==32 and records[0]['result']['index']==8


def test_export_and_diagnostic_evidence_over_real_stdio(tmp_path):
    import asyncio,os,sys
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def run():
        root=Path(__file__).resolve().parents[1]
        params=StdioServerParameters(command=sys.executable,args=['-X','utf8','-m','gxworks2_mcp'],cwd=str(root),
            env={**os.environ,'GXWORKS2_MCP_HOME':str(tmp_path),'PYTHONPATH':str(root/'src')})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                assert any(t.name=='gx_handover_export' for t in (await client.list_tools()).tools)
                async def call(tool_name,**args):
                    r=await client.call_tool(tool_name,args)
                    return json.loads(next(c.text for c in r.content if c.type=='text'))
                assert (await call('gx_program_prepare',name='demo',cpu='FX3U',il='LD X0\nOUT Y0\nEND'))['prepared']
                assert (await call('gx_diagnose_output',name='demo',device='Y0',snapshot={'X0':0,'Y0':0}))['status']=='blocked'
                result=await call('gx_handover_export',name='demo')
                assert result['ok'] and result['evidenceCount']==1 and not result['simulatorAccessed']
                data=contents(result['path'])
                assert json.loads(data['归档证据.json'])[0]['result']['sampling']['source']=='provided snapshot'
                assert not (await call('gx_handover_export',name='demo',path=result['path']))['ok']
                verified=await call('gx_handover_verify',path=result['path'],name='demo')
                assert verified['ok'] and verified['workspaceMatch'] and verified['evidenceCount']==1
                assert not verified['simulatorAccessed'] and not verified['signatureVerified']
    asyncio.run(run())
