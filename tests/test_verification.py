import pytest
import json
from pathlib import Path

from gxworks2_mcp.il import parse_il
from gxworks2_mcp.verification import compare_programs


@pytest.fixture
def stored(tmp_path, monkeypatch):
    import gxworks2_mcp.server as api
    from gxworks2_mcp.store import Store
    instance = Store(tmp_path)
    monkeypatch.setattr(api, '_store', instance)
    return instance.create('expected', cpu='FX3U', initial_il='LD X1\nOUT Y1\nEND')


def test_same_step_count_different_content_is_not_match():
    result = compare_programs(parse_il('LD X1\nOUT Y1\nEND'), parse_il('LD X2\nOUT Y1\nEND'))
    assert not result['matches']
    assert result['differenceCount'] == 1
    assert result['differences'][0]['instructionIndex'] == 1


def test_equal_instructions_ignore_display_steps_and_identifier_case():
    assert compare_programs(parse_il('LD X1\nOUT Y1\nEND'),
                            parse_il('0 ld x1\n3 out y1\n5 end'))['matches']


@pytest.mark.parametrize('actual', ['LD X1\nMOV K1 D2\nEND', 'LD X1\nMOV D1 K1\nEND',
                                    'LD X1\nMOV K1 D1\nMOV K1 D1\nEND'])
def test_operand_change_order_and_duplicates_are_compared(actual):
    assert not compare_programs(parse_il('LD X1\nMOV K1 D1\nEND'), parse_il(actual))['matches']


def test_labels_and_comments_are_not_silently_ignored():
    assert not compare_programs(parse_il('P1:\nEND'), parse_il('P2:\nEND'))['matches']
    before, after = parse_il('LD X1 // start\nEND'), parse_il('LD X1 // stop\nEND')
    assert not compare_programs(before, after)['matches']
    assert compare_programs(before, after, compare_comments=False)['matches']


def test_mismatch_output_is_bounded_and_total_is_accurate():
    result = compare_programs(parse_il('LD X1\n'*100), parse_il('LD X2\n'*100), limit=3)
    assert result['differenceCount'] == 100
    assert len(result['differences']) == 3 and result['truncated']


def test_provided_csv_match_does_not_claim_live_verification(stored, tmp_path):
    import gxworks2_mcp.server as api
    from gxworks2_mcp.csvio import write_program_csv
    path = write_program_csv(stored.read_program(), tmp_path / 'provided.csv')
    result = json.loads(api.gx_verify_import('expected', str(path)))
    assert result['matches'] and not result['verified']
    assert not result['liveReadback'] and not result['hardwareValidated']


def test_fresh_ui_export_is_compared_even_when_instruction_count_is_same(stored, monkeypatch):
    import gxworks2_mcp.server as api
    from gxworks2_mcp import ui
    from gxworks2_mcp.csvio import write_program_csv
    def export(path, **kwargs):
        assert not Path(path).exists()
        write_program_csv(parse_il('LD X2\nOUT Y1\nEND'), path)
        return {'ok': True}
    monkeypatch.setattr(ui, 'export_program_csv', export)
    result = json.loads(api.gx_verify_import('expected'))
    assert result['liveReadback'] and not result['verified'] and not result['matches']


def test_matching_fresh_export_and_stale_expected_detection(stored, monkeypatch):
    import gxworks2_mcp.server as api
    from gxworks2_mcp import ui
    from gxworks2_mcp.csvio import write_program_csv
    def export(path, **kwargs):
        write_program_csv(stored.read_program(), path)
        return {'ok': True}
    monkeypatch.setattr(ui, 'export_program_csv', export)
    assert json.loads(api.gx_verify_import('expected'))['verified']
    def stale_export(path, **kwargs):
        write_program_csv(stored.read_program(), path)
        stored.save(parse_il('LD X2\nOUT Y2\nEND'))
        return {'ok': True}
    monkeypatch.setattr(ui, 'export_program_csv', stale_export)
    result = json.loads(api.gx_verify_import('expected'))
    assert not result['verified'] and 'changed' in result['error']


def test_export_claim_without_file_is_not_success(stored, monkeypatch):
    import gxworks2_mcp.server as api
    from gxworks2_mcp import ui
    monkeypatch.setattr(ui, 'export_program_csv', lambda *args, **kwargs: {'ok': True})
    result = json.loads(api.gx_verify_import('expected'))
    assert not result['verified'] and 'not produced' in result['error']
