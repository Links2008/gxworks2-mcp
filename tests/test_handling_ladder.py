"""使用实际 GXW 读回指令检查搬运顺序和联锁，不替代实机验收。"""
import pytest
from test_drilling import Scan
from gxworks2_mcp.il import parse_il
from examples.fx3u_handling_ladder import generate


class HandlingScan(Scan):
    def __init__(self, il):
        self.il = '\n'.join(line for line in il.splitlines() if line != 'END')
        self.bits, self.timers, self.ms = {}, {}, 0
        self.inputs = {f'X{i:o}': False for i in range(11)}
        self.inputs.update(X5=True, X11=True)

    def tick(self, **inputs):
        self.inputs.update(inputs)
        self.bits.update(self.inputs, M8002=self.ms == 0)
        self.execute(self.il)
        self.ms += 10
        assert sum(bool(self.bits.get(f'M{200+i}')) for i in range(10)) == 1
        for a, b in [('Y0', 'Y1'), ('Y2', 'Y3'), ('Y4', 'Y5')]:
            assert not (self.bits[a] and self.bits[b])
        if self.bits['M500']: assert not any(self.bits[f'Y{i}'] for i in range(6))

    @property
    def step(self):
        return next(i for i in range(10) if self.bits[f'M{200+i}'])

    def start(self):
        self.tick(X0=True)
        self.tick(X0=False)


@pytest.fixture(scope='module')
def il(tmp_path_factory):
    root = tmp_path_factory.mktemp('handling')
    generate(root)
    return (root/'机械手_指令表.il').read_text(encoding='utf-8')


@pytest.fixture
def plc(il):
    p = HandlingScan(il); p.tick(); p.start()
    assert p.step == 1 and p.bits['Y0']
    return p


def at_b(p, occupied=True):
    p.tick(X5=False, X4=True); assert p.step == 2 and p.bits['Y4']
    p.run(1000); assert p.step == 3 and p.bits['Y1']
    p.tick(X4=False, X5=True); assert p.step == 4 and p.bits['Y2']
    p.tick(X5=False, X11=False, X6=True, X10=True, X12=occupied)
    assert p.step == 5


def test_automatic_cycle_and_one_second_release(plc):
    at_b(plc, False); plc.tick(); assert plc.step == 6 and plc.bits['Y0']
    plc.tick(X6=False, X7=True); assert plc.step == 7 and plc.bits['Y5']
    plc.run(990); assert plc.step == 7
    plc.tick(); assert plc.step == 8 and plc.bits['Y1']
    plc.tick(X7=False, X6=True); assert plc.step == 9 and plc.bits['Y3']
    plc.tick(X6=False, X10=False, X5=True, X11=True); assert plc.step == 0
    plc.tick(); assert plc.step == 1 and plc.bits['Y0']


def test_b_occupied_waits_without_timeout_then_descends(plc):
    at_b(plc); plc.run(12000)
    assert plc.step == 5 and plc.bits['Y4'] and not plc.bits['Y0'] and not plc.bits['Y6']
    plc.tick(X12=False); assert plc.step == 6 and plc.bits['Y0']


def test_b_obstruction_during_descent_latches_fault(plc):
    at_b(plc, False); plc.tick(); plc.tick(X12=True)
    assert plc.bits['M500'] and plc.bits['Y6']
    plc.tick(X12=False); plc.start(); assert plc.bits['M500']


def test_pause_retains_clamp_step_and_elapsed_time(plc):
    plc.tick(X5=False, X4=True); plc.run(400); plc.tick(X1=True)
    before = dict(plc.timers); plc.run(2000, X1=False)
    assert plc.step == 2 and plc.bits['Y4'] and plc.timers == before
    assert not any(plc.bits[f'Y{i}'] for i in (0, 1, 2, 3, 5))
    plc.start(); plc.run(600); assert plc.step == 3


def test_movement_timeout_and_origin_required_after_reset(plc):
    plc.tick(X5=False); plc.run(10100)
    assert plc.bits['M500'] and plc.bits['Y6']
    plc.tick(X3=True); plc.tick(X3=False); assert plc.step == 0 and not plc.bits['M500']
    plc.start(); assert plc.step == 0 and not plc.bits['Y0']
    plc.tick(X5=True); plc.start(); assert plc.step == 1


def test_estop_and_held_reset_do_not_restart(plc):
    plc.tick(X2=True); plc.tick(X3=True); plc.tick(X2=False)
    assert plc.bits['M500']
    plc.tick(X3=False); plc.tick(X3=True); plc.tick(X3=False)
    assert plc.step == 0 and not plc.bits['M500'] and not plc.bits['M0']


def test_conflicting_limits_stop_motion(plc):
    plc.tick(X10=True)
    assert plc.bits['M500'] and plc.bits['Y6']


def test_short_commented_rungs(il):
    depth, stack = None, []
    for ins in parse_il(il):
        op = ins.mnemonic
        if op in ('LD', 'LDI'):
            if depth is not None: stack.append(depth)
            depth = 1
        elif op in ('AND', 'ANI'): depth += 1
        elif op in ('OR', 'ORI'): depth = max(depth, 1)
        elif op in ('ANB', 'ORB'):
            other = stack.pop(); depth = depth + other if op == 'ANB' else max(depth, other)
        elif op in ('OUT', 'SET', 'RST'):
            assert depth <= 4 and ins.comment, (ins.operands, depth)
            depth = None
    assert not stack
