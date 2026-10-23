"""原生 SFC 读回后复用同一组机械手工艺验收。"""
import os
import pytest
from test_drilling import Scan
from test_handling_ladder import (test_automatic_cycle_and_one_second_release,
    test_b_occupied_waits_without_timeout_then_descends,
    test_b_obstruction_during_descent_latches_fault,
    test_pause_retains_clamp_step_and_elapsed_time,
    test_movement_timeout_and_origin_required_after_reset,
    test_estop_and_held_reset_do_not_restart, test_conflicting_limits_stop_motion)
from examples.fx3u_handling_sfc import generate
from gxworks2_mcp.native_sfc import read_sfc_gxw


class HandlingSfcScan(Scan):
    def __init__(self, doc):
        super().__init__(doc)
        self.inputs = {f'X{i:o}': False for i in range(11)}
        self.inputs.update(X5=True, X11=True)

    @property
    def step(self):
        return next(n for n, step in self.steps.items() if self.bits[step['state']])

    def start(self):
        self.tick(X0=True)
        self.tick(X0=False)

    def tick(self, **inputs):
        super().tick(**inputs)
        for a, b in [('Y0','Y1'), ('Y2','Y3'), ('Y4','Y5')]:
            assert not (self.bits[a] and self.bits[b])
        if self.bits['M500']: assert not any(self.bits[f'Y{i}'] for i in range(6))


@pytest.fixture(scope='module')
def document(tmp_path_factory):
    path = os.environ.get('GXWORKS2_HANDLING_SFC')
    if not path:
        template = os.environ.get('GXWORKS2_SFC_TEMPLATE')
        if not template: pytest.skip('需要实际SFC工程或原生模板')
        root = tmp_path_factory.mktemp('handling-sfc')
        generate(root, template, os.environ['GXWORKS2_INSTALL_ROOT'])
        path = root/'机械手自动搬运_SFC.gxw'
    return read_sfc_gxw(str(path))


@pytest.fixture
def plc(document):
    p = HandlingSfcScan(document); p.tick(); p.start()
    assert p.step == 1 and p.bits['Y0']
    return p


def test_native_graph_has_ten_steps_and_a_closed_cycle(document):
    chart = document['blocks'][0]
    assert len(chart['steps']) == len(chart['transitions']) == 10
    assert [(e['source'], e['target']) for e in chart['transitions']] == [(i,(i+1)%10) for i in range(10)]


def test_obstruction_and_lower_limit_in_same_scan(plc):
    from test_handling_ladder import at_b
    at_b(plc, False); plc.tick()
    plc.tick(X6=False, X7=True, X12=True)
    assert plc.bits['M500'] and not plc.bits['Y5']
