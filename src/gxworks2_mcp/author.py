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

from .blocks import Contact
from .il import Instruction, Program, parse_il


class BuildError(ValueError):
    """Raised when a rung description cannot be compiled to IL."""


def _coerce_operand(value: Any) -> Contact:
    """Turn "X0", {"not": "X0"}, {"rise": "M0"} or {"fall": "M0"} into a Contact."""

    if isinstance(value, str):
        device = value.strip()
        if not device:
            raise BuildError("empty device string")
        return Contact(device=device)
    if isinstance(value, dict):
        if len(value) != 1:
            raise BuildError("operand object must have exactly one key, got " + repr(value))
        (key, inner), = value.items()
        key = str(key).lower()
        if not isinstance(inner, str):
            raise BuildError("operand " + key + " needs a device string")
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
        return [_contact_line("LD", _coerce_operand(node))]
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
            lines.append(_contact_line(simple_join, _coerce_operand(child)))
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
    out = rung.get("out") or rung.get("coil") or rung.get("device")
    if not out:
        raise BuildError("rung needs an out device such as Y0")
    logic = rung.get("logic")
    comment = rung.get("comment")
    if "preset" in rung:
        out = str(out) + " " + str(rung["preset"])
    if logic is None:
        lines = ["OUT " + str(out)]
    else:
        lines = compile_logic(logic) + ["OUT " + str(out)]
    program = parse_il("\n".join(lines))
    if comment:
        for instruction in program.instructions:
            if instruction.is_output:
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
