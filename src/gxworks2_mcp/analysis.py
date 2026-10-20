"""Program analysis: where-used cross reference and coil-tracing (why will this turn on).

Design borrowed from purinzan/gx3-cli-mcp (read-only GX Works3 analyzer): the questions
an AI actually asks after writing a program are where is M100 written, and what has to
be true for Y1 to turn on. This works over our own instruction/rung IR, so it runs on
projects decoded from .gxw, imported CSV, or freshly authored rungs - no GX Works2,
no PLC. Read-only; it never edits the program.
"""

from __future__ import annotations

from typing import Any

from .blocks import Contact, Invert, Node, Parallel, Rung, Series, build_rungs
from .devices import parse_device
from .il import DEVICE_OPERAND_RE, Program, Instruction


class AnalysisError(ValueError):
    """Raised for analysis requests the program cannot answer."""


_WRITE_MNEMONICS = {"OUT", "SET", "RST", "PLS", "PLF", "LDP_COIL"}
_OUTPUT_LIKE = {"OUT", "SET", "RST", "PLS", "PLF"}

#: application instructions whose first operand is a destination, second a source, etc.
_RW_SHAPES = {
    "MOV": ("W", "R"), "DMOV": ("W", "R"), "MOVP": ("W", "R"),
    "ADD": ("R", "R", "W"), "SUB": ("R", "R", "W"), "MUL": ("R", "R", "W"), "DIV": ("R", "R", "W"),
    "DADD": ("R", "R", "W"), "DSUB": ("R", "R", "W"), "DMUL": ("R", "R", "W"), "DDIV": ("R", "R", "W"),
    "INC": ("RW",), "DEC": ("RW",), "DINC": ("RW",), "DDEC": ("RW",),
    "CMP": ("R", "R", "W"), "ZCP": ("R", "R", "R", "W"),
    "BMOV": ("W", "R", "R"), "FMOV": ("R", "W"),
}


def _operands(instruction: Instruction) -> list[str]:
    return [o for o in instruction.operands if DEVICE_OPERAND_RE.match(o)]


def where_used(program: Program) -> dict[str, list[dict[str, Any]]]:
    """Map every device to its write and read sites, with instruction context.

    Mirrors the GX cross-reference: OUT/SET/RST/PLS/PLF and application
    destinations are writes; contacts and application sources are reads.
    """

    sites: dict[str, list[dict[str, Any]]] = {}
    for index, instruction in enumerate(program.instructions):
        operands = _operands(instruction)
        if not operands:
            continue
        if instruction.mnemonic in _OUTPUT_LIKE:
            kinds = ["W"] + ["R"] * (len(operands) - 1)
        elif instruction.mnemonic in _RW_SHAPES:
            kinds = list(_RW_SHAPES[instruction.mnemonic])
        else:
            kinds = ["R"] * len(operands)
        for device, kind in zip(operands, kinds):
            try:
                key = str(parse_device(device))
            except Exception:
                continue
            entry = {
                "rung": index,
                "kind": kind,
                "instruction": str(instruction),
                "comment": instruction.comment,
            }
            sites.setdefault(key, []).append(entry)
    return dict(sorted(sites.items()))


def _contact_name(contact: Contact) -> str:
    try:
        return str(parse_device(contact.device))
    except Exception:
        return contact.device


def _render(node: Node | None) -> str:
    return "(always)" if node is None else str(node)


def _leaf_devices(node: Node | None) -> list[Contact]:
    if node is None:
        return []
    if isinstance(node, Contact):
        return [node]
    if isinstance(node, Invert):
        return _leaf_devices(node.child)
    if isinstance(node, (Series, Parallel)):
        out: list[Contact] = []
        for child in node.children:
            out.extend(_leaf_devices(child))
        return out
    return []


def trace_device(program: Program, device: str, *, max_depth: int = 4) -> dict[str, Any]:
    """Why will this coil turn on? Expand internal conditions to external inputs.

    Returns a tree: each rung that writes the device gives its driving logic;
    every contact on a settable device (M/L/S/...) is expanded to that device's
    own write rung, recursively, until reaching boundary inputs (X, T/C
    contacts, SM) or a depth/cycle stop, which is reported, never hidden.
    """

    try:
        target = str(parse_device(device))
    except Exception as exc:
        raise AnalysisError("cannot read device " + repr(device) + ": " + str(exc)) from exc

    writers: dict[str, list[Rung]] = {}
    for rung in build_rungs(program):
        ins = rung.instruction
        if ins.mnemonic in _OUTPUT_LIKE and ins.operands:
            try:
                key = str(parse_device(ins.operands[0]))
            except Exception:
                continue
            writers.setdefault(key, []).append(rung)

    def expand(name: str, depth: int, seen: set[str]) -> dict[str, Any]:
        node_out: dict[str, Any] = {"device": name, "writes": []}
        rungs = writers.get(name)
        if not rungs:
            family = name.rstrip("0123456789.")
            node_out["boundary"] = (
                "external input / no program writer (X/T/C/SM family or HMI/PLC-wired)"
                if family in {"X", "TS", "TC", "SM", "T", "C"}
                else "never written by this program"
            )
            return node_out
        if len(rungs) > 1:
            node_out["multiWriter"] = str(len(rungs)) + " coils (last OUT wins each scan)"
        for rung in rungs:
            item: dict[str, Any] = {
                "rung": rung.instruction.step if rung.instruction.step is not None else rung.steps[0],
                "mnemonic": rung.instruction.mnemonic,
                "logic": _render(rung.logic),
            }
            if rung.instruction.comment:
                item["comment"] = rung.instruction.comment
            if name in seen or depth >= max_depth:
                item["stopped"] = "cycle or max depth " + str(max_depth)
            else:
                children = {}
                for contact in _leaf_devices(rung.logic):
                    cname = _contact_name(contact)
                    ctry = parse_device(cname) if DEVICE_OPERAND_RE.match(cname) else None
                    if ctry is None:
                        continue
                    if ctry.device in {"X", "SM"} or ctry.is_timer_counter_contact:
                        children.setdefault(cname, "boundary")
                        continue
                    children[cname] = expand(cname, depth + 1, seen | {name})
                if children:
                    item["expands"] = children
            node_out["writes"].append(item)
        return node_out

    return {"program": program.title or "(program)", "target": target, **expand(target, 0, set())}


def interlock_summary(program: Program) -> dict[str, Any]:
    """Quick static review: coils written more than once and devices read but never written."""

    usage = where_used(program)
    multi = {}
    dangling = []
    for device, entries in usage.items():
        writes = [e for e in entries if e["kind"] == "W"]
        reads = [e for e in entries if e["kind"] != "W"]
        if len(writes) > 1 and device[0] not in ("D", "W", "R", "ZR"):
            multi[device] = len(writes)
        if writes and not reads:
            dangling.append(device)
    return {"multiWriterCoils": multi, "writeOnlyDevices": sorted(dangling)}
