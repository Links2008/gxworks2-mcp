"""三路顺启逆停示例：普通梯形图与 SFC 共用已说明的 I/O 和时序。"""

import json
from pathlib import Path

from gxworks2_mcp.author import compile_logic
from gxworks2_mcp.il import parse_il, render_il
from gxworks2_mcp.sfc import compile_sfc
from gxworks2_mcp.native import write_gxw


def sequence_spec():
    """停止优先；中途停止从最后一个已启动输出开始；停止过程不接受重启。"""
    def transition(target, condition):
        return {"to": target, "when": condition}
    def step(state, outputs, transitions, timer=None):
        actions = [{"out": output} for output in outputs]
        if timer:
            actions.append({"out": timer, "preset": "K10"})
        return {"state": state, "actions": actions, "transitions": transitions}
    return {
        "initial": "S0",
        "preamble": [{"out": "M1000", "logic": {"rise": "X0"}}],
        "steps": [
            step("S0", [], [transition("S20", {"and": ["M1000", {"not": "X1"}]})]),
            step("S20", ["Y0"], [transition("S0", "X1"), transition("S21", {"and": ["T0", {"not": "X1"}]})], "T0"),
            step("S21", ["Y0", "Y1"], [transition("S31", "X1"), transition("S22", {"and": ["T1", {"not": "X1"}]})], "T1"),
            step("S22", ["Y0", "Y1", "Y2"], [transition("S30", "X1")]),
            step("S30", ["Y0", "Y1"], [transition("S31", "T2")], "T2"),
            step("S31", ["Y0"], [transition("S0", "T3")], "T3"),
        ],
    }


def ladder_program():
    """普通梯形图用一组状态位；先取转移快照，避免单扫描内连跳。"""
    spec = sequence_spec()
    states = {step["state"]: "M" + str(100 + i) for i, step in enumerate(spec["steps"])}
    lines = ["LD M8002", "SET M100"]
    for state in list(states.values())[1:]:
        lines.extend(["LD M8002", "RST " + state])
    lines.extend(["LDP X0", "OUT M1000"])
    outputs, transitions = {}, []
    for step in spec["steps"]:
        state = states[step["state"]]
        for action in step["actions"]:
            if "preset" in action:
                lines.extend(["LD " + state, "OUT " + action["out"] + " " + action["preset"]])
            else:
                outputs.setdefault(action["out"], []).append(state)
        for transition in step["transitions"]:
            flag = "M" + str(200 + len(transitions))
            lines.extend(compile_logic({"and": [state, transition["when"]]}))
            lines.append("OUT " + flag)
            transitions.append((flag, state, states[transition["to"]]))
    for flag, source, target in transitions:
        lines.extend(["LD " + flag, "RST " + source, "LD " + flag, "SET " + target])
    for output, enabled in outputs.items():
        lines.extend(compile_logic({"or": enabled}))
        lines.append("OUT " + output)
    return parse_il("\n".join(lines + ["END"]))


def generate(directory):
    """只向新文件写入，返回原生源码核验和耗时记录。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    spec = sequence_spec()
    programs = {"顺启逆停_梯形图": ladder_program(), "顺启逆停_SFC": compile_sfc(spec, "FX3U")}
    template = Path(__file__).parents[1] / "src/gxworks2_mcp/data/fx3u-ladder.gxw"
    reports = {}
    for name, program in programs.items():
        target = directory / (name + ".gxw")
        reports[name] = write_gxw(str(template), str(target), program)
        (directory / (name + ".il")).write_text(render_il(program) + "\n", encoding="utf-8")
    (directory / "顺控步骤.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return reports


if __name__ == "__main__":
    import sys
    print(json.dumps(generate(sys.argv[1]), ensure_ascii=False, indent=2))
