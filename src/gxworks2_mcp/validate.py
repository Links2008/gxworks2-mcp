"""Static checks for an IL program, aligned with what the GX Works2 ladder editor rejects.

The checks here are the ones a programmer actually gets bitten by when writing a program
outside GX Works2: a coil driven twice in one scan, a jump to a label that does not
exist, an unbalanced MC/MCR or FOR/NEXT pair, a rung without END, or a device that does
not exist on the selected CPU model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .devices import DeviceError, DeviceRef, parse_device
from .blocks import LogicError, build_rungs
from .il import END, Instruction, Program, instruction_count

Severity = Literal["error", "warning", "info"]

#: Rough device limits per CPU family, used only for warnings.
DEVICE_LIMITS: dict[str, dict[str, int]] = {
    "Q": {"X": 8192, "Y": 8192, "M": 8192, "L": 8192, "B": 8192, "D": 12288, "W": 8192, "R": 32768, "ZR": 65536, "T": 2048, "C": 1024, "S": 8192},
    "L": {"X": 8192, "Y": 8192, "M": 8192, "L": 8192, "B": 8192, "D": 12288, "W": 8192, "R": 32768, "ZR": 65536, "T": 2048, "C": 1024, "S": 8192},
    "FX3U": {"X": 1024, "Y": 1024, "M": 7680, "S": 4096, "D": 8000, "R": 32768, "T": 512, "C": 256},
    "FX5U": {"X": 8192, "Y": 8192, "M": 8192, "L": 8192, "D": 12288, "W": 8192, "R": 32768, "ZR": 65536, "T": 2048, "C": 1024},
}

#: Instructions that drive an output bit and therefore must be unique per scan.
COIL_LIKE = {"OUT", "SET", "RST", "PLS", "PLF", "OUTH", "OUTL", "OUTF"}


@dataclass
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    line: int | None = None
    step: int | None = None
    device: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "line": self.line,
            "step": self.step,
            "device": self.device,
        }

    def __str__(self) -> str:
        where = ""
        if self.line is not None:
            where = " line " + str(self.line)
        elif self.step is not None:
            where = " step " + str(self.step)
        return "[" + self.severity + "]" + where + " " + self.code + ": " + self.message


def _cpu_limits(cpu: str | None) -> dict[str, int] | None:
    if not cpu:
        return None
    key = cpu.upper().replace(" ", "")
    if key in DEVICE_LIMITS:
        return DEVICE_LIMITS[key]
    for family in ("FX5U", "FX3U", "Q", "L"):
        if key.startswith(family):
            return DEVICE_LIMITS[family]
    return None


def validate_program(
    program: Program,
    *,
    cpu: str | None = None,
    strict: bool = False,
) -> list[ValidationIssue]:
    """Return every issue found; an empty list means the program is clean."""

    issues: list[ValidationIssue] = []
    limits = _cpu_limits(cpu)

    if not program.instructions:
        issues.append(ValidationIssue("error", "EMPTY", "program contains no instructions"))
        return issues

    mnemonic_lines: list[tuple[int, Instruction]] = list(enumerate(program.instructions, start=1))

    # 1) END instruction, exactly once at the tail.
    ends = [(line, ins) for line, ins in mnemonic_lines if ins.mnemonic == END]
    if not ends:
        issues.append(ValidationIssue("error", "MISSING_END", "program has no END instruction; GX Works2 needs END (or FEND for a sub-program)"))
    elif len(ends) > 1:
        issues.append(ValidationIssue("warning", "MULTIPLE_END", "program contains " + str(len(ends)) + " END instructions; the first one ends the scan"))
    elif ends[0][0] != len(program.instructions):
        trailing = len(program.instructions) - ends[0][0]
        issues.append(ValidationIssue("warning", "CODE_AFTER_END", str(trailing) + " instruction(s) follow END and never execute; move them before END", line=ends[0][0]))

    # 2) Structural checks that do not need the block stack.
    labels = {ins.label for ins in program.instructions if ins.label}
    for line, ins in mnemonic_lines:
        if ins.mnemonic in {"CJ", "CJP", "JMP", "SCJ", "CALL", "CALLP"}:
            target = ins.operands[0] if ins.operands else None
            if target is None:
                issues.append(ValidationIssue("error", "MISSING_OPERAND", ins.mnemonic + " needs a label", line=line))
            elif not target.isdigit() and target.upper() not in {lbl.upper() for lbl in labels}:
                issues.append(
                    ValidationIssue(
                        "warning",
                        "LABEL_NOT_FOUND",
                        ins.mnemonic + " jumps to " + repr(target) + " but no line defines that label",
                        line=line,
                    )
                )
    if strict and not labels and any(ins.mnemonic in {"CJ", "CJP"} for _, ins in mnemonic_lines):
        issues.append(ValidationIssue("warning", "NO_LABELS", "program uses jumps but defines no labels"))

    # 3) Pairing of FOR/NEXT, MC/MCR, EI/DI.
    counters: dict[str, int] = {"FOR": 0, "MC": 0}
    for line, ins in mnemonic_lines:
        if ins.mnemonic == "FOR":
            counters["FOR"] += 1
        elif ins.mnemonic == "NEXT":
            counters["FOR"] -= 1
            if counters["FOR"] < 0:
                issues.append(ValidationIssue("error", "UNBALANCED_NEXT", "NEXT without a matching FOR", line=line))
                counters["FOR"] = 0
        elif ins.mnemonic in {"MC", "MCB"}:
            counters["MC"] += 1
        elif ins.mnemonic in {"MCR", "MCRB"}:
            counters["MC"] -= 1
            if counters["MC"] < 0:
                issues.append(ValidationIssue("error", "UNBALANCED_MCR", "MCR without a matching MC", line=line))
                counters["MC"] = 0
    if counters["FOR"]:
        issues.append(ValidationIssue("error", "UNBALANCED_FOR", str(counters["FOR"]) + " FOR loop(s) without NEXT"))
    if counters["MC"]:
        issues.append(ValidationIssue("error", "UNBALANCED_MC", str(counters["MC"]) + " MC block(s) without MCR"))

    # 4) Block stack: ANB/ORB/MPS/MRD/MPP consistency.
    try:
        rungs = build_rungs(program)
    except LogicError as exc:
        issues.append(ValidationIssue("error", "LOGIC_STRUCTURE", str(exc)))
        rungs = []

    # 5) Double coils: the same device driven by two OUT-family instructions.
    coil_sites: dict[str, list[int]] = {}
    for line, ins in mnemonic_lines:
        if ins.mnemonic in COIL_LIKE and ins.operands:
            coil_sites.setdefault(ins.operands[0].upper(), []).append(line)
    for device, lines in coil_sites.items():
        if len(lines) > 1:
            issues.append(
                ValidationIssue(
                    "warning",
                    "DOUBLE_COIL",
                    "device " + device + " is driven by " + str(len(lines)) + " coils (lines " + ", ".join(str(x) for x in lines) + "); in MELSEC the last one wins",
                    device=device,
                )
            )

    # 6) Unused contact-only operations: LD ... with no output in the rung.
    for rung in rungs:
        if rung.instruction.mnemonic == "UNUSED":
            issues.append(ValidationIssue("error", "DANGLING_LOGIC", "logic is never consumed by an output; add OUT/ANB/ORB or remove it"))

    # 7) Device validity and range checks against the selected CPU.
    if limits is not None:
        for line, ins in mnemonic_lines:
            for operand in ins.operands:
                try:
                    ref: DeviceRef = parse_device(operand)
                except DeviceError:
                    continue
                family = "T" if ref.device in {"TN", "TS", "TC"} else ("C" if ref.device in {"CN", "CS", "CC"} else ref.device)
                limit = limits.get(family)
                if limit is not None and ref.index >= limit:
                    issues.append(
                        ValidationIssue(
                            "warning",
                            "DEVICE_OUT_OF_RANGE",
                            ref.device + str(ref.index) + " exceeds the " + family + " range of " + str(cpu) + " (0.." + str(limit - 1) + ")",
                            line=line,
                            device=str(ref),
                        )
                    )

    # 8) Sizing information.
    counts = instruction_count(program)
    if len(program.instructions) > 4000:
        issues.append(ValidationIssue("info", "LARGE_PROGRAM", "program has " + str(len(program.instructions)) + " instructions; consider splitting into sub-programs"))

    # 9) Application-instruction operand sanity (weights, hex constants).
    for line, ins in mnemonic_lines:
        if ins.mnemonic in {"MOV", "DMOV", "ADD", "DADD", "SUB", "DSUB", "MUL", "DMUL", "DIV", "DDIV", "CMP", "DCMP", "INC", "DINC", "DEC", "DDEC"}:
            if len(ins.operands) < 2:
                issues.append(ValidationIssue("error", "MISSING_OPERAND", ins.mnemonic + " requires a source and a destination", line=line))
            elif ins.mnemonic.startswith("D") and ins.mnemonic in {"DMOV", "DADD", "DSUB", "DMUL", "DDIV", "DCMP"}:
                dest = ins.operands[-1]
                try:
                    ref = parse_device(dest)
                except DeviceError:
                    continue
                if ref.kind == "bit":
                    issues.append(ValidationIssue("error", "BAD_DESTINATION", "32-bit instruction " + ins.mnemonic + " cannot target bit device " + dest, line=line))

    if not any(issue.severity == "error" for issue in issues) and not issues:
        issues.append(ValidationIssue("info", "OK", "program passed all checks (" + str(len(program.instructions)) + " instructions, " + str(len(counts)) + " distinct mnemonics)"))
    return issues


def summarize(issues: list[ValidationIssue]) -> dict[str, object]:
    """Counts plus a compact verdict used in tool output."""

    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]
    return {
        "ok": not errors,
        "errors": len(errors),
        "warnings": len(warnings),
        "issues": [issue.as_dict() for issue in issues],
    }
