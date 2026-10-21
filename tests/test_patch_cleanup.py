"""Cleanup failures must not falsify the outcome of an already committed patch."""
import json
import tempfile

import pytest
import gxworks2_mcp.server as api
import gxworks2_mcp.patching as patching
from gxworks2_mcp.store import Store


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    workspace = Store(tmp_path)
    workspace.create('demo', initial_il='LD X0\nOUT Y0\nEND')
    monkeypatch.setattr(api, '_store', workspace)
    cleanup = tempfile.TemporaryDirectory.cleanup

    def fail_cleanup(self):
        cleanup(self)
        if '.patch-stage-' in self.name:
            raise PermissionError('staging cleanup blocked')

    monkeypatch.setattr(tempfile.TemporaryDirectory, 'cleanup', fail_cleanup)
    return workspace


def operation(view):
    return [{'op': 'replace', 'target_id': view['instructions'][0]['id'], 'il': 'LD X1'}]


def test_committed_patch_reports_cleanup_warning_and_exports_csv(workspace):
    view = json.loads(api.gx_program_slice('demo'))
    result = json.loads(api.gx_program_patch('demo', view['version'], operation(view)))
    assert result['applied'] is True
    assert result['csvUpdated'] is True
    assert result['cleanupWarning'] == 'staging cleanup blocked'
    assert result['version'] != view['version']
    assert workspace.get('demo').read_il().startswith('LD X1\n')


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_cleanup_does_not_replace_commit_and_rollback_error(workspace, monkeypatch, rollback_fails):
    project = workspace.get('demo')
    before = project.il_path.read_bytes()
    view = patching.read_patch_view(project)
    replace = patching.os.replace
    calls = 0

    def fail_second_commit(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('commit failed')
        if calls == 3 and rollback_fails:
            raise OSError('rollback failed')
        return replace(src, dst)

    monkeypatch.setattr(patching.os, 'replace', fail_second_commit)
    with pytest.raises(patching.PatchWriteError) as failure:
        patching.apply_patch(project, view['version'], operation(view))
    assert failure.value.rollback_complete is (not rollback_fails)
    assert isinstance(failure.value.__cause__, OSError)
    assert str(failure.value.__cause__) == 'commit failed'
    assert (project.il_path.read_bytes() == before) is (not rollback_fails)
