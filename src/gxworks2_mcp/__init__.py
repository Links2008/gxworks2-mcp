"""GX Works2 / MELSEC programming toolkit exposed as an MCP server."""

__version__ = "0.1.1"

from .blocks import (  # noqa: F401
    Contact,
    Invert,
    LogicError,
    Parallel,
    Rung,
    Series,
    build_rungs,
    coerce_instructions,
    ladder_blocks,
    render_ascii,
    to_boolean,
)
from .devices import (  # noqa: F401
    DeviceError,
    DeviceRange,
    DeviceRef,
    describe as describe_device,
    device_code,
    expand_range,
    parse_device,
    parse_range,
)
from .il import (  # noqa: F401
    ILParseError,
    Instruction,
    Program,
    coerce_program,
    instruction_count,
    parse_il,
    parse_line,
    render_il,
)
from .validate import ValidationIssue, summarize, validate_program  # noqa: F401

__all__ = [
    "Contact",
    "DeviceError",
    "DeviceRange",
    "DeviceRef",
    "ILParseError",
    "Instruction",
    "Invert",
    "LogicError",
    "Parallel",
    "Program",
    "Rung",
    "Series",
    "ValidationIssue",
    "build_rungs",
    "coerce_instructions",
    "coerce_program",
    "describe_device",
    "device_code",
    "expand_range",
    "instruction_count",
    "ladder_blocks",
    "parse_device",
    "parse_il",
    "parse_line",
    "parse_range",
    "render_ascii",
    "render_il",
    "summarize",
    "to_boolean",
    "validate_program",
]
