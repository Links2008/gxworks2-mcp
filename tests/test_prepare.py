import json
from pathlib import Path
from unittest.mock import patch

import pytest

import gxworks2_mcp.server as api
from gxworks2_mcp.csvio import read_program_csv
from gxworks2_mcp.il import parse_il
from gxworks2_mcp.store import Project, Store, diff_programs


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    instance = Store(tmp_path)
    monkeypatch.setattr(api, '_store', instance)
    return instance


def test_prepare_batch_is_one_save_and_compact(workspace, monkeypatch):
    original = Project.save
    calls = []
    def save(project, program, **kwargs):
        calls.append(len(program))
        return original(project, program, **kwargs)
    monkeypatch.setattr(Project, 'save', save)
    rungs = [{'out': f'M{i}', 'logic': 'X1'} for i in range(100)]
    result = json.loads(api.gx_program_prepare('batch', 'FX3U', rungs=rungs))
    assert result['prepared'] and not result['imported']
    assert result['validation']['ok']
    assert calls == [201]
    assert 'il' not in result and 'ascii' not in result
    assert result['instructions'] == len(read_program_csv(result['csv']).rows)
    assert Path(result['csv']).read_bytes().startswith(b'\xff\xfe')
    assert 'FX3U' in read_program_csv(result['csv']).banner[1][1]


def test_prepare_stl_sequence_preserves_instructions(workspace):
    il = 'STL S0\nOUT Y1\nLD X1\nOUT S1\nSTL S1\nOUT Y2\nRET\nEND'
    result = json.loads(api.gx_program_prepare('sequence', 'FX3U', il=il, preview=True))
    assert result['prepared'] and result['validation']['ok']
    assert result['il'] == il
    assert read_program_csv(result['csv']).to_il() == il
    assert workspace.get('sequence').cpu == 'FX3U'


@pytest.mark.parametrize('kwargs', [
    {}, {'il': 'LD X1'}, {'il': 'OUT'},
    {'il': 'LD X1\nOUT Y1\nEND', 'rungs': [{'out': 'Y2'}]},
    {'il': 'LD X1\nOUT Y1\nEND', 'locale': 'xx'},
    {'rungs': [{'bad': 'Y1'}]},
])
def test_invalid_input_does_not_create_project(workspace, kwargs):
    result = json.loads(api.gx_program_prepare('bad', 'FX3U', **kwargs))
    assert not result.get('prepared')
    assert not workspace.project_path('bad').exists()


def test_validation_failure_preserves_existing_program_and_csv(workspace):
    first = json.loads(api.gx_program_prepare('keep', 'FX3U', il='LD X1\nOUT Y1\nEND'))
    project = workspace.get('keep')
    files = [project.il_path, project.json_path, Path(first['csv'])]
    before = [p.read_bytes() for p in files]
    failed = json.loads(api.gx_program_prepare('keep', 'FX3U', il='LD X1', overwrite=True))
    assert failed['prepared'] is False
    assert before == [p.read_bytes() for p in files]


def test_existing_project_requires_explicit_overwrite(workspace):
    api.gx_program_prepare('keep', 'FX3U', il='LD X1\nOUT Y1\nEND')
    failed = json.loads(api.gx_program_prepare('keep', 'FX3U', il='LD X2\nOUT Y2\nEND'))
    assert 'error' in failed
    assert 'X1' in workspace.get('keep').read_il()
    result = json.loads(api.gx_program_prepare('keep', 'FX3U', il='LD X2\nOUT Y2\nEND', overwrite=True))
    assert result['prepared']
    assert 'X2' in workspace.get('keep').read_il()


def test_warnings_remain_visible(workspace):
    result = json.loads(api.gx_program_prepare('warn', 'Q', il='LD X1\nOUT Y1\nLD X2\nOUT Y1\nEND'))
    assert result['validation']['warnings'] > 0
    assert result['validation']['issues']


@pytest.mark.parametrize('cpu', ['', 'FX', 'unknown'])
def test_prepare_requires_a_known_cpu_for_limits(workspace, cpu):
    result = json.loads(api.gx_program_prepare('unknown', cpu, il='LD X1\nOUT Y1\nEND'))
    assert 'error' in result
    assert not workspace.project_path('unknown').exists()


def test_identical_repetitive_program_skips_quadratic_matcher():
    program = parse_il(('LD X1\nOUT Y1\n' * 2500) + 'END')
    with patch('gxworks2_mcp.store.SequenceMatcher', side_effect=AssertionError('must not run')):
        assert diff_programs(program, program)['changes'] == []


def test_csv_export_failure_leaves_existing_files_unchanged(workspace):
    result = json.loads(api.gx_program_prepare('keep', 'FX3U', il='LD X1\nOUT Y1\nEND'))
    project = workspace.get('keep')
    paths = [project.il_path, project.json_path, Path(result['csv'])]
    before = [p.read_bytes() for p in paths]
    with patch.object(api, 'write_program_csv', side_effect=OSError('disk full')):
        failed = json.loads(api.gx_program_prepare('keep', 'FX3U', il='LD X2\nOUT Y2\nEND', overwrite=True))
    assert not failed['prepared'] and not failed['projectMayBeModified']
    assert failed['doNotImport'] and failed['phase'] == 'csv_export'
    assert before == [p.read_bytes() for p in paths]


def test_csv_publish_failure_explicitly_reports_partial_state(workspace):
    api.gx_program_prepare('keep', 'FX3U', il='LD X1\nOUT Y1\nEND')
    with patch.object(api.os, 'replace', side_effect=OSError('permission denied')):
        failed = json.loads(api.gx_program_prepare('keep', 'FX3U', il='LD X2\nOUT Y2\nEND', overwrite=True))
    assert not failed['prepared'] and failed['projectMayBeModified']
    assert failed['doNotImport'] and not failed['csvUpdated']
    assert failed['phase'] == 'csv_publish' and 'csv' not in failed
