"""已验证 GX Works2 1.620 的仿真生命周期；只向模拟器及所属工程窗口发命令。"""
import ctypes as C
import hashlib
from pathlib import Path
import time
from . import simulator as sim


class SimulatorNotReady(ValueError):
    """冷启动进程已出现，但控制面板尚未创建。"""


def windows(parent=None):
    _,u=sim.api(); rows=[]
    callback=C.WINFUNCTYPE(C.c_bool,C.c_void_p,C.c_void_p)
    @callback
    def visit(h,_):
        title=C.create_unicode_buffer(4096); kind=C.create_unicode_buffer(256); pid=C.c_uint()
        u.GetWindowTextW(h,title,len(title));u.GetClassNameW(h,kind,len(kind));u.GetWindowThreadProcessId(h,C.byref(pid))
        rows.append(dict(hwnd=h,pid=pid.value,title=title.value,kind=kind.value,
                         id=u.GetDlgCtrlID(h),visible=bool(u.IsWindowVisible(h))))
        return True
    if parent is None:u.EnumWindows(visit,0)
    else:u.EnumChildWindows(parent,visit,0)
    return rows


def send(hwnd,message,wparam=0,buffer=None):
    _,u=sim.api();result=C.c_size_t()
    if not u.SendMessageTimeoutW(hwnd,message,wparam,C.cast(buffer,C.c_void_p) if buffer is not None else None,
                                 2,3000,C.byref(result)):
        raise TimeoutError('模拟器控件没有响应')
    return result.value


def text(hwnd):
    buf=C.create_unicode_buffer(8192);send(hwnd,0xD,len(buf),buf);return buf.value


def wait(check,seconds=20):
    end=time.perf_counter()+seconds
    while time.perf_counter()<end:
        result=check()
        if result:return result
        time.sleep(.1)
    raise TimeoutError('模拟器没有在限定时间内达到目标状态')


def frame():
    apps=[p for p in sim.processes() if p['name'].lower()=='gd2.exe']
    frames=[w for w in windows() if w['kind']=='GXW2FrameWnd' and w['visible'] and any(p['pid']==w['pid'] for p in apps)]
    if len(frames)!=1:raise ValueError('需要唯一的 GX Works2 工程窗口')
    window=frames[0]; app=next(p for p in apps if p['pid']==window['pid'])
    engine=Path(app['path']).parent/'GX Simulator2'/'FXCPU'/'FXSimRun2.exe'
    if hashlib.sha256(engine.read_bytes()).hexdigest()!=sim.ENGINE_SHA256:raise ValueError('此安装的模拟器版本未经核验')
    if '.gxw' not in window['title'].lower():raise ValueError('请先打开要仿真的 GXW 工程')
    return window,engine


def panel():
    engine=sim.target()
    expected=Path(engine['path']).parents[1]/'Common'/'SimManager.exe'
    managers=[p for p in sim.processes() if p['name'].lower()=='simmanager.exe' and Path(p['path'])==expected]
    rows=[w for w in windows() if w['title']=='GX Simulator2' and w['visible'] and any(p['pid']==w['pid'] for p in managers)]
    if not rows:raise SimulatorNotReady('虚拟CPU进程已启动，模拟器面板尚未就绪')
    if len(rows)!=1:raise ValueError('没有找到与虚拟 CPU 对应的唯一模拟器面板')
    return rows[0]


def status():
    if not any(p['name'].lower()=='fxsimrun2.exe' for p in sim.processes()):
        return dict(ok=True,active=False,physicalPlc=False)
    engine=sim.target(); view=panel(); children=windows(view['hwnd'])
    switches={w['id']:send(w['hwnd'],0xF0) for w in children if w['id'] in (1008,1009)}
    mode='run' if switches.get(1009)==1 else 'stop' if switches.get(1008)==1 else 'unknown'
    live=sim.read_devices(['M8000'])
    if live['pid']!=engine['pid']:raise ValueError('模拟器实例已改变')
    return dict(ok=True,active=True,pid=engine['pid'],engine=engine['path'],cpuSwitch=mode,
                runState='run' if live['values']['M8000'] else 'stop',engineSha256=sim.ENGINE_SHA256,physicalPlc=False)


