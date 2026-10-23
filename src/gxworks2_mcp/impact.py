"""比较已存程序的静态影响候选；不执行逻辑、不操作软件或PLC。"""
from collections import defaultdict, deque
import hashlib
import re
from .analysis import _leaf_devices, where_used
from .author import DATA_ACTIONS
from .blocks import build_rungs, contact_for
from .devices import DeviceError, parse_device
from .il import CONTACT_START, CONTACT_SERIES, CONTACT_PARALLEL, BLOCK_JOIN, STACK_OPS, OUTPUTS, render_il
from .store import diff_programs


def _model(program):
    reads=defaultdict(set);writes=defaultdict(set)
    for device,sites in where_used(program).items():
        for site in sites:
            if 'R' in site['kind']:reads[site['rung']].add(device)
            if 'W' in site['kind']:writes[site['rung']].add(device)
    contact_reads=defaultdict(set)
    for index,instruction in enumerate(program):
        if instruction.is_contact:contact_reads[contact_for(instruction)].update(reads[index])
    signatures=defaultdict(list);graph=defaultdict(set);details=defaultdict(list)
    positions={id(ins):i for i,ins in enumerate(program)}
    for rung in build_rungs(program,max_blocks=8):
        index=positions.get(id(rung.instruction))
        if index is None or not writes[index]:continue
        dependencies=set(reads[index])
        for leaf in _leaf_devices(rung.logic):
            if leaf in contact_reads:dependencies.update(contact_reads[leaf])
            else:
                try:dependencies.add(str(parse_device(leaf.device)))
                except DeviceError:pass
        signature=(str(rung.instruction),str(rung.logic),tuple(sorted(dependencies)),
                   tuple((str(c),c.bits) for c in _leaf_devices(rung.logic)))
        for device in writes[index]:
            signatures[device].append(signature)
            details[device].append(dict(line=index+1,instruction=str(rung.instruction),logic=str(rung.logic)))
        for source in dependencies:graph[source].update(writes[index])
    allowed=CONTACT_START|CONTACT_SERIES|CONTACT_PARALLEL|BLOCK_JOIN|STACK_OPS|OUTPUTS|set(DATA_ACTIONS)|{
        'STL','RET','END','FEND','NOP','NOPLF'}
    unsupported=set();unresolved=set()
    for ins in program:
        base=ins.mnemonic[:-1] if ins.mnemonic.endswith('P') else ins.mnemonic
        if ins.mnemonic not in allowed and base not in DATA_ACTIONS:unsupported.add(ins.mnemonic)
        for value in ins.operands:
            if re.fullmatch(r'K-?\d+|H[0-9A-F]+',value,re.I):continue
            if not re.fullmatch(r'(?:X|Y|M|S|T|TN|C|CN|D|R|W|ZR|L|SM)\d+',value,re.I):
                unresolved.add(value)
    return dict(signatures=signatures,graph=graph,reads=reads,writes=writes,details=details,
                unsupported=unsupported,unresolved=unresolved)


