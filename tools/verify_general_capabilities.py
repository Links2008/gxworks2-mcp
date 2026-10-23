"""在不切换的 Windows 隔离桌面验收真实 MCP 与 FX 模拟器；保留用户工程。"""
import asyncio
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
from gxworks2_mcp import simulator as sim, simulator_ui as ui
from tools.finish_handling_sfc import verify_native


def start_owned_worker(command, startup, env, cwd):
    """先挂入私有作业再恢复执行，保证子进程从出生起属于本次验收。"""
    import win32api,win32job,win32process,win32event
    job=win32job.CreateJobObject(None,'GX_MCP_Job_'+uuid4().hex);process=thread=None
    try:
        limits=win32job.QueryInformationJobObject(job,win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job,win32job.JobObjectExtendedLimitInformation,limits)
        process,thread,pid,_=win32process.CreateProcess(None,command,None,None,False,
            subprocess.CREATE_NO_WINDOW|0x4,env,cwd,startup)  # CREATE_SUSPENDED
        win32job.AssignProcessToJobObject(job,process)
        win32process.ResumeThread(thread)
        return job,process,pid
    except BaseException:
        try:
            if process is not None:
                try:win32api.TerminateProcess(process,1);win32event.WaitForSingleObject(process,5000)
                finally:process.Close()
        finally:job.Close()
        raise
    finally:
        if thread is not None:thread.Close()


