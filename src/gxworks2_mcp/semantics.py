"""按写入方式及 STL 上下文区分风险；静态信息不冒充路径互斥证明。"""
import re
from collections import defaultdict
from .devices import parse_device, DeviceError


def _name(value):
    try:return str(parse_device(value))
    except DeviceError:return value.upper()


def coil_writes(program):
    state = None
    writes = defaultdict(list)
    for line, ins in enumerate(program.instructions, 1):
        if ins.mnemonic == 'STL':
            state = _name(ins.operands[0])
        elif ins.mnemonic in {'RET', 'FEND', 'END'}:
            state = None
        if ins.is_output and ins.operands:
            device = _name(ins.operands[0])
            writes[device].append(dict(line=line, op=ins.mnemonic, state=state))
    return dict(writes)


def write_kind(device, sites, *, fx=False):
    ops = {s['op'] for s in sites}
    if fx and re.fullmatch(r'S\d+', device) and ops <= {'OUT', 'SET', 'RST'}:
        return 'state_control'
    if ops <= {'SET', 'RST'}:
        return 'latch'
    if re.fullmatch(r'(?:TN?|CN?)\d+', device) and ops <= {'OUT', 'RST'}:
        drivers=[s for s in sites if s['op']=='OUT']
        contexts=[s['state'] for s in drivers]
        if len(drivers)<=1 or fx and None not in contexts and len(set(contexts))==len(contexts):
            return 'timer_counter_reset'
    contexts = [s['state'] for s in sites]
    if fx and ops == {'OUT'} and None not in contexts and len(set(contexts)) == len(contexts):
        return 'stl_state_output'
    return 'multiple_writes' if len(sites) > 1 else 'single_write'


def semantic_issues(program, *, fx=False):
    result = []
    writes = coil_writes(program)
    for device, sites in writes.items():
        kind = write_kind(device, sites, fx=fx)
        if len(sites) > 1:
            code = 'DOUBLE_COIL' if kind == 'multiple_writes' else 'WRITE_' + kind.upper()
            message = ('同一输出存在多处驱动，需核对扫描顺序及覆盖关系' if kind == 'multiple_writes'
                       else {'latch': '置位/复位写入：同时满足时按执行顺序决定最终状态',
                             'state_control': '步骤初始化、转移或复位；需结合状态流分析',
                             'timer_counter_reset': '定时器/计数器驱动与复位；需核对复位条件',
                             'stl_state_output': '不同 STL 状态复用输出；未证明并发路径或输出互锁'}[kind])
            result.append(dict(severity='warning' if code == 'DOUBLE_COIL' else 'info', code=code,
                               message=message, device=device, line=sites[0]['line']))
        if re.fullmatch(r'[MY]\d+', device) and 'SET' in {s['op'] for s in sites} and not any(s['op']=='RST' for s in sites):
            result.append(dict(severity='info', code='LATCH_RESET_NOT_FOUND', device=device,
                               message='本程序内未找到显式 RST；请核对保持及外部复位约定'))
    if not fx:
        return result
    states = [_name(ins.operands[0]) for ins in program if ins.mnemonic == 'STL']
    for state in set(states):
        if states.count(state) > 1:
            result.append(dict(severity='warning', code='STL_STATE_DUPLICATE', device=state,
                               message='同一状态在本程序中出现多个 STL 定义'))
    timers = defaultdict(set)
    for device, sites in writes.items():
        if re.fullmatch(r'TN?\d+', device):
            for site in sites:
                if site['op']=='OUT' and site['state']:
                    timers[site['state']].add(device)
    for target, sites in writes.items():
        if not re.fullmatch(r'S\d+', target):
            continue
        for site in sites:
            source = site['state']
            if source and site['op'] in {'OUT','SET'}:
                if target not in states:
                    result.append(dict(severity='warning', code='STL_TARGET_UNDEFINED', device=target,
                                       line=site['line'], message='本程序未定义该转移目标；如为跨程序状态需另行核对'))
                for timer in timers[source] & timers[target]:
                    result.append(dict(severity='warning', code='STL_TIMER_REUSE', device=timer,
                                       line=site['line'], message=f'{source} → {target} 相邻步骤复用定时器，转移时可能不清零'))
    return result
