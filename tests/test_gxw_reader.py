"""Tests for the .gxw direct reader (no GX Works2 process required).

Unit tests run anywhere. The FX3U/Q real-project checks skip when the local sample
.gxw files are absent (they are user data, not committed fixtures).
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gxworks2_mcp.gxw import GxwError, open_gxw, pou_anchors  # noqa: E402

SAMPLE_FX = r"D:\Codex\2026-09-15\en\FX3U_Manipulator_SFC.gxw"
SAMPLE_FX_B = r"D:\Codex\2026-09-15\en\FX3U_Manipulator_SFC123456.gxw"
SAMPLE_FX_C = r"D:\Codex\2026-09-15\en\FX3U_Manipulator_SFC123456_Pre_S0_S14_Backup.gxw"
_have_samples = all(os.path.exists(p) for p in (SAMPLE_FX, SAMPLE_FX_B, SAMPLE_FX_C))
samples = pytest.mark.skipif(not _have_samples, reason="local .gxw samples not present")


def _synthetic(records):
    out = bytearray()
    for body in records:
        n = len(body)
        out += struct.pack("<I", 1)
        out += struct.pack("<II", n, n)
        out += struct.pack("<II", 1, 12)
        out += b"\x00\x00\x00\x00"
        out += b"\xff\xff\xff\xff"
        out += body
        out += b"DEADBEEF"[:4]
    return bytes(out)


def test_pou_anchors_finds_synthetic_records():
    info = pou_anchors(_synthetic([b"hello", "Block1".encode("utf-16-le"), b"xyz"]))
    assert info["anchorCount"] == 3
    assert info["firstSizes"][:3] == [5, 12, 3]
    assert sum(info["firstSizes"]) == info["totalPayloadBytes"]


def test_pou_anchors_empty_on_noise():
    assert pou_anchors(bytes(range(256)) * 8)["anchorCount"] == 0


def test_pou_anchors_notes_framing_only():
    assert "REA" in pou_anchors(_synthetic([b"a"]))["note"]


def test_open_gxw_rejects_missing_file():
    with pytest.raises(GxwError):
        open_gxw("no_such_file.gxw")


def test_open_gxw_rejects_non_ole(tmp_path):
    p = tmp_path / "x.gxw"
    p.write_bytes(b"PK\x03\x04not an ole2 file")
    with pytest.raises(GxwError):
        open_gxw(str(p))


@samples
def test_inventory_reads_real_project():
    project = open_gxw(SAMPLE_FX)
    try:
        s = project.summary()
        assert s["projectName"] == "FX3U_Manipulator_SFC"
        assert s["dataFiles"] > 40
        names = [f.name for f in project.data_files]
        assert "MAIN.res" in names
        assert any(n.endswith(".pou") for n in names)
        assert any(n.endswith(".tsk") for n in names)
    finally:
        project.close()


@samples
def test_stream_ids_match_inventory():
    project = open_gxw(SAMPLE_FX)
    try:
        res = project.find("MAIN.res")
        assert res is not None and res.stream_id is not None
        body = project.read_stream(res)
        assert len(body) == res.size > 0
    finally:
        project.close()


@samples
def test_pou_anchors_on_real_pou():
    project = open_gxw(SAMPLE_FX)
    try:
        block = project.find("Block.Program.pou")
        assert block is not None
        assert pou_anchors(project.read_stream(block))["anchorCount"] > 0
    finally:
        project.close()


@samples
def test_known_edit_moves_record_size():
    a = open_gxw(SAMPLE_FX)
    b = open_gxw(SAMPLE_FX_B)
    try:
        pa = pou_anchors(a.read_stream(a.find("Block1.Program.pou")))
        pb = pou_anchors(b.read_stream(b.find("Block1.Program.pou")))
        # The record carrying the edited branch starts at the same offset, but its
        # declared payload size shrinks (113 -> 92); the next anchor shifts by the
        # same delta, which is exactly what a framing model must explain.
        assert pa["firstOffsets"][0] == pb["firstOffsets"][0]
        assert pa["firstSizes"][0] != pb["firstSizes"][0]
        assert len(pa["firstOffsets"]) > 1 and len(pb["firstOffsets"]) > 1
        delta = pb["firstSizes"][0] - pa["firstSizes"][0]
        assert pb["firstOffsets"][1] - pa["firstOffsets"][1] == delta
    finally:
        a.close()
        b.close()

@samples
def test_identical_project_identical_bytes():
    a = open_gxw(SAMPLE_FX)
    c = open_gxw(SAMPLE_FX_C)
    try:
        assert a.read_stream(a.find("Block1.Program.pou")) == c.read_stream(c.find("Block1.Program.pou"))
    finally:
        a.close()
        c.close()


# --- MAIN.res decoder (grammar proven by a known write/read round trip) -------

from gxworks2_mcp.gxw import main_res_program  # noqa: E402

GROUND_TRUTH = r'C:\Users\lenovo\AppData\Local\Temp\gxout\demo1.gxw'
have_gt = os.path.exists(GROUND_TRUTH)


def _synthetic_res(stream_bytes):
    return bytes(0x36) + struct.pack('<I', len(stream_bytes)) + stream_bytes + bytes(16)


def test_decoder_end_to_end_on_synthetic_rung():
    stream = (
        bytes.fromhex('030003')
        + bytes.fromhex('049c0004')
        + bytes.fromhex('032003')
        + bytes.fromhex('049d0004')
        + b'\x07\x82\x03abc\x07'
        + bytes.fromhex('04340204')
    )
    out = main_res_program(_synthetic_res(stream))
    assert out['decoded'] is True
    assert out['coverage'] == 1.0
    assert out['il'].splitlines() == ['LD X0', 'OUT Y0  // abc', 'END']


def test_decoder_stops_on_unknown_token():
    stream = bytes.fromhex('030004ff0004') + bytes.fromhex('04340204')
    out = main_res_program(_synthetic_res(stream))
    assert out['decoded'] is False
    assert 'stopReason' in out


@pytest.mark.skipif(not have_gt, reason='ground-truth .gxw from the live round trip not present')
def test_decoder_reads_back_the_live_saved_project():
    project = open_gxw(GROUND_TRUTH)
    try:
        body = project.read_stream(project.find('MAIN.res'))
        out = main_res_program(body)
        assert out['decoded'] is True and out['coverage'] == 1.0
        il = out['il']
        for line in ('LD X0', 'ANI X1', 'ORB', 'OUT Y0  // motor', 'LDP X2', 'OUT Y2  // flag', 'END'):
            assert line in il
        assert "LDP X2" in il
    finally:
        project.close()


def test_decoder_reports_unknown_records_honestly():
    # fx3u family MAIN.res uses a different body header: the Q-oriented walk
    # must stop with a reason, never emit fabricated instructions
    if have_gt:
        return
    fake = bytes(0x36) + struct.pack('<I', 4) + bytes.fromhex('056c0100')
    out = main_res_program(fake)
    assert out['decoded'] is False
    assert 'unknown record' in out.get('stopReason', '')


def test_decoder_handles_ladder_and_block_operators():
    # LD X0 / LD X1 / ANB / OUT Y0 / END  (pure table records)
    stream = (
        bytes.fromhex('030003') + bytes.fromhex('049c0004')
        + bytes.fromhex('030003') + bytes.fromhex('049c0104')
        + bytes.fromhex('031903')
        + bytes.fromhex('032003') + bytes.fromhex('049d0004')
        + bytes.fromhex('04340204')
    )
    out = main_res_program(_synthetic_res(stream))
    assert out['decoded'] is True
    assert out['il'].splitlines() == ['LD X0', 'LD X1', 'ANB', 'OUT Y0', 'END']

# --- analysis (where-used / trace / lint) -------------------------------------

from gxworks2_mcp.analysis import interlock_summary, trace_device, where_used  # noqa: E402
from gxworks2_mcp.il import parse_il  # noqa: E402

BELT = parse_il(
    "LD X0\nANI X1\nLD Y0\nANI X1\nORB\nOUT Y0\n"
    "LD X5\nAND Y0\nOUT M20\n"
    "LD M20\nANI X9\nOUT Y1\n"
    "LD X8\nOUT Y1\n"
    "END",
    title="belt",
)


def test_where_used_separates_writes_and_reads():
    sites = where_used(BELT)["M20"]
    kinds = sorted(e["kind"] for e in sites)
    assert kinds == ["R", "W"]


def test_trace_expands_internal_and_marks_boundary():
    tree = trace_device(BELT, "Y1")
    assert tree["multiWriter"] == "2 coils (last OUT wins each scan)"
    first = tree["writes"][0]
    assert "M20" in first["expands"]
    assert first["expands"]["X9"] == "boundary"
    assert first["expands"]["M20"]["writes"]  # expanded to its own rung


def test_trace_reports_boundary_without_writer():
    tree = trace_device(BELT, "M99")
    assert tree["writes"] == []
    assert "never written" in tree["boundary"]


def test_interlock_summary_finds_multi_writer():
    summary = interlock_summary(BELT)
    assert summary["multiWriterCoils"].get("Y1") == 2