def close_owned_worker(job, process):
    """只收回本次作业中的进程树，不按软件名称终止任何用户进程。"""
    import win32job,win32event
    try:
        win32job.TerminateJobObject(job,1)
        deadline=time.monotonic()+5
        while win32job.QueryInformationJobObject(job,win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']:
            if time.monotonic()>=deadline:raise TimeoutError('本次验收进程树尚未全部退出')
            time.sleep(.02)
        if win32event.WaitForSingleObject(process,1000)!=0:raise TimeoutError('验收主进程尚未退出')
    finally:
        try:job.Close()
        finally:process.Close()


def desktop_name():
    u=C.WinDLL('user32',use_last_error=True);k=C.WinDLL('kernel32',use_last_error=True)
    u.GetThreadDesktop.argtypes=[C.c_uint];u.GetThreadDesktop.restype=C.c_void_p
    u.GetUserObjectInformationW.argtypes=[C.c_void_p,C.c_int,C.c_void_p,C.c_uint,C.POINTER(C.c_uint)]
    handle=u.GetThreadDesktop(k.GetCurrentThreadId());name=C.create_unicode_buffer(256);needed=C.c_uint()
    if not u.GetUserObjectInformationW(handle,2,name,C.sizeof(name),C.byref(needed)):
        raise C.WinError(C.get_last_error())
    return name.value


def cascade_steps():
    """启动软件前即可构造和校验的完整输入组合。"""
    from itertools import product
    return [dict(label=f'连续输出{a}{b}{c}{d}',
        inputs=dict(zip(['X30','X31','X32','X33'],[a,b,c,d])),
        expect={'Y30':a,'Y31':a,'Y32':a and b,'Y33':int(a and b and not c),'Y34':d},
        hold_ms=60) for a,b,c,d in product((0,1),repeat=4)]


def counter_steps():
    """同一批逐次等待按下及松开反馈，减少协议交接而不缩短动作。"""
    steps=[]
    for device,counter,ack,count in [('X3','CN0','M10',3),('X4','CN200','M11',2)]:
        for value in range(1,count+1):
            steps.extend([dict(inputs={device:1},expect={counter:value,ack:1}),
                          dict(inputs={device:0},expect={ack:0})])
    return steps


def close_owned_app(app):
    """仿真已结束，只回收本轮在隔离桌面创建的只读测试进程。"""
    if app.poll() is None:app.terminate()
    app.wait(timeout=5)


async def exercise(project, out):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from examples.fx3u_data_operations import build_spec
    params=StdioServerParameters(command=sys.executable,args=['-X','utf8','-m','gxworks2_mcp'],cwd=str(Path(__file__).resolve().parents[1]),
        env={**os.environ,'GXWORKS2_MCP_HOME':str(out/'store'),'PYTHONIOENCODING':'utf-8'})
    events=[];failure=None
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize()
            async def call(tool_name,**args):
                if tool_name=='gx_simulator_verify':args.setdefault('project_name','data')
                began=time.perf_counter()
                response=await client.call_tool(tool_name,args)
                result=json.loads(next(c.text for c in response.content if c.type=='text'))
                events.append(dict(tool=tool_name,result=result,callMs=round((time.perf_counter()-began)*1000,2)))
                return result
            try:
                listing=await client.list_tools()
                read_tool=next(x for x in listing.tools if x.name=='gx_simulator_read')
                schema=getattr(read_tool,'input_schema',None) or getattr(read_tool,'inputSchema',{})
                assert 'types' in schema['properties']
                library=await call('gx_skill_list')
                for entry in library['skills']:
                    assert (await call('gx_skill_read',name=entry['name']))['ok']
                prepared=await call('gx_program_prepare',name='data',cpu='FX3U',rungs=build_spec()['preamble'])
                assert prepared['prepared'],prepared
                candidate=[dict(r) for r in build_spec()['preamble']]
                next(r for r in candidate if r.get('out')=='T0')['preset']='K3'
                assert (await call('gx_program_prepare',name='impact-after',cpu='FX3U',rungs=candidate))['prepared']
                impact=await call('gx_change_impact',before_name='data',after_name='impact-after')
                assert impact['ok'] and impact['affectedOutputs']==['Y2'] and 'TN0' in impact['affectedTimers'],impact
                assert not impact['runtimeVerified'] and not impact['externalMappingsVerified'],impact
                (out/'改动影响样例.json').write_text(json.dumps(impact,ensure_ascii=False,indent=2),encoding='utf-8')
                occupancy=(await call('gx_where_used',name='data'))['usage']
                proposed={'D60','D61','M310'}
                assert proposed.isdisjoint(occupancy)
                (out/'地址规划样例.json').write_text(json.dumps(dict(existing=sorted(occupancy),
                    proposed=[dict(role='新增DINT',address='D60',span=['D60','D61']),
                              dict(role='新增许可',address='M310',span=['M310'])],
                    assumptions=['独立验收工程，无外部HMI占用'],conflicts=[]),ensure_ascii=False,indent=2),encoding='utf-8')
                assert (await call('gx_program_prepare',name='bad-reset',cpu='FX3U',
                    il='LD X23\nSET M300\nLD M300\nOUT Y20\nEND'))['prepared']
                bad=await call('gx_trace_device',name='bad-reset',device='M300')
                assert any('X23' in r['logic'] for r in bad['writes']),bad
                (out/'模式审查样例.json').write_text(json.dumps(dict(requirement='复位仅恢复待机',
                    finding='X23复位直接置位运行M300，会启动Y20',evidence=bad,
                    correction='独立运行沿触发及报警/模式许可，复位不置位运行'),ensure_ascii=False,indent=2),encoding='utf-8')
                refs=await call('gx_where_used',name='data',device='M104')
                assert any(r['kind']=='W' and r['instruction'].startswith('DCMP ') for r in refs['sites']),refs
                traced=await call('gx_trace_device',name='data',device='M104')
                assert any(r['mnemonic']=='DCMP' for r in traced['writes']),traced
                readback=await call('gx_sfc_read',path=str(project))
                assert readback['sourceReadback']
                scan_il='\n'.join(s['il'] for b in readback['blocks'] if b['scanBlock'] for s in b['steps'])
                cascade_fixture='OUT Y30' in scan_il
                if cascade_fixture:
                    assert (await call('gx_program_prepare',name='cascade',cpu='FX3U',il=scan_il+'\nEND'))['prepared']
                    for device,required,excluded in [('Y31',['X30'],['X31']),
                            ('Y32',['X30','X31'],['X32']),('Y33',['X30','X31','X32'],[]),
                            ('Y34',['X33'],['X30','X31','X32'])]:
                        traced=await call('gx_trace_device',name='cascade',device=device)
                        condition=traced['writes'][0]['logic']
                        assert all(x in condition for x in required),traced
                        assert not any(x in condition for x in excluded),traced
                assert (await call('gx_simulator_start',confirm=True))['active']
                assert (await call('gx_simulator_control',action='stop',confirm=True))['runState']=='stop'
                initial={f'X{i:o}':0 for i in [*range(11),*range(16,22)]}
                if cascade_fixture:initial.update({f'X3{i}':0 for i in range(4)})
                assert (await call('gx_simulator_inputs',values=initial,confirm=True))['ok']
                assert (await call('gx_simulator_control',action='run',confirm=True))['runState']=='run'
                expected={'D0':-123,'D2':-123456,'D12':15,'D13':9,'D14':36,'D16':4,'D17':0,
                          'D20':-122956,'D22':-123556,'D24':-246912,'D26':-1,
                          'D28':-61728,'D30':0,'D40':10,'D42':-100000,
                          'M100':1,'M101':0,'M102':0,'M103':0,'M104':0,'M105':1,
                          'CN0':0,'CN200':0,'C0':0,'C200':0,'Y4':1}
                types={d:'int32' for d in ['D2','D20','D22','D24','D26','D28','D30','D42']};types['D0']='int16'
                result=await call('gx_simulator_verify',steps=[dict(expect=expected,types=types,hold_ms=100)],confirm=True)
                assert result['ok'],result
                raw=await call('gx_simulator_read',devices=['D0','D2'],types={'D2':'uint32'})
                assert raw['values']=={'D0':65413,'D2':4294843840},raw
                numeric=await call('gx_diagnose_output',name='data',device='Y4')
                assert numeric['status']=='active',numeric
                comparison=numeric['writers'][0]['evidence']
                assert comparison['kind']=='comparison' and comparison['bits']==32,numeric
                assert (comparison['leftActual'],comparison['rightActual'],comparison['value'])==(-123456,-100000,True),numeric
                diagnostic=await call('gx_diagnose_output',name='data',device='Y0')
                assert diagnostic['status']=='blocked',diagnostic
                assert diagnostic['writers'][0]['unmet']['children'][0]['device']=='X7',diagnostic
                assert (await call('gx_simulator_verify',steps=[dict(inputs={'X7':1,'X10':0},
                    expect={'Y0':1,'Y1':0},hold_ms=60)],confirm=True))['ok']
                before=(await call('gx_simulator_read',devices=['X7','X10']))['values']
                diagnostic=await call('gx_diagnose_output',name='data',device='Y0')
                assert diagnostic['status']=='active',diagnostic
                assert (await call('gx_simulator_read',devices=['X7','X10']))['values']==before
                assert (await call('gx_simulator_verify',steps=[dict(inputs={'X10':1},
                    expect={'Y0':0,'Y1':0},hold_ms=60)],confirm=True))['ok']
                diagnostic=await call('gx_diagnose_output',name='data',device='Y0')
                assert diagnostic['status']=='blocked',diagnostic
                blocked=diagnostic['writers'][0]['unmet']['children'][0]
                assert (blocked['device'],blocked['actual'],blocked['required'])==('X10',1,0),diagnostic
                assert (await call('gx_simulator_verify',steps=[dict(inputs={'X7':0,'X10':0},
                    expect={'Y0':0,'Y1':0,'Y2':0},hold_ms=60)],confirm=True))['ok']
                result=await call('gx_simulator_verify',steps=[
                    dict(inputs={'X0':1},expect={'S20':1,'D50':-123456},types={'D50':'int32'}),
                    dict(inputs={'X0':0,'X1':1},expect={'S0':1}),
                    dict(inputs={'X1':0},expect={'S0':1})],confirm=True)
                assert result['ok'],result
                ack=await call('gx_simulator_verify',steps=counter_steps(),confirm=True)
                assert ack['ok'],ack
                counter=await call('gx_simulator_read',devices=['C0','CN0','C200','CN200'])
                assert counter['values']=={'C0':1,'CN0':3,'C200':1,'CN200':2},counter
                normal=await call('gx_simulator_verify',steps=[
                    dict(inputs={'X7':1},expect={'Y2':1},min_delay_ms=80,timeout_ms=1500,hold_ms=100),
                    dict(inputs={'X7':0},expect={'Y0':0,'Y2':0},hold_ms=100),
                    dict(inputs={'X10':1},expect={'Y0':0,'Y1':1},hold_ms=100),
                    dict(inputs={'X7':1},expect={'Y0':0,'Y1':0},hold_ms=60),
                    dict(inputs={'X7':0,'X10':0},expect={'Y0':0,'Y1':0})],forbid=[{'Y0':1,'Y1':1}],confirm=True)
                assert normal['ok'],normal
                if cascade_fixture:
                    result=await call('gx_simulator_verify',steps=cascade_steps(),confirm=True)
                    assert result['ok'],result
                failures=[
                    ([dict(inputs={'X12':1},expect={'Y3':1},hold_ms=2200)],None,'hold_broken'),
                    ([dict(expect={'D12':15},min_delay_ms=200)],None,'too_early'),
                    ([dict(expect={'D12':999},timeout_ms=80)],None,'timeout'),
                    ([dict(inputs={'X7':1},expect={'Y1':1})],[{'Y0':1}],'forbidden_combination'),
                ]
                for steps,forbid,reason in failures:
                    steps.append(dict(inputs={'X11':1},expect={'Y0':0}))
                    result=await call('gx_simulator_verify',steps=steps,forbid=forbid,confirm=True)
                    assert not result['ok'] and len(result['trace'])==1 and result['trace'][0]['reason']==reason,result
                    assert (await call('gx_simulator_read',devices=['X11']))['values']['X11']==0
                mode_steps=[
                    dict(label='自动启动',inputs={'X20':1},expect={'M300':1,'M302':1,'Y20':1,'Y21':0}),
                    dict(label='启动松开',inputs={'X20':0},expect={'M302':0,'Y20':1}),
                    dict(label='故障切断并锁存',inputs={'X22':1},expect={'M300':0,'M301':1,'Y20':0,'Y21':1}),
                    dict(label='故障存在拒绝复位',inputs={'X23':1},expect={'M301':1,'Y20':0},hold_ms=100),
                    dict(label='故障解除仍保持报警',inputs={'X23':0,'X22':0},expect={'M301':1,'Y20':0}),
                    dict(label='报警中启动拒绝',inputs={'X20':1},expect={'M302':1,'M300':0,'Y20':0}),
                    dict(label='复位不重启',inputs={'X23':1},expect={'M301':0,'M300':0,'Y20':0},hold_ms=100),
                    dict(label='松开启动重新准备',inputs={'X20':0,'X23':0},expect={'M302':0}),
                    dict(label='重新启动',inputs={'X20':1},expect={'M300':1,'Y20':1}),
                    dict(label='停止优先',inputs={'X21':1},expect={'M300':0,'Y20':0}),
                    dict(label='停止释放且启动保持不重启',inputs={'X21':0},expect={'M300':0,'Y20':0},hold_ms=100),
                    dict(label='手动点动',inputs={'X20':0,'X24':1,'X25':1},expect={'M300':0,'Y20':1}),
                    dict(label='返回自动不自行启动',inputs={'X24':0},expect={'M300':0,'Y20':0},hold_ms=100),
                    dict(label='点动释放',inputs={'X25':0},expect={'Y20':0}),
                ]
                (out/'工艺场景样例.json').write_text(json.dumps(dict(steps=mode_steps,forbid=[{'Y20':1,'Y21':1}]),ensure_ascii=False,indent=2),encoding='utf-8')
                modes=await call('gx_simulator_verify',steps=mode_steps,forbid=[{'Y20':1,'Y21':1}],confirm=True)
                assert modes['ok'],modes
                assert (await call('gx_simulator_inputs',values=initial,confirm=True))['ok']
                archive=await call('gx_handover_export',name='data')
                assert archive['ok'] and archive['evidenceCount']>=10,archive
                verified=await call('gx_handover_verify',path=archive['path'],name='data')
                assert verified['ok'] and verified['workspaceMatch'] and not verified['simulatorAccessed'],verified
                mismatch=await call('gx_handover_verify',path=archive['path'],name='impact-after')
                assert not mismatch['ok'] and mismatch['integrityVerified'] and mismatch['workspaceMatch'] is False,mismatch
                import hashlib,zipfile
                archive_path=Path(archive['path'])
                assert hashlib.sha256(archive_path.read_bytes()).hexdigest()==archive['sha256']
                with zipfile.ZipFile(archive_path) as zipped:
                    assert zipped.testzip() is None
                    manifest=json.loads(zipped.read('归档清单.json'))
                    assert any(r['kind']=='simulator_verify' for r in json.loads(zipped.read('归档证据.json')))
                    assert all(hashlib.sha256(zipped.read(n)).hexdigest()==h for n,h in manifest['files'].items())
            except BaseException as exc:
                failure=repr(exc)
                raise
            finally:
                cleanup=[];results=[]
                for name,args in [('gx_simulator_control',dict(action='stop',confirm=True)),
                                  ('gx_simulator_stop',dict(confirm=True))]:
                    try:result=await call(name,**args)
                    except Exception as exc:
                        result=dict(ok=False,error=repr(exc));cleanup.append(dict(tool=name,error=repr(exc)))
                    results.append(result)
                stopped,ended=results
                (out/'MCP真实验收.json').write_text(json.dumps(dict(events=events,cpuFinal=stopped,
                    simulatorFinal=ended,failure=failure,cleanupErrors=cleanup),ensure_ascii=False,indent=2),encoding='utf-8')
                if failure is None:
                    assert stopped.get('runState')=='stop' and ended.get('active') is False,(stopped,ended)
    return len(events)


def child(project,report,out):
    actual_desktop=desktop_name()
    if actual_desktop!=os.environ['GX_VALIDATION_DESKTOP'].split('\\')[-1]:
        raise ValueError('实际桌面与隔离目标不一致，拒绝启动验收软件')
    started=time.perf_counter();verify_native(project,json.loads(report.read_text(encoding='utf-8')))
    startup=subprocess.STARTUPINFO();startup.lpDesktop=os.environ['GX_VALIDATION_DESKTOP']
    startup.dwFlags=subprocess.STARTF_USESHOWWINDOW|0x80;startup.wShowWindow=4
    app=subprocess.Popen([r'D:\新建文件夹\GPPW2\GD2.exe',str(project)],startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW)
    result=dict(passed=False,desktop=actual_desktop,switchedDesktop=False,screenshots=0)
    try:
        frame=ui.wait(lambda:next((w for w in ui.windows() if w['pid']==app.pid and w['kind']=='GXW2FrameWnd' and project.name in w['title']),None),30)
        ready=time.perf_counter();result['gxReadySeconds']=round(ready-started,3)
        result['mcpCalls']=asyncio.run(exercise(project,out));result['passed']=True
        result['mcpSeconds']=round(time.perf_counter()-ready,3)
        verify_native(project,json.loads(report.read_text(encoding='utf-8')))
    except Exception as exc:
        result['error']=repr(exc)
    finally:
        if any(p['name'].lower()=='fxsimrun2.exe' for p in sim.processes()):
            try:ui.cpu_control('stop');ui.stop()
            except Exception as exc:result.update(passed=False,cleanupError=repr(exc))
        cleanup_started=time.perf_counter()
        try:
            close_owned_app(app)
            verify_native(project,json.loads(report.read_text(encoding='utf-8')))
        except Exception as exc:result.update(passed=False,appCleanupError=repr(exc))
        result['appCleanupSeconds']=round(time.perf_counter()-cleanup_started,3)
        result['seconds']=round(time.perf_counter()-started,3)
        (out/'隔离桌面验收.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def launch(project,report,out):
    import win32process,win32event
    out.mkdir(parents=True,exist_ok=True);verify_native(project,json.loads(report.read_text(encoding='utf-8')))
    sim.validate_verification(cascade_steps())
    sim.validate_verification(counter_steps())
    original=ui.status()
    frames={w['hwnd']:w['title'] for w in ui.windows() if w['kind']=='GXW2FrameWnd' and w['visible']}
    if original.get('runState')=='run':raise ValueError('用户模拟器正在运行，保留其状态')
    _,u=sim.api();u.GetForegroundWindow.restype=C.c_void_p
    u.CreateDesktopW.argtypes=[C.c_wchar_p,C.c_wchar_p,C.c_void_p,C.c_uint,C.c_uint,C.c_void_p];u.CreateDesktopW.restype=C.c_void_p
    u.CloseDesktop.argtypes=[C.c_void_p]
    desktop='GX_MCP_Validation_'+uuid4().hex;handle=u.CreateDesktopW(desktop,None,None,0,0x1ff,None)
    if not handle:raise C.WinError(C.get_last_error())
    before=u.GetForegroundWindow();activated=False;started=time.perf_counter()
    try:
        if original['active']:ui.stop()
        # subprocess 的 STARTUPINFO 封装忽略 lpDesktop，必须使用原生 CreateProcess。
        startup=win32process.STARTUPINFO();startup.lpDesktop='WinSta0\\'+desktop;startup.dwFlags=0x80
        env={**os.environ,'GX_VALIDATION_DESKTOP':startup.lpDesktop,'PYTHONIOENCODING':'utf-8'}
        command=subprocess.list2cmdline([sys.executable,'-X','utf8','-m','tools.verify_general_capabilities','--child',str(project),str(report),str(out)])
        job,process,_=start_owned_worker(command,startup,env,str(Path(__file__).resolve().parents[1]))
        try:
            while win32event.WaitForSingleObject(process,0)==258:
                activated |= u.GetForegroundWindow() in frames
                if time.perf_counter()-started>120:raise TimeoutError('隔离验收超时，保留诊断')
                time.sleep(.05)
            exit_code=win32process.GetExitCodeProcess(process)
        finally:close_owned_worker(job,process)
        result=json.loads((out/'隔离桌面验收.json').read_text(encoding='utf-8'))
        if exit_code!=0 or result.get('desktop')!=desktop:result['passed']=False
    finally:
        u.CloseDesktop(handle)
    after={w['hwnd']:w['title'] for w in ui.windows() if w['kind']=='GXW2FrameWnd' and w['visible']}
    preserved=all(after.get(h)==title for h,title in frames.items())
    result.update(totalSeconds=round(time.perf_counter()-started,3),foregroundBefore=before,
                  foregroundAfter=u.GetForegroundWindow(),userGxActivated=activated,
                  originalProjectWindows=list(frames.values()),originalWindowsPreserved=preserved,simulatorFinal=ui.status(),
                  originalSimulatorRestarted=False)
    if not preserved or activated:result['passed']=False
    (out/'后台完整验收.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    args=sys.argv[1:];worker=args[:1]==['--child'];args=args[1:] if worker else args
    try:result=(child if worker else launch)(*[Path(x).resolve() for x in args])
    except Exception as exc:
        result=dict(passed=False,error=repr(exc),actualDesktop=desktop_name())
        if len(args)==3:
            target=Path(args[2]);target.mkdir(parents=True,exist_ok=True)
            (target/('隔离桌面验收.json' if worker else '后台完整验收.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False));raise SystemExit(0 if result['passed'] else 1)
