"""Runnable demo: build a small program and print IL, ladder preview and validation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gxworks2_mcp.author import compile_rungs
from gxworks2_mcp.blocks import render_ascii, to_boolean
from gxworks2_mcp.validate import summarize, validate_program

RUNGS = [
    {
        "out": "Y0",
        "comment": "conveyor motor, self-holding",
        "logic": {"or": [{"and": ["X0", {"not": "X1"}]}, {"and": ["Y0", {"not": "X1"}]}]},
    },
    {
        "out": "Y10",
        "comment": "one-shot pulse on start",
        "logic": {"and": ["X0", {"rise": "M0"}]},
    },
    {
        "out": "M20",
        "comment": "stopped flag",
        "logic": {"and": [{"not": "Y0"}, "X3"]},
    },
    {"out": "Y12", "comment": "stopped lamp", "logic": "M20"},
]


def main() -> None:
    program = compile_rungs(RUNGS)
    print("=== IL ===")
    for instruction in program.instructions:
        text = str(instruction)
        if instruction.comment:
            text += "  // " + instruction.comment
        print(text)
    print()
    print("=== ladder preview ===")
    print(render_ascii(program))
    print()
    print("=== logic ===")
    print(to_boolean(program))
    print()
    report = summarize(validate_program(program, cpu="Q"))
    print("=== validation ===")
    print("ok:", report["ok"], "errors:", report["errors"], "warnings:", report["warnings"])
    for issue in report["issues"]:
        print(" -", issue["severity"], issue["code"], issue["message"])


if __name__ == "__main__":
    main()
