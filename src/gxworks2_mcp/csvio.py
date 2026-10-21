"""GX Works2 program CSV: the one textual format the tool can round-trip with the app.

GX Works2 can write a program (instruction list) to CSV from the project tree:
right-click the program name (e.g. MAIN) -> "Write to CSV File" / "写入至 CSV 文件".
The exported file is UTF-16 LE with a BOM, tab separated, every field double quoted,
seven columns:

    step | interline statement | instruction | I/O(device) | (empty) | PI statement | note

The first two rows carry the project/CPU banner. Exporting is documented by Mitsubishi;
importing is NOT officially promised for programs (FAQ 22643 covers device comments
only), but it was verified live on GX Works2 1.620W: Edit > Read from CSV file accepts
this exact shape and rebuilds the ladder (docs/RESEARCH.md E3). The import REPLACES the
whole current program block - generate one CSV per complete program and re-check after.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .il import Instruction, Program, parse_il

#: Header spellings seen across GX Works2 locales (Japanese, Chinese, English).
STEP_HEADERS = ("ステップ番号", "步号", "步番", "ステップ", "step", "step no", "step no.")
COMMENT_HEADERS = (
    "行間ステートメント",
    "行间声明",
    "行间语句",
    "一括语句",
    "interline",
    "interline statement",
    "statement",
)
INSTRUCTION_HEADERS = ("命令", "指令", "instruction", "command", "mnemonic")
DEVICE_HEADERS = ("I/O(デバイス)", "I/O(软元件)", "I/O(デバイス名)", "I/O(Device)", "device", "i/o", "operand")
PI_HEADERS = ("PIステートメント", "PI声明", "PI语句", "pi statement", "pi")
NOTE_HEADERS = ("ノート", "注解", "注释", "备注", "note", "remark")

#: Column headers exactly as GX Works2 writes them, per UI language.
#: Verified on a Chinese GX Works2 1.620W install (see docs/RESEARCH.md E3(2)).
HEADER_ROWS = {
    "zh": ["步号", "行间声明", "指令", "I/O(软元件)", "空白栏", "PI声明", "注解"],
    "ja": ["ステップ番号", "行間ステートメント", "命令", "I/O(デバイス)", "空白欄", "PIステートメント", "ノート"],
    "en": ["Step", "Interline statement", "Instruction", "I/O(Device)", "", "PI statement", "Note"],
}
DEFAULT_LOCALE = "zh"
HEADER_ROW = HEADER_ROWS[DEFAULT_LOCALE]

#: Second banner row GX Works2 writes: a label plus the CPU description.
BANNER_PLC_LABELS = {"zh": "PLC信息:", "ja": "PC情報:", "en": "PLC information:"}


class CSVFormatError(ValueError):
    """Raised when a file does not look like a GX Works2 program CSV."""


@dataclass
class CsvRow:
    step: str = ""
    interline: str = ""
    instruction: str = ""
    device: str = ""
    pi: str = ""
    note: str = ""

    def to_list(self) -> list[str]:
        return [self.step, self.interline, self.instruction, self.device, "", self.pi, self.note]


@dataclass
class CsvProgram:
    """A parsed program CSV: rows plus whatever banner the export carried."""

    rows: list[CsvRow] = field(default_factory=list)
    banner: list[list[str]] = field(default_factory=list)
    source: str = ""

    def to_il(self) -> str:
        """Convert the rows to IL text (comments become trailing // comments)."""

        # Additional operands are device-only continuation rows in the original
        # GX Works2 export fixture (ito-soft-design/ladder_converter,
        # test/files/src/MAIN.csv, blob f3957ff53867d9e265f60bbb6b01260e250cbb82).
        # PI is a statement column, NEVER an extra-operand column.
        instructions: list[tuple[str, list[str], str]] = []
        for number, row in enumerate(self.rows, 1):
            if row.pi.strip():
                raise CSVFormatError(f"row {number}: nonempty PI statement cannot be converted losslessly")
            instruction = row.instruction.strip()
            device = row.device.strip()
            if any(c in instruction + device for c in "\r\n\t"):
                raise CSVFormatError(f"row {number}: multiline instruction/operand is unsupported")
            if device and (any(c.isspace() for c in device) or "//" in device or "#" in device):
                raise CSVFormatError(f"row {number}: ambiguous operand field is unsupported")
            if instruction and (any(c.isspace() for c in instruction) or ":" in instruction):
                raise CSVFormatError(f"row {number}: unsupported instruction/label representation")
            if row.interline and row.note and row.interline != row.note:
                raise CSVFormatError(f"row {number}: multiple comment fields cannot be represented losslessly")
            comment = row.note.strip() or row.interline.strip()
            if any(c in comment for c in "\r\n"):
                raise CSVFormatError(f"row {number}: multiline comment is unsupported")
            if not instruction:
                if not device:
                    raise CSVFormatError(f"row {number}: standalone metadata cannot be converted losslessly")
                if not instructions or row.step or comment:
                    raise CSVFormatError(f"row {number}: ambiguous or orphan operand continuation")
                instructions[-1][1].append(device)
                continue
            instructions.append((instruction, [device] if device else [], comment))
        lines = []
        for mnemonic, operands, comment in instructions:
            body = " ".join([mnemonic, *operands])
            lines.append(body + ("  // " + comment if comment else ""))
        text = "\n".join(lines)
        # IL has its own comment/label syntax. A field that changes meaning when
        # parsed must not be allowed to disappear in a read-back comparison.
        parsed = parse_il(text)
        expected = [(m.upper(), ops, comment or None) for m, ops, comment in instructions]
        actual = [(i.mnemonic, i.operands, i.comment) for i in parsed.instructions]
        if actual != expected or any(i.label is not None for i in parsed.instructions):
            raise CSVFormatError("CSV fields cannot be represented losslessly in IL")
        return text

    def to_program(self, title: str = "") -> Program:
        return parse_il(self.to_il(), title=title)


def _normalise(header: str) -> str:
    return header.strip().strip('"').lower().replace(" ", "")


def _detect_columns(header: list[str]) -> dict[str, int] | None:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header):
        key = _normalise(cell)
        if not key:
            continue
        for name, aliases in (
            ("step", STEP_HEADERS),
            ("interline", COMMENT_HEADERS),
            ("instruction", INSTRUCTION_HEADERS),
            ("device", DEVICE_HEADERS),
            ("pi", PI_HEADERS),
            ("note", NOTE_HEADERS),
        ):
            if any(_normalise(alias) == key for alias in aliases):
                if name in mapping:
                    raise CSVFormatError(f"duplicate {name} column")
                mapping[name] = index
    if "instruction" not in mapping:
        return None
    return mapping


