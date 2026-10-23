"""按块栈还原 MELSEC IL 条件与输出。

LD/AND/OR 构建当前条件，ANB/ORB 从待合并栈取回左侧条件；
MPS/MRD/MPP 使用独立的分支记忆栈。结构问题交由校验器报告。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .il import (
    CONTACT_PARALLEL,
    CONTACT_SERIES,
    CONTACT_START,
    Instruction,
    Program,
    COMPARISON_CONTACTS,
    STRUCTURAL,
)


class LogicError(ValueError):
    """Raised when IL block structure cannot be reduced (unbalanced ANB/ORB/MPS/MPP)."""


@dataclass(frozen=True)
class Contact:
    """A single ladder contact."""

    device: str
    negated: bool = False
    edge: str | None = None
    bits: int = 16

    def __str__(self) -> str:
        prefix = "!" if self.negated else ""
        suffix = "^" if self.edge == "rising" else ("v" if self.edge == "falling" else "")
        return prefix + self.device + suffix


@dataclass(frozen=True)
class Series:
    """Contacts that must all be true (series connection)."""

    children: tuple["Node", ...]

    def __str__(self) -> str:
        return "(" + " & ".join(str(child) for child in self.children) + ")"


@dataclass(frozen=True)
class Parallel:
    """Branches where any one being true is enough (parallel connection)."""

    children: tuple["Node", ...]

    def __str__(self) -> str:
        return "(" + " | ".join(str(child) for child in self.children) + ")"


@dataclass(frozen=True)
class Invert:
    """Logically inverted operand (MELSEC INV)."""

    child: "Node"

    def __str__(self) -> str:
        return "~" + str(self.child)


Node = Contact | Series | Parallel | Invert


def contact_for(instruction: Instruction) -> Contact:
    """Build a Contact from an LD/AND/OR-family instruction."""

    mnemonic = instruction.mnemonic
    if mnemonic in COMPARISON_CONTACTS:
        import re
        op = re.search(r'[<>=]+$',mnemonic).group()
        return Contact(device=f'{instruction.operands[0]} {op} {instruction.operands[1]}',
                       bits=32 if mnemonic.startswith(('LDD','ANDD','ORD')) else 16)
    negated = mnemonic in {"LDI", "ANI", "ORI"}
    edge = None
    if mnemonic in {"LDP", "ANDP", "ORP"}:
        edge = "rising"
    elif mnemonic in {"LDF", "ANDF", "ORF"}:
        edge = "falling"
    device = instruction.operands[0] if instruction.operands else "?"
    return Contact(device=device, negated=negated, edge=edge)


def intersect(left: Node | None, right: Node) -> Node:
    if left is None:
        return right
    if isinstance(left, Series):
        return Series(left.children + (right,))
    return Series((left, right))


def unite(left: Node | None, right: Node) -> Node:
    if left is None:
        return right
    if isinstance(left, Parallel):
        return Parallel(left.children + (right,))
    return Parallel((left, right))


@dataclass
class Rung:
    """The logic that drives one output (or one standalone instruction)."""

    logic: Node | None
    instruction: Instruction
    steps: tuple[int, int] = (0, 0)

    @property
    def device(self) -> str | None:
        return self.instruction.target_device

    def expression(self) -> str:
        logic = "(always)" if self.logic is None else str(self.logic)
        return logic + "  ->  " + str(self.instruction)


class _State:
    """Mutable scan state for build_rungs."""

    def __init__(self) -> None:
        self.block: Node | None = None
        self.saved: list[Node] = []
        self.mem: list[Node | None] = []
        self.invert_next = False
        self.step = 0
        self.start = 0
        self.emitted = False


def build_rungs(program: Program, *, max_blocks: int | None = None) -> list[Rung]:
    """Pair every output / standalone instruction with the logic that reaches it."""

    state = _State()
    rungs: list[Rung] = []
    active_step: Node | None = None

    def take_contact() -> Node:
        node: Node = contact_for(instruction)
        if state.invert_next:
            state.invert_next = False
            return Invert(node)
        return node

    def close() -> None:
        if state.saved:
            raise LogicError(f"{instruction.mnemonic} at step {state.step} with unjoined blocks")
        rungs.append(
            Rung(
                logic=(intersect(active_step, state.block) if state.block is not None else active_step)
                if instruction.mnemonic not in STRUCTURAL else None,
                instruction=instruction,
                steps=(state.start, state.step - 1),
            )
        )
        # OUT 后可继续级联接点；下一条独立 LD 才另起条件。
        state.emitted = instruction.mnemonic not in STRUCTURAL
        if not state.emitted:
            state.block = None
        state.saved.clear()
        state.invert_next = False
        state.start = state.step
        # An MPS that was never closed belongs to the rung that just ended.
        if not state.mem:
            state.mem = []

    for instruction in program.instructions:
        mnemonic = instruction.mnemonic
        after_output = state.emitted
        state.emitted = False
        state.step += 1
        if mnemonic == "STL":
            if state.saved:
                raise LogicError(f"STL at step {state.step} with unjoined blocks")
            active_step = Contact(device=instruction.operands[0])
            state.block = None
        elif mnemonic == "RET":
            active_step = None
            close()
        elif mnemonic in CONTACT_START:
            if state.block is not None and not after_output:
                if max_blocks is not None and len(state.saved) + 2 > max_blocks:
                    raise LogicError(f"LD block depth exceeds {max_blocks} at step {state.step}")
                state.saved.append(state.block)
            state.block = take_contact()
        elif mnemonic in CONTACT_SERIES:
            state.block = intersect(state.block, take_contact())
        elif mnemonic in CONTACT_PARALLEL:
            state.block = unite(state.block, take_contact())
        elif mnemonic == "ANB":
            if not state.saved or state.block is None:
                raise LogicError("ANB at step " + str(state.step) + " without two blocks")
            state.block = Series((state.saved.pop(), state.block))
        elif mnemonic == "ORB":
            if not state.saved or state.block is None:
                raise LogicError("ORB at step " + str(state.step) + " without two blocks")
            state.block = Parallel((state.saved.pop(), state.block))
        elif mnemonic == "MPS":
            state.mem.append(state.block)
        elif mnemonic == "MRD":
            if not state.mem:
                raise LogicError("MRD at step " + str(state.step) + " without MPS")
            state.block = state.mem[-1]
        elif mnemonic == "MPP":
            if not state.mem:
                raise LogicError("MPP at step " + str(state.step) + " without MPS")
            state.block = state.mem.pop()
        elif mnemonic == "INV":
            if state.block is not None:
                state.block = Invert(state.block)
            else:
                state.invert_next = True
        else:
            close()
    if state.mem:
        rungs.append(
            Rung(
                logic=state.mem[-1],
                instruction=Instruction("UNUSED"),
                steps=(state.start, state.step),
            )
        )
    elif (state.block is not None or state.saved) and not state.emitted:
        rungs.append(
            Rung(
                logic=state.block,
                instruction=Instruction("UNUSED"),
                steps=(state.start, state.step),
            )
        )
    return rungs


def to_boolean(program: Program) -> str:
    """Render the program as a readable logic listing."""

    lines: list[str] = []
    for rung in build_rungs(program):
        if rung.instruction.mnemonic == "UNUSED":
            lines.append("?? dangling logic: " + str(rung.logic))
            continue
        lines.append(rung.expression())
    return "\n".join(lines)


def _cell_text(contact: Contact) -> str:
    name = contact.device
    if contact.edge == "rising":
        name += "^"
    elif contact.edge == "falling":
        name += "v"
    return ("/" + name) if contact.negated else name


def _node_lines(node: Node | None) -> list[str]:
    """Render a logic node as the contact chain of one ladder row."""

    if node is None:
        return ["~~~~"]
    if isinstance(node, Contact):
        return ["[" + _cell_text(node) + "]"]
    if isinstance(node, Series):
        parts: list[str] = []
        for child in node.children:
            parts.extend(_node_lines(child))
        return parts
    if isinstance(node, Parallel):
        branches = [render_row(child) for child in node.children]
        width = max(len(line) for line in branches)
        padded = [line.ljust(width) for line in branches]
        out: list[str] = []
        for index, line in enumerate(padded):
            if index == 0:
                out.append("+-" + line + "-+")
            elif index == len(padded) - 1:
                out.append("+-" + line + "-+")
            else:
                out.append("| " + line + " |")
        return out
    if isinstance(node, Invert):
        return ["(INV " + str(node) + ")"]
    return [str(node)]


def render_row(node: Node | None) -> str:
    """One ladder row as a single string of contacts."""

    if node is None:
        return "~~~~"
    if isinstance(node, Contact):
        return "[" + _cell_text(node) + "]"
    if isinstance(node, Series):
        inner: list[str] = []
        for child in node.children:
            text = render_row(child)
            inner.append(text)
        return "----".join(inner)
    if isinstance(node, Parallel):
        return "(" + " | ".join(render_row(child) for child in node.children) + ")"
    if isinstance(node, Invert):
        return "(INV " + str(node) + ")"
    return str(node)


def render_ascii(program: Program) -> str:
    """Render a ladder preview: contacts in series on one row, parallels inline."""

    rows: list[str] = []
    for rung in build_rungs(program):
        if rung.instruction.mnemonic == "UNUSED":
            rows.append("?? dangling logic: " + str(rung.logic))
            continue
        left = render_row(rung.logic)
        instruction = rung.instruction
        if instruction.is_output:
            target = instruction.operands[0] if instruction.operands else "?"
            row = "|" + left + "----(" + target + ")---|"
        else:
            row = "|" + left + "----[" + str(instruction) + "]"
        comment = instruction.comment
        if comment:
            row = row.ljust(52) + "  // " + comment
        rows.append(row)
    return "\n".join(rows) if rows else "(empty program)"


def ladder_blocks(program: Program) -> list[dict[str, object]]:
    """Structured rung description used by the JSON tools and the snippet writer."""

    result: list[dict[str, object]] = []
    for rung in build_rungs(program):
        if rung.instruction.mnemonic == "UNUSED":
            result.append({"kind": "dangling", "logic": str(rung.logic)})
            continue
        result.append(
            {
                "kind": "output" if rung.instruction.is_output else "instruction",
                "mnemonic": rung.instruction.mnemonic,
                "device": rung.device,
                "operands": list(rung.instruction.operands),
                "logic": None if rung.logic is None else str(rung.logic),
                "steps": list(rung.steps),
                "comment": rung.instruction.comment,
            }
        )
    return result


def coerce_instructions(items: Iterable[Instruction | str]) -> list[Instruction]:
    """Accept either Instruction objects or IL lines and return Instructions."""

    from .il import parse_line

    result: list[Instruction] = []
    for item in items:
        if isinstance(item, Instruction):
            result.append(item)
        else:
            parsed = parse_line(str(item))
            if parsed is not None:
                result.append(parsed)
    return result
