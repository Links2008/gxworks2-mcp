"""无窗口的 GXW 原生源码写入：编码、封装、元数据同步、逐流核验。"""

import base64
import hashlib
import json
import os
import re
import struct
import tempfile
import time
from pathlib import Path

from .devices import parse_device
from .gxw import GxwError, _CONTACT_CODES, _fx_operators, open_gxw, pou_stream
from .il import Program
from .validate import summarize, validate_program


def encode_program(program: Program, cpu: str = "FX3U", text_encoding: str = "utf-8") -> bytes:
    """按有原生依据的 FX3U 指令表编码；未知指令或操作数直接拒绝。"""
    if not cpu.upper().startswith("FX3U"):
        raise GxwError("原生写入目前支持 FX3U/FX3UC")
    validation = summarize(validate_program(program, cpu=cpu))
    if not validation["ok"]:
        raise GxwError("程序校验失败：" + json.dumps(validation, ensure_ascii=False))
    operators = _fx_operators()
    devices = {name: code for code, (name, _) in _CONTACT_CODES.items()}
    output = bytearray()
    for ins in program.instructions:
        choices = [(raw, arity) for raw, (name, arity) in operators.items()
                   if name == ins.mnemonic and arity == len(ins.operands)]
        if not choices:
            raise GxwError("没有已验证的编码：" + str(ins))
        if ins.mnemonic in {"SET", "RST"} and ins.operands[0].upper().startswith("S"):
            # 本机原生程序检查要求 S 步骤使用专用置位、复位编码。
            output.extend(bytes.fromhex("04230204" if ins.mnemonic == "SET" else "04240204"))
        elif ins.mnemonic == "RST" and ins.operands[0].upper().startswith(("T", "C")):
            output.extend(bytes.fromhex("04240304"))
        else:
            output.extend(min(choices, key=lambda item: len(item[0]))[0])
        for text in ins.operands:
            if re.fullmatch(r"K[+-]?\d+", text, re.I):
                value = int(text[1:])
                bits = 32 if ins.mnemonic.startswith(('DMOV','DADD','DSUB','DMUL','DDIV','DCMP','LDD','ANDD','ORD')) else 16
                if not -(1 << (bits-1)) <= value < (1 << (bits-1)):
                    raise GxwError("K 常量超出指令位宽")
                value_bytes = bytes([value]) if 0 <= value <= 255 else struct.pack('<h' if -32768 <= value <= 32767 else '<i',value)
                code = 0xE8
            elif re.fullmatch(r"H[0-9A-F]+", text, re.I):
                value = int(text[1:],16)
                bits = 32 if ins.mnemonic.startswith(('DMOV','DADD','DSUB','DMUL','DDIV','DCMP','LDD','ANDD','ORD')) else 16
                if value >= 1 << bits: raise GxwError('H 常量超出指令位宽')
                value_bytes = value.to_bytes(2 if value <= 65535 else 4,'little')
                code = 0xEA
            else:
                ref = parse_device(text)
                if ref.bit is not None or ref.device not in devices:
                    raise GxwError("尚无原生编码依据的操作数：" + text)
                value = int(text[1:], 8) if ref.device in ("X", "Y") else ref.index
                if not 0 <= value <= 65535:
                    raise GxwError("设备地址超过当前编码范围：" + text)
                value_bytes = value.to_bytes(1 if value <= 255 else 2, "little")
                code = devices[ref.device]
            size = len(value_bytes) + 3
            output.extend(bytes([size, code]) + value_bytes + bytes([size]))
        if ins.comment:
            if not text_encoding:
                raise GxwError("模板代码页未知，不能安全写入行注释")
            comment = ins.comment.encode(text_encoding)
            if len(comment) > 60:
                raise GxwError("原生行注释暂限编码后 60 字节")
            output.extend(bytes([len(comment) + 4, 0x82, len(comment)]) + comment + bytes([len(comment) + 4]))
    return bytes(output)


def _replace_cfb(data: bytes, replacements: dict[str, bytes]) -> bytes:
    """在内存中使用 Windows IStorage 封装，支持流增长和缩小。"""
    try:
        import pythoncom
        from win32com import storagecon
    except ImportError as exc:
        raise GxwError("Windows 原生封装需要 pywin32：pip install 'gxworks2-mcp[native]'") from exc
    pythoncom.CoInitialize()
    storage = stream = memory = None
    try:
        mode = storagecon.STGM_READWRITE | storagecon.STGM_SHARE_EXCLUSIVE
        memory = pythoncom.CreateILockBytesOnHGlobal()
        if memory.WriteAt(0, data) != len(data):
            raise GxwError("复合文档未完整载入内存")
        storage = pythoncom.StgOpenStorageOnILockBytes(memory, None, mode)
        for name, payload in replacements.items():
            stream = storage.OpenStream(name, None, mode)
            stream.SetSize(len(payload))
            stream.Seek(0, 0)
            written = stream.Write(payload)
            if written != len(payload):
                raise GxwError("复合文档流未完整写入：" + name)
            stream.Commit(0)
            stream = None
        storage.Commit(0)
        storage = None
        size = memory.Stat(1)[2]
        result = memory.ReadAt(0, size)
        if len(result) != size:
            raise GxwError("复合文档未完整读回")
        return result
    except pythoncom.com_error as exc:
        raise GxwError("Windows 复合文档写入失败：" + str(exc)) from exc
    finally:
        stream = storage = memory = None
        pythoncom.CoUninitialize()


