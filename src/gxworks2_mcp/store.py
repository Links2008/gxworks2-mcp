"""On-disk program store: one directory per GX Works2 project, IL + JSON side by side.

The MCP server keeps what the model writes in a workspace directory so that a session can
span many turns: rungs added yesterday are still there today, and every program can be
exported as paste-ready IL, structured JSON, or an ASCII ladder preview.

Layout::

    <workspace>/
      projects/
        <project>/
          program.il        # canonical instruction list
          program.json      # structured description (rungs, devices, comments)
          devices.csv       # device -> comment (GX Works2 device comment import format)
          notes.md          # free-form notes for the engineer

Nothing here talks to GX Works2; ui.py does that. Keeping the store independent means the
programming tools work on a machine without GX Works2 installed.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .blocks import ladder_blocks, render_ascii, to_boolean
from .devices import DeviceError, parse_device
from .il import Program, instruction_count, parse_il, render_il
from .validate import summarize, validate_program

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class StoreError(ValueError):
    """Raised for unsafe project names or unreadable project files."""


@dataclass
class Project:
    """A named program workspace."""

    name: str
    root: Path
    cpu: str = "Q"

    @property
    def il_path(self) -> Path:
        return self.root / "program.il"

    @property
    def json_path(self) -> Path:
        return self.root / "program.json"

    @property
    def devices_path(self) -> Path:
        return self.root / "devices.csv"

    @property
    def notes_path(self) -> Path:
        return self.root / "notes.md"

    def exists(self) -> bool:
        return self.root.is_dir()

    def read_il(self) -> str:
        try:
            return self.il_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def read_program(self) -> Program:
        return parse_il(self.read_il(), title=self.name)

    def device_comments(self) -> dict[str, str]:
        if not self.devices_path.exists():
            return {}
        comments: dict[str, str] = {}
        text = self.devices_path.read_text(encoding="utf-8-sig")
        for row in csv.reader(io.StringIO(text)):
            if len(row) >= 2 and row[0].strip():
                comments[row[0].strip().upper()] = row[1].strip()
        return comments

    def save(self, program: Program, *, comments: dict[str, str] | None = None) -> dict[str, object]:
        """Persist IL, structured JSON and device comments; return a write report."""

        self.root.mkdir(parents=True, exist_ok=True)
        il_text = render_il(program)
        self.il_path.write_text(il_text + "\n", encoding="utf-8")
        snapshot = {
            "project": self.name,
            "cpu": self.cpu,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "title": program.title,
            "instructionCount": len(program),
            "instructions": instruction_count(program),
            "devices": program.devices(),
            "rungs": ladder_blocks(program),
            "logic": to_boolean(program),
            "ascii": render_ascii(program),
            "validation": summarize(validate_program(program, cpu=self.cpu)),
        }
        self.json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        merged = self.device_comments()
        if comments:
            for device, comment in comments.items():
                merged[device.strip().upper()] = comment
        if merged:
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            for device, comment in sorted(merged.items()):
                writer.writerow([device, comment])
            self.devices_path.write_text("\ufeff" + buffer.getvalue(), encoding="utf-8")
        return {
            "project": self.name,
            "root": str(self.root),
            "instructions": len(program),
            "devices": len(program.devices()),
            "bytes": len(il_text),
            "files": [span.name for span in (self.il_path, self.json_path, self.devices_path) if span.exists()],
        }


class Store:
    """Project container rooted at a workspace directory."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.projects_dir = self.workspace / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def project_path(self, name: str) -> Path:
        if not SAFE_NAME.match(name or ""):
            raise StoreError(
                "project name " + repr(name) + " is not allowed; use letters, digits, dot, "
                "dash or underscore (max 64 chars)"
            )
        return self.projects_dir / name

    def create(self, name: str, *, cpu: str = "Q", title: str = "", initial_il: str = "") -> Project:
        project = Project(name=name, root=self.project_path(name), cpu=cpu)
        if project.exists():
            raise StoreError("project " + repr(name) + " already exists at " + str(project.root))
        project.root.mkdir(parents=True, exist_ok=False)
        program = parse_il(initial_il, title=title or name) if initial_il else Program(title=title or name)
        project.save(program)
        return project

    def get(self, name: str) -> Project:
        project = Project(name=name, root=self.project_path(name))
        if not project.exists():
            raise StoreError("project " + repr(name) + " does not exist under " + str(self.projects_dir))
        try:
            project.cpu = json.loads(project.json_path.read_text(encoding="utf-8")).get("cpu", "Q")
        except (OSError, ValueError, AttributeError) as exc:
            raise StoreError("工程元数据无法读取：" + str(exc)) from exc
        return project

    def list(self) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for path in sorted(self.projects_dir.iterdir()):
            if not path.is_dir():
                continue
            il_path = path / "program.il"
            try:
                text = il_path.read_text(encoding="utf-8")
            except OSError:
                text = ""
            items.append(
                {
                    "name": path.name,
                    "instructions": len([line for line in text.splitlines() if line.strip()]),
                    "il_file": str(il_path) if il_path.exists() else None,
                    "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                }
            )
        return items

    def delete(self, name: str) -> None:
        project = self.get(name)
        for child in sorted(project.root.rglob("*"), reverse=True):
            child.unlink() if child.is_file() else child.rmdir()
        project.root.rmdir()


def diff_programs(before: Program, after: Program) -> dict[str, object]:
    """Line-level diff between two programs, for review before writing to a project."""

    left = [str(instruction) for instruction in before.instructions]
    right = [str(instruction) for instruction in after.instructions]
    removed = [line for line in left if line not in right]
    added = [line for line in right if line not in left]
    return {
        "added": added,
        "removed": removed,
        "before": len(left),
        "after": len(right),
    }


def device_usage(program: Program) -> dict[str, list[dict[str, object]]]:
    """Every device used, with the instructions that touch it (a wiring checklist)."""

    usage: dict[str, list[dict[str, object]]] = {}
    for index, instruction in enumerate(program.instructions):
        for operand in instruction.operands:
            try:
                ref = parse_device(operand)
            except DeviceError:
                continue
            usage.setdefault(str(ref), []).append(
                {"index": index, "mnemonic": instruction.mnemonic, "instruction": str(instruction)}
            )
    return dict(sorted(usage.items(), key=lambda item: (item[0][0], _device_sort_key(item[0])))) 


def _device_sort_key(text: str) -> tuple[int, str]:
    match = re.match(r"([A-Z]+)(\d+)", text)
    if not match:
        return (0, text)
    return (int(match.group(2)), match.group(1))
