"""Versioned, instruction-granularity offline IL edits.

IDs survive edits made here, not external rewrites. No rung boundaries are inferred.
The advisory lock serializes cooperating writers (including Store.save) on
Windows/POSIX only: editors and other non-cooperating writers are NOT protected. Multi-file replacement
is rollback-on-error, not crash-atomic; this is not a PLC download transaction.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from .locking import project_lock
from .il import parse_il, parse_line
from .store import Project
from .validate import summarize, validate_program


class PatchError(ValueError):
    """Rejected patch; program files were not changed."""


class VersionConflict(PatchError):
    pass


class PatchWriteError(OSError):
    """A commit failed; inspect rollback_complete before reading/retrying."""

    def __init__(self, message: str, *, rollback_complete: bool):
        super().__init__(message)
        self.rollback_complete = rollback_complete



def _snapshot(project):
    raw = project.il_path.read_bytes()
    metadata_raw = project.json_path.read_bytes() if project.json_path.exists() else b''
    try:
        metadata = json.loads(metadata_raw) if metadata_raw else {}
        if not isinstance(metadata, dict):
            raise ValueError('metadata must be an object')
        cpu = metadata.get('cpu', project.cpu)
        if not isinstance(cpu, str) or (cpu and not cpu.strip()):
            raise ValueError('invalid CPU metadata')
        text = raw.decode('utf-8')
        parse_il(text)
    except (ValueError, UnicodeError) as exc:
        raise PatchError(f'Unreadable program or metadata: {exc}') from exc
    version = hashlib.sha256(len(raw).to_bytes(8, 'big') + raw + metadata_raw).hexdigest()
    return raw, metadata_raw, metadata, cpu, version


def _index(project, raw, version):
    lines = raw.decode('utf-8').splitlines(keepends=True)
    positions = [i for i, line in enumerate(lines) if parse_line(line) is not None]
    path = project.root / '.patch-ids.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        ids = data['ids']
        valid = (isinstance(ids, list) and data['version'] == version and len(ids) == len(positions)
                 and all(isinstance(x, str) and x for x in ids) and len(set(ids)) == len(ids))
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        # Deterministic within an unmodified snapshot; no read-side write needed.
        ids = [hashlib.sha256(f'{version}:{i}'.encode()).hexdigest()[:24] for i in positions]
    return lines, positions, ids


def read_patch_view(project: Project, *, start: int = 0, limit: int = 50) -> dict:
    """Return a bounded instruction slice, not guessed ladder rungs (0-based range)."""
    if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise PatchError('start must be nonnegative and limit must be 1..200')
    with project_lock(project):
        raw, _, _, cpu, version = _snapshot(project)
        lines, positions, ids = _index(project, raw, version)
        end = min(start + limit, len(ids))
        return {'project': project.name, 'version': version, 'cpu': cpu,
                'granularity': 'instruction', 'range': [start, max(start, end)],
                'total': len(ids), 'nextStart': end if end < len(ids) else None,
                'instructions': [{'id': ids[i], 'index': i, 'line': positions[i] + 1,
                                  'il': lines[positions[i]].rstrip('\r\n')}
                                 for i in range(start, end)],
                'concurrency': 'advisory; non-cooperating writers are not protected'}


def apply_patch(project: Project, expected_version: str, operations: list[dict]) -> dict:
    """Apply an original-snapshot-anchored batch, rejecting duplicate targets.

