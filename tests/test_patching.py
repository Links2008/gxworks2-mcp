"""Offline patch regressions: all project files live under pytest temp directories."""
import json
import multiprocessing
import threading
import time

import pytest

from gxworks2_mcp.locking import project_lock
from gxworks2_mcp.patching import (PatchError, PatchWriteError, VersionConflict,
                                  apply_patch, read_patch_view)
from gxworks2_mcp.store import Store, Project


@pytest.fixture
def project(tmp_path):
    store = Store(tmp_path)
    store.create('patch', cpu='Q', initial_il='LD X0\nOUT Y0\nEND')
    return store.get('patch')


def state(project):
    return {p.name: p.read_bytes() for p in project.root.iterdir() if p.is_file()}


def replace(view, il='LD X1', index=0):
    return [{'op': 'replace', 'target_id': view['instructions'][index]['id'], 'il': il}]


def test_read_is_bounded_and_does_not_write(project):
    before = state(project)
    view = read_patch_view(project, limit=1)
    assert view['granularity'] == 'instruction'
    assert view['range'] == [0, 1] and view['nextStart'] == 1
    assert len(view['instructions']) == 1
    assert view == read_patch_view(project, limit=1)
    assert state(project) == before


def test_preserve_bytes_comments_cpu_metadata_and_ids(project):
    project.il_path.write_bytes(b'// header\r\n  LD X0 // contact\r\n\r\nOUT Y0 // output\r\nEND\r\n// footer\r\n')
    project.devices_path.write_bytes(b'device comments unchanged')
    project.notes_path.write_bytes(b'notes unchanged')
    metadata = json.loads(project.json_path.read_text())
    metadata.update(cpu='FX3U', custom='keep me')
    project.json_path.write_text(json.dumps(metadata))
    before = read_patch_view(project)
    result = apply_patch(project, before['version'], replace(before, 'LD X1 // new contact'))
    after = read_patch_view(project)
    assert project.il_path.read_bytes() == b'// header\r\nLD X1 // new contact\r\n\r\nOUT Y0 // output\r\nEND\r\n// footer\r\n'
    assert [i['id'] for i in before['instructions']] == [i['id'] for i in after['instructions']]
    assert result['version'] == after['version'] != before['version']
    assert result['cpu'] == 'FX3U'
    assert json.loads(project.json_path.read_text())['custom'] == 'keep me'
    assert project.devices_path.read_bytes() == b'device comments unchanged'
    assert project.notes_path.read_bytes() == b'notes unchanged'
    assert 'program' not in result and len(result['changes']) == 1


def test_batch_insert_delete_and_stable_ids(project):
    view = read_patch_view(project)
    ids = [i['id'] for i in view['instructions']]
    result = apply_patch(project, view['version'], [
        {'op': 'insert_after', 'target_id': ids[0], 'il': 'AND X1'},
        {'op': 'insert_before', 'target_id': ids[2], 'il': 'LD X2\nOUT Y1'}])
    after = read_patch_view(project)
    assert [i['il'] for i in after['instructions']] == ['LD X0', 'AND X1', 'OUT Y0', 'LD X2', 'OUT Y1', 'END']
    assert [after['instructions'][i]['id'] for i in (0, 2, 5)] == ids
    apply_patch(project, result['version'], [{'op': 'delete', 'target_id': after['instructions'][1]['id']}])
    assert 'AND' not in project.read_il()


@pytest.mark.parametrize('il', ['LD', 'NEXT', 'MOV D0'])
def test_parse_and_validation_failure_leave_all_files_unchanged(project, il):
    view = read_patch_view(project)
    before = state(project)
    with pytest.raises(PatchError):
        apply_patch(project, view['version'], replace(view, il))
    assert state(project) == before


def test_version_conflict_and_external_changes_invalidate_ids(project):
    view = read_patch_view(project)
    project.il_path.write_text('LD X2\nOUT Y0\nEND\n')
    before = state(project)
    with pytest.raises(VersionConflict):
        apply_patch(project, view['version'], replace(view))
    assert state(project) == before
    fresh = read_patch_view(project)
    assert fresh['instructions'][0]['id'] != view['instructions'][0]['id']
    with pytest.raises(PatchError, match='Unknown target'):
        apply_patch(project, fresh['version'], replace(view))


def test_metadata_only_change_conflicts(project):
    view = read_patch_view(project)
    meta = json.loads(project.json_path.read_text())
    meta['cpu'] = 'FX3U'
    project.json_path.write_text(json.dumps(meta))
    with pytest.raises(VersionConflict):
        apply_patch(project, view['version'], replace(view))


