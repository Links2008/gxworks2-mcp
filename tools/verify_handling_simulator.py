"""机械手真实仿真一次验收；保留原动作/保持时间，统一采样握手及故障证据。"""
import json
from pathlib import Path
import sys
import time
import subprocess
from gxworks2_mcp import simulator as sim, simulator_ui as ui
from tools.finish_handling_sfc import verify_native,show_existing

STATES=['S0']+[f'S{i}' for i in range(20,29)]
OUTPUTS=[f'Y{i}' for i in range(7)]
INITIAL={'X0':0,'X1':0,'X2':0,'X3':0,'X4':0,'X5':1,'X6':0,'X7':0,'X10':0,'X11':1,'X12':0}


class Acceptance:
    def __init__(self):
        self.started=time.perf_counter();self.events=[];self.timings={}

    def check(self,label,expected,timeout=3):
        deadline=time.perf_counter()+timeout
        while True:
            actual=sim.read_devices(list(expected))['values']
            if actual==expected:
                row=dict(label=label,seconds=round(time.perf_counter()-self.started,3),expected=expected,actual=actual)
                self.events.append(row);return row
            if time.perf_counter()>=deadline:raise AssertionError((label,expected,actual))
            time.sleep(.03)

    def state(self,label,state,outputs,**extra):
        expected={**{s:int(s==state) for s in STATES},**{y:int(y in outputs) for y in OUTPUTS},**extra}
        row=self.check(label,expected)
        print(label,flush=True)
        return row['seconds']

    def hold(self,label,seconds,expected):
        deadline=time.perf_counter()+seconds;reads=0
        while time.perf_counter()<deadline:
            actual=sim.read_devices(list(expected))['values'];reads+=1
            if actual!=expected:raise AssertionError((label,expected,actual))
            time.sleep(.06)
        self.events.append(dict(label=label,holdSeconds=seconds,samples=reads,expected=expected,actual=actual))

    def press(self,device,memory):
        # 输入松开必须已被该程序扫描，避免连续按键丢失上升沿。
        self.check(device+'按下前已释放',{device:0,memory:0})
        sim.pulse(device,120)
        self.check(device+'松开已扫描',{device:0,memory:0})

    def normal_cycle(self):
        self.state('01 原点待机','S0',[]);time.sleep(.8)
        self.press('X0','M90');self.state('02 启动松开后下降','S20',['Y0'])
        self.hold('下降输出保持',1.0,{'S20':1,'Y0':1})
        sim.write_inputs({'X5':0,'X4':1});begin=self.state('03 A台下位后夹紧','S21',['Y4'])
        end=self.state('04 夹紧计时后上升','S22',['Y1','Y4'])
        self.timings['clampSeconds']=round(end-begin,3)
        assert .65<=end-begin<=1.5,('夹紧时长',end-begin)
        self.hold('上升夹紧保持',.8,{'S22':1,'Y1':1,'Y4':1})
        sim.write_inputs({'X4':0,'X5':1});self.state('05 右移','S23',['Y2','Y4'])
        self.hold('右移输出保持',.8,{'S23':1,'Y2':1,'Y4':1})
        sim.write_inputs({'X5':0,'X11':0});self.hold('离开左限仍右移',.5,{'S23':1,'Y2':1,'Y4':1})
        sim.write_inputs({'X10':1,'X6':1,'X12':1});self.state('06 B台有料等待','S24',['Y4'])
        self.hold('B台占用两秒不下降',2.0,{'S24':1,'Y0':0,'Y4':1,'M500':0})
        sim.write_inputs({'X12':0});self.state('07 B台空闲后下降','S25',['Y0','Y4'])
        self.hold('B台下降夹紧保持',.8,{'S25':1,'Y0':1,'Y4':1})
        sim.write_inputs({'X6':0,'X7':1});begin=self.state('08 到下限松开','S26',['Y5'])
        end=self.state('09 松开计时后上升','S27',['Y1'])
        self.timings['releaseSeconds']=round(end-begin,3)
        assert .65<=end-begin<=1.5,('松开时长',end-begin)
        self.hold('放料后上升',.8,{'S27':1,'Y1':1,'Y4':0})
        sim.write_inputs({'X7':0,'X6':1});self.state('10 左移返回','S28',['Y3'])
        self.hold('左移输出保持',.8,{'S28':1,'Y3':1})
        sim.write_inputs({'X10':0,'X6':0});self.hold('离开右限仍左移',.4,{'S28':1,'Y3':1})
        sim.write_inputs({'X11':1,'X5':1});self.state('11 自动下一轮','S20',['Y0'])
        sim.write_inputs({'X1':1});self.state('12 下降暂停','S20',[],M0=0)
        self.hold('暂停后不自动启动',.6,{'S20':1,'M0':0,'Y0':0})

    def pause_resume(self):
        sim.write_inputs({'X1':0});self.press('X0','M90');self.state('13 续行','S20',['Y0'])
        sim.write_inputs({'X5':0,'X4':1});self.state('14 再次夹紧','S21',['Y4'])
        time.sleep(.2);sim.write_inputs({'X1':1})
        self.state('15 夹紧暂停许可已归零','S21',['Y4'],M0=0)
        count=sim.read_devices(['TN250'])['values']['TN250'];self.timings['pausedTimerCount']=count
        self.hold('暂停1.3秒夹紧计时均保持',1.3,{'S21':1,'Y4':1,'M0':0,'TN250':count})
        sim.write_inputs({'X1':0});self.hold('松开停止按钮不自行续行',.3,{'S21':1,'Y4':1,'M0':0,'TN250':count})
        self.press('X0','M90');self.state('16 续行完成夹紧计时','S22',['Y1','Y4'])
        sim.write_inputs({'X1':1});self.state('17 上升暂停夹紧保持','S22',['Y4'],M0=0)

    def protection(self):
        ui.cpu_control('stop');sim.write_inputs({**INITIAL,'X4':1,'X5':0});ui.cpu_control('run')
        self.check('非原点待机',{'S0':1,'M0':0,'Y0':0,'M10':0})
        self.press('X0','M90');self.state('非原点拒绝启动','S0',[],M0=0)
        sim.write_inputs({'X4':0,'X5':1});self.check('原点许可到位',{'M10':1,'M500':0})
        self.press('X0','M90');self.state('原点允许启动','S20',['Y0'])
        sim.write_inputs({'X2':1});self.state('急停切断动作锁存报警','S20',['Y6'],M500=1,M0=0)
        sim.write_inputs({'X2':0});self.press('X0','M90');self.state('解除急停仍锁机','S20',['Y6'],M500=1,M0=0)
        self.press('X3','M91');self.state('复位回待机不自行启动','S0',[],M500=0,M0=0)
        sim.write_inputs({'X4':1});self.state('上下限矛盾锁机','S0',['Y6'],M500=1)
        sim.write_inputs({'X4':0});self.check('矛盾消失',{'M3':0})
        self.press('X3','M91');self.state('矛盾消除后人工复位','S0',[],M500=0,M0=0)


