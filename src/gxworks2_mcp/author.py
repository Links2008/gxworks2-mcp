"""Authoring helpers: turn a compact, model-friendly ladder description into IL.

Asking a language model to emit exact MELSEC IL for a whole program is error-prone, and a
raw IL string is hard to review. These helpers accept a small logic tree and compile it to
IL with the right ANB/ORB scaffolding:

    {"out": "Y0", "comment": "conveyor start",
     "logic": {"or": [
         {"and": ["X0", {"not": "X1"}]},
         {"and": ["M10", {"rise": "X2"}]}
     ]}}

compiles to::

    LD X0
    ANI X1
    LD M10
    ANDP X2
    ORB
    OUT Y0    // conveyor start
"""

from __future__ import annotations

from typing import Any, Iterable
import re

from .blocks import Contact
from .il import Instruction, Program, parse_il


class BuildError(ValueError):
    """Raised when a rung description cannot be compiled to IL."""


# 参数数、运算位宽、结果占用字数；CMP 的目标为连续三个 M 位。
DATA_ACTIONS = {'MOV':(2,16,1), 'DMOV':(2,32,2), 'ADD':(3,16,1), 'SUB':(3,16,1),
                'MUL':(3,16,2), 'DIV':(3,16,2), 'DADD':(3,32,2), 'DSUB':(3,32,2),
                'DMUL':(3,32,4), 'DDIV':(3,32,4), 'CMP':(3,16,3), 'DCMP':(3,32,3),
                'INC':(1,16,1), 'DEC':(1,16,1), 'DINC':(1,32,2), 'DDEC':(1,32,2)}


def data_operand(value, bits=16):
    """只接受单个数据操作数，防止把另一条指令混入结构化字段。"""
    if not isinstance(value, str) or not re.fullmatch(r'(?:[DTC]\d+|K[+-]?\d+|H[0-9A-Fa-f]+)', value):
        raise BuildError('数据操作数须为 D/T/C 地址或 K/H 常量')
    value = value.upper()
    if value[0]=='K' and not -(1 << (bits-1)) <= int(value[1:]) < (1 << (bits-1)):
        raise BuildError('K 常量超出运算位宽')
    if value[0]=='H' and int(value[1:],16) >= 1 << bits:
        raise BuildError('H 常量超出运算位宽')
    if value[0] in 'DTC':
        limit = {'D':8000,'T':512,'C':256}[value[0]]
        width = 2 if bits==32 and value[0]=='D' else 1
        if int(value[1:])+width > limit:
            raise BuildError('数据地址或连续字超界')
        if bits==32 and (value[0]=='T' or value[0]=='C' and int(value[1:])<200):
            raise BuildError('32 位源只接受连续 D 或 C200..C255')
    return value


def data_action(op, operands):
    if op not in DATA_ACTIONS or not isinstance(operands,list) or len(operands)!=DATA_ACTIONS[op][0]:
        raise BuildError('数据指令或操作数数量无效')
    _,bits,words = DATA_ACTIONS[op]
    args = [data_operand(x,bits) for x in operands[:-1]]
    dest = operands[-1]
    kind = 'M' if op in {'CMP','DCMP'} else 'D'
    if not isinstance(dest,str) or not re.fullmatch(kind+r'\d+',dest,re.I):
        raise BuildError(f'{op} 的目标须为 {kind} 地址')
    if int(dest[1:])+words > (7680 if kind=='M' else 8000):
        raise BuildError('指令结果占用的连续地址超界')
    return args+[dest.upper()]


def comparison_line(base, value):
    if not isinstance(value,dict) or set(value)-{'op','left','right','bits'} or not {'op','left','right'} <= value.keys():
        raise BuildError('compare 需要 op、left、right，可选 bits')
    op,bits = value['op'],value.get('bits',16)
    if op not in {'=','<>','>','>=','<','<='} or type(bits) is not int or bits not in (16,32):
        raise BuildError('比较符或位宽无效')
    return base+('D' if bits==32 else '')+op+' '+data_operand(value['left'],bits)+' '+data_operand(value['right'],bits)


def logic_line(base, node):
    if isinstance(node,dict) and set(node)=={'compare'}:
        return comparison_line(base,node['compare'])
    return _contact_line(base,_coerce_operand(node))


def _coerce_operand(value: Any) -> Contact:
    """Turn "X0", {"not": "X0"}, {"rise": "M0"} or {"fall": "M0"} into a Contact."""

    if isinstance(value, str):
        device = value.strip()
        if not device:
            raise BuildError("empty device string")
        if not re.fullmatch(r'[A-Za-z]{1,3}[0-9A-Fa-f]+(?:\.[0-9A-Fa-f]+)?',device):
            raise BuildError('触点须为单个软元件地址')
        return Contact(device=device)
    if isinstance(value, dict):
        if len(value) != 1:
            raise BuildError("operand object must have exactly one key, got " + repr(value))
        (key, inner), = value.items()
        key = str(key).lower()
        if not isinstance(inner, str):
            raise BuildError("operand " + key + " needs a device string")
        if not re.fullmatch(r'[A-Za-z]{1,3}[0-9A-Fa-f]+(?:\.[0-9A-Fa-f]+)?',inner.strip()):
            raise BuildError('触点须为单个软元件地址')
        if key in {"not", "inv", "neg"}:
            return Contact(device=inner.strip(), negated=True)
        if key in {"rise", "rising", "pulse"}:
            return Contact(device=inner.strip(), edge="rising")
        if key in {"fall", "falling"}:
            return Contact(device=inner.strip(), edge="falling")
        raise BuildError("unknown operand key " + repr(key) + "; use not / rise / fall")
    raise BuildError("operand must be a device string or {not|rise|fall: device}, got " + repr(value))


