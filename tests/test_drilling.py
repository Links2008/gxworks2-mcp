"""以实际工程读回指令验证钻孔工位；此扫描模型不替代硬件调试。"""
import os
import pytest
from gxworks2_mcp.native_sfc import read_sfc_gxw
from gxworks2_mcp.il import parse_il


class Scan:
    def __init__(self, doc):
        chart, scan = doc['blocks']
        self.steps = {s['number']: s for s in chart['steps']}
        self.edges = chart['transitions']
        self.control = scan['steps'][0]['il']
        self.bits, self.timers, self.ms = {}, {}, 0
        self.inputs = {**{f'X{i}': False for i in range(8)}, 'X10': False, 'X6': True}

    def execute(self, il, actions=False):
        acc, stack = None, []
        for ins in parse_il(il).instructions:
            op, a = ins.mnemonic, ins.operands
            if op in ('LD', 'LDI'):
                if acc is not None: stack.append(acc)
                acc = bool(self.bits.get(a[0], False))
                if op == 'LDI': acc = not acc
            elif op in ('AND', 'ANI', 'OR', 'ORI'):
                v = bool(self.bits.get(a[0], False))
                if op in ('ANI', 'ORI'): v = not v
                acc = acc and v if op in ('AND', 'ANI') else acc or v
            elif op in ('ANB', 'ORB'):
                v = stack.pop()
                acc = acc and v if op == 'ANB' else acc or v
            elif op in ('OUT', 'SET', 'RST'):
                enabled = actions if acc is None else bool(acc)
                if len(a) == 2:
                    assert op == 'OUT' and a[0] in {'T250','T251','T252','T253','T254'}
                    if enabled: self.timers[a[0]] = self.timers.get(a[0], 0) + 10
                    self.bits[a[0]] = self.timers.get(a[0], 0) >= int(a[1][1:]) * 100
                elif op == 'OUT': self.bits[a[0]] = enabled
                elif enabled:
                    self.bits[a[0]] = op == 'SET'
                    if op == 'RST' and a[0].startswith('T'): self.timers[a[0]] = 0
                acc = None
            else: raise AssertionError(str(ins))
        assert not stack
        return acc

    def tick(self, **inputs):
        self.inputs.update(inputs)
        self.bits.update(self.inputs, M8002=self.ms == 0)
        active = [n for n, s in self.steps.items() if self.bits.get(s['state'])]
        for n in active: self.execute(self.steps[n]['il'], True)
        for n in active:
            edge = next((e for e in self.edges if e['source'] == n and self.execute(e['whenIl'])), None)
            if edge:
                self.bits[self.steps[n]['state']] = False
                self.bits[self.steps[edge['target']]['state']] = True
        self.execute(self.control)
        self.ms += 10
        assert sum(bool(self.bits.get(s['state'])) for s in self.steps.values()) == 1
        assert not (self.bits['Y0'] and self.bits['Y3'])
        assert not (self.bits['Y1'] and self.bits['Y2'])
        if self.bits['M500']: assert not any(self.bits[f'Y{i}'] for i in range(4))

    def run(self, ms, **inputs):
        for _ in range(ms // 10): self.tick(**inputs)

    def start(self):
        self.tick(X0=True)
        self.tick(X0=False)
        self.tick()

    def reach(self, state, **inputs):
        for _ in range(400):
            self.tick(**inputs)
            if self.bits.get(state): return
        raise AssertionError('未到达 ' + state)


@pytest.fixture
def plc():
    path = os.environ.get('GXWORKS2_DRILLING_TEST_PROJECT')
    if not path: pytest.skip('需指定实际保存的钻孔工程')
    p = Scan(read_sfc_gxw(path)); p.tick(X3=True); p.start()
    assert p.bits['S20'] and p.bits['Y0']
    return p


def to_step(p, state):
    if state == 'S20': return
    p.reach('S21', X4=True)
    if state == 'S21': return
    p.reach('S22', X6=False)
    if state == 'S22': return
    p.reach('S23', X5=True)
    if state == 'S23': return
    p.reach('S24')
    if state == 'S24': return
    p.reach('S25', X6=True)


@pytest.mark.parametrize('state', ['S20','S21','S22','S23','S24','S25'])
def test_pause_preserves_step_and_accumulated_times(plc, state):
    to_step(plc, state); plc.run(100)
    plc.tick(X1=True)
    before = dict(plc.timers)
    plc.run(4000, X1=False)
    assert plc.bits[state] and plc.timers == before and not plc.bits['Y4']
    assert not any(plc.bits[f'Y{i}'] for i in (1,2,3))
    assert plc.bits['Y0'] == (state != 'S25')
    plc.start()
    assert plc.bits[state] and plc.bits['M0']


@pytest.mark.parametrize('state,delay,reason', [('S20',2100,'M501'), ('S22',3100,'M502')])
def test_timeout_locks_until_reset(plc, state, delay, reason):
    to_step(plc, state); plc.run(delay)
    assert plc.bits['Y4'] and plc.bits[reason]
    plc.start(); assert plc.bits['Y4']
    plc.tick(X10=True); plc.tick(X10=False); plc.tick()
    assert plc.bits['S0'] and not plc.bits['Y4'] and not plc.bits['M0']


def test_estop_latches_and_held_reset_cannot_bypass(plc):
    to_step(plc, 'S22'); plc.tick(X2=True)
    assert plc.bits['Y4']
    plc.tick(X10=True); plc.tick(X2=False); plc.run(100)
    assert plc.bits['Y4']
    plc.tick(X10=False); plc.tick(X10=True); plc.tick(X10=False); plc.tick()
    assert plc.bits['S0'] and not plc.bits['Y4']


@pytest.mark.parametrize('continuous', [False,True])
def test_cycle_mode_requires_old_workpiece_to_leave(plc, continuous):
    plc.tick(X7=continuous); to_step(plc,'S25'); plc.reach('S26')
    plc.run(1000)
    assert plc.bits['S26'] and not any(plc.bits[f'Y{i}'] for i in range(4))
    plc.reach('S0', X3=False, X4=False, X5=False)
    plc.run(100, X3=True)
    assert bool(plc.bits.get('S20')) == continuous
    if not continuous:
        plc.start(); assert plc.bits['S20']


def test_delays_are_300_and_800_milliseconds(plc):
    plc.reach('S21', X4=True)
    plc.run(290, X6=False); assert plc.bits['S21']
    plc.tick(); assert plc.bits['S22']
    plc.reach('S23', X5=True)
    plc.run(790); assert plc.bits['S23']
    plc.tick(); assert plc.bits['S24']
