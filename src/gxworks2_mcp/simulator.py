"""仅连接已核对的本地 FX Simulator2；不使用 MC、串口或实体 PLC 接口。"""
from contextlib import contextmanager
import ctypes as C
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import re
import struct
import threading
import time

ENGINE_SHA256 = '4b401141a67a36c97b53bfd181b54255e0d320fa774cec7f151df55970658711'
MASTER_ADDRESS = 0x51dfac
_lock = threading.RLock()


def location(device):
    """FX 的 X/Y 为八进制；T 为触点，TN 为当前计数值。"""
    if not isinstance(device, str): raise ValueError('软元件必须是字符串')
    match = re.fullmatch(r'(TN|CN|X|Y|M|S|T|C|D)([0-9]+)', device.upper())
    if not match: raise ValueError('仅支持 X/Y/M/S/T/TN/C/CN/D')
    kind, digits = match.groups()
    index = int(digits, 8 if kind in ('X','Y') else 10)
    limits = {'X':256,'Y':256,'M':7680,'S':4096,'T':512,'TN':512,'C':256,'CN':256,'D':8000}
    if kind == 'M' and 8000 <= index < 8512: return 0x7e0 + (index-8000)//16*2, (index-8000)%16
    if index >= limits[kind]: raise ValueError('超出已验证的模拟器软元件范围')
    if kind=='CN' and index>=200:return 0x17d0+(index-200)*4,None
    base = {'X':0,'Y':0x20,'M':0x60,'S':0x860,'T':0xa80,'TN':0xb40,'C':0x1600,'CN':0x1640,'D':0x2000}[kind]
    return (base + index*2, None) if kind in ('TN','CN','D') else (base + index//16*2, index%16)


def read_spec(device, dtype=None):
    offset,bit=location(device)
    wide=device.upper().startswith('CN') and int(device[2:])>=200
    dtype=('bit' if bit is not None else 'int32' if wide else 'uint16') if dtype is None else dtype
    if not isinstance(dtype,str):raise ValueError('数据类型必须是字符串')
    formats={'uint16':(2,False),'int16':(2,True),'uint32':(4,False),'int32':(4,True)}
    if bit is not None:
        if dtype!='bit':raise ValueError('位软元件只能使用 bit 类型')
        return offset,bit,2,False
    if dtype not in formats:raise ValueError('数据类型须为 bit/uint16/int16/uint32/int32')
    size,signed=formats[dtype]
    if wide and size!=4:raise ValueError('C200..C255 当前值必须按32位读取')
    if size==4 and not wide:
        if not device.upper().startswith('D') or int(device[1:])>=7999:
            raise ValueError('连续32位读取需要 D0..D7998')
    return offset,bit,size,signed


def validate_inputs(values):
    if not isinstance(values, dict) or not 1 <= len(values) <= 256: raise ValueError('输入数量须为1..256')
    seen = set()
    for device, value in values.items():
        point = location(device)
        if not device.upper().startswith('X') or type(value) is not int or value not in (0,1):
            raise ValueError('只允许向虚拟 X 输入写入整数0或1')
        if point in seen: raise ValueError('同一输入地址重复')
        seen.add(point)


@lru_cache(maxsize=1)
def api():
    if os.name != 'nt': raise OSError('GX Simulator2 需要 Windows')
    k = C.WinDLL('kernel32', use_last_error=True); u = C.WinDLL('user32', use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes=[C.c_uint,C.c_uint]; k.CreateToolhelp32Snapshot.restype=C.c_void_p
    k.Process32FirstW.argtypes=k.Process32NextW.argtypes=[C.c_void_p,C.c_void_p]
    k.OpenProcess.argtypes=[C.c_uint,C.c_bool,C.c_uint]; k.OpenProcess.restype=C.c_void_p
    k.QueryFullProcessImageNameW.argtypes=[C.c_void_p,C.c_uint,C.c_wchar_p,C.POINTER(C.c_uint)]
    k.CloseHandle.argtypes=[C.c_void_p]
    u.EnumWindows.argtypes=[C.c_void_p,C.c_ssize_t]
    u.EnumChildWindows.argtypes=[C.c_void_p,C.c_void_p,C.c_ssize_t]
    for name in ('ReadProcessMemory','WriteProcessMemory'):
        getattr(k,name).argtypes=[C.c_void_p,C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)]
    u.GetWindowTextW.argtypes=u.GetClassNameW.argtypes=[C.c_void_p,C.c_wchar_p,C.c_int]
    u.GetWindowThreadProcessId.argtypes=[C.c_void_p,C.POINTER(C.c_uint)]
    u.GetDlgCtrlID.argtypes=u.IsWindowVisible.argtypes=u.IsWindowEnabled.argtypes=[C.c_void_p]
    u.PostMessageW.argtypes=[C.c_void_p,C.c_uint,C.c_size_t,C.c_ssize_t]
    u.SendMessageTimeoutW.argtypes=[C.c_void_p,C.c_uint,C.c_size_t,C.c_void_p,C.c_uint,C.c_uint,C.c_void_p]
    return k,u


def processes():
    k,_=api()
    class Entry(C.Structure):
        _fields_=[('size',C.c_uint),('usage',C.c_uint),('pid',C.c_uint),('heap',C.c_size_t),
                  ('module',C.c_uint),('threads',C.c_uint),('parent',C.c_uint),('priority',C.c_long),
                  ('flags',C.c_uint),('name',C.c_wchar*260)]
    snap=k.CreateToolhelp32Snapshot(2,0)
    if snap in (None,C.c_void_p(-1).value): raise C.WinError(C.get_last_error())
    row=Entry(); row.size=C.sizeof(row); result=[]
    try:
        valid=k.Process32FirstW(snap,C.byref(row))
        while valid:
            if row.name.lower() in ('fxsimrun2.exe','simmanager.exe','gd2.exe'):
                h=k.OpenProcess(0x1000,False,row.pid)
                if not h: raise C.WinError(C.get_last_error())
                try:
                    path=C.create_unicode_buffer(32768); size=C.c_uint(len(path))
                    if not k.QueryFullProcessImageNameW(h,0,path,C.byref(size)): raise C.WinError(C.get_last_error())
                    result.append(dict(pid=row.pid,name=row.name,path=path.value))
                finally:k.CloseHandle(h)
            valid=k.Process32NextW(snap,C.byref(row))
    finally:k.CloseHandle(snap)
    return result


def target():
    candidates=[p for p in processes() if p['name'].lower()=='fxsimrun2.exe']
    if len(candidates)!=1: raise ValueError('需要唯一的 FX Simulator2；未启动或存在多个实例')
    process=candidates[0]
    if hashlib.sha256(Path(process['path']).read_bytes()).hexdigest()!=ENGINE_SHA256:
        raise ValueError('模拟器版本未经验证，拒绝按旧地址读写')
    return process


@contextmanager
def memory(write=False):
    k,_=api(); process=target(); h=k.OpenProcess(0x1010 | (0x28 if write else 0),False,process['pid'])
    if not h: raise C.WinError(C.get_last_error())
    def read(address, size):
        buf=C.create_string_buffer(size); count=C.c_size_t()
        if not k.ReadProcessMemory(h,address,buf,size,C.byref(count)) or count.value!=size:
            raise OSError('模拟器读取失败；不使用缓存或零值代替')
        return buf.raw
    def put(address, data):
        buf=C.create_string_buffer(data); count=C.c_size_t()
        if not k.WriteProcessMemory(h,address,buf,len(data),C.byref(count)) or count.value!=len(data):
            raise OSError('虚拟输入写入失败')
    try:
        live_path=C.create_unicode_buffer(32768); size=C.c_uint(len(live_path))
        if not k.QueryFullProcessImageNameW(h,0,live_path,C.byref(size)) or Path(live_path.value)!=Path(process['path']):
            raise ValueError('模拟器进程身份已变化')
        base=struct.unpack('<I',read(MASTER_ADDRESS,4))[0]
        if not 0x10000 <= base < 0x80000000: raise ValueError('模拟器数据区尚未就绪')
        yield process,base,read,put
    finally:k.CloseHandle(h)


def read_devices(devices, types=None):
    if not isinstance(devices,list) or not 1 <= len(devices) <= 256: raise ValueError('读取数量须为1..256')
    if types is not None and (not isinstance(types,dict) or set(types)-set(devices)):
        raise ValueError('types 只可指定本批读取的地址')
    points=[read_spec(d,(types or {}).get(d)) for d in devices]
    with _lock, memory() as (process,base,read,_):
        raw={(offset,size):read(base+offset,size) for offset,_,size,_ in points}
        values={d:(int.from_bytes(raw[offset,size],'little',signed=signed) if bit is None
                   else (int.from_bytes(raw[offset,size],'little')>>bit)&1)
                for d,(offset,bit,size,signed) in zip(devices,points)}
    return dict(ok=True,source='GX Simulator2 / FXSimRun2',pid=process['pid'],values=values,physicalPlc=False)


def write_inputs(values):
    validate_inputs(values)
    with _lock, memory(True) as (process,base,read,put):
        groups={}
        for device,value in values.items():
            offset,bit=location(device); groups.setdefault(offset,[]).append((bit,value))
        for offset,bits in groups.items():
            word=int.from_bytes(read(base+offset,2),'little')
            for bit,value in bits: word=(word | (1<<bit)) if value else (word & ~(1<<bit))
            put(base+offset,word.to_bytes(2,'little'))
        observed={}
        for device in values:
            offset,bit=location(device); observed[device]=(int.from_bytes(read(base+offset,2),'little')>>bit)&1
        if observed!=values: raise ValueError('虚拟输入读回不一致')
    return dict(ok=True,pid=process['pid'],values=observed,physicalPlc=False)


def pulse(device, duration_ms=100):
    validate_inputs({device:1})
    if type(duration_ms) is not int or not 20 <= duration_ms <= 2000: raise ValueError('按键时间须为20..2000ms')
    with _lock, memory(True) as (process,base,read,put):
        offset,bit=location(device);mask=1<<bit
        if int.from_bytes(read(base+offset,2),'little') & mask:
            raise ValueError('输入已按下，请先释放，避免伪造上升沿')
        def set_bit(pressed):
            if int.from_bytes(read(MASTER_ADDRESS,4),'little')!=base:
                raise ValueError('模拟器数据区已变化，拒绝写入旧地址')
            word=int.from_bytes(read(base+offset,2),'little')
            put(base+offset,((word|mask) if pressed else (word&~mask)).to_bytes(2,'little'))
            if bool(int.from_bytes(read(base+offset,2),'little')&mask)!=pressed:
                raise ValueError('模拟按键读回不一致')
        try:
            set_bit(True); time.sleep(duration_ms/1000)
        finally:
            set_bit(False)
    return dict(ok=True,pid=process['pid'],device=device,released=True,physicalPlc=False)


def validate_verification(steps, forbid=None, sample_ms=20):
    """只校验验收参数，不连接或写入模拟器。"""
    if not isinstance(steps,list) or not 1 <= len(steps) <= 64: raise ValueError('验证步骤须为1..64条')
    if type(sample_ms) is not int or not 5<=sample_ms<=200:raise ValueError('采样间隔须为5..200ms')
    forbid=[] if forbid is None else forbid
    if not isinstance(forbid,list) or len(forbid)>32:raise ValueError('禁止组合须为至多32个对象')
    def validate_expected(expected,types=None):
        if not isinstance(expected,dict) or not 1<=len(expected)<=256:raise ValueError('每步必须有期望值')
        if types is not None and (not isinstance(types,dict) or set(types)-set(expected)):
            raise ValueError('types 只可指定该步骤的期望地址')
        for d,v in expected.items():
            _,bit,size,signed=read_spec(d,(types or {}).get(d))
            low=-(1<<(size*8-1)) if signed else 0
            high=(1<<(size*8-(1 if signed else 0)))-1 if bit is None else 1
            if type(v) is not int or not low<=v<=high:raise ValueError('期望值超界')
    for pattern in forbid:validate_expected(pattern)
    budget=0
    for step in steps:
        if not isinstance(step,dict) or set(step)-{'inputs','expect','timeout_ms','min_delay_ms','hold_ms','types','label'}:
            raise ValueError('验证步骤字段无效')
        if 'inputs' in step and step['inputs']!={}:validate_inputs(step['inputs'])
        validate_expected(step.get('expect'),step.get('types'))
        timeout=step.get('timeout_ms',2000)
        if type(timeout) is not int or not 50 <= timeout <= 5000: raise ValueError('每步等待须为50..5000ms')
        minimum,hold=step.get('min_delay_ms',0),step.get('hold_ms',0)
        if type(minimum) is not int or not 0<=minimum<=timeout:raise ValueError('最早响应时间须为0..timeout_ms')
        if type(hold) is not int or not 0<=hold<=10000:raise ValueError('保持时间须为0..10000ms')
        addresses=set(step['expect'])|{d for p in forbid for d in p}
        if len(addresses)>256:raise ValueError('单次采样地址不能超过256')
        if any(d in (step.get('types') or {}) for p in forbid for d in p):
            raise ValueError('禁止组合与带类型地址不能重叠，避免类型歧义')
        if 'label' in step and (not isinstance(step['label'],str) or len(step['label'])>120):
            raise ValueError('label 须为至多120字的字符串')
        budget+=timeout+hold
    if budget>60000: raise ValueError('验证等待预算不能超过60秒')
    return forbid


def verify(steps, forbid=None, sample_ms=20):
    """按采样验证时间窗与持续保持；失败后不执行下一动作。"""
    forbid=validate_verification(steps,forbid,sample_ms)
    with _lock:
        pid=target()['pid']; trace=[]; started=time.perf_counter()
        timeline=[];last_sample=None;max_gap=0;sample_count=0;truncated=False
        for index,step in enumerate(steps):
            if target()['pid']!=pid: raise ValueError('模拟器实例改变，验证已停止')
            if step.get('inputs'):
                written=write_inputs(step['inputs'])
                if written and written.get('pid',pid)!=pid:raise ValueError('输入写入实例已改变')
            begin=time.perf_counter();matched_at=None;reason='timeout';passed=False;samples=0
            expected=step['expect'];types=step.get('types')
            addresses=list(dict.fromkeys([*expected,*(d for p in forbid for d in p)]))
            while True:
                actual=read_devices(addresses,types=types) if types else read_devices(addresses)
                if actual['pid']!=pid: raise ValueError('模拟器实例改变，验证已停止')
                now=time.perf_counter();elapsed=(now-begin)*1000;samples+=1;sample_count+=1
                if last_sample is not None:max_gap=max(max_gap,(now-last_sample)*1000)
                last_sample=now;values=actual['values']
                if not timeline or timeline[-1]['values']!=values:
                    timeline.append(dict(step=index,atMs=round((now-started)*1000,2),values=values))
                    if len(timeline)>256:timeline.pop(0);truncated=True
                blocked=next((p for p in forbid if all(values[d]==v for d,v in p.items())),None)
                equal=all(values[d]==v for d,v in expected.items())
                if blocked is not None:reason='forbidden_combination';break
                if matched_at is None:
                    if elapsed>step.get('timeout_ms',2000):break
                    if equal:
                        if elapsed+0.001<step.get('min_delay_ms',0):reason='too_early';break
                        matched_at=now
                elif not equal:reason='hold_broken';break
                if matched_at is not None and (now-matched_at)*1000+0.001>=step.get('hold_ms',0):
                    passed=True;reason='passed';break
                time.sleep(sample_ms/1000)
            trace.append(dict(step=index,label=step.get('label',''),passed=passed,reason=reason,
                              expected=expected,actual=values,samples=samples,
                              matchedAtMs=None if matched_at is None else round((matched_at-begin)*1000,2),
                              elapsedMs=round((now-begin)*1000,2),forbidden=blocked))
            if not passed:break
    return dict(ok=all(t['passed'] for t in trace),pid=pid,trace=trace,
                elapsedMs=round((time.perf_counter()-started)*1000,2),source='real GX Simulator2',physicalPlc=False,
                timeline=timeline,timelineTruncated=truncated,samples=sample_count,sampleIntervalMs=sample_ms,
                maxSampleGapMs=round(max_gap,2),coverage='sampled; sub-sample changes are not proven absent')
