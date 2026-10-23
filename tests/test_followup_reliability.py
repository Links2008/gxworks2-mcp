"""补修必须同时保留诊断准确性与后台进程所有权边界。"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import pytest
from gxworks2_mcp.il import parse_il
from gxworks2_mcp.validate import validate_program
from gxworks2_mcp.analysis import where_used, trace_device, interlock_summary
from gxworks2_mcp.semantics import write_kind


def test_reset_must_not_hide_multiple_timer_drivers():
    source='LD X0\nOUT T0 K10\nLD X1\nOUT T0 K20\nLD X2\nRST T0\nEND'
    assert 'DOUBLE_COIL' in {i.code for i in validate_program(parse_il(source),cpu='FX3U')}
    assert 'T0' in interlock_summary(parse_il(source))['multiWriterCoils'] or 'TN0' in interlock_summary(parse_il(source))['multiWriterCoils']


def test_single_timer_driver_with_reset_remains_legal():
    source='LD X0\nOUT T0 K10\nLD X1\nRST T0\nEND'
    assert 'DOUBLE_COIL' not in {i.code for i in validate_program(parse_il(source),cpu='FX3U')}


def test_state_initialization_is_not_a_generic_latch_pair():
    assert write_kind('S0',[dict(op='SET',state=None),dict(op='RST',state=None)],fx=True)=='state_control'


@pytest.mark.parametrize('source,reads,writes',[
    ('DMOV D0 D2',{'D0','D1'},{'D2','D3'}),
    ('DMOV C200 D0',{'CN200'},{'D0','D1'}),
    ('DMUL D0 D2 D4',{'D0','D1','D2','D3'},{'D4','D5','D6','D7'}),
    ('DIV D0 D1 D2',{'D0','D1'},{'D2','D3'}),
    ('CMP D0 K10 M100',{'D0'},{'M100','M101','M102'}),
    ('DCMP D0 D2 M100',{'D0','D1','D2','D3'},{'M100','M101','M102'}),
    ('DINC D0',{'D0','D1'},{'D0','D1'}),
])
def test_implicit_addresses_are_in_cross_reference(source,reads,writes):
    sites=where_used(parse_il('LD X0\n'+source+'\nEND'))
    assert reads <= {d for d,rows in sites.items() if any('R' in r['kind'] for r in rows)}
    assert writes <= {d for d,rows in sites.items() if any('W' in r['kind'] for r in rows)}


def test_compare_result_trace_finds_its_writer():
    p=parse_il('LD X0\nCMP D0 K10 M100\nEND')
    tree=trace_device(p,'M101')
    assert tree['writes'] and tree['writes'][0]['mnemonic']=='CMP'
    assert 'X0' in tree['writes'][0]['expands']


def test_wide_comparison_contact_reads_both_words():
    sites=where_used(parse_il('LDD< D0 D2\nOUT Y0\nEND'))
    assert all(sites[d][0]['kind']=='R' for d in ['D0','D1','D2','D3'])


def test_native_32_bit_counter_is_one_logical_device():
    sites=where_used(parse_il('LD X0\nDMOV C200 D0\nEND'))
    assert 'CN200' in sites and 'CN201' not in sites


def test_implicit_compare_write_conflicts_with_another_coil():
    p=parse_il('LD X0\nCMP D0 K10 M100\nLD X1\nOUT M101\nEND')
    assert interlock_summary(p)['multiWriterCoils']['M101']==2


def test_primary_failure_survives_failed_cleanup(monkeypatch,tmp_path):
    from tools import verify_general_capabilities as runner
    from contextlib import asynccontextmanager
    import mcp
    import mcp.client.stdio
    calls=[]
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
        async def initialize(self):pass
        async def list_tools(self):raise RuntimeError('primary_failure')
        async def call_tool(self,name,args):
            calls.append(name)
            if name=='gx_simulator_control':raise RuntimeError('cleanup_failure')
            return SimpleNamespace(content=[SimpleNamespace(type='text',text='{"active":false}')])
    @asynccontextmanager
    async def transport(*args,**kwargs):yield None,None
    monkeypatch.setattr(mcp,'ClientSession',lambda *a:Client())
    monkeypatch.setattr(mcp.client.stdio,'stdio_client',transport)
    with pytest.raises(BaseException) as error:asyncio.run(runner.exercise(tmp_path/'test.gxw',tmp_path))
    assert 'primary_failure' in repr(error.value)
    assert calls==['gx_simulator_control','gx_simulator_stop']
    report=json.loads((tmp_path/'MCP真实验收.json').read_text(encoding='utf-8'))
    assert report['cleanupErrors']


@pytest.mark.skipif(os.name!='nt',reason='Windows进程所有权检查')
def test_owned_job_cleanup_kills_its_descendants_only(tmp_path):
    import win32api,win32process,win32event
    from tools import verify_general_capabilities as runner
    ready=tmp_path/'ready.json'
    script="import subprocess,sys,time,json,os;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=0x08000000);Path(sys.argv[1]).write_text(json.dumps([os.getpid(),p.pid]));time.sleep(60)"
    stranger=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW)
    job=process=child=None
    try:
        job,process,_=runner.start_owned_worker(subprocess.list2cmdline([sys.executable,'-c',script,str(ready)]),win32process.STARTUPINFO(),dict(os.environ),str(tmp_path))
        deadline=time.monotonic()+5
        while not ready.exists():
            assert time.monotonic()<deadline
            time.sleep(.02)
        child=win32api.OpenProcess(0x100000,False,json.loads(ready.read_text())[1])
        runner.close_owned_worker(job,process);job=process=None
        assert win32event.WaitForSingleObject(child,1000)==0
        assert stranger.poll() is None
    finally:
        if job is not None:runner.close_owned_worker(job,process)
        if child is not None:child.Close()
        stranger.terminate();stranger.wait(timeout=5)


@pytest.mark.skipif(os.name!='nt',reason='Windows启动失败收尾检查')
def test_assignment_failure_never_resumes_the_created_process(monkeypatch,tmp_path):
    import win32api,win32process,win32event,win32job
    from tools import verify_general_capabilities as runner
    created=[];original=win32process.CreateProcess;marker=tmp_path/'must-not-exist'
    def create(*args):
        result=original(*args)
        created.append(win32api.OpenProcess(0x100000,False,result[2]))
        return result
    def deny(*args):raise RuntimeError('assignment_denied')
    monkeypatch.setattr(win32process,'CreateProcess',create)
    monkeypatch.setattr(win32job,'AssignProcessToJobObject',deny)
    command=subprocess.list2cmdline([sys.executable,'-c','from pathlib import Path;import sys;Path(sys.argv[1]).write_text("ran")',str(marker)])
    try:
        with pytest.raises(RuntimeError,match='assignment_denied'):
            runner.start_owned_worker(command,win32process.STARTUPINFO(),dict(os.environ),str(tmp_path))
        assert created and win32event.WaitForSingleObject(created[0],1000)==0
        assert not marker.exists()
    finally:
        for handle in created:handle.Close()
