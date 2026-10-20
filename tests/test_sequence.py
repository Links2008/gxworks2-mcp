"""独立扫描执行器验证示例，不依赖生成器的状态转移实现。"""

import pytest

from examples.fx3u_sequence import ladder_program, sequence_spec
from gxworks2_mcp.sfc import compile_sfc
from gxworks2_mcp.validate import summarize, validate_program


class Scan:
    """仅执行本示例使用的基本指令和 100ms T0..T3，不冒充完整 PLC 模拟器。"""

    def __init__(self, program=None):
        self.program = program or ladder_program()
        self.bits, self.timers, self.edges = {}, {}, {}
        self.ms = 0
        self.step_outputs = {}
        scope = None
        for ins in self.program.instructions:
            if ins.mnemonic == "STL":
                scope = ins.operands[0]
                self.step_outputs[scope] = []
            elif ins.mnemonic == "RET":
                scope = None
            elif scope and ins.mnemonic == "OUT":
                self.step_outputs[scope].append(ins.operands[0])

    def tick(self, **inputs):
        self.bits.update(X0=inputs.get("X0", False), X1=inputs.get("X1", False), M8002=self.ms == 0)
        acc, stack = None, []
        scope, active = None, True
        for index, ins in enumerate(self.program.instructions):
            op, args = ins.mnemonic, ins.operands
            if op == "STL":
                scope = args[0]
                active = self.bits.get(scope, False)
                acc = None
            elif op == "RET":
                scope, active, acc = None, True, None
            elif op in ("LD", "LDI", "LDP"):
                if acc is not None:
                    stack.append(acc)
                value = self.bits.get(args[0], False)
                if op == "LDP":
                    previous = self.edges.get(index, False)
                    self.edges[index] = value
                    value = value and not previous
                acc = not value if op == "LDI" else value
            elif op in ("AND", "ANI", "OR", "ORI"):
                value = self.bits.get(args[0], False)
                if op in ("ANI", "ORI"):
                    value = not value
                acc = (acc and value) if op in ("AND", "ANI") else (acc or value)
            elif op in ("ANB", "ORB"):
                saved = stack.pop()
                acc = (saved and acc) if op == "ANB" else (saved or acc)
            elif op in ("SET", "RST", "OUT"):
                target = args[0]
                enabled = active and (True if acc is None and scope else bool(acc))
                if scope and not active:
                    acc = None
                    continue
                if len(args) == 2:
                    assert target in ("T0", "T1", "T2", "T3") and args[1] == "K10"
                    elapsed = self.timers.get(target, 0) + 10 if enabled else 0
                    self.timers[target] = elapsed
                    self.bits[target] = elapsed >= 1000
                elif op == "OUT":
                    self.bits[target] = enabled
                elif enabled:
                    self.bits[target] = op == "SET"
                    if scope and op == "SET" and target.startswith("S") and target != scope:
                        self.bits[scope] = False
                        for output in self.step_outputs[scope]:
                            self.bits[output] = False
                            self.timers.pop(output, None)
                acc = None
            elif op == "END":
                break
            else:
                raise AssertionError("未支持的验证指令：" + str(ins))
        assert not stack
        states = self.step_outputs or {"M" + str(i): [] for i in range(100, 106)}
        assert sum(bool(self.bits.get(state)) for state in states) == 1
        self.ms += 10
        return tuple(bool(self.bits.get(y)) for y in ("Y0", "Y1", "Y2"))

    def run(self, milliseconds, **inputs):
        return [self.tick(**inputs) for _ in range(milliseconds // 10)]


def ready():
    plc = Scan()
    assert plc.tick() == (False, False, False)
    assert plc.tick(X0=True) == (True, False, False)
    return plc


def test_start_and_reverse_stop_timing():
    plc = ready()
    forward = plc.run(2100)
    assert forward[98] == (True, False, False)
    assert forward[99] == (True, True, False)
    assert forward[199] == (True, True, True)
    assert plc.tick(X1=True) == (True, True, False)
    reverse = plc.run(2100)
    assert reverse[98] == (True, True, False)
    assert reverse[99] == (True, False, False)
    assert reverse[199] == (False, False, False)


@pytest.mark.parametrize("elapsed,immediate", [(200, (False, False, False)), (1200, (True, False, False))])
def test_stop_during_start_never_starts_another_motor(elapsed, immediate):
    plc = ready()
    plc.run(elapsed)
    assert plc.tick(X1=True) == immediate
    assert all(not y2 for _, _, y2 in plc.run(2100))
    assert plc.tick() == (False, False, False)


def test_held_start_does_not_restart_after_stop():
    plc = ready()
    plc.run(2200, X0=True)
    plc.tick(X0=True, X1=True)
    assert plc.run(2200, X0=True)[-1] == (False, False, False)
    plc.tick()
    assert plc.tick(X0=True) == (True, False, False)


def test_stop_wins_over_start_and_early_restart_is_ignored():
    plc = Scan()
    assert plc.tick(X0=True, X1=True) == (False, False, False)
    plc.tick()
    plc.tick(X0=True)
    plc.run(2200)
    plc.tick(X1=True)
    plc.tick(X0=True)
    assert plc.run(2200)[-1] == (False, False, False)


def test_both_program_forms_validate_for_fx3u():
    for program in (ladder_program(), compile_sfc(sequence_spec(), "FX3U")):
        assert summarize(validate_program(program, cpu="FX3U"))["ok"]


def test_sfc_and_ladder_follow_the_same_output_sequence():
    programs = (ladder_program(), compile_sfc(sequence_spec(), "FX3U"))
    events = []
    for program in programs:
        plc = Scan(program)
        previous, changes = (False, False, False), []
        for scan in range(700):
            value = plc.tick(X0=scan == 1, X1=scan == 350)
            if value != previous:
                changes.append((plc.ms, value))
                previous = value
        events.append(changes)
    expected = [(True, False, False), (True, True, False), (True, True, True),
                (True, True, False), (True, False, False), (False, False, False)]
    assert [value for _, value in events[0]] == expected
    assert [value for _, value in events[1]] == expected
    assert all(abs(left[0] - right[0]) <= 20 for left, right in zip(*events))
