"""Unsupported project formats must fail before parsing or materialization."""

import json

import pytest

from gxworks2_mcp import gxw, server


@pytest.mark.parametrize("name", ["example.gpj", "example.GPJ"])
def test_gpj_has_explicit_diagnostic_before_container_access(tmp_path, monkeypatch, name):
    path = tmp_path / name
    # Even a renamed OLE header is not sufficient evidence of format conversion.
    path.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1"))
    def forbidden(*args, **kwargs):
        pytest.fail("GPJ must not reach the OLE parser")
    monkeypatch.setattr(gxw.olefile, "OleFileIO", forbidden)
    with pytest.raises(gxw.GxwError, match=r"不支持 .gpj"):
        gxw.open_gxw(path)


def test_gpj_import_does_not_create_store(tmp_path, monkeypatch):
    def forbidden():
        pytest.fail("unsupported formats must not access the store")
    monkeypatch.setattr(server, "store", forbidden)
    result = json.loads(server.gx_gxw_open(str(tmp_path / "missing.gpj"), import_into="must-not-exist"))
    assert "error" in result
    assert ".gpj" in result["error"]
    assert not (tmp_path / "store").exists()


def test_gxw_invalid_container_keeps_format_error(tmp_path):
    path = tmp_path / "example.gxw"
    path.write_bytes(b"not an OLE container")
    with pytest.raises(gxw.GxwError, match="OLE2"):
        gxw.open_gxw(path)
