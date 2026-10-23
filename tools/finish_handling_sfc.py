"""机械手工程的一次调用交付：验哈希、完整测试、显示确认、分段计时。"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def verify_native(project, report):
    status = report['nativeProjectCompile']
    if (not report.get('compiledReadback') or status.get('compileStatus') != 'accepted'
            or status.get('checkStatus') != 'accepted' or status.get('errors') != 0
            or status.get('warnings') != 0):
        raise ValueError('原生编译检查未完整通过')
    if hashlib.sha256(project.read_bytes()).hexdigest() != report['sha256']:
        raise ValueError('工程与原生核验结果不同，需重新验证')


def verify_tests(path):
    suites = ET.parse(path).getroot().iter('testsuite')
    counts = {k: 0 for k in ('tests', 'errors', 'failures', 'skipped')}
    for suite in suites:
        for key in counts: counts[key] += int(suite.get(key, 0))
    if not counts['tests'] or any(counts[k] for k in ('errors', 'failures', 'skipped')):
        raise ValueError('测试未完整通过：' + str(counts))
    return counts['tests']


def show_existing(project):
    """仅用于已验证的本机 GX Works2 1.620，不确认未知或保存对话框。"""
    u = ctypes.windll.user32
    cb_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    for name in ('GetWindowTextW', 'GetClassNameW'):
        getattr(u, name).argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    u.SendMessageTimeoutW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                     ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    def windows(parent=None):
        rows = []
        @cb_type
        def visit(h, _):
            t = ctypes.create_unicode_buffer(2048); c = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, t, 2048); u.GetClassNameW(h, c, 256)
            pid = ctypes.c_ulong(); u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            rows.append((h, c.value, t.value, pid.value, u.GetDlgCtrlID(h), bool(u.IsWindowVisible(h))))
            return True
        if parent is None: u.EnumWindows(visit, 0)
        else: u.EnumChildWindows(parent, visit, 0)
        return rows
    def wait_for(check):
        deadline = time.perf_counter() + 15
        while time.perf_counter() < deadline:
            value = check()
            if value: return value
            time.sleep(.05)
        raise TimeoutError('未确认目标就绪；保留当前窗口及对话框')
    frames = [r for r in windows() if r[1] == 'GXW2FrameWnd' and r[5]]
    if len(frames) != 1: raise ValueError('需要唯一的现有 GX Works2 窗口')
    frame, _, _, pid, _, _ = frames[0]
    def dialogs(): return [r for r in windows() if r[3] == pid and r[1] == '#32770' and r[5]]
    if dialogs(): raise ValueError('已有对话框，保留现场')
    u.PostMessageW(frame, 0x111, 1024, 0)
    def opening():
        ds = dialogs()
        if any(r[2] != '打开工程' for r in ds): raise ValueError('出现未知或保存对话框，停止')
        return ds[0][0] if len(ds) == 1 else None
    dialog = wait_for(opening)
    controls = windows(dialog)
    edit = [r[0] for r in controls if r[1] == 'Edit' and r[4] == 1148 and r[5]]
    button = [r[0] for r in controls if r[1] == 'Button' and r[4] == 1 and r[2].startswith('打开') and r[5]]
    if len(edit) != 1 or len(button) != 1: raise ValueError('打开对话框结构不匹配')
    value = ctypes.create_unicode_buffer(str(project)); result = ctypes.c_size_t()
    if not u.SendMessageTimeoutW(edit[0], 0xC, 0, ctypes.cast(value, ctypes.c_void_p), 2, 3000, ctypes.byref(result)):
        raise TimeoutError('文件名输入超时')
    # 跨进程编辑框必须发 WM_GETTEXT；GetWindowText 只能可靠读取窗口标题。
    observed = ctypes.create_unicode_buffer(2048)
    if not u.SendMessageTimeoutW(edit[0], 0xD, 2048, ctypes.cast(observed, ctypes.c_void_p), 2, 3000, ctypes.byref(result)):
        raise TimeoutError('文件名读回超时')
    if observed.value != str(project):
        raise ValueError('文件名输入核对失败')
    u.PostMessageW(button[0], 0xF5, 0, 0)
    def loaded():
        ds = dialogs()
        if any(r[2] != '打开工程' for r in ds): raise ValueError('加载时出现未知对话框，停止')
        titles = [r[2] for r in windows() if r[0] == frame]
        views = [r[2] for r in windows(frame) if '[PRG]' in r[2] and '步' in r[2] and r[5]]
        return views if not ds and titles and project.name in titles[0] and views else None
    return wait_for(loaded)


def finish(project, native_report):
    start = time.perf_counter(); project = Path(project).resolve()
    report = json.loads(Path(native_report).read_text(encoding='utf-8'))
    verify_native(project, report); checked = time.perf_counter()
    xml = project.parent/'交付测试.junit.xml'
    env = dict(os.environ, GXWORKS2_HANDLING_SFC=str(project))
    run = subprocess.run([sys.executable, '-m', 'pytest', 'tests/test_handling_sfc.py', '-q',
                          '--tb=short', '--junitxml', str(xml)], cwd=Path(__file__).resolve().parents[1],
                         env=env, capture_output=True, text=True, timeout=60)
    if run.returncode: raise RuntimeError(run.stdout + run.stderr)
    count = verify_tests(xml); tested = time.perf_counter()
    verify_native(project, report)
    views = show_existing(project); shown = time.perf_counter()
    verify_native(project, report); end = time.perf_counter()
    result = dict(ok=True, testsPassed=count, skippedTests=0, screenshots=0, views=views,
                  nativeHashCheckSeconds=round(checked-start, 3), testSeconds=round(tested-checked, 3),
                  openAndReadySeconds=round(shown-tested, 3), finalHashCheckSeconds=round(end-shown, 3),
                  totalSeconds=round(end-start, 3), physicalPlcVerified=False)
    (project.parent/'交付流程计时.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    print(json.dumps(finish(*sys.argv[1:3]), ensure_ascii=False))