def read_program_csv(path: str | Path) -> CsvProgram:
    """Read a GX Works2 program CSV (UTF-16 LE with BOM, or UTF-8 as a fallback)."""

    target = Path(path).expanduser()
    raw = target.read_bytes()
    text: str | None = None
    for encoding in ("utf-16", "utf-16-le", "utf-8-sig", "utf-8", "cp932"):
        try:
            candidate = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "命令" in candidate or "instruction" in candidate.lower() or "指令" in candidate:
            text = candidate
            break
    if text is None:
        raise CSVFormatError(
            "cannot decode " + str(target) + " as a GX Works2 program CSV; expected UTF-16 LE "
            "with a BOM and a header row containing the instruction column"
        )

    reader = csv.reader(io.StringIO(text), delimiter="\t")
    rows = [row for row in reader]
    if not rows:
        raise CSVFormatError("empty CSV: " + str(target))

    header_index = None
    mapping: dict[str, int] | None = None
    for index, row in enumerate(rows[:10]):
        mapping = _detect_columns(row)
        if mapping is not None:
            header_index = index
            break
    if mapping is None or header_index is None:
        raise CSVFormatError(
            "no header row with an instruction column found in " + str(target) + "; "
            "expected columns such as ステップ番号 / 命令 / I/O(デバイス) / ノート"
        )

    parsed = CsvProgram(banner=rows[:header_index], source=str(target))
    for row in rows[header_index + 1 :]:
        if not any(cell.strip() for cell in row):
            continue

        def cell(name: str) -> str:
            index = mapping.get(name)
            if index is None or index >= len(row):
                return ""
            return row[index].strip()

        if any(value.strip() for index, value in enumerate(row) if index not in mapping.values()):
            raise CSVFormatError("nonempty unmapped column cannot be converted losslessly")
        parsed.rows.append(
            CsvRow(
                step=cell("step"),
                interline=cell("interline"),
                instruction=cell("instruction"),
                device=cell("device"),
                pi=cell("pi"),
                note=cell("note"),
            )
        )
    return parsed