def change_impact(before,after,*,limit=50):
    """返回两版已知依赖图中的影响候选与复测建议，不证明语义等价或运行安全。"""
    if type(limit) is not int or not 1<=limit<=200:raise ValueError('明细条数须为1..200')
    if max(len(before),len(after))>8192:raise ValueError('每版程序最多8192条指令，请缩小比较范围')
    diff=diff_programs(before,after);old=_model(before);new=_model(after)
    all_writes=set(old['signatures'])|set(new['signatures'])
    seeds={d for d in all_writes if old['signatures'].get(d)!=new['signatures'].get(d)}
    changed_references=set()
    for change in diff['changes']:
        for key,model in [('beforeRange',old),('afterRange',new)]:
            for index in range(*change[key]):
                seeds.update(model['writes'][index])
                changed_references.update(model['reads'][index]|model['writes'][index])
    graph=defaultdict(set)
    for model in (old,new):
        for source,targets in model['graph'].items():graph[source].update(targets)
    if len(set(graph)|all_writes)>4096:raise ValueError('已知依赖超过4096个地址，请缩小比较范围')
    unsupported=sorted(old['unsupported']|new['unsupported'])
    unresolved=sorted(old['unresolved']|new['unresolved'])
    complete=not unsupported and not unresolved
    reasons={d:dict(kind='changed_writer') for d in sorted(seeds)}
    if diff['changes'] and not complete:
        for d in sorted(all_writes):reasons.setdefault(d,dict(kind='conservative_context'))
    queue=deque(reasons)
    while queue:
        source=queue.popleft()
        for target in sorted(graph[source]):
            if target not in reasons:
                reasons[target]=dict(kind='depends_on',**{'from':source});queue.append(target)
    affected=sorted(reasons)
    categories={key:[d for d in affected if re.fullmatch(pattern,d)] for key,pattern in [
        ('affectedOutputs',r'Y\d+'),('affectedSteps',r'S\d+'),('affectedTimers',r'TN?\d+'),
        ('affectedCounters',r'CN?\d+'),('affectedRegisters',r'(?:D|R|W|ZR)\d+') ]}
    recommendations=[]
    registers_to_review=sorted(set(categories['affectedRegisters'])|{
        d for d in changed_references if re.fullmatch(r'(?:D|R|W|ZR)\d+',d)})
    for key,kind,checks in [
        ('affectedOutputs','outputs',['动作触发与禁止条件','停止、故障、复位后的状态按工艺核对']),
        ('affectedSteps','sequence',['正常转移与分支','异常中断、返回与恢复']),
        ('affectedTimers','timing',['到时前后的输出','暂停、保持与复位行为按工艺核对']),
        ('affectedCounters','counting',['逐次按下与松开确认','计数到值及复位']),
        ('affectedRegisters','data',['位宽、符号及连续字占用','数据结果与外部映射核对'])]:
        devices=registers_to_review if kind=='data' else categories[key]
        if devices:recommendations.append(dict(kind=kind,devices=devices,checks=checks,status='recommended'))
    selected=affected[:limit]
    changes=[dict(operation=c['operation'],beforeRange=c['beforeRange'],afterRange=c['afterRange'],
        removed=c['removed'][:limit],added=c['added'][:limit],
        linesTruncated=max(len(c['removed']),len(c['added']))>limit) for c in diff['changes'][:limit]]
    old_hash=hashlib.sha256(render_il(before).encode('utf-8')).hexdigest()
    new_hash=hashlib.sha256(render_il(after).encode('utf-8')).hexdigest()
    return dict(ok=True,readOnly=True,physicalPlc=False,codeChanged=bool(diff['changes']),
        commentsOnly=not diff['changes'] and old_hash!=new_hash,beforeSha256=old_hash,afterSha256=new_hash,
        changedWrites=sorted(seeds),changedReferences=sorted(changed_references),
        registersToReview=registers_to_review,affectedDevices=affected,**categories,
        reasons={d:reasons[d] for d in selected},details=[dict(device=d,
            before=old['details'].get(d,[])[:limit],after=new['details'].get(d,[])[:limit],
            writersTruncated=max(len(old['details'].get(d,[])),len(new['details'].get(d,[])))>limit) for d in selected],
        detailsTruncated=len(affected)>limit,changes=changes,changesTruncated=len(diff['changes'])>limit,
        analysisComplete=complete,unsupportedInstructions=unsupported,unresolvedReferences=unresolved,
        retestRecommendations=recommendations,runtimeVerified=False,externalMappingsVerified=False,
        limitations=['仅分析所选程序内已知静态依赖，结果为影响候选而非语义等价证明。',
                    '未加载的HMI、通讯映射、其他程序及硬件配置须另行核对。',
                    '复测建议尚未执行，不代表可下载或运行安全。'])
