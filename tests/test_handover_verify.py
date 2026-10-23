"""交付包核验应发现缺项、内容和版本不一致，且不改源文件。"""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from gxworks2_mcp import handover, server
from gxworks2_mcp.il import parse_il
from gxworks2_mcp.store import Store


@pytest.fixture
def bundle(tmp_path):
    project = Store(tmp_path / 'store').create('demo', cpu='FX3U')
    project.save(parse_il('LD X0\nOUT Y0\nEND'))
    return project, Path(handover.export_handover(project)['path'])


def rewrite(path, change, rehash=False):
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    change(files)
    if rehash:
        manifest = json.loads(files['归档清单.json'])
        manifest['files'] = {n: hashlib.sha256(b).hexdigest() for n, b in files.items() if n != '归档清单.json'}
        files['归档清单.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, 'w') as z:
        for n, b in files.items(): z.writestr(n, b)


def test_valid_archive_is_read_only_and_workspace_comparison_is_optional(bundle):
    project, path = bundle
    before = path.read_bytes(), project.il_path.read_bytes()
    result = handover.verify_handover(path)
    assert result['ok'] and result['integrityVerified'] and result['workspaceMatch'] is None
    assert result['checkedFiles'] == 10 and not result['signatureVerified']
    assert not result['simulatorAccessed'] and not result['programModified']
    assert result['acceptance'] == '待评定' and not result['programMatchVerified']
    assert handover.verify_handover(path, project)['workspaceMatch'] is True
    assert (path.read_bytes(), project.il_path.read_bytes()) == before


@pytest.mark.parametrize('change', [
    lambda f: f.pop('I_O清单.csv'),
    lambda f: f.update({'其他.txt': b'extra'}),
    lambda f: f.update({'../escape': b'no'}),
    lambda f: f.update({'程序.il': b'LD X1\nOUT Y1\nEND'}),
    lambda f: f.update({'归档清单.json': b'[]'}),
    lambda f: f.update({'归档清单.json': b'{'})])
def test_invalid_structure_or_hash_is_rejected(bundle, change):
    _, path = bundle
    rewrite(path, change)
    with pytest.raises(ValueError): handover.verify_handover(path)


@pytest.mark.parametrize('member,payload', [
    ('程序信息.json', {'project': 'demo', 'cpu': 'Q', 'programSha256': '0' * 64}),
    ('归档证据.json', [{'cpu': 'FX3U', 'programSha256': '0' * 64}]),
    ('归档证据.json', {}),
    ('程序.il', 'LD X1\nOUT Y1\nEND')])
def test_rehashed_but_internally_inconsistent_archive_is_rejected(bundle, member, payload):
    _, path = bundle
    raw = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
    rewrite(path, lambda f: f.update({member: raw}), rehash=True)
    with pytest.raises(ValueError): handover.verify_handover(path)


def test_duplicate_member_is_rejected(bundle):
    _, path = bundle
    with zipfile.ZipFile(path, 'a') as z, pytest.warns(UserWarning): z.writestr('程序.il', b'END')
    with pytest.raises(ValueError): handover.verify_handover(path)


@pytest.mark.parametrize('field,value', [('cpu', 'Q'), ('programSha256', '0' * 64)])
def test_evidence_version_is_checked_even_with_matching_count_and_file_hash(bundle, field, value):
    _, path = bundle
    def change(files):
        manifest = json.loads(files['归档清单.json'])
        entry = {'cpu': manifest['cpu'], 'programSha256': manifest['programSha256'], 'result': {'ok': True}}
        entry[field] = value
        manifest['evidenceCount'] = 1
        files['归档清单.json'] = json.dumps(manifest).encode()
        files['归档证据.json'] = json.dumps([entry]).encode()
    rewrite(path, change, rehash=True)
    with pytest.raises(ValueError, match='证据版本'): handover.verify_handover(path)


def test_uncompressed_size_limit_is_checked_before_reading_members(bundle, monkeypatch):
    _, path = bundle
    rewrite(path, lambda f: f.update({'交付说明.txt': b'x' * (16 * 1024 * 1024)}))
    monkeypatch.setattr(zipfile.ZipFile, 'read', lambda *a, **k: pytest.fail('超限包不可读取成员'))
    with pytest.raises(ValueError): handover.verify_handover(path)


def test_crc_damage_and_non_zip_are_rejected(bundle):
    _, path = bundle
    rewrite(path, lambda f: None)
    raw = path.read_bytes().replace(b'LD X0', b'LD X9', 1)
    path.write_bytes(raw)
    with pytest.raises(ValueError): handover.verify_handover(path)
    path.write_bytes(b'not a zip')
    with pytest.raises(ValueError): handover.verify_handover(path)


def test_workspace_version_mismatch_does_not_invalidate_archive_integrity(bundle):
    project, path = bundle
    project.save(parse_il('LD X1\nOUT Y0\nEND'))
    result = handover.verify_handover(path, project)
    assert not result['ok'] and result['integrityVerified'] and result['workspaceMatch'] is False
    assert result['status'] == 'workspace_mismatch'


def test_missing_file_returns_structured_mcp_failure(tmp_path):
    result = json.loads(server.gx_handover_verify(str(tmp_path / 'missing.zip')))
    assert not result['ok'] and result['readOnly'] and not result['physicalPlc']