#: MELSEC spelling: AND + inverted is ANI (not ANDI), OR + inverted is ORI.
_INVERTED = {"LD": "LDI", "AND": "ANI", "OR": "ORI"}


def _contact_line(base: str, contact: Contact) -> str:
    device = contact.device
    if contact.negated:
        base = _INVERTED.get(base, base + "I")
    if contact.edge == "rising":
        base += "P"
    elif contact.edge == "falling":
        base += "F"
    return base + " " + device


def _is_branch(key: str) -> bool:
    return key in {"and", "or"}


def _children(value: Any, key: str) -> list[Any]:
    if isinstance(value, (list, tuple)):
        children = list(value)
    else:
        children = [value]
    if not children:
        raise BuildError(key + " needs at least one operand")
    return children


def _is_simple(value: Any) -> bool:
    """True for a node that compiles to exactly one LD-style line."""

    if isinstance(value, str):
        return True
    if isinstance(value, dict) and len(value) == 1:
        key = str(next(iter(value))).lower()
        return not _is_branch(key)
    return False


def emit_block(node: Any) -> list[str]:
    """Compile a logic node to IL lines that begin with LD and leave one result on the bus."""

    if _is_simple(node):
        return [logic_line("LD", node)]
    if not isinstance(node, dict) or len(node) != 1:
        raise BuildError("logic node must be a device or {and|or: [...]}, got " + repr(node))
    (key, inner), = node.items()
    key = str(key).lower()
    if not _is_branch(key):
        raise BuildError("unknown logic key " + repr(key) + "; use and / or")
    children = _children(inner, key)
    lines: list[str] = []
    simple_join = "AND" if key == "and" else "OR"
    block_join = "ANB" if key == "and" else "ORB"
    for index, child in enumerate(children):
        if index == 0:
            lines.extend(emit_block(child))
            continue
        if _is_simple(child):
            lines.append(logic_line(simple_join, child))
        else:
            lines.extend(emit_block(child))
            lines.append(block_join)
    return lines


def compile_logic(node: Any) -> list[str]:
    """Compile a logic expression into IL lines (without the OUT line)."""

    return emit_block(node)


def compile_rung(rung: dict[str, Any]) -> list[Instruction]:
    """Compile one rung dict into IL instructions."""

    if not isinstance(rung, dict):
        raise BuildError("rung must be an object with out/logic")
    logic = rung.get("logic")
    comment = rung.get("comment")
    if comment is not None and (not isinstance(comment,str) or any(c in comment for c in '\r\n\x00')):
        raise BuildError('comment 须为单行字符串')
    instruction = rung.get("instruction", "OUT")
    if not isinstance(instruction, str):
        raise BuildError('instruction 必须是字符串')
    instruction = instruction.upper()
    if instruction in DATA_ACTIONS:
        if any(key in rung for key in ('out','coil','device','preset')):
            raise BuildError('数据指令使用 operands，不能混用 out 或 preset')
        out = ' '.join(data_action(instruction,rung.get('operands')))
    else:
        out = rung.get("out") or rung.get("coil") or rung.get("device")
        if instruction not in {'OUT','SET','RST'} or 'operands' in rung:
            raise BuildError('回路指令不支持，或 operands 使用方式无效')
        if not isinstance(out,str) or not re.fullmatch(r'[A-Za-z]{1,3}\d+(?:\.\d+)?',out):
            raise BuildError('out 必须是单个软元件地址')
        if "preset" in rung:
            preset=rung['preset']
            if instruction != 'OUT' or not re.fullmatch(r'[TC]\d+',out,re.I):
                raise BuildError('preset 只用于 OUT 定时器或计数器')
            out += ' '+data_operand(preset)
    if logic is None:
        lines = [instruction + " " + str(out)]
    else:
        lines = compile_logic(logic) + [instruction + " " + str(out)]
    program = parse_il("\n".join(lines))
    if comment:
        for instruction in program.instructions:
            if instruction.is_output or instruction.mnemonic in DATA_ACTIONS:
                instruction.comment = str(comment)
    return list(program.instructions)


def compile_rungs(rungs: Iterable[dict[str, Any]]) -> Program:
    """Compile a list of rung descriptions into a Program ending with END."""

    program = Program()
    for index, rung in enumerate(rungs, start=1):
        try:
            program.extend(compile_rung(rung))
        except BuildError as exc:
            raise BuildError("rung " + str(index) + ": " + str(exc)) from exc
    program.append(Instruction("END"))
    return program


def explain_rung(rung: dict[str, Any]) -> str:
    """Return the IL for one rung, for review before it is added to a project."""

    return "\n".join(str(instruction) for instruction in compile_rung(rung))
