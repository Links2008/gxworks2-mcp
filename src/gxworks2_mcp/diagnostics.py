"""解释所选程序的位条件及整数比较；不扫描执行、不写入或推断硬件故障。"""
import operator
import re
from .analysis import where_used
from .author import DATA_ACTIONS, data_operand
from .blocks import Contact, Invert, Parallel, build_rungs
from .il import CONTACT_START, CONTACT_SERIES, CONTACT_PARALLEL, BLOCK_JOIN, STACK_OPS, OUTPUTS, STRUCTURAL
from .simulator import read_spec

_COMPARE={'=':operator.eq,'<>':operator.ne,'>':operator.gt,'>=':operator.ge,'<':operator.lt,'<=':operator.le}
_OPPOSITE={'=':'<>','<>':'=','>':'<=','>=':'<','<':'>=','<=':'>'}


def _data_value(operand,bits,values,points):
    operand=data_operand(operand.upper(),bits)
    if operand.startswith('K'):return int(operand[1:])
    if operand.startswith('H'):raw=int(operand[1:],16)
    else:
        kind,index=operand[0],int(operand[1:])
        if kind=='C' and index>=200 and bits!=32:
            raise ValueError('C200..C255须使用32位比较')
        names=[f'{kind}{index}'] if kind=='D' else [f'{kind}N{index}']
        if kind=='D' and bits==32:names.append(f'D{index+1}')
        points.update(names)
        if any(name not in values for name in names):return None
        raw=values[names[0]]
        if len(names)==2:raw|=values[names[1]]<<16
    # JY997D16601R §28.1：最高位为1时按负数比较。
    raw&=(1<<bits)-1
    return raw-(1<<bits) if raw&(1<<(bits-1)) else raw


def _point(name):
    spec=read_spec(name)
    kind,digits=re.fullmatch(r'(TN|CN|X|Y|M|S|T|C|D)(\d+)',name.upper()).groups()
    index=int(digits,8 if kind in {'X','Y'} else 10)
    return kind+(format(index,'o') if kind in {'X','Y'} else str(index)),spec


def _unmet(tree, wanted=True):
    if tree['value'] is wanted:return None
    if tree['kind']=='not':return _unmet(tree['children'][0],not wanted)
    if tree['kind'] in {'all','any'}:
        join=tree['kind'] if wanted else ('any' if tree['kind']=='all' else 'all')
        return dict(join=join,children=[r for c in tree['children'] if (r:=_unmet(c,wanted)) is not None])
    result=dict(tree)
    if tree['kind']=='contact':result['required']=int(wanted != tree['negated'])
    if tree['kind']=='comparison':
        result['requiredResult']=wanted
        result['requiredComparison']=tree['operator'] if wanted else _OPPOSITE[tree['operator']]
    return result