def _history(raw: bytes, replacements: dict[str, tuple[bytes, bytes]]) -> bytes:
    """只改目标历史行的长度与已识别 MD5 字段，其余 XML 字节原样保留。"""
    seen = set()
    def row(match):
        body = match.group(0)
        identity = re.search(rb"<iProjectdataID>(\d+)</iProjectdataID>", body)
        key = identity.group(1).decode() if identity else ""
        if key not in replacements:
            return body
        if key in seen:
            raise GxwError("历史记录的流号重复：" + key)
        seen.add(key)
        old, new = replacements[key]
        body, count = re.subn(rb"(<iFileSize>)\d+(</iFileSize>)", lambda m: m[1] + str(len(new)).encode() + m[2], body)
        if count != 1:
            raise GxwError("目标历史行缺少唯一文件长度：" + key)
        old_md5 = base64.b64encode(hashlib.md5(old).digest())
        new_md5 = base64.b64encode(hashlib.md5(new).digest())
        return body.replace(b"<szMD5val>" + old_md5 + b"</szMD5val>", b"<szMD5val>" + new_md5 + b"</szMD5val>")
    result = re.sub(rb"<D_History\b[^>]*>.*?</D_History>", row, raw, flags=re.S)
    if seen != set(replacements):
        raise GxwError("未找到目标流的历史元数据")
    return result


def write_gxw(template: str, output: str, program: Program, *, cpu: str = "FX3U", block: str = "") -> dict:
    """模板另存为新的 GXW；源码完整读回并验证其余所有数据流未变后才交付。"""
    started = time.perf_counter()
    source, target = Path(template).resolve(), Path(output).resolve()
    if source == target or target.exists():
        raise GxwError("输出必须是新的 .gxw 路径，禁止覆盖输入或已有文件")
    if target.suffix.lower() != ".gxw" or not target.parent.is_dir():
        raise GxwError("输出目录必须存在，扩展名必须为 .gxw")
    project = open_gxw(source)
    try:
        if not project.cpu.upper().startswith("FX3U"):
            raise GxwError("模板 CPU 不是已核实的 FX3U：" + project.cpu)
        text_encoding = project.text_encoding
        tokens = encode_program(program, cpu, text_encoding)
        candidates = [e for e in project.programs() if e.name.endswith(".Program.pou") and (not block or e.name == block)]
        if len(candidates) != 1:
            raise GxwError("请指定唯一 block，使用程序清单中的 .Program.pou 名称")
        entry = candidates[0]
        original = project.read_stream(entry)
        if len(original) < 103 or original[54] != 1 or struct.unpack_from("<II", original, 55) != (len(original) - 83,) * 2:
            raise GxwError("仅支持已验证的简单梯形图源码模板；原生 SFC 图形结构不能套用此格式")
        replacement = bytearray(original[:79] + tokens + original[-24:])
        struct.pack_into("<II", replacement, 55, len(replacement) - 83, len(replacement) - 83)
        struct.pack_into("<I", replacement, 50, 0)
        replacement = bytes(replacement)
        before_outer = {"/".join(n): project._outer.openstream(n).read() for n in project._outer.listdir()}
        before_inner = {"/".join(n): project._inner.openstream(n).read() for n in project._inner.listdir()}
        history = _history(before_outer["history.xml"], {entry.stream_id: (original, replacement)})
        stream_id, block_name = entry.stream_id, entry.name
        source_bytes = project._raw
    finally:
        project.close()
    from .il import parse_il
    readback = pou_stream(replacement, cpu=cpu, text_encoding=text_encoding)
    if not readback.get("decoded") or [str(i) for i in parse_il(readback["il"]).instructions] != [str(i) for i in program.instructions]:
        raise GxwError("二进制源码读回与待写程序不一致")
    with tempfile.TemporaryDirectory(prefix="gxw-write-", dir=target.parent) as scratch:
        outer_path = Path(scratch) / "candidate.gxw"
        inner_bytes = _replace_cfb(before_outer["_hdb"], {stream_id: replacement})
        candidate = _replace_cfb(source_bytes, {"_hdb": inner_bytes, "history.xml": history})
        outer_path.write_bytes(candidate)
        check = open_gxw(outer_path)
        try:
            after_inner = {"/".join(n): check._inner.openstream(n).read() for n in check._inner.listdir()}
            after_outer = {"/".join(n): check._outer.openstream(n).read() for n in check._outer.listdir()}
            if after_inner != {**before_inner, stream_id: replacement} or after_outer != {**before_outer, "_hdb": inner_bytes, "history.xml": history}:
                raise GxwError("原生封装改变了非目标数据流，已拒绝交付")
        finally:
            check.close()
        os.rename(outer_path, target)
    return {"ok": True, "path": str(target), "block": block_name, "cpu": cpu,
            "instructions": len(program.instructions), "sourceReadback": True,
            "otherStreamsPreserved": True, "requiresCompile": True,
            "writeMs": round((time.perf_counter() - started) * 1000, 2),
            "method": "direct-gxw-source", "computerUseCalls": 0}
