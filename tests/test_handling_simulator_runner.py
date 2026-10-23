"""回归上次读到暂停过渡态的问题；保持校验不得接受计时变化。"""
import pytest
from tools import verify_handling_simulator as runner


def test_pause_waits_for_run_permission_zero(monkeypatch):
    readings=iter([{'M0':1},{'M0':0}]);calls=[]
    monkeypatch.setattr(runner.sim,'read_devices',lambda _: {'values':next(readings)})
    monkeypatch.setattr(runner.time,'sleep',lambda _:calls.append('wait'))
    check=runner.Acceptance();check.check('暂停确认',{'M0':0})
    assert calls==['wait'] and check.events[0]['actual']=={'M0':0}


def test_timer_change_during_hold_is_failure(monkeypatch):
    monkeypatch.setattr(runner.sim,'read_devices',lambda _: {'values':{'TN250':3}})
    with pytest.raises(AssertionError):runner.Acceptance().hold('暂停保持',.2,{'TN250':2})


def test_start_button_requires_release_scan_ack(monkeypatch):
    readings=iter([{'X0':0,'M90':1},{'X0':0,'M90':0},{'X0':0,'M90':0}]);order=[]
    monkeypatch.setattr(runner.sim,'read_devices',lambda _: {'values':next(readings)})
    monkeypatch.setattr(runner.time,'sleep',lambda _:order.append('wait'))
    monkeypatch.setattr(runner.sim,'pulse',lambda *a:order.append('press'))
    runner.Acceptance().press('X0','M90')
    assert order==['wait','press']
