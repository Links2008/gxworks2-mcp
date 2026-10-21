"""Fail-closed FX3U step-table planning, NOT a runnable STL compiler.

Mitsubishi JY997D16601R, sections 35.1.4, 35.2.2--35.2.5, distinguishes
logical transfers from the old state's OFF-cleanup execution cycle. A simple
SET-per-edge translation does not establish output exclusivity or one transfer
per scan. Until reset, scan order and output cleanup are validated together,
this module deliberately returns structured previews without Program/IL/CSV.

The startup/reset policies below describe requested behavior, not implemented
PLC behavior. Conditions are compiled only as isolated review fragments using
author.compile_logic. These fragments are not an executable SFC program.
"""
from __future__ import annotations

import re
from typing import Any

from .author import BuildError, compile_logic, compile_rung
from .blocks import build_rungs
from .devices import DeviceError, parse_device
from .il import Program, parse_il, render_il

MANUAL_URL = (
    "https://dl.mitsubishielectric.com/dl/fa/document/manual/plc_fx/"
    "jy997d16601/jy997d16601r.pdf"
)


class SFCError(BuildError):
    """Invalid or unsupported step-table specification."""


def _object(value: Any, allowed: set[str], required: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        raise SFCError(f"{where} must be an object")
    unknown = value.keys() - allowed
    missing = required - value.keys()
    if unknown or missing:
        raise SFCError(f"{where}: unknown fields {sorted(unknown, key=str)!r}; missing {sorted(missing)!r}")
    return value


def _name(value: Any, where: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", value):
        raise SFCError(f"{where} must be an ASCII identifier (1..64 characters)")
    return value


def _bit(value: Any, where: str, *, output: bool = False) -> str:
    # Use FX octal X/Y spelling, not the shared Q-family hexadecimal parser.
    if not isinstance(value, str):
        raise SFCError(f"{where} must be a device string")
    value = value.strip().upper()
    match = re.fullmatch(r"([XYM])([0-9]{1,5})", value)
    if not match:
        raise SFCError(f"{where}: only X/Y octal or ordinary M contacts are supported")
    kind, number = match.groups()
    if output and kind == "X":
        raise SFCError(f"{where}: X inputs cannot be OUT actions")
    if kind in "XY":
        if any(c not in "01234567" for c in number) or int(number, 8) > 0o377:
            raise SFCError(f"{where}: preview supports only {kind}000..{kind}377 (octal)")
        return kind + format(int(number, 8), "03o")
    if int(number) > 7679:
        raise SFCError(f"{where}: special/out-of-range M devices are unsupported")
    return "M" + str(int(number))


def _condition(value: Any, where: str) -> list[str]:
    budget = [0]

    def normalize(node: Any, depth: int = 0) -> Any:
        budget[0] += 1
        if depth > 16 or budget[0] > 512:
            raise SFCError(f"{where}: condition exceeds depth/node limit")
        if isinstance(node, str):
            return _bit(node, where)
        if not isinstance(node, dict) or len(node) != 1:
            raise SFCError(f"{where}: expected contact or one-key logic object")
        key, children = next(iter(node.items()))
        if key == "not":
            return {key: _bit(children, where)}
        if key not in {"and", "or"} or not isinstance(children, list) or not children:
            raise SFCError(f"{where}: only not/and/or level conditions are supported")
        return {key: [normalize(child, depth + 1) for child in children]}

    return compile_logic(normalize(value))


def preview_sfc(table: dict[str, Any]) -> dict[str, Any]:
    """Validate a single-path step table and return a non-executable preview.

    Schema: cpu='FX3U', initial=<step id>, startup='restart_initial',
    reset={when:<logic>, mode:'all_off_then_initial'}, steps=[{id, state,
    actions:[<Y/M device>], transition?:{when:<logic>, to:<step id>}}].
    Initial state is S0..S9; other states are restricted to S20..S499.
    Loops are representable, branches/parallel steps are not. Each output has
    one owner. Level conditions only; timers, edge contacts, latched actions,
    external state ownership and safety functions are intentionally excluded.
    No caller input is mutated and no project or file is created.
    """
    _object(table, {"cpu", "initial", "startup", "reset", "steps"},
            {"cpu", "initial", "startup", "reset", "steps"}, "table")
    if table["cpu"] != "FX3U":
        raise SFCError("only FX3U structure previews are supported")
    if table["startup"] != "restart_initial":
        raise SFCError("startup must explicitly request restart_initial; resume is unsupported")
    initial = _name(table["initial"], "initial")
    reset = _object(table["reset"], {"when", "mode"}, {"when", "mode"}, "reset")
    if reset["mode"] != "all_off_then_initial":
        raise SFCError("reset mode must explicitly request all_off_then_initial")
    reset_condition = _condition(reset["when"], "reset.when")
    raw_steps = table["steps"]
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 128:
        raise SFCError("steps must contain 1..128 step objects")
    steps: dict[str, dict[str, Any]] = {}
    states: set[str] = set()
    owners: dict[str, str] = {}
    for raw in raw_steps:
        _object(raw, {"id", "state", "actions", "transition"}, {"id", "state", "actions"}, "step")
        name = _name(raw["id"], "step.id")
        if name in steps:
            raise SFCError(f"duplicate step id: {name}")
        state = raw["state"]
        if not isinstance(state, str) or not re.fullmatch(r"S[0-9]{1,4}", state):
            raise SFCError(f"{name}: state must be S<number>")
        number = int(state[1:])
        state = f"S{number}"
        if (name == initial and not 0 <= number <= 9) or (name != initial and not 20 <= number <= 499):
            raise SFCError(f"{name}: initial state requires S0..S9; other states require S20..S499")
        if state in states:
            raise SFCError(f"duplicate state device: {state}")
        states.add(state)
        actions = raw["actions"]
        if not isinstance(actions, list) or len(actions) > 128:
            raise SFCError(f"{name}: actions must be a list of at most 128 OUT devices")
        outputs = []
        for value in actions:
            device = _bit(value, f"{name}.actions", output=True)
            if device in owners:
                raise SFCError(f"duplicate output owner: {device} in {owners[device]} and {name}")
            owners[device] = name
            outputs.append(device)
        transition = None
        if "transition" in raw:
            tr = _object(raw["transition"], {"when", "to"}, {"when", "to"}, f"{name}.transition")
            target = _name(tr["to"], f"{name}.transition.to")
            if target == name:
                raise SFCError(f"{name}: self-transition is unsupported")
            transition = {"to": target, "conditionPreview": _condition(tr["when"], f"{name}.transition.when")}
        steps[name] = {"id": name, "state": state, "outActions": outputs, "transition": transition}
    if initial not in steps:
        raise SFCError("initial step does not exist")
    for step in steps.values():
        if step["transition"] and step["transition"]["to"] not in steps:
            raise SFCError(f"{step['id']}: transition target does not exist")
    reachable: set[str] = set()
    current = initial
    while current not in reachable:
        reachable.add(current)
        transition = steps[current]["transition"]
        if transition is None:
            break
        current = transition["to"]
    if unreachable := steps.keys() - reachable:
        raise SFCError(f"unreachable steps: {sorted(unreachable)!r}")
    return {
        "valid": True, "runnable": False, "mode": "structure_preview", "cpu": "FX3U",
        "initial": initial, "requestedStartup": table["startup"],
        "requestedReset": {"mode": reset["mode"], "conditionPreview": reset_condition},
        "steps": list(steps.values()),
        "blockingReasons": [
            "STL scan ordering, same-scan cascades and old-state OFF cleanup are not verified by this preview.",
            "Startup/reset intent is recorded, not compiled; output interlocks and reset timing require engineering review.",
            "No runnable IL, CSV, native graphical SFC, simulator validation or PLC safety guarantee is provided.",
        ],
        "source": {"url": MANUAL_URL, "manual": "JY997D16601R", "sections": ["35.1.4", "35.2.2", "35.2.4", "35.2.5"],
                   "printedPages": [762, 786, 789, 791]},
    }


def compile_sfc(table: dict[str, Any]) -> None:
    """Fail closed instead of treating a structure preview as runnable STL."""
    preview_sfc(table)
    raise SFCError("SFC compilation is disabled: only structure preview is available; scan/reset semantics require verification")


def _state(value: Any) -> str:
    try:
        ref = parse_device(value)
    except (DeviceError, TypeError) as exc:
        raise BuildError("步骤必须是 S 软元件") from exc
    if ref.device != "S" or ref.bit is not None or not 0 <= ref.index < 4096:
        raise BuildError("步骤范围为 S0..S4095")
    if 900 <= ref.index <= 999:
        raise BuildError("S900..S999 为报警状态区，不作为顺控步骤")
    return str(ref)


def compile_stl(spec: dict[str, Any], cpu: str) -> Program:
    """生成 FX3U 顺控：步骤动作、条件转移和并行分支使用原生 STL/SET。"""
    if not cpu.upper().startswith("FX3U"):
        raise BuildError("结构化 SFC 目前支持 FX3U，请显式指定 cpu=FX3U")
    if not isinstance(spec, dict) or not isinstance(spec.get("steps"), list) or not spec["steps"]:
        raise BuildError("SFC 需要非空 steps 数组")
    steps = spec["steps"]
    if any(not isinstance(step, dict) for step in steps):
        raise BuildError("每个步骤必须是对象")
    states = [_state(step.get("state")) for step in steps]
    if len(states) != len(set(states)):
        raise BuildError("SFC 步骤不允许重复")
    initial = _state(spec.get("initial", states[0]))
    if initial not in states or int(initial[1:]) > 9:
        raise BuildError("初始步骤必须已定义，且位于 S0..S9")
    lines = ["LD M8002", "SET " + initial]
    preamble = spec.get("preamble", [])
    if not isinstance(preamble, list):
        raise BuildError("preamble 必须是梯形图回路数组")
    for rung in preamble:
        lines.extend(render_il(Program(instructions=compile_rung(rung))).splitlines())
    for state, step in zip(states, steps):
        lines.append("STL " + state)
        actions = step.get("actions", [])
        transitions = step.get("transitions", [])
        if not isinstance(actions, list) or not isinstance(transitions, list):
            raise BuildError("actions 和 transitions 必须是数组")
        for action in actions:
            lines.extend(render_il(Program(instructions=compile_rung(action))).splitlines())
        for transition in transitions:
            if not isinstance(transition, dict) or "when" not in transition or "to" not in transition:
                raise BuildError("转移需要 when 条件和 to 目标")
            targets = transition["to"]
            targets = targets if isinstance(targets, list) else [targets]
            targets = [_state(target) for target in targets]
            if not targets or len(targets) != len(set(targets)) or any(t not in states for t in targets):
                raise BuildError("转移目标必须非空、不重复且已定义")
            lines.extend(compile_logic(transition["when"]))
            if len(targets) > 1:
                lines.append("MPS")
            for index, target in enumerate(targets):
                if index:
                    lines.append("MPP" if index == len(targets) - 1 else "MRD")
                lines.append("SET " + target)
    return parse_il("\n".join(lines + ["RET", "END"]))


def describe_sfc(program: Program) -> dict[str, Any]:
    """从实际指令恢复步骤、动作和转移；条件保留 STL 步骤门控。"""
    conditions = {id(rung.instruction): str(rung.logic) for rung in build_rungs(program)}
    steps = []
    current = None
    for ins in program.instructions:
        if ins.mnemonic == "STL":
            current = {"state": ins.operands[0], "actions": [], "transitions": []}
            steps.append(current)
        elif ins.mnemonic == "RET":
            current = None
        elif current is not None and ins.is_output:
            if ins.operands[0].upper().startswith("S") and ins.mnemonic in {"SET", "OUT"}:
                current["transitions"].append({"to": ins.operands[0], "when": conditions.get(id(ins))})
            else:
                current["actions"].append({"instruction": str(ins), "when": conditions.get(id(ins))})
    return {"representation": "FX3U STL/RET", "steps": steps}
