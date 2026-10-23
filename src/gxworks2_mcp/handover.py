"""按代码版本归档本会话证据；仅新建资料ZIP，不改程序或访问PLC。"""
from collections import deque
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from uuid import uuid4
import zipfile
import zlib
from .analysis import where_used
from .blocks import build_rungs
from .devices import parse_device, DeviceError
from .il import render_il
from .locking import project_lock
from .sfc import describe_sfc
from .validate import summarize, validate_program

_records=deque(maxlen=32)
_guard=threading.Lock()


def program_hash(program):
    return hashlib.sha256(render_il(program).encode('utf-8')).hexdigest()


def remember_evidence(project, version, kind, result, role='current'):
    """有界内存记录；缓存失败或过大不改变已有工具的调用结果。"""
    try:payload=json.dumps(result,ensure_ascii=False,allow_nan=False)
    except (ValueError,TypeError):return None
    if len(payload.encode('utf-8'))>131072:return None
    entry=dict(id=uuid4().hex,root=os.path.normcase(str(project.root.resolve())),cpu=project.cpu,
        programSha256=version,kind=kind,role=role,createdUtc=datetime.now(timezone.utc).isoformat(),payload=payload)
    with _guard:_records.append(entry)
    return entry['id']


def _json(value):
    return json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')


def _csv_cell(value):
    value=str(value)
    return "'"+value if value.lstrip(' \t\r\n').startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n')) else value


def verify_handover(path, project=None):
    """只读检查既有导出格式；文件内哈希不构成来源认证或现场验收。"""
    path=Path(path).expanduser().absolute()
    with path.open('rb') as stream:raw=stream.read(32*1024*1024+1)
    if len(raw)>32*1024*1024:raise ValueError('ZIP文件超过32MiB')
    expected={'程序.il','程序信息.json','I_O清单.csv','软元件引用.json','步骤与置复位.json',
              '静态检查.json','归档证据.json','现场信息.json','交付说明.txt','归档清单.json'}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            members=z.infolist()
            if len(members)!=len(expected) or {m.filename for m in members}!=expected:
                raise ValueError('资料文件缺失、重复或包含未知路径')
            if any(m.flag_bits & 1 for m in members):raise ValueError('不支持加密资料包')
            if sum(m.file_size for m in members)>16*1024*1024:raise ValueError('展开资料超过16MiB')
            files={m.filename:z.read(m) for m in members}
    except (zipfile.BadZipFile,RuntimeError,NotImplementedError,EOFError,zlib.error) as exc:
        raise ValueError('ZIP结构、压缩数据或CRC损坏') from exc
    manifest=json.loads(files['归档清单.json'])
    if not isinstance(manifest,dict) or not isinstance(manifest.get('files'),dict):
        raise ValueError('归档清单格式错误')
    hashes=manifest['files']
    if set(hashes)!=expected-{'归档清单.json'}:raise ValueError('哈希清单缺项或包含未知文件')
    for name,digest in hashes.items():
        if digest!=hashlib.sha256(files[name]).hexdigest():raise ValueError('文件哈希不一致：'+name)
    info=json.loads(files['程序信息.json']);evidence=json.loads(files['归档证据.json'])
    version=hashlib.sha256(files['程序.il']).hexdigest()
    if not isinstance(info,dict) or any(not isinstance(manifest.get(k),str) or not manifest[k]
        or info.get(k)!=manifest[k] for k in ('project','cpu','programSha256')):
        raise ValueError('程序信息与归档清单不一致')
    if manifest['programSha256']!=version:raise ValueError('程序内容与归档版本不一致')
    if not isinstance(evidence,list) or len(evidence)>32 or type(manifest.get('evidenceCount')) is not int \
        or manifest['evidenceCount']!=len(evidence):raise ValueError('归档证据数量或格式不一致')
    if any(not isinstance(e,dict) or e.get('programSha256')!=version or e.get('cpu')!=manifest['cpu']
        or not isinstance(e.get('result'),dict) for e in evidence):raise ValueError('归档证据版本或格式不一致')
    matched=None;current=None
    if project is not None:
        with project_lock(project):
            current=dict(project=project.name,cpu=project.cpu,programSha256=program_hash(project.read_program()))
        matched=current['cpu']==manifest['cpu'] and current['programSha256']==version
    return dict(ok=matched is not False,status='workspace_mismatch' if matched is False else 'verified',
        path=str(path),sha256=hashlib.sha256(raw).hexdigest(),checkedFiles=len(files),integrityVerified=True,
        project=manifest['project'],cpu=manifest['cpu'],programSha256=version,evidenceCount=len(evidence),
        workspaceMatch=matched,workspaceVersion=current,signatureVerified=False,programMatchVerified=False,
        readOnly=True,programModified=False,simulatorAccessed=False,physicalPlc=False,acceptance='待评定')


