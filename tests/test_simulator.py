"""模拟器通道必须固定目标、严格处理 FX 地址并拒绝非输入写入。"""
import json
import pytest
from gxworks2_mcp import simulator as sim


def test_fx_octal_and_decimal_addresses():
    assert sim.location('X10') == (0, 8)
    assert sim.location('Y20') == (0x22, 0)
    assert sim.location('M500') == (0x60 + 62, 4)
    assert sim.location('S20') == (0x862, 4)
    assert sim.location('T250') == (0xa80 + 30, 10)
    assert sim.location('TN250') == (0xb40 + 500, None)


@pytest.mark.parametrize('device', ['X8','X400','Y400','M7680','M8512','S4096','TN512','D8000','X0\nY0'])
def test_unsupported_addresses_rejected(device):
    with pytest.raises(ValueError): sim.location(device)


@pytest.mark.parametrize('values', [{'Y0':1},{'M0':1},{'X0':2},{'X0':True},{'X8':0},{}])
def test_only_explicit_virtual_input_bits_can_be_written(values):
    with pytest.raises(ValueError): sim.validate_inputs(values)


def test_duplicate_normalized_input_rejected():
    with pytest.raises(ValueError): sim.validate_inputs({'X0':0,'x00':1})


def test_sequence_validates_all_steps_before_writes(monkeypatch):
    monkeypatch.setattr(sim, 'write_inputs', lambda *a,**k: pytest.fail('不得提前写入'))
    with pytest.raises(ValueError):
        sim.verify([{'inputs':{'X0':1},'expect':{'Y0':1}}, {'inputs':{'Y0':1},'expect':{'Y0':0}}])


def test_unknown_engine_version_is_rejected(monkeypatch,tmp_path):
    exe=tmp_path/'FXSimRun2.exe';exe.write_bytes(b'unknown version')
    monkeypatch.setattr(sim,'processes',lambda:[dict(name='FXSimRun2.exe',pid=1,path=str(exe))])
    with pytest.raises(ValueError,match='版本'):sim.target()


def test_multiple_simulators_are_not_selected_implicitly(monkeypatch):
    monkeypatch.setattr(sim,'processes',lambda:[dict(name='FXSimRun2.exe',pid=p) for p in (1,2)])
    with pytest.raises(ValueError,match='唯一'):sim.target()


def test_pulse_releases_input_when_wait_fails(monkeypatch):
    from contextlib import contextmanager
    word=[0]
    @contextmanager
    def fake_memory(write=False):
        def read(address,size):return (0x10000 if address==sim.MASTER_ADDRESS else word[0]).to_bytes(size,'little')
        def put(address,data):word[0]=int.from_bytes(data,'little')
        yield dict(pid=1),0x10000,read,put
    monkeypatch.setattr(sim,'memory',fake_memory)
    monkeypatch.setattr(sim.time,'sleep',lambda _:(_ for _ in ()).throw(RuntimeError('interrupted')))
    with pytest.raises(RuntimeError):sim.pulse('X0')
    assert word[0]==0


def test_confirmation_rejection_does_not_touch_simulator(monkeypatch):
    from gxworks2_mcp import server
    monkeypatch.setattr(sim,'write_inputs',lambda *_:pytest.fail('未确认不写入'))
    assert not json.loads(server.gx_simulator_inputs({'X0':1}))['ok']


def test_start_waits_for_completed_transfer(monkeypatch):
    from gxworks2_mcp import simulator_ui as ui
    from types import SimpleNamespace
    state={'phase':0}
    def status():
        if state['phase']==1:raise ui.SimulatorNotReady('进程先于面板出现')
        return dict(ok=True,active=state['phase']>0)
    monkeypatch.setattr(ui,'status',status)
    monkeypatch.setattr(ui,'frame',lambda:(dict(pid=1,hwnd=1,title='test.gxw'),None))
    def windows(parent=None):
        if parent==1:return [dict(title='[PRG]监视 执行中')]
        if parent==2:return [dict(id=3008,hwnd=3)]
        return [dict(pid=1,kind='#32770',visible=True,title='PLC写入',hwnd=2)] if state['phase']==2 else []
    monkeypatch.setattr(ui,'windows',windows)
    monkeypatch.setattr(ui,'text',lambda _: '程序 (MAIN) 写入 : 完成\nPLC写入 : 结束')
    monkeypatch.setattr(sim,'api',lambda:(None,SimpleNamespace(PostMessageW=lambda *args:state.update(phase=1 if args[0]==1 else 3))))
    def wait(check,seconds):
        assert check() is None
        state['phase']=2;assert check() is None
        return check()
    monkeypatch.setattr(ui,'wait',wait)
    assert ui.start()['transferConfirmed']


def test_cpu_control_needs_no_focus_or_mouse_dependency(monkeypatch):
    import sys
    from gxworks2_mcp import simulator_ui as ui
    state={'mode':'stop'}
    monkeypatch.setitem(sys.modules,'pywinauto',None)
    monkeypatch.setattr(ui,'status',lambda:dict(active=True,pid=1,cpuSwitch=state['mode'],runState=state['mode']))
    monkeypatch.setattr(ui,'panel',lambda:dict(hwnd=10,pid=2))
    monkeypatch.setattr(ui,'windows',lambda _:[dict(id=1008,title='STOP',hwnd=11),dict(id=1009,title='RUN',hwnd=12)])
    def send(hwnd,message,wparam=0,buffer=None):
        if message==0x111:
            assert hwnd==10 and buffer.value==12
            state['mode']='run'
    monkeypatch.setattr(ui,'send',send)
    assert ui.cpu_control('run')['runState']=='run'


def test_cpu_control_does_not_accept_radio_state_as_execution(monkeypatch):
    from gxworks2_mcp import simulator_ui as ui
    readings=iter([dict(active=True,pid=1,cpuSwitch='stop',runState='stop'),
                   dict(active=True,pid=1,cpuSwitch='run',runState='stop'),
                   dict(active=True,pid=1,cpuSwitch='run',runState='stop')])
    monkeypatch.setattr(ui,'status',lambda:next(readings))
    monkeypatch.setattr(ui,'panel',lambda:dict(hwnd=10,pid=2))
    monkeypatch.setattr(ui,'windows',lambda _:[dict(id=1008,title='STOP',hwnd=11),dict(id=1009,title='RUN',hwnd=12)])
    monkeypatch.setattr(ui,'send',lambda *a:None)
    def fail_wait(check,seconds):
        assert check() is None
        raise TimeoutError('引擎没有运行')
    monkeypatch.setattr(ui,'wait',fail_wait)
    with pytest.raises(TimeoutError):ui.cpu_control('run')
