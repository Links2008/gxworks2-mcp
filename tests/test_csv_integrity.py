"""CSV integrity regressions; no GUI or PLC access.

Continuation evidence: original export in
https://github.com/ito-soft-design/ladder_converter/blob/master/test/files/src/MAIN.csv
blob f3957ff53867d9e265f60bbb6b01260e250cbb82, steps 72/77.
"""
import csv
import io

import pytest

from gxworks2_mcp.csvio import (
    CSVFormatError, CsvProgram, CsvRow, HEADER_ROWS,
    program_to_csv_rows, read_program_csv, write_program_csv,
)
from gxworks2_mcp.il import parse_il


def test_multiple_operands_use_device_continuations(tmp_path):
    source = parse_il("LD M120\nMOV K0 D0\nADD D0 K1 D0\nEND")
    path = write_program_csv(source, tmp_path / "main.csv")
    rows = list(csv.reader(io.StringIO(path.read_bytes().decode("utf-16")), delimiter="\t"))[3:]
    assert rows[1][2:6] == ["MOV", "K0", "", ""]
    assert rows[2] == ["", "", "", "D0", "", "", ""]
    assert all(not r[5] for r in rows)
    restored = read_program_csv(path).to_program()
    assert [(x.mnemonic, x.operands) for x in restored.instructions] == [(x.mnemonic, x.operands) for x in source.instructions]


def test_original_export_continuation_shape():
    # Minimal transcription of the original export's MOV/ADD rows.
    rows = [CsvRow(step="72", instruction="MOV", device="K0"), CsvRow(device="D0"),
            CsvRow(step="77", instruction="ADD", device="D0"), CsvRow(device="K1"), CsvRow(device="D0")]
    assert CsvProgram(rows=rows).to_il() == "MOV K0 D0\nADD D0 K1 D0"


@pytest.mark.parametrize("rows", [
    [CsvRow(instruction="MOV", device="K0", pi="D0")],
    [CsvRow(device="D0")],
    [CsvRow(instruction="MOV", device="K0"), CsvRow(step="1", device="D0")],
    [CsvRow(instruction="MOV", device="K0"), CsvRow(device="D0", note="unmapped")],
    [CsvRow(interline="standalone")],
    [CsvRow(instruction="LD", device="X0", interline="a", note="b")],
    [CsvRow(instruction="LD", device="X0\nOUT Y0")],
    [CsvRow(instruction="MOV", device="K0 D0")],
])
def test_ambiguous_rows_fail_closed(rows):
    with pytest.raises(CSVFormatError):
        CsvProgram(rows=rows).to_il()


def test_label_export_rejected_without_clobbering(tmp_path):
    path = tmp_path / "main.csv"
    path.write_bytes(b"original")
    with pytest.raises(CSVFormatError, match="labels"):
        write_program_csv(parse_il("start:\nLD X0\nEND"), path)
    assert path.read_bytes() == b"original"


@pytest.mark.parametrize("locale", ["en", "ja", "zh"])
def test_locale_headers_map_without_statement_collision(tmp_path, locale):
    path = write_program_csv(parse_il("MOV K0 D0\nEND"), tmp_path / "main.csv", locale=locale)
    assert read_program_csv(path).to_il() == "MOV K0 D0\nEND"


def test_unmapped_data_is_rejected(tmp_path):
    path = tmp_path / "main.csv"
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t")
    writer.writerow(HEADER_ROWS["en"])
    writer.writerow(["0", "", "END", "", "unexpected", "", ""])
    path.write_text(out.getvalue(), encoding="utf-8")
    with pytest.raises(CSVFormatError, match="unmapped"):
        read_program_csv(path)


def test_metadata_only_rows_are_not_silently_dropped(tmp_path):
    path = tmp_path / "main.csv"
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t")
    writer.writerow(HEADER_ROWS["en"])
    writer.writerow(["0", "", "END", "", "", "", ""])
    writer.writerow(["", "", "", "", "", "PI statement", ""])
    path.write_text(out.getvalue(), encoding="utf-8")
    parsed = read_program_csv(path)
    assert len(parsed.rows) == 2
    with pytest.raises(CSVFormatError, match="PI"):
        parsed.to_program()


@pytest.mark.parametrize("row", [
    CsvRow(instruction="#", device="X0"),
    CsvRow(instruction="//", device="X0"),
    CsvRow(instruction="LD", device="X0(*hidden*)"),
])
def test_il_syntax_cannot_silently_consume_csv_fields(row):
    with pytest.raises(ValueError):
        CsvProgram(rows=[row]).to_il()


def test_empty_operand_is_not_dropped():
    from gxworks2_mcp.il import Instruction, Program
    with pytest.raises(CSVFormatError):
        program_to_csv_rows(Program(instructions=[Instruction("OUT", ["Y0", ""])]))
def test_for_single_loop_count_roundtrip(tmp_path):
    from gxworks2_mcp.il import parse_il
    from gxworks2_mcp.csvio import write_program_csv, read_program_csv
    program = parse_il('FOR K1\nLD X1\nOUT Y1\nNEXT\nEND')
    path = write_program_csv(program, tmp_path / 'for.csv')
    assert read_program_csv(path).to_il() == 'FOR K1\nLD X1\nOUT Y1\nNEXT\nEND'
