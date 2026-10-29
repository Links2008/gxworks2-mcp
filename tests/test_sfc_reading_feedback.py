"""独立合成编码回归；不包含用户工程或生产逻辑。"""
import pytest
from gxworks2_mcp.gxw import GxwError, _read_operand
from gxworks2_mcp.sfc_binary import decode_body, parse_graph
from test_sfc_binary import GRAPH


def test_inspection_opcodes_do_not_expand_native_writer():
    from gxworks2_mcp.gxw import _fx_operators
    assert bytes.fromhex("0563111d05") not in _fx_operators()
    assert bytes.fromhex("04200204") not in _fx_operators()


def test_mcp_open_routes_sfc_without_allowing_flat_import(monkeypatch):
    import json
    from types import SimpleNamespace
    from gxworks2_mcp import server
    from test_sfc_binary import source_frame
    entry = SimpleNamespace(name="example.Program.pou", iid="16")
    project = SimpleNamespace(cpu="FX3U/FX3UC", programs=lambda: [entry],
        read_stream=lambda _: source_frame(), summary=lambda: {}, program_structure=lambda: [], close=lambda: None)
    monkeypatch.setattr(server, "open_gxw", lambda _: project)
    monkeypatch.setattr(server, "device_comments", lambda _: {})
    result = json.loads(server.gx_gxw_open("example.gxw", import_into="must-not-create"))
    block = result["decodedPrograms"][0]
    assert block["representation"] == "native-sfc-graph"
    assert block["steps"][1]["il"] == "OUT Y0"
    assert result["programBodiesDecoded"] == 1
    assert not result["editable"] and "importError" in result


def test_one_unreadable_block_does_not_hide_other_blocks(monkeypatch):
    from types import SimpleNamespace
    from gxworks2_mcp import native_sfc
    from test_sfc_binary import source_frame
    entries = [SimpleNamespace(name=n) for n in ("bad.Program.pou", "good.Program.pou")]
    project = SimpleNamespace(cpu="FX3U/FX3UC", path="example.gxw", programs=lambda: entries,
        read_stream=lambda e: b"bad" if e is entries[0] else source_frame(), close=lambda: None)
    monkeypatch.setattr(native_sfc, "open_gxw", lambda _: project)
    result = native_sfc.read_sfc_gxw("example.gxw")
    assert not result["ok"] and not result["sourceReadback"]
    assert not result["blocks"][0]["decoded"]
    assert result["blocks"][1]["steps"][1]["il"] == "OUT Y0"


def test_read_sfc_exposes_bodies_and_unresolved_graph(monkeypatch):
    from gxworks2_mcp import native_sfc
    from test_sfc_binary import source_frame
    from types import SimpleNamespace
    raw = source_frame().replace(bytes.fromhex("056202000504980104"),
                                 bytes.fromhex("056202000504983204"), 1)
    entry = SimpleNamespace(name="sample.Program.pou")
    project = SimpleNamespace(cpu="FX3U/FX3UC", path="sample.gxw", programs=lambda: [entry],
                              read_stream=lambda _: raw, close=lambda: None)
    monkeypatch.setattr(native_sfc, "open_gxw", lambda _: project)
    result = native_sfc.read_sfc_gxw("sample.gxw")
    assert result["ok"] and result["sourceReadback"]
    assert not result["topologyVerified"] and not result["runnable"]
    assert result["blocks"][0]["steps"][1]["il"] == "OUT Y0"
    assert result["blocks"][0]["transitions"][0]["target"] == 50


@pytest.mark.parametrize("token,expected", [
    ("04e90004", "K0"), ("05e9ffff05", "K65535"),
    ("06e980380106", "K80000"), ("07e970a0feff07", "K-90000"),
    ("04cc0004", "Z0"), ("04f0000404a8d204", "D210Z0"),
])
def test_fx_extended_operands(token, expected):
    raw = bytes.fromhex(token)
    assert _read_operand(raw, 0, "FX3U") == (expected, len(raw))


def test_unknown_truncated_or_invalid_index_still_rejected():
    for token in ("06e9803801", "04f0080404a80004", "04f00004030003", "04ff0004"):
        assert _read_operand(bytes.fromhex(token), 0, "FX3U") is None
    assert _read_operand(bytes.fromhex("04e90004"), 0, "Q") is None


def test_measured_out_width_and_absolute_positioning_header():
    assert decode_body(bytearray.fromhex("0420020405905c1f05")) == "OUT M8028"
    assert decode_body(bytes.fromhex("0420020405905c1f05")) == "OUT M8028"
    assert decode_body(bytes.fromhex("0563111d0504f0000404a8d20404a8ec04049d0004049d0404")) == "DDRVA D210Z0 D236 Y0 Y4"


def test_read_only_multitarget_and_unresolved_reference_are_preserved():
    tokens = GRAPH.replace(bytes.fromhex("056c020405"), bytes.fromhex("056c020605"), 1)
    tokens = tokens.replace(bytes.fromhex("056202000504980104"),
        bytes.fromhex("056202000504980104056202000504983204"), 1)
    with pytest.raises(GxwError):
        parse_graph(tokens)
    graph = parse_graph(tokens, strict=False)
    assert [e["target"] for e in graph["edges"] if e["transition"] == 0] == [1, 50]
    assert graph["diagnostics"]
    assert not graph["topologyVerified"]
    assert {s["number"] for s in graph["steps"]} == {0, 1}
