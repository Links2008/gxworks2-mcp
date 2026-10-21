"""独立构造小型 SFC 帧，验证边界、绑定及未知指令的拒绝行为。"""

import pytest

from gxworks2_mcp.gxw import GxwError
from gxworks2_mcp.sfc_binary import words, parse_source, parse_graph, decode_body


GRAPH = bytes.fromhex(
    "056c041c050498000404980004056c02040504990004056202000504980104056c010a05"
    "056c0414050498010404981404056c021d0504990104056202000504980004056c010a05"
)


def source_frame():
    def name(text):
        return words(len(text) + 1) + (text + "\0").encode("utf-16le")
    def child(body):
        return words(1, 20 + len(body), 20 + len(body), 1, 12, 0, 0xFFFFFFFF) + body + words(1)
    region = words(20 + len(GRAPH), 0, 0, 4 + len(GRAPH)) + GRAPH + words(4)
    header = name("") + name("") + bytes(42)
    return (header + words(240, len(region)) + region + words(2) + name("z0") + child(b"") +
            name("z20") + child(bytes.fromhex("032003049d0004")) + words(2) +
            name("a0") + words(0, 1, 0) + name("z0") + words(1) +
            name("a20") + words(20, 1, 0) + name("z20") + words(1) + words(2) +
            name("t0") + words(0) + child(bytes.fromhex("030003049c0004")) +
            name("t1") + words(1) + child(bytes.fromhex("030003049c0104")) + words(0))


def test_sfc_frame_recovers_exact_graph_children_and_registrations():
    source = parse_source(source_frame())
    assert source["graph"] == GRAPH
    assert decode_body(source["actions"][20]["body"]) == "OUT Y0"
    assert decode_body(source["transitions"][0]["body"]) == "LD X0"
    graph = parse_graph(GRAPH)
    assert [s["state"] for s in graph["steps"]] == ["S0", "S20"]
    assert graph["edges"] == [dict(source=0, transition=0, target=1), dict(source=1, transition=1, target=0)]


@pytest.mark.parametrize("size", [0, 3, 54, 80, -1, -7])
def test_sfc_frame_truncation_is_rejected(size):
    with pytest.raises(GxwError):
        parse_source(source_frame()[:size])


def test_sfc_unknown_storage_and_trailing_data_are_rejected():
    raw = bytearray(source_frame())
    raw[66:70] = words(1)
    with pytest.raises(GxwError, match="存储模式"):
        parse_source(raw)
    with pytest.raises(GxwError, match="尾部"):
        parse_source(source_frame() + b"unknown")


@pytest.mark.parametrize("tokens", [GRAPH[:-1], b"\x05\x6c\xff\xff\x05",
                                    GRAPH.replace(bytes.fromhex("04990104"), bytes.fromhex("04990004")), GRAPH + b"x"])
def test_graph_unknown_or_truncated_tokens_are_rejected(tokens):
    with pytest.raises(GxwError):
        parse_graph(tokens)


def test_graph_unknown_target_is_rejected():
    graph = GRAPH.replace(bytes.fromhex("056202000504980104"), bytes.fromhex("056202000504987f04"))
    with pytest.raises(GxwError, match="引用不完整"):
        parse_graph(graph)


def test_child_unknown_instruction_and_early_end_are_rejected():
    for tokens in [bytes.fromhex("039903"), bytes.fromhex("033403")]:
        with pytest.raises(GxwError):
            decode_body(tokens)