def write_program_csv(
    program: Program,
    path: str | Path,
    *,
    project_name: str = "MAIN",
    cpu_label: str = "QCPU (Q mode) Q03UDV",
    locale: str = "zh",
    banner: list[list[str]] | None = None,
) -> Path:
    """Write a program in the shape GX Works2's "Read from CSV file" actually imports.

    The template below was verified against a live GX Works2 1.620W round trip
    (docs/RESEARCH.md E3): project name line, a PLC info line, the seven quoted
    headers, then instruction rows and device-only operand continuation rows,
    with UTF-16 LE + BOM and tab separators. Continuations follow the original
    export fixture cited in CsvProgram.to_il; they have not been live-import
    tested by this offline implementation.

        "demo1"
        "PLC信息:"\t"QCPU (Q mode) Q03UDV"
        "步号"\t"行间声明"\t"指令"\t"I/O(软元件)"\t"空白栏"\t"PI声明"\t"注解"
        "0"\t""\t"LD"\t"X0"\t""\t""\t""

    Pass banner to override the two label lines when the caller already knows the
    locale-specific wording.
    """

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    headers = HEADER_ROWS.get(locale, HEADER_ROWS["zh"])
    info = BANNER_PLC_LABELS.get(locale, BANNER_PLC_LABELS["zh"])
    rows: list[list[str]] = [list(r) for r in banner] if banner else [[project_name], [info, cpu_label]]
    rows.append(headers)
    rows.extend(program_to_csv_rows(program))
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    for row in rows:
        writer.writerow(row)
    target.write_bytes(b"\xff\xfe" + buffer.getvalue().encode("utf-16-le"))
    return target


def write_device_comments(comments: dict[str, str], path: str | Path) -> Path:
    """Write a device-comment CSV in the shape GX Works2 accepts (UTF-16 LE, tab separated).

    Mitsubishi documents device-comment CSV import/export from Ver.1.64S onward, so this
    direction of the round trip is the one the application itself supports.
    """

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(["Device", "Comment"])
    for device, comment in sorted(comments.items()):
        writer.writerow([device, comment])
    target.write_bytes(b"\xff\xfe" + buffer.getvalue().encode("utf-16-le"))
    return target


def read_device_comments(path: str | Path) -> dict[str, str]:
    """Read a device-comment CSV exported from GX Works2."""

    target = Path(path).expanduser()
    raw = target.read_bytes()
    for encoding in ("utf-16", "utf-8-sig", "cp932"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise CSVFormatError("cannot decode device comment CSV: " + str(target))
    delimiter = "\t" if "\t" in text.splitlines()[0] else ","
    comments: dict[str, str] = {}
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        if len(row) >= 2 and row[0].strip() and row[0].strip().lower() not in {"device", "デバイス", "软元件"}:
            comments[row[0].strip().upper()] = row[1].strip()
    return comments


def program_to_csv_rows(program: Program) -> list[list[str]]:
    """Rows in export shape, without the file envelope (useful for tests and previews)."""

    rows: list[list[str]] = []
    for index, instruction in enumerate(program.instructions):
        if instruction.label is not None:
            raise CSVFormatError("IL labels have no verified GX Works2 CSV representation")
        if any(not operand or operand != operand.strip() for operand in instruction.operands):
            raise CSVFormatError("empty or padded operands cannot be represented losslessly")
        device = instruction.operands[0] if instruction.operands else ""
        rows.append([str(index), "", instruction.mnemonic, device, "", "", instruction.comment or ""])
        for operand in instruction.operands[1:]:
            rows.append(["", "", "", operand, "", "", ""])
    # Apply the same losslessness checks before writing any file content.
    CsvProgram(rows=[CsvRow(r[0], r[1], r[2], r[3], r[5], r[6]) for r in rows]).to_il()
    return rows
