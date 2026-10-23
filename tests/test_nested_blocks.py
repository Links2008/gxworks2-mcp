"""用真值表和三菱手册指令独立核对嵌套块与连续输出。"""

from itertools import product

import pytest

from gxworks2_mcp.author import compile_rungs
from gxworks2_mcp.blocks import Contact, Invert, Parallel, build_rungs
from gxworks2_mcp.il import parse_il
from gxworks2_mcp.validate import validate_program


def evaluate_spec(node, values):
    if isinstance(node, str):
        return values[node]
    op, value = next(iter(node.items()))
    if op == 'not':
        return not values[value]
    return (all if op == 'and' else any)(evaluate_spec(x, values) for x in value)


def evaluate_rung(node, values):
    if isinstance(node, Contact):
        return bool(values[node.device]) != node.negated
    if isinstance(node, Invert):
        return not evaluate_rung(node.child, values)
    return (any if isinstance(node, Parallel) else all)(evaluate_rung(x, values) for x in node.children)


@pytest.mark.parametrize('logic', [
    {'and': [{'not': 'X0'}, {'not': 'X1'}, {'or': [
        {'and': ['X2', {'not': 'X3'}]}, {'and': ['X3', 'X4']}]}]},
    {'or': ['X0', {'and': ['X1', {'or': ['X2', {'and': ['X3', 'X4']}]}]}]},
    {'and': [{'or': ['X0', 'X1']}, {'or': [
        {'and': ['X2', 'X3']}, {'and': [{'not': 'X4'}, 'X5']}]}]},
])
def test_nested_blocks_match_all_input_combinations(logic):
    program = compile_rungs([dict(out='Y0', logic=logic)])
    rung = build_rungs(program)[0]
    assert not [x for x in validate_program(program, cpu='FX3U') if x.severity == 'error']
    for bits in product((False, True), repeat=6):
        values = dict(zip(('X0', 'X1', 'X2', 'X3', 'X4', 'X5'), bits))
        assert evaluate_rung(rung.logic, values) == evaluate_spec(logic, values), values


def test_manual_anb_orb_example_preserves_outer_block():
    program = parse_il('LD X0\nOR X1\nLD X2\nAND X3\nLDI X4\nAND X5\nORB\nOR X6\nANB\nOR X3\nOUT Y7\nEND')
    rung = build_rungs(program)[0]
    for bits in product((False, True), repeat=7):
        x = dict(zip(('X0', 'X1', 'X2', 'X3', 'X4', 'X5', 'X6'), bits))
        expected = ((x['X0'] or x['X1']) and ((x['X2'] and x['X3']) or (not x['X4'] and x['X5']) or x['X6'])) or x['X3']
        assert evaluate_rung(rung.logic, x) == expected


def test_nested_blocks_do_not_consume_mps_memory():
    program = parse_il('LD X0\nMPS\nLD X1\nLD X2\nAND X3\nORB\nANB\nOUT Y0\nMRD\nLD X4\nOR X5\nANB\nOUT Y1\nMPP\nAND X6\nOUT Y2\nEND')
    rungs = build_rungs(program)[:3]
    for bits in product((False, True), repeat=7):
        x = dict(zip(('X0', 'X1', 'X2', 'X3', 'X4', 'X5', 'X6'), bits))
        assert [evaluate_rung(r.logic, x) for r in rungs] == [
            x['X0'] and (x['X1'] or (x['X2'] and x['X3'])),
            x['X0'] and (x['X4'] or x['X5']), x['X0'] and x['X6']]


@pytest.mark.parametrize('ending', ['OUT Y0\nEND', 'STL S20\nOUT Y0\nRET\nEND'])
def test_unjoined_blocks_cannot_silently_drop_a_condition(ending):
    issues = validate_program(parse_il('LD X0\nLD X1\n' + ending), cpu='FX3U')
    assert any(x.code == 'LOGIC_STRUCTURE' for x in issues)


