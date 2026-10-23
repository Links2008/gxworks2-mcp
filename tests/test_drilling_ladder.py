"""同一组工艺要求直接运行实际读回的普通梯形图指令。"""
import os
from pathlib import Path
import pytest
from examples.fx3u_drilling_ladder import ladder_program
from test_drilling import Scan, to_step, test_pause_preserves_step_and_accumulated_times, test_timeout_locks_until_reset, test_estop_latches_and_held_reset_cannot_bypass, test_cycle_mode_requires_old_workpiece_to_leave, test_delays_are_300_and_800_milliseconds


class LadderScan(Scan):
    def __init__(self, il):
        self.il = '\n'.join(line for line in il.splitlines() if line != 'END')
        self.bits, self.timers, self.ms = {}, {}, 0
        self.inputs = {**{f'X{i}': False for i in range(8)}, 'X10': False, 'X6': True}
    def tick(self, **inputs):
        self.inputs.update(inputs); self.bits.update(self.inputs, M8002=self.ms == 0)
        self.execute(self.il); self.ms += 10
        for i, state in enumerate(['S0','S20','S21','S22','S23','S24','S25','S26']):
            self.bits[state] = self.bits.get(f'M{200+i}', False)
        assert sum(bool(self.bits.get(f'M{200+i}')) for i in range(8)) == 1
        assert not (self.bits['Y0'] and self.bits['Y3'])
        assert not (self.bits['Y1'] and self.bits['Y2'])
        if self.bits['M500']: assert not any(self.bits[f'Y{i}'] for i in range(4))


def test_each_rung_has_at_most_four_series_contacts_and_a_note():
    depth, stack = None, []
    for ins in ladder_program().instructions:
        op = ins.mnemonic
        if op in ('LD', 'LDI'):
            if depth is not None: stack.append(depth)
            depth = 1
        elif op in ('AND', 'ANI'): depth += 1
        elif op in ('OR', 'ORI'): depth = max(depth, 1)
        elif op in ('ANB', 'ORB'):
            other = stack.pop()
            depth = depth + other if op == 'ANB' else max(depth, other)
        elif op in ('OUT', 'SET', 'RST'):
            assert depth <= 4, (ins.operands, depth)
            assert ins.comment, ins.operands
            depth = None
    assert not stack


@pytest.fixture
def plc():
    path = os.environ.get('GXWORKS2_LADDER_TEST_IL')
    if not path: pytest.skip('需要实际读回的梯形图指令表')
    p = LadderScan(Path(path).read_text(encoding='utf-8'))
    p.tick(X3=True); p.start(); assert p.bits['S20'] and p.bits['Y0']
    return p
