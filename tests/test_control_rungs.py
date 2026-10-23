"""钻孔工位所需的条件置位、复位和清步边界。"""

import pytest

from gxworks2_mcp.author import BuildError, compile_rung
from gxworks2_mcp.il import Program, render_il
from gxworks2_mcp.native_sfc import sequence_blocks
from gxworks2_mcp.sfc_binary import decode_body
import base64


def test_conditional_reset_and_set_are_explicit():
    assert render_il(Program(compile_rung({"out": "T250", "instruction": "RST", "logic": "M101"}))) == "LD M101\nRST T250"
    assert render_il(Program(compile_rung({"out": "M0", "instruction": "SET", "logic": "X0"}))) == "LD X0\nSET M0"


def test_unknown_instruction_and_preset_on_reset_are_rejected():
    for rung in [{"out": "Y0", "instruction": "JMP"}, {"out": "T250", "instruction": "RST", "preset": "K3"}]:
        with pytest.raises(BuildError):
            compile_rung(rung)


def test_sfc_optional_reset_is_combined_with_first_scan_reset():
    spec = {"initial": "S0", "reset": "M101", "steps": [
        {"state": "S0", "actions": [], "transitions": [{"to": "S20", "when": "X0"}]},
        {"state": "S20", "actions": [], "transitions": [{"to": "S0", "when": "X1"}]},
    ]}
    _, blocks = sequence_blocks(spec)
    il = decode_body(base64.b64decode(blocks[1]["bodies"][0]["body"]))
    assert "LD M8002\nOR M101\nRST S20" in il
    assert il.count("RST S0") == il.count("SET S0") == 1
