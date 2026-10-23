"""启动前拒绝无效参数，且任何失败不得继续操作桌面。"""
import json
from pathlib import Path
import pytest
from gxworks2_mcp import simulator as sim


def test_verify_parameters_can_be_checked_without_a_simulator(monkeypatch):
    monkeypatch.setattr(sim, 'target', lambda: pytest.fail('预检不得连接模拟器'))
    sim.validate_verification([dict(expect={'Y0': 0})])
    with pytest.raises(ValueError, match='期望值超界'):
        sim.validate_verification([dict(expect={'Y0': True})])


def test_all_cascade_cases_are_preflighted():
    from tools.verify_general_capabilities import cascade_steps
    steps = cascade_steps()
    assert len(steps) == 16
    assert len({tuple(s['inputs'].values()) for s in steps}) == 16
    sim.validate_verification(steps)


def test_bad_native_identity_stops_before_tests_or_gui(monkeypatch, tmp_path):
    from tools import run_verification as pipeline
    monkeypatch.setattr(pipeline, 'launch', lambda *a: pytest.fail('无效工程不能启动软件'))
    monkeypatch.setattr(pipeline.subprocess, 'run', lambda *a, **kw: pytest.fail('预检失败不能启动测试'))
    project = tmp_path / 'sample.gxw'; project.write_bytes(b'test')
    report = tmp_path / 'native.json'; report.write_text('{}')
    result = pipeline.run(project, report, tmp_path / 'out')
    assert not result['passed'] and result['failedStage'] == 'preflight'


def test_failed_tests_stop_before_gui(monkeypatch, tmp_path):
    from tools import run_verification as pipeline
    from types import SimpleNamespace
    monkeypatch.setattr(pipeline, 'preflight', lambda *a: None)
    monkeypatch.setattr(pipeline, 'launch', lambda *a: pytest.fail('测试失败不能启动软件'))
    def fail(command, **kwargs):
        assert command[1:4] == ['-X', 'utf8', '-m']
        assert kwargs['encoding'] == 'utf-8'
        return SimpleNamespace(returncode=1, stdout='定向失败', stderr='')
    monkeypatch.setattr(pipeline.subprocess, 'run', fail)
    result = pipeline.run(tmp_path / 'p.gxw', tmp_path / 'n.json', tmp_path / 'out')
    assert not result['passed'] and result['failedStage'] == 'regression'
    assert '定向失败' in (tmp_path / 'out' / '回归.log').read_text(encoding='utf-8')


def test_counter_batch_keeps_each_press_release_acknowledgement():
    from tools.verify_general_capabilities import counter_steps
    steps = counter_steps()
    assert len(steps) == 10
    for index, (device, counter, ack, value) in enumerate([
        ('X3','CN0','M10',1),('X3','CN0','M10',2),('X3','CN0','M10',3),
        ('X4','CN200','M11',1),('X4','CN200','M11',2)]):
        pressed, released = steps[index*2:index*2+2]
        assert pressed['inputs'] == {device:1}
        assert pressed['expect'] == {counter:value, ack:1}
        assert released['inputs'] == {device:0}
        assert released['expect'] == {ack:0}
    sim.validate_verification(steps)


@pytest.mark.parametrize('exited',[False, True])
def test_only_owned_validation_app_is_reaped_without_a_close_dialog(exited):
    from tools.verify_general_capabilities import close_owned_app
    calls=[]
    class OwnedApp:
        def poll(self):return 0 if exited else None
        def terminate(self):calls.append('terminate')
        def wait(self,timeout):calls.append(('wait',timeout))
    close_owned_app(OwnedApp())
    assert calls == ([] if exited else ['terminate']) + [('wait',5)]


@pytest.mark.parametrize('packaged',[None,b'old source'])
def test_package_check_rejects_missing_or_stale_source(monkeypatch,tmp_path,packaged):
    from types import SimpleNamespace
    import zipfile
    from tools import run_verification as pipeline
    source=tmp_path/'src'/'gxworks2_mcp';source.mkdir(parents=True)
    (source/'server.py').write_bytes(b'new source')
    (tmp_path/'pyproject.toml').write_text('[tool.setuptools.package-data]\ngxworks2_mcp=[]\n')
    monkeypatch.setattr(pipeline,'ROOT',tmp_path)
    out=tmp_path/'wheel'
    def build(command,**kwargs):
        assert kwargs['encoding']=='utf-8' and '-X' in command
        with zipfile.ZipFile(out/'test.whl','w') as z:
            if packaged is not None:z.writestr('gxworks2_mcp/server.py',packaged)
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(pipeline.subprocess,'run',build)
    with pytest.raises(ValueError,match='软件包'):
        pipeline.check_package(out)
