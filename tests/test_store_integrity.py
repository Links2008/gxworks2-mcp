import json

import pytest

from gxworks2_mcp.il import parse_il
from gxworks2_mcp.store import Store, StoreError, diff_programs
from gxworks2_mcp.validate import validate_program


@pytest.mark.parametrize('cpu', ['FX3U', 'FX5U', 'L', 'Q03UDECPU', ''])
def test_cpu_survives_reopen_and_save(tmp_path, cpu):
    Store(tmp_path).create('project', cpu=cpu, initial_il='LD X1\nOUT Y1\nEND')
    reopened = Store(tmp_path).get('project')
    assert reopened.cpu == cpu
    reopened.save(reopened.read_program())
    assert json.loads(reopened.json_path.read_text())['cpu'] == cpu


def test_reopened_fx_keeps_cpu_specific_validation(tmp_path):
    Store(tmp_path).create('fx', cpu='FX3U', initial_il='LD M7680\nOUT Y1\nEND')
    project = Store(tmp_path).get('fx')
    issues = validate_program(project.read_program(), cpu=project.cpu)
    assert any('FX3U' in issue.message for issue in issues)


@pytest.mark.parametrize('metadata', ['not json', '[]', '{"cpu": null}', '{"cpu": 7}', '{"cpu": " "}'])
def test_corrupt_metadata_is_not_silently_replaced(tmp_path, metadata):
    project = Store(tmp_path).create('bad')
    project.json_path.write_text(metadata)
    original_il = project.il_path.read_bytes()
    with pytest.raises(StoreError, match='metadata'):
        Store(tmp_path).get('bad')
    assert project.json_path.read_text() == metadata
    assert project.il_path.read_bytes() == original_il


@pytest.mark.parametrize('metadata', [None, '{}'])
def test_legacy_metadata_defaults_to_q(tmp_path, metadata):
    project = Store(tmp_path).create('legacy')
    if metadata is None:
        project.json_path.unlink()
    else:
        project.json_path.write_text(metadata)
    assert Store(tmp_path).get('legacy').cpu == 'Q'


def test_diff_counts_duplicate_removal():
    result = diff_programs(parse_il('LD X1\nOUT Y1\nOUT Y1\nEND'), parse_il('LD X1\nOUT Y1\nEND'))
    assert result['removed'] == ['OUT Y1']
    assert result['added'] == []
    assert result['changes'][0]['operation'] == 'delete'


def test_diff_counts_duplicate_addition():
    result = diff_programs(parse_il('LD X1\nOUT Y1\nEND'), parse_il('LD X1\nOUT Y1\nOUT Y1\nEND'))
    assert result['added'] == ['OUT Y1']
    assert result['removed'] == []


def test_diff_reports_instruction_reordering():
    result = diff_programs(parse_il('LD X1\nOUT Y1\nEND'), parse_il('OUT Y1\nLD X1\nEND'))
    assert result['added'] and result['removed']
    assert result['before'] == result['after'] == 3


def test_diff_reports_label_changes():
    result = diff_programs(parse_il('P1:\nEND'), parse_il('P2:\nEND'))
    assert result['removed'] == ['P1: NOP']
    assert result['added'] == ['P2: NOP']


def test_diff_ignores_comments_and_steps_but_not_instructions():
    result = diff_programs(parse_il('0 LD X1 // old\n1 OUT Y1'), parse_il('LD X1 // new\nOUT Y1'))
    assert result == {'added': [], 'removed': [], 'before': 2, 'after': 2, 'changes': []}
