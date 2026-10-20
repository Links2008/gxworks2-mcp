"""FX3U 的结构化顺控图与 STL/RET 指令之间的转换。"""

from typing import Any

from .author import BuildError, compile_logic, compile_rung
from .blocks import build_rungs
from .devices import DeviceError, parse_device
from .il import Program, parse_il, render_il


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


def compile_sfc(spec: dict[str, Any], cpu: str) -> Program:
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