def test_eight_pending_ld_blocks_can_be_merged():
    program = parse_il('\n'.join([f'LD X{i}' for i in range(8)] + ['ORB'] * 7 + ['OUT Y0', 'END']))
    rung = build_rungs(program)[0]
    for active in range(8):
        assert evaluate_rung(rung.logic, {f'X{i}': i == active for i in range(8)})


def test_fx_rejects_more_than_eight_unmerged_blocks():
    program = parse_il('\n'.join([f'LD M{i}' for i in range(9)] + ['ORB'] * 8 + ['OUT Y0', 'END']))
    issues = validate_program(program, cpu='FX3U')
    assert any(x.code == 'LOGIC_STRUCTURE' and 'exceeds 8' in x.message for x in issues)


@pytest.mark.parametrize('continuation, expected', [
    ('OUT Y1', lambda x: x['X0']),
    ('AND X1\nOUT Y1', lambda x: x['X0'] and x['X1']),
    ('ANI X1\nOUT Y1', lambda x: x['X0'] and not x['X1']),
    ('INV\nOUT Y1', lambda x: not x['X0']),
    ('MPS\nAND X1\nOUT Y1\nMPP\nOUT Y2', lambda x: x['X0'] and x['X1']),
])
def test_output_keeps_condition_for_the_next_output(continuation, expected):
    from gxworks2_mcp.analysis import trace_device
    program = parse_il('LD X0\nOUT Y0\n' + continuation + '\nEND')
    rungs = {r.device: r for r in build_rungs(program)}
    for first, second in product((False, True), repeat=2):
        values = {'X0': first, 'X1': second}
        assert evaluate_rung(rungs['Y0'].logic, values) == first
        assert evaluate_rung(rungs['Y1'].logic, values) == expected(values)
    assert 'X0' in trace_device(program, 'Y1')['writes'][0]['logic']
    assert not [r for r in build_rungs(program) if r.instruction.mnemonic == 'UNUSED']


def test_manual_cascade_combines_all_preceding_contacts():
    program = parse_il('LD X0\nOUT Y4\nAND X1\nOUT Y3\nAND X2\nOUT Y2\nAND X3\nOUT Y1\nAND X4\nOUT Y0\nEND')
    rungs = build_rungs(program)[:5]
    for bits in product((False, True), repeat=5):
        values = dict(zip(('X0', 'X1', 'X2', 'X3', 'X4'), bits))
        assert [evaluate_rung(r.logic, values) for r in rungs] == [all(bits[:n]) for n in range(1, 6)]


def test_next_ld_and_stl_do_not_inherit_the_previous_output():
    program = parse_il('LD X0\nOUT Y0\nAND X1\nOUT Y1\nLD X2\nOUT Y2\nSTL S20\nOUT Y3\nLD X3\nOUT Y4\nRET\nLD X4\nOUT Y5\nEND')
    logic = {r.device: str(r.logic) for r in build_rungs(program) if r.instruction.is_output}
    assert logic == {'Y0': 'X0', 'Y1': '(X0 & X1)', 'Y2': 'X2', 'Y3': 'S20', 'Y4': '(S20 & X3)', 'Y5': 'X4'}


def test_mrd_and_mpp_still_restore_after_cascade():
    program = parse_il('LD X0\nMPS\nAND X1\nOUT Y0\nAND X2\nOUT Y1\nMRD\nAND X3\nOUT Y2\nMPP\nAND X4\nOUT Y3\nEND')
    rungs = build_rungs(program)[:4]
    for bits in product((False, True), repeat=5):
        x0, x1, x2, x3, x4 = bits
        values = dict(zip(('X0', 'X1', 'X2', 'X3', 'X4'), bits))
        assert [evaluate_rung(r.logic, values) for r in rungs] == [x0 and x1, x0 and x1 and x2, x0 and x3, x0 and x4]


def test_output_does_not_leave_unused_logic_but_following_contacts_do():
    assert [r.instruction.mnemonic for r in build_rungs(parse_il('LD X0\nOUT Y0'))] == ['OUT']
    rungs = build_rungs(parse_il('LD X0\nOUT Y0\nAND X1'))
    assert rungs[-1].instruction.mnemonic == 'UNUSED'
    assert str(rungs[-1].logic) == '(X0 & X1)'
