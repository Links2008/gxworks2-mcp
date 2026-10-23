"""Program analysis: where-used cross reference and coil-tracing (why will this turn on).

Design borrowed from purinzan/gx3-cli-mcp (read-only GX Works3 analyzer): the questions
an AI actually asks after writing a program are where is M100 written, and what has to
be true for Y1 to turn on. This works over our own instruction/rung IR, so it runs on
projects decoded from .gxw, imported CSV, or freshly authored rungs - no GX Works2,
no PLC. Read-only; it never edits the program.
"""

from __future__ import annotations

from typing import Any
from dataclasses import replace
import re

from .blocks import Contact, Invert, Node, Parallel, Rung, Series, build_rungs
from .devices import parse_device
from .il import DEVICE_OPERAND_RE, Program, Instruction
from .semantics import coil_writes, write_kind
from .author import DATA_ACTIONS


class AnalysisError(ValueError):
    """Raised for analysis requests the program cannot answer."""


_WRITE_MNEMONICS = {"OUT", "SET", "RST", "PLS", "PLF", "LDP_COIL"}
_OUTPUT_LIKE = {"OUT", "SET", "RST", "PLS", "PLF"}

#: application instructions whose first operand is a destination, second a source, etc.
_RW_SHAPES = {
    "MOV": ("R", "W"), "DMOV": ("R", "W"), "MOVP": ("R", "W"),
    "ADD": ("R", "R", "W"), "SUB": ("R", "R", "W"), "MUL": ("R", "R", "W"), "DIV": ("R", "R", "W"),
    "DADD": ("R", "R", "W"), "DSUB": ("R", "R", "W"), "DMUL": ("R", "R", "W"), "DDIV": ("R", "R", "W"),
    "INC": ("RW",), "DEC": ("RW",), "DINC": ("RW",), "DDEC": ("RW",),
    "CMP": ("R", "R", "W"), "DCMP": ("R", "R", "W"), "ZCP": ("R", "R", "R", "W"),
    "BMOV": ("R", "W", "R"), "FMOV": ("R", "W", "R"),
}


def where_used(program: Program) -> dict[str, list[dict[str, Any]]]:
    """Map every device to its write and read sites, with instruction context.

    Mirrors the GX cross-reference: OUT/SET/RST/PLS/PLF and application
    destinations are writes; contacts and application sources are reads.
    """

    sites: dict[str, list[dict[str, Any]]] = {}
    for index, instruction in enumerate(program.instructions):
        operands = instruction.operands
        if not operands:
            continue
        op=instruction.mnemonic
        base=op[:-1] if op not in DATA_ACTIONS and op.endswith('P') and op[:-1] in DATA_ACTIONS else op
        meta=DATA_ACTIONS.get(base)
        wide=bool(meta and meta[1]==32 or re.fullmatch(r'(?:LDD|ANDD|ORD)[<>=]+',op))
        if op in _OUTPUT_LIKE:
            kinds = ["W"] + ["R"] * (len(operands) - 1)
        elif base in _RW_SHAPES:
            kinds = list(_RW_SHAPES[base])
        else:
            kinds = ["R"] * len(operands)
        for operand_index,(device, kind) in enumerate(zip(operands, kinds)):
            try:
                ref=parse_device(device)
            except Exception:
                continue
            width=1
            if ref.device in {'D','R','W','ZR'}:
                width=meta[2] if meta and operand_index==len(operands)-1 else 2 if wide else 1
            elif base in {'CMP','DCMP','ZCP'} and operand_index==len(operands)-1:
                width=3
            for offset in range(width):
                key=str(replace(ref,index=ref.index+offset))
                entry=dict(rung=index,kind=kind,instruction=str(instruction),comment=instruction.comment)
                if offset:entry.update(implicit=True,baseDevice=str(ref))
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
    rungs={id(r.instruction):r for r in build_rungs(program)}
    for key,sites in where_used(program).items():
        for site in sites:
            rung=rungs.get(id(program.instructions[site['rung']]))
            if 'W' in site['kind'] and rung is not None:
                writers.setdefault(key,[]).append(rung)

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
            sites = coil_writes(program).get(name, [])
            kind = write_kind(name, sites, fx=any(i.mnemonic=='STL' for i in program))
            node_out["multiWriter"] = (str(len(rungs))+" coils (last OUT wins each scan)"
                                      if kind=='multiple_writes' and all(r.instruction.mnemonic=='OUT' for r in rungs)
                                      else "多处写入，需结合置复位及 STL 状态核对执行关系")
        for rung in rungs:
            item: dict[str, Any] = {
                "rung": rung.instruction.step if rung.instruction.step is not None else rung.steps[0],
                "mnemonic": rung.instruction.mnemonic,
                "operands": list(rung.instruction.operands),
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
    coils = coil_writes(program)
    fx = any(ins.mnemonic == 'STL' for ins in program)
    classified = {d: write_kind(d, sites, fx=fx) for d, sites in coils.items()}
    for device, entries in usage.items():
        writes = [e for e in entries if 'W' in e["kind"]]
        reads = [e for e in entries if 'R' in e["kind"]]
        ordinary=classified.get(device,'multiple_writes')=='multiple_writes' or len(writes)!=len(coils.get(device,[]))
        if len(writes) > 1 and device[0] not in ("D", "W", "R", "ZR") and ordinary:
            multi[device] = len(writes)
        if writes and not reads:
            dangling.append(device)
    return {"multiWriterCoils": multi, "writeOnlyDevices": sorted(dangling),
            "writeSemantics": {d: kind for d, kind in classified.items() if len(coils[d]) > 1}}
