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


def test_concurrent_prepare_cannot_publish_csv_from_an_older_program(workspace, monkeypatch):
    from threading import Event, Thread
    first_at_publish, release_first, second_started, second_done = (Event() for _ in range(4))
    original_replace = api.os.replace
    results = {}
    def replace(source, target):
        if not first_at_publish.is_set():
            first_at_publish.set()
            assert release_first.wait(5)
        return original_replace(source, target)
    monkeypatch.setattr(api.os, 'replace', replace)
    def first():
        results['first'] = json.loads(api.gx_program_prepare('race', 'FX3U', il='LD X1\nOUT Y1\nEND'))
    def second():
        second_started.set()
        results['second'] = json.loads(api.gx_program_prepare('race', 'FX3U', il='LD X2\nOUT Y2\nEND', overwrite=True))
        second_done.set()
    a, b = Thread(target=first), Thread(target=second)
    a.start()
    assert first_at_publish.wait(5)
    b.start()
    assert second_started.wait(5)
    try:
        assert not second_done.wait(0.2), 'second writer must wait until first publishes CSV'
    finally:
        release_first.set()
        a.join(5)
        b.join(5)
    assert not a.is_alive() and not b.is_alive()
    assert results['first']['prepared'] and results['second']['prepared']
    assert read_program_csv(results['second']['csv']).to_il() == workspace.get('race').read_il().strip()


def test_export_csv_reports_physical_rows_and_preserves_cpu(workspace):
    import csv
    project = workspace.create('export', cpu='FX3U', initial_il='LD X1\nMOV K1 D1\nEND')
    result = json.loads(api.gx_program_export_csv('export'))
    with open(result['written'], encoding='utf-16') as handle:
        rows = list(csv.reader(handle, delimiter='\t'))
    assert result['rows'] == len(rows) == 7
    assert result['instructions'] == 3
    assert rows[1][1] == 'FX3U'


def test_cleanup_failure_does_not_misreport_successful_prepare(workspace, monkeypatch):
    factory = api.tempfile.TemporaryDirectory
    def temporary(*args, **kwargs):
        instance = factory(*args, **kwargs)
        original = instance.cleanup
        def cleanup():
            original()
            raise OSError('staging cleanup failed')
        instance.cleanup = cleanup
        return instance
    monkeypatch.setattr(api.tempfile, 'TemporaryDirectory', temporary)
    result = json.loads(api.gx_program_prepare('clean', 'FX3U', il='LD X1\nOUT Y1\nEND'))
    assert result['prepared']
    assert result['cleanupWarnings'] == ['staging cleanup failed']
    assert read_program_csv(result['csv']).to_il() == workspace.get('clean').read_il().strip()