def cpu_control(action):
    if action not in ('run','stop'):raise ValueError('CPU操作仅支持run或stop')
    with sim._lock:
        before=status()
        if not before['active']:raise ValueError('模拟器尚未启动')
        if before['cpuSwitch']==action and before['runState']==action:return before
        view=panel();control=1009 if action=='run' else 1008
        buttons={w['id']:w for w in windows(view['hwnd']) if w['id'] in (1008,1009)}
        if len(buttons)!=2 or buttons[control]['title']!=action.upper():raise ValueError('模拟器开关结构不匹配')
        # 后台发送单选状态及点击通知，再用真实运行标志确认，不激活窗口。
        for ident,button in buttons.items():send(button['hwnd'],0xF1,int(ident==control))
        send(view['hwnd'],0x111,control,C.c_void_p(buttons[control]['hwnd']))
        def ready():
            observed=status()
            if not observed['active'] or observed['pid']!=before['pid']:raise ValueError('模拟器实例已改变')
            return observed if observed['cpuSwitch']==action and observed['runState']==action else None
        try:return wait(ready,5)
        except Exception:
            observed=status()
            if observed.get('pid')==before['pid']:
                actual=1009 if observed.get('runState')=='run' else 1008
                for ident,button in buttons.items():send(button['hwnd'],0xF1,int(ident==actual))
            raise


def start():
    with sim._lock:
        old=status();view,_=frame();_,u=sim.api()
        pending=[w for w in windows() if w['pid']==view['pid'] and w['kind']=='#32770' and w['visible']]
        if old['active'] and not pending:return dict(old,alreadyRunning=True)
        if any(w['title']!='PLC写入' for w in pending):raise ValueError('工程已有对话框，请先完成当前操作')
        if not old['active']:
            if pending:raise ValueError('工程已有传输，请先完成当前操作')
            u.PostMessageW(view['hwnd'],0x111,1677,0)
        transfer=[]
        def ready():
            dialogs=[w for w in windows() if w['pid']==view['pid'] and w['kind']=='#32770' and w['visible']]
            for dialog in dialogs:
                if dialog['title']!='PLC写入':raise ValueError('仿真启动出现未知对话框：'+dialog['title'])
                controls=windows(dialog['hwnd']);logs=[text(w['hwnd']) for w in controls if w['id']==3008]
                if logs and any(s in logs[0] for s in ('错误','失败')):raise ValueError(logs[0])
                if logs and 'PLC写入 : 结束' in logs[0] and '完成' in logs[0]:
                    if logs[0] not in transfer:transfer.append(logs[0])
                    u.PostMessageW(dialog['hwnd'],0x111,3001,0)
                return None
            try:current=status()
            except SimulatorNotReady:return None
            monitoring=any('[PRG]监视' in w['title'] for w in windows(view['hwnd']))
            aligned=current.get('runState')==current.get('cpuSwitch')
            return dict(current,projectWindow=view['title'],transferReports=transfer,transferConfirmed=True) if current['active'] and monitoring and transfer and aligned else None
        return wait(ready,30)


def stop():
    with sim._lock:
        old=status()
        if not old['active']:return dict(old,alreadyStopped=True)
        view,engine=frame()
        if Path(old['engine'])!=engine:raise ValueError('工程与模拟器不属于同一安装')
        if any(w['pid']==view['pid'] and w['kind']=='#32770' and w['visible'] for w in windows()):
            raise ValueError('工程已有对话框，停止仿真前保留现场')
        _,u=sim.api();u.PostMessageW(view['hwnd'],0x111,1677,0)
        return wait(lambda: dict(ok=True,active=False,physicalPlc=False) if not any(
            p['pid']==old['pid'] for p in sim.processes()) else None,20)