Operations have op (insert_before/insert_after/replace/delete), target_id and il
(required except delete). Replacements retain the first instruction's target ID;
new instructions receive new IDs. Non-instruction lines outside targets stay exact.
"""
    if not isinstance(expected_version, str) or not expected_version:
        raise PatchError('expected_version is required')
    if not isinstance(operations, list) or not operations or len(operations) > 200:
        raise PatchError('operations must contain 1..200 items')
    with project_lock(project):
        raw, meta_raw, metadata, cpu, version = _snapshot(project)
        if version != expected_version:
            raise VersionConflict('Version changed; read a fresh instruction slice before retrying')
        lines, positions, ids = _index(project, raw, version)
        anchors = dict(zip(ids, positions))
        line_ids = dict(zip(positions, ids))
        changes, edits, seen = [], {}, set()
        newline = '\r\n' if b'\r\n' in raw else '\n'
        for operation in operations:
            if not isinstance(operation, dict):
                raise PatchError('Each operation must be an object')
            op, target = operation.get('op'), operation.get('target_id')
            if not isinstance(target, str) or target not in anchors:
                raise PatchError('Unknown target_id; read a fresh instruction slice')
            if target in seen:
                raise PatchError('Only one operation per target is allowed in a batch')
            seen.add(target)
            if not isinstance(op, str) or op not in {'insert_before', 'insert_after', 'replace', 'delete'}:
                raise PatchError('Unsupported patch operation')
            fragment = operation.get('il', '')
            if not isinstance(fragment, str) or (op != 'delete' and not fragment.strip()):
                raise PatchError('Non-delete operations require nonempty il')
            if op == 'delete' and fragment:
                raise PatchError('delete must not include il')
            try:
                parsed = parse_il(fragment)
            except ValueError as exc:
                raise PatchError(f'Invalid patch IL: {exc}') from exc
            if op != 'delete' and not len(parsed):
                raise PatchError('Fragment must contain at least one instruction')
            added = fragment.splitlines(keepends=True)
            if added and not added[-1].endswith(('\n', '\r')):
                added[-1] += newline
            edits[anchors[target]] = (op, added, target)
            changes.append({'op': op, 'target_id': target,
                            'removed': lines[anchors[target]].rstrip('\r\n') if op in {'replace', 'delete'} else '',
                            'added': fragment, 'addedIds': []})
        output, new_ids = [], []
        changes_by_target = {change['target_id']: change for change in changes}
        def emit(chunk, first_id=None):
            start_id = len(new_ids)
            first = True
            for line in chunk:
                output.append(line)
                if parse_line(line) is not None:
                    new_ids.append(first_id if first and first_id else uuid.uuid4().hex)
                    first = False
            return new_ids[start_id:]
        for i, line in enumerate(lines):
            edit = edits.get(i)
            if not edit:
                output.append(line)
                if i in line_ids:
                    new_ids.append(line_ids[i])
                continue
            op, added, target = edit
            if op == 'insert_before':
                changes_by_target[target]['addedIds'] = emit(added)
            if op in {'insert_before', 'insert_after'}:
                output.append(line)
                new_ids.append(target)
            if op == 'insert_after':
                if not output[-1].endswith(('\r', '\n')):
                    output[-1] += newline
                changes_by_target[target]['addedIds'] = emit(added)
            elif op == 'replace':
                changes_by_target[target]['addedIds'] = emit(added, target)
        new_raw = ''.join(output).encode('utf-8')
        try:
            program = parse_il(new_raw.decode('utf-8'), title=metadata.get('title', project.name))
            validation = summarize(validate_program(program, cpu=cpu))
        except ValueError as exc:
            raise PatchError(f'Invalid resulting program: {exc}') from exc
        if not validation['ok']:
            raise PatchError('Full-program validation failed: ' + json.dumps(validation, ensure_ascii=False))
        # Derive metadata in isolation; never normalize or rewrite the original IL.
        with tempfile.TemporaryDirectory(prefix='.patch-stage-', dir=project.root) as directory:
            staged = Project(project.name, Path(directory) / 'staged', cpu=cpu)
            staged.save(program)
            merged = dict(metadata)
            merged.update(json.loads(staged.json_path.read_text(encoding='utf-8')))
            new_meta = (json.dumps(merged, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
            new_version = hashlib.sha256(len(new_raw).to_bytes(8, 'big') + new_raw + new_meta).hexdigest()
            new_index = json.dumps({'version': new_version, 'ids': new_ids}).encode('utf-8')
            index_path = project.root / '.patch-ids.json'
            old_index = index_path.read_bytes() if index_path.exists() else None
            staged_files = [(project.il_path, new_raw, raw),
                            (project.json_path, new_meta, meta_raw if project.json_path.exists() else None),
                            (index_path, new_index, old_index)]
            # Detect external changes already visible before commit, not a substitute for cooperation.
            if _snapshot(project)[-1] != version:
                raise VersionConflict('Program changed during patch preparation')
            committed = []
            try:
                for number, (path, content, old) in enumerate(staged_files):
                    candidate = Path(directory) / f'commit-{number}'
                    candidate.write_bytes(content)
                    os.replace(candidate, path)
                    committed.append((path, old))
            except OSError as exc:
                restored = True
                for number, (path, old) in enumerate(reversed(committed)):
                    try:
                        if old is None:
                            path.unlink(missing_ok=True)
                        else:
                            backup = Path(directory) / f'rollback-{number}'
                            backup.write_bytes(old)
                            os.replace(backup, path)
                    except OSError:
                        restored = False
                raise PatchWriteError('Patch commit failed; verify files before retrying', rollback_complete=restored) from exc
        return {'project': project.name, 'beforeVersion': version, 'version': new_version,
                'granularity': 'instruction', 'changes': changes, 'validation': validation,
                'instructionCount': len(program), 'cpu': cpu}
