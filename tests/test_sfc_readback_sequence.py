"""从原生保存的文件核对控制时序；扫描模型仅覆盖本示例，不替代实体 PLC。"""

import os

import pytest

from examples.fx3u_sequence import native_sequence_spec, sequence_spec
from gxworks2_mcp.gxw import GxwError
from gxworks2_mcp.il import parse_il
from gxworks2_mcp.native_sfc import read_sfc_gxw, write_sfc_gxw


class ReadbackScan:
    """只执行实际读回的动作、布尔条件、初始复位和 T0..T3 的 100ms 定时。"""

    def __init__(self, document):
        chart, startup = document["blocks"]
        assert not chart["scanBlock"] and startup["scanBlock"]
        self.steps = {s["number"]: s for s in chart["steps"]}
        self.transitions = chart["transitions"]
        self.startup = parse_il(startup["steps"][0]["il"])
        self.bits, self.timers = {}, {}
        self.ms = 0

    def fragment(self, il, *, actions=False):
        acc, stack = None, []
        for instruction in parse_il(il).instructions:
            op, args = instruction.mnemonic, instruction.operands
            if op in {"LD", "LDI"}:
                if acc is not None:
                    stack.append(acc)
                acc = self.bits.get(args[0], False)
                if op == "LDI":
                    acc = not acc
            elif op in {"AND", "ANI", "OR", "ORI"}:
                value = self.bits.get(args[0], False)
                if op in {"ANI", "ORI"}:
                    value = not value
                acc = (acc and value) if op in {"AND", "ANI"} else (acc or value)
            elif op in {"ANB", "ORB"}:
                saved = stack.pop()
                acc = (saved and acc) if op == "ANB" else (saved or acc)
            elif op in {"OUT", "SET", "RST"}:
                enabled = True if actions and acc is None else bool(acc)
                device = args[0]
                if len(args) == 2:
                    assert device in {"T0", "T1", "T2", "T3"} and args[1] == "K10"
                    self.timers[device] = self.timers.get(device, 0) + 10 if enabled else 0
                    self.bits[device] = self.timers[device] >= 1000
                elif op == "OUT":
                    self.bits[device] = enabled
                elif enabled:
                    self.bits[device] = op == "SET"
                acc = None
            else:
                raise AssertionError("本示例扫描模型未支持：" + str(instruction))
        assert not stack
        return acc

    def tick(self, X0=False, X1=False):
        self.bits.update(X0=X0, X1=X1, M8002=self.ms == 0)
        active = [n for n, s in self.steps.items() if self.bits.get(s["state"], False)]
        for number in active:
            self.fragment(self.steps[number]["il"], actions=True)
        for number in active:
            edges = [e for e in self.transitions if e["source"] == number]
            selected = next((e for e in edges if self.fragment(e["whenIl"])), None)
            if selected:
                state = self.steps[number]
                self.bits[state["state"]] = False
                for ins in parse_il(state["il"]).instructions:
                    if ins.mnemonic == "OUT" and ins.operands[0].startswith("T"):
                        self.bits[ins.operands[0]] = False
                        self.timers.pop(ins.operands[0], None)
                self.bits[self.steps[selected["target"]]["state"]] = True
        self.fragment("\n".join(str(i) for i in self.startup.instructions))
        assert sum(bool(self.bits.get(s["state"])) for s in self.steps.values()) == 1
        self.ms += 10
        return tuple(bool(self.bits.get(y)) for y in ("Y0", "Y1", "Y2"))

    def run(self, milliseconds, **inputs):
        return [self.tick(**inputs) for _ in range(milliseconds // 10)]


@pytest.fixture(scope="module")
def native_project(tmp_path_factory):
    template = os.environ.get("GXWORKS2_SFC_TEST_TEMPLATE")
    installation = os.environ.get("GXWORKS2_INSTALL_ROOT")
    if not template or not installation:
        pytest.skip("需显式指定本机原生集成测试模板和安装目录")
    target = tmp_path_factory.mktemp("native-sfc") / "sequence.gxw"
    report = write_sfc_gxw(template, str(target), native_sequence_spec(), installation)
    assert report["nativeProjectCompile"]["errors"] == report["nativeProjectCompile"]["warnings"] == 0
    assert report["compiledReadback"] and report["nativeBindingsReadback"]
    return read_sfc_gxw(str(target)), report


def ready(document):
    plc = ReadbackScan(document)
    assert plc.tick() == (False, False, False)
    plc.tick(X0=True)
    assert plc.tick() == (True, False, False)
    return plc


def test_native_saved_start_and_reverse_stop(native_project):
    plc = ready(native_project[0])
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
def test_native_saved_stop_during_start(native_project, elapsed, immediate):
    plc = ready(native_project[0])
    plc.run(elapsed)
    assert plc.tick(X1=True) == immediate
    assert all(not result[2] for result in plc.run(2200))
    assert plc.tick() == (False, False, False)


def test_native_saved_simultaneous_start_stop_has_no_deferred_start(native_project):
    plc = ReadbackScan(native_project[0])
    plc.tick()
    assert plc.tick(X0=True, X1=True) == (False, False, False)
    assert all(result == (False, False, False) for result in plc.run(2200))


def test_native_saved_held_start_does_not_restart(native_project):
    plc = ready(native_project[0])
    plc.run(2200, X0=True)
    plc.tick(X0=True, X1=True)
    assert plc.run(2200, X0=True)[-1] == (False, False, False)
    plc.tick()
    plc.tick(X0=True)
    assert plc.tick() == (True, False, False)


def test_native_saved_startup_clears_previous_states_and_requires_new_press(native_project):
    plc = ReadbackScan(native_project[0])
    plc.bits.update(S20=True, S22=True, S31=True, M1000=True, M1001=False)
    assert plc.tick(X0=True) == (False, False, False)
    assert plc.run(2100, X0=True)[-1] == (False, False, False)
    plc.tick()
    plc.tick(X0=True)
    assert plc.tick() == (True, False, False)


def test_native_saved_restart_during_stop_is_ignored(native_project):
    plc = ready(native_project[0])
    plc.run(2200)
    plc.tick(X1=True)
    plc.tick(X0=True)
    assert plc.run(2200)[-1] == (False, False, False)


def test_native_rejects_duplicate_coils_without_publishing(native_project, tmp_path):
    output = tmp_path / "rejected.gxw"
    with pytest.raises(GxwError, match="原生检查拒绝"):
        write_sfc_gxw(os.environ["GXWORKS2_SFC_TEST_TEMPLATE"], str(output), sequence_spec(),
                      os.environ["GXWORKS2_INSTALL_ROOT"])
    assert not output.exists()


def test_native_saved_project_can_be_reopened_and_reused_as_template(native_project, tmp_path):
    document, first = native_project
    second = write_sfc_gxw(first["path"], str(tmp_path / "second.gxw"), native_sequence_spec(),
                           os.environ["GXWORKS2_INSTALL_ROOT"])
    assert second["nativeProjectCompile"]["checkStatus"] == "accepted"
    assert read_sfc_gxw(second["path"])["blocks"] == document["blocks"]
