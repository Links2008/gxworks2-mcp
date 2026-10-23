"""缺失源文件不能充当空程序或可信版本；故障应先于仿真输入写入。"""
import json
import pytest
from gxworks2_mcp import server,simulator
from gxworks2_mcp.il import parse_il
from gxworks2_mcp.store import Store,StoreError


@pytest.fixture
def project(monkeypatch,tmp_path):
    database=Store(tmp_path)
    p=database.create('selected',cpu='FX3U',initial_il='LD X0\nOUT Y0\nEND')
    monkeypatch.setattr(server,'_store',database)
    return p


def test_missing_source_raises_but_real_empty_project_remains_valid(tmp_path):
    p=Store(tmp_path).create('empty',cpu='FX3U')
    assert p.read_il().strip()=='' and len(p.read_program())==0
    p.il_path.unlink()
    with pytest.raises(StoreError,match='源文件'):p.read_program()


@pytest.mark.parametrize('operation', ['diagnose','impact','handover','verify'])
def test_missing_source_stops_all_new_workflows_before_io(project,monkeypatch,tmp_path,operation):
    project.il_path.unlink()
    def forbidden(*args,**kwargs):pytest.fail('源文件缺失不应访问模拟器')
    monkeypatch.setattr(simulator,'verify',forbidden)
    monkeypatch.setattr(simulator,'read_devices',forbidden)
    target=tmp_path/'missing.zip'
    calls={
        'diagnose':lambda:server.gx_diagnose_output(project.name,'Y0'),
        'impact':lambda:server.gx_change_impact(project.name,project.name),
        'handover':lambda:server.gx_handover_export(project.name,str(target)),
        'verify':lambda:server.gx_simulator_verify([{'inputs':{'X0':1},'expect':{'Y0':1}}],
                                                 confirm=True,project_name=project.name),
    }
    result=json.loads(calls[operation]())
    assert result['ok'] is False and '源文件' in result['error']
    assert not target.exists() and not project.il_path.exists()


@pytest.mark.parametrize('source',['','\n // only comment\n'])
def test_named_verification_rejects_empty_source_before_execution(project,monkeypatch,source):
    project.il_path.write_text(source,encoding='utf-8')
    monkeypatch.setattr(simulator,'verify',lambda *a:pytest.fail('空版本不得执行验证'))
    result=json.loads(server.gx_simulator_verify([{'expect':{'Y0':0}}],confirm=True,project_name=project.name))
    assert result['ok'] is False and '空' in result['error']


def test_lost_source_after_verification_keeps_result_and_warns(project,monkeypatch):
    def execute(*args):
        project.il_path.unlink()
        return {'ok':False,'trace':[{'reason':'timeout','passed':False}]}
    monkeypatch.setattr(simulator,'verify',execute)
    result=json.loads(server.gx_simulator_verify([{'expect':{'Y0':0}}],confirm=True,project_name=project.name))
    assert result['ok'] is False and result['trace'][0]['reason']=='timeout'
    assert result['archiveEvidenceId'] is None and 'archiveWarning' in result


def test_existing_end_only_program_still_executes_and_records(project,monkeypatch):
    project.save(parse_il('END'))
    calls=[]
    monkeypatch.setattr(simulator,'verify',lambda *a:calls.append(a) or {'ok':True,'trace':[]})
    result=json.loads(server.gx_simulator_verify([{'expect':{'Y0':0}}],confirm=True,project_name=project.name))
    assert result['ok'] and result['archiveEvidenceId'] and len(calls)==1
