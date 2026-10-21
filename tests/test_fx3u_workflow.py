"""FX3U 编程与工程读回的回归验证。"""

import json
import struct

import pytest

from gxworks2_mcp import server
from gxworks2_mcp.author import BuildError
from gxworks2_mcp.gxw import pou_stream
from gxworks2_mcp.il import parse_il, render_il
from gxworks2_mcp.store import Store
from gxworks2_mcp.validate import summarize, validate_program


SFC = {
    "initial": "S0",
    "steps": [
        {"state": "S0", "actions": [{"out": "Y0"}],
         "transitions": [{"to": "S20", "when": "X10"}]},
        {"state": "S20", "actions": [{"out": "Y1"}],
         "transitions": [{"to": "S0", "when": "X11"}]},
    ],
}


def test_store_preserves_cpu(tmp_path):
    store = Store(tmp_path)
    store.create("fx", cpu="FX3U", initial_il="END")
    assert store.get("fx").cpu == "FX3U"


@pytest.mark.parametrize("address", ["X8", "Y9", "XA"])
def test_fx3u_rejects_non_octal_io(address):
    result = summarize(validate_program(parse_il(f"LD {address}\nOUT M0\nEND"), cpu="FX3U"))
    assert not result["ok"]


def test_fx3u_accepts_special_relays_and_octal_io():
    report = summarize(validate_program(parse_il("LD M8002\nOUT Y10\nEND"), cpu="FX3U"))
    assert report["ok"] and report["warnings"] == 0


def test_sfc_create_read_and_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_store", Store(tmp_path))
    result = json.loads(server.gx_program_create("sequence", cpu="FX3U", sfc=SFC))
    assert "error" not in result
    assert result["il"].startswith("LD M8002\nSET S0\nSTL S0")
    assert result["il"].endswith("RET\nEND")
    read = json.loads(server.gx_program_read("sequence", include="il,sfc,logic"))
    assert [s["state"] for s in read["sfc"]["steps"]] == ["S0", "S20"]
    assert "S0" in read["logic"]
    assert read["cpu"] == "FX3U"
    assert json.loads(server.gx_program_validate("sequence"))["ok"]


@pytest.mark.parametrize("change", [
    {"initial": "S20"},
    {"initial": "S1"},
    {"steps": [{"state": "S0", "transitions": [{"to": "S99", "when": "X0"}]}]},
    {"steps": [{"state": "S0"}, {"state": "S00"}]},
])
def test_bad_sfc_is_rejected_before_writing(tmp_path, monkeypatch, change):
    monkeypatch.setattr(server, "_store", Store(tmp_path))
    result = json.loads(server.gx_program_create("bad", cpu="FX3U", sfc={**SFC, **change}))
    assert "error" in result
    assert not (tmp_path / "projects" / "bad").exists()


def test_pou_keeps_multiple_operands():
    from test_gxw_reader import _pou_record
    tokens = bytes.fromhex("030003049c0004054c05000504e8010404a80004")
    result = pou_stream(_pou_record(tokens))
    assert "MOV K1 D0" in result["il"]
    assert parse_il(result["il"]).instructions[1].operands == ["K1", "D0"]


def test_pou_partial_network_never_becomes_complete():
    from test_gxw_reader import _pou_record
    tokens = bytes.fromhex("030003049c0004") + b"\x99\x88\x77"
    result = pou_stream(_pou_record(tokens))
    assert result["decoded"] is False
    assert result.get("partialIl") and result.get("stopReason")


def test_direct_write_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_store", Store(tmp_path))
    result = json.loads(server.gx_write_project("missing", str(tmp_path / "x.gxw")))
    assert result["ok"] is False and "confirm" in result["error"]
