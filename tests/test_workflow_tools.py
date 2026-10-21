import json
from unittest.mock import patch

import pytest
import gxworks2_mcp.server as api
from gxworks2_mcp.store import Store
from gxworks2_mcp.csvio import read_program_csv


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    instance = Store(tmp_path)
    monkeypatch.setattr(api, '_store', instance)
    instance.create('demo', cpu='FX3U', initial_il='LD X1\nOUT Y1\nEND')
    return instance


def test_patch_tool_refreshes_csv_and_rejects_old_version(workspace):
    view = json.loads(api.gx_program_slice('demo'))
    op = {'op': 'insert_before', 'target_id': view['instructions'][1]['id'], 'il': 'ANI X2'}
    result = json.loads(api.gx_program_patch('demo', view['version'], [op]))
    assert result['applied'] and result['csvUpdated'] and not result['imported']
    assert read_program_csv(result['csv']).to_il() == 'LD X1\nANI X2\nOUT Y1\nEND'
    before = workspace.get('demo').read_il()
    stale = json.loads(api.gx_program_patch('demo', view['version'], [op]))
    assert not stale['applied'] and stale['conflict']
    assert before == workspace.get('demo').read_il()


def test_patch_export_failure_reports_applied_but_not_importable(workspace):
    view = json.loads(api.gx_program_slice('demo'))
    op = {'op': 'replace', 'target_id': view['instructions'][0]['id'], 'il': 'LD X2'}
    with patch.object(api, 'write_program_csv', side_effect=OSError('disk full')):
        result = json.loads(api.gx_program_patch('demo', view['version'], [op]))
    assert result['applied'] and not result['csvUpdated'] and result['doNotImport']
    assert 'csv' not in result and 'X2' in workspace.get('demo').read_il()


def test_sfc_tool_is_explicitly_not_a_runnable_compiler():
    table = {'cpu': 'FX3U', 'initial': 'idle', 'startup': 'restart_initial',
             'reset': {'when': 'X007', 'mode': 'all_off_then_initial'},
             'steps': [{'id': 'idle', 'state': 'S0', 'actions': [],
                        'transition': {'when': 'X001', 'to': 'work'}},
                       {'id': 'work', 'state': 'S20', 'actions': ['Y001']}]}
    result = json.loads(api.gx_sfc_preview(table))
    assert result['valid'] and result['runnable'] is False
    assert 'il' not in result and 'csv' not in result
    assert result['blockingReasons']


def test_sfc_invalid_table_returns_a_structured_error():
    result = json.loads(api.gx_sfc_preview({'cpu': 'FX3U'}))
    assert not result['valid'] and not result['runnable']
