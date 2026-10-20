"""Instruction List (IL / 指令表 / リスト) parsing and rendering.

GX Works2 keeps ladder programs in a binary project, but its ladder editor accepts an
instruction-list paste and rebuilds the ladder from it. IL is therefore the portable
authoring format for this MCP server: the model writes IL, we parse and validate it,
and the user (or the optional UI automation) pastes it into the ladder editor.

This module owns the text layer only. Logic reconstruction lives in blocks.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from .devices import DeviceError


class ILParseError(ValueError):
    """Raised for instruction lines that cannot be parsed."""


#: Contact instructions, using the MELSEC mnemonics exactly as GX Works2 prints them:
#: LD/LDI/LDP/LDF open a block, AND/ANI/ANDP/ANDF extend it in series, OR/ORI/ORP/ORF
#: add a parallel branch. There is no separate "ANDI" family - inverted contacts are ANI/ORI.
CONTACT_START = {"LD", "LDI", "LDP", "LDF"}
CONTACT_SERIES = {"AND", "ANI", "ANDP", "ANDF"}
CONTACT_PARALLEL = {"OR", "ORI", "ORP", "ORF"}
BLOCK_JOIN = {"ANB", "ORB"}
STACK_OPS = {"MPS", "MRD", "MPP", "INV"}
OUTPUTS = {"OUT", "SET", "RST", "PLS", "PLF", "OUTH", "OUTL", "OUTF"}
END = "END"

#: Operand count per mnemonic. Mnemonics that are absent are treated as opaque
#: (program control, application instructions, ...) and keep their operands verbatim.
ARITY: dict[str, int] = {
    **{mnemonic: 1 for mnemonic in CONTACT_START | CONTACT_SERIES | CONTACT_PARALLEL | OUTPUTS},
    **{mnemonic: 0 for mnemonic in BLOCK_JOIN | STACK_OPS | {END}},
    "NOP": 1,
    "NOPLF": 0,
    "MEP": 0,
    "MEF": 0,
    "MCR": 1,
    "MC": 2,
    "MCB": 2,
    "MCRB": 1,
    "CJ": 1,
    "CJP": 1,
    "SCJ": 1,
    "JMP": 1,
    "CALL": 1,
    "CALLP": 1,
    "STL": 1,
    "RET": 0,
    "FOR": 2,
    "NEXT": 0,
    "EI": 0,
    "DI": 0,
    "FEND": 0,
    "WDT": 1,
    "RRET": 0,
    "IRET": 0,
    "RESET": 0,
    "STOP": 0,
}

#: Statements that carry no rung logic of their own.
STRUCTURAL = {"END", "FEND", "RET", "IRET", "EI", "DI", "NEXT", "NOP", "NOPLF", "WDT", "RRET", "STOP", "RESET"}

STEP_RE = re.compile(r"^(?P<step>\d{1,6})\s+(?P<rest>[A-Za-z].*)$")
LABEL_LINE_RE = re.compile(r"^(?P<label>[A-Za-z_][A-Za-z0-9_]{0,31})\s*:\s*$")
COMMENT_RE = re.compile(r"(?:^|\s)(?://|;)\s?(?P<comment>.*)$")
DEVICE_OPERAND_RE = re.compile(r"^(?:[A-Za-z]{1,3}\d+)(?:[.]\d)?$")


@dataclass
class Instruction:
    """One IL line: mnemonic, operands, optional label and trailing comment."""

    mnemonic: str
    operands: list[str] = field(default_factory=list)
    label: str | None = None
    comment: str | None = None
    step: int | None = None

    def __str__(self) -> str:
        body = self.mnemonic
        if self.operands:
            body += " " + " ".join(self.operands)
        return body

    @property
    def is_contact(self) -> bool:
        return self.mnemonic in CONTACT_START | CONTACT_SERIES | CONTACT_PARALLEL

    @property
    def is_output(self) -> bool:
        return self.mnemonic.startswith("OUT") or self.mnemonic in OUTPUTS

    @property
    def target_device(self) -> str | None:
        """Device an output writes, or the first operand of a single-device instruction."""

        return self.operands[0] if self.operands else None


def _split_comment(line: str) -> tuple[str, str | None]:
    match = COMMENT_RE.search(line)
    if match:
        body = line[: match.start()].rstrip()
        if body:
            return body, match.group("comment").strip() or None
    return line.rstrip(), None


def parse_line(raw: str) -> Instruction | None:
    """Parse one IL line; blank lines and pure comments return None."""

    if raw is None:
        return None
    line = raw.replace("\t", " ").strip()
    if not line or line.startswith("#") or line.startswith("//"):
        return None
    if line.startswith("(*"):
        return None
    line = line.replace("(*", "//").replace("*)", "")

    label_match = LABEL_LINE_RE.match(line)
    if label_match:
        return Instruction(mnemonic="NOP", label=label_match.group("label"))

    body, comment = _split_comment(line)
    if not body:
        return None

    step: int | None = None
    step_match = STEP_RE.match(body)
    if step_match:
        step = int(step_match.group("step"))
        body = step_match.group("rest")

    tokens = body.split()
    if not tokens:
        return None
    mnemonic = tokens[0].upper()
    operands = tokens[1:]
    expected = ARITY.get(mnemonic)
    if expected is not None and len(operands) < expected:
        raise ILParseError("instruction " + mnemonic + " needs " + str(expected) + " operand(s)")
    return Instruction(mnemonic=mnemonic, operands=operands, comment=comment, step=step)


@dataclass
class Program:
    """An ordered IL program with an optional title."""

    instructions: list[Instruction] = field(default_factory=list)
    title: str = ""

    def __len__(self) -> int:
        return len(self.instructions)

    def __iter__(self) -> Iterator[Instruction]:
        return iter(self.instructions)

    def append(self, instruction: Instruction) -> None:
        self.instructions.append(instruction)

    def extend(self, instructions: Iterable[Instruction]) -> None:
        self.instructions.extend(instructions)

    def devices(self) -> list[str]:
        """Every device-looking operand, in first-seen order."""

        seen: dict[str, None] = {}
        for instruction in self.instructions:
            for operand in instruction.operands:
                if DEVICE_OPERAND_RE.match(operand):
                    seen.setdefault(operand.upper(), None)
        return list(seen)

    def labels(self) -> dict[str, int]:
        """Declared labels mapped to their instruction index."""

        return {
            instruction.label: index
            for index, instruction in enumerate(self.instructions)
            if instruction.label
        }


def parse_il(text: str, title: str = "") -> Program:
    """Parse a whole IL program, tolerating blank lines, comments and step numbers."""

    program = Program(title=title)
    for index, raw in enumerate(text.splitlines(), start=1):
        try:
            instruction = parse_line(raw)
        except ILParseError as exc:
            raise ILParseError("line " + str(index) + ": " + str(exc)) from exc
        except DeviceError:
            raise
        if instruction is not None:
            program.append(instruction)
    return program


def render_il(program: Program, *, with_steps: bool = False, with_comments: bool = True) -> str:
    """Render a program back to paste-ready IL text."""

    lines: list[str] = []
    for index, instruction in enumerate(program.instructions):
        body = instruction.mnemonic
        if instruction.operands:
            body += " " + " ".join(instruction.operands)
        if instruction.label:
            lines.append(instruction.label + ":")
        lines.append((str(index) + " " + body) if with_steps else body)
        if with_comments and instruction.comment:
            lines[-1] += "  // " + instruction.comment
    return "\n".join(lines)


def instruction_count(program: Program) -> dict[str, int]:
    """Count instructions by mnemonic (useful for size and step estimates)."""

    counts: dict[str, int] = {}
    for instruction in program.instructions:
        counts[instruction.mnemonic] = counts.get(instruction.mnemonic, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def coerce_program(source: str | Program | Iterable[Instruction | str]) -> Program:
    """Accept IL text, a Program, or an iterable of lines/Instructions."""

    if isinstance(source, Program):
        return source
    if isinstance(source, str):
        return parse_il(source)
    program = Program()
    for item in source:
        if isinstance(item, Instruction):
            program.append(item)
        else:
            parsed = parse_line(str(item))
            if parsed is not None:
                program.append(parsed)
    return program
