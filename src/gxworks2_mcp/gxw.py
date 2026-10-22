"""Read GX Works2 .gxw projects without GX Works2.

Verified against real files on this machine (docs/GXWORKS2_NOTES.md section 7):

    .gxw  =  OLE2 compound file
             + XML diffgram streams (UTF-8, MS Access persisted rows):
                 projectlist.xml       project row
                 projectdatalist.xml   every data file: iID == inner CFB stream id
                 label.xml etc.        labels/comments
             + Project.gd2             digest/manifest (opaque, retained)
             + _hdb                    a NESTED complete OLE2 containing the data itself,
                                       one stream per data file, named by iID:
                                         MAIN.res        program body (record format:
                                          POU .pou        pending reverse-engineering,
                                                          see re-evidence/README.md)
                                         COMMENT.qcd     device comments (Jet DB)
                                         *.dat/.stg/.lif other engines

This module does the container + metadata + inventory part, which is 100% solvable
today, and keeps every program-body byte addressable (read_stream / logical files)
so instruction decoding can be layered on without re-parsing. It never invents
instructions: unknown bodies are returned as framed candidates plus diagnostics.

Optional dependency: olefile (pip install olefile). It is declared in pyproject.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from functools import lru_cache
from typing import Any

try:
    import olefile  # type: ignore
except ImportError:  # pragma: no cover
    olefile = None  # type: ignore

#: folder types seen in the FX3U/Q samples -> friendly categories
FOLDER_NAMES = {
    "1": "project", "2": "parameter", "3": "program", "5": "global",
    "6": "device-memory", "7": "block", "8": "block", "48": "function-library",
    "64": "function-library", "49": "function-library", "87": "user-library",
    "112": "password", "0": "extension",
}


class GxwError(ValueError):
    """Raised when a file is not a readable GX Works2 project."""


@dataclass
class DataFile:
    """One entry of projectdatalist.xml, joined with the inner CFB stream."""

    iid: str
    name: str
    folder_type: str = ""
    file_type: str = ""
    scrap: bool = False
    update_date: str = ""
    stream_id: str | None = None   # inner _hdb stream name holding its bytes
    size: int | None = None
    sha256: str | None = None

    @property
    def category(self) -> str:
        return FOLDER_NAMES.get(self.folder_type, "?")

    @property
    def kind(self) -> str:
        ext = self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""
        return {"res": "program", "pou": "pou", "tsk": "task", "qcd": "device-comment",
                "qdv": "device-memory", "col": "collection", "prj": "project",
                "lif": "library", "dat": "table", "gh": "global", "lh": "labels",
                "wpa": "parameter", "lup": "parameter", "sld": "system-label",
                "stg": "compiler", "pji": "project-index", "wbt": "startup"}.get(ext, ext or "other")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.iid, "name": self.name, "kind": self.kind, "folder": self.category,
            "fileType": self.file_type, "scrap": self.scrap, "stream": self.stream_id,
            "size": self.size, "sha256": self.sha256[:16] if self.sha256 else None,
            "updated": self.update_date,
        }


@dataclass
class GxwProject:
    """A parsed .gxw container: metadata, inventory, and raw stream access."""

    path: str
    name: str = ""
    cpu: str = ""
    text_encoding: str = "utf-8"
    created: str = ""
    data_files: list[DataFile] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    coverage: dict[str, int] = field(default_factory=dict)
    _outer: Any = None
    _inner: Any = None
    _raw: bytes = field(default=b"", repr=False)

    def find(self, suffix: str) -> DataFile | None:
        want = suffix.lower()
        exact = [f for f in self.data_files if f.name.lower() == want]
        if exact:
            return exact[0]
        hits = [f for f in self.data_files if f.name.lower().endswith(want) and not f.scrap]
        return hits[0] if hits else None

    def programs(self) -> list[DataFile]:
        return [f for f in self.data_files
                if f.kind in ("program", "pou") and not f.scrap]

    def read_stream(self, data_file: DataFile) -> bytes:
        if self._inner is None or data_file.stream_id is None:
            raise GxwError("stream bytes unavailable for " + data_file.name)
        return self._inner.openstream(data_file.stream_id).read()

    def close(self) -> None:
        for handle in (self._inner, self._outer):
            try:
                if handle is not None:
                    handle.close()
            except Exception:  # pragma: no cover
                pass

    def program_structure(self) -> list[dict[str, Any]]:
        """Framing evidence per program body (pou_anchors; no instruction semantics)."""

        out: list[dict[str, Any]] = []
        for entry in self.programs():
            try:
                payload = self.read_stream(entry)
            except GxwError:
                out.append({"name": entry.name, "available": False})
                continue
            info = pou_anchors(payload)
            out.append(
                {
                    "name": entry.name,
                    "available": True,
                    "size": len(payload),
                    "sha256": (entry.sha256 or "")[:16],
                    "recordAnchors": info["anchorCount"],
                    "anchorSizes": info["firstSizes"],
                    "decodedInstructions": 0,
                }
            )
        return out

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for f in self.data_files:
            kinds[f.kind] = kinds.get(f.kind, 0) + 1
        return {
            "path": self.path,
            "projectName": self.name,
            "cpu": self.cpu,
            "textEncoding": self.text_encoding,
            "created": self.created,
            "dataFiles": len(self.data_files),
            "byKind": dict(sorted(kinds.items())),
            "programs": [f.name for f in self.programs()],
            "deviceCommentFile": (self.find("COMMENT.qcd") or DataFile("", "")).name or None,
            "diagnostics": self.diagnostics,
            "coverage": self.coverage,
        }


def _rows(diffgram: bytes, container: str) -> list[dict[str, str]]:
    """Parse a persisted-XML diffgram (UTF-8 in practice) into row dicts."""

    text = diffgram.decode("utf-8", errors="replace").lstrip("\ufeff")
    root = ET.fromstring(text)
    out: list[dict[str, str]] = []
    for cont in root:
        if cont.tag != container and not cont.tag.endswith(container):
            continue
        for row in cont:
            out.append({c.tag: (c.text or "") for c in row})
    return out


def open_gxw(path: str | Path) -> GxwProject:
    """Open a .gxw and return its container view (no program decoding)."""

    target = Path(path).expanduser()
    if target.suffix.lower() == ".gpj":
        raise GxwError("不支持 .gpj 工程；当前离线读取仅支持 GX Works2 .gxw 容器。"
                       "请在原工程软件中另存或导出受支持的 .gxw 副本；修改扩展名不是格式转换。")
    if olefile is None:
        raise GxwError("olefile is required: py -m pip install olefile")
    if not target.exists():
        raise GxwError("file not found: " + str(target))
    raw = target.read_bytes()
    if raw[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise GxwError(str(target) + " is not an OLE2 compound file; not a .gxw?")

    project = GxwProject(path=str(target), _raw=raw)
    outer = olefile.OleFileIO(io.BytesIO(raw))
    project._outer = outer
    try:
        names = ["/".join(e) for e in outer.listdir()]
    except Exception:
        names = []

    # 1) project identity
    try:
        rows = _rows(outer.openstream("projectlist.xml").read(), "DSPROJECT")
        for row in rows:
            if row.get("szName"):
                project.name = row["szName"]
            if row.get("dtCreateDate"):
                project.created = row["dtCreateDate"]
            if row.get("iProjectAttributeID"):
                project.coverage["projectAttributeID"] = int(row["iProjectAttributeID"] or 0)
    except Exception as exc:
        project.diagnostics.append("projectlist.xml: " + str(exc))

    # 2) data-file inventory, joined to inner streams
    inner = None
    hdb_bytes: bytes | None = None
    try:
        hdb_bytes = outer.openstream("_hdb").read()
        inner = olefile.OleFileIO(io.BytesIO(hdb_bytes))
        project._inner = inner
    except Exception as exc:
        project.diagnostics.append("inner _hdb container: " + str(exc))

    try:
        rows = _rows(outer.openstream("projectdatalist.xml").read(), "DSPROJECTDATA")
        inner_ids = set()
        if inner is not None:
            inner_ids = {"/".join(e) for e in inner.listdir()}
        for row in rows:
            iid = row.get("iID", "")
            entry = DataFile(
                iid=iid,
                name=row.get("szName", ""),
                folder_type=row.get("ucFolderType", ""),
                file_type=row.get("ucFileType", ""),
                scrap=str(row.get("bScrapFlag", "")).lower() == "true",
                update_date=row.get("dtUpdateDate", ""),
            )
            if inner is not None and iid in inner_ids:
                entry.stream_id = iid
                try:
                    data = inner.openstream(iid).read()
                    entry.size = len(data)
                    entry.sha256 = hashlib.sha256(data).hexdigest()
                except Exception as exc:  # pragma: no cover
                    project.diagnostics.append("stream " + iid + ": " + str(exc))
            project.data_files.append(entry)
    except Exception as exc:
        project.diagnostics.append("projectdatalist.xml: " + str(exc))

    for entry in project.data_files:
        if entry.name.endswith(".prj") and entry.stream_id:
            try:
                raw_prj = project.read_stream(entry)
                cursor, texts = 0, []
                for _ in range(3):
                    count = struct.unpack_from("<I", raw_prj, cursor)[0]
                    if not 1 <= count <= 8192 or cursor + 4 + count * 2 > len(raw_prj):
                        raise ValueError("CPU 元数据字符串越界")
                    texts.append(raw_prj[cursor + 4:cursor + 4 + count * 2].decode("utf-16le").rstrip("\0"))
                    cursor += 4 + count * 2
                project.cpu = texts[1]
                codepage = struct.unpack_from("<I", raw_prj, cursor + 42)[0]
                project.text_encoding = {932: "cp932", 936: "gb18030", 949: "cp949", 1252: "cp1252"}.get(codepage, "")
            except (ValueError, struct.error) as exc:
                project.diagnostics.append("CPU metadata: " + str(exc))

    # 3) coverage bookkeeping: how much of the container is explained
    known = {"Project.gd2", "_hdb", "projectlist.xml", "projectdatalist.xml", "checkout.xml",
             "dataprotection.xml", "history.xml", "label.xml", "labellink.xml",
             "securitylevel.xml", "storedhistory.xml", "user.xml"}
    unaccounted = [n for n in names if n not in known and "/" not in n]
    mapped_ids = {f.stream_id for f in project.data_files if f.stream_id}
    if inner is not None:
        orphan_streams = sorted(
            int(i) for i in {"/".join(e) for e in inner.listdir()} - mapped_ids if i.isdigit()
        )
        if orphan_streams:
            project.diagnostics.append(
                "inner streams not referenced by the inventory: " + str(orphan_streams[:12])
            )
    else:
        project.diagnostics.append("no inner container")
    project.coverage = {
        "outerStreams": len(names),
        "innerStreams": len(mapped_ids),
        "dataFiles": len(project.data_files),
        "dataFilesWithBytes": sum(1 for f in project.data_files if f.size is not None),
        "programBodiesDecoded": 0,   # honest: record decoding is the REA layer's job
    }
    if unaccounted:
        project.diagnostics.append("top-level streams kept opaque: " + ", ".join(sorted(unaccounted)[:10]))
    return project


def device_comments(project: GxwProject) -> dict[str, str]:
    """Best-effort device comments from label.xml / labellink.xml.

    The canonical store is COMMENT.qcd (an Access DB whose engine password GX Works2
    knows); until that is cracked, label tables give whatever named-device comments
    exist and we report what was used.
    """

    comments: dict[str, str] = {}
    try:
        for row in _rows(project._outer.openstream("label.xml").read(), "DSLABEL"):
            device = row.get("szDevice") or row.get("szAddress") or ""
            text = row.get("szComment") or row.get("szLabel") or ""
            if device and text:
                comments[device.strip().upper()] = text.strip()
    except Exception:
        pass
    return comments


# --- POU record framing (anchor scan; grammar is an open REA task) ------------

# Verified record header in FX3U structured-POU bodies (_hdb streams named
# *Block*.Program.pou), 28 bytes then a payload of `size` bytes:
#   00 01 00 00 00 | 04 size(u32) | 08 size(u32) | 12 01 00 00 00
#   16 0c 00 00 00 | 20 00 00 00 00 | 24 ff ff ff ff | 28 payload[size]
# Name-payloads carry UTF-16 text (the 20/36-byte records hold block names).

# Evidence this matters: in the FX3U sample pair the only known edit (one extra
# AND/ANB branch in Block1) changes the FIRST record size 20 -> 36 while every
# later record keeps identical content at shifted offsets; and the byte-identical
# backup file (C) produces an identical anchor table to A. Framing proof only -
# instruction semantics are the REA task in re-evidence/README.md. Q projects
# (MAIN.res family) use a different layout and anchor to 0 here; report that.

import struct as _struct


def pou_anchors(payload: bytes) -> dict[str, Any]:
    """Locate FX-style POU record anchors (offset + declared size). Framing only."""

    hits: list[dict[str, Any]] = []
    i = 0
    n = len(payload)
    while i + 28 <= n:
        m1, size, size2, m2, m3 = _struct.unpack_from("<IIIII", payload, i)
        if (
            m1 == 1 and size == size2 and m2 == 1 and m3 == 12
            and payload[i + 20: i + 24] == b"\x00\x00\x00\x00"
            and payload[i + 24: i + 28] == b"\xff\xff\xff\xff"
            and 0 < size < 0x0010_0000
        ):
            hits.append({"offset": i, "size": size})
            i += 28 + size  # record body is consumed; next block follows
            continue
        i += 1
    return {
        "anchorCount": len(hits),
        "totalPayloadBytes": sum(h["size"] for h in hits),
        "firstOffsets": [h["offset"] for h in hits[:12]],
        "firstSizes": [h["size"] for h in hits[:12]],
        "note": "framing evidence only; instruction grammar pending the REA task "
        "(re-evidence/README.md)",
    }


# --- MAIN.res program-body decoder (table from gxwlib catalog.rs) -----------
# Operator/operand records are byte-exact, from monozukuri-ai/gxwlib
# crates/gxw-core/src/formats/simple/catalog.rs (pinned FX2N GXW/CSV pair) and
# re-verified on our live Q round trip. Unverified constructs stop the walk.

import struct

_OPERATORS = {
    bytes.fromhex('030003'): ('LD', 1),
    bytes.fromhex('030103'): ('LDI', 1),
    bytes.fromhex('030c03'): ('AND', 1),
    bytes.fromhex('030d03'): ('ANI', 1),
    bytes.fromhex('030603'): ('OR', 1),
    bytes.fromhex('030703'): ('ORI', 1),
    bytes.fromhex('031903'): ('ANB', 0),
    bytes.fromhex('031803'): ('ORB', 0),
    bytes.fromhex('032003'): ('OUT', 1),
    bytes.fromhex('032303'): ('SET', 1),
    bytes.fromhex('032403'): ('RST', 1),
    bytes.fromhex('031a03'): ('MPS', 0),
    bytes.fromhex('031b03'): ('MRD', 0),
    bytes.fromhex('031c03'): ('MPP', 0),
    bytes.fromhex('033303'): ('FEND', 0),
    bytes.fromhex('033403'): ('END', 0),
    bytes.fromhex('04020204'): ('LDP', 1),
    bytes.fromhex('04030204'): ('LDF', 1),
    bytes.fromhex('040e0204'): ('ANDP', 1),
    bytes.fromhex('040f0204'): ('ANDF', 1),
    bytes.fromhex('04080204'): ('ORP', 1),
    bytes.fromhex('04090204'): ('ORF', 1),
    bytes.fromhex('04250204'): ('PLS', 1),
    bytes.fromhex('04260204'): ('PLF', 1),
    bytes.fromhex('04340204'): ('END', 0),
    bytes.fromhex('054c050005'): ('MOV', 2),
    bytes.fromhex('0549072805'): ('ADD', 3),
    bytes.fromhex('0549072a05'): ('SUB', 3),
    bytes.fromhex('0549072c05'): ('MUL', 3),
    bytes.fromhex('0549072e05'): ('DIV', 3),
    bytes.fromhex('054c090105'): ('DMOV', 2),
    bytes.fromhex('05490d2905'): ('DADD', 3),
    bytes.fromhex('05490d2b05'): ('DSUB', 3),
    bytes.fromhex('05490d2d05'): ('DMUL', 3),
    bytes.fromhex('05490d2f05'): ('DDIV', 3),
    bytes.fromhex('054a030005'): ('INC', 1),
    bytes.fromhex('054a030205'): ('DEC', 1),
    bytes.fromhex('054a050105'): ('DINC', 1),
    bytes.fromhex('054a050305'): ('DDEC', 1),
    bytes.fromhex('054d030005'): ('CJ', 1),
    bytes.fromhex('0554030105'): ('CALL', 1),
    bytes.fromhex('054c070705'): ('FMOV', 3),
    bytes.fromhex('054c070605'): ('BMOV', 3),
    bytes.fromhex('056a030005'): ('FOR', 1),
    bytes.fromhex('056a010105'): ('NEXT', 0),
    bytes.fromhex('056a010705'): ('SRET', 0),
    bytes.fromhex('064c05000206'): ('MOVP', 2),
    bytes.fromhex('064907280206'): ('ADDP', 3),
    bytes.fromhex('0649072a0206'): ('SUBP', 3),
    bytes.fromhex('0649072c0206'): ('MULP', 3),
    bytes.fromhex('0649072e0206'): ('DIVP', 3),
    bytes.fromhex('064c09010206'): ('DMOVP', 2),
    bytes.fromhex('064a03000206'): ('INCP', 1),
    bytes.fromhex('064a03020206'): ('DECP', 1),
    bytes.fromhex('064a05010206'): ('DINCP', 1),
    bytes.fromhex('064a05030206'): ('DDECP', 1),
}

_CONTACT_CODES = {
    156: ('X', 16),
    157: ('Y', 16),
    160: ('B', 16),
    180: ('W', 16),
    161: ('SB', 16),
    181: ('SW', 16),
    176: ('ZR', 16),
    144: ('M', 10),
    146: ('L', 10),
    148: ('F', 10),
    152: ('S', 10),
    168: ('D', 10),
    175: ('R', 10),
    194: ('TN', 10),
    197: ('CN', 10),
    193: ('TS', 10),
    192: ('TC', 10),
    196: ('CS', 10),
    195: ('CC', 10),
    199: ('STS', 10),
    198: ('STC', 10),
    200: ('STN', 10),
}


@lru_cache(maxsize=1)
def _fx_operators():
    """采用有原生样例依据的 FX3U 表；来源及许可见 third-party。"""
    catalog = json.loads((Path(__file__).with_name("data") / "fx3u_step_widths.json").read_text(encoding="utf-8"))
    operators = {bytes.fromhex(k): tuple(v) for k, v in {**catalog["opcodes"], **catalog["supplemental_encodings"]}.items()}
    operators[bytes.fromhex("04210304")] = ("OUT", 2)
    return operators


def _fmt(name, radix, value, cpu=""):
    if cpu.upper().startswith("FX"):
        if name in ("X", "Y"):
            return name + format(value, "o")
        name = {"TN": "T", "CN": "C"}.get(name, name)
    return name + (format(value, 'X') if radix == 16 else str(value))


def _read_operator(stream, i, cpu=""):
    operators = _fx_operators() if cpu.upper().startswith("FX3U") else _OPERATORS
    for seq, (name, arity) in operators.items():
        if stream[i : i + len(seq)] == seq:
            return (name, arity, i + len(seq))
    return None


def _read_operand(stream, i, cpu=""):
    if i + 4 <= len(stream) and stream[i] == 0x04 and stream[i + 3] == 0x04:
        named = _CONTACT_CODES.get(stream[i + 1])
        if named:
            return (_fmt(named[0], named[1], stream[i + 2], cpu), i + 4)
        if stream[i + 1] == 0xE8:
            return ('K' + str(stream[i + 2]), i + 4)
    if i + 5 <= len(stream) and stream[i] == 0x05 and stream[i + 4] == 0x05:
        named = _CONTACT_CODES.get(stream[i + 1])
        if named:
            value = int.from_bytes(stream[i + 2 : i + 4], 'little')
            return (_fmt(named[0], named[1], value, cpu), i + 5)
        if stream[i + 1] == 0xE8:
            return ('K' + str(int.from_bytes(stream[i + 2:i + 4], 'little', signed=True)), i + 5)
        if stream[i + 1] == 0xEA:
            return ('H' + format(int.from_bytes(stream[i + 2 : i + 4], 'little'), '04X'), i + 5)
    return None


def main_res_program(payload, body_len_offset=0x36, cpu="", text_encoding="utf-8"):
    """Decode a MAIN.res body to IL using the proven operator table.

    Walks operator records then operands. A coil comment record is
    len+4 82 utf8... len+4 and attaches to the preceding coil. Unknown
    records stop the walk with stopReason + coverage; never guessed.
    """

    from .il import Instruction, Program, render_il

    out = {'decoded': False, 'coverage': 0.0}
    if len(payload) < body_len_offset + 4:
        out['error'] = 'body too short for a MAIN.res program'
        return out
    size = struct.unpack_from('<I', payload, body_len_offset)[0]
    start = body_len_offset + 4
    if size <= 0 or start + size > len(payload):
        out['error'] = 'program length ' + str(size) + ' out of bounds'
        return out
    stream = payload[start : start + size]
    program = Program()
    i = 0
    stop = ''
    while i < len(stream):
        operator = _read_operator(stream, i, cpu)
        if operator is not None:
            name, arity, i = operator
            operands = []
            bad = ''
            for _ in range(arity):
                got = _read_operand(stream, i, cpu)
                if got is None:
                    bad = name + ' operand unreadable at ' + hex(i)
                    break
                text, i = got
                operands.append(text)
            if bad:
                stop = bad
                break
            program.append(Instruction(name, operands))
            continue
        if i + 3 < len(stream) and stream[i + 1] == 0x82:
            length = stream[i + 2]
            tail = stream[i + 3 + length : i + 4 + length]
            if 1 <= length <= 60 and stream[i] == length + 4 and tail == bytes([stream[i]]):
                raw = stream[i + 3 : i + 3 + length]
                try:
                    text = raw.decode(text_encoding or "utf-8")
                except UnicodeDecodeError:
                    stop = "comment cannot be decoded with the project code page"
                    break
                if program.instructions and program.instructions[-1].is_output:
                    program.instructions[-1].comment = text
                i += 4 + length
                continue
        stop = 'unknown record at ' + hex(i) + ' (bytes ' + stream[i : i + 6].hex(' ') + ')'
        break
    out['consumedBytes'] = i
    out['coverage'] = round(min(1.0, i / max(1, len(stream))), 3)
    if not stop and not any(x.mnemonic in ('END', 'FEND') for x in program.instructions):
        stop = 'stream ended without END/FEND'
    if stop:
        out['stopReason'] = stop
        out['partialIl'] = render_il(program) if program.instructions else ''
        return out
    out['decoded'] = True
    out['il'] = render_il(program)
    out['devices'] = program.devices()
    return out


def _pou_records(payload):
    """Yield (offset, size, body) for the 28-byte record framing in .pou streams.

    Header (verified against three real projects and the live round trip):
      01 00 00 00 | size u32 | size u32 | 01 00 00 00 | 0c 00 00 00 |
      00*4 | ff*4, then size payload bytes.
    """

    i = 0
    n = len(payload)
    marker_zero = bytes.fromhex("00000000")
    marker_ff = bytes.fromhex("ffffffff")
    while i + 28 <= n:
        m1, size, size2, m2, m3 = struct.unpack_from("<IIIII", payload, i)
        if (
            m1 == 1
            and size == size2
            and m2 == 1
            and m3 == 12
            and payload[i + 20 : i + 24] == marker_zero
            and payload[i + 24 : i + 28] == marker_ff
            and size > 0
        ):
            # Keep recognized truncated records visible to the decoder. Silently
            # scanning past them could turn a damaged program into a success.
            yield (i, size, payload[i + 28 : i + 28 + size])
            i += 28 + size
        else:
            i += 1


def _strip_network_trailer(body):
    """Drop the per-network metadata tail: 01 00 00 00 [u32]? 21 00 00 00 <utf16>."""

    marker_start = bytes.fromhex("01000000")
    marker_tag = bytes.fromhex("21000000")
    for position in range(len(body) - 8):
        if body[position : position + 4] != marker_start:
            continue
        for skip in (4, 8):
            tag_at = position + skip
            if body[tag_at : tag_at + 4] != marker_tag:
                continue
            tail = body[tag_at + 4 :]
            if not tail or len(tail) % 2:
                continue
            if all(0x20 <= tail[i] < 0x7F and tail[i + 1] == 0 for i in range(0, len(tail), 2)):
                return body[:position]
    return body


def pou_stream(payload, cpu="", text_encoding="utf-8"):
    """Decode a .pou program body by cutting it into networks, honestly.

    Structured .pou bodies hold one ladder network per 28-byte record; each
    record payload is a token run plus a metadata tail
    (01 00 00 00 [u32]? 21 00 00 00 <utf16 tag>) that carries no ladder data.
    Two framings are handled:
      * continuous token stream ending in END (simple projects, MAIN.res style),
      * one network per record (structured projects) - the tail is stripped and
        each network decoded on its own, then concatenated.
    Anything outside the proven operator table is counted, never guessed.
    """

    from .il import ILParseError, Instruction, Program, parse_il, render_il

    native_sfc = len(payload) >= 58 and struct.unpack_from("<I", payload, 54)[0] == 240
    if len(payload) >= 103 and payload[54] == 1:
        sizes = struct.unpack_from("<II", payload, 55)
        if sizes == (len(payload) - 83,) * 2 and payload[63:79] in (
            bytes.fromhex("010000000c00000000000000ffffffff"),
            bytes.fromhex("000000000c00000000000000ffffffff"),
        ):
            tokens = payload[79:-24]
            result = main_res_program(struct.pack("<I", len(tokens)) + tokens, body_len_offset=0, cpu=cpu, text_encoding=text_encoding)
            result["framing"] = "simple-token-frame"
            return result

    program = Program()
    networks = 0
    skipped = 0
    failures = []
    records = 0
    partial_lines = []

    for index, (offset, size, body) in enumerate(_pou_records(payload)):
        records += 1
        if len(body) != size:
            failures.append({
                "networkIndex": index, "recordOffset": offset,
                "stopReason": "network record payload truncated",
                "declaredBytes": size, "availableBytes": len(body),
            })
            continue
        tokens = _strip_network_trailer(body)
        if not tokens:
            # Only a recognized metadata-only record may be skipped. Unknown
            # non-empty records are failures, not harmless metadata.
            skipped += 1
            continue
        attempt = main_res_program(struct.pack("<I", len(tokens)) + tokens, body_len_offset=0, cpu=cpu, text_encoding=text_encoding)
        # A structured network need not carry END. This is the sole recoverable
        # MAIN.res failure: the whole token run must have decoded, without an
        # unknown operator, unreadable operand, or truncated record.
        complete = attempt.get("decoded") or (
            attempt.get("stopReason") == "stream ended without END/FEND"
            and attempt.get("consumedBytes") == len(tokens)
        )
        text = attempt.get("il") if attempt.get("decoded") else attempt.get("partialIl", "")
        if not complete:
            failures.append({
                "networkIndex": index, "recordOffset": offset,
                "stopReason": attempt.get("stopReason", attempt.get("error", "network not decoded")),
                "consumedBytes": attempt.get("consumedBytes", 0),
                "tokenBytes": len(tokens), "partialIl": text,
            })
            if not failures[:-1] and text:
                partial_lines.extend(text.splitlines())
            continue
        try:
            decoded = parse_il(text)
        except ILParseError as exc:
            failures.append({
                "networkIndex": index, "recordOffset": offset,
                "stopReason": "decoded IL could not be parsed: " + str(exc),
                "partialIl": text,
            })
            continue
        networks += 1
        instructions = [ins for ins in decoded.instructions if ins.mnemonic != "END"]
        program.extend(instructions)
        if not failures:
            partial_lines.extend(render_il(Program(instructions)).splitlines())

    if failures:
        # Never expose an editable IL or synthesize END across unknown data.
        # partialIl is only the contiguous prefix before the first failure.
        return {
            "decoded": False,
            "coverage": min(0.999, round(networks / max(1, networks + len(failures)), 3)),
            "framing": "network-records",
            "networks": networks, "skippedNetworks": skipped,
            "failedNetworks": failures,
            "stopReason": "network " + str(failures[0]["networkIndex"])
                + " at " + hex(failures[0]["recordOffset"]) + ": " + failures[0]["stopReason"],
            "partialIl": "\n".join(partial_lines),
        }

    if not program.instructions:
        # A recognized record must not be bypassed by finding a convenient
        # continuous token fragment inside it.
        if not records:
            # Decode the entire body, never a convenient LD...END fragment
            # hidden inside unknown framing or followed by unparsed data.
            attempt = main_res_program(struct.pack("<I", len(payload)) + payload, body_len_offset=0, cpu=cpu, text_encoding=text_encoding)
            if attempt.get("decoded") and attempt.get("consumedBytes") == len(payload):
                return {
                    "decoded": True,
                    "coverage": 1.0,
                    "framing": "continuous-token-stream",
                    "il": attempt["il"],
                    "devices": attempt["devices"],
                }
        return {
            "decoded": False,
            "coverage": 0.0,
            "stopReason": "no network record and no continuous token stream decoded",
        }

    program.append(Instruction("END"))
    if native_sfc:
        return {
            "decoded": False,
            "coverage": 0.0,
            "framing": "network-records",
            "partialIl": render_il(program),
            "stopReason": "请用 gx_sfc_read 完整读取原生 SFC 图形及步骤绑定；此处仅预览子网络" if native_sfc else "存在未完整解码的网络，禁止作为完整程序写回",
            "incompleteNetworks": [],
        }
    return {
        "decoded": True,
        "coverage": 1.0,
        "framing": "network-records",
        "networks": networks,
        "skippedNetworks": skipped,
        "il": render_il(program),
        "devices": program.devices(),
    }
