"""Tests for the GX Works2 MCP toolkit (offline only: no PLC, no GX Works2 needed).

The MC protocol frames are pinned against bytes captured from a reference
implementation (pymcprotocol 0.3.0, plctype Q, binary 3E), so a byte-order regression
fails here instead of on a real PLC.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gxworks2_mcp.author import BuildError, compile_logic, compile_rung, compile_rungs  # noqa: E402
from gxworks2_mcp.blocks import render_ascii, to_boolean  # noqa: E402
from gxworks2_mcp.devices import (  # noqa: E402
    DeviceError,
    device_code,
    parse_device,
    parse_range,
)
from gxworks2_mcp.il import ILParseError, parse_il, render_il  # noqa: E402
from gxworks2_mcp.mcprotocol import (  # noqa: E402
    MCConfig,
    build_request,
    pack_bits,
    parse_response,
    unpack_bits,
)
from gxworks2_mcp.csvio import (  # noqa: E402
    CSVFormatError,
    read_device_comments,
    read_program_csv,
    write_device_comments,
    write_program_csv,
)
from gxworks2_mcp.store import Store  # noqa: E402
from gxworks2_mcp.validate import validate_program  # noqa: E402


# --- devices ---------------------------------------------------------------

@pytest.mark.parametrize(
    "text, device, index, bit",
    [
        ("X0", "X", 0, None),
        ("Y10", "Y", 0x10, None),          # Y is hexadecimal
        ("M100", "M", 100, None),
        ("D100", "D", 100, None),
        ("D100.3", "D", 100, 3),
        ("ZR512", "ZR", 0x512, None),      # ZR is hexadecimal
        ("T0", "TN", 0, None),
        ("C10", "CN", 10, None),
        ("SM400", "SM", 400, None),
    ],
)
def test_parse_device(text, device, index, bit):
    ref = parse_device(text)
    assert ref.device == device
    assert ref.index == index
    assert ref.bit == bit


@pytest.mark.parametrize("text", ["", "Q100", "D", "@@@", "M-1"])
def test_parse_device_rejects(text):
    with pytest.raises(DeviceError):
        parse_device(text)


def test_hex_device_rejects_invalid_digits():
    with pytest.raises(DeviceError):
        parse_device("XG1")


def test_device_codes_are_one_byte_values():
    assert device_code("D0") == 0xA8
    assert device_code("M0") == 0x90
    assert device_code("X0") == 0x9C
    assert device_code("Y0") == 0x9D
    assert device_code("ZR0") == 0xB0
    for code in (0xA8, 0x90, 0x9C, 0x9D, 0xB0):
        assert code <= 0xFF


def test_parse_range_forms():
    assert parse_range("D100-D119").count == 20
    assert parse_range("D100..D109").count == 10
    assert parse_range("D100 5").count == 5
    assert parse_range("M0").count == 1
    assert parse_range("X0-XF").count == 16
    with pytest.raises(DeviceError):
        parse_range("D100-M10")
    with pytest.raises(DeviceError):
        parse_range("D100-D99")


def test_device_kinds():
    assert parse_device("D100").kind == "word"
    assert parse_device("M0").kind == "bit"
    assert parse_device("D100.5").kind == "bit"


def test_hex_device_rendering():
    assert str(parse_device("Y1A")) == "Y1A"
    assert str(parse_device("zr10")) == "ZR10"


# --- IL parsing ------------------------------------------------------------

def test_parse_and_render_roundtrip():
    source = "LD X0\nANI X1\nOUT Y0\nEND"
    assert render_il(parse_il(source)) == source


def test_parse_tolerates_comments_steps_and_blank_lines():
    source = """
    // header comment
    0 LD X0
    1 OUT Y0   // drive the lamp
    (* block comment *)
    2 END
    """
    program = parse_il(source)
    assert [instruction.mnemonic for instruction in program.instructions] == ["LD", "OUT", "END"]
    assert program.instructions[1].step == 1
    assert program.instructions[1].comment == "drive the lamp"


def test_melsec_mnemonics_use_ani_not_andi():
    program = parse_il("LD X0\nANI X1\nLD M0\nORI M1\nANDP X2\nOUT Y0")
    mnemonics = [i.mnemonic for i in program.instructions]
    assert mnemonics[:5] == ["LD", "ANI", "LD", "ORI", "ANDP"]
    assert "ANDI" not in mnemonics


def test_missing_operand_raises():
    with pytest.raises(ILParseError):
        parse_il("LD")


def test_labels_are_collected():
    program = parse_il("LD X0\nCJ P1\nP1:\nOUT Y0\nEND")
    assert "P1" in program.labels()


# --- authoring compiler ----------------------------------------------------

def test_compile_series():
    assert compile_logic({"and": ["X0", "X1"]}) == ["LD X0", "AND X1"]


def test_compile_parallel_uses_or():
    assert compile_logic({"or": ["X0", "X1"]}) == ["LD X0", "OR X1"]


def test_compile_nested_uses_block_join():
    assert compile_logic({"or": [{"and": ["X0", "X1"]}, {"and": ["X2", "X3"]}]}) == [
        "LD X0",
        "AND X1",
        "LD X2",
        "AND X3",
        "ORB",
    ]


def test_compile_inverted_first_operand_uses_ldi():
    assert compile_logic({"not": "X1"}) == ["LDI X1"]


def test_compile_inverted_series_operand_uses_ani():
    assert compile_logic({"and": ["X0", {"not": "X1"}]}) == ["LD X0", "ANI X1"]


def test_compile_edge_operands():
    assert compile_logic({"and": ["X0", {"rise": "X2"}]}) == ["LD X0", "ANDP X2"]
    assert compile_logic({"and": ["X0", {"fall": "X2"}]}) == ["LD X0", "ANDF X2"]


def test_compile_rung_adds_out_and_comment():
    instructions = compile_rung({"out": "Y0", "logic": "X0", "comment": "lamp"})
    assert [str(i) for i in instructions] == ["LD X0", "OUT Y0"]
    assert instructions[-1].comment == "lamp"


def test_compile_rungs_appends_end():
    assert compile_rungs([{"out": "Y0", "logic": "X0"}]).instructions[-1].mnemonic == "END"


@pytest.mark.parametrize("bad", [{}, {"out": "Y0", "logic": {"xor": ["X0"]}}, {"logic": "X0"}])
def test_compile_rejects_bad_rungs(bad):
    with pytest.raises(BuildError):
        compile_rungs([bad])


# --- logic recovery --------------------------------------------------------

def test_logic_series():
    assert "X0 & X1" in to_boolean(parse_il("LD X0\nAND X1\nOUT Y0"))


def test_logic_parallel_blocks():
    text = to_boolean(parse_il("LD X0\nAND X1\nLD X2\nAND X3\nORB\nOUT Y0"))
    assert "|" in text and "&" in text


def test_logic_mps_branching_reuses_condition():
    text = to_boolean(parse_il("LD X0\nMPS\nAND X1\nOUT M10\nMPP\nAND X2\nOUT M20"))
    assert "OUT M10" in text and "OUT M20" in text
    assert text.count("X0") == 2


def test_ascii_preview_contains_contacts_and_coil():
    text = render_ascii(parse_il("LD X0\nANI X1\nOUT Y0"))
    assert "[X0]" in text and "[/X1]" in text and "(Y0)" in text


# --- validation ------------------------------------------------------------

def test_validation_ok_for_clean_program():
    assert all(issue.severity != "error" for issue in validate_program(parse_il("LD X0\nOUT Y0\nEND"), cpu="Q"))


def test_validation_flags_missing_end():
    codes = {issue.code for issue in validate_program(parse_il("LD X0\nOUT Y0"), cpu="Q")}
    assert "MISSING_END" in codes


def test_validation_flags_double_coil():
    codes = {issue.code for issue in validate_program(parse_il("LD X0\nOUT Y0\nLD X1\nOUT Y0\nEND"), cpu="Q")}
    assert "DOUBLE_COIL" in codes


def test_validation_flags_unbalanced_orb():
    codes = {issue.code for issue in validate_program(parse_il("LD X0\nORB\nOUT Y0\nEND"), cpu="Q")}
    assert "LOGIC_STRUCTURE" in codes


def test_validation_warns_out_of_range_device():
    codes = {issue.code for issue in validate_program(parse_il("LD X400\nOUT Y0\nEND"), cpu="FX3U")}
    assert "DEVICE_OUT_OF_RANGE" in codes


def test_validation_flags_missing_jump_label():
    codes = {issue.code for issue in validate_program(parse_il("LD X0\nCJ P9\nOUT Y0\nEND"), cpu="Q")}
    assert "LABEL_NOT_FOUND" in codes


# --- MC protocol frames (golden bytes captured from pymcprotocol) -----------

GOLDEN = {
    "read_words": (
        bytes.fromhex("500000ffff03000c00040001040000640000a80300"),
        (0x0401, 0x0000, b"\x64\x00\x00" + b"\xa8" + b"\x03\x00"),
    ),
    "read_bits": (
        bytes.fromhex("500000ffff03000c00040001040100000000900800"),
        (0x0401, 0x0001, b"\x00\x00\x00" + b"\x90" + b"\x08\x00"),
    ),
    "write_words": (
        bytes.fromhex("500000ffff03001000040001140000640000a8020001000200"),
        (0x1401, 0x0000, b"\x64\x00\x00" + b"\xa8" + b"\x02\x00" + b"\x01\x00\x02\x00"),
    ),
    "write_bits": (
        bytes.fromhex("500000ffff030010000400011401000000009d080010100011"),
        (0x1401, 0x0001, b"\x00\x00\x00" + b"\x9d" + b"\x08\x00" + bytes.fromhex("10100011")),
    ),
    "remote_run": (
        bytes.fromhex("500000ffff03000a0004000110000001000100"),
        (0x1001, 0x0000, b"\x01\x00\x01\x00"),
    ),
    "remote_stop": (
        bytes.fromhex("500000ffff030008000400021000000100"),
        (0x1002, 0x0000, b"\x01\x00"),
    ),
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_mc_frames_match_reference_implementation(name):
    expected_frame, (command, subcommand, data) = GOLDEN[name]
    frame = build_request(MCConfig(), command, subcommand, data)
    assert frame.hex() == expected_frame.hex()


def test_length_field_counts_from_monitor_timer():
    frame = build_request(MCConfig(), 0x0401, 0x0000, b"\x00\x00\x00" + b"\xa8" + b"\x01\x00")
    length = int.from_bytes(frame[7:9], "little")
    assert length == len(frame) - 9
    # length 0x000c = monitor timer + command + sub-command + 6 data bytes
    assert frame[:9] == bytes.fromhex("500000ffff03000c00")


def test_bit_packing_two_points_per_byte():
    # Matches the golden write_bits frame: even points in bit 4, odd points in bit 0.
    assert pack_bits([1, 0, 1, 0, 0, 0, 1, 1]) == bytes.fromhex("10100011")


def test_bit_unpacking_roundtrip():
    values = [1, 0, 1, 1, 0, 0, 1, 0]
    assert unpack_bits(pack_bits(values), len(values), False) == values


def test_parse_response_rejects_bad_subheader():
    from gxworks2_mcp.mcprotocol import MCProtocolError

    with pytest.raises(MCProtocolError):
        parse_response(bytes.fromhex("12345678"))


# --- store -----------------------------------------------------------------

def test_store_create_read_and_diff(tmp_path):
    store = Store(tmp_path)
    project = store.create("line1", cpu="Q", initial_il="LD X0\nOUT Y0\nEND")
    assert project.il_path.exists()
    assert len(project.read_program()) == 3
    report = project.save(project.read_program(), comments={"X0": "start button", "Y0": "lamp"})
    assert "devices.csv" in report["files"]
    assert project.device_comments()["X0"] == "start button"
    assert [item["name"] for item in store.list()] == ["line1"]


def test_store_rejects_path_traversal(tmp_path):
    with pytest.raises(Exception):
        Store(tmp_path).get("../../etc")


# --- GX Works2 program CSV -------------------------------------------------

def test_program_csv_roundtrip(tmp_path):
    program = parse_il("LD X0\nANI X1\nOUT Y0  // lamp\nEND")
    path = write_program_csv(program, tmp_path / "MAIN.csv", banner=("MAIN", "Q03UDECPU"))
    parsed = read_program_csv(path)
    assert len(parsed.rows) == 4
    assert parsed.rows[0].instruction == "LD"
    assert parsed.rows[0].device == "X0"
    assert parsed.rows[2].note == "lamp"
    assert parsed.to_il().splitlines()[0] == "LD X0"


def test_program_csv_is_utf16_le_with_bom(tmp_path):
    path = write_program_csv(parse_il("LD X0\nEND"), tmp_path / "MAIN.csv")
    raw = path.read_bytes()
    assert raw[:2] == b"\xff\xfe"
    assert "\t" in raw.decode("utf-16")
    assert '"' in raw.decode("utf-16")


def test_program_csv_reads_gxworks2_export_shape(tmp_path):
    # Shape taken from a real GX Works2 export: banner rows, then the seven-column header.
    target = tmp_path / "MAIN.csv"
    rows = [
        '"FX2N\t""\t""\t""\t""\t""\t""',
        '"PC情報:"\t""\t"FXCPU FX2N/FX2NC"\t""\t""\t""\t""',
        '"ステップ番号"\t"行間ステートメント"\t"命令"\t"I/O(デバイス)"\t""\t"PIステートメント"\t"ノート"',
        '"0"\t""\t"LD"\t"X000"\t""\t""\t""',
        '"3"\t""\t"OUT"\t"Y001"\t""\t""\t"lamp"',
        '"4"\t""\t"END"\t""\t""\t""\t""',
    ]
    target.write_bytes(b"\xff\xfe" + "\r\n".join(rows).encode("utf-16-le"))
    parsed = read_program_csv(target)
    assert [row.instruction for row in parsed.rows] == ["LD", "OUT", "END"]
    assert parsed.rows[1].device == "Y001"
    assert parsed.rows[1].note == "lamp"
    assert parsed.to_il().splitlines()[1].startswith("OUT Y001")


def test_program_csv_rejects_unknown_file(tmp_path):
    target = tmp_path / "bad.csv"
    target.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(CSVFormatError):
        read_program_csv(target)


def test_device_comment_csv_roundtrip(tmp_path):
    path = write_device_comments({"X0": "start", "Y0": "lamp"}, tmp_path / "comments.csv")
    assert path.read_bytes()[:2] == b"\xff\xfe"
    assert read_device_comments(path) == {"X0": "start", "Y0": "lamp"}


# --- facts taken from the official MC protocol manual ----------------------

def test_official_bit_packing_example():
    # SH-080008: M10..M14 = ON/OFF/ON/OFF/ON packs to 10H 10H 10H (4 bit per point).
    assert pack_bits([1, 0, 1, 0, 1]) == bytes.fromhex("101010")
    assert unpack_bits(bytes.fromhex("101010"), 5) == [1, 0, 1, 0, 1]


def test_point_limits_follow_manual():
    from gxworks2_mcp.mcprotocol import MAX_BITS, MAX_WORDS

    assert MAX_WORDS == 960
    assert MAX_BITS == 7168


def test_step_relay_is_read_only():
    assert parse_device("S10").is_read_only is True
    assert parse_device("M10").is_read_only is False
    from gxworks2_mcp.devices import describe

    assert describe("S0")["read_only"] is True


@pytest.mark.parametrize(
    "device, code",
    [("S0", 0x98), ("Z0", 0xCC), ("STS0", 0xC7), ("STC0", 0xC6), ("STN0", 0xC8)],
)
def test_device_codes_from_official_table(device, code):
    assert device_code(device) == code


def test_export_csv_matches_verified_import_template(tmp_path):
    from gxworks2_mcp.csvio import HEADER_ROWS

    program = parse_il("LD X0\nEND")
    path = write_program_csv(program, tmp_path / "MAIN.csv", project_name="demo1")
    text = path.read_bytes().decode("utf-16")
    lines = text.replace("\r\n", "\n").split("\n")
    assert lines[0] == '"demo1"'
    assert lines[1] == '"PLC信息:"\t"QCPU (Q mode) Q03UDV"'
    assert lines[2] == '"步号"\t"行间声明"\t"指令"\t"I/O(软元件)"\t"空白栏"\t"PI声明"\t"注解"'
    assert HEADER_ROWS["ja"][0] == "ステップ番号"


# --- SFC step-ladder (STL/RET) authoring path ---------------------------------

SFC_IL = (
    "STL S0\nOUT Y10\nLD X10\nOUT S8\nRET\n"
    "STL S8\nOUT Y11\nLD X11\nOUT S9\nRET\n"
    "STL S9\nOUT Y12\nRET\nEND"
)


def test_sfc_il_parses_renders_and_validates():
    from gxworks2_mcp.il import parse_il, render_il

    program = parse_il(SFC_IL, title="sfc")
    assert render_il(program) == SFC_IL
    assert "S0" in program.devices() and "X10" in program.devices()


def test_sfc_survives_program_csv_roundtrip(tmp_path):
    from gxworks2_mcp.csvio import read_program_csv, write_program_csv
    from gxworks2_mcp.il import parse_il, render_il

    program = parse_il(SFC_IL, title="sfc")
    path = write_program_csv(program, str(tmp_path / "sfc.csv"), project_name="sfc")
    back = read_program_csv(path).to_program("sfc")
    assert render_il(back) == render_il(program)