def run(project,native_report,directory):
    started=time.perf_counter();project=Path(project).resolve();out=Path(directory).resolve();out.mkdir(parents=True,exist_ok=True)
    report=json.loads(Path(native_report).read_text(encoding='utf-8'));verify_native(project,report)
    if not any(p['name'].lower()=='gd2.exe' for p in sim.processes()):
        from gxworks2_mcp.ui import find_install
        installation=find_install()
        if not installation['found']:raise ValueError('未找到 GX Works2 安装')
        startup=subprocess.STARTUPINFO();startup.dwFlags=subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow=4  # SW_SHOWNOACTIVATE：启动窗口时不切换前台。
        subprocess.Popen([installation['executable'],str(project)],creationflags=subprocess.CREATE_NO_WINDOW,startupinfo=startup)
        deadline=time.perf_counter()+30
        while True:
            try:frame,_=ui.frame();break
            except ValueError:
                if time.perf_counter()>=deadline:raise
                time.sleep(.1)
    else:frame,_=ui.frame()
    if project.name not in frame['title']:
        if ui.status()['active']:raise ValueError('另一个工程正在仿真，保留其状态')
        show_existing(project);frame,_=ui.frame()
    ui.start()  # 进程存在仍可能尚在传输，须完成传输握手后才能操作CPU。
    # 同一套已就绪的监视窗口直接复用，不输入地址、不重新排列或逐步截图。
    runner=Acceptance();result=dict(passed=False,source='real GX Simulator2',physicalPlc=False,screenshots=0)
    try:
        ui.cpu_control('stop');sim.write_inputs(INITIAL);ui.cpu_control('run')
        tests_started=time.perf_counter()
        runner.normal_cycle();runner.pause_resume();runner.protection()
        verify_native(project,report)
        result.update(passed=True,judgment='已测完整动作、保持条件、暂停续行及保护逻辑通过',testsSeconds=round(time.perf_counter()-tests_started,3))
    except Exception as exc:
        result['failure']=repr(exc)
    finally:
        try:result['cpuFinal']=ui.cpu_control('stop')
        except Exception as exc:result.update(passed=False,stopError=repr(exc))
        result.update(totalSeconds=round(time.perf_counter()-started,3),checks=len(runner.events),timings=runner.timings,events=runner.events)
        (out/'真实仿真验收.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    result=run(*sys.argv[1:4]);print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)