def test_duplicate_targets_rejected_without_write(project):
    view = read_patch_view(project)
    before = state(project)
    with pytest.raises(PatchError, match='one operation'):
        apply_patch(project, view['version'], replace(view) * 2)
    assert state(project) == before


def test_commit_failure_rolls_back(project, monkeypatch):
    import gxworks2_mcp.patching as module
    view = read_patch_view(project)
    before = state(project)
    real_replace = module.os.replace
    def fail_metadata(source, target):
        if str(source).endswith('commit-1'):
            raise OSError('injected commit failure')
        return real_replace(source, target)
    monkeypatch.setattr(module.os, 'replace', fail_metadata)
    with pytest.raises(PatchWriteError) as error:
        apply_patch(project, view['version'], replace(view))
    assert error.value.rollback_complete
    assert state(project) == before


def test_same_thread_reentrant_and_other_thread_blocked(project):
    entered = threading.Event()
    def other():
        with project_lock(project):
            entered.set()
    with project_lock(project):
        with project_lock(project):
            worker = threading.Thread(target=other)
            worker.start()
            assert not entered.wait(.1)
    worker.join(timeout=5)
    assert entered.is_set()


def _process_patch(root, version, target, barrier, results):
    from pathlib import Path
    project = Project('patch', Path(root))
    barrier.wait(timeout=10)
    try:
        apply_patch(project, version, [{'op': 'replace', 'target_id': target, 'il': 'LD X1'}])
        results.put('ok')
    except VersionConflict:
        results.put('conflict')
    except Exception as exc:
        results.put(type(exc).__name__ + ': ' + str(exc))


def test_process_race_exactly_one_writer_wins(project):
    view = read_patch_view(project)
    context = multiprocessing.get_context('spawn')
    barrier, results = context.Barrier(2), context.Queue()
    processes = [context.Process(target=_process_patch, args=(str(project.root), view['version'], view['instructions'][0]['id'], barrier, results)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert sorted([results.get(timeout=2), results.get(timeout=2)]) == ['conflict', 'ok']
    assert read_patch_view(project)['instructions'][0]['il'] == 'LD X1'


def test_delete_end_rejects_entire_batch(project):
    view = read_patch_view(project)
    before = state(project)
    with pytest.raises(PatchError, match='MISSING_END'):
        apply_patch(project, view['version'], replace(view) + [
            {'op': 'delete', 'target_id': view['instructions'][-1]['id']}])
    assert state(project) == before


def test_unknown_cpu_and_missing_final_newline_are_preserved(project):
    project.il_path.write_bytes(b'LD X0\nOUT Y0\nEND')
    metadata = json.loads(project.json_path.read_text())
    metadata['cpu'] = ''
    project.json_path.write_text(json.dumps(metadata))
    view = read_patch_view(project)
    result = apply_patch(project, view['version'], replace(view))
    assert result['cpu'] == ''
    assert project.il_path.read_bytes() == b'LD X1\nOUT Y0\nEND'
    assert json.loads(project.json_path.read_text())['cpu'] == ''


def test_malformed_operation_rejected(project):
    view = read_patch_view(project)
    before = state(project)
    for operation in [{'op': [], 'target_id': view['instructions'][0]['id']},
                      {'op': 'replace', 'target_id': []}, None]:
        with pytest.raises(PatchError):
            apply_patch(project, view['version'], [operation])
    assert state(project) == before


def test_lock_survives_project_deletion(project):
    import shutil
    entered = threading.Event()
    def other():
        with project_lock(project):
            entered.set()
    with project_lock(project):
        shutil.rmtree(project.root)
        project.root.mkdir()
        worker = threading.Thread(target=other)
        worker.start()
        assert not entered.wait(.1)
    worker.join(timeout=5)
    assert entered.is_set()


def test_commit_rollback_failure_is_explicit(project, monkeypatch):
    import gxworks2_mcp.patching as module
    view = read_patch_view(project)
    real_replace = module.os.replace
    def fail_metadata_and_rollback(source, target):
        if str(source).endswith('commit-1') or 'rollback-' in str(source):
            raise OSError('injected failure')
        return real_replace(source, target)
    monkeypatch.setattr(module.os, 'replace', fail_metadata_and_rollback)
    with pytest.raises(PatchWriteError) as error:
        apply_patch(project, view['version'], replace(view))
    assert error.value.rollback_complete is False
    assert project.read_il().startswith('LD X1')
def test_temporary_project_locks_are_not_cached_forever(tmp_path):
    import gc
    from gxworks2_mcp import locking
    from gxworks2_mcp.store import Project
    gc.collect()
    before = len(locking._locks)
    for number in range(30):
        with locking.project_lock(Project(str(number), tmp_path / str(number))):
            pass
    gc.collect()
    assert len(locking._locks) <= before

