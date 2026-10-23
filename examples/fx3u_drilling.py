"""题目3：钻孔工位原生 SFC，气动顺控、保持型计时、暂停续行和超时锁机。"""

import json
from pathlib import Path

from gxworks2_mcp.native_sfc import write_sfc_gxw, read_sfc_gxw


IO = {
    "X0": "启动/暂停后续行", "X1": "停止/暂停", "X2": "急停触发=1", "X3": "工件到位",
    "X4": "夹紧到位", "X5": "钻孔下限", "X6": "钻头原点", "X7": "连续=1/单周期=0",
    "X10": "报警复位", "Y0": "夹紧电磁阀", "Y1": "钻头下行", "Y2": "钻头上行",
    "Y3": "松开", "Y4": "报警锁机",
}
STEPS = {"S0": "待机/等待工件", "S20": "夹紧并监控2秒超时", "S21": "夹紧后延时0.3秒",
         "S22": "下钻并监控3秒超时", "S23": "下限停留0.8秒", "S24": "上行至原点",
         "S25": "松开0.3秒", "S26": "周期完成/等待工件离开"}
TIMERS = {"T250": ("S20", 20), "T251": ("S21", 3), "T252": ("S22", 30),
          "T253": ("S23", 8), "T254": ("S25", 3)}


def drilling_spec():
    """X10 仅清报警并退回待机；X7 连续循环必须经历工件离开和重新到位。"""
    def AND(*items):
        return {"and": list(items)}
    def OR(*items):
        return {"or": list(items)}
    def NOT(item):
        return {"not": item}
    def rung(out, logic, **options):
        return dict(out=out, logic=logic, **options)

    gate = AND("M0", NOT("X1"), NOT("X2"), NOT("X10"), NOT("M500"), NOT("M101"), NOT("M8002"))
    clear_gate = AND(NOT("X1"), NOT("X2"), NOT("X10"), NOT("M500"), NOT("M101"), NOT("M8002"))
    steps = []
    definitions = [
        ("S0", "S20", AND(gate, "X3", "X6"), []),
        ("S20", "S21", AND(gate, "X4"), [rung("T250", AND(gate, NOT("X4")), preset="K20")]),
        ("S21", "S22", AND(gate, "X4", "T251"), [rung("T251", AND(gate, "X4"), preset="K3")]),
        ("S22", "S23", AND(gate, "X5"), [rung("T252", AND(gate, NOT("X5")), preset="K30")]),
        ("S23", "S24", AND(gate, "T253"), [rung("T253", gate, preset="K8")]),
        ("S24", "S25", AND(gate, "X6"), []),
        ("S25", "S26", AND(gate, "T254"), [rung("T254", gate, preset="K3")]),
        ("S26", "S0", AND(clear_gate, NOT("X3")), []),
    ]
    for state, target, when, actions in definitions:
        steps.append(dict(state=state, actions=actions, transitions=[dict(to=target, when=when)]))

    preamble = [
        rung("M100", AND("X0", NOT("M10"), NOT("X1"), NOT("X2"), NOT("X10"), NOT("M500"), NOT("M8002"))),
        rung("M101", AND("X10", NOT("M11"), NOT("X2"), "M500", NOT("M8002"))),
        rung("M501", AND(OR("M501", AND("S20", "T250", NOT("X4"))), NOT("M101"))),
        rung("M502", AND(OR("M502", AND("S22", "T252", NOT("X5"))), NOT("M101"))),
        rung("M503", AND(OR("M503", "X2"), NOT("M101"))),
        rung("M500", OR("M501", "M502", "M503", AND("M500", NOT("M101")))),
        rung("M0", AND(OR("M0", "M100"), NOT("X1"), NOT("X2"), NOT("X10"), NOT("M500"),
                       NOT("M8002"), OR(NOT("S26"), "X7"))),
    ]
    for timer, (state, _) in TIMERS.items():
        reset = OR("M8002", "M101", "M500", NOT(state))
        if timer == "T251":
            reset = OR(reset, AND("M0", state, NOT("X4")))
        preamble.append(rung(timer, reset, instruction="RST"))
    clamp_states = OR("S20", "S21", "S22", "S23", "S24")
    stationary_safe = AND(NOT("M500"), NOT("X2"), NOT("M101"), NOT("M8002"))
    preamble += [
        rung("Y0", AND(stationary_safe, clamp_states, NOT("S25"))),
        rung("Y1", AND(gate, "S22", "X3", "X4", NOT("X5"), NOT("S24"))),
        rung("Y2", AND(gate, "S24", NOT("X6"), NOT("S22"))),
        rung("Y3", AND(gate, "S25", NOT("S20"), NOT("S21"), NOT("S22"), NOT("S23"), NOT("S24"))),
        rung("Y4", OR("M500", "X2")),
        rung("M10", "X0"), rung("M11", "X10"),
    ]
    return dict(initial="S0", reset="M101", preamble=preamble, steps=steps)


def generate(directory: str, template: str, installation: str):
    """编译、核验后另存新工程，保留 I/O、步骤和实际文件读回。"""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    target = root / "题目3_钻孔加工工位SFC.gxw"
    spec = drilling_spec()
    report = write_sfc_gxw(template, str(target), spec, installation)
    actual = read_sfc_gxw(str(target))
    for filename, data in [("工位编写输入.json", spec), ("实际工程读回.json", actual),
                           ("原生编译核验.json", report), ("IO与步骤.json", dict(io=IO, steps=STEPS, timers=TIMERS))]:
        (root / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "Block1_控制回路.txt").write_text(actual["blocks"][1]["steps"][0]["il"] + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    import sys
    print(json.dumps(generate(*sys.argv[1:4]), ensure_ascii=False, indent=2))
