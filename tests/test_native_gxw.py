"""原生 GXW 写入验证：无需软件窗口、PLC 或网络。"""

import hashlib
import json
import struct
import sys
from pathlib import Path

import pytest

from gxworks2_mcp import server
from gxworks2_mcp.gxw import GxwError, open_gxw, pou_stream, main_res_program
from gxworks2_mcp.il import parse_il
from gxworks2_mcp.native import encode_program, write_gxw
from gxworks2_mcp.store import Store


windows_storage = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows IStorage / pywin32")


TEMPLATE = Path(__file__).parents[1] / "src/gxworks2_mcp/data/fx3u-ladder.gxw"


@pytest.mark.parametrize("il", [
    "LD X10\nOUT Y20\nEND",
    "LD M8002\nSET S0\nSTL S0\nOUT Y0\nLD X1\nSET S20\nSTL S20\nOUT Y1\nRET\nEND",
    "LD X0\nOUT T0 K10\nLD T0\nOUT Y0\nEND",
    "LD X0\nMOV K-10 D1000\nEND",
])
def test_native_encoding_roundtrip(il):
    tokens = encode_program(parse_il(il))
    decoded = main_res_program(struct.pack("<I", len(tokens)) + tokens, body_len_offset=0, cpu="FX3U")
    assert decoded["decoded"]
    assert decoded["il"] == il


@windows_storage
def test_native_file_preserves_template_and_other_streams(tmp_path):
    source_hash = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    path = tmp_path / "native.gxw"
    result = write_gxw(str(TEMPLATE), str(path), parse_il("LD X10\nOUT Y1\nEND"))
    assert result["ok"] and result["computerUseCalls"] == 0
    assert result["otherStreamsPreserved"] and result["sourceReadback"]
    assert hashlib.sha256(TEMPLATE.read_bytes()).hexdigest() == source_hash
    project = open_gxw(path)
    try:
        assert project.cpu == "FX3U/FX3UC"
        assert pou_stream(project.read_stream(project.find("MAIN.Program.pou")), cpu=project.cpu)["il"] == "LD X10\nOUT Y1\nEND"
    finally:
        project.close()
    with pytest.raises(GxwError, match="禁止覆盖"):
        write_gxw(str(TEMPLATE), str(path), parse_il("END"))


def test_native_writer_rejects_unknown_instruction(tmp_path):
    with pytest.raises(GxwError, match="没有已验证"):
        write_gxw(str(TEMPLATE), str(tmp_path / "bad.gxw"), parse_il("MADEUP M0\nEND"))
    assert not (tmp_path / "bad.gxw").exists()


@windows_storage
def test_mcp_direct_writer_and_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_store", Store(tmp_path / "store"))
    server.gx_program_create("native", cpu="FX3U", il="LD X10\nOUT Y0\nEND")
    result = json.loads(server.gx_write_project("native", str(tmp_path / "written.gxw"), confirm=True))
    assert result["ok"], result
    read = json.loads(server.gx_gxw_open(result["path"]))
    source = next(p for p in read["decodedPrograms"] if p["name"] == "MAIN.Program.pou")
    assert source["decoded"] and "LD X10" in source["il"]


@windows_storage
def test_chinese_notes_use_project_codepage(tmp_path):
    path = tmp_path / "chinese.gxw"
    write_gxw(str(TEMPLATE), str(path), parse_il("LD X0\nOUT Y0  // 电机启动\nEND"))
    project = open_gxw(path)
    try:
        decoded = pou_stream(project.read_stream(project.find("MAIN.Program.pou")), cpu=project.cpu, text_encoding=project.text_encoding)
        assert "电机启动" in decoded["il"]
    finally:
        project.close()


def test_xref_keeps_original_operand_positions():
    from gxworks2_mcp.analysis import where_used
    sites = where_used(parse_il("MOV D0 D1\nMOV K10 D2\nEND"))
    assert sites["D0"][0]["kind"] == "R"
    assert sites["D1"][0]["kind"] == "W"
    assert sites["D2"][0]["kind"] == "W"


def test_native_sfc_child_networks_are_not_a_complete_program():
    from test_gxw_reader import _pou_record
    source = bytes(54) + struct.pack("<I", 240) + _pou_record(bytes.fromhex("030003049c0004032003049d0004"))
    result = pou_stream(source, cpu="FX3U")
    assert not result["decoded"]
    assert "SFC" in result["stopReason"] and "LD X0" in result["partialIl"]


@windows_storage
def test_memory_storage_grows_and_shrinks_without_touching_other_streams():
    import io
    import olefile
    from gxworks2_mcp.native import _replace_cfb
    original = TEMPLATE.read_bytes()
    with olefile.OleFileIO(io.BytesIO(original)) as document:
        expected = {"/".join(n): document.openstream(n).read() for n in document.listdir()}
    for payload in (b"x" * 18000, b"small"):
        updated = _replace_cfb(original, {"history.xml": payload})
        with olefile.OleFileIO(io.BytesIO(updated)) as document:
            actual = {"/".join(n): document.openstream(n).read() for n in document.listdir()}
        assert actual == {**expected, "history.xml": payload}
        original = updated
        expected = actual


@windows_storage
def test_disk_corruption_is_rejected_and_temporary_files_are_cleaned(tmp_path, monkeypatch):
    original_write = Path.write_bytes
    def corrupt_candidate(path, payload):
        if path.name == "candidate.gxw":
            payload = b"invalid container"
        return original_write(path, payload)
    monkeypatch.setattr(Path, "write_bytes", corrupt_candidate)
    with pytest.raises(GxwError):
        write_gxw(str(TEMPLATE), str(tmp_path / "rejected.gxw"), parse_il("LD X0\nOUT Y0\nEND"))
    assert list(tmp_path.iterdir()) == []


@windows_storage
def test_large_source_crosses_sector_boundary_then_shrinks(tmp_path):
    large = parse_il("\n".join(f"LD X0\nOUT M{i}" for i in range(700)) + "\nEND")
    path = tmp_path / "large.gxw"
    assert write_gxw(str(TEMPLATE), str(path), large)["sourceReadback"]
    assert write_gxw(str(path), str(tmp_path / "small.gxw"), parse_il("END"))["otherStreamsPreserved"]


@windows_storage
def test_valid_container_with_unrelated_stream_change_is_rejected(tmp_path, monkeypatch):
    from gxworks2_mcp import native
    replace = native._replace_cfb
    def alter(data, replacements):
        if "_hdb" in replacements:
            replacements = {**replacements, "Project.gd2": b"unrelated change"}
        return replace(data, replacements)
    monkeypatch.setattr(native, "_replace_cfb", alter)
    with pytest.raises(GxwError, match="非目标"):
        write_gxw(str(TEMPLATE), str(tmp_path / "rejected.gxw"), parse_il("END"))
    assert list(tmp_path.iterdir()) == []