def diagnose_output(program, device, snapshot, comments=None):
    """只解释Y/M目标的驱动条件；缺失数据与不支持的语义保留为未知。"""
    target,spec=_point(device)
    if target[0] not in {'Y','M'} or spec[1] is None:raise ValueError('诊断目标目前限Y/M位输出')
    if len(program)>8192:raise ValueError('诊断程序须不超过8192条指令')
    if not isinstance(snapshot,dict) or len(snapshot)>256:raise ValueError('快照须为至多256个地址的对象')
    values={}
    for name,value in snapshot.items():
        key,(_,bit,size,signed)=_point(name)
        low=-(1<<(size*8-1)) if signed else 0
        high=1 if bit is not None else (1<<(size*8-(1 if signed else 0)))-1
        if key in values or type(value) is not int or not low<=value<=high:
            raise ValueError('快照包含重复地址、非整数或越界值')
        values[key]=value
    points={target,'M8000'};visited=0

    def evaluate(node,depth=0):
        nonlocal visited
        visited+=1
        if visited>4096:raise ValueError('诊断条件节点超过4096个')
        if depth>32:return dict(kind='unknown',value=None,reason='条件深度超过32层')
        if node is None:return dict(kind='constant',value=True)
        if isinstance(node,Contact):
            comparison=re.fullmatch(r'(\S+)\s+(<>|<=|>=|=|<|>)\s+(\S+)',node.device)
            if comparison:
                left,op,right=comparison.groups()
                try:
                    a=_data_value(left,node.bits,values,points)
                    b=_data_value(right,node.bits,values,points)
                except ValueError as exc:
                    return dict(kind='unknown',value=None,expression=node.device,reason=str(exc))
                known=a is not None and b is not None
                return dict(kind='comparison',expression=node.device,bits=node.bits,operator=op,
                    leftOperand=left,rightOperand=right,leftActual=a,rightActual=b,
                    value=_COMPARE[op](a,b) if known else None,
                    reason='快照数值比较' if known else '缺少比较操作数采样值')
            try:
                point,meta=_point(node.device)
                if meta[1] is None:raise ValueError('位条件不支持字值')
            except ValueError:
                return dict(kind='unknown',value=None,expression=node.device,reason='该触点类型暂不求值')
            points.add(point);actual=values.get(point)
            return dict(kind='contact',device=point,label=(comments or {}).get(point,'')[:200],
                actual=actual,negated=node.negated,edge=node.edge,
                value=None if actual is None or node.edge else bool(actual) != node.negated,
                reason='需要前一扫描的边沿信息' if node.edge else '缺少采样值' if actual is None else '快照触点')
        if isinstance(node,Invert):
            child=evaluate(node.child,depth+1)
            return dict(kind='not',value=None if child['value'] is None else not child['value'],children=[child])
        children=[evaluate(c,depth+1) for c in node.children]
        states=[c['value'] for c in children];parallel=isinstance(node,Parallel)
        value=(True if True in states else None if None in states else False) if parallel else (
            False if False in states else None if None in states else True)
        return dict(kind='any' if parallel else 'all',value=value,children=children)

    by_instruction={id(r.instruction):r for r in build_rungs(program,max_blocks=8)}
    indices={s['rung'] for s in where_used(program).get(target,[]) if 'W' in s['kind']}
    if len(indices)>32:raise ValueError('同一目标写入点超过32处，请先缩小程序范围')
    writers=[]
    for index in sorted(indices):
        instruction=program.instructions[index];rung=by_instruction.get(id(instruction))
        if rung is None:continue
        tree=evaluate(rung.logic)
        writers.append(dict(line=index+1,instruction=str(instruction),mnemonic=instruction.mnemonic,
            comment=(instruction.comment or '')[:200],condition=tree['value'],evidence=tree,unmet=_unmet(tree)))
    if len(points)>256:raise ValueError('所需采样地址超过256个')
    allowed=CONTACT_START|CONTACT_SERIES|CONTACT_PARALLEL|BLOCK_JOIN|STACK_OPS|OUTPUTS|STRUCTURAL|set(DATA_ACTIONS)|{'STL'}
    unsupported=sorted({i.mnemonic for i in program if i.mnemonic not in allowed})
    actual=values.get(target)
    if values.get('M8000')==0:status='cpu_stopped'
    elif actual==1:status='active'
    elif not writers:status='no_writer'
    elif actual is None:status='unknown'
    elif unsupported:status='unsupported_context'
    elif any(w['mnemonic']!='OUT' for w in writers):status='history_required'
    elif len(writers)>1:status='multiple_writers'
    elif writers[0]['condition'] is None:status='unknown'
    elif writers[0]['condition'] is False:status='blocked'
    else:status='condition_disagrees'
    messages={
        'cpu_stopped':'快照显示CPU停止，需先核对运行状态。',
        'active':'采样逻辑输出为1；机械是否到位仍需现场反馈。',
        'no_writer':'当前支持的交叉引用中未找到该目标的写入，请核对程序范围与外部映射。',
        'unknown':'采样缺失或条件需要历史/当前未支持的语义，不能确定阻断原因。',
        'unsupported_context':'程序含当前未解释的控制流，不能认定该回路已执行。',
        'history_required':'存在锁存、脉冲或数据指令写入，需结合执行历史判定。',
        'multiple_writers':'目标有多处驱动，需结合扫描顺序与活动步骤判定。',
        'blocked':'所选程序与快照对应时，驱动条件未满足；all表示全部，any表示任选一路。',
        'condition_disagrees':'快照中条件满足而输出为0，需核对程序版本、扫描时序及输出控制。'}
    return dict(ok=True,readOnly=True,physicalPlc=False,device=target,actual=actual,status=status,
        summary=messages[status],writers=writers,requiredDevices=sorted(points),
        missingDevices=sorted(points-values.keys()),unsupportedInstructions=unsupported,
        programMatchVerified=False,limitations=['单次非原子采样，不能排除采样间瞬变。',
        '未自动核对所选程序与模拟器实际程序版本。','不从逻辑快照认定硬件或机械故障。'])
