"""严格有界的 GX Works2 SFC 源码帧；未识别的存储方式拒绝重写。"""

import struct

from .gxw import GxwError, main_res_program


def words(*values):
    return struct.pack("<" + "I" * len(values), *values)


def decode_body(tokens: bytes) -> str:
    """动作片段借用已有指令解码器；补入 END 仅用于片段校验。"""
    body = tokens + bytes.fromhex("033403")
    result = main_res_program(words(len(body)) + body, body_len_offset=0, cpu="FX3U")
    if not result.get("decoded"):
        raise GxwError("SFC 子程序不能完整解码：" + str(result))
    if any(line in {"END", "FEND"} for line in result["il"].splitlines()[:-1]):
        raise GxwError("SFC 子程序片段包含程序结束指令")
    return result["il"].removesuffix("END").rstrip()


def parse_source(raw: bytes) -> dict:
    """读出图形、动作注册和条件字节，并验证所有边界与绑定。"""
    cursor = 0

    def word():
        nonlocal cursor
        if cursor + 4 > len(raw):
            raise GxwError("SFC 数据截断")
        value = struct.unpack_from("<I", raw, cursor)[0]
        cursor += 4
        return value

    def count():
        n = word()
        if n > 8192:
            raise GxwError("SFC 记录数超界")
        return n

    def name():
        nonlocal cursor
        n = count()
        end = cursor + n * 2
        if not n or end > len(raw) or raw[end - 2:end] != b"\0\0":
            raise GxwError("SFC 名称边界无效")
        value = raw[cursor:end - 2].decode("utf-16le")
        if "\0" in value:
            raise GxwError("SFC 名称含空字符")
        cursor = end
        return value

    def child():
        nonlocal cursor
        if word() != 1:
            raise GxwError("未知 SFC 子程序存储模式")
        size = word()
        start, end = cursor, cursor + size
        if size < 20 or end > len(raw) or raw[start:start + 20] != words(size, 1, 12, 0, 0xFFFFFFFF):
            raise GxwError("SFC 子程序帧不匹配")
        body = raw[start + 20:end]
        decode_body(body)
        cursor = end
        if word() != 1:
            raise GxwError("SFC 子程序尾部无效")
        return body

    name()
    name()
    cursor += 42
    header = raw[:cursor]
    if word() != 240:
        raise GxwError("不是原生 SFC 图形源码")
    size = word()
    start, end = cursor, cursor + size
    if size < 20 or end > len(raw) or (word(), word(), word()) != (size, 0, 0):
        raise GxwError("未知 SFC 图形存储模式")
    block = word()
    if block < 4 or cursor + block > end:
        raise GxwError("SFC 图形长度无效")
    graph = raw[cursor:cursor + block - 4]
    cursor += block - 4
    cache = raw[cursor:end]
    if len(cache) < 4 or struct.unpack_from("<I", cache)[0] != len(cache):
        raise GxwError("SFC 图形缓存长度无效")
    cursor = end
    zooms = {}
    for _ in range(count()):
        key = name()
        if key in zooms:
            raise GxwError("SFC 动作体名称重复")
        zooms[key] = child()
    actions = {}
    for _ in range(count()):
        identity, number = name(), word()
        if number in actions:
            raise GxwError("SFC 动作编号重复")
        registers = []
        for _ in range(count()):
            qualifier, zoom = word(), name()
            if qualifier != 0 or zoom not in zooms:
                raise GxwError("SFC 动作限定词或绑定未知")
            registers.append(zoom)
        if word() != 1:
            raise GxwError("SFC 动作尾部无效")
        actions[number] = dict(name=identity, zooms=registers,
                               body=b"".join(zooms[z] for z in registers))
    if {z for action in actions.values() for z in action["zooms"]} != set(zooms):
        raise GxwError("SFC 含未注册的动作体")
    transitions = {}
    for _ in range(count()):
        identity, number = name(), word()
        if number in transitions:
            raise GxwError("SFC 转移编号重复")
        transitions[number] = dict(name=identity, body=child())
    if raw[cursor:] not in (b"", b"\0" * 4):
        raise GxwError("未知 SFC 尾部数据")
    return dict(header=header, graph=graph, cache=cache, actions=actions,
                transitions=transitions, tail=raw[cursor:])


def parse_graph(tokens: bytes) -> dict:
    """按本机原生输出恢复步骤、动作号和显式跳转；未知图形指令直接拒绝。"""
    steps, edges, transition_numbers = {}, [], set()
    cursor, current, transition = 0, None, None
    scan = False

    def operand(code):
        nonlocal cursor
        if cursor >= len(tokens):
            raise GxwError("SFC 图形操作数截断")
        size = tokens[cursor]
        raw = tokens[cursor:cursor + size]
        if size not in (4, 5) or len(raw) != size or raw[1] != code or raw[-1] != size:
            raise GxwError("SFC 图形引用格式未知")
        cursor += size
        return int.from_bytes(raw[2:-1], "little")

    while cursor < len(tokens):
        raw = tokens[cursor:cursor + 5]
        cursor += 5
        if raw in (bytes.fromhex("056c041c05"), bytes.fromhex("056c041405"), bytes.fromhex("056c021705")):
            if current is not None:
                raise GxwError("SFC 步骤分组缺少结束标记")
            current = operand(0x98)
            single = raw == bytes.fromhex("056c021705")
            action = current if single else operand(0x98)
            if current in steps:
                raise GxwError("SFC 步骤引用重复")
            steps[current] = dict(number=current, action=action, state=None if single else "S" + str(action),
                                  initial=raw == bytes.fromhex("056c041c05"))
            scan |= single
        elif raw in {bytes.fromhex("056c02" + key + "05") for key in ("04", "1d", "1e", "05")}:
            if current is None or transition is not None:
                raise GxwError("SFC 转移所属步骤不明确")
            transition = operand(0x99)
            if transition in transition_numbers:
                raise GxwError("SFC 转移引用重复")
            transition_numbers.add(transition)
        elif raw == bytes.fromhex("0562020005"):
            if current is None or transition is None:
                raise GxwError("SFC 跳转缺少步骤或条件")
            edges.append(dict(source=current, transition=transition, target=operand(0x98)))
            transition = None
        elif raw == bytes.fromhex("056c010a05"):
            if current is None or transition is not None:
                raise GxwError("SFC 步骤分组不完整")
            current = None
        else:
            raise GxwError("未知 SFC 图形指令：" + raw.hex())
    if current is not None or transition is not None or not steps or any(e["target"] not in steps for e in edges):
        raise GxwError("SFC 图形引用不完整")
    if any(not any(e["source"] == number for e in edges) for number in steps):
        raise GxwError("SFC 步骤缺少转移")
    if scan and (len(steps) != 1 or len(edges) != 1):
        raise GxwError("未知连续扫描块布局")
    return dict(steps=list(steps.values()), edges=edges, scanBlock=scan)
