"""原生 SFC 图形、动作和转移绑定验证。"""

import pytest
import json

from examples.fx3u_sequence import sequence_spec
from gxworks2_mcp.native_sfc import layout_sfc, graph_edges


def test_sfc_initial_set_uses_native_state_opcode():
    from gxworks2_mcp.il import parse_il
    from gxworks2_mcp.native import encode_program
    tokens = encode_program(parse_il("LD M8002\nSET S0\nEND"))
    assert bytes.fromhex("0423020404980004") in tokens
    assert bytes.fromhex("03230304980004") not in tokens


def test_sequence_layout_preserves_step_and_transition_identity():
    layout = layout_sfc(sequence_spec())
    assert [s["state"] for s in layout["steps"]] == ["S0", "S20", "S21", "S22", "S30", "S31"]
    assert len(layout["transitions"]) == 8
    assert layout["transitions"][1]["source"] == "S20"
    assert layout["transitions"][1]["target"] == "S0"
    assert len({(s["x"], s["y"]) for s in layout["symbols"] if s["kind"] == "step"}) == 9


def test_missing_native_connection_is_rejected():
    cells = [
        {"x": 0, "y": 0, "type": 26, "substep": 0, "number": 0, "result": 1},
        {"x": 0, "y": 1, "number": 0, "result": 2},
    ]
    with pytest.raises(ValueError, match="连接"):
        graph_edges(cells)


def test_native_graph_reader_follows_transition_and_jump():
    cells = [
        {"x": 0, "y": 0, "type": 26, "substep": 0, "number": 0, "result": 1},
        {"x": 0, "y": 1, "number": 0, "result": 2},
        {"x": 0, "y": 2, "branch": 3, "result": 3},
        {"x": 0, "y": 3, "type": 15, "number": 0, "result": 1},
    ]
    assert graph_edges(cells) == [(0, 0, 0)]


@pytest.mark.parametrize("change", [
    lambda s: s.update(parallel=True),
    lambda s: s["steps"][0].update(actions=None),
    lambda s: s["steps"][0]["transitions"][0].update(priority=1),
    lambda s: s["steps"][0].update(actions=[{"out": "Y0\nOUT Y1"}]),
])
def test_native_spec_rejects_ambiguous_fields_and_instruction_injection(change):
    spec = sequence_spec()
    change(spec)
    with pytest.raises((ValueError, TypeError)):
        layout_sfc(spec)


def test_native_spec_rejects_recursive_input():
    spec = sequence_spec()
    spec["preamble"].append(spec)
    with pytest.raises(ValueError, match="深度"):
        layout_sfc(spec)


def test_native_tool_confirmation_and_missing_file_do_not_start_helper(monkeypatch, tmp_path):
    from gxworks2_mcp import server
    import gxworks2_mcp.native_sfc as native
    def forbidden(*args, **kwargs):
        pytest.fail("不应启动原生进程")
    monkeypatch.setattr(native, "native_batch", forbidden)
    assert not json.loads(server.gx_sfc_write(sequence_spec(), str(tmp_path / "new.gxw"), "missing"))["ok"]
    assert not json.loads(server.gx_sfc_read(str(tmp_path / "missing.gxw")))["ok"]


def test_native_cleanup_refuses_unowned_directory(tmp_path, monkeypatch):
    from gxworks2_mcp.native_sfc import _cleanup_native_temp
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    other = tmp_path / "keep"
    other.mkdir()
    (workspace / "native-temp-path.txt").write_text(str(other), encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with pytest.raises(ValueError, match="归属"):
        _cleanup_native_temp(workspace)
    assert other.is_dir()
