# Batch authoring and MCP latency

Use `gx_program_prepare` for a complete ladder program (`rungs`) or FX STL/RET
sequence (`il`). Supply one input, a supported CPU family/model, and the project
name. One call compiles/parses, validates, saves and exports CSV. The default
response contains the path, counts and all validation messages, not full IL and
ASCII copies. Set `preview=true` when those previews are needed.

This reduces the preparation workflow from create → validate → export (three
tool calls) to one. Do not send a tool call per rung. Keep the MCP stdio process
alive across calls instead of paying startup/initialization for each edit.

## Safety and failure handling

Validation errors prevent writes. Existing local projects require
`overwrite=true`. Warnings remain visible. CSV is first exported to a temporary
file, so an export failure leaves an existing project and CSV unchanged. Project
saving and final CSV publication are not a multi-file transaction: if those later
steps fail, the response explicitly includes `projectMayBeModified`,
`csvUpdated=false`, `doNotImport=true`, and the failed phase. No usable new CSV
path is returned. Inspect the project and successfully prepare again before
importing; an old CSV at the prior path may not represent the current program.

Preparation does not touch GX Works2 or a PLC. Desktop import remains a separate
confirmed whole-block replacement, followed by compilation and checks. Native
graphical SFC editing is not implemented by this tool; FX SFC here means the
existing STL/RET instruction representation. No GUI waits were shortened without
Windows measurements, and no validation was disabled to improve timings.

## Reproduce

Run `python tools/benchmark_prepare.py` in an environment installed with
`pip install -e '.[dev]'`. The script starts one local stdio server, verifies the
tool schema, and uses isolated temporary project directories. It invokes no GUI
or PLC tools. Each case alternates old/new order and reports five-run medians.

Example measured on 2026-10-21, Python 3.12, MCP 2.3.0, Linux:

| Input | Instructions | Old 3 calls (ms) | Prepare 1 call (ms) | Response bytes old → new |
|---|---:|---:|---:|---:|
| Ladder, Q | 501 | 27.745 | 15.018 | 10,244 → 471 |
| Ladder, Q | 2001 | 72.502 | 50.580 | 38,755 → 477 |
| FX3U STL | 501 | 22.119 | 12.803 | 12,407 → 474 |
| FX3U STL | 2001 | 62.801 | 42.462 | 48,717 → 479 |

These are local tool-call durations, not model generation or GX Works2 import
latency. Response sizes count UTF-8 text, not tokens or complete JSON-RPC framing.
Timings vary with machine load; the reliable structural benefits are 3 → 1
calls and approximately 95–99% less response text for these clean test programs.
Programs with many warnings deliberately return more diagnostic text.

The unchanged-program diff now returns before SequenceMatcher. A 5,001-instruction
repetitive fixture measured about 1.7 ms after this change, versus about 1.6 s
before. Changed programs still use the order/duplicate-sensitive matcher; this
does not claim to remove all worst-case diff behavior.