def export_handover(project, target=None):
    """导出所选程序与匹配版本的近期证据；不将资料包当成原生全工程或现场验收。"""
    with project_lock(project):
        if not project.il_path.is_file():raise ValueError('程序源文件不存在')
        program=project.read_program()
        if len(program)>8192:raise ValueError('归档程序最多8192条指令')
        version=program_hash(program);root=os.path.normcase(str(project.root.resolve()))
        with _guard:records=[dict(r) for r in _records if r['root']==root]
        matching=[r for r in records if r['programSha256']==version and r['cpu']==project.cpu]
        evidence=[{**{k:v for k,v in r.items() if k not in {'root','payload'}},'result':json.loads(r['payload'])} for r in matching]
        usage=where_used(program);raw_comments=project.device_comments();comments={}
        for name,label in raw_comments.items():
            try:key=str(parse_device(name))
            except DeviceError:continue
            comments[key]=label if key not in comments or comments[key]==label else '[待确认：注释冲突]'
        table=io.StringIO(newline='');writer=csv.writer(table)
        writer.writerow(['软元件','类型','注释','引用行','端子号'])
        for device,sites in usage.items():
            if device.startswith(('X','Y')):
                row=[device,'输入' if device.startswith('X') else '输出',comments.get(device) or '[待填写]',
                     ','.join(str(i+1) for i in sorted({s['rung'] for s in sites})),'[待填写]']
                writer.writerow([_csv_cell(v) for v in row])
        positions={id(ins):index+1 for index,ins in enumerate(program)}
        reset=[dict(line=positions[id(r.instruction)],instruction=str(r.instruction),
                    condition=str(r.logic),comment=r.instruction.comment or '[待填写]')
               for r in build_rungs(program,max_blocks=8) if r.instruction.mnemonic in {'SET','RST'}]
        now=datetime.now(timezone.utc).isoformat()
        text=(f'调试交付资料\n程序：{project.name}\nCPU：{project.cpu}\n代码SHA256：{version}\n生成UTC：{now}\n'
            f'当前版本归档证据：{len(evidence)}条\n'+('未归档本会话证据，不据此认定未测试或已验收。\n' if not evidence else '')+
            '范围仅为所选存储程序，不含原生GXW全工程或未加载模块。\n'
            '本会话最多保留近期32条、单条128KiB证据；重启、版本变化和容量限制可能导致缺项。\n'
            '离线诊断、影响建议与仿真调用分别保留原始来源；仿真记录未自动认证实际装载代码。\n'
            'I/O表来自代码引用，外部映射、接线、工艺用途和现场签字须核对。\n'
            'SHA256用于内容完整性检查，不是签字认证；现场验收：[待评定]。\n')
        files={'程序.il':render_il(program).encode('utf-8'),
            '程序信息.json':_json(dict(project=project.name,cpu=project.cpu,programSha256=version,deviceComments=raw_comments)),
            'I_O清单.csv':table.getvalue().encode('utf-8-sig'),'软元件引用.json':_json(usage),
            '步骤与置复位.json':_json(dict(sfc=describe_sfc(program),setReset=reset,
                note='未发现STL时不编造步骤；置复位用途与报警含义须按工艺确认。')),
            '静态检查.json':_json(summarize(validate_program(program,cpu=project.cpu))),
            '归档证据.json':_json(evidence),'现场信息.json':_json({'设备/工位':'[待填写]','接线版本':'[待填写]',
                '调试人员':'[待填写]','复核人员':'[待填写]','现场验收':'[待评定]'}),'交付说明.txt':text.encode('utf-8')}
        manifest=dict(project=project.name,cpu=project.cpu,programSha256=version,createdUtc=now,
            evidenceCount=len(evidence),excludedVersionRecords=len(records)-len(matching),
            evidenceScope='仅本MCP进程捕获的匹配版本近期记录',programMatchVerified=False,
            acceptance='待评定',files={n:hashlib.sha256(b).hexdigest() for n,b in files.items()})
        files['归档清单.json']=_json(manifest)
        if sum(map(len,files.values()))>16777216:raise ValueError('资料超过16MiB，请缩小范围')
        if target is None:
            directory=project.root.parent.parent/'handover';directory.mkdir(parents=True,exist_ok=True)
            target=directory/(project.name+'_'+uuid4().hex+'.zip')
        else:target=Path(target).expanduser().absolute()
        if target.is_symlink() or target.exists() or target.suffix.lower()!='.zip' or not target.parent.is_dir():
            raise ValueError('输出须为现有目录中尚不存在的ZIP文件，禁止覆盖或符号链接')
        with tempfile.TemporaryDirectory(prefix='gx-handover-',dir=target.parent,ignore_cleanup_errors=True) as scratch:
            staged=Path(scratch)/'资料.zip'
            with zipfile.ZipFile(staged,'w',zipfile.ZIP_DEFLATED) as z:
                for name,data in files.items():z.writestr(name,data)
            with zipfile.ZipFile(staged) as z:
                if z.testzip() is not None or set(z.namelist())!=set(files):raise ValueError('ZIP结构核验失败')
                if any(z.read(n)!=b for n,b in files.items()):raise ValueError('ZIP读回不一致')
            digest=hashlib.sha256(staged.read_bytes()).hexdigest()
            # 同目录发布；Windows rename及POSIX link均拒绝覆盖既有文件。
            if os.name=='nt':os.rename(staged,target)
            else:os.link(staged,target)
        return dict(ok=True,path=str(target.resolve()),sha256=digest,files=list(files),
            programSha256=version,evidenceCount=len(evidence),excludedVersionRecords=manifest['excludedVersionRecords'],
            programModified=False,simulatorAccessed=False,physicalPlc=False,acceptance='待评定')
