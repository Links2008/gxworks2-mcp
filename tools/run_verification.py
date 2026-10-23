"""一次执行预检、完整回归和隔离验收，失败即停并保存分段计时。"""
import argparse
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

from gxworks2_mcp import simulator as sim
from gxworks2_mcp.native_sfc import read_sfc_gxw
from tools.finish_handling_sfc import verify_native
from tools.verify_general_capabilities import cascade_steps, counter_steps, launch

ROOT = Path(__file__).resolve().parents[1]


def check_package(out):
    """一次完成构建、源码/资源逐字节核对与隔离导入，替代临时收尾脚本。"""
    import tomllib
    out.mkdir(parents=True,exist_ok=True)
    done=subprocess.run([sys.executable,'-X','utf8','-m','pip','wheel','.',
        '--no-deps','--no-build-isolation','--wheel-dir',str(out)],cwd=ROOT,
        capture_output=True,text=True,encoding='utf-8',timeout=120,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (out/'构建.log').write_text(done.stdout+done.stderr,encoding='utf-8')
    wheels=list(out.glob('*.whl'))
    if done.returncode or len(wheels)!=1:raise ValueError('软件包构建失败，已保留日志')
    wheel=wheels[0];source=ROOT/'src'/'gxworks2_mcp'
    config=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))
    expected=set(source.rglob('*.py'))
    for pattern in config['tool']['setuptools']['package-data']['gxworks2_mcp']:
        expected.update(p for p in source.glob(pattern) if p.is_file())
    with zipfile.ZipFile(wheel) as z:
        if z.testzip() is not None:raise ValueError('软件包CRC校验失败')
        names=set(z.namelist())
        for path in expected:
            name=path.relative_to(ROOT/'src').as_posix()
            if name not in names or z.read(name)!=path.read_bytes():raise ValueError('软件包源码/资源缺失或不一致：'+name)
        if any('..' in Path(n).parts or n.startswith(('/','\\')) or ':' in n or '\\' in n for n in names):
            raise ValueError('软件包包含不安全路径')
        isolated=out/'isolated';z.extractall(isolated)
    code="import sys;sys.path.insert(0,sys.argv[1]);import gxworks2_mcp.server"
    imported=subprocess.run([sys.executable,'-I','-X','utf8','-c',code,str(isolated)],cwd=out,
        capture_output=True,text=True,encoding='utf-8',timeout=30,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (out/'隔离导入.log').write_text(imported.stdout+imported.stderr,encoding='utf-8')
    if imported.returncode:raise ValueError('软件包隔离导入失败，已保留日志')
    return dict(path=str(wheel),sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
                checkedFiles=len(expected),sourceEqual=True,isolatedImport=True)


def preflight(project, report):
    """在任何软件启动前拒绝过期工程、损坏读回及无效输入组合。"""
    verify_native(project, json.loads(report.read_text(encoding='utf-8')))
    if not read_sfc_gxw(str(project))['sourceReadback']:
        raise ValueError('工程源码读回失败')
    sim.validate_verification(cascade_steps())
    sim.validate_verification(counter_steps())


def run(project, native_report, out, task_start_utc=None, package=False):
    started=time.perf_counter()
    task_start=datetime.fromisoformat(task_start_utc) if task_start_utc else datetime.now(timezone.utc)
    if task_start.tzinfo is None:raise ValueError('任务起始时间必须含时区')
    if out.exists() and any(out.iterdir()):raise ValueError('验收输出目录必须为空，禁止覆盖旧证据')
    out.mkdir(parents=True,exist_ok=True)
    result=dict(passed=False,stagesSeconds={},nativeCompileReused=False)
    stage='preflight';stage_started=time.perf_counter()
    try:
        print('[验收] 预检',file=sys.stderr,flush=True)
        preflight(project,native_report)
        result['nativeCompileReused']=True
        result['stagesSeconds'][stage]=round(time.perf_counter()-stage_started,3)
        stage='regression';stage_started=time.perf_counter()
        print('[验收] 全量回归',file=sys.stderr,flush=True)
        done=subprocess.run([sys.executable,'-X','utf8','-m','pytest','--tb=short',
            '--junitxml='+str(out/'回归.xml')],cwd=ROOT,capture_output=True,text=True,
            encoding='utf-8',timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (out/'回归.log').write_text(done.stdout+done.stderr,encoding='utf-8')
        if done.returncode:raise ValueError('回归失败，已保留日志，未启动GX')
        counts={key:sum(int(s.get(key,0)) for s in ET.parse(out/'回归.xml').iter('testsuite'))
                for key in ('tests','errors','failures','skipped')}
        if not counts['tests'] or counts['errors'] or counts['failures']:raise ValueError('回归结果不完整')
        result['regression']=counts
        result['stagesSeconds'][stage]=round(time.perf_counter()-stage_started,3)
        stage='background';stage_started=time.perf_counter()
        print('[验收] 隔离后台验证',file=sys.stderr,flush=True)
        background=launch(project,native_report,out/'真实验收')
        result['background']=background
        if not background['passed']:raise ValueError('真实验收失败，已保留诊断')
        verify_native(project,json.loads(native_report.read_text(encoding='utf-8')))
        if package:
            result['stagesSeconds'][stage]=round(time.perf_counter()-stage_started,3)
            stage='package';stage_started=time.perf_counter()
            print('[验收] 构建、源码与资源核对、隔离导入',file=sys.stderr,flush=True)
            result['package']=check_package(out/'wheel')
        result['passed']=True
    except Exception as exc:
        result.update(failedStage=stage,error=repr(exc))
    finally:
        result['stagesSeconds'][stage]=round(time.perf_counter()-stage_started,3)
        result['pipelineSeconds']=round(time.perf_counter()-started,3)
        result['taskStartUtc']=task_start.isoformat()
        result['recordedUtc']=datetime.now(timezone.utc).isoformat()
        result['totalTaskSeconds']=round((datetime.now(timezone.utc)-task_start).total_seconds(),3)
        (out/'流水线结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('project','native_report','out'):parser.add_argument(name,type=Path)
    parser.add_argument('--task-start-utc')
    parser.add_argument('--package',action='store_true',help='验收通过后构建并核对交付包（本机Python3.11+）')
    args=parser.parse_args()
    result=run(args.project.resolve(),args.native_report.resolve(),args.out.resolve(),args.task_start_utc,args.package)
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)
